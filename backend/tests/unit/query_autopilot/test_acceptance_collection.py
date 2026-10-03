from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.access_control.service import access_control_service
from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.acceptance import (
    collect_ranger_acceptance,
    ranger_acceptance_reason,
)
from app.modules.query_autopilot.engine_state import ObjectState, object_name
from app.modules.query_autopilot.models import ActionKind, Scope, utcnow
from app.modules.query_autopilot.runtime import AuthorizationUnavailable
from tests.unit.query_autopilot import test_service

setup = test_service.setup


@pytest.mark.parametrize(
    "change,reason",
    [
        (None, None),
        ("policy_difference", "ranger_snapshot_security_effects_differ"),
        ("no_mask", "ranger_filter_or_mask_acceptance_unproven"),
        ("unsupported_policy", "ranger_security_effects_comparison_unsupported"),
        ("source_denied", "engine_authorization_refused"),
        ("rewrite_missing", "ranger_governed_rewrite_unproven"),
        ("truncated", "ranger_complete_result_or_correlation_unavailable"),
        ("uncorrelated", "ranger_complete_result_or_correlation_unavailable"),
        ("changed_value", "ranger_governed_result_changed"),
        ("changed_type", "ranger_governed_result_changed"),
        ("snapshot_changed", "snapshot_changed"),
        ("freshness_changed", "materialized_view_rewrite_or_freshness_unproven"),
    ],
)
async def test_scoped_governed_proof_requires_real_complete_equivalence_and_unchanged_state(
    setup,
    monkeypatch,
    change,
    reason,
):
    service, repo, candidate, enrollment, _, _ = setup
    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    candidate = candidate.model_copy(
        update={
            "kind": ActionKind.MATERIALIZED_VIEW,
            "scope": candidate.scope.model_copy(update={"policy_revision": "epoch"}),
        }
    )
    sandbox = Scope(
        principal=enrollment.execution_principal,
        active_role=enrollment.execution_role,
        database=enrollment.sandbox_database,
        security_context_version=enrollment.version,
        policy_revision="epoch",
    )
    effects = {
        "security_effects_comparison_supported": change != "unsupported_policy",
        "security_effects_fingerprint": "same-resource-policy-set",
        "row_restrictions": ["city='scoped'"],
        "column_restrictions": {} if change == "no_mask" else {"phone": "MASK"},
        "column_restriction_fingerprints": {} if change == "no_mask" else {"phone": "same-mask"},
    }
    policies = AsyncMock(
        side_effect=(
            [effects, {**effects, "row_restrictions": ["city='other'"]}]
            if change == "policy_difference"
            else None
        ),
        return_value=effects,
    )
    monkeypatch.setattr(access_control_service, "effective_access", policies)
    owned = ObjectState(True, "8", "definition", True)
    fresh = owned if change != "freshness_changed" else ObjectState(True, "8", "definition", False)
    monkeypatch.setattr(
        "app.modules.query_autopilot.engine_state.inspect_object", AsyncMock(return_value=fresh)
    )
    calls, readers = [], []
    rewriting = False
    partitions = 0

    @asynccontextmanager
    async def connection(scope, **kwargs):
        readers.append(scope)
        if change == "source_denied":
            raise AuthorizationUnavailable("engine_authorization_refused")
        yield object()

    async def execute(statement, scope, **kwargs):
        nonlocal rewriting, partitions
        calls.append((statement, scope, kwargs))
        assert kwargs["category"] == "experiment" or statement.startswith("SHOW PARTITIONS")
        if statement.startswith("SHOW PARTITIONS"):
            partitions += 1
            version = 8 if change == "snapshot_changed" and partitions > 1 else 7
            return QueryResult(columns=["PartitionId", "VisibleVersion"], rows=[[1, version]])
        if statement.startswith("SET enable_materialized_view_rewrite"):
            rewriting = statement.endswith("true")
        if statement.startswith("EXPLAIN"):
            return QueryResult(
                columns=["plan"],
                rows=[
                    ["0:OlapScanNode"],
                    [
                        "TABLE: "
                        + (
                            object_name(candidate)
                            if rewriting and change != "rewrite_missing"
                            else "orders"
                        )
                    ],
                ],
            )
        return QueryResult(
            columns=["value"],
            rows=[[2 if rewriting and change == "changed_value" else 1]],
            column_types=("BIGINT" if rewriting and change == "changed_type" else "INT",),
            engine_query_ids=[] if change == "uncorrelated" else ["query-" + str(len(calls))],
            truncated=change == "truncated",
        )

    service.sql.connection = connection
    service.sql.execute = execute
    replay = "SELECT id FROM `snapshot`.`orders`"
    if reason:
        with pytest.raises(ValueError, match=reason):
            await collect_ranger_acceptance(
                service.sql,
                repo,
                candidate,
                enrollment,
                replay,
                sandbox,
                object(),
                owned,
            )
    else:
        proof = await collect_ranger_acceptance(
            service.sql,
            repo,
            candidate,
            enrollment,
            replay,
            sandbox,
            object(),
            owned,
        )
        assert proof.summary["complete_result_comparisons"] == 60
        assert proof.summary["warmups_per_phase"] == 3
        assert len(proof.query_ids) == 60
        assert proof.summary["row_filter"] == proof.summary["masking"] == "PASS"
        assert proof.summary["semantics"] == "governed_result_preservation"
        assert (
            ranger_acceptance_reason(
                proof.model_dump(mode="json"),
                candidate.scope,
                enrollment.snapshot_id,
                replay,
                utcnow(),
                family_id=candidate.family_id,
                execution_scope=sandbox,
                snapshot_state=proof.summary["snapshot_state"],
            )
            is None
        )
    persisted = [
        v
        for (kind, _), v in repo.records.items()
        if kind == "evidence" and v.get("kind") == "ranger_acceptance"
    ]
    assert len(persisted) == 1
    assert persisted[0]["availability"] == (
        "available"
        if reason is None
        else "unauthorized"
        if change == "source_denied"
        else "unavailable"
    )
    assert "rows" not in persisted[0]["summary"]
    if readers:
        reader = readers[0]
        assert (reader.principal, reader.active_role, reader.security_context_version) == (
            candidate.scope.principal,
            candidate.scope.active_role,
            candidate.scope.security_context_version,
        )
        assert reader.database == enrollment.sandbox_database
    assert all(scope.principal.lower() != "root" for _, scope, _ in calls)
    if change in {"policy_difference", "no_mask", "unsupported_policy"}:
        assert not calls and not readers


async def test_native_engine_cannot_issue_governed_proof(setup, monkeypatch):
    service, repo, candidate, enrollment, _, _ = setup
    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    with pytest.raises(ValueError, match="ranger_acceptance_unsupported"):
        await collect_ranger_acceptance(
            service.sql,
            repo,
            candidate,
            enrollment,
            "SELECT id",
            enrollment.scope,
            object(),
            ObjectState(True),
        )
    assert not service.sql.calls
    assert not any(v.get("kind") == "ranger_acceptance" for v in repo.records.values())
