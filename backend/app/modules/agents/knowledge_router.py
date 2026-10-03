"""Review operations extending private agent-memory APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user
from app.modules.agents.knowledge import KnowledgeReview
from app.modules.agents.memory import governed_memories, memory_repository
from app.modules.assistant.security import session_security
from app.modules.intelligence.responses import IntelligenceResponse

router = APIRouter(default_response_class=IntelligenceResponse)
CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.post("/{agent_id}/knowledge/consolidate")
async def consolidate_knowledge(agent_id: str, user: CurrentUser):
    from app.modules.agents.learning_work import enqueue_pending_learning
    from app.modules.agents.router import _require_agent

    await _require_agent(agent_id, user)
    return {"queued": await enqueue_pending_learning(user=user, agent_id=agent_id)}


@router.get("/{agent_id}/memories/{memory_id}/revisions")
async def memory_revisions(agent_id: str, memory_id: str, user: CurrentUser):
    from app.modules.agents.router import _require_agent

    await _require_agent(agent_id, user)
    revisions = await memory_repository.revisions(
        memory_id,
        user_name=user["username"],
        agent_id=agent_id,
        role_name=session_security(user).active_role,
    )
    visible = await governed_memories(
        [{"knowledge": revision.model_dump(mode="json")} for revision in revisions], user
    )
    if not visible:
        raise HTTPException(status_code=404, detail="Memory evidence unavailable")
    return {"items": [row["knowledge"] for row in visible]}


@router.get("/{agent_id}/memories/{memory_id}/evidence")
async def memory_evidence(agent_id: str, memory_id: str, user: CurrentUser):
    import json

    from app.core.database import db

    revisions = await memory_revisions(agent_id, memory_id, user)
    allowed = {ident for row in revisions["items"] for ident in row["evidence_ids"]}
    if not allowed:
        return {"items": []}
    rows = await db.execute_system(
        "SELECT evidence_id,payload FROM NOVA_SYSTEM.CONFIG_AGENT_MEMORY_EVIDENCE "
        "WHERE memory_id=%s AND user_name=%s AND agent_id=%s AND role_name=%s "
        "AND evidence_id IN ("
        + ",".join("%s" for _ in allowed)
        + ") ORDER BY created_at DESC LIMIT 100",
        [
            memory_id,
            user["username"],
            agent_id,
            session_security(user).active_role,
            *sorted(allowed),
        ],
    )
    return {
        "items": [
            {"id": row[0], **(json.loads(row[1]) if isinstance(row[1], str) else row[1])}
            for row in rows["rows"]
        ]
    }


@router.post("/{agent_id}/memories/{memory_id}/review")
async def review_memory(agent_id: str, memory_id: str, body: KnowledgeReview, user: CurrentUser):
    from app.modules.agents.router import _require_agent

    await _require_agent(agent_id, user)
    return await memory_repository.review(memory_id, agent_id=agent_id, user=user, request=body)


@router.post("/{agent_id}/memories/{memory_id}/context")
async def project_memory_context(agent_id: str, memory_id: str, user: CurrentUser):
    from app.modules.intelligence.context_graph import semantic_node_id
    from app.modules.intelligence.contracts import (
        ContextEdge,
        ContextNode,
        KnowledgeState,
        Scope,
        fingerprint,
    )
    from app.modules.intelligence.engine import CycleBudget, intelligence_service

    revisions = await memory_revisions(agent_id, memory_id, user)
    latest = max(revisions["items"], key=lambda row: row["revision"])
    scope = Scope.from_user(user)
    record = ContextNode(
        id=fingerprint([scope.model_dump(exclude={"session_id"}), memory_id, latest["revision"]]),
        scope=scope,
        kind="rule",
        name=latest["fact"][:256],
        reference_id=memory_id,
        reference_revision=latest["revision"],
        reference_agent_id=agent_id,
        semantic=latest.get("semantic"),
        state=latest["state"],
        authority=latest["authority"],
    )
    budget = CycleBudget()
    await intelligence_service.authorize_record(record, user, budget)
    prior = await intelligence_service.repository.get("nodes", record.id, scope, ContextNode)
    saved = prior or await intelligence_service.repository.save("nodes", record)
    if saved.semantic:
        row = await intelligence_service.authorize_semantic(saved.semantic, user, budget=budget)
        name = row["definition"]["name"]
        semantic_node = ContextNode(
            id=semantic_node_id(scope, saved.semantic, "semantic_view", name),
            scope=scope,
            kind="semantic_view",
            name=name,
            reference_id=saved.semantic.view_id,
            semantic=saved.semantic,
            state=KnowledgeState.VERIFIED
            if row["status"] in {"ACTIVE", "SUPERSEDED"}
            else KnowledgeState.HYPOTHESIS,
            authority="published_semantic_definition",
        )
        existing = await intelligence_service.repository.get(
            "nodes", semantic_node.id, scope, ContextNode
        )
        if existing is None:
            await intelligence_service.repository.save("nodes", semantic_node)
        edge = ContextEdge(
            id=fingerprint([saved.id, semantic_node.id, "applies_to"]),
            scope=scope,
            source=saved.id,
            target=semantic_node.id,
            relationship="applies_to",
            state=saved.state,
        )
        await intelligence_service.authorize_record(edge, user, budget)
        existing_edge = await intelligence_service.repository.get(
            "edges", edge.id, scope, ContextEdge
        )
        if existing_edge is None:
            await intelligence_service.repository.save("edges", edge)
    await intelligence_service._audit("PROJECT_KNOWLEDGE", saved, user)
    return {"root_id": saved.id, "revision": latest["revision"]}
