"""D3: Studio has no save-from-chat tool; the agent points to the Save button.

Re-bench session-037.t3 ("Oke simpan grafiknya") and session-006.t4 ("Simpan ke
dashboard"): the agent said it could not save, without naming where to do it.
The owner decided (D3) against a save tool: the expected answer is an honest
limitation that points to the button under the chart or table.
"""

from __future__ import annotations

from app.modules.agents.prompt import build_system_prompt


def _prompt() -> str:
    return " ".join(build_system_prompt(
        {"name": "Sales Analyst", "default_tools": ["semantic_query", "data_to_chart"]}).split())


def test_the_agent_says_it_cannot_save_and_names_the_buttons():
    prompt = _prompt()

    assert "You cannot save a chart or table to a Dataviz dashboard from the chat." in prompt
    assert '"Save to dashboard…" for a chart, "Add to Dataviz" for a table' in prompt
    assert "Never claim it was saved." in prompt


def test_the_button_labels_match_the_studio_interface():
    """The labels the agent names are the ones Studio shows."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "frontend" / "src" / "features" / "studio"
    views = (root / "runtime" / "artifacts" / "artifact-views.tsx").read_text()
    add = (root / "dataviz" / "add-to-dataviz.tsx").read_text()
    assert "Save to dashboard…" in views
    assert "Add to Dataviz" in add
