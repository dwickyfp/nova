"""Agent-owned quality records and bounded evaluation on the shared AssistantLoop."""

from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, model_validator

from app.core.database import configured_timezone, db
from app.modules.agents.quality_scoring import (
    PERFORMANCE_SCORERS,
    Assertion,
    PromotionGates,
    aggregate_status,
    gate_results,
    promotion_eligible,
    score_assertion,
    score_case,
)
from app.modules.agents.releases import SCORER_SET_VERSION, load_runtime_manifest, safe_content
from app.modules.intelligence.contracts import Contract, Scope, fingerprint
from app.modules.intelligence.engine_repository import metadata_lock

QUALITY_TABLES = {
    "cases": "CONFIG_AGENT_QUALITY_CASES",
    "runs": "CONFIG_AGENT_QUALITY_RUNS",
    "monitoring": "CONFIG_AGENT_QUALITY_MONITORING",
    "proposals": "CONFIG_AGENT_IMPROVEMENT_PROPOSALS",
}
QUALITY_DDL = tuple(
    f"""
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.{table} (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""
    for table in QUALITY_TABLES.values()
)


class CaseRequest(Contract):
    name: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=16000)
    mandatory: bool = True
    critical: bool = True
    production_match: Literal["exact_prompt", "all_traces"] = "exact_prompt"
    source: Literal["manual", "regression", "production_failure", "verified_query", "feedback"] = (
        "manual"
    )
    assertions: list[Assertion] = Field(min_length=1, max_length=32)
    expected_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def critical_evidence(self):
        if (
            self.mandatory
            and self.critical
            and not any(
                assertion.required and assertion.scorer not in PERFORMANCE_SCORERS
                for assertion in self.assertions
            )
        ):
            raise ValueError("Mandatory critical cases require a behavioral assertion")
        if len({assertion.scorer for assertion in self.assertions}) != len(self.assertions):
            raise ValueError("Use one assertion per scorer in each case")
        return self


class RunRequest(Contract):
    version_id: str = Field(min_length=1, max_length=64)
    case_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    gates: PromotionGates = Field(default_factory=PromotionGates)


class MonitoringRequest(Contract):
    enabled: bool = Field(default=False, strict=True)
    sample_rate: float = Field(default=0.1, gt=0, le=1, strict=True)
    max_traces: int = Field(default=20, ge=1, le=100, strict=True)
    cadence_minutes: int = Field(default=60, ge=15, le=1440, strict=True)
    expected_revision: int = Field(default=0, ge=0, strict=True)


class ProposalReview(Contract):
    expected_revision: int = Field(ge=1)
    resolution: Literal["accepted", "rejected"]
    patch_id: str | None = Field(default=None, max_length=64)
    regression_case_ids: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def distinct_cases(self):
        if len(set(self.regression_case_ids)) != len(self.regression_case_ids):
            raise ValueError("Select each regression case once")
        return self


async def records(
    kind: str, agent_id: str, user: dict, identifier: str | None = None
) -> list[dict]:
    scope = Scope.from_user(user)
    result = await db.execute_system(
        "SELECT payload,branches FROM (SELECT id,payload,ROW_NUMBER() OVER "
        "(PARTITION BY id ORDER BY revision DESC) rn,COUNT(*) OVER "
        f"(PARTITION BY id,revision) branches FROM NOVA_SYSTEM.{QUALITY_TABLES[kind]} "
        "WHERE agent_id=%s AND principal=%s AND active_role=%s AND security_context_version=%s"
        + (" AND id=%s" if identifier else "")
        + ") current_records WHERE rn=1 ORDER BY id LIMIT 101",
        [agent_id, scope.principal, scope.active_role, scope.security_context_version]
        + ([identifier] if identifier else []),
    )
    if len(result["rows"]) > 100 or any(row[1] != 1 for row in result["rows"]):
        raise HTTPException(409, "Quality records require reconciliation or a smaller selection")
    return [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in result["rows"]]


async def save_record(kind: str, record: dict, user: dict, expected_revision: int = 0) -> dict:
    record = deepcopy(record)
    async with metadata_lock(f"agent-quality:{kind}:{record['id']}") as lock:
        return await _save_record_locked(kind, record, user, expected_revision, lock)


async def _save_record_locked(
    kind: str, record: dict, user: dict, expected_revision: int, lock
) -> dict:
    scope = Scope.from_user(user)
    safe_content(record)
    result = await db.execute_system(
        f"SELECT payload,revision FROM NOVA_SYSTEM.{QUALITY_TABLES[kind]} "
        "WHERE id=%s ORDER BY revision DESC LIMIT 2",
        [record["id"]],
    )
    if len(result["rows"]) > 1 and result["rows"][0][1] == result["rows"][1][1]:
        raise HTTPException(409, "Quality revision requires reconciliation")
    old = (
        (
            json.loads(result["rows"][0][0])
            if isinstance(result["rows"][0][0], str)
            else result["rows"][0][0]
        )
        if result["rows"]
        else None
    )
    if old and (
        old["agent_id"] != record["agent_id"]
        or old["scope"] != scope.model_dump(mode="json", exclude={"session_id"})
    ):
        raise HTTPException(404, "Quality record unavailable")
    if (old["revision"] if old else 0) != expected_revision:
        raise HTTPException(409, "Quality record changed; reload before editing")
    if old and kind == "runs":
        frozen_fields = {
            "cases",
            "version_id",
            "manifest_id",
            "manifest_fingerprint",
            "scorer_set_version",
            "gates",
            "source",
            "source_message_id",
            "configuration_revision",
            "case_fingerprint",
        }
        if any(old.get(key) != record.get(key) for key in frozen_fields):
            raise HTTPException(409, "Evaluation inputs are frozen; create a new run")
        if old["status"] != "running":
            raise HTTPException(409, "Completed evaluations are immutable")
    record = {
        **record,
        "revision": expected_revision + 1,
        "scope": scope.model_dump(mode="json", exclude={"session_id"}),
        "created_at": old["created_at"] if old else datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    safe_content(record)
    if not await lock.renew():
        raise HTTPException(409, "Quality metadata lease expired")
    await db.execute_system(
        f"INSERT INTO NOVA_SYSTEM.{QUALITY_TABLES[kind]} "
        "(id,revision,operation_id,agent_id,principal,active_role,"
        "security_context_version,payload,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
        [
            record["id"],
            record["revision"],
            uuid4().hex,
            record["agent_id"],
            scope.principal,
            scope.active_role,
            scope.security_context_version,
            json.dumps(record),
        ],
    )
    return record


class ReadOnlyEvaluationTool:
    def __init__(self, tool):
        self._tool = tool

    def __getattr__(self, name):
        return getattr(self._tool, name)

    async def run(self, invocation, context):
        from app.modules.assistant.tools import ToolOutcome, invocation_classification

        if invocation_classification(self._tool, invocation) != "read_only":
            return ToolOutcome(
                ok=False,
                summary="Evaluation permits read-only tools only",
                error="Evaluation permits read-only tools only",
                error_class="EVALUATION_READ_ONLY",
                recoverable=False,
                safe_detail="Evaluation permits read-only tools only",
            )
        return await self._tool.run(invocation, context)


def evaluation_registry(registry):
    from app.modules.assistant.tools import ToolRegistry

    bounded = ToolRegistry()
    bounded.default_skills = registry.default_skills
    bounded.discoverable_skills = registry.discoverable_skills
    bounded.skill_definitions = deepcopy(registry.skill_definitions)
    if hasattr(registry, "release_manifest"):
        bounded.release_manifest = registry.release_manifest
    for name in registry.names():
        tool = registry.get(name)
        if getattr(tool, "classification", None) == "read_only":
            bounded.register(ReadOnlyEvaluationTool(tool))
    return bounded


def observed_trace(steps: list[dict]) -> dict:
    observation = next(
        (
            step
            for step in reversed(steps)
            if isinstance(step, dict) and step.get("kind") == "quality_observation"
        ),
        None,
    )
    if observation is None:
        return {}
    facts = observation.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    return {
        "facts": deepcopy(facts),
        "evidence": deepcopy(facts.get("evidence")),
        "duration_ms": observation.get("duration_ms"),
        "tool_names": [
            step["name"]
            for step in steps
            if isinstance(step, dict)
            and step.get("kind") == "tool"
            and isinstance(step.get("name"), str)
        ],
        "counts": deepcopy(observation.get("counts")),
    }


def case_result(case: dict, trace: dict, trace_id: str | None, gates: PromotionGates) -> dict:
    status, scores = score_case(
        [Assertion.model_validate(item) for item in case["assertions"]], trace
    )
    facts = trace.get("facts") or {}
    execution = facts.get("execution") or {}
    finish_reason = execution.get("finish_reason") if isinstance(execution, dict) else None
    if finish_reason and finish_reason != "stop":
        status = "failed"
    return {
        "case_id": case["id"],
        "case_revision": case["revision"],
        "status": status,
        "scores": [score.model_dump() for score in scores],
        "trace_id": trace_id,
        "duration_ms": trace.get("duration_ms"),
        "trace": trace,
        "budget_scores": [
            score_assertion(assertion, trace).model_dump()
            for assertion in gates.performance_assertions()
        ],
    }


async def evaluate(
    agent: dict,
    manifest: dict,
    cases: list[dict],
    user: dict,
    gates: PromotionGates | None = None,
) -> dict:
    from app.observability.metrics import studio_operation

    with studio_operation("quality", "evaluate"):
        return await _evaluate(agent, manifest, cases, user, gates or PromotionGates())


async def _evaluate(
    agent: dict, manifest: dict, cases: list[dict], user: dict, gates: PromotionGates
) -> dict:
    from app.modules.agents.service import agent_service, loop_limits
    from app.modules.assistant.context import ContextManager
    from app.modules.assistant.provider import assistant_provider
    from app.modules.assistant.service import AssistantLoop, LoopContext
    from app.modules.assistant.state import AssistantThread

    if not cases or len(cases) > 100 or len({case["id"] for case in cases}) != len(cases):
        raise HTTPException(422, "Select between one and 100 distinct evaluation cases")
    Scope.from_user(user)
    manifest, cases = deepcopy(manifest), deepcopy(cases)
    for case in cases:
        for assertion in case["assertions"]:
            Assertion.model_validate(assertion)
    run = await save_record(
        "runs",
        {
            "id": str(uuid4()),
            "agent_id": agent["agent_id"],
            "version_id": manifest["version_id"],
            "manifest_id": manifest["id"],
            "manifest_fingerprint": manifest["fingerprint"],
            "scorer_set_version": SCORER_SET_VERSION,
            "cases": cases,
            "case_fingerprint": fingerprint(cases),
            "gates": gates.model_dump(mode="json"),
            "source": "evaluation",
            "status": "running",
            "promotion_eligible": False,
            "results": [],
        },
        user,
    )
    cancelled = False
    try:
        pinned = {
            **agent,
            **manifest["dependencies"]["configuration"],
            "release_manifest_id": manifest["id"],
        }
        await load_runtime_manifest(pinned)
        registry, prompt, _, token_budget = await agent_service.build_loop_inputs(pinned)
        limits = loop_limits(pinned)
        registry = evaluation_registry(registry)
        async with asyncio.timeout(600):
            for case in cases:
                context = LoopContext(
                    user_name=user["username"],
                    execution_timezone=configured_timezone(),
                    user=user,
                    role=user["active_role"],
                    session_id=user.get("session_id"),
                    agent_id=agent["agent_id"],
                    agent_owner_name=agent["owner_name"],
                    database=pinned.get("database_name"),
                    schema_name=pinned.get("schema_name"),
                    semantic_view_ids=pinned.get("semantic_view_ids"),
                    release_manifest=manifest,
                    model_provider_id=pinned.get("model_provider_id"),
                    model_name=pinned.get("model_name"),
                    user_question=case["prompt"],
                    steps=[],
                    quality_evaluation=True,
                )

                async def consent(_invocation, classification):
                    return classification == "read_only"

                loop = AssistantLoop(
                    provider=assistant_provider,
                    registry=registry,
                    system_prompt=prompt,
                    iterative=True,
                    max_iterations=limits.max_iterations,
                    time_budget_seconds=min(limits.time_budget_seconds, 180),
                    max_calls_per_tool=limits.max_calls_per_tool,
                    context_manager=ContextManager(token_budget=token_budget)
                    if token_budget
                    else None,
                )
                thread = AssistantThread(
                    thread_id=str(uuid4()), user_name=user["username"], title="Quality evaluation"
                )
                context.thread_id = thread.thread_id
                started = time.monotonic()
                runtime_error = None
                finish_reason = None
                try:
                    async with asyncio.timeout(min(limits.time_budget_seconds, 180) + 1):
                        async for _frame in loop.run(
                            thread=thread,
                            user_content=case["prompt"],
                            context=context,
                            resolve_consent=consent,
                        ):
                            if _frame.startswith("event: done\n"):
                                finish_reason = json.loads(_frame.split("data: ", 1)[1]).get(
                                    "finish_reason"
                                )
                except Exception as exc:
                    runtime_error = type(exc).__name__
                trace = observed_trace(context.steps or [])
                # The elapsed clock is an execution measurement even when observation is disabled.
                trace["duration_ms"] = round((time.monotonic() - started) * 1000)
                if not trace.get("facts") and context.quality_facts:
                    trace["facts"] = deepcopy(context.quality_facts)
                    trace["evidence"] = deepcopy(context.quality_facts.get("evidence"))
                if "tool_names" not in trace:
                    trace["tool_names"] = [
                        step["name"] for step in context.steps or [] if step.get("kind") == "tool"
                    ]
                if not isinstance(trace.get("counts"), dict):
                    trace["counts"] = {}
                if finish_reason is not None:
                    trace.setdefault("facts", {}).setdefault(
                        "execution",
                        {
                            "finish_reason": finish_reason,
                        },
                    )
                result = case_result(case, trace, context.run_id, gates)
                if runtime_error:
                    result.update(status="unavailable", error_class=runtime_error)
                elif finish_reason is None:
                    result["status"] = "unavailable"
                run["results"].append(result)
        run.update(
            status=aggregate_status([item["status"] for item in run["results"]]),
            promotion_eligible=promotion_eligible(cases, run["results"], gates),
            gate_results=gate_results(cases, run["results"], gates),
        )
    except (Exception, asyncio.CancelledError) as exc:
        cancelled = isinstance(exc, asyncio.CancelledError)
        run.update(
            status="unavailable",
            promotion_eligible=False,
            error="Evaluation could not obtain its required runtime evidence",
            error_class=type(exc).__name__,
            diagnosis="DEPENDENCY_DRIFT"
            if isinstance(exc, HTTPException) and exc.status_code == 409
            else "RUNTIME_UNAVAILABLE",
            gate_results=gate_results(cases, run["results"], gates),
        )
    saved = await save_record("runs", run, user, run["revision"])
    if cancelled:
        raise asyncio.CancelledError
    if saved["status"] != "passed":
        await feedback_proposal(agent["agent_id"], saved["id"], user, saved, agent=agent)
    return saved


async def require_promotion(agent_id: str, manifest: dict, run_id: str | None, user: dict) -> dict:
    found = await records("runs", agent_id, user, run_id) if run_id else []
    if not found or len(found) != 1:
        raise HTTPException(409, "Evaluate this release before publishing; select its quality run")
    run = found[0]
    if (
        run["manifest_id"] != manifest["id"]
        or run["scorer_set_version"] != SCORER_SET_VERSION
        or run.get("manifest_fingerprint") != manifest.get("fingerprint")
    ):
        raise HTTPException(409, "Evaluation belongs to different release dependencies")
    # Editing or adding mandatory cases invalidates an earlier promotion result.
    current = await records("cases", agent_id, user)
    frozen = {case["id"]: case["revision"] for case in run["cases"]}
    if any(frozen.get(case["id"]) != case["revision"] for case in current if case["mandatory"]):
        raise HTTPException(409, "Mandatory cases changed; evaluate this release again")
    if any(
        case["id"] not in {item["id"] for item in current}
        for case in run["cases"]
        if case["mandatory"]
    ):
        raise HTTPException(409, "Mandatory cases changed; evaluate this release again")
    gates = PromotionGates.model_validate(run.get("gates") or {})
    current_by_id = {case["id"]: case for case in current}
    gated_cases = [
        case
        for case in run["cases"]
        if (
            case.get("mandatory")
            or gates.other_cases == "all"
            or gates.required_scorers
            or gates.performance == "required"
        )
    ]
    if any(
        current_by_id.get(case["id"], {}).get("revision") != case["revision"]
        for case in gated_cases
    ):
        raise HTTPException(409, "Gated cases changed; evaluate this release again")
    if (
        run.get("source") == "production"
        or run["status"] == "running"
        or run.get("error_class")
        or (run.get("case_fingerprint") and run["case_fingerprint"] != fingerprint(run["cases"]))
        or not run["promotion_eligible"]
        or not promotion_eligible(run["cases"], run["results"], gates)
    ):
        raise HTTPException(
            409, "Mandatory critical cases and configured gates must pass before publication"
        )
    return run


async def configure_monitoring(agent_id: str, body: MonitoringRequest, user: dict) -> dict:
    from types import SimpleNamespace

    from app.modules.intelligence.schedules import configure_schedule, require_execution_binding

    scope = Scope.from_user(user)
    identifier = fingerprint([agent_id, scope.model_dump(exclude={"session_id"})])
    if body.enabled:
        await require_execution_binding(user)
    async with metadata_lock(f"agent-quality:monitoring:{identifier}") as lock:
        current = await records("monitoring", agent_id, user, identifier)
        if (current[0]["revision"] if current else 0) != body.expected_revision:
            raise HTTPException(409, "Monitoring configuration changed; reload before editing")
        # A durable pause prevents a schedule from scoring across a partial configuration write.
        pending = await _save_record_locked(
            "monitoring",
            {
                "id": identifier,
                "agent_id": agent_id,
                **body.model_dump(exclude={"expected_revision"}),
                "enabled": False,
                "desired_enabled": body.enabled,
                "schedule_status": "pending",
                "schedule": current[0].get("schedule") if current else None,
            },
            user,
            body.expected_revision,
            lock,
        )
        if not await lock.renew():
            raise HTTPException(409, "Quality metadata lease expired")
        schedule = await configure_schedule(
            SimpleNamespace(id=identifier),
            user,
            handler="agents.quality",
            enabled=body.enabled,
            cadence=body.cadence_minutes,
        )
        return await _save_record_locked(
            "monitoring",
            {**pending, "enabled": body.enabled, "schedule": schedule, "schedule_status": "ready"},
            user,
            pending["revision"],
            lock,
        )


async def feedback_proposal(
    agent_id: str, message_id: str, user: dict, trace: dict, *, agent: dict | None = None
) -> dict:
    identifier = fingerprint(
        [agent_id, message_id, Scope.from_user(user).model_dump(exclude={"session_id"})]
    )
    async with metadata_lock(f"agent-quality:proposals:{identifier}") as lock:
        existing = await records("proposals", agent_id, user, identifier)
        if existing:
            if (
                agent is not None
                and existing[0]["status"] == "proposed"
                and not existing[0].get("patches")
            ):
                from app.modules.agents.doctor import report

                diagnosis = await report(
                    agent, user, await records("runs", agent_id, user), current_run=trace
                )
                return await _save_record_locked(
                    "proposals",
                    {
                        **existing[0],
                        "doctor": diagnosis,
                        "patches": diagnosis["patches"],
                        "regression_candidates": diagnosis["regression_candidates"],
                    },
                    user,
                    existing[0]["revision"],
                    lock,
                )
            return existing[0]
        failures = sorted(
            {
                score.get("failure_taxonomy")
                for result in trace.get("results", [])
                for score in [*result.get("scores", []), *result.get("budget_scores", [])]
                if score.get("failure_taxonomy")
            }
        )
        observations = [
            {
                "run_id": trace.get("id"),
                "case_id": result["case_id"],
                "case_revision": result["case_revision"],
                "scorer": score["scorer"],
                "status": score["status"],
                "detail": score["detail"],
            }
            for result in trace.get("results", [])
            for score in [*result.get("scores", []), *result.get("budget_scores", [])]
            if score["status"] != "pass"
        ][:100]
        diagnosis = {}
        if agent is not None:
            from app.modules.agents.doctor import report

            diagnosis = await report(
                agent, user, await records("runs", agent_id, user), current_run=trace
            )
        return await _save_record_locked(
            "proposals",
            {
                "id": identifier,
                "agent_id": agent_id,
                "source_message_id": message_id,
                "status": "proposed",
                "diagnosis": failures or [trace.get("diagnosis") or "REVIEW_REQUIRED"],
                "observations": observations,
                "run_id": trace.get("id"),
                "hypothesis": True,
                **(
                    {
                        "doctor": diagnosis,
                        "patches": diagnosis["patches"],
                        "regression_candidates": diagnosis["regression_candidates"],
                    }
                    if diagnosis
                    else {}
                ),
                "suggestion": (
                    "Review the evidence and add a regression case before changing the release."
                ),
            },
            user,
            0,
            lock,
        )
