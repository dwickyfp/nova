from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.engine_state import compensation_sql, inspect_object
from app.modules.query_autopilot.models import ActionKind, Candidate, State
from app.modules.query_autopilot.resource_trial import (
    trial_group_statement,
    validate_resource_baseline,
)
from app.sql_frontend.parser import parse_statement
from tests.unit.query_autopilot import test_service
from tests.unit.query_autopilot.test_policy_experiments import fixtures

setup = test_service.setup


def inventory(name="sandbox", identity="12", **overrides):
    row = {
        "name": name,
        "id": identity,
        "cpu_weight_percent": "10",
        "exclusive_cpu_percent": "null",
        "mem_limit": "10.0%",
        "concurrency_limit": "2",
        "big_query_cpu_second_limit": "0",
        "big_query_scan_rows_limit": "100000",
        "big_query_mem_limit": "0",
        "spill_mem_limit_threshold": "100%",
        "classifiers": "",
        "warehouses": "",
        **overrides,
    }
    return QueryResult(columns=list(row), rows=[list(row.values())])


def test_trial_preserves_limits_and_cannot_classify_other_clients():
    statement = trial_group_statement(
        inventory(), "sandbox", "nova_ap_trial", {"concurrency_limit": 1}
    )
    parse_statement(statement)
    assert statement.startswith(
        "CREATE RESOURCE GROUP `nova_ap_trial` TO (source_ip = '0.0.0.0/32') WITH"
    )
    assert '"mem_limit" = "0.1"' in statement
    assert '"big_query_scan_rows_limit" = "100000"' in statement
    assert '"concurrency_limit" = "1"' in statement
    assert "classifiers" not in statement


@pytest.mark.parametrize(
    "changes",
    [
        {"concurrency_limit": 0},
        {"concurrency_limit": 3},
        {"mem_limit": "20%"},
        {"big_query_scan_rows_limit": 0},
        {"warehouses": "other"},
        {"mem_limit": "NaN"},
        {"concurrency_limit": 1.5},
    ],
)
def test_budget_expansion_and_unsupported_limits_block(changes):
    with pytest.raises(ValueError, match="sandbox_resource"):
        trial_group_statement(inventory(), "sandbox", "nova_ap_trial", changes)


@pytest.mark.parametrize(
    "overrides",
    [
        {"cpu_weight_percent": "null"},
        {"cpu_weight_percent": "0"},
        {"exclusive_cpu_percent": "10"},
        {"warehouses": "other"},
        {"spill_mem_limit_threshold": "0%"},
    ],
)
def test_uncertifiable_inherited_budget_blocks(overrides):
    with pytest.raises(ValueError, match="sandbox_resource"):
        trial_group_statement(inventory(**overrides), "sandbox", "nova_ap_trial", {})


async def test_cleanup_binding_detects_replaced_group_or_new_classifier():
    candidate, _, _ = fixtures()
    candidate = candidate.model_copy(
        update={
            "kind": ActionKind.RESOURCE_GROUP,
            "owned_object": "sandbox-resource-group:nova_ap_trial",
            "parameters": {"resource_group": "nova_ap_trial", "properties": {}},
        }
    )
    sql = AsyncMock()
    sql.execute.return_value = inventory("nova_ap_trial")
    original = await inspect_object(sql, candidate, candidate.scope, object())
    for result in (
        inventory("nova_ap_trial", "13"),
        inventory("nova_ap_trial", classifiers="user=other"),
    ):
        sql.execute.return_value = result
        current = await inspect_object(sql, candidate, candidate.scope, object())
        assert current.binding != original.binding
    assert compensation_sql(candidate) == "DROP RESOURCE GROUP `nova_ap_trial`"
    with pytest.raises(ValueError, match="ownership_required"):
        compensation_sql(candidate.model_copy(update={"owned_object": None}))


async def test_full_trial_uses_temporary_group_and_verifies_cleanup(setup, monkeypatch):
    service, repo, candidate, enrollment, _, user = setup
    enrollment = enrollment.model_copy(update={"production_resource_group": "production"})
    await repo.put("enrollments", enrollment.id, enrollment.model_dump(mode="json"))
    candidate = candidate.model_copy(
        update={
            "kind": ActionKind.RESOURCE_GROUP,
            "parameters": {"resource_group": "production", "properties": {"concurrency_limit": 1}},
        }
    )
    await service._candidate(candidate)
    original_execute = service.sql.execute
    temporary = None
    production_identity = "38"

    async def execute(statement, scope, **options):
        nonlocal temporary
        if statement == "SHOW RESOURCE GROUPS ALL":
            result = inventory(enrollment.budget.resource_group)
            result.rows += inventory("production", production_identity).rows
            if temporary:
                result.rows += inventory(temporary, "99", concurrency_limit="1").rows
            return result
        if statement.startswith("CREATE RESOURCE GROUP"):
            assert any(
                kind == "experiments" and value.get("state") == "APPLYING"
                for (kind, _), value in repo.records.items()
            )
            temporary = statement.split("`")[1]
            service.sql.optimized = True
        if statement.startswith("DROP RESOURCE GROUP"):
            assert statement == f"DROP RESOURCE GROUP `{temporary}`"
            assert any(
                value.get("kind") == "EXPERIMENT_CLEANUP" and value["state"] == "APPLYING"
                for value in repo.records.values()
            )
            temporary = None
        return await original_execute(statement, scope, **options)

    service.sql.execute = execute
    job = await service.mutate(
        candidate.id, "experiment", version=1, idempotency_key="isolated-resource", user=user
    )
    assert (await service.run_operation(await repo.get("jobs", job["id"])))["state"] == "COMPLETED"
    ready = Candidate.model_validate(await repo.get("opportunities", candidate.id))
    assert ready.state == State.READY_APPROVAL
    result = await repo.get("experiments", ready.experiment_id)
    assert result["cleanup"] == "verified_object_removed"
    assert temporary is None
    statements = [call[0] for call in service.sql.calls]
    assert not any(statement.startswith("ALTER RESOURCE GROUP") for statement in statements)
    restored = f"SET resource_group = '{enrollment.budget.resource_group}'"
    assert statements.count(restored) == 2
    assert result["resource_source"]["identity"] == "38"
    assert result["resource_budget"]["identity"] == "12"
    await service.mutate(
        candidate.id, "approve", version=ready.version,
        idempotency_key="resource-approval", user=user,
    )
    approved = Candidate.model_validate(await repo.get("opportunities", candidate.id))
    production_identity = "39"
    from app.modules.query_autopilot.service import session_store

    monkeypatch.setattr(session_store, "get", AsyncMock(return_value=user))
    apply = await service.mutate(
        candidate.id, "apply", version=approved.version,
        idempotency_key="resource-apply", user=user,
    )
    blocked = await service.run_operation(await repo.get("jobs", apply["id"]))
    assert blocked == {"state": "BLOCKED", "reason": "resource_group_changed_since_experiment"}
    assert (await repo.get("opportunities", candidate.id))["state"] == "BLOCKED"
    assert not any(call[2] == "maintenance" for call in service.sql.calls)


@pytest.mark.parametrize("changes", [
    {"concurrency_limit": "1"}, {"mem_limit": "20%"},
    {"cpu_weight_percent": "20"}, {"big_query_scan_rows_limit": "0"},
])
def test_production_baseline_must_be_represented_in_sandbox(changes):
    with pytest.raises(ValueError, match="sandbox_resource_baseline_differs_from_production"):
        validate_resource_baseline(
            inventory("production", "38", **changes), "production", inventory(), "sandbox",
        )


def test_baseline_binds_original_group_identity_and_classifiers():
    original = validate_resource_baseline(
        inventory("production", "38", classifiers="user=alice"), "production",
        inventory(), "sandbox",
    )
    replaced = validate_resource_baseline(
        inventory("production", "39", classifiers="user=bob"), "production",
        inventory(), "sandbox",
    )
    assert original.identity == "38" and original.binding != replaced.binding
