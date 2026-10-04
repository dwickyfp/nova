from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.intelligence import engine
from app.modules.intelligence.engine import (
    MAX_COUNT,
    MAX_DURATION_MS,
    CanonicalizationCollector,
    CanonicalizationMetrics,
    CycleBudget,
    current_canonicalization,
    window_plan,
)
from tests.unit.test_automatic_investigation import automatic_mission as _automatic_mission
from tests.unit.test_automatic_investigation import resume_binding, seed
from tests.unit.test_chat_investigation import comparison as _comparison
from tests.unit.test_intelligence_engine import REF, USER, WINDOW
from tests.unit.test_intelligence_engine import lifecycle as _lifecycle

automatic_mission, comparison, lifecycle = _automatic_mission, _comparison, _lifecycle


@pytest.fixture
def timed(comparison, monkeypatch):
    service, repo, source, body = comparison
    clock = SimpleNamespace(now=1.0, writes=[])
    monkeypatch.setattr(engine, "monotonic", lambda: clock.now)
    execute, save = source.execute_plan, repo.save

    async def measured_execute(*args):
        clock.now += 0.005
        return await execute(*args)

    async def measured_save(kind, record, **kwargs):
        clock.now += 0.007
        clock.writes.append(kind)
        return await save(kind, record, **kwargs)

    monkeypatch.setattr(source, "execute_plan", measured_execute)
    monkeypatch.setattr(repo, "save", measured_save)
    return service, repo, source, body, clock


def assert_queries(metrics, source):
    assert metrics["automatic_investigation_query_count"] == len(source.calls)
    assert metrics["automatic_investigation_query_count"] == sum(
        metrics[f"{purpose}_query_count"] for purpose in ("comparison", "driver", "other")
    )


async def test_created_operation_counts_dispatches_not_authorization_cache_hits(timed):
    service, _, source, body, clock = timed
    result = await service.initiate_investigation(body, USER)
    metrics = result["metrics"]
    assert result["status"] == "complete"
    assert_queries(metrics, source)
    assert metrics["automatic_investigation_query_count"] == 4
    assert metrics["comparison_query_count"] == 2
    assert metrics["driver_query_count"] == 2
    assert metrics["other_query_count"] == 0
    assert metrics["query_cache_reuse_count"] == 2
    assert metrics["query_duration_ms"] == pytest.approx(20)
    assert metrics["persistence_duration_ms"] == pytest.approx(len(clock.writes) * 7)
    assert metrics["investigation_persistence_duration_ms"] == pytest.approx(7)
    assert metrics["business_canonicalization_duration_ms"] == pytest.approx(
        metrics["query_duration_ms"] + metrics["persistence_duration_ms"]
    )
    assert metrics["business_canonicalization_status"] == "created"
    assert metrics["canonical_investigation_created"] is True
    assert metrics["canonical_investigation_reused"] is False
    assert metrics["scope"] == "current_operation_attempt"
    assert metrics["unavailable"] == []


async def test_parent_operation_timer_includes_linkage_once(timed):
    service, _, _, body, clock = timed
    collector = CanonicalizationCollector()
    with collector.operation():
        result = await service.initiate_investigation(body, USER, canonicalization=collector)
        engine_duration = result["metrics"]["business_canonicalization_duration_ms"]
        clock.now += 0.020
    metrics = collector.snapshot()
    assert metrics.business_canonicalization_duration_ms == pytest.approx(engine_duration + 20)
    assert metrics.business_canonicalization_status == "created"
    assert metrics.automatic_investigation_query_count == 4
    before = metrics.model_dump()
    clock.now += 1
    collector.record_query("other")
    collector.complete("failed")
    assert collector.snapshot().model_dump() == before
    with pytest.raises(ValueError, match="one operation attempt"), collector.operation():
        pass


async def test_reuse_returns_exact_news_revision_without_reanalysis(
    comparison, automatic_mission,
):
    service, repo, source, body = comparison
    original = await service.initiate_investigation(body, USER)
    news, investigation = original["news"], original["investigation"]
    assert news.revision == investigation.news_revision
    assert (news.before, news.after, news.change) == (100, 60, -40)
    assert repo.rows[("news", news.id)].revision > news.revision
    source.calls.clear()
    reused = await service.initiate_investigation(body, USER)
    assert reused["news"].model_dump() == news.model_dump()
    assert reused["investigation"].id == investigation.id
    metrics = reused["metrics"]
    assert_queries(metrics, source)
    assert metrics["automatic_investigation_query_count"] == 4
    assert metrics["comparison_query_count"] == metrics["driver_query_count"] == 0
    assert metrics["other_query_count"] == 4
    assert metrics["business_canonicalization_status"] == "reused"
    assert metrics["canonical_investigation_created"] is False
    assert metrics["canonical_investigation_reused"] is True
    assert metrics["persistence_duration_ms"] == 0


async def test_all_recovery_budgets_contribute_to_current_attempt(
    comparison, automatic_mission,
):
    service, _, source, _ = comparison
    original = await service.automatic_investigation(
        seed(), USER, mission_id="mission", agent_id="finance",
    )
    source.calls.clear()
    user, _ = resume_binding(automatic_mission)
    collector = CanonicalizationCollector()
    recovered = await service.automatic_investigation(
        seed(), user, mission_id="mission", agent_id="finance", canonicalization=collector,
    )
    metrics = recovered["metrics"]
    assert_queries(metrics, source)
    assert metrics["other_query_count"] == 6
    assert metrics["comparison_query_count"] == metrics["driver_query_count"] == 0
    assert metrics["business_canonicalization_status"] == "reused"
    assert metrics["canonical_investigation_reused"] is True
    assert recovered["news"].model_dump() == original["news"].model_dump()
    assert recovered["news"].revision == recovered["investigation"].news_revision
    assert all(caller == user for _, caller in source.calls)
    assert collector.snapshot().model_dump(mode="json") == metrics


@pytest.mark.parametrize("checkpoint", ["investigations", "news_link", "comparison_link"])
async def test_failed_and_recovered_attempts_keep_costs_and_creation_honest(
    comparison, automatic_mission, monkeypatch, checkpoint,
):
    service, repo, source, _ = comparison
    original_save = repo.save
    failed = False

    async def interrupt(kind, record, **kwargs):
        nonlocal failed
        matches = ((checkpoint == "investigations" and kind == "investigations")
                   or (checkpoint == "news_link" and kind == "news" and record.investigation_id)
                   or (checkpoint == "comparison_link" and kind == "comparisons"
                       and record.status != "pending"))
        if matches and not failed:
            failed = True
            raise RuntimeError("private dispatch fence and secret")
        return await original_save(kind, record, **kwargs)

    monkeypatch.setattr(repo, "save", interrupt)
    collector = CanonicalizationCollector()
    with pytest.raises(RuntimeError):
        await service.automatic_investigation(
            seed(), USER, mission_id="mission", agent_id="finance", canonicalization=collector,
        )
    failure = collector.snapshot().model_dump(mode="json")
    assert_queries(failure, source)
    assert failure["business_canonicalization_status"] == "failed"
    assert failure["canonical_investigation_created"] is (checkpoint != "investigations")
    assert "secret" not in json.dumps(failure)
    assert failure["business_canonicalization_duration_ms"] > 0
    source.calls.clear()
    user, _ = resume_binding(automatic_mission)
    recovered = await service.automatic_investigation(
        seed(), user, mission_id="mission", agent_id="finance",
    )
    metrics = recovered["metrics"]
    assert_queries(metrics, source)
    assert metrics["scope"] == "current_operation_attempt"
    assert metrics["business_canonicalization_status"] == (
        "created" if checkpoint == "investigations" else "reused"
    )
    assert recovered["news"].revision == recovered["investigation"].news_revision
    assert recovered["news"].change == -40
    assert len([kind for kind, _ in repo.rows if kind == "investigations"]) == 1


async def test_failed_query_attempt_and_elapsed_time_survive_exception(timed, monkeypatch):
    service, _, source, body, _ = timed
    execute = source.execute_plan

    async def fail_current(*args):
        result = await execute(*args)
        if len(source.calls) == 2:
            raise RuntimeError("private filter value")
        return result

    monkeypatch.setattr(source, "execute_plan", fail_current)
    collector = CanonicalizationCollector()
    with pytest.raises(RuntimeError):
        await service.initiate_investigation(body, USER, canonicalization=collector)
    metrics = collector.snapshot().model_dump(mode="json")
    assert_queries(metrics, source)
    assert metrics["comparison_query_count"] == 2
    assert metrics["query_duration_ms"] == pytest.approx(10)
    assert metrics["business_canonicalization_status"] == "failed"
    assert metrics["canonical_investigation_created"] is False
    assert "private" not in json.dumps(metrics)


async def test_failed_persistence_duration_is_measured(timed):
    service, repo, _, body, clock = timed
    repo.fail_once = "investigations"
    collector = CanonicalizationCollector()
    with pytest.raises(RuntimeError):
        await service.initiate_investigation(body, USER, canonicalization=collector)
    metrics = collector.snapshot()
    assert metrics.business_canonicalization_status == "failed"
    assert metrics.investigation_persistence_duration_ms == pytest.approx(7)
    assert metrics.persistence_duration_ms == pytest.approx(len(clock.writes) * 7)
    assert metrics.canonical_investigation_created is False


async def test_cancellation_is_failed_with_actual_attempt_cost(timed, monkeypatch):
    service, _, source, body, clock = timed
    entered = asyncio.Event()

    async def waiting(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(source, "execute_plan", waiting)
    collector = CanonicalizationCollector()
    task = asyncio.create_task(service.initiate_investigation(
        body, USER, canonicalization=collector,
    ))
    await entered.wait()
    clock.now += 0.003
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    metrics = collector.snapshot()
    assert metrics.business_canonicalization_status == "failed"
    assert metrics.automatic_investigation_query_count == metrics.comparison_query_count == 1
    assert metrics.query_duration_ms == pytest.approx(3)


@pytest.mark.parametrize("missing", ["values", "samples", "windows"])
async def test_incomplete_has_independent_creation_flags_and_optional_news(
    comparison, monkeypatch, missing,
):
    service, _, source, body = comparison
    if missing == "values":
        execute = source.execute_plan

        async def missing_values(*args):
            result = await execute(*args)
            result["rows"] = [[None, 0]]
            return result

        monkeypatch.setattr(source, "execute_plan", missing_values)
    elif missing == "samples":
        source.sample_count = 2
    else:
        body = body.model_copy(update={"baseline_window": None})
    result = await service.initiate_investigation(body, USER)
    metrics = result["metrics"]
    assert_queries(metrics, source)
    assert metrics["business_canonicalization_status"] == "incomplete"
    assert metrics["canonical_investigation_created"] is (missing == "samples")
    assert metrics["canonical_investigation_reused"] is False
    assert metrics["driver_query_count"] == 0
    if missing == "samples":
        assert result["news"].revision == result["investigation"].news_revision
    else:
        assert result["news"] is None
        assert result["investigation"] is None
    if missing == "windows":
        assert metrics["automatic_investigation_query_count"] == 0
        assert metrics["persistence_duration_ms"] == 0


async def test_reused_incomplete_is_not_reported_as_complete(comparison, automatic_mission):
    service, _, source, body = comparison
    source.sample_count = 2
    original = await service.initiate_investigation(body, USER)
    reused = await service.initiate_investigation(body, USER)
    metrics = reused["metrics"]
    assert metrics["business_canonicalization_status"] == "incomplete"
    assert metrics["canonical_investigation_created"] is False
    assert metrics["canonical_investigation_reused"] is True
    assert reused["news"].model_dump() == original["news"].model_dump()


async def test_failed_reauthorization_does_not_reuse_cached_authority(
    comparison, automatic_mission,
):
    service, _, source, _ = comparison
    await service.automatic_investigation(seed(), USER, mission_id="mission", agent_id="finance")
    source.masked = True
    user, _ = resume_binding(automatic_mission)
    collector = CanonicalizationCollector()
    with pytest.raises(HTTPException) as error:
        await service.automatic_investigation(
            seed(), user, mission_id="mission", agent_id="finance", canonicalization=collector,
        )
    assert error.value.status_code == 409
    metrics = collector.snapshot()
    assert metrics.business_canonicalization_status == "failed"
    assert metrics.other_query_count == 1
    assert metrics.canonical_investigation_reused is False


@pytest.mark.parametrize("tamper", ["missing", "scope", "semantic"])
async def test_recovery_cannot_return_unavailable_or_changed_pinned_news(
    comparison, automatic_mission, tamper,
):
    service, _, source, _ = comparison
    original = await service.automatic_investigation(
        seed(), USER, mission_id="mission", agent_id="finance",
    )
    news = original["news"]
    key = ("news", news.id, news.revision)
    if tamper == "missing":
        automatic_mission.history.pop(key)
    elif tamper == "scope":
        automatic_mission.history[key].scope.principal = "another-owner"
    else:
        automatic_mission.history[key].semantic = REF.model_copy(update={"version": 2})
    source.calls.clear()
    user, _ = resume_binding(automatic_mission)
    collector = CanonicalizationCollector()
    with pytest.raises(HTTPException) as error:
        await service.automatic_investigation(
            seed(), user, mission_id="mission", agent_id="finance", canonicalization=collector,
        )
    assert error.value.status_code == 409
    metrics = collector.snapshot().model_dump(mode="json")
    assert_queries(metrics, source)
    assert metrics["business_canonicalization_status"] == "failed"
    assert metrics["canonical_investigation_reused"] is False


async def test_concurrent_operations_do_not_mix_query_counts(comparison, monkeypatch):
    service, _, source, body = comparison
    execute = source.execute_plan
    first_queries = 0
    together = asyncio.Event()

    async def concurrent(*args):
        nonlocal first_queries
        first_queries += 1
        if first_queries >= 2:
            together.set()
        await together.wait()
        return await execute(*args)

    monkeypatch.setattr(source, "execute_plan", concurrent)
    collectors = [CanonicalizationCollector(), CanonicalizationCollector()]
    results = await asyncio.gather(*(service.initiate_investigation(
        body.model_copy(update={"operation_id": f"comparison-operation-{index}"}), USER,
        canonicalization=collector,
    ) for index, collector in enumerate(collectors)))
    assert len(source.calls) == 8
    assert results[0]["investigation"].id != results[1]["investigation"].id
    for result, collector in zip(results, collectors, strict=True):
        metrics = result["metrics"]
        assert metrics["automatic_investigation_query_count"] == 4
        assert metrics["business_canonicalization_status"] == "created"
        assert collector.snapshot().model_dump(mode="json") == metrics


async def test_budget_rejection_is_not_counted_as_query_execution(comparison):
    service, repo, source, _ = comparison
    collector = CanonicalizationCollector()
    budget = CycleBudget(queries=20, canonicalization=collector)
    with collector.operation(), pytest.raises(HTTPException) as error:
        await service.query(REF, window_plan(repo.rows[("monitors", "monitor")], WINDOW),
                            USER, budget, purpose="comparison")
    assert error.value.status_code == 429
    assert not source.calls
    assert collector.snapshot().automatic_investigation_query_count == 0


def test_skips_and_failure_labels_are_closed_and_content_free(monkeypatch):
    monkeypatch.setattr(engine, "monotonic", lambda: 1.0)
    collector = CanonicalizationCollector()
    with collector.operation():
        collector.complete("skipped")
    metrics = collector.snapshot()
    assert metrics.business_canonicalization_status == "skipped"
    assert metrics.automatic_investigation_query_count == 0
    with pytest.raises(ValueError, match="status"):
        collector.complete("private-secret")
    with pytest.raises(ValueError, match="purpose"):
        collector.record_query("private-filter")
    with pytest.raises(ValueError, match="duration"):
        collector.record_duration("private-attachment", 0)
    assert "private" not in metrics.model_dump_json()


def test_budget_scope_restores_nested_collectors_and_failures():
    assert current_canonicalization() is None
    outer, inner = CanonicalizationCollector(), CanonicalizationCollector()
    with outer.operation():
        assert CycleBudget().canonicalization is outer
        with pytest.raises(RuntimeError), inner.operation():
            assert CycleBudget().canonicalization is inner
            raise RuntimeError("private reason")
        assert CycleBudget().canonicalization is outer
        outer.complete("skipped")
    assert current_canonicalization() is None
    assert CycleBudget().canonicalization is None
    assert inner.snapshot().business_canonicalization_status == "failed"


def test_count_saturation_discloses_incomplete_coverage():
    collector = CanonicalizationCollector()
    collector._metrics.automatic_investigation_query_count = MAX_COUNT
    collector._metrics.comparison_query_count = MAX_COUNT
    collector.record_query("comparison")
    metrics = collector.snapshot()
    assert metrics.automatic_investigation_query_count == MAX_COUNT
    assert metrics.comparison_query_count == MAX_COUNT
    assert metrics.unavailable == ["counter_limit_exceeded"]


def test_duration_saturation_and_snapshot_are_bounded(monkeypatch):
    collector = CanonicalizationCollector()
    now = 1.0
    monkeypatch.setattr(engine, "monotonic", lambda: now)
    with collector.operation():
        now += MAX_DURATION_MS / 1000 + 1
        collector.record_duration("query", 1.0)
        collector.complete("skipped")
    metrics = collector.snapshot()
    assert metrics.query_duration_ms == metrics.business_canonicalization_duration_ms == (
        MAX_DURATION_MS
    )
    assert metrics.unavailable == ["duration_limit_exceeded"]
    metrics.unavailable.clear()
    assert collector.snapshot().unavailable == ["duration_limit_exceeded"]


@pytest.mark.parametrize("values", [
    {"automatic_investigation_query_count": True},
    {"comparison_query_count": -1},
    {"driver_query_count": MAX_COUNT + 1},
    {"query_duration_ms": float("inf")},
    {"business_canonicalization_status": "secret"},
    {"session_id": "private-session"},
    {"unavailable": ["private-filter"]},
])
def test_typed_metrics_reject_unsafe_or_invalid_fields(values):
    with pytest.raises(ValidationError):
        CanonicalizationMetrics(**values)
