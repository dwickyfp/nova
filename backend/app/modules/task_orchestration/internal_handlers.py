"""Allowlisted Intelligence handlers using the existing task execution identity."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import Field

from app.core.redis import session_store
from app.modules.assistant.security import observation_context, session_security
from app.modules.intelligence.contracts import Contract, Scope, Window
from app.modules.task_orchestration.credentials import CredentialUnavailable
from app.modules.task_orchestration.execution import TaskSpec, _dict_cursor

HANDLERS = frozenset({"intelligence.monitor", "intelligence.consolidate", "intelligence.outcome"})


class InternalTaskConfiguration(Contract):
    version: Literal[1] = 1
    scope: Scope
    record_id: str | None = Field(default=None, max_length=128)
    source_thread_id: str | None = Field(default=None, max_length=64)
    source_message_id: str | None = Field(default=None, max_length=64)
    agent_id: str | None = Field(default=None, max_length=64)
    provider_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=256)


async def run_once(
    handler: str, config: InternalTaskConfiguration, user: dict, *, now=None
) -> dict:
    if handler not in HANDLERS:
        raise ValueError("Unknown internal task handler")
    actual = Scope.from_user(user)
    if actual.model_dump(exclude={"session_id"}) != config.scope.model_dump(exclude={"session_id"}):
        raise CredentialUnavailable("Internal work requires its originating execution identity")
    from app.modules.intelligence.engine import intelligence_service

    async with asyncio.timeout(120):
        if handler != "intelligence.consolidate" and not config.record_id:
            raise ValueError("Internal work has no target record")
        if handler == "intelligence.monitor":
            monitor = await intelligence_service.get("monitors", config.record_id, user)
            if not monitor.enabled:
                return {"status": "disabled"}
            clock = now or datetime.now(UTC)
            end = clock.replace(minute=0, second=0, microsecond=0)
            return await intelligence_service.run_monitor(
                monitor.id, Window(start=end - timedelta(hours=monitor.window_hours), end=end), user
            )
        if handler == "intelligence.outcome":
            outcome = await intelligence_service.evaluate_outcome(config.record_id, user)
            return {"status": outcome.status, "outcome_id": outcome.id}
        from app.modules.agents.memory import remember_user_message
        from app.modules.agents.router import _require_agent
        from app.modules.assistant.provider import assistant_provider
        from app.modules.assistant.repository import assistant_repository

        if not actual.session_id or actual.session_id != config.scope.session_id:
            raise CredentialUnavailable("Private consolidation requires its originating session")
        if not config.source_thread_id or not config.source_message_id or not config.agent_id:
            raise ValueError("Learning work has no source message")
        await _require_agent(config.agent_id, user)
        source = await assistant_repository.learning_source(
            config.source_thread_id, config.source_message_id, user_name=actual.principal
        )
        if source is None:
            return {"status": "excluded", "extracted": 0}
        if source["security_context"] != observation_context(session_security(user)):
            raise CredentialUnavailable(
                "The learning source belongs to an expired security context"
            )
        from app.modules.agents.knowledge import LearningCallDetail, LearningCallTrace

        attempt_id = str(uuid4())

        async def record_call(phase, provider, usage):
            counts = {
                key: value if type(value) is int and 0 <= value <= 10**12 else None
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                for value in [(usage if isinstance(usage, dict) else {}).get(key)]
            }
            trace = LearningCallTrace(
                id=attempt_id,
                status="done"
                if phase == "response"
                else "error"
                if phase == "error"
                else "running",
                provider_id=getattr(provider, "provider_id", None),
                model_name=getattr(provider, "model", None),
                trace_detail=LearningCallDetail(phase=phase, **counts),
            )
            await assistant_repository.record_learning_trace(
                config.source_message_id,
                user_name=actual.principal,
                trace=trace.model_dump(mode="json"),
            )
            if phase != "error":
                session = await session_store.get(actual.session_id)
                if (
                    not session
                    or Scope.from_user({**session, "session_id": actual.session_id}) != actual
                ):
                    raise CredentialUnavailable("Learning execution context expired or changed")
                from app.modules.task_orchestration.access import verify_active_role

                await verify_active_role({**session, "session_id": actual.session_id})

        extracted = await remember_user_message(
            user_name=actual.principal,
            agent_id=config.agent_id,
            role_name=actual.active_role,
            thread_id=config.source_thread_id,
            message=source["content"],
            provider_id=config.provider_id,
            model=config.model,
            provider=assistant_provider,
            session_id=actual.session_id,
            source_message_id=config.source_message_id,
            record_call=record_call,
            source_scope=actual,
            observed_at=source.get("created_at"),
        )
        await assistant_repository.mark_learning_complete(
            config.source_message_id, user_name=actual.principal
        )
        return {"status": "complete", "extracted": extracted}


async def run_internal_task(task, job, run_id, executor, repository) -> None:
    handler = task.get("handler")
    if handler not in HANDLERS:
        raise ValueError("Unknown internal task handler")
    raw = task.get("handler_config")
    config = InternalTaskConfiguration.model_validate(
        json.loads(raw) if isinstance(raw, str) else raw
    )
    if (
        config.scope.principal != job.execution_user
        or config.scope.active_role != job.execution_role
    ):
        raise CredentialUnavailable("Internal task execution binding changed")

    async def heartbeat():
        while True:
            task_run = await repository.get_task_run(run_id)
            graph_run = await repository.get_graph_run(job.graph_run_id)
            if (
                not task_run
                or not graph_run
                or task_run["state"] != "running"
                or graph_run["state"] != "running"
            ):
                raise CredentialUnavailable("Internal work no longer owns a running task")
            await repository.mark_task_run_heartbeat(run_id)
            await repository.mark_graph_run_heartbeat(job.graph_run_id)
            await asyncio.sleep(10)

    async def execute():
        current = await repository.get_task(task["id"])
        if current is None or current["version"] != task["version"]:
            raise CredentialUnavailable("Task configuration changed before execution")
        if job.trigger_type == "manual":
            user = await session_store.get(job.execution_session_id)
            if not user:
                raise CredentialUnavailable("The execution session expired")
            user = {**user, "session_id": job.execution_session_id}
            from app.modules.task_orchestration.access import verify_active_role

            await verify_active_role(user)
            await run_once(handler, config, user)
            return
        if handler == "intelligence.consolidate":
            raise CredentialUnavailable("Private learning cannot run through a service principal")
        if current.get("schedule_kind") == "manual":
            return
        bound = await repository.get_role_execution_user(job.execution_role)
        if bound != job.execution_user:
            raise CredentialUnavailable("Scheduled execution account binding changed")
        from app.modules.query.service import delegated_connection

        async with executor.owner_connection(bound) as connection:
            async with _dict_cursor(connection) as cursor:
                await executor._prepare_task_session(
                    cursor, TaskSpec(name=task["name"], body="", active_role=job.execution_role)
                )
            user = {
                "username": bound,
                "active_role": job.execution_role,
                "roles": [job.execution_role],
                "encrypted_password": "delegated",
                "security_context_version": config.scope.security_context_version,
                "session_id": f"task:{job.graph_run_id}",
            }
            with delegated_connection(bound, connection):
                await run_once(handler, config, user)

    async with asyncio.timeout(120):
        async with asyncio.TaskGroup() as group:
            keeper = group.create_task(heartbeat())

            async def finish():
                try:
                    await execute()
                finally:
                    keeper.cancel()

            group.create_task(finish())
