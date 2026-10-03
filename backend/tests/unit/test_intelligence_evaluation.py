"""A score requires numerical output from the same verified tool invocation."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.agents.semantic.planning import SemanticPlan
from tests.benchmark.business_intelligence.client import StudioClient, Turn
from tests.benchmark.business_intelligence.evaluate import evaluate
from tests.benchmark.business_intelligence.gold.cases import Case


@pytest.mark.parametrize(
    ("detail", "reason"),
    [
        ("Semantic version not found", "semantic_version_unavailable"),
        ("private server detail", None),
        ([{"input": "private request content"}], None),
    ],
)
async def test_benchmark_errors_keep_only_allowlisted_reasons(detail, reason):
    def response(request):
        return httpx.Response(
            404, json={"detail": detail, "stored_secret": "private server content"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(response), base_url="http://benchmark.test"
    ) as http:
        with pytest.raises(RuntimeError) as caught:
            await StudioClient(http).request("GET", "intelligence/decisions/missing?private=value")
    message = str(caught.value)
    assert "HTTP 404" in message
    assert "private" not in message and "stored_secret" not in message
    assert ("semantic_version_unavailable" in message) == bool(reason)


async def test_score_does_not_join_a_correct_plan_with_an_unrelated_table():
    plan = SemanticPlan.from_dict({"metrics": ["healthy_orders"], "limit": 100}).as_dict()
    case = Case(
        id="development-score",
        category="rule",
        question="Development fixture",
        learning_sensitive=True,
        expected_plan=plan,
        parameters={
            "metric": "healthy_orders",
            "city": "Jakarta",
            "start": "2026-01-01",
            "end": "2026-01-02",
            "daily": False,
        },
    )
    table = {"kind": "table", "tool_call_id": "unrelated", "rows": [[12]]}
    turn = Turn(
        "thread",
        "answer",
        {
            "steps": [
                {
                    "kind": "tool",
                    "tool_call_id": "expected",
                    "name": "semantic_query",
                    "status": "done",
                    "trace_detail": {"semantic_plan": plan},
                },
                table,
            ]
        },
        [],
        1.0,
        "stop",
    )
    execute = AsyncMock(return_value={"rows": [[12]]})
    result = await evaluate(case, turn, execute, repetition=0)
    assert result["scores"]["plan_accuracy"] is True
    assert result["scores"]["answer_accuracy"] is False
    table["tool_call_id"] = "expected"
    result = await evaluate(case, turn, execute, repetition=0)
    assert result["scores"]["answer_accuracy"] is True
    table["rows"] = [[13]]
    result = await evaluate(case, turn, execute, repetition=0)
    assert result["scores"]["answer_accuracy"] is False
    assert result["silent_wrong"] is True
