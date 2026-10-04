"""Learning consumes only authenticated workload shape and remains reviewable."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic import usage_learning
from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from app.modules.intelligence.contracts import Scope, SemanticRef
from tests.unit.test_semantic_intelligence import sales_model

SCOPE = Scope(principal="alice", active_role="ANALYST", security_context_version=3)
REF = SemanticRef(view_id="sales", version=2, fingerprint="definition")
DEFINITION = semantic_ir_to_definition(sales_model())


def usage(**patch):
    return {
        "owner_name": "alice", "active_role": "ANALYST", "security_context_version": 3,
        "semantic_model_id": "sales", "model_fingerprint": "definition", "semantic_version": 2,
        "metrics": ["total_revenue"], "dimensions": ["city"],
        "filter_shape": [{"field": "city", "operator": "=", "value": "PRIVATE-LITERAL"}],
        "sql": "SELECT secret FROM credentials", "question": "Private customer query",
        "time_grain": "month", "succeeded": True, "execution_latency_ms": 10,
        **patch,
    }


@pytest.mark.parametrize("patch", [
    {"owner_name": "bob"}, {"active_role": None}, {"active_role": "ACCOUNTADMIN"},
    {"security_context_version": None}, {"security_context_version": 2},
    {"security_context_version": True}, {"semantic_model_id": "other"},
    {"semantic_version": 1}, {"model_fingerprint": "previous"},
    {"metrics": ["private_metric"]}, {"dimensions": ["password"]},
    {"filter_shape": [{"field": "private", "operator": "="}]},
    {"filter_shape": [{"field": "city", "operator": ["="]}]},
    {"filter_shape": [{"field": ["city"], "operator": "="}]},
    {"succeeded": "true"}, {"succeeded": 2}, {"time_grain": ["month"]},
])
def test_unknown_legacy_scope_and_cross_role_rows_are_excluded(patch):
    result = usage_learning.aggregate_usage([usage(**patch)], SCOPE, REF, DEFINITION)
    assert result["patterns"] == [] and result["excluded_rows"] == 1


def test_canonical_shapes_are_deduplicated_and_never_contain_sql_or_values():
    rows = [usage(), usage(), usage(), usage(succeeded=False, execution_latency_ms=30)]
    before = deepcopy(rows)
    summary = usage_learning.aggregate_usage(rows, SCOPE, REF, DEFINITION)
    assert rows == before
    assert len(summary["patterns"]) == 1
    pattern = summary["patterns"][0]
    assert pattern["observations"] == 4 and pattern["successes"] == 3
    assert pattern["mean_latency_ms"] == 15
    assert set(pattern["suggestions"]) == {"review_failed_workload", "consider_verified_query"}
    assert pattern["filter_shape"] == [{"field": "city", "operator": "="}]
    assert summary["authority"] == "usage_observation" and summary["review_required"]
    for private in ["PRIVATE-LITERAL", "secret", "Private customer query", "credentials", "sql"]:
        assert private not in str(summary)
    assert usage_learning.aggregate_usage(list(reversed(rows)), SCOPE, REF, DEFINITION) == summary


def test_absent_semantic_version_is_allowed_only_with_known_scope_and_fingerprint():
    result = usage_learning.aggregate_usage([usage(semantic_version=None)], SCOPE, REF, DEFINITION)
    assert result["patterns"][0]["observations"] == 1
    result = usage_learning.aggregate_usage(
        [usage(semantic_version=None, active_role=None)], SCOPE, REF, DEFINITION,
    )
    assert result["patterns"] == []


async def test_loader_requires_repository_scope_before_aggregation(monkeypatch):
    load = AsyncMock(return_value=[usage()])
    monkeypatch.setattr(
        usage_learning.agent_repository, "list_semantic_usage", load, raising=False,
    )
    result = await usage_learning.load_scoped_usage(SCOPE, REF, DEFINITION)
    assert load.await_args.kwargs == {
        "owner_name": "alice", "active_role": "ANALYST", "security_context_version": 3,
        "semantic_model_id": "sales", "model_fingerprint": "definition",
        "semantic_version": 2, "limit": 2000,
    }
    assert result["patterns"][0]["observations"] == 1
