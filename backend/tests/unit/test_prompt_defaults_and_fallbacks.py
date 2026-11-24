"""Prompt rules from the re-bench root cause (P2, P5), kept in the text the model reads.

P2: what_if-001 and session-016.t2 ended on "the what-if total was not
calculated" after compute_metrics failed; the contract had no way forward.
P5: trend-003 and multi_step-010 asked for a period although the full data
range is the default for a trend, and session-015.t4 put a form beside an
interactive page that already had the control.
"""

from app.modules.agents.prompt import build_system_prompt
from app.modules.assistant.human_input import ASK_INSTRUCTION


def test_a_failed_calculation_has_a_declarative_way_forward():
    prompt = build_system_prompt({"name": "Sales Analyst"})
    assert "compute_metrics fails twice" in prompt
    assert "declarative" in prompt and 'never stop at "not calculated"' in prompt


def test_asking_is_not_used_when_a_default_or_a_control_answers_it():
    assert "Do not ask when a sensible default exists" in ASK_INSTRUCTION
    assert "the full data range for a trend" in ASK_INSTRUCTION
    assert "never add a question or form beside it" in ASK_INSTRUCTION
