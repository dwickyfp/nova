"""Hand a News story to Studio: one Mission per reader, story and proof.

The handoff carries references, never the headline as trusted context:

1. the story is proven again for the caller now;
2. its signal and incident are recorded (idempotent);
3. a Studio thread with the agent the reader picked and an investigating Mission
   are created, or found again, under ids derived from the story, its proof
   digest, the caller's role and that agent;
4. the Mission is anchored to the incident by reference, and the incident
   moves to INVESTIGATING if nobody had started.

Clicking twice, or retrying after a failure, opens the same thread and
Mission. A Mission resumed later proves the story again for the caller.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.business_events import (
    UNAVAILABLE,
    BusinessIncident,
    TransitionRequest,
    business_event_service,
)
from app.modules.intelligence.contracts import fingerprint
from app.modules.ontology.contracts import Actor

INVESTIGATE_PROMPT = (
    "Investigate this News incident: confirm what changed and for whom, test what could "
    "explain it, and say what to check next."
)


async def _usable_agent(agent_id: str, user: dict) -> dict | None:
    """The agent the caller picked, when they own it or it is shared with their role
    and its access checks pass now."""
    from app.modules.agents.access import has_verified_access
    from app.modules.agents.repository import agent_repository
    from app.modules.assistant.security import session_security

    role = session_security(user).active_role
    agent = await agent_repository.get_agent(agent_id, owner_name=user["username"])
    if agent is None:
        agent = await agent_repository.get_shared_agent(agent_id, role_name=role)
    if agent is None or not await has_verified_access(agent, role_name=role, user=user):
        return None
    return agent


async def investigate(story_id: str, agent_id: str, user: dict) -> dict:
    from app.modules.agents.mission import mission_service
    from app.modules.agents.mission_schema import IncidentAnchor, MissionCreate, WorkIntent
    from app.modules.assistant.repository import assistant_repository
    from app.modules.assistant.state import thread_store
    from app.modules.intelligence.newsroom import newsroom_service

    events = business_event_service
    story = await newsroom_service.readable(story_id, user)
    if story is None:
        await events._audit(user, "INVESTIGATE_STORY", story_id, "DENIED", "DENY")
        raise HTTPException(status_code=404, detail=UNAVAILABLE)
    agent = await _usable_agent(agent_id, user)
    if agent is None:
        await events._audit(user, "INVESTIGATE_STORY", story_id, "DENIED", "DENY")
        raise HTTPException(status_code=404, detail="Agent not found")
    actor = Actor.from_user(user)
    (signal,) = await events.record_stories([story], actor)
    if not signal.incident_id:
        raise HTTPException(status_code=409, detail="The story is not linked to an incident yet")

    key = fingerprint([
        story.id, signal.source.digest, actor.principal, actor.active_role, agent["agent_id"],
    ])
    thread_id = str(uuid5(NAMESPACE_URL, f"nova:news-handoff:{key}"))
    thread = await assistant_repository.create_thread(
        user_name=actor.principal,
        title=story.narrative.headline[:200],
        agent_id=agent["agent_id"],
        thread_id=thread_id,
    )
    thread_store.register(
        thread_id=thread["thread_id"],
        user_name=actor.principal,
        title=thread["title"],
        workspace_file_id=thread.get("workspace_file_id"),
    )
    mission = await mission_service.create(
        thread_id,
        MissionCreate(
            objective=f"Investigate News incident {signal.incident_id}: {story.metric_label}",
            work_intent=WorkIntent.INVESTIGATE,
            operation_id=f"news-handoff:{key[:48]}",
        ),
        user,
    )
    mission = await mission_service.record_incident_anchor(
        mission.mission_id,
        IncidentAnchor(
            incident_id=signal.incident_id,
            story_id=story.id,
            source_revision=story.revision,
            source_digest=signal.source.digest,
            ontology_release_id=signal.concept.release_id if signal.concept else None,
        ),
        user,
    )
    incident = await events.repository.latest(
        "incidents", signal.incident_id, BusinessIncident,
        actor=SecurityContext.from_session(user),
    )
    if incident is not None and incident.status in {"OPEN", "TRIAGED"}:
        try:
            await events.transition(
                incident.id,
                TransitionRequest(
                    to="INVESTIGATING",
                    reason="Investigation opened in Studio from News",
                    expected_revision=incident.revision,
                    operation_id=f"handoff:{key[:48]}",
                ),
                user,
            )
        except HTTPException as exc:
            if exc.status_code != 409:  # someone moved it at the same moment
                raise
    await events._audit(user, "INVESTIGATE_STORY", story.id, "SUCCESS", "ALLOW")
    return {
        "handoff_status": "READY",
        "incident_id": signal.incident_id,
        "mission_id": mission.mission_id,
        "thread_id": thread_id,
        "agent_id": agent["agent_id"],
        "source_revision": story.revision,
        "ontology_release_id": signal.concept.release_id if signal.concept else None,
        "prompt": INVESTIGATE_PROMPT,
    }
