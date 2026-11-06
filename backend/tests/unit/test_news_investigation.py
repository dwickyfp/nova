"""News to Studio handoff, investigation updates and their review."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import business_results
from app.modules.agents.mission_schema import IncidentAnchor, MissionResume
from app.modules.agents.tools import incident as incident_tools
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.intelligence import business_events, investigations
from app.modules.intelligence.business_events import InvestigationUpdate, UpdateReviewRequest
from app.modules.intelligence.investigation_contracts import Claim, UpdateSubmission
from tests.unit._ontology import ANALYST, FINANCE_STEWARD, OUTSIDER, user
from tests.unit.test_business_events import Hub, story
from tests.unit.test_studio_missions import USER as MISSION_USER
from tests.unit.test_studio_missions import create
from tests.unit.test_studio_missions import mission_io as mission_io

#: A second analyst in the same role, who may review the first one's findings.
PEER = user("paula", "finance_analyst")


def headline_story(story_id="story_rev", **extra):
    value = story(story_id, "net_revenue", **extra)
    value.narrative = SimpleNamespace(headline="Revenue fell 34.6% in Enterprise")
    value.metric_label = "Net revenue"
    return value


@pytest.fixture
def hub(monkeypatch):
    hub = Hub(monkeypatch)
    monkeypatch.setattr(business_events, "business_event_service", hub.service)
    monkeypatch.setattr(investigations, "business_event_service", hub.service)
    return hub


@pytest.fixture
def studio(monkeypatch, hub):
    """Newsroom proof, thread store and Mission service for the handoff."""
    from app.modules.agents.mission import mission_service
    from app.modules.assistant.repository import assistant_repository
    from app.modules.assistant.state import thread_store
    from app.modules.intelligence.newsroom import newsroom_service

    stories = {"story_rev": headline_story()}

    async def readable(story_id, reader):
        if reader["username"] in hub.readable.get(story_id, set()):
            return stories.get(story_id)
        return None

    threads: dict[str, dict] = {}

    async def create_thread(*, user_name, title, agent_id, thread_id):
        return threads.setdefault(
            thread_id, {"thread_id": thread_id, "title": title, "agent_id": agent_id}
        )

    missions: dict[str, SimpleNamespace] = {}

    async def create_mission(thread_id, body, caller):
        return missions.setdefault(
            body.operation_id,
            SimpleNamespace(mission_id=f"mission-{len(missions) + 1}", body=body),
        )

    anchors = []

    async def anchor(mission_id, value, caller):
        anchors.append((mission_id, value))
        return SimpleNamespace(mission_id=mission_id)

    monkeypatch.setattr(newsroom_service, "readable", readable)
    hub.service.read_story = readable
    monkeypatch.setattr(assistant_repository, "create_thread", create_thread)
    monkeypatch.setattr(thread_store, "register", lambda **_: None)
    monkeypatch.setattr(mission_service, "create", create_mission)
    monkeypatch.setattr(mission_service, "record_incident_anchor", anchor)
    return SimpleNamespace(stories=stories, threads=threads, missions=missions, anchors=anchors)


# --- handoff -------------------------------------------------------------------------------


async def test_investigating_twice_opens_the_same_thread_and_mission(hub, studio):
    hub.share("story_rev", ANALYST)
    first = await investigations.investigate("story_rev", ANALYST)
    again = await investigations.investigate("story_rev", ANALYST)
    assert first == again
    assert first["handoff_status"] == "READY" and first["agent_id"] == "__smart__"
    assert len(studio.threads) == 1 and len(studio.missions) == 1
    (mission,) = studio.missions.values()
    assert "Revenue fell" not in mission.body.objective  # references, not the headline
    assert mission.body.work_intent.value == "INVESTIGATE"
    mission_id, anchor = studio.anchors[0]
    assert anchor.incident_id == first["incident_id"] and anchor.story_id == "story_rev"
    (incident,) = await hub.incidents()
    assert incident.status == "INVESTIGATING"
    assert len(incident.transitions) == 1  # the second click did not move it again


async def test_each_reader_gets_their_own_mission(hub, studio):
    hub.share("story_rev", ANALYST, FINANCE_STEWARD)
    mine = await investigations.investigate("story_rev", ANALYST)
    theirs = await investigations.investigate("story_rev", FINANCE_STEWARD)
    assert mine["thread_id"] != theirs["thread_id"]
    assert mine["incident_id"] == theirs["incident_id"]


async def test_a_reader_without_the_story_gets_a_generic_not_found(hub, studio):
    with pytest.raises(HTTPException) as hidden:
        await investigations.investigate("story_rev", OUTSIDER)
    assert hidden.value.status_code == 404
    assert hidden.value.detail == business_events.UNAVAILABLE
    assert hub.audits[-1]["status"] == "DENIED"
    assert studio.threads == {} and studio.missions == {}


async def test_the_story_incident_is_read_through_the_story_proof(hub, studio):
    hub.share("story_rev", ANALYST)
    assert await hub.service.incident_for_story("story_rev", ANALYST) is None
    await investigations.investigate("story_rev", ANALYST)
    incident = await hub.service.incident_for_story("story_rev", ANALYST)
    assert incident["status"] == "INVESTIGATING"
    with pytest.raises(HTTPException):
        await hub.service.incident_for_story("story_rev", OUTSIDER)


# --- investigation updates -------------------------------------------------------------------


def claim(*evidence, status="association"):
    return Claim(
        statement="Refunds rose in the same accounts", causal_status=status,
        supporting_evidence_ids=list(evidence),
    )


async def opened_incident(hub):
    hub.share("story_rev", ANALYST, PEER, FINANCE_STEWARD)
    await hub.record(story("story_rev", "net_revenue"))
    (incident,) = await hub.incidents()
    return incident


async def test_an_update_cites_only_verified_evidence_of_the_run(hub):
    incident = await opened_incident(hub)
    with pytest.raises(HTTPException) as fake:
        await hub.service.submit_update(
            incident.id, UpdateSubmission(summary="Refund driven", claims=[claim("ev_fake")]),
            ANALYST, evidence={"ev_1": "a" * 64}, operation_id="op-0000001",
        )
    assert fake.value.status_code == 422 and "ev_fake" in fake.value.detail
    assert hub.audits[-1]["status"] == "DENIED"
    update = await hub.service.submit_update(
        incident.id, UpdateSubmission(summary="Refund driven", claims=[claim("ev_1")]),
        ANALYST, evidence={"ev_1": "a" * 64, "ev_2": "b" * 64}, operation_id="op-0000001",
    )
    assert update.status == "PENDING_REVIEW"
    assert [item.id for item in update.evidence] == ["ev_1"]
    assert update.author_role == "finance_analyst"


def test_an_agent_cannot_assert_a_supported_effect():
    with pytest.raises(ValueError):
        Claim(statement="Refunds caused it", causal_status="supported_effect",
              supporting_evidence_ids=["ev_1"])


async def test_updates_are_reviewed_by_someone_else_in_the_same_role(hub):
    incident = await opened_incident(hub)
    update = await hub.service.submit_update(
        incident.id, UpdateSubmission(summary="Refund driven", claims=[claim("ev_1")]),
        ANALYST, evidence={"ev_1": "a" * 64}, operation_id="op-0000002",
    )
    seen = await hub.service.get(incident.id, PEER)
    assert seen["updates"][0]["status"] == "PENDING_REVIEW" and seen["updates"][0]["can_review"]
    own = await hub.service.get(incident.id, ANALYST)
    assert own["updates"][0]["can_review"] is False
    # Another role computed different figures under its own policy: it sees nothing.
    assert (await hub.service.get(incident.id, FINANCE_STEWARD))["updates"] == []

    body = UpdateReviewRequest(decision="APPROVE", rationale="Checked", operation_id="rev-0001")
    with pytest.raises(HTTPException) as self_review:
        await hub.service.review_update(incident.id, update.id, body, ANALYST)
    assert self_review.value.status_code == 403
    with pytest.raises(HTTPException) as other_role:
        await hub.service.review_update(incident.id, update.id, body, FINANCE_STEWARD)
    assert other_role.value.status_code == 404
    reviewed = await hub.service.review_update(incident.id, update.id, body, PEER)
    assert reviewed["updates"][0]["status"] == "APPROVED"
    assert reviewed["updates"][0]["review"]["principal"] == "paula"
    replay = await hub.service.review_update(incident.id, update.id, body, PEER)
    assert replay["updates"][0]["status"] == "APPROVED"
    with pytest.raises(HTTPException) as twice:
        await hub.service.review_update(
            incident.id, update.id, body.model_copy(update={"operation_id": "rev-0002"}), PEER
        )
    assert twice.value.status_code == 409


async def test_a_reader_who_cannot_prove_the_author_signals_does_not_see_the_update(hub):
    incident = await opened_incident(hub)
    await hub.service.submit_update(
        incident.id, UpdateSubmission(summary="Refund driven", claims=[claim("ev_1")]),
        ANALYST, evidence={"ev_1": "a" * 64}, operation_id="op-0000003",
    )
    hub.readable["story_rev"].discard(PEER["username"])
    with pytest.raises(HTTPException):
        await hub.service.get(incident.id, PEER)
    updates = await hub.repository.current("updates", InvestigationUpdate)
    with pytest.raises(HTTPException):
        await hub.service.review_update(
            incident.id, updates[0].id,
            UpdateReviewRequest(decision="REJECT", rationale="x", operation_id="rev-0003"), PEER,
        )


# --- agent tools ----------------------------------------------------------------------------


def tool_context(**extra):
    context = LoopContext(user_name="ana", user=ANALYST, run_id="run-1")
    for key, value in extra.items():
        setattr(context, key, value)
    return context


async def test_incident_context_reads_the_mission_incident(hub, monkeypatch):
    from app.common import news_entitlement

    monkeypatch.setattr(news_entitlement, "is_news_enabled", AsyncMock(return_value=True))
    incident = await opened_incident(hub)
    context = tool_context(incident_id=incident.id)
    outcome = await incident_tools.incident_context_tool.run(
        ToolInvocation("t1", "incident_context", {}), context
    )
    assert outcome.ok and outcome.data["id"] == incident.id
    assert "No cause has been established" in outcome.summary
    json.dumps(outcome.data)
    missing = await incident_tools.incident_context_tool.run(
        ToolInvocation("t2", "incident_context", {}), tool_context()
    )
    assert not missing.ok and "No incident is attached" in missing.error
    # Choosing an incident tool without an incident is recoverable, not a failed turn.
    assert missing.recoverable
    monkeypatch.setattr(news_entitlement, "is_news_enabled", AsyncMock(return_value=False))
    denied = await incident_tools.incident_context_tool.run(
        ToolInvocation("t3", "incident_context", {}), context
    )
    assert not denied.ok


async def test_investigation_update_tool_rejects_invented_evidence(hub, monkeypatch):
    from app.common import news_entitlement

    monkeypatch.setattr(news_entitlement, "is_news_enabled", AsyncMock(return_value=True))
    incident = await opened_incident(hub)
    context = tool_context(
        incident_id=incident.id, mission_id="mission-1",
        evidence_tables={"ev_1": {"columns": ["x"], "rows": [[1]]}},
    )
    args = {"summary": "Refund driven", "claims": [{
        "statement": "Refunds rose", "causal_status": "association",
        "supporting_evidence_ids": ["ev_made_up"],
    }]}
    rejected = await incident_tools.investigation_update_tool.run(
        ToolInvocation("u1", "investigation_update", args), context
    )
    assert rejected.error_class == "UNVERIFIED_EVIDENCE" and rejected.recoverable
    assert rejected.repair_context == {"evidence_ids": ["ev_1"]}
    args["claims"][0]["supporting_evidence_ids"] = ["ev_1"]
    posted = await incident_tools.investigation_update_tool.run(
        ToolInvocation("u1", "investigation_update", args), context
    )
    assert posted.ok and posted.data["status"] == "PENDING_REVIEW"
    (update,) = await hub.repository.current("updates", InvestigationUpdate)
    assert update.mission_id == "mission-1"
    invalid = await incident_tools.investigation_update_tool.run(
        ToolInvocation("u2", "investigation_update", {"summary": "x"}), context
    )
    assert not invalid.ok


def test_the_incident_tools_are_registered_for_agents_and_smart():
    from app.modules.agents.harness_worker import SMART_ROOT_TOOLS
    from app.modules.agents.registry import KNOWN_TOOLS, build_registry
    from app.modules.agents.tool_catalog import AGENT_BUNDLEABLE_TOOLS, BUILTIN_TOOLS
    from app.modules.assistant.service import SMART_DRAFT_TOOLS

    for name in ("incident_context", "investigation_update"):
        assert name in KNOWN_TOOLS and name in AGENT_BUNDLEABLE_TOOLS
        assert name in BUILTIN_TOOLS
    # Smart reads the incident itself and drafts findings, since it cannot ask consent.
    assert "incident_context" in SMART_ROOT_TOOLS
    assert "investigation_update" not in SMART_ROOT_TOOLS
    assert "draft_investigation_update" in SMART_DRAFT_TOOLS
    draft_tool = incident_tools.draft_investigation_update_tool
    assert draft_tool.classification == "read_only" and not draft_tool.requires_consent
    registry = build_registry({"default_tools": ["incident_context", "investigation_update"]})
    assert {"incident_context", "investigation_update"} <= set(registry.names())
    assert incident_tools.investigation_update_tool.classification == "session_change"
    for tool in (incident_tools.incident_context_tool, incident_tools.investigation_update_tool):
        assert tool.requires_consent and tool.preview(None)


# --- Mission anchor --------------------------------------------------------------------------


ANCHOR = IncidentAnchor(
    incident_id="inc_1", story_id="story_rev", source_revision=1, source_digest="a" * 64,
)


async def test_a_mission_investigates_one_incident(mission_io):
    _, service = mission_io
    mission = await create(service)
    anchored = await service.record_incident_anchor(mission.mission_id, ANCHOR, MISSION_USER)
    assert anchored.incident_anchor == ANCHOR
    again = await service.record_incident_anchor(mission.mission_id, ANCHOR, MISSION_USER)
    assert again.revision == anchored.revision
    with pytest.raises(HTTPException) as other:
        await service.record_incident_anchor(
            mission.mission_id, ANCHOR.model_copy(update={"incident_id": "inc_2"}), MISSION_USER
        )
    assert other.value.status_code == 409


async def test_resuming_a_news_mission_proves_the_story_again(mission_io, monkeypatch):
    from app.modules.intelligence.actions import action_service
    from app.modules.intelligence.newsroom import newsroom_service

    _, service = mission_io
    mission = await create(service)
    mission = await service.record_incident_anchor(mission.mission_id, ANCHOR, MISSION_USER)
    monkeypatch.setattr(action_service, "revalidate", AsyncMock())
    monkeypatch.setattr(newsroom_service, "readable", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as revoked:
        await service.resume(
            mission.mission_id,
            MissionResume(expected_revision=mission.revision, operation_id="resume-news"),
            MISSION_USER,
        )
    assert revoked.value.status_code == 404


def test_the_turn_context_carries_the_incident_by_reference():
    context = LoopContext(user_name="ana", user=ANALYST, mission_id="m")
    business_results.bind_business_hooks(context, {"incident_anchor": ANCHOR.model_dump()})
    assert context.incident_id == "inc_1"
    later = LoopContext(user_name="ana", user=ANALYST, mission_id="m")
    business_results.bind_business_hooks(later, SimpleNamespace(
        business_anchors=[], incident_anchor=ANCHOR,
    ))
    assert later.incident_id == "inc_1"


def test_incident_routes_for_review_and_handoff(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.common.news_entitlement import _news_user
    from app.modules.intelligence import business_events_router

    service = business_events_router.business_event_service
    monkeypatch.setattr(service, "review_update", AsyncMock(return_value={"id": "inc_1"}))
    monkeypatch.setattr(service, "incident_for_story", AsyncMock(return_value=None))
    monkeypatch.setattr(investigations, "investigate", AsyncMock(return_value={"mission_id": "m"}))
    app = FastAPI()
    app.include_router(business_events_router.router, prefix="/intelligence")
    app.dependency_overrides[_news_user] = lambda: ANALYST
    client = TestClient(app)
    assert client.get("/intelligence/stories/s/incident").json() == {"incident": None}
    assert client.post("/intelligence/stories/s/investigate").json() == {"mission_id": "m"}
    reviewed = client.post(
        "/intelligence/incidents/inc_1/updates/upd_1/review",
        json={"decision": "APPROVE", "rationale": "ok", "operation_id": "rev-00001"},
    )
    assert reviewed.json() == {"id": "inc_1"}


async def test_a_concurrent_incident_move_does_not_fail_the_handoff(hub, studio, monkeypatch):
    hub.share("story_rev", ANALYST)
    monkeypatch.setattr(hub.service, "transition", AsyncMock(side_effect=HTTPException(409)))
    assert (await investigations.investigate("story_rev", ANALYST))["handoff_status"] == "READY"
    monkeypatch.setattr(hub.service, "transition", AsyncMock(side_effect=HTTPException(500)))
    hub.readable["story_rev"].add(FINANCE_STEWARD["username"])
    with pytest.raises(HTTPException):
        await investigations.investigate("story_rev", FINANCE_STEWARD)


async def test_smart_drafts_a_finding_and_the_user_posts_it(hub, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.common import news_entitlement
    from app.common.news_entitlement import _news_user
    from app.modules.intelligence import business_events_router, update_drafts

    monkeypatch.setattr(news_entitlement, "is_news_enabled", AsyncMock(return_value=True))
    incident = await opened_incident(hub)
    context = tool_context(
        incident_id=incident.id, mission_id="mission-1",
        evidence_tables={"ev_1": {"columns": ["x"], "rows": [[1]]}},
    )
    args = {"summary": "Refund driven", "claims": [{
        "statement": "Refunds rose", "causal_status": "association",
        "supporting_evidence_ids": ["ev_made_up"],
    }]}
    tool = incident_tools.draft_investigation_update_tool
    rejected = await tool.run(ToolInvocation("d1", "draft_investigation_update", args), context)
    assert rejected.error_class == "UNVERIFIED_EVIDENCE" and not context.smart_drafts
    args["claims"][0]["supporting_evidence_ids"] = ["ev_1"]
    drafted = await tool.run(ToolInvocation("d1", "draft_investigation_update", args), context)
    assert drafted.ok and drafted.data == {"incident_id": incident.id, "posted": False}
    # Drafting writes nothing.
    assert await hub.repository.current("updates", InvestigationUpdate) == []
    (draft,) = context.smart_drafts
    assert draft["draft_kind"] == "investigation_update" and draft["detail"] == "Refund driven"
    token = draft["payload"]["draft"]

    reader = {"user": ANALYST}
    app = FastAPI()
    app.include_router(business_events_router.router, prefix="/intelligence")
    app.dependency_overrides[_news_user] = lambda: reader["user"]
    monkeypatch.setattr(business_events_router, "business_event_service", hub.service)
    client = TestClient(app)
    path = f"/intelligence/incidents/{incident.id}/updates"
    encoded, mac = token.rsplit(".", 1)
    tampered = encoded[:-4] + ("AAAA" if not encoded.endswith("AAAA") else "BBBB")
    for bad in (tampered + "." + mac, token + "0", "not-a-draft"):
        assert client.post(path, json={"draft": bad}).status_code == 403
    assert client.post("/intelligence/incidents/inc_other/updates",
                       json={"draft": token}).status_code == 403
    reader["user"] = OUTSIDER
    assert client.post(path, json={"draft": token}).status_code == 403
    reader["user"] = ANALYST
    monkeypatch.setattr(update_drafts, "DRAFT_TTL", -update_drafts.DRAFT_TTL)
    stale = tool_context(incident_id=incident.id, evidence_tables=context.evidence_tables)
    assert (await tool.run(ToolInvocation("d2", "draft_investigation_update", args), stale)).ok
    assert client.post(path, json={"draft": stale.smart_drafts[0]["payload"]["draft"]}
                       ).status_code == 410
    created = client.post(path, json={"draft": token})
    assert created.status_code == 201 and created.json()["status"] == "PENDING_REVIEW"
    posted = await hub.repository.current("updates", InvestigationUpdate)
    (update,) = posted
    assert update.status == "PENDING_REVIEW" and update.mission_id == "mission-1"
    assert [item.id for item in update.evidence] == ["ev_1"]
    refusals = [item for item in hub.audits
                if item["action"] == "POST_INVESTIGATION_UPDATE" and item["status"] == "DENIED"]
    assert len(refusals) == 6
