"""schedule_automation says what to fix, in words the step chip can show.

Benchmark automation-001/002/004 and session-025.t2: the first call failed and
the chip read "The step could not be completed." Pydantic's message links to its
documentation site, and a reason holding a link is replaced by that generic line,
so neither the agent nor the user saw what was wrong.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import (
    MetadataOutcomeUnknown,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
    MetadataTimeout,
)
from app.modules.agents.automations import (
    AutomationConflict,
    AutomationError,
    automation_repository,
)
from app.modules.agents.tools.schedule_automation import schedule_automation_tool
from app.modules.assistant.service import failure_reason
from app.modules.assistant.tools import ToolInvocation

ARGS = {
    "title": "Ringkasan pendapatan harian", "prompt": "Ringkasan pendapatan kemarin",
    "schedule_kind": "cron", "schedule_expr": "0 7 * * *",
}


def _context() -> SimpleNamespace:
    return SimpleNamespace(agent_id="agent-1", agent_owner_name="owner", role="ANALYST",
                           user={"username": "owner", "active_role": "ANALYST"})


async def _run(arguments: dict) -> object:
    return await schedule_automation_tool.run(
        ToolInvocation("c1", "schedule_automation", arguments), _context())


async def test_a_constraint_the_call_schema_does_not_state_is_named():
    """The call schema has no minimum length; the stored automation does."""
    outcome = await _run({**ARGS, "title": ""})

    assert not outcome.ok and outcome.recoverable
    assert outcome.error == "title: String should have at least 1 character."
    assert failure_reason(outcome.error, outcome.error_class) == outcome.error


async def test_invalid_arguments_name_the_field_without_a_link():
    outcome = await _run({**ARGS, "schedule_kind": "weekly", "prompt": "ab"})

    assert not outcome.ok and outcome.recoverable
    assert outcome.error_class == "INVALID_TOOL_ARGUMENTS"
    assert "schedule_kind: Input should be 'cron' or 'interval'" in outcome.error
    assert "prompt: String should have at least 3 characters" in outcome.error
    assert "http" not in outcome.error
    # The chip shows the reason itself, not the generic line.
    assert failure_reason(outcome.error, outcome.error_class) == outcome.error


async def test_a_missing_required_field_is_named():
    outcome = await _run({key: value for key, value in ARGS.items() if key != "schedule_expr"})

    assert not outcome.ok and outcome.recoverable
    assert "schedule_expr: Field required" in outcome.error


@pytest.mark.parametrize(("error", "error_class", "recoverable", "says"), [
    (AutomationError("invalid cron expression: '0 7 * *'"), "INVALID_SCHEDULE", True,
     "invalid cron expression"),
    (AutomationError("unknown IANA timezone: 'WIB'"), "INVALID_SCHEDULE", True,
     "unknown IANA timezone"),
    (AutomationError("Automations run at most every 60 minutes."), "INVALID_SCHEDULE", True,
     "at most every 60 minutes"),
    (AutomationError("An agent has at most 20 automations."), "LIMIT_REACHED", False,
     "Remove an automation"),
    (AutomationConflict("Automation revision changed; reload before updating"), "CONFLICT",
     False, "revision changed"),
])
async def test_store_refusals_are_classified(monkeypatch, error, error_class, recoverable, says):
    monkeypatch.setattr(automation_repository, "create", AsyncMock(side_effect=error))

    outcome = await _run(dict(ARGS))

    assert not outcome.ok
    assert outcome.error_class == error_class
    assert outcome.recoverable is recoverable
    assert says in outcome.error
    assert failure_reason(outcome.error, outcome.error_class) == outcome.error


@pytest.mark.parametrize(("error", "error_class"), [
    (MetadataPermissionDenied(), "PERMISSION_DENIED"),
    (MetadataOutcomeUnknown(), "OUTCOME_UNKNOWN"),
    (MetadataTimeout(), "TEMPORARILY_UNAVAILABLE"),
    (MetadataReadOnlyMaintenance(), "TEMPORARILY_UNAVAILABLE"),
])
async def test_metadata_failures_end_the_attempt_without_a_blind_retry(
    monkeypatch, error, error_class,
):
    monkeypatch.setattr(automation_repository, "create", AsyncMock(side_effect=error))

    outcome = await _run(dict(ARGS))

    assert not outcome.ok and not outcome.recoverable
    assert outcome.error_class == error_class
    # The store's own message is never shown.
    assert "Metadata" not in outcome.error
    assert failure_reason(outcome.error, outcome.error_class) == outcome.error
