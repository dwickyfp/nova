from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.engine_state import (
    ObjectState,
    baseline_state,
    compensation_sql,
    inspect_object,
)
from app.modules.query_autopilot.models import ActionKind, digest
from tests.unit.query_autopilot import test_service
from tests.unit.query_autopilot.test_policy_experiments import fixtures

setup = test_service.setup


def baseline(identifier="12", *, enabled="Y", query_ms="1"):
    return QueryResult(
        columns=["Id", "global", "enable", "bindSQL", "planSQL", "source", "updateTime", "queryMs"],
        rows=[[identifier, "Y", enabled, "bound", "planned", "USER", "2026-10-01", query_ms]],
    )


def test_baseline_identity_ignores_runtime_cost_but_detects_definition_changes():
    original = baseline_state(baseline())
    assert original.exists and original.ready and original.identity == "12"
    assert original.binding == baseline_state(baseline(query_ms="50")).binding
    assert original.binding != baseline_state(baseline(enabled="N")).binding
    assert not baseline_state(baseline(), "99").exists
    with pytest.raises(ValueError, match="ambiguous"):
        baseline_state(QueryResult(columns=baseline().columns, rows=baseline().rows * 2))
    with pytest.raises(ValueError, match="truncated"):
        baseline_state(
            QueryResult(columns=baseline().columns, rows=baseline().rows, truncated=True)
        )


async def test_baseline_compensation_reads_only_exact_owned_id():
    candidate, _, _ = fixtures()
    candidate = candidate.model_copy(
        update={"kind": ActionKind.PLAN_BASELINE, "owned_object": "baseline:12"}
    )
    sql = AsyncMock()
    sql.execute.return_value = baseline()
    state = await inspect_object(sql, candidate, candidate.scope, object())
    assert state.identity == "12"
    assert sql.execute.call_args.args[0] == "SHOW BASELINE WHERE Id = 12"
    assert compensation_sql(candidate) == "DROP BASELINE 12"
    with pytest.raises(ValueError, match="identity_required"):
        compensation_sql(candidate.model_copy(update={"owned_object": "baseline:12;DROP TABLE x"}))


async def test_sandbox_cleanup_intent_precedes_drop_and_recovery_never_resubmits(
    setup, monkeypatch
):
    from app.modules.query_autopilot import service as module

    service, repo, candidate, enrollment, _, _ = setup
    candidate = candidate.model_copy(
        update={"kind": ActionKind.PLAN_BASELINE, "owned_object": "baseline:12"}
    )
    owned = ObjectState(True, "12", "definition", True)
    lookup = AsyncMock(side_effect=[owned, ObjectState(False)])
    monkeypatch.setattr(module, "inspect_object", lookup)

    async def execute(*args, **kwargs):
        assert args[0] == "DROP BASELINE 12"
        assert (await repo.get("actions", "trial-cleanup:operation"))["state"] == "APPLYING"

    service.sql.execute = AsyncMock(side_effect=execute)
    assert (
        await service.cleanup_trial(candidate, owned, candidate.scope, object(), "operation")
        == "verified_object_removed"
    )
    service.sql.execute.assert_awaited_once()

    lookup.side_effect = None
    lookup.return_value = owned
    assert (
        await service.cleanup_trial(candidate, owned, candidate.scope, object(), "operation")
        == "sandbox_cleanup_outcome_uncertain_no_retry"
    )
    service.sql.execute.assert_awaited_once()
    lookup.return_value = ObjectState(False)
    assert (
        await service.cleanup_trial(candidate, owned, candidate.scope, object(), "operation")
        == "verified_object_removed"
    )
    service.sql.execute.assert_awaited_once()


@pytest.mark.parametrize("changed", [False, True])
async def test_interrupted_trial_cleanup_revalidates_enrollment_and_original_access(setup, changed):
    service, repo, candidate, enrollment, _, _ = setup
    trial = candidate.model_copy(
        update={"kind": ActionKind.PLAN_BASELINE, "owned_object": "baseline:12"}
    )
    record = {
        "id": "trial",
        "candidate_id": candidate.id,
        "state": "APPLIED",
        "owned_object": asdict(ObjectState(True, "12", "definition", True)),
        "trial_candidate": trial.model_dump(mode="json"),
        "enrollment_binding": digest(enrollment.model_dump(mode="json")),
    }
    await repo.put("experiments", "trial", record)
    if changed:
        await repo.put(
            "enrollments",
            enrollment.id,
            enrollment.model_copy(update={"enabled": False}).model_dump(mode="json"),
        )
    service.cleanup_trial = AsyncMock(return_value="verified_object_removed")
    result = await service.reconcile_trial(candidate, {"id": "trial"})
    if changed:
        assert result == "sandbox_cleanup_enrollment_changed"
        service.cleanup_trial.assert_not_awaited()
        assert not service.sql.calls
    else:
        assert result == "verified_object_removed"
        service.cleanup_trial.assert_awaited_once()
        assert (
            "EXPLAIN SELECT id FROM orders",
            candidate.scope.database,
            "diagnostic",
        ) in service.sql.calls
        assert service.cleanup_trial.call_args.args[2].principal == enrollment.execution_principal
        assert (await repo.get("experiments", "trial"))["state"] == "INCONCLUSIVE"


async def test_interrupted_creation_without_acknowledged_ownership_never_drops(setup):
    service, repo, candidate, _, _, _ = setup
    await repo.put("experiments", "trial", {"id": "trial", "state": "APPLYING"})
    service.cleanup_trial = AsyncMock()
    assert await service.reconcile_trial(candidate, {"id": "trial"}) == (
        "sandbox_creation_outcome_uncertain_ownership_unproven"
    )
    service.cleanup_trial.assert_not_awaited()


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, True),
        ({"last_refresh_state": "FAILED"}, False),
        ({"last_refresh_state": "RUNNING"}, False),
        ({"is_active": "false"}, False),
        ({"query_rewrite_status": "INVALID"}, False),
        ({"last_refresh_error_code": "1"}, False),
        ({"last_refresh_error_message": "Refresh failed"}, False),
        ({"last_freshness_confirmed_at": None}, False),
        ({"last_refresh_time": "2026-10-01 12:01:00"}, False),
        ({"base_table_refresh_version_times": "{}"}, False),
        ({"base_table_refresh_version_times": "[]"}, False),
        ({"base_table_refresh_version_times": "invalid"}, False),
        ({"base_table_refresh_version_times": '{"facts":"2026-10-01 12:01:00"}'}, False),
        ({"base_table_refresh_version_times": '{"facts":null}'}, False),
    ],
)
def test_skipped_refresh_requires_explicit_engine_freshness(change, expected):
    from app.modules.query_autopilot.engine_state import materialized_view_ready

    data = {
        "is_active": "true",
        "last_refresh_state": "SKIPPED",
        "query_rewrite_status": "VALID",
        "last_refresh_error_code": "0",
        "last_refresh_error_message": "",
        "last_refresh_time": "2026-10-01 11:59:00",
        "last_freshness_confirmed_at": "2026-10-01 12:00:00",
        "base_table_refresh_version_times": '{"facts":"2026-10-01 11:59:00"}',
        **change,
    }
    assert materialized_view_ready(data) is expected
