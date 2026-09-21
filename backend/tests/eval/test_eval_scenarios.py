"""Agentic-harness evaluation suite (NOVA-124).

Run:

    cd backend
    uv run pytest tests/eval -q

Each scenario is deterministic and offline: a scripted provider stands in for the
model, so the suite runs in CI with no provider key and no engine. A failing
check fails the corresponding test, so a change that breaks tool selection,
consent, guarding, boundedness, redaction, or context management is caught here
rather than in production.

The same scenarios can be printed as a scorecard with::

    uv run python -m tests.eval.report
"""

from __future__ import annotations

import pytest

from tests.eval.harness import evaluate, run_scenario
from tests.eval.scenarios import all_scenarios

pytestmark = pytest.mark.eval

SCENARIOS = all_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
async def test_scenario(scenario):
    result = await run_scenario(scenario)
    report = evaluate(scenario, result)
    assert report.passed, report.summary()
