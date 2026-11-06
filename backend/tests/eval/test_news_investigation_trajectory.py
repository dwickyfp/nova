"""A Mission opened from News reads its incident and posts only verified findings.

Scripted provider frames drive the shared loop: the incident tools stay behind
consent, the incident comes from the Mission by reference, and a finding that
cites evidence the run never produced is refused without being stored.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools.incident import incident_context_tool, investigation_update_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence import business_events
from app.modules.intelligence.business_events import InvestigationUpdate
from tests.benchmark.harness import ScriptedProvider, allow, deny, text_frame, tool_call_frame
from tests.unit._ontology import ANALYST
from tests.unit.test_business_events import Hub, story


@pytest.fixture
async def incident(monkeypatch):
    from app.common import news_entitlement

    monkeypatch.setattr(news_entitlement, "is_news_enabled", AsyncMock(return_value=True))
    hub = Hub(monkeypatch)
    monkeypatch.setattr(business_events, "business_event_service", hub.service)
    hub.share("story_rev", ANALYST)
    await hub.record(story("story_rev", "net_revenue"))
    (record,) = await hub.incidents()
    hub.incident_id = record.id
    return hub


async def run(script, *, incident_id, consent=allow):
    registry = ToolRegistry()
    registry.register(incident_context_tool)
    registry.register(investigation_update_tool)
    context = LoopContext(
        user_name="ana", user=ANALYST.copy(), run_id="run-1", role=ANALYST["active_role"]
    )
    context.incident_id = incident_id
    frames = [
        frame
        async for frame in AssistantLoop(
            provider=ScriptedProvider(script), registry=registry, system_prompt="test"
        ).run(
            thread=AssistantThread(thread_id="news", user_name="ana", title="Eval"),
            user_content="Investigate this News incident",
            context=context,
            resolve_consent=consent,
        )
    ]
    return "".join(frames).replace(" ", ""), context


async def test_the_mission_incident_is_read_by_reference(incident):
    joined, _ = await run(
        [
            # The harness swaps empty arguments for SQL, so name the Mission's incident.
            tool_call_frame(
                "i1", name="incident_context", arguments={"incident_id": incident.incident_id}
            ),
            text_frame("One related signal so far; no cause has been established."),
        ],
        incident_id=incident.incident_id,
    )
    assert '"finish_reason":"stop"' in joined
    assert "Nocausehasbeenestablished" in joined
    assert incident.audits[-1]["action"] == "READ_INCIDENT"


async def test_incident_tools_need_consent(incident):
    joined, _ = await run(
        [
            # The harness swaps empty arguments for SQL, so name the Mission's incident.
            tool_call_frame(
                "i1", name="incident_context", arguments={"incident_id": incident.incident_id}
            ),
            text_frame("I could not open the incident without your permission."),
        ],
        incident_id=incident.incident_id,
        consent=deny,
    )
    assert not [item for item in incident.audits if item["action"] == "READ_INCIDENT"]
    assert '"finish_reason":"denied"' in joined


async def test_a_finding_with_invented_evidence_is_refused_and_not_stored(incident):
    joined, _ = await run(
        [
            tool_call_frame("u1", name="investigation_update", arguments={
                "summary": "Refunds drove the decline",
                "claims": [{
                    "statement": "Refunds rose in the same accounts",
                    "causal_status": "association",
                    "supporting_evidence_ids": ["ev_invented"],
                }],
            }),
            text_frame("I need a verified result before I can post a finding."),
        ],
        incident_id=incident.incident_id,
    )
    assert await incident.repository.current("updates", InvestigationUpdate) == []
    # The refused finding is not reported as done.
    assert '"finish_reason":"required_capability_incomplete"' in joined
