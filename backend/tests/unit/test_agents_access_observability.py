"""Unit tests for access verification and observability helpers.

Pure logic only: dependency resolution shape and the usage accumulator. No
engine, no network.
"""

from __future__ import annotations

from app.modules.assistant.service import _accumulate_usage


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
