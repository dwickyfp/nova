"""Lifecycle retries and authorization against an independent observation source."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.intelligence import engine
from app.modules.intelligence.contracts import (
    ContextEdge,
    ContextNode,
    Monitor,
    Scope,
    SemanticRef,
    Window,
)
from app.modules.intelligence.engine import CycleBudget, IntelligenceService

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "roles": ["ANALYST"],
    "security_context_version": 1,
    "session_id": "session-alice",
}
REF = SemanticRef(view_id="sales", version=1, fingerprint="frozen-definition")
END = datetime(2026, 9, 20, tzinfo=UTC)
WINDOW = Window(start=END - timedelta(days=1), end=END)


class MemoryRepository:
    def __init__(self):
        self.rows = {}
        self.fail_once = None

    async def get(self, kind, record_id, scope, model, **kwargs):
        row = self.rows.get((kind, record_id))
        if row and (
            row.scope.principal,
            row.scope.active_role,
            row.scope.security_context_version,
        ) == (scope.principal, scope.active_role, scope.security_context_version):
            return deepcopy(row)
        return None

    async def save(self, kind, record, *, expected_revision=0):
        if self.fail_once == kind:
            self.fail_once = None
            raise RuntimeError("worker interrupted between journal and projection")
        old = self.rows.get((kind, record.id))
        ignored = {"created_at", "updated_at", "revision"}
        if old and old.model_dump(exclude=ignored) == record.model_dump(exclude=ignored):
            return deepcopy(old)
        if old and old.revision != expected_revision:
            raise HTTPException(status_code=409, detail="stale revision")
        record = record.model_copy(update={"revision": expected_revision + 1})
        self.rows[(kind, record.id)] = deepcopy(record)
        return record

    async def recent_incident(self, candidate, cooldown_hours):
        return next(
            (
                deepcopy(row)
                for (kind, _), row in self.rows.items()
                if kind == "news"
                and row.monitor_id == candidate.monitor_id
                and row.semantic == candidate.semantic
                and (row.change > 0) == (candidate.change > 0)
                and abs(row.window.end - candidate.window.end) < timedelta(hours=cooldown_hours)
            ),
            None,
        )

    async def page(self, kind, scope, model, *, limit=50, after="", endpoint=None):
        return [
            deepcopy(row)
            for (table, ident), row in sorted(self.rows.items())
            if table == kind
            and ident > after
            and row.scope.principal == scope.principal
            and row.scope.active_role == scope.active_role
            and row.scope.security_context_version == scope.security_context_version
            and (endpoint is None or endpoint in {row.source, row.target})
        ][:limit]


class ObservationSource:
    def __init__(self):
        self.active_version = 1
        self.revoked = False
        self.masked = False
        self.sample_count = 100
        self.calls = []

    async def _readable_version(self, view_id, version, user):
        if self.revoked:
            raise HTTPException(status_code=404, detail="source unavailable")
        return (
            {"active_version": self.active_version},
            {"fingerprint": REF.fingerprint, "status": "ACTIVE"},
        )

    async def execute_plan(self, view_id, version, plan, user):
        self.calls.append((plan, deepcopy(user)))
        await self._readable_version(view_id, version, user)
        current = plan.filters[-1].value.startswith("2026-09-20")
        if plan.dimensions:
            rows = [["Jakarta", 10 if current else 50, 50], ["Bandung", 50, 50]]
            columns = ["city", "revenue", "orders"]
        else:
            rows = [[60 if current else 100, self.sample_count]]
            columns = ["revenue", "orders"]
        if self.masked:
            rows[0][-2] = 0
        return {
            "columns": columns,
            "rows": rows,
            "model_fingerprint": REF.fingerprint,
            "query_id": "governed-query",
        }


@pytest.fixture
def lifecycle(monkeypatch):
    @asynccontextmanager
    async def lock(_key):
        yield

    monkeypatch.setattr(engine, "metadata_lock", lock)
    repository, source = MemoryRepository(), ObservationSource()
    service = IntelligenceService(repository, source)
    monkeypatch.setattr(engine, "write_audit_log", AsyncMock())
    monitor = Monitor(
        id="monitor",
        scope=Scope.from_user(USER),
        name="Revenue",
        agent_id="finance",
        semantic=REF,
        plan={"metrics": ["revenue", "orders"]},
        value_column="revenue",
        count_column="orders",
        time_dimension="ordered_at",
        driver_dimensions=["city"],
    )
    repository.rows[("monitors", monitor.id)] = monitor
    return service, repository, source


@pytest.mark.asyncio
async def test_material_change_news_and_dimensional_reconciliation(lifecycle):
    service, repo, source = lifecycle
    result = await service.run_monitor("monitor", WINDOW, USER)
    assert result["detection"]["change"] == -40
    investigation = await service.investigate(result["news_id"], USER)
    assert investigation.hypotheses[0].label == 'city = "Jakarta"'
    assert investigation.hypotheses[0].causal_status == "arithmetic"
    assert sum(row.contribution for row in investigation.hypotheses) + investigation.residual == -40
    assert all(user == USER for _, user in source.calls)


@pytest.mark.asyncio
async def test_repeated_cycle_does_not_duplicate_news_observations_or_investigation(lifecycle):
    service, repo, _ = lifecycle
    first = await service.run_monitor("monitor", WINDOW, USER)
    second = await service.run_monitor("monitor", WINDOW, USER)
    assert first["news_id"] == second["news_id"]
    assert len([key for key in repo.rows if key[0] == "observations"]) == 5
    initial = await service.investigate(first["news_id"], USER)
    repeated = await service.investigate(first["news_id"], USER)
    assert initial.id == repeated.id
    assert len([key for key in repo.rows if key[0] == "investigations"]) == 1


@pytest.mark.asyncio
async def test_investigation_repairs_crash_before_news_projection(lifecycle):
    service, repo, _ = lifecycle
    detected = await service.run_monitor("monitor", WINDOW, USER)
    repo.fail_once = "news"
    with pytest.raises(RuntimeError, match="interrupted"):
        await service.investigate(detected["news_id"], USER)
    recovered = await service.investigate(detected["news_id"], USER)
    assert repo.rows[("news", detected["news_id"])].investigation_id == recovered.id
    assert len([key for key in repo.rows if key[0] == "investigations"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"username": "bob"},
        {"active_role": "VIEWER", "roles": ["VIEWER"]},
        {"security_context_version": 2},
    ],
)
async def test_same_role_other_principal_role_switch_and_revocation_isolate_results(
    lifecycle, change
):
    service, _, _ = lifecycle
    result = await service.run_monitor("monitor", WINDOW, USER)
    with pytest.raises(HTTPException) as error:
        await service.get("news", result["news_id"], {**USER, **change})
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_row_filter_or_mask_change_hides_previously_derived_evidence(lifecycle):
    service, _, source = lifecycle
    result = await service.run_monitor("monitor", WINDOW, USER)
    source.masked = True
    with pytest.raises(HTTPException) as error:
        await service.get("news", result["news_id"], USER)
    assert error.value.status_code == 409
    assert (await service.page("news", USER))["items"] == []


@pytest.mark.asyncio
async def test_changed_semantic_version_stops_old_monitor(lifecycle):
    service, _, source = lifecycle
    source.active_version = 2
    with pytest.raises(HTTPException) as error:
        await service.run_monitor("monitor", WINDOW, USER)
    assert error.value.status_code == 409
    assert not source.calls


@pytest.mark.asyncio
async def test_low_volume_apparent_drop_is_not_news(lifecycle):
    service, repo, source = lifecycle
    source.sample_count = 2
    result = await service.run_monitor("monitor", WINDOW, USER)
    assert result["news_id"] is None
    assert not [key for key in repo.rows if key[0] == "news"]


@pytest.mark.parametrize("missing_window", ["current", "baseline"])
async def test_empty_aggregate_is_missing_observation_and_never_news(
    lifecycle, monkeypatch, missing_window
):
    service, repo, source = lifecycle
    original = source.execute_plan

    async def empty_window(view_id, version, plan, user):
        result = await original(view_id, version, plan, user)
        current = plan.filters[-1].value.startswith("2026-09-20")
        if current == (missing_window == "current"):
            result["rows"] = [[None, 0]]
        return result

    monkeypatch.setattr(source, "execute_plan", empty_window)
    result = await service.run_monitor("monitor", WINDOW, USER)
    assert result["detection"]["reason"] == "missing_observations"
    assert result["news_id"] is None
    missing = [
        row for (kind, _), row in repo.rows.items() if kind == "observations" and row.value is None
    ]
    assert missing and all(row.sample_count == 0 and row.evidence for row in missing)


async def test_null_metric_with_nonzero_count_still_fails_closed(lifecycle, monkeypatch):
    service, _, source = lifecycle
    original = source.execute_plan

    async def masked(*args):
        result = await original(*args)
        result["rows"][0][0] = None
        return result

    monkeypatch.setattr(source, "execute_plan", masked)
    with pytest.raises(HTTPException) as exc:
        await service.run_monitor("monitor", WINDOW, USER)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_graph_edge_cannot_expose_hidden_endpoint(lifecycle):
    service, repo, _ = lifecycle
    scope = Scope.from_user(USER)
    repo.rows[("nodes", "visible")] = ContextNode(
        id="visible", scope=scope, kind="domain", name="Sales", reference_id="sales"
    )
    repo.rows[("edges", "edge")] = ContextEdge(
        id="edge", scope=scope, source="visible", target="hidden", relationship="depends_on"
    )
    assert (await service.page("edges", USER))["items"] == []


def test_cycle_query_ceiling_is_enforced():
    budget = CycleBudget()
    for _ in range(20):
        budget.consume("queries")
    with pytest.raises(HTTPException) as error:
        budget.consume("queries")
    assert error.value.status_code == 429


@pytest.mark.parametrize(
    "changed",
    [
        {"username": "bob"},
        {"active_role": "FINANCE", "roles": ["FINANCE"]},
        {"security_context_version": 2},
        {"session_id": "replacement-session"},
    ],
)
async def test_semantic_authorization_cache_is_request_and_identity_bound(lifecycle, changed):
    service, _, source = lifecycle
    source._readable_version = AsyncMock(wraps=source._readable_version)
    budget = CycleBudget()
    await service.authorize_semantic(REF, USER, budget=budget)
    await service.authorize_semantic(REF, USER, budget=budget)
    source._readable_version.assert_awaited_once()
    source.revoked = True
    with pytest.raises(HTTPException) as error:
        await service.authorize_semantic(REF, {**USER, **changed}, budget=budget)
    assert error.value.status_code == 404
    assert source._readable_version.await_count == 2
    with pytest.raises(HTTPException):
        await service.authorize_semantic(REF, USER, budget=CycleBudget())


async def test_cached_semantic_read_cannot_authorize_active_or_unbound_view(lifecycle):
    service, _, source = lifecycle
    budget = CycleBudget()
    await service.authorize_semantic(REF, USER, budget=budget)
    source.active_version = 2
    with pytest.raises(HTTPException) as stale:
        await service.authorize_semantic(REF, USER, active=True, budget=budget)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as unbound:
        await service.authorize_semantic(
            REF, {**USER, "intelligence_allowed_views": []}, budget=budget
        )
    assert unbound.value.status_code == 404


async def test_slow_semantic_authorization_is_cancelled_at_the_cycle_deadline(
    lifecycle, monkeypatch
):
    import asyncio

    service, _, source = lifecycle
    budget = CycleBudget()
    monkeypatch.setattr(engine, "monotonic", lambda: budget.started + 119.99)
    cancelled = False

    async def slow(*_args):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    source._readable_version = slow
    with pytest.raises(HTTPException) as error:
        await service.authorize_semantic(REF, USER, budget=budget)
    assert error.value.status_code == 429 and cancelled
    assert budget.semantic_authorizations == {}


async def test_cached_query_cannot_bypass_cycle_deadline_or_agent_bindings(lifecycle):
    from app.modules.intelligence.engine import window_plan

    service, repo, source = lifecycle
    budget = CycleBudget()
    plan = window_plan(repo.rows[("monitors", "monitor")], WINDOW)
    await service.query(REF, plan, USER, budget)
    assert len(source.calls) == 1
    with pytest.raises(HTTPException) as unbound:
        await service.query(REF, plan, {**USER, "intelligence_allowed_views": []}, budget)
    assert unbound.value.status_code == 404
    budget.started -= 121
    with pytest.raises(HTTPException) as expired:
        await service.query(REF, plan, USER, budget)
    assert expired.value.status_code == 429
    assert len(source.calls) == 1


async def test_graph_cycles_terminate_and_hidden_neighbors_do_not_consume_visible_limit(
    lifecycle, monkeypatch
):
    from app.modules.intelligence import context_graph

    service, repo, _ = lifecycle
    monkeypatch.setattr(context_graph, "intelligence_service", service)
    scope = Scope.from_user(USER)
    for name in ("root", "visible"):
        repo.rows[("nodes", name)] = ContextNode(
            id=name, scope=scope, kind="domain", name=name, reference_id=name
        )
    for ident, source, target in (
        ("a-hidden", "root", "hidden"),
        ("b-visible", "root", "visible"),
        ("c-cycle", "visible", "root"),
    ):
        repo.rows[("edges", ident)] = ContextEdge(
            id=ident, scope=scope, source=source, target=target, relationship="depends_on"
        )
    original = repo.page

    async def page(kind, scope, model, *, endpoint=None, **kwargs):
        rows = await original(kind, scope, model, **kwargs)
        return [row for row in rows if endpoint in {row.source, row.target}]

    repo.page = page
    result = await context_graph.traverse_context("root", USER, depth=4, limit=2)
    assert {node.id for node in result["nodes"]} == {"root", "visible"}
    assert {edge.id for edge in result["edges"]} == {"b-visible", "c-cycle"}
    assert result["bounded"] is True
    del repo.rows[("edges", "a-hidden")]
    without_hidden = await context_graph.traverse_context("root", USER, depth=4, limit=2)
    assert result == without_hidden


@pytest.mark.asyncio
async def test_timeline_is_scoped_evidence_and_never_promoted_to_a_cause(lifecycle):
    from app.modules.intelligence.contracts import TimelineSource

    service, repo, source = lifecycle
    monitor = repo.rows[("monitors", "monitor")]
    monitor.timeline_sources = [
        TimelineSource(
            kind="deployment",
            time_dimension="deployed_at",
            time_column="deployed_at",
            identity_column="deployment_id",
            label_columns=["service"],
            preceding_hours=24,
            plan={"dimensions": ["deployed_at", "deployment_id", "service"], "limit": 31},
        )
    ]
    original = source.execute_plan

    async def execute(view, version, plan, user):
        if "deployment_id" in plan.dimensions:
            assert user == USER
            assert [item.operator for item in plan.filters[-2:]] == [">=", "<"]
            return {
                "columns": ["deployed_at", "deployment_id", "service"],
                "rows": [["2026-09-19 04:00:00", "unrelated-release", "catalog-service"]],
                "model_fingerprint": REF.fingerprint,
                "query_id": "timeline-query",
            }
        return await original(view, version, plan, user)

    source.execute_plan = execute
    incident = await service.run_monitor("monitor", WINDOW, USER)
    investigation = await service.investigate(incident["news_id"], USER)
    event = next(row for row in investigation.timeline if row["kind"] == "deployment")
    assert event["causal_status"] == "association"
    assert event["at"] == "2026-09-18T21:00:00+00:00"
    assert event["evidence_id"] in {row.id for row in investigation.evidence}
    assert all(row.causal_status != "supported_effect" for row in investigation.hypotheses)


@pytest.mark.asyncio
async def test_timeline_rejects_truncated_event_sets(lifecycle):
    from app.modules.intelligence.contracts import TimelineSource

    service, repo, source = lifecycle
    repo.rows[("monitors", "monitor")].timeline_sources = [
        TimelineSource(
            kind="deployment",
            time_dimension="deployed_at",
            time_column="deployed_at",
            identity_column="deployment_id",
            plan={"dimensions": ["deployed_at", "deployment_id"], "limit": 31},
        )
    ]
    original = source.execute_plan

    async def execute(view, version, plan, user):
        if "deployment_id" in plan.dimensions:
            return {
                "columns": ["deployed_at", "deployment_id"],
                "rows": [["2026-09-19", str(i)] for i in range(31)],
                "model_fingerprint": REF.fingerprint,
            }
        return await original(view, version, plan, user)

    source.execute_plan = execute
    incident = await service.run_monitor("monitor", WINDOW, USER)
    with pytest.raises(HTTPException) as rejected:
        await service.investigate(incident["news_id"], USER)
    assert rejected.value.status_code == 422
    assert not any(kind == "investigations" for kind, _ in repo.rows)
