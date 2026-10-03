"""Chat comparisons preserve canonical observations without creating periodic work."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core import config
from app.modules.intelligence import engine
from app.modules.intelligence.contracts import MonitorConfiguration, Window
from app.modules.intelligence.engine import ChatInvestigationRequest
from tests.unit.test_intelligence_engine import END, USER, WINDOW
from tests.unit.test_intelligence_engine import lifecycle as _lifecycle

lifecycle = _lifecycle


@pytest.fixture
def comparison(lifecycle, monkeypatch):
    service, repo, source = lifecycle
    monkeypatch.setattr(config, "settings", SimpleNamespace(STUDIO_BUSINESS_WORKFLOW_ENABLED=True))
    from app.modules.agents import router

    monkeypatch.setattr(router, "_require_agent", AsyncMock())
    monkeypatch.setattr(service, "validate_monitor", AsyncMock())
    monkeypatch.setattr(engine, "utc_now", lambda: END)
    configuration = MonitorConfiguration.model_validate(
        repo.rows[("monitors", "monitor")].model_dump(
            exclude={"id", "scope", "revision", "created_at", "updated_at"}
        )
    )
    baseline = Window(start=WINDOW.start - timedelta(days=14), end=WINDOW.end - timedelta(days=14))
    body = ChatInvestigationRequest(
        operation_id="chat-comparison-1",
        configuration=configuration,
        current_window=WINDOW,
        baseline_window=baseline,
    )
    return service, repo, source, body


@pytest.mark.asyncio
async def test_chat_investigation_canonical_lineage_original_windows_and_retry(comparison):
    service, repo, source, body = comparison
    result = await service.initiate_investigation(body, USER)
    assert result["status"] == "complete"
    investigation = result["investigation"]
    news = repo.rows[("news", investigation.news_id)]
    monitor = repo.rows[("monitors", news.monitor_id)]
    assert monitor.enabled is False
    assert news.baseline_windows == [body.baseline_window]
    assert news.window == body.current_window
    assert all(h.causal_status == "arithmetic" for h in investigation.hypotheses)
    assert len([row for (kind, _), row in repo.rows.items() if kind == "comparisons"]) == 1
    repeated = await service.initiate_investigation(body, USER)
    assert repeated["investigation"].id == investigation.id
    # Driver baseline queries use the user's requested fortnight baseline,
    # rather than replacing it with the monitor's usual previous week.
    assert all(not plan.filters[-1].value.startswith("2026-09-13") for plan, _ in source.calls)
    assert {kind for kind, _ in repo.rows} <= {
        "comparisons",
        "monitors",
        "observations",
        "news",
        "investigations",
    }


@pytest.mark.asyncio
async def test_missing_comparison_requires_clarification_without_writes(comparison):
    service, repo, _, body = comparison
    before = len(repo.rows)
    result = await service.initiate_investigation(
        body.model_copy(update={"baseline_window": None}), USER
    )
    assert result["status"] == "clarification" and result["required_inputs"] == ["baseline_window"]
    assert len(repo.rows) == before


@pytest.mark.asyncio
async def test_low_samples_produce_explicit_incomplete_investigation(comparison):
    service, repo, source, body = comparison
    source.sample_count = 2
    result = await service.initiate_investigation(body, USER)
    assert result["status"] == "insufficient"
    assert result["reason"] == "insufficient_observations"
    assert result["investigation"].hypotheses == []
    repeated = await service.investigate(result["comparison"].news_id, USER)
    assert repeated.status == "insufficient" and repeated.hypotheses == []
    assert result["comparison"].news_id in {ident for kind, ident in repo.rows if kind == "news"}


@pytest.mark.asyncio
async def test_missing_values_never_fabricate_news_numbers(comparison, monkeypatch):
    service, repo, source, body = comparison
    original = source.execute_plan

    async def missing(*args):
        result = await original(*args)
        result["rows"] = [[None, 0]]
        return result

    monkeypatch.setattr(source, "execute_plan", missing)
    result = await service.initiate_investigation(body, USER)
    assert result["status"] == "insufficient" and result["reason"] == "missing_observations"
    assert result["investigation"] is None
    assert not [kind for kind, _ in repo.rows if kind == "news"]


@pytest.mark.asyncio
async def test_crash_recovers_the_same_comparison_and_news(comparison):
    service, repo, _, body = comparison
    repo.fail_once = "investigations"
    with pytest.raises(RuntimeError):
        await service.initiate_investigation(body, USER)
    recovered = await service.initiate_investigation(body, USER)
    assert recovered["status"] == "complete"
    assert len([kind for kind, _ in repo.rows if kind == "news"]) == 1
    assert len([kind for kind, _ in repo.rows if kind == "comparisons"]) == 1


@pytest.mark.asyncio
async def test_operation_digest_and_current_access_remain_live(comparison):
    service, _, source, body = comparison
    await service.initiate_investigation(body, USER)
    changed = body.model_copy(
        update={"configuration": body.configuration.model_copy(update={"name": "Other"})}
    )
    with pytest.raises(HTTPException) as error:
        await service.initiate_investigation(changed, USER)
    assert error.value.status_code == 409
    source.revoked = True
    with pytest.raises(HTTPException):
        await service.initiate_investigation(body, USER)


@pytest.mark.asyncio
async def test_comparison_intent_lease_is_released_before_authorized_queries(
    comparison, monkeypatch,
):
    service, repo, source, body = comparison
    locked = False
    entered, resume = asyncio.Event(), asyncio.Event()
    execute = source.execute_plan

    @asynccontextmanager
    async def lock(_key):
        nonlocal locked
        assert not locked
        locked = True
        try:
            yield
        finally:
            locked = False

    async def paused_query(*args):
        assert not locked, "Data collection must outlive the short metadata lease"
        assert [row for (kind, _), row in repo.rows.items() if kind == "comparisons"]
        entered.set()
        await resume.wait()
        return await execute(*args)

    monkeypatch.setattr(engine, "metadata_lock", lock)
    monkeypatch.setattr(source, "execute_plan", paused_query)
    pending = asyncio.create_task(service.initiate_investigation(body, USER))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        changed = body.model_copy(update={
            "configuration": body.configuration.model_copy(update={"name": "Changed inputs"})
        })
        with pytest.raises(HTTPException) as error:
            await service.initiate_investigation(changed, USER)
        assert error.value.status_code == 409
        resume.set()
        result = await pending
        assert result["status"] == "complete"
        assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1
        assert not locked
    finally:
        resume.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_pending_comparison_recovers_crash_before_monitor_registration(comparison):
    service, repo, _, body = comparison
    repo.fail_once = "monitors"
    with pytest.raises(RuntimeError):
        await service.initiate_investigation(body, USER)
    intent = next(row for (kind, _), row in repo.rows.items() if kind == "comparisons")
    assert intent.status == "pending"
    result = await service.initiate_investigation(body, USER)
    assert result["status"] == "complete" and result["comparison"].id == intent.id
    assert len([key for key in repo.rows if key[0] == "comparisons"]) == 1


@pytest.mark.parametrize("change", ["enabled", "overlap", "length"])
def test_invalid_comparison_shape_is_rejected(comparison, change):
    *_, body = comparison
    data = body.model_dump()
    if change == "enabled":
        data["configuration"]["enabled"] = True
    elif change == "overlap":
        data["baseline_window"] = data["current_window"]
    else:
        data["baseline_window"]["start"] -= timedelta(hours=1)
    with pytest.raises(ValidationError):
        ChatInvestigationRequest.model_validate(data)
