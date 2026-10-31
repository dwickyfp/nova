"""The Smart token benchmark attributes prompt tokens and calls correctly."""

from __future__ import annotations

from tests.benchmark.smart_tokens.probe import _participant, composition
from tests.benchmark.smart_tokens.report import compare, summarise_run

SYSTEM = {"role": "system", "content": "x" * 400 + " You are /root/visits in a governed Smart "
          "collaboration. Your parent is /root."}


def test_prompt_parts_name_each_tool_result_and_coordination_message() -> None:
    messages = [
        SYSTEM,
        {"role": "user", "content": "t" * 40},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "semantic_query", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "r" * 80},
        {"role": "user", "content": "<AGENT_COORDINATION_MESSAGE>" + "m" * 72},
    ]
    parts = composition(messages, [{"function": {"name": "data_to_chart"}}])
    assert parts["task"] == 10 and parts["tool:semantic_query"] == 20
    assert parts["coordination"] == 25 and parts["tool_schemas"] > 0
    assert _participant(messages) == "/root/visits"
    assert _participant([{"role": "user", "content": "plan"}]) == "internal"


def test_a_run_counts_only_calls_inside_its_window_by_participant() -> None:
    def call(at, participant, prompt, purpose="tools"):
        return {"at": at, "seconds": 1.0, "participant": participant, "purpose": purpose,
                "tools": ["wait_agent"], "tool_calls": [], "parts": {"system": prompt // 2},
                "usage": {"prompt_tokens": prompt, "completion_tokens": 10}}

    run = {"request": "dashboard", "run_id": "r1", "status": "completed", "seconds": 60,
           "started": 100.0, "finished": 160.0}
    calls = [call(102, "/root", 9000), call(130, "/root/visits", 4000),
             call(140, "internal", 2000, "semantic_plan"), call(400, "/root", 99999)]
    summary = summarise_run(run, calls)
    assert summary["calls"] == 3 and summary["prompt_tokens"] == 15000
    assert summary["groups"]["root"]["prompt_tokens"] == 9000
    assert summary["groups"]["specialists"]["calls"] == 1
    assert summary["groups"]["internal"]["prompt_tokens"] == 2000


def test_comparison_reports_the_relative_change() -> None:
    before = {"mean": {"root_prompt_tokens": 80000.0}, "completed": 3, "attempted": 3}
    after = {"mean": {"root_prompt_tokens": 40000.0}, "completed": 3, "attempted": 3}
    assert "-50%" in compare(before, after)
