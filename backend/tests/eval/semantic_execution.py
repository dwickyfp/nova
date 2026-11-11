"""Assertions for a semantic answer and its two governed data-window probes."""

from unittest.mock import AsyncMock


def assert_answer_with_window_probes(execute: AsyncMock, user: dict):
    calls = execute.await_args_list
    assert len(calls) == 3
    answer, *probes = calls
    assert "< '2100-01-01'" not in answer.kwargs["sql"]
    for call in calls:
        assert call.kwargs["username"] == user["username"]
        assert call.kwargs["role"] == user["active_role"]
        assert call.kwargs["security_context_version"] == user.get("security_context_version", 1)
        assert call.kwargs["confirm_destructive"] is False
    for probe in probes:
        assert probe.kwargs["max_rows"] == 1
        assert "< '2100-01-01'" in probe.kwargs["sql"]
        assert "LIMIT 1" in probe.kwargs["sql"]
    assert sum(" ASC" in probe.kwargs["sql"] for probe in probes) == 1
    assert sum(" DESC" in probe.kwargs["sql"] for probe in probes) == 1
    return answer
