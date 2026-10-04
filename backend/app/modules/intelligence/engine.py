"""Governed monitoring, investigations, and decision lifecycle operations."""

from __future__ import annotations

import asyncio
import json
import math
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Literal, TypeVar, cast
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, model_validator

from app.common.audit import write_audit_log
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.assistant.measurements import MAX_COUNT
from app.modules.intelligence.action_contracts import Action, ActionEvent
from app.modules.intelligence.contracts import (
    Confidence,
    ContextEdge,
    ContextNode,
    Contract,
    Decision,
    DecisionEvent,
    EvidenceRef,
    Hypothesis,
    Investigation,
    MetricObservation,
    Monitor,
    MonitorConfiguration,
    NewsItem,
    Outcome,
    OutcomeLearningRef,
    Record,
    Scope,
    SemanticRef,
    Window,
    fingerprint,
    utc_now,
)
from app.modules.intelligence.cursors import read_cursor, write_cursor
from app.modules.intelligence.engine_repository import intelligence_repository, metadata_lock
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.ml_engine.analysis import detect_change, outcome_dimensions

R = TypeVar("R", bound=Record)
MODELS = {
    "nodes": ContextNode,
    "edges": ContextEdge,
    "monitors": Monitor,
    "observations": MetricObservation,
    "news": NewsItem,
    "investigations": Investigation,
    "decisions": Decision,
    "events": DecisionEvent,
    "outcomes": Outcome,
    "actions": Action,
    "action_events": ActionEvent,
}


class ChatInvestigationRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    configuration: MonitorConfiguration
    current_window: Window | None = None
    baseline_window: Window | None = None
    calendar_timezone: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def complete_windows(self):
        if self.configuration.enabled:
            raise ValueError("Chat investigations require a disabled comparison monitor")
        if self.calendar_timezone and self.calendar_timezone != self.configuration.timezone:
            raise ValueError("Calendar comparison must use the monitor timezone")
        if (
            self.current_window
            and self.baseline_window
            and (
                self._span(self.current_window) != self._span(self.baseline_window)
                or self.baseline_window.end > self.current_window.start
            )
        ):
            raise ValueError("Use equal length, nonoverlapping comparison windows")
        return self

    def _span(self, window: Window) -> timedelta:
        if self.calendar_timezone:
            try:
                zone = ZoneInfo(self.calendar_timezone)
            except (KeyError, ValueError) as exc:
                raise ValueError("Use a named IANA timezone") from exc
            return (window.end.astimezone(zone).replace(tzinfo=None)
                    - window.start.astimezone(zone).replace(tzinfo=None))
        return window.end - window.start


class ChatComparison(Record):
    request_digest: str
    semantic: SemanticRef
    monitor_id: str
    monitor_revision: int | None = Field(default=None, ge=1, exclude_if=lambda value: value is None)
    configuration: MonitorConfiguration | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    automatic_mission_id: str | None = Field(
        default=None, max_length=128, exclude_if=lambda value: value is None
    )
    automatic_operation_id: str | None = Field(
        default=None, max_length=128, exclude_if=lambda value: value is None
    )
    investigation_revision: int | None = Field(
        default=None, ge=1, exclude_if=lambda value: value is None
    )
    current_window: Window
    baseline_window: Window
    status: str = "pending"
    reason: str | None = None
    observation_ids: list[str] = Field(default_factory=list, max_length=2)
    news_id: str | None = None
    investigation_id: str | None = None
    calendar_timezone: str | None = None


MODELS["comparisons"] = ChatComparison


QueryPurpose = Literal["comparison", "driver", "other"]
CanonicalizationStatus = Literal["created", "reused", "incomplete", "skipped", "failed"]
MAX_DURATION_MS = MAX_COUNT * 1000


class CanonicalizationMetrics(Contract):
    version: Literal["1"] = "1"
    scope: Literal["current_operation_attempt"] = "current_operation_attempt"
    query_count_basis: Literal["semantic_execute_plan_attempts"] = "semantic_execute_plan_attempts"
    business_canonicalization_status: CanonicalizationStatus = "skipped"
    business_canonicalization_duration_ms: float = Field(default=0, ge=0, le=MAX_DURATION_MS)
    automatic_investigation_query_count: int = Field(default=0, strict=True, ge=0, le=MAX_COUNT)
    comparison_query_count: int = Field(default=0, strict=True, ge=0, le=MAX_COUNT)
    driver_query_count: int = Field(default=0, strict=True, ge=0, le=MAX_COUNT)
    other_query_count: int = Field(default=0, strict=True, ge=0, le=MAX_COUNT)
    query_cache_reuse_count: int = Field(default=0, strict=True, ge=0, le=MAX_COUNT)
    query_duration_ms: float = Field(default=0, ge=0, le=MAX_DURATION_MS)
    persistence_duration_ms: float = Field(default=0, ge=0, le=MAX_DURATION_MS)
    investigation_persistence_duration_ms: float = Field(default=0, ge=0, le=MAX_DURATION_MS)
    canonical_investigation_created: bool = False
    canonical_investigation_reused: bool = False
    unavailable: list[Literal["counter_limit_exceeded", "duration_limit_exceeded"]] = Field(
        default_factory=list, max_length=2,
    )


@dataclass
class CanonicalizationCollector:
    """One attempt's safe counters, shared by its existing Intelligence budgets."""

    _metrics: CanonicalizationMetrics = field(default_factory=CanonicalizationMetrics, init=False)
    _depth: int = field(default=0, init=False)
    _started: float | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def _unavailable(self, reason: Literal["counter_limit_exceeded", "duration_limit_exceeded"]):
        if reason not in self._metrics.unavailable:
            self._metrics.unavailable.append(reason)

    def record_query(self, purpose: QueryPurpose, *, cache_reuse: bool = False) -> None:
        if purpose not in {"comparison", "driver", "other"}:
            raise ValueError("Unknown canonicalization query purpose")
        if self._closed:
            return
        names = (["query_cache_reuse_count"] if cache_reuse else
                 ["automatic_investigation_query_count", f"{purpose}_query_count"])
        for name in names:
            value = getattr(self._metrics, name)
            if value >= MAX_COUNT:
                self._unavailable("counter_limit_exceeded")
            else:
                setattr(self._metrics, name, value + 1)

    def record_duration(
        self, kind: Literal["query", "persistence", "investigation_persistence"], started: float,
        *, ended: float | None = None,
    ) -> None:
        if kind not in {"query", "persistence", "investigation_persistence"}:
            raise ValueError("Unknown canonicalization duration")
        if self._closed:
            return
        name = f"{kind}_duration_ms"
        elapsed = (monotonic() if ended is None else ended) - started
        value = getattr(self._metrics, name) + max(0, elapsed * 1000)
        if not math.isfinite(value) or value > MAX_DURATION_MS:
            self._unavailable("duration_limit_exceeded")
            value = MAX_DURATION_MS
        setattr(self._metrics, name, value)

    def investigation(self, *, created: bool) -> None:
        if not self._closed:
            name = ("canonical_investigation_created" if created
                    else "canonical_investigation_reused")
            setattr(self._metrics, name, True)

    def complete(self, status: CanonicalizationStatus) -> None:
        if status not in {"created", "reused", "incomplete", "skipped", "failed"}:
            raise ValueError("Unknown canonicalization status")
        if not self._closed:
            self._metrics.business_canonicalization_status = status

    def result(self, result: dict) -> None:
        if result.get("status") != "complete" or result.get("investigation") is None:
            self.complete("incomplete")
        elif self._metrics.canonical_investigation_created:
            self.complete("created")
        else:
            self.complete("reused")

    def snapshot(self) -> CanonicalizationMetrics:
        metrics = self._metrics.model_copy(deep=True)
        if self._started is not None and not self._closed:
            duration = max(0, (monotonic() - self._started) * 1000)
            if not math.isfinite(duration) or duration > MAX_DURATION_MS:
                if "duration_limit_exceeded" not in metrics.unavailable:
                    metrics.unavailable.append("duration_limit_exceeded")
                duration = MAX_DURATION_MS
            metrics.business_canonicalization_duration_ms = duration
        return metrics

    @contextmanager
    def operation(self):
        if self._closed:
            raise ValueError("Canonicalization collector covers one operation attempt")
        if self._depth == 0:
            self._started = monotonic()
            self.complete("failed")
        self._depth += 1
        token = _canonicalization.set(self)
        try:
            yield self
        except BaseException:
            self.complete("failed")
            raise
        finally:
            _canonicalization.reset(token)
            self._depth -= 1
            if self._depth == 0:
                self._metrics = self.snapshot()
                self._closed = True


_canonicalization: ContextVar[CanonicalizationCollector | None] = ContextVar(
    "intelligence_canonicalization", default=None,
)


def current_canonicalization() -> CanonicalizationCollector | None:
    collector = _canonicalization.get()
    return collector if collector is not None and not collector._closed else None


@dataclass
class CycleBudget:
    queries: int = 0
    investigations: int = 0
    items: int = 0
    started: float = 0
    checked_evidence: set[str] = field(default_factory=set)
    query_results: dict[str, tuple[dict, EvidenceRef]] = field(default_factory=dict)
    semantic_authorizations: dict[str, dict] = field(default_factory=dict)
    mission_bindings: list[Scope] = field(default_factory=list)
    mission_records: dict[tuple[str, str], int | None] = field(default_factory=dict)
    mission_record_bindings: dict[tuple[str, str], Scope] = field(default_factory=dict)
    canonicalization: CanonicalizationCollector | None = field(
        default_factory=current_canonicalization, repr=False,
    )
    comparison_news: NewsItem | None = field(default=None, repr=False)

    def __post_init__(self):
        self.started = monotonic()

    def consume(self, dimension: str, count: int = 1) -> None:
        limits = {"queries": 20, "investigations": 3, "items": 100}
        if (
            monotonic() - self.started >= 120
            or getattr(self, dimension) + count > limits[dimension]
        ):
            raise HTTPException(status_code=429, detail="Intelligence cycle budget exhausted")
        setattr(self, dimension, getattr(self, dimension) + count)


def table_digest(result: dict) -> str:
    rows = sorted(json.dumps(row, default=str, sort_keys=True) for row in result["rows"])
    return fingerprint({"columns": result["columns"], "rows": rows})


def window_plan(monitor: Monitor, window: Window, dimension: str | None = None) -> SemanticPlan:
    value = dict(monitor.plan)
    value["time"] = None
    value["dimensions"] = [dimension] if dimension else []
    if dimension:
        value["metrics"] = [monitor.value_column]
    value["limit"] = 1000 if dimension else 2
    value["filters"] = [
        *(value.get("filters") or []),
        {
            "field": monitor.time_dimension,
            "operator": ">=",
            "value": window.start.astimezone(ZoneInfo(monitor.timezone)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            ),
        },
        {
            "field": monitor.time_dimension,
            "operator": "<",
            "value": window.end.astimezone(ZoneInfo(monitor.timezone)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            ),
        },
    ]
    return SemanticPlan.from_dict(value)


class IntelligenceService:
    def __init__(self, repository=intelligence_repository, semantic=semantic_view_service):
        self.repository = repository
        self.semantic = semantic

    async def _audit(self, action: str, record: Record, user: dict) -> None:
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user["username"],
            action=action,
            object_type=type(record).__name__,
            object_name=record.id,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )

    async def authorize_semantic(
        self,
        ref: SemanticRef,
        user: dict,
        *,
        active=False,
        budget: CycleBudget | None = None,
    ) -> dict:
        allowed_views = user.get("intelligence_allowed_views")
        if allowed_views is not None and ref.view_id not in allowed_views:
            raise HTTPException(status_code=404, detail="Semantic View is not bound to this agent")
        key = fingerprint([Scope.from_user(user).model_dump(), ref.model_dump(), active])
        if budget:
            budget.consume("items", 0)
            if key in budget.semantic_authorizations:
                return budget.semantic_authorizations[key]
        try:
            async with asyncio.timeout(
                max(0.01, 120 - (monotonic() - budget.started)) if budget else 120
            ):
                view, row = await self.semantic._readable_version(ref.view_id, ref.version, user)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=429, detail="Intelligence cycle budget exhausted"
            ) from exc
        if row["fingerprint"] != ref.fingerprint:
            raise HTTPException(status_code=409, detail="Semantic definition changed")
        if active and (view["active_version"] != ref.version or row["status"] != "ACTIVE"):
            raise HTTPException(
                status_code=409, detail="Semantic version is stale; revalidate first"
            )
        if budget:
            budget.semantic_authorizations[key] = row
        return row

    async def query(
        self, ref: SemanticRef, plan: SemanticPlan, user: dict, budget: CycleBudget, *,
        purpose: QueryPurpose = "other",
    ) -> tuple[dict, EvidenceRef]:
        budget.consume("queries", 0)
        allowed_views = user.get("intelligence_allowed_views")
        if allowed_views is not None and ref.view_id not in allowed_views:
            raise HTTPException(status_code=404, detail="Semantic View is not bound to this agent")
        cache_key = fingerprint(
            [Scope.from_user(user).model_dump(), ref.model_dump(), plan.as_dict()]
        )
        if cache_key in budget.query_results:
            if budget.canonicalization:
                budget.canonicalization.record_query(purpose, cache_reuse=True)
            return budget.query_results[cache_key]
        budget.consume("queries")
        started = monotonic()
        if budget.canonicalization:
            budget.canonicalization.record_query(purpose)
        try:
            async with asyncio.timeout(max(0.01, 120 - (monotonic() - budget.started))):
                result = await self.semantic.execute_plan(ref.view_id, ref.version, plan, user)
        finally:
            if budget.canonicalization:
                budget.canonicalization.record_duration("query", started)
        if result["model_fingerprint"] != ref.fingerprint:
            raise HTTPException(status_code=409, detail="Semantic definition changed")
        digest = table_digest(result)
        evidence = EvidenceRef(
            id=fingerprint(
                [Scope.from_user(user).model_dump(), ref.model_dump(), plan.as_dict(), digest]
            ),
            source_type="query",
            source_id=str(result.get("query_id") or fingerprint(plan.as_dict())),
            scope=Scope.from_user(user),
            semantic=ref,
            method="semantic-compiler-v1",
            digest=digest,
            observed_at=utc_now(),
            semantic_plan=plan.as_dict(),
        )
        budget.query_results[cache_key] = (result, evidence)
        return result, evidence

    async def authorize_record(self, record: Record, user: dict, budget: CycleBudget) -> None:
        budget.consume("items", 0)
        scope = Scope.from_user(user)
        same_scope = (
            record.scope.principal,
            record.scope.active_role,
            record.scope.security_context_version,
        ) == (scope.principal, scope.active_role, scope.security_context_version)
        mission_authorized = any(record.scope == binding for binding in budget.mission_bindings)
        if not same_scope and not mission_authorized and (
            not isinstance(record, Decision)
            or not await self._decision_grant(record.id, user, record.scope.principal)
        ):
            raise HTTPException(status_code=404, detail="Record unavailable")
        ref = getattr(record, "semantic", None)
        if ref:
            await self.authorize_semantic(ref, user, budget=budget)
        for evidence in getattr(record, "evidence", []):
            check_key = fingerprint([evidence.model_dump(mode="json"), scope.model_dump()])
            if check_key in budget.checked_evidence:
                continue
            if evidence.source_type == "query":
                if not evidence.semantic or not evidence.semantic_plan:
                    raise HTTPException(status_code=404, detail="Evidence unavailable")
                result, _ = await self.query(
                    evidence.semantic, SemanticPlan.from_dict(evidence.semantic_plan), user, budget
                )
                if table_digest(result) != evidence.digest:
                    raise HTTPException(
                        status_code=409, detail="Evidence changed; refresh the investigation"
                    )
            elif evidence.semantic:
                await self.authorize_semantic(evidence.semantic, user, budget=budget)
            budget.checked_evidence.add(check_key)
        if isinstance(record, ContextEdge):
            for node_id in (record.source, record.target):
                await self.get("nodes", node_id, user, budget=budget)
        if isinstance(record, ContextNode) and (
            record.kind in {
                "mission", "action", "deliverable", "agent_release", "dashboard", "artifact",
                "document", "agent", "skill", "tool",
            } or (
                record.kind in {"decision", "outcome", "news", "investigation", "policy"}
                and record.reference_parent_id is not None
            )
        ):
            from app.modules.intelligence.context_sources import authorize_context_reference

            await authorize_context_reference(record, user, budget)
        if (
            isinstance(record, ContextNode) and record.reference_parent_id is None
            and record.kind in {
                "decision",
                "outcome",
                "news",
                "investigation",
            }
        ):
            kind = {
                "decision": "decisions",
                "outcome": "outcomes",
                "news": "news",
                "investigation": "investigations",
            }[record.kind]
            await self.get(
                kind,
                record.reference_id,
                user,
                budget=budget,
                revision=record.reference_revision,
            )
        if isinstance(record, ContextNode) and record.kind == "rule":
            from app.modules.agents.memory import governed_memories, memory_repository
            from app.modules.agents.router import _require_agent

            if not record.reference_agent_id or not record.reference_revision:
                raise HTTPException(status_code=404, detail="Knowledge reference unavailable")
            await _require_agent(record.reference_agent_id, user)
            revisions = await memory_repository.revisions(
                record.reference_id,
                user_name=scope.principal,
                agent_id=record.reference_agent_id,
                role_name=scope.active_role,
            )
            pinned = next(
                (row for row in revisions if row.revision == record.reference_revision), None
            )
            if pinned is None or not await governed_memories(
                [{"knowledge": pinned.model_dump(mode="json")}], user
            ):
                raise HTTPException(status_code=404, detail="Knowledge reference unavailable")
        if isinstance(record, DecisionEvent):
            await self.get("decisions", record.decision_id, user, budget=budget)
        if isinstance(record, Action):
            if mission_authorized:
                budget.mission_records[("decisions", record.decision_id)] = record.decision_revision
            await self.get("decisions", record.decision_id, user, budget=budget,
                           revision=record.decision_revision if mission_authorized else None)
        if isinstance(record, ActionEvent):
            await self.get("actions", record.action_id, user, budget=budget)
        if isinstance(record, Outcome):
            if mission_authorized:
                budget.mission_records[("decisions", record.decision_id)] = record.decision_revision
            # Outcome errors and impact also derive from the prediction and
            # baseline, whose authorization can change independently of actuals.
            decision = await self.get(
                "decisions",
                record.decision_id,
                user,
                budget=budget,
                revision=record.decision_revision,
            )
            for action_id in record.action_ids:
                action = await self.get("actions", action_id, user, budget=budget)
                if action.decision_id != record.decision_id or action.semantic != record.semantic:
                    raise HTTPException(status_code=409, detail="Outcome action lineage changed")
            if record.learning_refs:
                await self._require_outcome_learning(record, decision.agent_id)
        if isinstance(record, Decision):
            from app.modules.intelligence.decisions import decision_digest

            proof = await self.repository.get(
                "events",
                fingerprint([record.id, record.last_operation_id]),
                record.scope,
                DecisionEvent,
            )
            if (
                proof is None
                or proof.decision_revision != record.revision
                or proof.context_digest != decision_digest(record)
            ):
                raise HTTPException(
                    status_code=409, detail="Decision lineage is incomplete; retry the operation"
                )

    async def get(
        self,
        kind: str,
        record_id: str,
        user: dict,
        *,
        budget: CycleBudget | None = None,
        revision: int | None = None,
    ) -> Record:
        budget = budget or CycleBudget()
        record = await self.repository.get(
            kind, record_id, Scope.from_user(user), MODELS[kind], revision=revision
        )
        if record is None and (kind, record_id) in budget.mission_records:
            pinned = budget.mission_records[(kind, record_id)]
            if revision is not None and pinned != revision:
                raise HTTPException(status_code=404, detail="Mission revision unavailable")
            pinned_binding = budget.mission_record_bindings.get((kind, record_id))
            bindings = [pinned_binding] if pinned_binding else budget.mission_bindings
            for binding in bindings:
                record = await self.repository.get(
                    kind, record_id, binding, MODELS[kind], revision=pinned,
                )
                if record is not None:
                    if record.scope != binding:
                        raise HTTPException(status_code=404, detail="Mission binding unavailable")
                    break
        pinned_binding = budget.mission_record_bindings.get((kind, record_id))
        if record is not None and pinned_binding is not None and record.scope != pinned_binding:
            raise HTTPException(status_code=404, detail="Mission binding unavailable")
        if record is None and kind == "decisions":
            grant = await self._decision_grant(record_id, user)
            if grant:
                record = await self.repository.shared_decision(
                    record_id, grant["owner_name"], Decision
                )
                if record is not None and revision is not None:
                    record = await self.repository.get(
                        "decisions", record_id, record.scope, Decision, revision=revision
                    )
        if record is None:
            raise HTTPException(status_code=404, detail="Record unavailable")
        await self.authorize_record(record, user, budget)
        return record

    async def get_for_mission(
        self, kind: str, record_id: str, user: dict, *, mission_id: str,
        revision: int | None = None, budget: CycleBudget | None = None,
    ) -> Record:
        """Read an immutable Mission pin against current caller authorization."""
        from app.modules.agents.mission import (
            mission_service,
            require_thread,
            require_workflow,
            workflow_scope,
        )
        from app.modules.agents.mission_schema import object_binding_key

        canonical = {"investigations": "investigation", "decisions": "decision",
                     "actions": "action", "outcomes": "outcome"}
        if kind not in canonical or (revision is not None and revision < 1):
            raise HTTPException(status_code=404, detail="Mission record unavailable")
        require_workflow()
        scope = workflow_scope(user)
        mission = await mission_service._get_owner(mission_id, scope)
        await require_thread(mission.thread_id, user)
        pins = [ref for ref in (mission.pinned_objects if revision is not None
                                else mission.object_refs)
                if ref.kind == canonical[kind] and ref.id == record_id
                and (revision is None or ref.revision == revision)]
        if len(pins) != 1:
            raise HTTPException(status_code=404, detail="Mission record unavailable")
        revision = pins[0].revision
        budget = budget or CycleBudget()
        bindings = [mission.scope, *mission.run_bindings.values(),
                    *mission.object_bindings.values()]
        for binding in bindings:
            if (binding.principal, binding.active_role) != (scope.principal, scope.active_role):
                raise HTTPException(status_code=404, detail="Mission owner scope changed")
            if binding not in budget.mission_bindings:
                budget.mission_bindings.append(binding)
        for ref in mission.object_refs:
            table = next(table for table, name in canonical.items() if name == ref.kind)
            budget.mission_records[(table, ref.id)] = ref.revision
            budget.mission_record_bindings[(table, ref.id)] = mission.object_bindings.get(
                object_binding_key(ref), mission.scope
            )
        pin = pins[0]
        budget.mission_records[(kind, record_id)] = pin.revision
        budget.mission_record_bindings[(kind, record_id)] = mission.object_bindings.get(
            object_binding_key(pin), mission.scope
        )
        return await self.get(kind, record_id, user, budget=budget, revision=revision)

    async def mission_investigation_context(
        self, mission_id: str, investigation_id: str, revision: int, user: dict,
    ) -> dict:
        budget = CycleBudget()
        investigation = await self.get_for_mission(
            "investigations", investigation_id, user, mission_id=mission_id,
            revision=revision, budget=budget,
        )
        budget.mission_records[("news", investigation.news_id)] = investigation.news_revision
        budget.mission_record_bindings[("news", investigation.news_id)] = investigation.scope
        news = await self.get("news", investigation.news_id, user, budget=budget,
                              revision=investigation.news_revision)
        budget.mission_records[("monitors", news.monitor_id)] = news.monitor_revision
        budget.mission_record_bindings[("monitors", news.monitor_id)] = news.scope
        monitor = await self.get("monitors", news.monitor_id, user, budget=budget,
                                 revision=news.monitor_revision)
        return {"investigation": investigation, "news": news, "monitor": monitor}

    @staticmethod
    async def _decision_grant(record_id, user, owner=None):
        from app.modules.agents.sharing import share_repository

        grants = await share_repository.active_grants(
            user,
            object_type="decision",
            object_id=record_id,
            owner_name=owner,
            limit=1,
        )
        return grants[0] if grants else None

    async def page(
        self,
        kind: str,
        user: dict,
        *,
        after: str = "",
        limit: int = 20,
        search: str | None = None,
    ) -> dict:
        budget, visible, scope = CycleBudget(), [], Scope.from_user(user)
        cursor_kind = kind if search is None else f"{kind}:{fingerprint(search)}"
        position = await read_cursor(after, cursor_kind, scope)
        rows = await self.repository.page(
            kind,
            scope,
            MODELS[kind],
            after=position,
            limit=101,
            **({"search": search} if search is not None else {}),
        )
        more = len(rows) > 100
        for index, row in enumerate(rows[:100]):
            try:
                budget.consume("items")
                await self.authorize_record(row, user, budget)
            except HTTPException as exc:
                if exc.status_code == 429:
                    if index == 0:
                        raise
                    more = True
                    break
                if exc.status_code not in {403, 404, 409}:
                    raise
            else:
                visible.append(row)
            position = row.id
            if len(visible) >= min(limit, 20):
                more = index + 1 < len(rows)
                break
        return {
            "items": visible,
            "next_after": await write_cursor(position, cursor_kind, scope) if more else None,
        }

    async def validate_monitor(self, monitor: Monitor, user: dict) -> None:
        row = await self.authorize_semantic(monitor.semantic, user, active=True)
        ir = SemanticModelIR.from_ossie(row["definition"])
        plan = SemanticPlan.from_dict(monitor.plan)
        if monitor.completeness_column and monitor.completeness_column not in plan.metrics:
            raise HTTPException(status_code=422, detail="Include the data completeness metric")
        if (
            monitor.value_column not in plan.metrics
            or (monitor.count_column is not None and monitor.count_column not in plan.metrics)
            or (monitor.enabled and monitor.count_column is None)
            or plan.dimensions
            or plan.time
            or plan.having
            or plan.transforms
            or plan.top_n_per_group
        ):
            raise HTTPException(
                status_code=422, detail="A monitor requires scalar metrics and explicit windows"
            )
        target_metric = ir.metric(monitor.value_column)
        if (
            monitor.driver_dimensions
            and target_metric
            and target_metric.additivity.value != "additive"
        ):
            raise HTTPException(
                status_code=422, detail="Arithmetic driver contributions require an additive metric"
            )
        for dimension in [None, *monitor.driver_dimensions]:
            compiled_plan = window_plan(
                monitor, Window(start=utc_now() - timedelta(days=1), end=utc_now()), dimension
            )
            SemanticCompiler().compile(ir, compiled_plan)
        for source in monitor.timeline_sources:
            event_plan = SemanticPlan.from_dict(source.plan)
            if event_plan.time or event_plan.limit is None or event_plan.limit > 31:
                raise HTTPException(
                    status_code=422,
                    detail="Timeline plans require explicit windows and at most 31 rows",
                )
            field = ir.field(source.time_dimension)
            if field is None or not field.is_time:
                raise HTTPException(
                    status_code=422, detail="Timeline time dimension is unavailable"
                )
            SemanticCompiler().compile(ir, event_plan)

    async def register_monitor(
        self, monitor: Monitor, user: dict, *, expected_revision=0
    ) -> Monitor:
        await self.validate_monitor(monitor, user)
        monitor.scope = Scope.from_user(user)
        saved = await self.repository.save("monitors", monitor, expected_revision=expected_revision)
        await self._audit("REGISTER", saved, user)
        return saved

    async def observe(
        self, monitor: Monitor, window: Window, user: dict, budget: CycleBudget, *, _mission=None,
    ) -> MetricObservation:
        async with self._automatic_fence(_mission, user):
            pass
        result, evidence = await self.query(
            monitor.semantic, window_plan(monitor, window), user, budget, purpose="comparison",
        )
        if len(result["rows"]) != 1:
            raise HTTPException(status_code=422, detail="Monitor needs exactly one aggregate row")
        row = dict(zip(result["columns"], result["rows"][0], strict=True))
        value = row.get(monitor.value_column)
        count = row.get(monitor.count_column) if monitor.count_column else None
        if monitor.count_column and (
            count is None or Decimal(str(count)) != int(count) or count < 0
        ):
            raise HTTPException(status_code=422, detail="The observation is incomplete")
        if value is None and count is not None and count > 0:
            raise HTTPException(status_code=422, detail="The observation is incomplete")
        evidence.window_start, evidence.window_end = window.start, window.end
        execution_scope = Scope.from_user(user)
        identity = [monitor.id, monitor.revision, window.model_dump(), evidence.digest]
        if monitor.scope.model_dump(exclude={"session_id"}) != execution_scope.model_dump(
            exclude={"session_id"}
        ):
            identity.append(execution_scope.model_dump(exclude={"session_id"}))
        observation = MetricObservation(
            id=fingerprint(identity),
            scope=execution_scope,
            monitor_id=monitor.id,
            monitor_revision=monitor.revision,
            semantic=monitor.semantic,
            window=window,
            value=float(value) if value is not None else None,
            sample_count=int(count) if count is not None else None,
            completeness=row.get(monitor.completeness_column)
            if monitor.completeness_column
            else None,
            evidence=[evidence],
        )
        prior = await self.repository.get(
            "observations", observation.id, Scope.from_user(user), MetricObservation
        )
        return prior or await self._save_automatic(
            "observations", observation, user, _mission, collector=budget.canonicalization,
        )

    async def run_monitor(
        self, monitor_id: str, window: Window, user: dict, budget: CycleBudget | None = None
    ) -> dict:
        budget = budget or CycleBudget()
        monitor = await self.get("monitors", monitor_id, user, budget=budget)
        if monitor.count_column is None:
            raise HTTPException(
                status_code=422, detail="Scheduled monitoring needs reviewed counts"
            )
        await self.authorize_semantic(monitor.semantic, user, active=True, budget=budget)
        if window.end > utc_now() or window.end - window.start != timedelta(
            hours=monitor.window_hours
        ):
            raise HTTPException(
                status_code=422, detail="Use a complete window of the configured length"
            )
        observations = [await self.observe(monitor, window, user, budget)]
        for week in range(1, monitor.baseline_weeks + 1):
            delta = timedelta(weeks=week)
            observations.append(
                await self.observe(
                    monitor,
                    Window(start=window.start - delta, end=window.end - delta),
                    user,
                    budget,
                )
            )
        current, *baselines = observations
        if any(row.value is None for row in observations):
            return {
                "observation_id": current.id,
                "detection": {
                    "detected": False,
                    "reason": "missing_observations",
                    "method": "matched-weekday-median-mad-v1",
                },
                "news_id": None,
            }
        detection = detect_change(
            [row.value for row in baselines],
            current.value,
            sample_count=min(row.sample_count for row in observations),
            minimum_samples=monitor.minimum_samples,
            relative_threshold=monitor.relative_threshold,
            absolute_threshold=monitor.absolute_threshold,
        )
        if not detection["detected"]:
            return {"observation_id": current.id, "detection": detection, "news_id": None}
        dedup = fingerprint(
            [
                monitor.id,
                monitor.semantic.model_dump(),
                window.model_dump(),
                "increase" if detection["change"] > 0 else "decrease",
            ]
        )
        prior = await self.repository.get("news", dedup, Scope.from_user(user), NewsItem)
        evidence = [entry for row in observations for entry in row.evidence]
        if prior and [item.digest for item in prior.evidence] == [item.digest for item in evidence]:
            return {"observation_id": current.id, "detection": detection, "news_id": prior.id}
        news = NewsItem(
            id=dedup,
            dedup_key=dedup,
            scope=Scope.from_user(user),
            monitor_id=monitor.id,
            monitor_revision=monitor.revision,
            semantic=monitor.semantic,
            window=window,
            title=f"{monitor.name}: material change",
            summary="Change against matched weekday baselines. The cause has not been established.",
            before=detection["before"],
            after=detection["after"],
            change=detection["change"],
            relative_change=detection["relative_change"],
            severity=detection["severity"],
            confidence=Confidence(
                dimension="detection",
                method=detection["method"],
                label="medium",
                evidence_ids=[item.id for item in evidence],
            ),
            evidence=evidence,
            baseline_windows=[
                row.window
                for row in sorted(baselines, key=lambda row: row.value)[
                    (len(baselines) - 1) // 2 : len(baselines) // 2 + 1
                ]
            ],
        )
        async with metadata_lock(f"incident:{monitor.id}"):
            current_news = await self.repository.get("news", dedup, Scope.from_user(user), NewsItem)
            if current_news:
                if [item.digest for item in current_news.evidence] == [
                    item.digest for item in evidence
                ]:
                    return {
                        "observation_id": current.id,
                        "detection": detection,
                        "news_id": current_news.id,
                    }
                news = await self.repository.save(
                    "news",
                    news.model_copy(
                        update={
                            "scope": current_news.scope,
                            "created_at": current_news.created_at,
                            "status_note": (
                                "Observation data changed; investigate the revised evidence."
                            ),
                        }
                    ),
                    expected_revision=current_news.revision,
                )
                await self._audit("REFRESH_DETECTION", news, user)
                return {"observation_id": current.id, "detection": detection, "news_id": news.id}
            prior = await self.repository.recent_incident(news, monitor.cooldown_hours)
            if prior:
                await self.authorize_record(prior, user, budget)
                return {"observation_id": current.id, "detection": detection, "news_id": prior.id}
            news = await self.repository.save("news", news)
        await self._audit("DETECT", news, user)
        return {"observation_id": current.id, "detection": detection, "news_id": news.id}

    async def investigate(
        self, news_id: str, user: dict, *, arithmetic_only: bool = False,
        budget: CycleBudget | None = None, _mission=None,
    ) -> Investigation:
        budget = budget or CycleBudget()
        budget.consume("investigations")
        news = await self.get("news", news_id, user, budget=budget)
        monitor = await self.get(
            "monitors", news.monitor_id, user, budget=budget, revision=news.monitor_revision
        )
        historical = budget.mission_record_bindings.get(("news", news.id)) == news.scope
        record_scope = news.scope if historical else Scope.from_user(user)
        await self.authorize_semantic(news.semantic, user, active=not historical, budget=budget)
        if monitor.semantic != news.semantic:
            raise HTTPException(status_code=409, detail="Monitor changed; revalidate the incident")
        arithmetic_only = (arithmetic_only and monitor.count_column is None
                           and not monitor.enabled
                           and news.confidence.method == "explicit-window-comparison-v1")
        if news.confidence.label == "insufficient" and not arithmetic_only:
            return await self.incomplete_investigation(news, user, budget=budget, _mission=_mission)
        prior = (
            await self.repository.get(
                "investigations", news.investigation_id, record_scope, Investigation
            )
            if news.investigation_id
            else None
        )
        if prior:
            try:
                await self.authorize_record(prior, user, budget)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
            else:
                if budget.canonicalization:
                    budget.canonicalization.investigation(created=False)
                return prior
        previous = (
            news.baseline_windows[0]
            if len(news.baseline_windows) == 1
            else Window(
                start=news.window.start - timedelta(weeks=1),
                end=news.window.end - timedelta(weeks=1),
            )
        )
        evidence, hypotheses, decompositions = [], [], []
        residual = Decimal(str(news.change))
        # Each dimension is an alternative decomposition, never an additive cause.
        for dimension in monitor.driver_dimensions:
            before_tables, before_evidence = [], []
            for baseline_window in news.baseline_windows or [previous]:
                before, first = await self.query(
                    news.semantic, window_plan(monitor, baseline_window, dimension), user, budget,
                    purpose="driver",
                )
                before_tables.append(before)
                before_evidence.append(first)
            after, second = await self.query(
                news.semantic, window_plan(monitor, news.window, dimension), user, budget,
                purpose="driver",
            )
            if any(len(table["rows"]) >= 1000 for table in [*before_tables, after]):
                raise HTTPException(
                    status_code=422, detail="Driver cardinality exceeds the investigation bound"
                )

            def segments(result, dimension=dimension):
                di, vi = (
                    result["columns"].index(dimension),
                    result["columns"].index(monitor.value_column),
                )
                values = {}
                for row in result["rows"]:
                    key = json.dumps(row[di], default=str)
                    if key in values or row[vi] is None:
                        raise HTTPException(
                            status_code=422, detail="Ambiguous or missing driver segment"
                        )
                    values[key] = Decimal(str(row[vi]))
                return values

            left, right = {}, segments(after)
            for table in before_tables:
                for key, value in segments(table).items():
                    left[key] = left.get(key, Decimal(0)) + value / len(before_tables)
            ranked = sorted(
                (
                    (key, right.get(key, Decimal(0)) - left.get(key, Decimal(0)))
                    for key in left.keys() | right.keys()
                ),
                key=lambda item: (-abs(item[1]), item[0]),
            )
            evidence.extend([*before_evidence, second])
            for key, delta in ranked[:10]:
                hypotheses.append(
                    Hypothesis(
                        id=fingerprint([dimension, key]),
                        label=f"{dimension} = {key}",
                        contribution=float(delta),
                        dimension=dimension,
                        causal_status="arithmetic",
                        confidence=Confidence(
                            dimension="causal",
                            method="dimension-difference-v1",
                            label="insufficient",
                        ),
                        evidence_ids=[entry.id for entry in [*before_evidence, second]],
                        next_test="Validate this hypothesis with an explicit experimental design.",
                    )
                )
            explained = sum((delta for _, delta in ranked[:10]), Decimal(0))
            dimension_residual = Decimal(str(news.change)) - explained
            decompositions.append(
                {
                    "dimension": dimension,
                    "change": str(news.change),
                    "components": [
                        {"segment": key, "contribution": str(delta)} for key, delta in ranked[:10]
                    ],
                    "residual": str(dimension_residual),
                    "reconciled": explained + dimension_residual == Decimal(str(news.change)),
                    "baseline": "fixed-window-difference" if len(news.baseline_windows) == 1
                    else "matched-weekday-median-windows",
                }
            )
            if len(monitor.driver_dimensions) == 1:
                residual = dimension_residual
        if not hypotheses:
            hypotheses.append(Hypothesis(
                id=fingerprint([news.id, "total-change"]), label="Total metric change",
                contribution=news.change, causal_status="arithmetic",
                confidence=Confidence(dimension="causal", method="window-difference-v1",
                                      label="insufficient"),
                evidence_ids=[entry.id for entry in news.evidence],
                next_test="Select a governed driver dimension to decompose the change.",
            ))
        timeline = [{"at": news.window.end.isoformat(), "kind": "detection", "reference": news.id}]
        for related_id in monitor.related_monitor_ids:
            related = await self.get("monitors", related_id, user, budget=budget)
            observed = await self.observe(related, news.window, user, budget)
            baseline = await self.observe(related, previous, user, budget)
            if (observed.sample_count is None or baseline.sample_count is None
                    or min(observed.sample_count, baseline.sample_count) < related.minimum_samples):
                continue
            evidence.extend([*baseline.evidence, *observed.evidence])
            hypotheses.append(
                Hypothesis(
                    id=fingerprint([related.id, news.id]),
                    label=related.name,
                    causal_status="association",
                    confidence=Confidence(
                        dimension="causal", method="aligned-window-comparison-v1"
                    ),
                    evidence_ids=[item.id for item in [*baseline.evidence, *observed.evidence]],
                    next_test=(
                        "Check business scope, assignment, and an unaffected comparison group."
                    ),
                )
            )
            timeline.append(
                {
                    "at": news.window.end.isoformat(),
                    "kind": "related_observation",
                    "reference": observed.id,
                    "label": related.name,
                    "before": baseline.value,
                    "after": observed.value,
                    "causal_status": "association",
                }
            )
        for source in monitor.timeline_sources:
            event_window = Window(
                start=news.window.start - timedelta(hours=source.preceding_hours),
                end=news.window.end,
            )
            raw = dict(source.plan)
            raw["limit"] = 31
            raw["filters"] = [
                *(raw.get("filters") or []),
                {
                    "field": source.time_dimension,
                    "operator": ">=",
                    "value": event_window.start.astimezone(ZoneInfo(monitor.timezone)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                },
                {
                    "field": source.time_dimension,
                    "operator": "<",
                    "value": event_window.end.astimezone(ZoneInfo(monitor.timezone)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                },
            ]
            events, citation = await self.query(
                news.semantic, SemanticPlan.from_dict(raw), user, budget
            )
            if len(events["rows"]) >= 31:
                raise HTTPException(
                    status_code=422,
                    detail="Narrow the timeline scope; event results may be truncated",
                )
            required = {source.time_column, source.identity_column, *source.label_columns}
            if not required <= set(events["columns"]):
                raise HTTPException(
                    status_code=422, detail="Timeline projection is missing reviewed columns"
                )
            evidence.append(citation)
            for values in events["rows"]:
                event = dict(zip(events["columns"], values, strict=True))
                if event[source.time_column] is None or event[source.identity_column] is None:
                    raise HTTPException(
                        status_code=422, detail="Timeline events require identity and time"
                    )
                try:
                    timestamp = datetime.fromisoformat(str(event[source.time_column]))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=ZoneInfo(monitor.timezone))
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422, detail="Timeline timestamp is invalid"
                    ) from exc
                timeline.append(
                    {
                        "at": timestamp.astimezone(UTC).isoformat(),
                        "kind": source.kind,
                        "reference": str(event[source.identity_column]),
                        "label": " · ".join(str(event[name]) for name in source.label_columns),
                        "evidence_id": citation.id,
                        "causal_status": "association",
                    }
                )
        timeline.sort(key=lambda row: row["at"])
        investigation = Investigation(
            id=fingerprint(
                [
                    news.id,
                    monitor.revision,
                    [item.digest for item in [*news.evidence, *evidence]],
                    "investigation-v2",
                ]
            ),
            scope=record_scope,
            news_id=news.id,
            news_revision=news.revision,
            semantic=news.semantic,
            hypotheses=hypotheses,
            evidence=[*news.evidence, *evidence],
            residual=float(residual),
            decompositions=decompositions,
            method="dimension-difference-median-windows-v2",
            status="complete" if hypotheses else "insufficient",
            timeline=timeline,
        )
        existing = await self.repository.get(
            "investigations", investigation.id, record_scope, Investigation
        )
        saved = existing or await self._save_automatic(
            "investigations", investigation, user, _mission, collector=budget.canonicalization,
        )
        if budget.canonicalization:
            budget.canonicalization.investigation(created=existing is None)
        updated = news.model_copy(update={"investigation_id": saved.id, "status": "investigating"})
        await self._save_automatic("news", updated, user, _mission,
                                   expected_revision=news.revision,
                                   collector=budget.canonicalization)
        await self._audit("INVESTIGATE", saved, user)
        return saved

    async def incomplete_investigation(
        self, news: NewsItem, user: dict, *, budget: CycleBudget | None = None, _mission=None,
    ) -> Investigation:
        identity = fingerprint([news.id, "insufficient-comparison"])
        scope = (news.scope if budget and budget.mission_record_bindings.get(("news", news.id))
                 == news.scope else Scope.from_user(user))
        investigation = await self.repository.get("investigations", identity, scope, Investigation)
        if investigation is None:
            investigation = await self._save_automatic(
                "investigations",
                Investigation(
                    id=identity,
                    scope=scope,
                    news_id=news.id,
                    news_revision=news.revision,
                    semantic=news.semantic,
                    evidence=news.evidence,
                    residual=news.change,
                    method="explicit-window-comparison-v1",
                    status="insufficient",
                ),
                user, _mission,
                collector=budget.canonicalization if budget else None,
            )
            if budget and budget.canonicalization:
                budget.canonicalization.investigation(created=True)
        else:
            await self.authorize_record(investigation, user, budget or CycleBudget())
            if budget and budget.canonicalization:
                budget.canonicalization.investigation(created=False)
        if news.investigation_id != investigation.id:
            await self._save_automatic(
                "news",
                news.model_copy(
                    update={
                        "investigation_id": investigation.id,
                        "status": "investigating",
                    }
                ),
                user, _mission,
                expected_revision=news.revision,
                collector=budget.canonicalization if budget else None,
            )
        await self._audit("INCOMPLETE_INVESTIGATION", investigation, user)
        return investigation

    @asynccontextmanager
    async def _automatic_fence(self, mission, user):
        if mission is None:
            yield
            return
        from app.modules.agents.harness_repository import harness_repository
        from app.modules.agents.mission import mission_service

        async with harness_repository.admission_lock(
            mission.thread_id, mission.scope.principal,
        ) as owned:
            current = await mission_service.get(mission.mission_id, user, project=False)
            if (current.current_binding != mission.current_binding
                    or current.scope != Scope.from_user(user)
                    or current.cancel_requested or current.status == "cancelled"):
                raise HTTPException(status_code=409, detail="Mission execution binding changed")
            await owned()
            yield

    async def _save_automatic(
        self, kind, record, user, mission, *, expected_revision=0,
        collector: CanonicalizationCollector | None = None,
    ):
        async with self._automatic_fence(mission, user):
            started = monotonic()
            try:
                return await self.repository.save(kind, record, expected_revision=expected_revision)
            finally:
                if collector:
                    ended = monotonic()
                    collector.record_duration("persistence", started, ended=ended)
                    if kind == "investigations":
                        collector.record_duration("investigation_persistence", started, ended=ended)

    async def _automatic_comparison_budget(
        self, comparison: ChatComparison, mission, operation_id: str, user: dict,
        *, collector: CanonicalizationCollector | None = None,
    ) -> tuple[CycleBudget, Monitor | None]:
        """Grant only dependencies of a durable internal Mission/seed operation."""
        async with self._automatic_fence(mission, user):
            pass
        bindings = [mission.scope, *mission.historical_bindings, *mission.run_bindings.values(),
                    *mission.object_bindings.values()]
        if (comparison.scope not in bindings
                or comparison.automatic_mission_id != mission.mission_id
                or comparison.automatic_operation_id != operation_id
                or comparison.id != fingerprint([
                    comparison.scope.model_dump(exclude={"session_id"}), operation_id,
                    "chat-comparison-v1",
                ]) or comparison.configuration is None):
            raise HTTPException(status_code=409, detail="Automatic comparison proof unavailable")
        body = ChatInvestigationRequest(
            operation_id=operation_id, configuration=comparison.configuration,
            current_window=comparison.current_window, baseline_window=comparison.baseline_window,
            calendar_timezone=comparison.calendar_timezone,
        )
        if comparison.request_digest != fingerprint(body.model_dump(
            mode="json", exclude={"calendar_timezone"} if body.calendar_timezone is None else set(),
        )) or comparison.semantic != body.configuration.semantic:
            raise HTTPException(status_code=409, detail="Automatic comparison proof changed")
        budget = CycleBudget(canonicalization=collector or current_canonicalization())
        budget.mission_bindings.append(comparison.scope)

        def pin(kind, record):
            budget.mission_records[(kind, record.id)] = record.revision
            budget.mission_record_bindings[(kind, record.id)] = record.scope
            if record.scope not in budget.mission_bindings:
                budget.mission_bindings.append(record.scope)

        monitor = await self.repository.get(
            "monitors", comparison.monitor_id, comparison.scope, Monitor,
            revision=comparison.monitor_revision,
        )
        if monitor is None:
            if comparison.monitor_revision is not None or comparison.monitor_id != fingerprint([
                comparison.id, "comparison-monitor",
            ]):
                raise HTTPException(status_code=409, detail="Pinned comparison monitor unavailable")
        else:
            fields = set(MonitorConfiguration.model_fields) - {"enabled"}
            if (monitor.scope.model_dump(exclude={"session_id"})
                    != comparison.scope.model_dump(exclude={"session_id"})
                    or monitor.model_dump(mode="json", include=fields)
                    != body.configuration.model_dump(mode="json", include=fields)):
                raise HTTPException(status_code=409, detail="Pinned comparison monitor changed")
            pin("monitors", monitor)
            await self.authorize_record(monitor, user, budget)
        news_id = fingerprint([comparison.id, "comparison-news"])
        if comparison.news_id not in {None, news_id}:
            raise HTTPException(status_code=409, detail="Comparison News linkage changed")
        news = await self.repository.get("news", news_id, comparison.scope, NewsItem)
        if news is not None:
            if (monitor is None or news.scope != comparison.scope
                    or news.semantic != comparison.semantic or news.monitor_id != monitor.id
                    or news.monitor_revision != monitor.revision
                    or news.window != comparison.current_window
                    or news.baseline_windows != [comparison.baseline_window]):
                raise HTTPException(status_code=409, detail="Comparison News lineage changed")
            pin("news", news)
            await self.authorize_record(news, user, budget)
        elif comparison.news_id is not None or comparison.investigation_id is not None:
            raise HTTPException(status_code=409, detail="Comparison News unavailable")
        if comparison.investigation_id:
            if comparison.investigation_revision is None or news is None:
                raise HTTPException(
                    status_code=409, detail="Comparison Investigation pin unavailable",
                )
            investigation = await self.repository.get(
                "investigations", comparison.investigation_id, comparison.scope, Investigation,
                revision=comparison.investigation_revision,
            )
            if (investigation is None or investigation.scope != comparison.scope
                    or investigation.news_id != news.id
                    or investigation.semantic != comparison.semantic):
                raise HTTPException(
                    status_code=409, detail="Comparison Investigation lineage changed",
                )
            original_news = await self.repository.get(
                "news", news.id, news.scope, NewsItem, revision=investigation.news_revision,
            )
            if (original_news is None or original_news.revision != investigation.news_revision
                    or original_news.scope != news.scope
                    or original_news.semantic != news.semantic
                    or original_news.monitor_id != monitor.id
                    or original_news.monitor_revision != monitor.revision):
                raise HTTPException(status_code=409, detail="Comparison News revision unavailable")
            await self.authorize_record(original_news, user, budget)
            budget.comparison_news = original_news
            pin("investigations", investigation)
        return budget, monitor

    async def _investigation_news(
        self, investigation: Investigation, user: dict, budget: CycleBudget, *,
        news: NewsItem | None = None,
    ) -> NewsItem:
        for candidate in (news, budget.comparison_news):
            if (candidate is not None and candidate.id == investigation.news_id
                    and candidate.revision == investigation.news_revision
                    and candidate.semantic == investigation.semantic):
                return candidate
        if news and budget.mission_record_bindings.get(("news", news.id)) == news.scope:
            original = await self.repository.get(
                "news", investigation.news_id, news.scope, NewsItem,
                revision=investigation.news_revision,
            )
            if (original is None or original.id != news.id or original.scope != news.scope
                    or original.revision != investigation.news_revision
                    or original.semantic != investigation.semantic
                    or original.monitor_id != news.monitor_id
                    or original.monitor_revision != news.monitor_revision):
                raise HTTPException(status_code=409, detail="Comparison News revision unavailable")
            await self.authorize_record(original, user, budget)
            return original
        return cast(NewsItem, await self.get(
            "news", investigation.news_id, user, budget=budget,
            revision=investigation.news_revision,
        ))

    async def initiate_investigation(
        self, body: ChatInvestigationRequest, user: dict, *,
        comparison_monitor: Monitor | None = None, _mission=None,
        _comparison: ChatComparison | None = None,
        canonicalization: CanonicalizationCollector | None = None,
    ) -> dict:
        collector = canonicalization or CanonicalizationCollector()
        with collector.operation():
            result = await self._initiate_investigation(
                body, user, comparison_monitor=comparison_monitor, _mission=_mission,
                _comparison=_comparison, collector=collector,
            )
            collector.result(result)
        return {**result, "metrics": collector.snapshot().model_dump(mode="json")}

    async def _initiate_investigation(
        self, body: ChatInvestigationRequest, user: dict, *,
        comparison_monitor: Monitor | None = None, _mission=None,
        _comparison: ChatComparison | None = None,
        collector: CanonicalizationCollector,
    ) -> dict:
        from app.core.config import settings
        from app.modules.agents.router import _require_agent

        if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
            raise HTTPException(status_code=503, detail="Studio business workflow is disabled")
        await _require_agent(body.configuration.agent_id, user)
        await self.authorize_semantic(body.configuration.semantic, user, active=_comparison is None)
        if not body.current_window or not body.baseline_window:
            return {
                "status": "clarification",
                "reason": "comparison_windows_required",
                "required_inputs": [
                    name
                    for name, value in (
                        ("current_window", body.current_window),
                        ("baseline_window", body.baseline_window),
                    )
                    if value is None
                ],
                "comparison": None,
                "investigation": None,
                "news": None,
            }
        if body.current_window.end > utc_now():
            raise HTTPException(
                status_code=422, detail="Use complete historical comparison windows"
            )
        scope = _comparison.scope if _comparison else Scope.from_user(user)
        identity = fingerprint(
            [scope.model_dump(exclude={"session_id"}), body.operation_id, "chat-comparison-v1"]
        )
        digest = fingerprint(body.model_dump(
            mode="json", exclude={"calendar_timezone"} if body.calendar_timezone is None else set()
        ))
        monitor_id = comparison_monitor.id if comparison_monitor else fingerprint(
            [identity, "comparison-monitor"]
        )
        budget = CycleBudget(canonicalization=collector)
        if _comparison:
            budget, comparison_monitor = await self._automatic_comparison_budget(
                _comparison, _mission, body.operation_id, user, collector=collector,
            )
        if comparison_monitor:
            await self.authorize_record(comparison_monitor, user, budget)
            fields = set(MonitorConfiguration.model_fields) - {"enabled"}
            if (
                comparison_monitor.model_dump(mode="json", include=fields)
                != body.configuration.model_dump(mode="json", include=fields)
            ):
                raise HTTPException(status_code=409, detail="Comparison monitor changed")
        # Persist the operation digest before collecting data. Canonical writes
        # keep their own revision checks; slow queries must not hold this lease.
        async with metadata_lock("chat-comparison:" + identity):
            comparison = await self.repository.get("comparisons", identity, scope, ChatComparison)
            if comparison and comparison.request_digest != digest:
                raise HTTPException(status_code=409, detail="Comparison operation inputs changed")
            if comparison is None:
                comparison = await self._save_automatic(
                    "comparisons",
                    ChatComparison(
                        id=identity,
                        scope=scope,
                        request_digest=digest,
                        semantic=body.configuration.semantic,
                        monitor_id=monitor_id,
                        monitor_revision=(
                            comparison_monitor.revision if comparison_monitor else None
                        ),
                        configuration=body.configuration,
                        current_window=body.current_window,
                        baseline_window=body.baseline_window,
                        calendar_timezone=body.calendar_timezone,
                        automatic_mission_id=_mission.mission_id if _mission else None,
                        automatic_operation_id=body.operation_id if _mission else None,
                    ),
                    user, _mission, collector=collector,
                )
        if comparison.status != "pending":
            investigation = None
            news = None
            if comparison.investigation_id:
                pinned = _mission and any(
                    ref.kind == "investigation" and ref.id == comparison.investigation_id
                    and ref.revision == comparison.investigation_revision
                    for ref in _mission.pinned_objects
                )
                investigation = await self.get_for_mission(
                    "investigations", comparison.investigation_id, user,
                    mission_id=_mission.mission_id, budget=budget,
                    revision=comparison.investigation_revision,
                ) if pinned else await self.get(
                    "investigations", comparison.investigation_id, user, budget=budget,
                    revision=comparison.investigation_revision,
                )
                collector.investigation(created=False)
                news = await self._investigation_news(investigation, user, budget)
            async with self._automatic_fence(_mission, user):
                pass
            return {
                "status": comparison.status,
                "reason": comparison.reason,
                "comparison": comparison,
                "investigation": investigation,
                "news": news,
            }
        prior_monitor = await self.repository.get(
            "monitors", monitor_id, scope, Monitor, revision=comparison.monitor_revision
        )
        monitor = comparison_monitor or prior_monitor
        if monitor is None:
            monitor = Monitor(id=monitor_id, scope=scope, **body.configuration.model_dump())
            await self.validate_monitor(monitor, user)
            monitor = await self._save_automatic(
                "monitors", monitor, user, _mission, collector=collector,
            )
            await self._audit("REGISTER", monitor, user)
        if comparison.monitor_revision is None:
            comparison = await self._save_automatic(
                "comparisons",
                comparison.model_copy(update={"monitor_revision": monitor.revision}),
                user, _mission,
                expected_revision=comparison.revision,
                collector=collector,
            )
        if _mission:
            budget, _ = await self._automatic_comparison_budget(
                comparison, _mission, body.operation_id, user, collector=collector,
            )
        async with self._automatic_fence(_mission, user):
            pass
        before = await self.observe(monitor, body.baseline_window, user, budget, _mission=_mission)
        after = await self.observe(monitor, body.current_window, user, budget, _mission=_mission)
        ids = [before.id, after.id]
        if before.value is None or after.value is None:
            saved = await self._save_automatic(
                "comparisons",
                comparison.model_copy(
                    update={
                        "status": "insufficient",
                        "reason": "missing_observations",
                        "observation_ids": ids,
                    }
                ),
                user, _mission,
                expected_revision=comparison.revision,
                collector=collector,
            )
            await self._audit("INCOMPLETE_COMPARISON", saved, user)
            return {
                "status": "insufficient",
                "reason": saved.reason,
                "comparison": saved,
                "investigation": None,
                "news": None,
            }
        unknown_count = before.sample_count is None or after.sample_count is None
        insufficient = (not unknown_count and min(
            before.sample_count, after.sample_count
        ) < monitor.minimum_samples) or any(
            row.completeness is not None and row.completeness < 1 for row in (before, after)
        )
        evidence = [*before.evidence, *after.evidence]
        news_id = fingerprint([identity, "comparison-news"])
        news = await self.repository.get("news", news_id, scope, NewsItem)
        if news is None:
            news = await self._save_automatic(
                "news",
                NewsItem(
                    id=news_id,
                    scope=scope,
                    monitor_id=monitor.id,
                    monitor_revision=monitor.revision,
                    title=f"{monitor.name}: requested comparison"[:256],
                    summary="Authorized window comparison. The cause has not been established.",
                    semantic=monitor.semantic,
                    window=body.current_window,
                    before=before.value,
                    after=after.value,
                    change=after.value - before.value,
                    relative_change=(after.value - before.value) / abs(before.value)
                    if before.value
                    else None,
                    severity="info",
                    dedup_key=news_id,
                    confidence=Confidence(
                        dimension="detection",
                        method="explicit-window-comparison-v1",
                        label="insufficient" if insufficient or unknown_count else "medium",
                        evidence_ids=[item.id for item in evidence],
                    ),
                    evidence=evidence,
                    baseline_windows=[body.baseline_window],
                ),
                user, _mission,
                collector=collector,
            )
            await self._audit("COMPARE", news, user)
        if _mission:
            budget.mission_records[("news", news.id)] = news.revision
            budget.mission_record_bindings[("news", news.id)] = news.scope
        if insufficient:
            investigation = await self.incomplete_investigation(
                news, user, budget=budget, _mission=_mission,
            )
        else:
            investigation = await self.investigate(
                news.id, user, arithmetic_only=unknown_count, budget=budget, _mission=_mission,
            )
        news = await self._investigation_news(investigation, user, budget, news=news)
        saved = await self._save_automatic(
            "comparisons",
            comparison.model_copy(
                update={
                    "status": investigation.status,
                    "reason": "insufficient_observations" if insufficient else None,
                    "observation_ids": ids,
                    "news_id": news.id,
                    "investigation_id": investigation.id,
                    "investigation_revision": investigation.revision,
                }
            ),
            user, _mission,
            expected_revision=comparison.revision,
            collector=collector,
        )
        return {
            "status": saved.status,
            "reason": saved.reason,
            "comparison": saved,
            "investigation": investigation,
            "news": news,
        }

    async def automatic_investigation(
        self, seed, user: dict, *, mission_id: str, agent_id: str,
        canonicalization: CanonicalizationCollector | None = None,
    ) -> dict:
        collector = canonicalization or CanonicalizationCollector()
        with collector.operation():
            result = await self._automatic_investigation(
                seed, user, mission_id=mission_id, agent_id=agent_id, collector=collector,
            )
            collector.result(result)
        return {**result, "metrics": collector.snapshot().model_dump(mode="json")}

    async def _automatic_investigation(
        self, seed, user: dict, *, mission_id: str, agent_id: str,
        collector: CanonicalizationCollector,
    ) -> dict:
        from dataclasses import replace

        from app.modules.agents.mission import mission_service

        plan = replace(seed.plan, metrics=(seed.target_metric,), dimensions=(), time=None,
                       order_by=(), limit=None)
        scope, budget = Scope.from_user(user), CycleBudget(canonicalization=collector)
        fixed = seed.execution_time
        operation_id = fingerprint([
            mission_id, seed.semantic.model_dump(), seed.target_metric,
            fixed.current.as_dict(), fixed.baseline.as_dict(), seed.plan_fingerprint,
        ])
        mission = await mission_service.get(mission_id, user, project=False)
        if mission.agent_id not in {None, agent_id} or mission.cancel_requested:
            raise HTTPException(status_code=409, detail="Mission cannot accept this comparison")
        scopes = {}
        for binding in [mission.scope, *mission.historical_bindings, *mission.run_bindings.values(),
                        *mission.object_bindings.values()]:
            scopes.setdefault(fingerprint(binding.model_dump(exclude={"session_id"})), binding)
        if len(scopes) > 100:
            raise HTTPException(status_code=409, detail="Mission comparison recovery bound reached")
        matches = {}
        for binding in scopes.values():
            identity = fingerprint([
                binding.model_dump(exclude={"session_id"}), operation_id, "chat-comparison-v1",
            ])
            found = await self.repository.get("comparisons", identity, binding, ChatComparison)
            if found is not None:
                matches[found.id] = found
        if len(matches) > 1:
            raise HTTPException(
                status_code=409, detail="Duplicate comparison requires reconciliation",
            )
        existing = next(iter(matches.values()), None)
        if existing:
            configuration = existing.configuration
            if (configuration is None or configuration.semantic != seed.semantic
                    or configuration.agent_id != agent_id
                    or configuration.value_column != seed.target_metric
                    or existing.current_window != Window(**fixed.current.as_dict())
                    or existing.baseline_window != Window(**fixed.baseline.as_dict())
                    or existing.calendar_timezone != fixed.timezone):
                raise HTTPException(status_code=409, detail="Automatic comparison seed changed")
            _, pinned_monitor = await self._automatic_comparison_budget(
                existing, mission, operation_id, user, collector=collector,
            )
            return await self.initiate_investigation(ChatInvestigationRequest(
                operation_id=operation_id, configuration=configuration,
                current_window=Window(**fixed.current.as_dict()),
                baseline_window=Window(**fixed.baseline.as_dict()),
                calendar_timezone=fixed.timezone,
            ), user, comparison_monitor=pinned_monitor, _mission=mission, _comparison=existing,
                canonicalization=collector)
        selected = None
        rows = await self.repository.page("monitors", scope, Monitor, limit=101)
        if len(rows) > 100:
            return {"status": "clarification", "reason": "monitor_matching_bound",
                    "required_inputs": ["comparison_monitor"],
                    "comparison": None, "investigation": None, "news": None}
        for monitor in rows:
            candidate = SemanticPlan.from_dict(monitor.plan)
            if (monitor.semantic != seed.semantic or monitor.agent_id != agent_id
                    or monitor.value_column != seed.target_metric
                    or monitor.time_dimension != seed.plan.time.dimension
                    or monitor.timezone != seed.execution_time.timezone
                    or tuple(monitor.driver_dimensions) != seed.driver_dimensions
                    or candidate.filters != plan.filters
                    or candidate.named_filters != plan.named_filters
                    or candidate.dimensions or candidate.time or candidate.having
                    or candidate.transforms or candidate.top_n_per_group
                    or set(candidate.metrics) != {
                        seed.target_metric, monitor.count_column, monitor.completeness_column,
                    } - {None}):
                continue
            try:
                await self.authorize_record(monitor, user, budget)
                await self.validate_monitor(monitor, user)
            except HTTPException as exc:
                if exc.status_code in {403, 404, 409, 422}:
                    continue
                raise
            selected = monitor
            break
        configuration = (
            MonitorConfiguration.model_validate(selected.model_dump(
                include=set(MonitorConfiguration.model_fields)
            )).model_copy(update={"enabled": False}) if selected else MonitorConfiguration(
                name="One-time " + seed.target_metric, agent_id=agent_id, semantic=seed.semantic,
                plan=json.loads(json.dumps(plan.as_dict())),
                value_column=seed.target_metric, count_column=None,
                time_dimension=seed.plan.time.dimension,
                driver_dimensions=list(seed.driver_dimensions),
                timezone=seed.execution_time.timezone, enabled=False,
            )
        )
        request = ChatInvestigationRequest(
            operation_id=operation_id, configuration=configuration,
            current_window=Window(**fixed.current.as_dict()),
            baseline_window=Window(**fixed.baseline.as_dict()),
            calendar_timezone=fixed.timezone,
        )
        return await self.initiate_investigation(
            request, user, comparison_monitor=selected, _mission=mission,
            canonicalization=collector,
        )

    async def lineage(self, decision_id: str, user: dict, *, mission_id: str | None = None) -> dict:
        budget = CycleBudget()
        decision = (
            await self.get_for_mission("decisions", decision_id, user,
                                       mission_id=mission_id, budget=budget)
            if mission_id else await self.get("decisions", decision_id, user, budget=budget)
        )

        async def related_record(kind, record_id, revision=None):
            if mission_id:
                if kind == "investigations":
                    return await self.get_for_mission(kind, record_id, user,
                        mission_id=mission_id, revision=revision, budget=budget)
                budget.mission_records[(kind, record_id)] = revision
                budget.mission_record_bindings[(kind, record_id)] = investigation.scope
                return await self.get(kind, record_id, user, budget=budget, revision=revision)
            record = await self.repository.get(
                kind, record_id, decision.scope, MODELS[kind], revision=revision
            )
            if record is None:
                raise HTTPException(status_code=409, detail="Critical lineage is incomplete")
            await self.authorize_record(
                record.model_copy(update={"scope": Scope.from_user(user)}), user, budget
            )
            return record

        investigation = await related_record(
            "investigations", decision.investigation_id, decision.investigation_revision
        )
        news = await related_record("news", investigation.news_id, investigation.news_revision)
        events = await self.repository.related("events", decision.id, decision.scope, DecisionEvent)
        outcomes = await self.repository.related("outcomes", decision.id, decision.scope, Outcome)
        actions = await self.repository.related("actions", decision.id, decision.scope, Action)
        if mission_id:
            for binding in budget.mission_bindings:
                for kind, model, records in (
                    ("actions", Action, actions), ("outcomes", Outcome, outcomes),
                ):
                    records.extend(await self.repository.related(kind, decision.id, binding, model))
            actions = list({(row.id, row.revision): row for row in actions}.values())
            outcomes = list({(row.id, row.revision): row for row in outcomes}.values())
        for action in actions:
            await self.authorize_record(
                action if mission_id else action.model_copy(
                    update={"scope": Scope.from_user(user)}
                ),
                user, budget
            )
        for outcome in outcomes:
            await self.authorize_record(
                outcome if mission_id else outcome.model_copy(
                    update={"scope": Scope.from_user(user)}
                ),
                user, budget
            )
        committed_events = []
        for event in events:
            historical = await self.repository.get(
                "decisions", decision.id, decision.scope, Decision, revision=event.decision_revision
            )
            from app.modules.intelligence.decisions import decision_digest

            if historical is not None and decision_digest(historical) == event.context_digest:
                committed_events.append(event)
        events = sorted(committed_events, key=lambda event: event.decision_revision)
        if {event.decision_revision for event in events} != set(range(1, decision.revision + 1)):
            raise HTTPException(status_code=409, detail="Critical decision events are incomplete")
        return {
            "decision": decision,
            "investigation": investigation,
            "news": news,
            "events": events,
            "actions": actions,
            "outcomes": sorted(
                outcomes, key=lambda row: (row.updated_at, row.revision, row.id), reverse=True
            ),
            "evidence": list(
                {
                    item.id: item
                    for item in [*decision.evidence, *investigation.evidence, *news.evidence]
                }.values()
            ),
        }

    async def evaluate_outcome(
        self, decision_id: str, user: dict, *, mission_id: str | None = None,
    ) -> Outcome:
        from contextlib import AsyncExitStack, asynccontextmanager

        from app.modules.intelligence.decisions import decision_digest

        budget = CycleBudget()
        mission = None
        execution_scope = Scope.from_user(user)
        if mission_id:
            from app.modules.agents.mission import mission_service

            mission = await mission_service.get(mission_id, user, project=False)
            execution_binding = mission.current_binding.model_copy(deep=True)
            decision = await self.get_for_mission("decisions", decision_id, user,
                                                 mission_id=mission_id, budget=budget)
        else:
            decision = await self.get("decisions", decision_id, user, budget=budget)

        @asynccontextmanager
        async def outcome_fence():
            async with AsyncExitStack() as stack:
                admitted = None
                if mission is not None:
                    from app.modules.agents.harness_repository import harness_repository
                    from app.modules.agents.mission_schema import object_binding_key

                    admitted = await stack.enter_async_context(
                        harness_repository.admission_lock(
                            mission.thread_id, execution_scope.principal
                        )
                    )
                    current_mission = await mission_service.get(mission_id, user, project=False)
                    if (
                        current_mission.scope != execution_scope
                        or current_mission.thread_id != mission.thread_id
                        or current_mission.current_binding != execution_binding
                        or current_mission.cancel_requested
                        or current_mission.status == "cancelled"
                    ):
                        raise HTTPException(
                            status_code=409, detail="Mission execution binding changed"
                        )
                    pin = next((ref for ref in current_mission.object_refs
                                if ref.kind == "decision" and ref.id == decision.id), None)
                    if (
                        pin is None or pin.revision != decision.revision
                        or current_mission.object_bindings.get(
                            object_binding_key(pin), current_mission.scope
                        ) != decision.scope
                    ):
                        raise HTTPException(
                            status_code=409, detail="Mission Decision revision changed"
                        )
                await stack.enter_async_context(metadata_lock(f"decisions:{decision.id}"))
                current_decision = await self.repository.get(
                    "decisions", decision.id, decision.scope, Decision
                )
                if (
                    current_decision is None or current_decision.scope != decision.scope
                    or current_decision.revision != decision.revision
                    or decision_digest(current_decision) != decision_digest(decision)
                ):
                    raise HTTPException(
                        status_code=409, detail="Decision changed; refresh the Outcome"
                    )
                if admitted:
                    await admitted()
                yield admitted

        async with outcome_fence():
            pass
        if decision.scope.principal != user["username"]:
            raise HTTPException(
                status_code=403,
                detail="The owner evaluates outcomes in the original business scope",
            )
        if decision.status not in {
            "selected",
            "approved",
            "observing",
            "evaluated",
            "cancelled",
            "superseded",
        }:
            raise HTTPException(status_code=409, detail="Only selected decisions can have outcomes")
        option = next(
            (row for row in decision.options if row.id == decision.selected_option_id), None
        )
        if not option:
            raise HTTPException(status_code=409, detail="Decision has no selected option")
        investigation = (
            await self.get_for_mission("investigations", decision.investigation_id, user,
                mission_id=mission_id, revision=decision.investigation_revision, budget=budget)
            if mission_id else await self.get(
                "investigations", decision.investigation_id, user, budget=budget)
        )
        if mission_id:
            budget.mission_records[("news", investigation.news_id)] = investigation.news_revision
            budget.mission_record_bindings[("news", investigation.news_id)] = investigation.scope
        news = await self.get(
            "news", investigation.news_id, user, budget=budget, revision=investigation.news_revision
        )
        if mission_id:
            budget.mission_records[("monitors", news.monitor_id)] = news.monitor_revision
            budget.mission_record_bindings[("monitors", news.monitor_id)] = news.scope
        monitor = await self.get(
            "monitors", news.monitor_id, user, budget=budget, revision=news.monitor_revision
        )
        if monitor.semantic != decision.semantic:
            raise HTTPException(
                status_code=409, detail="Outcome requires the pinned monitor definition"
            )
        complete_window = decision.outcome_window.end <= utc_now()
        actual, evidence, completeness = None, [], 0.0
        closed = decision.status in {"cancelled", "superseded"}
        if complete_window and not closed:
            async with outcome_fence():
                pass
            observed = await self.observe(monitor, decision.outcome_window, user, budget)
            if (observed.sample_count is not None
                    and observed.sample_count >= monitor.minimum_samples):
                actual, evidence = observed.value, observed.evidence
                completeness = observed.completeness or 0.0
        candidates = await self.repository.page(
            "decisions", Scope.from_user(user), Decision, limit=101
        )
        if mission_id:
            for binding in budget.mission_bindings:
                candidates.extend(await self.repository.page(
                    "decisions", binding, Decision, limit=101
                ))
            candidates = list({(row.id, row.revision): row for row in candidates}.values())
        overlapping, overlap_unknown = [], len(candidates) > 100
        for other in candidates[:100]:
            if (
                other.id == decision.id
                or other.semantic != decision.semantic
                or other.target_metric != decision.target_metric
                or other.status not in {"selected", "approved", "observing", "evaluated"}
                or other.outcome_window.start >= decision.outcome_window.end
                or other.outcome_window.end <= decision.outcome_window.start
            ):
                continue
            try:
                await self.authorize_record(other, user, budget)
            except HTTPException as exc:
                if exc.status_code not in {403, 404, 409, 429}:
                    raise
                overlap_unknown = True
            else:
                overlapping.append(other.id)
        dimensions = outcome_dimensions(
            predicted=option.prediction,
            actual=actual,
            baseline=decision.baseline,
            lower=option.lower_bound,
            upper=option.upper_bound,
            completeness=completeness,
            overlapping=bool(overlapping) or overlap_unknown,
        )
        from app.modules.access_control.business_policy import read_business_policy

        current_policy = await read_business_policy()
        dimensions["policy_compliance"] = (
            (
                decision.policy.policy_revision == current_policy.revision
                and decision.status in {"approved", "selected", "observing", "evaluated"}
            )
            if decision.policy
            else None
        )
        dimensions["time_to_insight_seconds"] = (
            investigation.created_at - news.created_at
        ).total_seconds()
        actions = await self.repository.related("actions", decision.id, decision.scope, Action)
        if mission_id:
            for binding in budget.mission_bindings:
                actions.extend(await self.repository.related(
                    "actions", decision.id, binding, Action
                ))
            actions = list({(row.id, row.revision): row for row in actions}.values())
        verified_actions = []
        for action in actions:
            if (
                action.status == "verified"
                and action.verification
                and action.verification.complete
                and action.semantic == decision.semantic
            ):
                await self.authorize_record(action, user, budget)
                verified_actions.append(action)
        dimensions["verified_action_count"] = len(verified_actions)
        dimensions["action_business_effect_verified"] = None
        outcome = Outcome(
            id=fingerprint([decision.id, decision.revision, "outcome-v1"] + (
                [mission_id, Scope.from_user(user).model_dump(exclude={"session_id"})]
                if mission_id else []
            )),
            scope=Scope.from_user(user),
            decision_id=decision.id,
            decision_revision=decision.revision,
            semantic=decision.semantic,
            target_metric=decision.target_metric,
            currency=decision.currency,
            window=decision.outcome_window,
            predicted=option.prediction,
            actual=actual,
            completeness=completeness,
            attribution=dimensions["attribution"],
            status="superseded"
            if closed
            else "complete"
            if completeness == 1 and actual is not None
            else ("missing_data" if complete_window else "pending"),
            overlapping_decisions=overlapping,
            evidence=evidence,
            action_ids=[action.id for action in verified_actions],
            learning_refs=[],
            dimensions=dimensions,
        )
        async with outcome_fence() as admitted:
            prior = await self.repository.get("outcomes", outcome.id, execution_scope, Outcome)
            if prior:
                outcome.scope = prior.scope
                ignored = {"learning_refs", "revision", "created_at", "updated_at"}
                if prior.model_dump(exclude=ignored) == outcome.model_dump(exclude=ignored):
                    outcome.learning_refs = prior.learning_refs
            if admitted:
                await admitted()
            saved = await self.repository.save(
                "outcomes", outcome, expected_revision=prior.revision if prior else 0
            )
        await self._audit("EVALUATE_OUTCOME", saved, user)
        if saved.status == "complete" and decision.learning_enabled:
            from app.modules.agents.memory import memory_repository

            if saved.learning_refs:
                try:
                    await self._require_outcome_learning(saved, decision.agent_id)
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                else:
                    return saved

            async def finalize_learning(memory_id: str, knowledge_revision: int) -> dict:
                nonlocal saved
                async with outcome_fence() as admitted:
                    if admitted:
                        await admitted()
                    saved = await self.repository.save(
                        "outcomes",
                        saved.model_copy(
                            update={
                                "learning_refs": [
                                    OutcomeLearningRef(id=memory_id, revision=knowledge_revision)
                                ]
                            }
                        ),
                        expected_revision=saved.revision,
                    )
                return {
                    "id": saved.id,
                    "revision": saved.revision,
                    "semantic": saved.semantic.model_dump(),
                }

            fact = (
                f"Prediction for {decision.target_metric} was {saved.predicted:g}; "
                f"observed {saved.actual:g}. Attribution: {saved.attribution}."
            )
            async with outcome_fence():
                pass
            await memory_repository.upsert(
                user_name=user["username"],
                agent_id=decision.agent_id,
                role_name=user["active_role"],
                fact_key="outcome_" + saved.id,
                fact=fact,
                source_quote=fact,
                source_thread_id=decision.thread_id or saved.id,
                source_message_id=f"{saved.id}:{saved.revision}",
                existing_id=None,
                outcome={
                    "id": saved.id,
                    "revision": saved.revision,
                    "semantic": saved.semantic.model_dump(),
                    "learning_ref": saved.learning_refs[0].model_dump()
                    if len(saved.learning_refs) == 1
                    else None,
                },
                source_scope=Scope.from_user(user),
                observed_at=saved.updated_at,
                finalize_outcome=finalize_learning,
            )
            await self._require_outcome_learning(saved, decision.agent_id)
        return saved

    @staticmethod
    async def _require_outcome_learning(record: Outcome, agent_id: str) -> None:
        from app.modules.agents.memory import memory_repository

        if record.status != "complete" or record.completeness != 1 or record.actual is None:
            raise HTTPException(status_code=409, detail="Incomplete outcome cannot supply learning")
        if not record.learning_refs:
            raise HTTPException(status_code=404, detail="Outcome learning reference unavailable")
        for reference in record.learning_refs:
            revisions = await memory_repository.revisions(
                reference.id,
                user_name=record.scope.principal,
                agent_id=agent_id,
                role_name=record.scope.active_role,
            )
            pinned = next((row for row in revisions if row.revision == reference.revision), None)
            if (
                pinned is None
                or pinned.semantic != record.semantic
                or pinned.definition.get("outcome_id") != record.id
                or pinned.definition.get("outcome_revision") != record.revision
            ):
                raise HTTPException(
                    status_code=404, detail="Outcome learning reference unavailable"
                )


intelligence_service = IntelligenceService()
