from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.engine_state import registered_snapshot_state
from app.modules.query_autopilot.models import Scope, State
from tests.unit.query_autopilot import test_service

setup = test_service.setup


@pytest.mark.parametrize(
    "result",
    [
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[]),
        QueryResult(columns=["PartitionId"], rows=[[1]]),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, 7]], truncated=True),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, None]]),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, True]]),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, 0]]),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, float("nan")]]),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, 7], [1, 8]]),
    ],
)
async def test_partial_or_ambiguous_partition_inventory_cannot_prove_snapshot(setup, result):
    _, _, _, enrollment, _, _ = setup
    sql = AsyncMock()
    sql.execute.return_value = result
    assert await registered_snapshot_state(sql, enrollment, enrollment.scope, object()) is None


async def test_snapshot_is_stable_under_inventory_order_and_changes_with_partition_version(setup):
    _, _, _, enrollment, _, _ = setup
    sql = AsyncMock()
    sql.execute.side_effect = [
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[2, 8], [1, 7]]),
        QueryResult(columns=["VisibleVersion", "PartitionId"], rows=[["7", "1"], ["8", "2"]]),
        QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[2, 9], [1, 7]]),
    ]
    scope = Scope(
        principal=enrollment.execution_principal,
        active_role=enrollment.execution_role,
        database=enrollment.sandbox_database,
        security_context_version=enrollment.version,
    )
    a = await registered_snapshot_state(sql, enrollment, scope, object())
    b = await registered_snapshot_state(sql, enrollment, scope, object())
    c = await registered_snapshot_state(sql, enrollment, scope, object())
    assert a is not None and a == b and a != c
    assert all(
        call.args[1] == scope and call.kwargs["category"] == "diagnostic"
        for call in sql.execute.call_args_list
    )


@pytest.mark.parametrize("change", ["missing", "partition_version", "unavailable"])
async def test_snapshot_change_blocks_approved_application_before_maintenance(setup, change):
    service, repo, candidate, enrollment, policy, user = setup
    enrollment = enrollment.model_copy(update={"statistics_auto": False})
    await repo.put("enrollments", enrollment.id, enrollment.model_dump(mode="json"))
    job = await service.mutate(
        candidate.id,
        "experiment",
        version=candidate.version,
        idempotency_key="snapshot-binding-trial",
        user=user,
    )
    assert (await service.run_operation(job))["state"] == "COMPLETED"
    candidate = test_service.Candidate.model_validate(await repo.get("opportunities", candidate.id))
    assert candidate.state == State.READY_APPROVAL
    await service.mutate(
        candidate.id,
        "approve",
        version=candidate.version,
        idempotency_key="snapshot-binding-approval",
        user=user,
    )
    candidate = test_service.Candidate.model_validate(await repo.get("opportunities", candidate.id))
    if change == "missing":
        experiment = await repo.get("experiments", candidate.experiment_id)
        experiment["result"].pop("snapshot")
        await repo.put("experiments", candidate.experiment_id, experiment)
    else:
        original = service.sql.execute

        async def execute(statement, scope, **kwargs):
            if statement.startswith("SHOW PARTITIONS"):
                return QueryResult(
                    columns=["PartitionId", "VisibleVersion"],
                    rows=[] if change == "unavailable" else [[1, 8]],
                )
            return await original(statement, scope, **kwargs)

        service.sql.execute = execute
    apply_job = await service.mutate(
        candidate.id,
        "apply",
        version=candidate.version,
        idempotency_key="snapshot-binding-apply",
        user=user,
    )
    # The administrator remains active; a data-state change is the reason for refusal.
    service_module = __import__("app.modules.query_autopilot.service", fromlist=["session_store"])
    service_module.session_store.get.return_value = user
    result = await service.run_operation(apply_job)
    assert result["state"] == "BLOCKED"
    assert result["reason"] == (
        "experiment_snapshot_evidence_unavailable"
        if change == "missing"
        else "snapshot_changed_since_experiment"
    )
    assert not any(call[-1] == "maintenance" for call in service.sql.calls)
    assert await repo.get("actions", apply_job["id"]) is None


@pytest.mark.parametrize("change", ["missing", "compiler", "unsupported"])
async def test_changed_action_definition_invalidates_approval_before_side_effect(
    setup, monkeypatch, change,
):
    from app.modules.query_autopilot import service as module
    from app.modules.query_autopilot.models import digest

    service, repo, candidate, enrollment, _, user = setup
    await repo.put(
        "enrollments", enrollment.id,
        enrollment.model_copy(update={"statistics_auto": False}).model_dump(mode="json"),
    )
    trial = await service.mutate(
        candidate.id, "experiment", version=candidate.version,
        idempotency_key="definition-trial", user=user,
    )
    assert (await service.run_operation(trial))["state"] == "COMPLETED"
    candidate = test_service.Candidate.model_validate(await repo.get("opportunities", candidate.id))
    experiment = await repo.get("experiments", candidate.experiment_id)
    assert experiment["application_definition_digest"] == digest(module.action_sql(candidate))
    await service.mutate(
        candidate.id, "approve", version=candidate.version,
        idempotency_key="definition-approve", user=user,
    )
    candidate = test_service.Candidate.model_validate(await repo.get("opportunities", candidate.id))
    if change == "missing":
        experiment.pop("application_definition_digest")
        await repo.put("experiments", candidate.experiment_id, experiment)
    elif change == "compiler":
        monkeypatch.setattr(module, "action_sql", lambda *_args, **_kwargs: ("CHANGED DDL",))
    else:
        def unsupported(*_args, **_kwargs):
            raise ValueError("private compiler failure")

        monkeypatch.setattr(module, "action_sql", unsupported)
    module.session_store.get.return_value = user
    apply_job = await service.mutate(
        candidate.id, "apply", version=candidate.version,
        idempotency_key="definition-apply", user=user,
    )
    result = await service.run_operation(apply_job)
    assert result == {
        "state": "BLOCKED",
        "reason": "application_definition_unavailable" if change == "unsupported"
        else "application_definition_changed_since_experiment",
    }
    current = test_service.Candidate.model_validate(await repo.get("opportunities", candidate.id))
    assert current.approval_digest is None and current.approved_by is None
    assert current.approval_expires_at is None
    assert not any(call[-1] == "maintenance" for call in service.sql.calls)
    assert await repo.get("actions", apply_job["id"]) is None
