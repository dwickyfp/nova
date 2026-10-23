from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.modules.smart_reports import distribution
from app.modules.smart_reports.distribution import SubscriptionRequest, Threshold, threshold_met
from app.modules.smart_reports.repository import digest
from tests.unit.test_smart_report_lifecycle import MemoryRecords
from tests.unit.test_smart_reports import USER, report_spec, view


def request():
    return SubscriptionRequest(
        operation_id="subscribe",
        view_session_id="view",
        expected_state_revision=1,
        title="Daily report",
        schedule_kind="cron",
        schedule_expr="0 9 * * *",
        timezone="Asia/Jakarta",
        consent_execution=True,
    )


def test_typed_threshold_preserves_decimal_precision_and_rejects_partial():
    rule = Threshold(
        widget_id="revenue",
        metric="revenue",
        comparator=">",
        value=Decimal("9007199254740992.01"),
        window="last_7_days",
    )
    result = {
        "status": "success",
        "completeness": "complete",
        "columns": ["revenue"],
        "rows": [["9007199254740992.02"]],
    }
    assert threshold_met(rule, result)
    assert not threshold_met(rule, {**result, "rows": [[None]]})
    with pytest.raises(ValueError):
        threshold_met(rule, {**result, "completeness": "truncated"})
    with pytest.raises(ValueError):
        SubscriptionRequest.model_validate({**request().model_dump(), "consent_execution": False})
    with pytest.raises(ValueError):
        SubscriptionRequest.model_validate({**request().model_dump(), "schedule_expr": "* * * * *"})


@pytest.mark.parametrize("superseded", [False, True])
async def test_delivery_recovers_after_message_before_final_record_without_duplicate_identity(
    monkeypatch,
    superseded,
):
    monkeypatch.setattr(distribution, "require_execution_binding", AsyncMock())
    records = MemoryRecords()
    spec, state = report_spec(), view()
    body = request()
    payload = {
        "kind": "subscription",
        "report_id": state.report_id,
        "identity": distribution.identity(USER),
        "configuration": body.model_dump(mode="json"),
        "state": state.model_dump(mode="json"),
    }
    await records.append(
        record_id="subscription",
        operation_id="create",
        expected_revision=0,
        request={},
        payload=payload,
        kind="subscription",
    )

    async def ids(*args, **kwargs):
        return [
            key for key, values in records.records.items() if values[-1]["payload"].get("phase")
        ]

    records.ids = ids
    reports = SimpleNamespace(
        repo=records,
        access=AsyncMock(return_value={"published_revision": 1}),
        spec=AsyncMock(return_value=(spec, 1)),
        audit=AsyncMock(),
    )

    @asynccontextmanager
    async def lease(*args, **kwargs):
        yield AsyncMock()

    monkeypatch.setattr(distribution, "metadata_lock", lease)
    monkeypatch.setattr(distribution, "report_service", reports)
    snapshot = {"pages": [{"result": {"widgets": []}}]}
    monkeypatch.setattr(distribution, "collect", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        distribution, "store", lambda: SimpleNamespace(put=Mock(return_value="test-snapshot"))
    )
    from app.modules.assistant.repository import assistant_repository

    thread, message = AsyncMock(), AsyncMock()
    monkeypatch.setattr(assistant_repository, "create_thread", thread)
    monkeypatch.setattr(assistant_repository, "append_message", message)
    append = records.append
    failed = False

    async def crash(**kwargs):
        nonlocal failed
        if kwargs["payload"].get("phase") == "complete" and not failed:
            failed = True
            raise TimeoutError("Crash after delivery")
        return await append(**kwargs)

    records.append = crash
    with pytest.raises(TimeoutError):
        await distribution.run_subscription(
            "subscription", USER, "run-one", instant=datetime(2026, 1, 1, tzinfo=UTC)
        )
    if superseded:
        payload["configuration"]["title"] = "Revised report"
        await records.append(
            record_id="subscription",
            operation_id="edit",
            expected_revision=1,
            request={"edit": True},
            payload=payload,
            kind="subscription",
        )
        with pytest.raises(HTTPException) as stale:
            await distribution.run_subscription("subscription", USER, "run-two")
        assert stale.value.status_code == 409
        cancelled = await distribution.run_subscription("subscription", USER, "run-one")
        assert cancelled["status"] == "cancelled"
        fresh = await distribution.run_subscription("subscription", USER, "run-three")
        assert fresh["status"] == "delivered"
        assert fresh["activity_id"] != cancelled["activity_id"]
        return
    recovered = await distribution.run_subscription("subscription", USER, "run-two")
    assert recovered["status"] == "delivered"
    assert len({call.kwargs["thread_id"] for call in thread.await_args_list}) == 1
    assert len({call.kwargs["message_id"] for call in message.await_args_list}) == 1
    assert records.records[recovered["activity_id"]][-1]["payload"]["phase"] == "complete"
    await distribution.run_subscription("subscription", USER, "run-one")
    assert message.await_count == 2
    await distribution.record_cooldown(
        "subscription", state.report_id, "cool-one", "2026-01-01T00:00:00+00:00", USER, AsyncMock()
    )
    await distribution.record_cooldown(
        "subscription", state.report_id, "cool-one", "2026-01-01T00:00:00+00:00", USER, AsyncMock()
    )
    assert len(records.records[distribution.stable_id("subscription", "cooldown")]) == 1


async def test_changed_subscription_or_revoked_access_blocks_delivery(monkeypatch):
    monkeypatch.setattr(distribution, "require_execution_binding", AsyncMock())
    records = MemoryRecords()
    state = view()
    payload = {
        "kind": "subscription",
        "report_id": state.report_id,
        "identity": distribution.identity(USER),
        "configuration": request().model_dump(mode="json"),
        "state": state.model_dump(mode="json"),
    }
    await records.append(
        record_id="subscription",
        operation_id="create",
        expected_revision=0,
        request={},
        payload=payload,
        kind="subscription",
    )
    records.ids = AsyncMock(return_value=[])
    reports = SimpleNamespace(
        repo=records, access=AsyncMock(side_effect=HTTPException(403)), audit=AsyncMock()
    )

    @asynccontextmanager
    async def lease(*args, **kwargs):
        yield AsyncMock()

    monkeypatch.setattr(distribution, "metadata_lock", lease)
    monkeypatch.setattr(distribution, "report_service", reports)
    baseline = digest(records.records)
    with pytest.raises(HTTPException):
        await distribution.run_subscription("subscription", USER, "run")
    assert digest(records.records) == baseline
