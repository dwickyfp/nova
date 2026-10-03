"""Governed monitoring, investigations, and decision lifecycle operations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import TypeVar
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.common.audit import write_audit_log
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.contracts import (
    Confidence,
    ContextEdge,
    ContextNode,
    Decision,
    DecisionEvent,
    EvidenceRef,
    Hypothesis,
    Investigation,
    MetricObservation,
    Monitor,
    NewsItem,
    Outcome,
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
}


@dataclass
class CycleBudget:
    queries: int = 0
    investigations: int = 0
    items: int = 0
    started: float = 0
    checked_evidence: set[str] = field(default_factory=set)
    query_results: dict[str, tuple[dict, EvidenceRef]] = field(default_factory=dict)
    semantic_authorizations: dict[str, dict] = field(default_factory=dict)

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
        self, ref: SemanticRef, plan: SemanticPlan, user: dict, budget: CycleBudget
    ) -> tuple[dict, EvidenceRef]:
        budget.consume("queries", 0)
        allowed_views = user.get("intelligence_allowed_views")
        if allowed_views is not None and ref.view_id not in allowed_views:
            raise HTTPException(status_code=404, detail="Semantic View is not bound to this agent")
        cache_key = fingerprint(
            [Scope.from_user(user).model_dump(), ref.model_dump(), plan.as_dict()]
        )
        if cache_key in budget.query_results:
            return budget.query_results[cache_key]
        budget.consume("queries")
        async with asyncio.timeout(max(0.01, 120 - (monotonic() - budget.started))):
            result = await self.semantic.execute_plan(ref.view_id, ref.version, plan, user)
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
        if not same_scope and (
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
        if isinstance(record, ContextNode) and record.kind in {
            "decision",
            "outcome",
            "news",
            "investigation",
        }:
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
        if isinstance(record, Outcome):
            # Outcome errors and impact also derive from the prediction and
            # baseline, whose authorization can change independently of actuals.
            await self.get(
                "decisions",
                record.decision_id,
                user,
                budget=budget,
                revision=record.decision_revision,
            )
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
        record = await self.repository.get(
            kind, record_id, Scope.from_user(user), MODELS[kind], revision=revision
        )
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
        await self.authorize_record(record, user, budget or CycleBudget())
        return record

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

    async def register_monitor(
        self, monitor: Monitor, user: dict, *, expected_revision=0
    ) -> Monitor:
        row = await self.authorize_semantic(monitor.semantic, user, active=True)
        ir = SemanticModelIR.from_ossie(row["definition"])
        plan = SemanticPlan.from_dict(monitor.plan)
        if monitor.completeness_column and monitor.completeness_column not in plan.metrics:
            raise HTTPException(status_code=422, detail="Include the data completeness metric")
        if (
            monitor.value_column not in plan.metrics
            or monitor.count_column not in plan.metrics
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
        monitor.scope = Scope.from_user(user)
        saved = await self.repository.save("monitors", monitor, expected_revision=expected_revision)
        await self._audit("REGISTER", saved, user)
        return saved

    async def observe(
        self, monitor: Monitor, window: Window, user: dict, budget: CycleBudget
    ) -> MetricObservation:
        result, evidence = await self.query(
            monitor.semantic, window_plan(monitor, window), user, budget
        )
        if len(result["rows"]) != 1:
            raise HTTPException(status_code=422, detail="Monitor needs exactly one aggregate row")
        row = dict(zip(result["columns"], result["rows"][0], strict=True))
        value, count = row.get(monitor.value_column), row.get(monitor.count_column)
        if count is None or Decimal(str(count)) != int(count) or count < 0:
            raise HTTPException(status_code=422, detail="The observation is incomplete")
        if value is None and count > 0:
            raise HTTPException(status_code=422, detail="The observation is incomplete")
        evidence.window_start, evidence.window_end = window.start, window.end
        observation = MetricObservation(
            id=fingerprint([monitor.id, monitor.revision, window.model_dump(), evidence.digest]),
            scope=Scope.from_user(user),
            monitor_id=monitor.id,
            monitor_revision=monitor.revision,
            semantic=monitor.semantic,
            window=window,
            value=float(value) if value is not None else None,
            sample_count=int(count),
            completeness=row.get(monitor.completeness_column)
            if monitor.completeness_column
            else None,
            evidence=[evidence],
        )
        prior = await self.repository.get(
            "observations", observation.id, Scope.from_user(user), MetricObservation
        )
        return prior or await self.repository.save("observations", observation)

    async def run_monitor(
        self, monitor_id: str, window: Window, user: dict, budget: CycleBudget | None = None
    ) -> dict:
        budget = budget or CycleBudget()
        monitor = await self.get("monitors", monitor_id, user, budget=budget)
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

    async def investigate(self, news_id: str, user: dict) -> Investigation:
        budget = CycleBudget()
        budget.consume("investigations")
        news = await self.get("news", news_id, user, budget=budget)
        monitor = await self.get(
            "monitors", news.monitor_id, user, budget=budget, revision=news.monitor_revision
        )
        await self.authorize_semantic(news.semantic, user, active=True, budget=budget)
        if monitor.semantic != news.semantic:
            raise HTTPException(status_code=409, detail="Monitor changed; revalidate the incident")
        prior = (
            await self.repository.get(
                "investigations", news.investigation_id, Scope.from_user(user), Investigation
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
                return prior
        previous = Window(
            start=news.window.start - timedelta(weeks=1), end=news.window.end - timedelta(weeks=1)
        )
        evidence, hypotheses, decompositions = [], [], []
        residual = Decimal(str(news.change))
        # Each dimension is an alternative decomposition, never an additive cause.
        for dimension in monitor.driver_dimensions:
            before_tables, before_evidence = [], []
            for baseline_window in news.baseline_windows or [previous]:
                before, first = await self.query(
                    news.semantic, window_plan(monitor, baseline_window, dimension), user, budget
                )
                before_tables.append(before)
                before_evidence.append(first)
            after, second = await self.query(
                news.semantic, window_plan(monitor, news.window, dimension), user, budget
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
                    "baseline": "matched-weekday-median-windows",
                }
            )
            if len(monitor.driver_dimensions) == 1:
                residual = dimension_residual
        timeline = [{"at": news.window.end.isoformat(), "kind": "detection", "reference": news.id}]
        for related_id in monitor.related_monitor_ids:
            related = await self.get("monitors", related_id, user, budget=budget)
            observed = await self.observe(related, news.window, user, budget)
            baseline = await self.observe(related, previous, user, budget)
            if min(observed.sample_count, baseline.sample_count) < related.minimum_samples:
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
            scope=Scope.from_user(user),
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
            "investigations", investigation.id, Scope.from_user(user), Investigation
        )
        saved = existing or await self.repository.save("investigations", investigation)
        updated = news.model_copy(update={"investigation_id": saved.id, "status": "investigating"})
        await self.repository.save("news", updated, expected_revision=news.revision)
        await self._audit("INVESTIGATE", saved, user)
        return saved

    async def lineage(self, decision_id: str, user: dict) -> dict:
        budget = CycleBudget()
        decision = await self.get("decisions", decision_id, user, budget=budget)

        async def related_record(kind, record_id, revision=None):
            record = await self.repository.get(
                kind, record_id, decision.scope, MODELS[kind], revision=revision
            )
            if record is None:
                raise HTTPException(status_code=409, detail="Critical lineage is incomplete")
            await self.authorize_record(
                record.model_copy(update={"scope": Scope.from_user(user)}), user, budget
            )
            return record

        investigation = await related_record("investigations", decision.investigation_id)
        news = await related_record("news", investigation.news_id, investigation.news_revision)
        events = await self.repository.related("events", decision.id, decision.scope, DecisionEvent)
        outcomes = await self.repository.related("outcomes", decision.id, decision.scope, Outcome)
        for outcome in outcomes:
            await self.authorize_record(
                outcome.model_copy(update={"scope": Scope.from_user(user)}), user, budget
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

    async def evaluate_outcome(self, decision_id: str, user: dict) -> Outcome:
        budget = CycleBudget()
        decision = await self.get("decisions", decision_id, user, budget=budget)
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
        investigation = await self.get(
            "investigations", decision.investigation_id, user, budget=budget
        )
        news = await self.get(
            "news", investigation.news_id, user, budget=budget, revision=investigation.news_revision
        )
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
            observed = await self.observe(monitor, decision.outcome_window, user, budget)
            if observed.sample_count >= monitor.minimum_samples:
                actual, evidence = observed.value, observed.evidence
                completeness = observed.completeness or 0.0
        candidates = await self.repository.page(
            "decisions", Scope.from_user(user), Decision, limit=101
        )
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
        outcome = Outcome(
            id=fingerprint([decision.id, decision.revision, "outcome-v1"]),
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
            dimensions=dimensions,
        )
        prior = await self.repository.get("outcomes", outcome.id, Scope.from_user(user), Outcome)
        if prior:
            outcome.scope = prior.scope
        saved = await self.repository.save(
            "outcomes", outcome, expected_revision=prior.revision if prior else 0
        )
        await self._audit("EVALUATE_OUTCOME", saved, user)
        if saved.status == "complete" and decision.learning_enabled:
            from app.modules.agents.memory import memory_repository

            fact = (
                f"Prediction for {decision.target_metric} was {saved.predicted:g}; "
                f"observed {saved.actual:g}. Attribution: {saved.attribution}."
            )
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
                },
                source_scope=Scope.from_user(user),
                observed_at=saved.updated_at,
            )
        return saved


intelligence_service = IntelligenceService()
