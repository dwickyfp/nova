"""Scheduled Studio agent runs: recurring reports and conditional alerts.

An automation is "ask this agent this question on this schedule, and deliver
the answer". It is consented to once, when the owner creates it; each run then
executes read-only as the owner:

* **Identity.** The worker's restricted service account impersonates the owner
  (``DelegateExecutor``), exactly like scheduled tasks. No password is stored.
  The verified connection is bound to the run with ``delegated_connection`` so
  every governed query in the turn uses it.
* **Read-only.** Only calls classified ``read_only`` are approved during a run;
  anything else is denied, so a schedule can never write.
* **Delivery.** Every run lands in the owner's Studio history as a new thread.
  An optional MCP tool the owner registered (a Slack or email connector)
  receives the answer text. A ``condition`` delivers only when a metric in the
  result crosses a threshold.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.modules.intelligence.contracts import Contract, Scope, SemanticRef, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock

logger = logging.getLogger(__name__)

#: A schedule may not fire more often than this.
MIN_INTERVAL = timedelta(minutes=15)
MAX_AUTOMATIONS_PER_AGENT = 20
MAX_TEXT_DELIVERED = 3000

AUTOMATIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS (
    automation_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    title VARCHAR(256) NOT NULL,
    prompt VARCHAR(4000) NOT NULL,
    schedule_kind VARCHAR(16) NOT NULL,
    schedule_expr VARCHAR(128) NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    condition_json JSON,
    delivery_json JSON,
    enabled BOOLEAN NOT NULL,
    next_run_at DATETIME,
    last_run_at DATETIME,
    last_status VARCHAR(64),
    last_thread_id VARCHAR(64),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(automation_id)
DISTRIBUTED BY HASH(automation_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

_COLUMNS = (
    "automation_id, agent_id, owner_name, role_name, title, prompt, schedule_kind, "
    "schedule_expr, timezone, condition_json, delivery_json, enabled, next_run_at, "
    "last_run_at, last_status, last_thread_id, created_at, updated_at"
)


class AutomationCondition(BaseModel):
    """Deliver only when ``metric`` in the result compares true against ``value``."""

    metric: str = Field(min_length=1, max_length=128)
    operator: Literal[">", ">=", "<", "<=", "=", "!="]
    value: float


class AutomationDelivery(BaseModel):
    #: The run always lands in Studio history; this only adds an external copy.
    mcp_tool_id: str | None = Field(default=None, max_length=64)
    #: The string argument of the MCP tool that receives the answer text.
    text_argument: str = Field(default="text", max_length=64)
    #: Fixed arguments such as a channel name. Screened for credentials.
    fixed_arguments: dict[str, str | int | bool] = Field(default_factory=dict, max_length=8)


class AutomationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=3, max_length=4000)
    schedule_kind: Literal["cron", "interval"]
    schedule_expr: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default_factory=lambda: settings.NOVA_TIMEZONE, max_length=64)
    condition: AutomationCondition | None = None
    delivery: AutomationDelivery = Field(default_factory=AutomationDelivery)
    enabled: bool = True


class AutomationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    prompt: str | None = Field(default=None, min_length=3, max_length=4000)
    schedule_kind: Literal["cron", "interval"] | None = None
    schedule_expr: str | None = Field(default=None, min_length=1, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)
    condition: AutomationCondition | None = None
    delivery: AutomationDelivery | None = None
    enabled: bool | None = None


class AutomationExecutionBinding(Contract):
    action_id: str = Field(min_length=1, max_length=64)
    scope: Scope
    semantic: SemanticRef


class AutomationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _naive(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC).replace(tzinfo=None) if value else None


def next_run(kind: str, expression: str, timezone: str, after: datetime) -> datetime:
    """The next fire strictly after ``after`` (aware UTC), or ``AutomationError``."""
    from app.modules.task_orchestration.schedule import ScheduleError, next_fire

    try:
        first = next_fire(kind, expression, timezone, after)
        second = next_fire(kind, expression, timezone, first) if first else None
    except ScheduleError as exc:
        raise AutomationError(str(exc)) from exc
    if first is None or second is None:
        raise AutomationError("The schedule never fires.")
    if second - first < MIN_INTERVAL:
        raise AutomationError(
            f"Automations run at most every {int(MIN_INTERVAL.total_seconds() // 60)} minutes."
        )
    return first


def condition_met(condition: dict[str, Any] | None, tables: dict[str, dict]) -> bool | None:
    """True/False against the first row holding ``metric``; ``None`` when absent."""
    if not condition:
        return True
    metric = str(condition.get("metric") or "")
    for table in tables.values():
        columns = [str(column) for column in table.get("columns") or []]
        if metric not in columns:
            continue
        for row in table.get("rows") or []:
            try:
                value = Decimal(str(row[columns.index(metric)]))
            except (InvalidOperation, IndexError, TypeError):
                continue
            target = Decimal(str(condition["value"]))
            return {
                ">": value > target,
                ">=": value >= target,
                "<": value < target,
                "<=": value <= target,
                "=": value == target,
                "!=": value != target,
            }[str(condition["operator"])]
    return None


def _row(values: list[Any]) -> dict[str, Any]:
    record = dict(zip(_COLUMNS.split(", "), values, strict=True))
    for key in ("condition_json", "delivery_json"):
        raw = record.pop(key)
        record[key.removesuffix("_json")] = json.loads(raw) if isinstance(raw, str) else raw
    record["enabled"] = bool(record["enabled"])
    return record


def automation_configuration(record: dict[str, Any]) -> dict[str, Any]:
    """Configuration excludes worker timestamps and result status."""
    return {
        key: record.get(key)
        for key in (
            "agent_id",
            "owner_name",
            "role_name",
            "title",
            "prompt",
            "schedule_kind",
            "schedule_expr",
            "timezone",
            "condition",
            "delivery",
            "enabled",
        )
    }


def automation_creation_id(owner_name: str, role_name: str, agent_id: str, identity: str) -> str:
    return fingerprint(["studio-automation-v1", owner_name, role_name, agent_id, identity])


class AutomationRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(AUTOMATIONS_DDL)

    async def create(
        self,
        *,
        agent_id: str,
        owner_name: str,
        role_name: str,
        body: AutomationCreate,
        creation_identity: str | None = None,
        execution_binding: AutomationExecutionBinding | None = None,
    ) -> dict[str, Any]:
        await self.ensure_schema()
        if creation_identity is not None and not 8 <= len(creation_identity) <= 128:
            raise AutomationError("Invalid automation creation identity")
        automation_id = (
            automation_creation_id(owner_name, role_name, agent_id, creation_identity)
            if creation_identity is not None
            else str(uuid4())
        )
        if execution_binding is not None and (
            execution_binding.scope.principal != owner_name
            or execution_binding.scope.active_role != role_name
            or execution_binding.scope.session_id is not None
            or body.delivery.mcp_tool_id is not None
            or body.delivery.fixed_arguments
        ):
            raise AutomationError("Automation execution binding does not match its owner")
        async with metadata_lock(
            "studio-automation-owner:" + fingerprint([owner_name, agent_id])
        ) as lease:
            return await self._create_locked(
                automation_id, agent_id, owner_name, role_name, body, execution_binding, lease
            )

    async def _create_locked(
        self, automation_id, agent_id, owner_name, role_name, body, execution_binding, lease
    ) -> dict[str, Any]:
        delivery = body.delivery.model_dump(mode="json")
        if execution_binding is not None:
            delivery["execution_binding"] = execution_binding.model_dump(mode="json")
        expected = {
            **body.model_dump(mode="json"),
            "delivery": delivery,
            "agent_id": agent_id,
            "owner_name": owner_name,
            "role_name": role_name,
        }
        prior = await self.get(automation_id, owner_name=owner_name)
        if prior is not None:
            if automation_configuration(prior) != automation_configuration(expected):
                raise AutomationError("Automation creation identity inputs changed")
            return prior
        existing = await self.list(agent_id=agent_id, owner_name=owner_name)
        if len(existing) >= MAX_AUTOMATIONS_PER_AGENT:
            raise AutomationError(f"An agent has at most {MAX_AUTOMATIONS_PER_AGENT} automations.")
        now = _now()
        upcoming = next_run(body.schedule_kind, body.schedule_expr, body.timezone, now)
        if not await lease.renew():
            raise AutomationError("Automation creation lease expired")
        await db.execute_system(
            f"INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS ({_COLUMNS}) VALUES ("
            + ", ".join(["%s"] * 18)
            + ")",
            [
                automation_id,
                agent_id,
                owner_name,
                role_name,
                body.title,
                body.prompt,
                body.schedule_kind,
                body.schedule_expr,
                body.timezone,
                json.dumps(body.condition.model_dump()) if body.condition else None,
                json.dumps(delivery),
                body.enabled,
                _naive(upcoming),
                None,
                None,
                None,
                _naive(now),
                _naive(now),
            ],
        )
        created = await self.get(automation_id, owner_name=owner_name)
        assert created is not None
        return created

    async def list(self, *, agent_id: str, owner_name: str) -> list[dict[str, Any]]:
        await self.ensure_schema()
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS "
            "WHERE agent_id = %s AND owner_name = %s ORDER BY created_at DESC",
            [agent_id, owner_name],
        )
        return [_row(row) for row in result.get("rows") or []]

    async def get(self, automation_id: str, *, owner_name: str) -> dict[str, Any] | None:
        await self.ensure_schema()
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS "
            "WHERE automation_id = %s AND owner_name = %s",
            [automation_id, owner_name],
        )
        rows = result.get("rows") or []
        return _row(rows[0]) if rows else None

    async def update(
        self,
        automation: dict[str, Any],
        body: AutomationUpdate,
        *,
        expected_configuration_digest: str | None = None,
    ) -> dict[str, Any]:
        async with metadata_lock("studio-automation:" + automation["automation_id"]) as lease:
            current = await self.get(
                automation["automation_id"], owner_name=automation["owner_name"]
            )
            if current is None:
                raise AutomationError("Automation unavailable")
            if expected_configuration_digest is not None and (
                fingerprint(automation_configuration(current)) != expected_configuration_digest
            ):
                raise AutomationError("Automation configuration changed; review before updating")
            return await self._update_locked(current, body, lease)

    async def _update_locked(self, automation, body, lease) -> dict[str, Any]:
        fields = body.model_dump(exclude_unset=True)
        merged = {**automation, **fields}
        assignments, params = [], []
        for column in ("title", "prompt", "schedule_kind", "schedule_expr", "timezone", "enabled"):
            if column in fields:
                assignments.append(f"{column} = %s")
                params.append(fields[column])
        if "condition" in fields:
            assignments.append("condition_json = %s")
            params.append(json.dumps(fields["condition"]) if fields["condition"] else None)
        if "delivery" in fields and fields["delivery"] is not None:
            assignments.append("delivery_json = %s")
            params.append(json.dumps(fields["delivery"]))
        if {"schedule_kind", "schedule_expr", "timezone", "enabled"} & set(fields):
            upcoming = next_run(
                merged["schedule_kind"], merged["schedule_expr"], merged["timezone"], _now()
            )
            assignments.append("next_run_at = %s")
            params.append(_naive(upcoming))
        if assignments:
            assignments.append("updated_at = %s")
            params.append(_naive(_now()))
            if not await lease.renew():
                raise AutomationError("Automation update lease expired")
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS SET "
                + ", ".join(assignments)
                + " WHERE automation_id = %s AND owner_name = %s",
                [*params, automation["automation_id"], automation["owner_name"]],
            )
        updated = await self.get(automation["automation_id"], owner_name=automation["owner_name"])
        assert updated is not None
        return updated

    async def delete(self, automation_id: str, *, owner_name: str) -> None:
        async with metadata_lock("studio-automation:" + automation_id) as lease:
            if not await lease.renew():
                raise AutomationError("Automation delete lease expired")
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS "
                "WHERE automation_id = %s AND owner_name = %s",
                [automation_id, owner_name],
            )

    async def due(self, now: datetime, *, limit: int = 20) -> list[dict[str, Any]]:
        await self.ensure_schema()
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS "
            "WHERE enabled = TRUE AND next_run_at IS NOT NULL AND next_run_at <= %s "
            "ORDER BY next_run_at LIMIT %s",
            [_naive(now), limit],
        )
        return [_row(row) for row in result.get("rows") or []]

    async def record_run(
        self,
        automation: dict[str, Any],
        *,
        status: str,
        thread_id: str | None,
        ran_at: datetime,
    ) -> None:
        try:
            upcoming = next_run(
                automation["schedule_kind"],
                automation["schedule_expr"],
                automation["timezone"],
                ran_at,
            )
        except AutomationError:
            upcoming = None
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AGENT_AUTOMATIONS SET last_run_at = %s, "
            "last_status = %s, last_thread_id = %s, next_run_at = %s, updated_at = %s "
            "WHERE automation_id = %s",
            [
                _naive(ran_at),
                status[:64],
                thread_id,
                _naive(upcoming),
                _naive(ran_at),
                automation["automation_id"],
            ],
        )


automation_repository = AutomationRepository()


@dataclass
class RunResult:
    status: str
    thread_id: str | None
    text: str = ""


class AutomationRunner:
    """Runs one automation as its owner through the ordinary Studio loop."""

    def __init__(self, executor: Any, *, repository: AutomationRepository = automation_repository):
        self._executor = executor
        self._repository = repository

    async def run(self, automation: dict[str, Any], *, now: datetime | None = None) -> RunResult:
        ran_at = now or _now()
        try:
            result = await self._run(automation, ran_at)
        except Exception as exc:  # noqa: BLE001 - recorded, never raised into the worker
            logger.warning(
                "Automation %s failed: %s", automation["automation_id"], type(exc).__name__
            )
            result = RunResult(status=f"failed:{type(exc).__name__}", thread_id=None)
        await self._repository.record_run(
            automation, status=result.status, thread_id=result.thread_id, ran_at=ran_at
        )
        await write_audit_log(
            event_type="AGENT_AUTOMATION",
            user_name=automation["owner_name"],
            action="RUN",
            object_type="AGENT_AUTOMATION",
            object_name=automation["automation_id"],
            status="SUCCESS" if not result.status.startswith("failed") else "FAILED",
            decision=result.status,
            active_role=automation["role_name"],
        )
        return result

    async def _run(self, automation: dict[str, Any], ran_at: datetime) -> RunResult:
        from app.modules.agents.repository import agent_repository
        from app.modules.assistant.repository import assistant_repository
        from app.modules.query.service import delegated_connection

        owner = automation["owner_name"]
        raw_binding = (automation.get("delivery") or {}).get("execution_binding")
        binding = AutomationExecutionBinding.model_validate(raw_binding) if raw_binding else None
        if binding:
            from app.modules.task_orchestration.repository import task_orchestration_repository

            current = await self._repository.get(automation["automation_id"], owner_name=owner)
            bound = await task_orchestration_repository.get_role_execution_user(
                automation["role_name"]
            )
            if (
                current is None
                or not current["enabled"]
                or bound != owner
                or automation_configuration(current) != automation_configuration(automation)
                or binding.scope.principal != owner
                or binding.scope.active_role != automation["role_name"]
                or binding.scope.session_id is not None
            ):
                return RunResult(status="failed:execution_binding_changed", thread_id=None)
        agent = await agent_repository.get_agent(automation["agent_id"], owner_name=owner)
        if agent is None:
            return RunResult(status="failed:agent_missing", thread_id=None)
        thread = await assistant_repository.create_thread(
            user_name=owner,
            title=f"{automation['title']} · {ran_at.date().isoformat()}",
            agent_id=agent["agent_id"],
        )
        thread_id = thread["thread_id"]
        async with self._executor.owner_connection(owner) as connection:
            if binding:
                from app.modules.task_orchestration.execution import TaskSpec, _dict_cursor

                async with _dict_cursor(connection) as cursor:
                    await self._executor._prepare_task_session(
                        cursor,
                        TaskSpec(
                            name=automation["title"], body="", active_role=automation["role_name"]
                        ),
                    )
            with delegated_connection(owner, connection):
                if binding:
                    from app.modules.intelligence.engine import intelligence_service

                    await intelligence_service.authorize_semantic(
                        binding.semantic,
                        automation_execution_user(automation, thread_id),
                        active=True,
                    )
                text, steps, tables = await _run_turn(agent, automation, thread_id)
        security = Scope.from_user(automation_execution_user(automation, thread_id)).model_dump()
        await assistant_repository.append_message(
            thread_id,
            user_name=owner,
            role="user",
            content=automation["prompt"],
            agent_id=agent["agent_id"],
            security_context=security,
        )
        await assistant_repository.append_message(
            thread_id,
            user_name=owner,
            role="assistant",
            content=text,
            agent_id=agent["agent_id"],
            steps=steps,
            security_context=security,
        )
        met = condition_met(automation.get("condition"), tables)
        if met is None:
            return RunResult(status="condition_unavailable", thread_id=thread_id, text=text)
        if not met:
            return RunResult(status="condition_not_met", thread_id=thread_id, text=text)
        delivered = await _deliver(automation, owner, text)
        return RunResult(status=delivered, thread_id=thread_id, text=text)


def automation_execution_user(automation: dict[str, Any], thread_id: str) -> dict[str, Any]:
    owner, role = automation["owner_name"], automation["role_name"]
    user = {
        "username": owner,
        "encrypted_password": "delegated",
        "active_role": role,
        "assigned_roles": [role],
        "security_context_version": 1,
        "session_id": f"automation:{thread_id}",
    }
    raw_binding = (automation.get("delivery") or {}).get("execution_binding")
    if raw_binding:
        binding = AutomationExecutionBinding.model_validate(raw_binding)
        user["security_context_version"] = binding.scope.security_context_version
        user["intelligence_allowed_views"] = [binding.semantic.view_id]
    return user


async def _run_turn(
    agent: dict[str, Any], automation: dict[str, Any], thread_id: str
) -> tuple[str, list[dict], dict[str, dict]]:
    from app.modules.agents.turns import read_only_consent, run_agent_turn

    output = await run_agent_turn(
        agent,
        # The password field is a marker: queries use the delegated connection.
        user=automation_execution_user(automation, thread_id),
        question=automation["prompt"],
        thread_id=thread_id,
        # A schedule was consented to for reading; it never writes.
        resolve_consent=read_only_consent,
        title=automation["title"],
        execution_timezone=automation["timezone"],
    )
    return output.text, output.steps, output.tables


async def _deliver(automation: dict[str, Any], owner: str, text: str) -> str:
    delivery = automation.get("delivery") or {}
    tool_id = delivery.get("mcp_tool_id")
    if not tool_id:
        return "delivered:inbox"
    from app.modules.agents import mcp_client
    from app.modules.agents.repository import agent_repository
    from app.modules.assistant.skills import contains_credential_shape

    tool = await agent_repository.get_tool(tool_id, owner_name=owner)
    if tool is None or tool.get("source") != "mcp":
        return "delivered:inbox;external_missing"
    server = await agent_repository.get_mcp_server(str(tool.get("server_id")), owner_name=owner)
    if server is None:
        return "delivered:inbox;external_missing"
    arguments = {
        **(delivery.get("fixed_arguments") or {}),
        str(delivery.get("text_argument") or "text"): text[:MAX_TEXT_DELIVERED],
    }
    if contains_credential_shape(json.dumps(arguments, ensure_ascii=False)):
        return "delivered:inbox;external_blocked"
    try:
        result = await asyncio.wait_for(
            mcp_client.call_tool(server, str(tool["name"]), arguments), timeout=30
        )
    except Exception as exc:  # noqa: BLE001 - the inbox copy already exists
        logger.warning("Automation delivery failed: %s", type(exc).__name__)
        return "delivered:inbox;external_failed"
    if result.get("isError"):
        return "delivered:inbox;external_failed"
    return "delivered:inbox+external"


class AutomationWorker:
    """Polls for due automations; a Redis claim keeps each fire single-run."""

    def __init__(self, runner: AutomationRunner, client: Any, *, poll_seconds: float = 30.0):
        self._runner = runner
        self._client = client
        self._poll = poll_seconds

    async def tick(self, now: datetime | None = None) -> int:
        now = now or _now()
        ran = 0
        for automation in await automation_repository.due(now):
            fire = automation.get("next_run_at")
            key = f"nova:automation:{automation['automation_id']}:{fire}"
            claimed = await self._client.set(key, "1", nx=True, ex=24 * 3600)
            if not claimed:
                continue
            await self._runner.run(automation, now=now)
            ran += 1
        return ran

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - the loop must survive a bad tick
                logger.warning("Automation tick failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll)
            except TimeoutError:
                continue
