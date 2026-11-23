"""The planning row says what the planner weighs while it works."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.assistant.service import _planning_status


def _registry(skills: int) -> SimpleNamespace:
    return SimpleNamespace(discoverable_skills=tuple(f"skill_{i}" for i in range(skills)))


def test_names_the_business_views_and_counts_the_skills() -> None:
    context = SimpleNamespace(
        agent_scope={"surface": "studio"},
        authorized_semantic_models=[{"name": "sales_orders"}, {"name": "returns"},
                                    {"name": "inventory"}],
    )
    text = _planning_status(context, _registry(6))
    assert text == (
        "Understanding the request: weighing the Sales orders, Returns and 1 more data"
        " and 6 skills"
    )
    assert "sales_orders" not in text  # business labels, not internal names


def test_one_skill_and_no_views() -> None:
    context = SimpleNamespace(agent_scope={"surface": "studio"}, authorized_semantic_models=None)
    assert _planning_status(context, _registry(1)) == "Understanding the request: weighing 1 skill"


def test_falls_back_when_nothing_is_known() -> None:
    context = SimpleNamespace(agent_scope=None, authorized_semantic_models=None)
    assert _planning_status(context, _registry(0)) == (
        "Understanding the request and choosing a skill"
    )


def test_selected_actions_row_names_every_analysis_tool_plainly() -> None:
    from app.modules.assistant import events
    from app.modules.assistant.plain_language import plain_frames
    from app.modules.assistant.service import _ANALYSIS_TOOLS

    frame = events.thinking(
        "plan", "Selected actions: " + ", ".join(_ANALYSIS_TOOLS), status="done",
    )
    shown = "".join(plain_frames()(frame))
    assert not any(name in shown for name in _ANALYSIS_TOOLS)
