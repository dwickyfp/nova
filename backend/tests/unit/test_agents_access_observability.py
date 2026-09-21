"""Unit tests for access verification and observability helpers.

Pure logic only: grant matching, dependency resolution shape, and the usage
accumulator. No engine, no network.
"""

from __future__ import annotations

from app.modules.agents.access import _safe_role, matches_object
from app.modules.assistant.service import _accumulate_usage


def test_safe_role_rejects_injection() -> None:
    assert _safe_role("ACCOUNTADMIN") == "ACCOUNTADMIN"
    assert _safe_role("data_engineer") == "data_engineer"
    for bad in ["a; DROP", "a`b", "a b", "", "a-b"]:
        try:
            _safe_role(bad)
            raise AssertionError(f"should reject {bad!r}")
        except ValueError:
            pass


def test_matches_object_function() -> None:
    grants = ["GRANT USAGE ON FUNCTION db.fn TO ROLE r"]
    assert matches_object(grants, "function", "db.fn")
    assert not matches_object(grants, "function", "db.other")
    assert not matches_object(grants, "table", "db.fn")


def test_matches_object_wildcard() -> None:
    grants = ["GRANT ALL ON *.* TO ROLE r"]
    assert matches_object(grants, "table", "any.table")
    assert matches_object(grants, "function", "db.fn")


def test_matches_object_table_select() -> None:
    grants = ["GRANT SELECT ON TABLE NOVA_DEMO.orders TO ROLE analyst"]
    assert matches_object(grants, "table", "NOVA_DEMO.orders")
    assert not matches_object(grants, "table", "NOVA_DEMO.other")


def test_accumulate_usage_sums_across_calls() -> None:
    class Ctx:
        usage = None

    ctx = Ctx()
    _accumulate_usage(
        ctx, {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    )
    _accumulate_usage(
        ctx, {"usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}}
    )
    assert ctx.usage == {"prompt_tokens": 30, "completion_tokens": 13, "total_tokens": 43}


def test_accumulate_usage_ignores_missing_usage() -> None:
    class Ctx:
        usage = None

    ctx = Ctx()
    _accumulate_usage(ctx, {"content": "hi"})
    assert ctx.usage is None


def test_trace_step_recording_shapes() -> None:
    """The loop records reasoning, tool, and answer steps with redacted args."""
    from app.modules.assistant.service import (
        _finish_tool_step,
        _record_step,
    )

    class Ctx:
        steps = None

    ctx = Ctx()
    _record_step(ctx, {"kind": "reasoning", "phase": "plan", "text": "Understanding"})
    _record_step(
        ctx,
        {
            "kind": "tool",
            "name": "query_execute",
            "preview": "SELECT 1",
            "arguments": {"sql": "SELECT 1"},
            "status": "running",
        },
    )
    _finish_tool_step(ctx, "done", None)
    _record_step(ctx, {"kind": "answer"})

    assert [s["kind"] for s in ctx.steps] == ["reasoning", "tool", "answer"]
    assert ctx.steps[1]["status"] == "done"


def test_redacted_arguments_masks_credentials() -> None:
    from app.modules.assistant.service import _redacted_arguments

    out = _redacted_arguments(
        {"question": "revenue", "password": "hunter2", "api_key": "sk-abc"}
    )
    assert out["question"] == "revenue"
    assert out["password"] == "***"
    assert out["api_key"] == "***"
