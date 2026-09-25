"""Agent Studio API router — agent CRUD, skills, and Nova Studio runs.

Endpoints under ``/api/v1/agents``:
  GET    /agents                                  → list the caller's agents
  POST   /agents                                  → create an agent
  GET    /agents/{agent_id}                       → agent detail
  PUT    /agents/{agent_id}                       → update an agent
  DELETE /agents/{agent_id}                       → delete an agent
  /agents/semantic-models/*                       → hidden legacy compatibility
  GET    /agents/skills                           → list built-in + user skills
  POST   /agents/skills                           → create a SKILL.md-compatible skill
  DELETE /agents/skills/{skill_id}                → delete a user skill

Every route requires ``get_current_user`` and is scoped to the caller. An
unknown or foreign id answers **404**, never 403, so existence does not leak.

Agents bind published Semantic Views. The legacy model routes cannot mutate
the retired catalog; rule proposal aliases under /semantic-views remain active.
"""

# ruff: noqa: B008 — `Depends(...)` in a default is FastAPI's dependency
# injection idiom used by every router in this codebase.

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.common.audit import write_audit_log
from app.core.deps import get_current_user
from app.modules.agents.access import has_verified_access
from app.modules.agents.auto_planner import (
    AgentDiscoveryUnavailable,
    authorized_candidates,
    rank_candidates,
    semantic_matches,
)
from app.modules.agents.capabilities import CapabilityManifest, capability_repository
from app.modules.agents.harness_repository import (
    TERMINAL,
    AutoAdmissionUnavailable,
    harness_repository,
)
from app.modules.agents.instructions import (
    InstructionCompilationError,
    compile_agent_instructions,
)
from app.modules.agents.memory import (
    memory_prompt,
    memory_repository,
    remember_user_message,
    select_memories,
)
from app.modules.agents.repository import AgentMetadataUnavailable, agent_repository
from app.modules.agents.rule_proposals import candidate_definition, rule_proposal_repository
from app.modules.agents.run_journal import run_journal
from app.modules.agents.schemas import (
    AgentCreateRequest,
    AgentListResponse,
    AgentUpdateRequest,
    AgentView,
    RuleProposalCreateRequest,
    RuleProposalListResponse,
    RuleProposalPreviewResponse,
    RuleProposalView,
    SemanticLintResponse,
    SemanticModelCreateRequest,
    SemanticModelListResponse,
    SemanticModelView,
    SemanticPreviewRequest,
    SemanticPreviewResponse,
    SemanticQualityLabResponse,
    SemanticValidateRequest,
    SemanticValidateResponse,
    SkillCreateRequest,
    SkillListResponse,
    SkillView,
    VerifiedQueryCreateRequest,
    VerifiedQueryListResponse,
    VerifiedQueryView,
)
from app.modules.agents.semantic.access import bound_view_ids
from app.modules.agents.service import agent_service
from app.modules.agents.skill_author import SKILL_AUTHOR_ID, skill_author_config
from app.modules.agents.skill_catalog import is_builtin_skill_id, merge_skill_rows
from app.modules.assistant import events
from app.modules.assistant.attachments import attachment_prompt
from app.modules.assistant.consent import consent_broker
from app.modules.assistant.context import ContextManager
from app.modules.assistant.provider import assistant_provider
from app.modules.assistant.repository import (
    AssistantThreadListUnavailable,
    assistant_repository,
)
from app.modules.assistant.schemas import (
    AgentMessageRequest,
    AttachmentView,
    ConsentDecisionRequest,
    ConsentDecisionResponse,
    MessageFeedbackRequest,
    MessageView,
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadListResponse,
    ThreadUpdateRequest,
    ThreadView,
)
from app.modules.assistant.security import observation_context, session_security
from app.modules.assistant.service import (
    DEFAULT_MAX_ITERATIONS,
    AssistantLoop,
    LoopContext,
)
from app.modules.assistant.state import AssistantMessage, thread_store
from app.modules.assistant.tools import ToolInvocation

logger = logging.getLogger(__name__)
_active_run_tasks: set[asyncio.Task[None]] = set()

router = APIRouter()
AUTO_AGENT_ID = "__auto__"


class AutoMessageRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=123)
    content: str = Field(min_length=1, max_length=4000)
    correlation_id: str | None = Field(default=None, max_length=64)
    reply_to: str | None = Field(default=None, max_length=64)


def _auto_agent(owner_name: str) -> dict:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return {
        "agent_id": AUTO_AGENT_ID,
        "owner_name": owner_name,
        "name": "Auto",
        "description": "Coordinate specialists for a question.",
        "created_at": now,
        "updated_at": now,
    }


#: The v1 tool surface an agent may bundle. A name outside this set is rejected
#: before storage so a typo cannot silently grant nothing.
KNOWN_TOOLS = {
    "load_skill",
    "query_execute",
    "semantic_query",
    "semantic_search",
    "ai_search",
    "semantic_view_query",
    "feature_lookup",
    "data_to_chart",
    "diagnose_change",
    "ml_execute",
}


def _unknown_tools(tools: list) -> list[str]:
    """Tool names outside builtin, custom, or selected MCP registry entries.

    Custom tools are selected by name with a ``custom:`` prefix; their existence
    is resolved at run time (a deleted tool is skipped, not rejected here).
    MCP tools use ``mcp:<tool_id>`` and are also checked at run time.
    """
    return [
        t
        for t in tools
        if t not in KNOWN_TOOLS
        and not (isinstance(t, str) and (t.startswith("custom:") or t.startswith("mcp:")))
    ]


async def _unavailable_mcp_tools(tools: list[str]) -> list[str]:
    selected = [value for value in tools if value.startswith("mcp:")]
    if not selected:
        return []
    available_tools = await agent_repository.list_tools(owner_name="__nova__")
    servers = {
        server["server_id"]: server
        for server in await agent_repository.list_mcp_servers(owner_name="__nova__")
    }
    available_ids = {
        tool["tool_id"]
        for tool in available_tools
        if tool.get("is_enabled")
        and str(tool.get("source") or "").startswith("mcp:")
        and (server := servers.get(str(tool["source"]).split(":", 1)[1]))
        and server.get("is_active")
        and server.get("transport") == "http"
    }
    return [value for value in selected if value.split(":", 1)[1] not in available_ids]


def _agent_view(row: dict) -> AgentView:
    # Harness strategy is platform-owned. Older rows may contain an explicit
    # mode, but Studio now exposes one stable automatic contract.
    return AgentView(**{**row, "harness_mode": "auto"})


def _semantic_view(row: dict) -> SemanticModelView:
    return SemanticModelView(**row)


def _skill_view(row: dict) -> SkillView:
    return SkillView(**row)


async def _require_agent(agent_id: str, user: dict | str) -> dict:
    user_name = user if isinstance(user, str) else user["username"]
    if agent_id == AUTO_AGENT_ID and isinstance(user, dict):
        return _auto_agent(user_name)
    if agent_id == SKILL_AUTHOR_ID:
        return skill_author_config(user_name)
    try:
        agent = await agent_repository.get_agent(agent_id, owner_name=user_name)
        if agent is None and isinstance(user, dict):
            role = session_security(user).active_role
            agent = await agent_repository.get_shared_agent(agent_id, role_name=role)
        if agent is not None and isinstance(user, dict):
            role = session_security(user).active_role
            if not await has_verified_access(agent, role_name=role, user=user):
                raise HTTPException(status_code=404, detail="Agent not found")
    except AgentMetadataUnavailable:
        raise HTTPException(
            status_code=503, detail="Agent metadata is temporarily unavailable"
        ) from None
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def _resolve_database(agent: dict, user: dict) -> str | None:
    """The default database a turn's SQL runs against.

    An explicit ``database_name`` wins. Otherwise it is derived from the first
    bound published View, so the user never types a database name. ``None`` means
    the caller's session default applies.
    """
    explicit = agent.get("database_name")
    if explicit:
        return explicit
    from app.modules.intelligence.semantic_views import semantic_view_service

    for view_id in bound_view_ids(agent):
        view = await semantic_view_service.get_active_for_agent(
            view_id, user, agent_id=agent.get("agent_id")
        )
        if view:
            return view.get("database_name")
    return None


async def _normalize_view_binding(
    fields: dict, *, provided: set[str], user: dict
) -> None:
    """Accept old field names at the edge, then persist only the View binding."""
    if "semantic_view_ids" in provided:
        ids = fields.get("semantic_view_ids") or []
    elif "semantic_model_ids" in provided:
        ids = fields.get("semantic_model_ids") or []
    elif "semantic_model_id" in provided:
        ids = [fields["semantic_model_id"]] if fields.get("semantic_model_id") else []
    else:
        ids = None
    fields.pop("semantic_model_id", None)
    fields.pop("semantic_model_ids", None)
    if ids is None:
        fields.pop("semantic_view_ids", None)
        return
    if len(ids) > 16 or any(not isinstance(item, str) or not item.strip() for item in ids):
        raise HTTPException(status_code=422, detail="Select at most 16 Semantic Views")
    unique = list(dict.fromkeys(item.strip() for item in ids))
    from app.modules.intelligence.semantic_views import semantic_view_service

    for view_id in unique:
        if await semantic_view_service.get_active_for_agent(view_id, user) is None:
            raise HTTPException(status_code=422, detail="A selected Semantic View is unavailable")
    fields["semantic_view_ids"] = unique


async def _require_agent_thread(thread_id: str, agent_id: str, user_name: str) -> dict:
    """Fetch a thread owned by ``user_name`` and bound to ``agent_id``, or 404.

    A thread belonging to another agent is not reachable through this agent's
    path, so a run cannot be pointed at a different agent's conversation.
    """
    thread = await assistant_repository.get_thread(thread_id, user_name=user_name)
    if thread is None or thread.get("agent_id") != agent_id:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


def _thread_view(row: dict) -> ThreadView:
    return ThreadView(
        thread_id=row["thread_id"],
        title=row["title"],
        workspace_file_id=row.get("workspace_file_id"),
        agent_id=row.get("agent_id"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=row.get("message_count", 0),
    )


def _message_view(row: dict) -> MessageView:
    return MessageView(
        message_id=row["message_id"],
        role=row["role"],
        content=row.get("content") or "",
        created_at=row["created_at"],
        steps=row.get("steps") or [],
        prompt_tokens=row.get("prompt_tokens"),
        completion_tokens=row.get("completion_tokens"),
        total_tokens=row.get("total_tokens"),
        model_name=row.get("model_name"),
        feedback=row.get("feedback"),
        attachments=[
            AttachmentView(
                name=item["name"],
                size_bytes=item["size_bytes"],
                media_type=item.get("media_type", "text/plain"),
            )
            for item in row.get("attachments") or []
            if isinstance(item, dict) and "name" in item and "size_bytes" in item
        ],
    )


#: Longest a conversation title may be. Long enough for a real question, short
#: enough to scan in a sidebar without wrapping.
_TITLE_MAX = 80

#: Title generation is a chat-completions call against the agent's provider; the
#: prompt below is combined with the user's first question as the sole message.
CREATE_THREAD_TITLE_PROMPT = """You are a chat title generation expert.

Critical rules:
- Generate a concise title based on the first user message
- Title must be under 80 characters (absolutely no more than 80 characters)
- Summarize only the core content clearly
- Do not use quotes, colons, or special characters
- Use the same language as the user's message"""


def _thread_title(question: str) -> str:
    """A bounded one-line title, used when no model call is available.

    The user's own words, collapsed to one line and bounded. This is the honest
    fallback: the question is already the most accurate label the thread has.
    """
    collapsed = " ".join(question.split())
    if len(collapsed) <= _TITLE_MAX:
        return collapsed
    return collapsed[: _TITLE_MAX - 1].rstrip() + "\u2026"


def _clean_title(raw: str, question: str) -> str:
    """Normalize a model-generated title and bound it to the sidebar width.

    Strips wrapping quotes and trailing punctuation the prompt forbids, collapses
    whitespace, and falls back to the deterministic question title when the model
    returned nothing usable.
    """
    title = " ".join((raw or "").split())
    title = title.strip("\"'`")
    title = title.rstrip(" .:;,-")
    if not title:
        return _thread_title(question)
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 1].rstrip() + "\u2026"
    return title


async def _generate_thread_title(
    question: str, *, provider_id: str | None, model: str | None
) -> str:
    """Ask the agent's model for a concise title, falling back to the question.

    Title generation is best-effort: a provider failure or an unusable reply must
    never fail the turn, so every error path returns the deterministic title.
    """
    try:
        config = await assistant_provider.resolve(provider_id=provider_id, model=model)
        message = await assistant_provider.complete(
            messages=[
                {"role": "system", "content": CREATE_THREAD_TITLE_PROMPT},
                {"role": "user", "content": question},
            ],
            provider=config,
        )
    except Exception:  # noqa: BLE001 - a title is not worth failing a turn
        logger.warning("Could not generate the thread title")
        return _thread_title(question)
    return _clean_title(message.get("content") or "", question)


def _text_from_frame(frame: str) -> str:
    """Extract ``text`` from a ``text_delta`` SSE frame for the transcript."""
    import json

    for line in frame.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip()).get("text", "")
            except (json.JSONDecodeError, AttributeError):
                return ""
    return ""


def _auto_frame(kind: str, payload: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


async def _stream_auto_events(root_run_id: str, after: int) -> AsyncIterator[str]:
    """Replay a unified tree in session order; the worker never waits on SSE."""
    cursor = max(-1, after // 3 - 1) if after >= 0 else -1
    deadline = asyncio.get_running_loop().time() + 3690
    final_id = str(uuid5(NAMESPACE_URL, f"nova:auto:final:{root_run_id}"))
    while asyncio.get_running_loop().time() < deadline:
        batch = await harness_repository.events_page(
            root_run_id, cursor, ensure_complete=True
        )
        for item in batch:
            cursor = int(item["event_id"])
            base = cursor * 3
            payload = {
                **item["payload"],
                "run_id": root_run_id,
                "sequence": base,
                "child_run_id": item["run_id"],
            }
            if base > after:
                yield _auto_frame(item["type"], payload)
            if item["run_id"] == root_run_id and item["type"] == "agent_completed":
                answer = str(item["payload"].get("answer") or "")
                if base + 1 > after:
                    yield _auto_frame(
                        "text_delta",
                        {
                            "text": answer,
                            "run_id": root_run_id,
                            "sequence": base + 1,
                        },
                    )
                if base + 2 > after:
                    yield _auto_frame(
                        "done",
                        {
                            "message_id": final_id,
                            "finish_reason": "stop",
                            "run_id": root_run_id,
                            "sequence": base + 2,
                        },
                    )
                return
            if item["run_id"] == root_run_id and item["type"] in {
                "agent_failed",
                "agent_cancelled",
            }:
                if base + 1 > after:
                    yield _auto_frame(
                        "error",
                        {
                            "code": item["type"],
                            "message": "The Auto run did not complete.",
                            "run_id": root_run_id,
                            "sequence": base + 1,
                        },
                    )
                if base + 2 > after:
                    yield _auto_frame(
                        "done",
                        {
                            "message_id": final_id,
                            "finish_reason": "error",
                            "run_id": root_run_id,
                            "sequence": base + 2,
                        },
                    )
                return
        if not batch:
            yield "event: ping\ndata: {}\n\n"
        await asyncio.sleep(0.5)


async def _scoped_auto_root(root_run_id: str, user: dict) -> dict:
    root = await harness_repository.get(root_run_id)
    invalid = (
        not root
        or root["depth"] != 0
        or root["agent_id"] != AUTO_AGENT_ID
        or root["owner_name"] != user["username"]
        or root["role_name"] != session_security(user).active_role
    )
    if invalid:
        raise HTTPException(status_code=404, detail="Run not found in this role")
    await _require_agent_thread(root["thread_id"], AUTO_AGENT_ID, user["username"])
    return root


@contextlib.asynccontextmanager
async def _auto_admission(
    thread_id: str, owner_name: str
) -> AsyncIterator[Callable[[], Awaitable[None]]]:
    try:
        async with harness_repository.admission_lock(thread_id, owner_name) as assert_owned:
            yield assert_owned
    except AutoAdmissionUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Auto admission is temporarily unavailable"
        ) from exc


# ── Agents ─────────────────────────────────────────────────────


@router.get("", response_model=AgentListResponse)
async def list_agents(
    database: str | None = None,
    search: str | None = None,
    studio: bool = False,
    user: dict = Depends(get_current_user),
):
    try:
        agents = await agent_repository.list_agents(
            owner_name=user["username"], database_name=database, search=search
        )
        role = session_security(user).active_role
        shared = await agent_repository.list_shared_agents(role_name=role)
        agents.extend(
            agent
            for agent in shared
            if agent["owner_name"] != user["username"]
            and (not database or agent.get("database_name") == database)
            and (not search or search.lower() in agent["name"].lower())
        )
        if studio:
            agents = [
                agent
                for agent in agents
                if await has_verified_access(agent, role_name=role, user=user)
            ]
            agents.insert(0, _auto_agent(user["username"]))
    except AgentMetadataUnavailable:
        raise HTTPException(
            status_code=503, detail="Agent metadata is temporarily unavailable"
        ) from None
    views = [_agent_view(a) for a in agents]
    return AgentListResponse(agents=views, count=len(views))


@router.get("/capabilities")
async def discover_agent_capabilities(query: str = "", user: dict = Depends(get_current_user)):
    """Compact manifests only for agents the current role can execute."""
    try:
        candidates = await authorized_candidates(user)
    except AgentDiscoveryUnavailable:
        raise HTTPException(
            status_code=503, detail="Agent discovery is temporarily unavailable"
        ) from None
    ranked = rank_candidates(query[:400], candidates, semantic_matches(query[:400], candidates))
    return {
        "agents": [
            {"agent_id": item.agent_id, "name": item.name, "manifest": item.manifest.model_dump()}
            for item in ranked[:32]
        ]
    }


@router.put("/capabilities/{agent_id}")
async def update_agent_capabilities(
    agent_id: str,
    body: CapabilityManifest,
    user: dict = Depends(get_current_user),
):
    agent = await agent_repository.get_agent(agent_id, owner_name=user["username"])
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        await capability_repository.put(agent, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit_log(
        event_type="AGENT_CAPABILITY",
        user_name=user["username"],
        action="UPDATE",
        object_type="AGENT",
        object_name=agent_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=session_security(user).active_role,
    )
    return body


@router.post("", response_model=AgentView, status_code=201)
async def create_agent(
    body: AgentCreateRequest,
    user: dict = Depends(get_current_user),
):
    fields = body.model_dump()
    await _normalize_view_binding(fields, provided=body.model_fields_set, user=user)
    # Keep accepting legacy clients that send a mode, but never persist a
    # user-selected strategy. Provider capabilities and turn risk decide it.
    fields["harness_mode"] = "auto"
    unknown = _unknown_tools(fields.get("default_tools") or [])
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown tool(s): {', '.join(sorted(unknown))}"
        )
    unavailable = await _unavailable_mcp_tools(fields.get("default_tools") or [])
    if unavailable:
        raise HTTPException(
            status_code=422, detail=f"Unavailable MCP tool(s): {', '.join(sorted(unavailable))}"
        )
    try:
        fields["compiled_instructions"] = compile_agent_instructions(
            response=fields.get("instructions_response", ""),
            orchestration=fields.get("instructions_orchestration", ""),
            description=fields.get("description", ""),
            response_style=fields.get("response_style"),
        ).as_dict()
    except InstructionCompilationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    created = await agent_repository.create_agent(owner_name=user["username"], fields=fields)
    return _agent_view(created)


# ── Semantic models ────────────────────────────────────────────
#
# Declared before the dynamic ``/{agent_id}`` routes at the bottom of this file:
# FastAPI matches in declaration order, so a literal first segment
# ("semantic-models", "skills") must be registered before ``/{agent_id}`` or the
# dynamic route would capture it as an agent id.


@router.get("/semantic-models", response_model=SemanticModelListResponse, include_in_schema=False)
async def list_semantic_models(user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=410, detail="Use /api/v1/semantic-views")


@router.post(
    "/semantic-models", response_model=SemanticModelView,
    status_code=201, include_in_schema=False,
)
async def create_semantic_model(
    body: SemanticModelCreateRequest,
    user: dict = Depends(get_current_user),
):
    raise HTTPException(status_code=410, detail="Create a Semantic View instead")


@router.post(
    "/semantic-models/validate", response_model=SemanticValidateResponse,
    include_in_schema=False,
)
async def validate_semantic_model(
    body: SemanticValidateRequest,
    user: dict = Depends(get_current_user),
):
    del user  # validation is pure; the dependency enforces authentication only
    from app.modules.agents.semantic.ossie import parse_ossie

    result = parse_ossie(body.definition, raise_on_error=False)
    if result.valid:
        from app.modules.agents.semantic.ir import SemanticModelIR
        from app.modules.agents.semantic.runtime import validate_semantic_model_ir

        ir_validation = validate_semantic_model_ir(SemanticModelIR.from_ossie(result.as_dict()))
        result.errors.extend(ir_validation.errors)
        result.warnings.extend(ir_validation.warnings)
        result.valid = result.valid and ir_validation.valid
    return SemanticValidateResponse(
        valid=result.valid,
        ossie_version=result.version,
        errors=result.errors,
        warnings=result.warnings,
        dataset_count=result.dataset_count,
        metric_count=result.metric_count,
        relationship_count=result.relationship_count,
    )


@router.post(
    "/semantic-models/{model_id}/preview",
    response_model=SemanticPreviewResponse,
    include_in_schema=False,
)
async def preview_semantic_question(
    model_id: str,
    body: SemanticPreviewRequest,
    user: dict = Depends(get_current_user),
):
    raise HTTPException(status_code=410, detail="Use Semantic View preview")


@router.get(
    "/semantic-models/{model_id}/lint",
    response_model=SemanticLintResponse,
    include_in_schema=False,
)
async def lint_semantic_model(
    model_id: str,
    user: dict = Depends(get_current_user),
):
    raise HTTPException(status_code=410, detail="Use Semantic View quality")


@router.post(
    "/semantic-models/{model_id}/verified-queries",
    response_model=VerifiedQueryView,
    status_code=201,
    include_in_schema=False,
)
async def create_verified_query(
    model_id: str,
    body: VerifiedQueryCreateRequest,
    user: dict = Depends(get_current_user),
):
    raise HTTPException(status_code=410, detail="Add the verified query to a Semantic View draft")


@router.get(
    "/semantic-models/{model_id}/verified-queries",
    response_model=VerifiedQueryListResponse,
    include_in_schema=False,
)
async def list_verified_queries(
    model_id: str,
    user: dict = Depends(get_current_user),
):
    raise HTTPException(status_code=410, detail="Use Semantic View quality")


@router.get(
    "/semantic-models/{model_id}/quality-lab",
    response_model=SemanticQualityLabResponse,
    include_in_schema=False,
)
async def run_semantic_quality_lab(
    model_id: str,
    user: dict = Depends(get_current_user),
):
    raise HTTPException(status_code=410, detail="Use Semantic View quality")


@router.post(
    "/semantic-models/{model_id}/rule-proposals",
    response_model=RuleProposalView,
    status_code=201,
    include_in_schema=False,
)
async def create_rule_proposal(
    model_id: str,
    body: RuleProposalCreateRequest,
    user: dict = Depends(get_current_user),
):
    owner = user["username"]
    role = session_security(user).active_role
    from app.modules.intelligence.semantic_views import semantic_view_service

    model = await semantic_view_service.get_active_for_agent(model_id, user)
    agent = await agent_repository.get_agent(body.agent_id, owner_name=owner)
    if model is None or model.get("owner_name") != owner or agent is None:
        raise HTTPException(status_code=404, detail="Semantic View or agent not found")
    if model_id not in bound_view_ids(agent):
        raise HTTPException(status_code=422, detail="The agent is not bound to this View.")
    memory = await memory_repository.get(
        body.memory_id, user_name=owner, agent_id=body.agent_id, role_name=role
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found in this role")
    try:
        _, prior_expression, prior_fp, proposed_fp, _, _ = candidate_definition(
            model.get("definition") or {}, body.metric_name, body.proposed_expression
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    created = await rule_proposal_repository.create(
        {
            "owner_name": owner,
            "agent_id": body.agent_id,
            "role_name": role,
            "memory_id": body.memory_id,
            "semantic_model_id": model_id,
            "metric_name": body.metric_name,
            "prior_expression": prior_expression,
            "proposed_expression": body.proposed_expression.strip(),
            "prior_fingerprint": prior_fp,
            "proposed_fingerprint": proposed_fp,
        }
    )
    await write_audit_log(
        event_type="AGENT_RULE_PROPOSAL",
        user_name=owner,
        action="CREATE",
        object_type="SEMANTIC_RULE_PROPOSAL",
        object_name=created["proposal_id"],
        status="SUCCESS",
        active_role=role,
        session_id=user.get("session_id"),
    )
    return RuleProposalView(**created)


@router.get(
    "/semantic-models/{model_id}/rule-proposals",
    response_model=RuleProposalListResponse,
    include_in_schema=False,
)
async def list_rule_proposals(model_id: str, user: dict = Depends(get_current_user)):
    owner = user["username"]
    from app.modules.intelligence.semantic_views import semantic_view_service

    model = await semantic_view_service.get_active_for_agent(model_id, user)
    if model is None or model.get("owner_name") != owner:
        raise HTTPException(status_code=404, detail="Semantic View not found")
    role = session_security(user).active_role
    rows = await rule_proposal_repository.list(
        owner_name=owner, role_name=role, semantic_model_id=model_id
    )
    return RuleProposalListResponse(
        proposals=[RuleProposalView(**row) for row in rows], count=len(rows)
    )


async def _pending_rule_proposal(model_id: str, proposal_id: str, user: dict) -> tuple[dict, dict]:
    owner = user["username"]
    role = session_security(user).active_role
    proposal = await rule_proposal_repository.get(proposal_id, owner_name=owner, role_name=role)
    from app.modules.intelligence.semantic_views import semantic_view_service

    model = await semantic_view_service.get_active_for_agent(model_id, user)
    if (
        proposal is None or model is None or model.get("owner_name") != owner
        or proposal["semantic_model_id"] != model_id
    ):
        raise HTTPException(status_code=404, detail="Rule proposal not found")
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail="Rule proposal is no longer pending")
    return proposal, model


@router.post(
    "/semantic-models/{model_id}/rule-proposals/{proposal_id}/preview",
    response_model=RuleProposalPreviewResponse,
    include_in_schema=False,
)
async def preview_rule_proposal(
    model_id: str, proposal_id: str, user: dict = Depends(get_current_user)
):
    from app.common.sql_guard import redact_sql_credentials, split_sql_statements
    from app.modules.assistant.tools import policy
    from app.modules.query.service import query_service

    proposal, model = await _pending_rule_proposal(model_id, proposal_id, user)
    try:
        _, _, prior_fp, proposed_fp, old_sql, new_sql = candidate_definition(
            model.get("definition") or {},
            proposal["metric_name"],
            proposal["proposed_expression"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if prior_fp != proposal["prior_fingerprint"] or proposed_fp != proposal["proposed_fingerprint"]:
        raise HTTPException(
            status_code=409, detail="The semantic model changed. Create a new proposal."
        )
    values: list[str | None] = []
    for sql in (old_sql, new_sql):
        statements = split_sql_statements(sql)
        classification, _ = policy.classify_statements(statements)
        if len(statements) != 1 or classification != "read_only":
            raise HTTPException(status_code=422, detail="Preview SQL must be read-only")
        try:
            results = await query_service.execute_statements(
                sql=sql,
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                role=session_security(user).active_role,
                database=model.get("database_name"),
                schema=model.get("schema_name"),
                max_rows=1,
                session_id=user.get("session_id"),
                confirm_destructive=False,
            )
        except Exception as exc:
            logger.warning("Rule preview failed: %s", type(exc).__name__)
            raise HTTPException(status_code=422, detail="The preview query failed") from exc
        failed = next((item for item in results if item.error), None)
        if failed is not None:
            raise HTTPException(status_code=422, detail="The preview query failed")
        row = next((item.rows[0] for item in results if item.rows), None)
        values.append(str(row[0]) if row and row[0] is not None else None)
    await rule_proposal_repository.mark_previewed(
        proposal_id, owner_name=user["username"], role_name=session_security(user).active_role
    )
    await write_audit_log(
        event_type="AGENT_RULE_PROPOSAL",
        user_name=user["username"],
        action="PREVIEW",
        object_type="SEMANTIC_RULE_PROPOSAL",
        object_name=proposal_id,
        status="SUCCESS",
        active_role=session_security(user).active_role,
        session_id=user.get("session_id"),
    )
    return RuleProposalPreviewResponse(
        proposal_id=proposal_id,
        prior_sql=redact_sql_credentials(old_sql),
        proposed_sql=redact_sql_credentials(new_sql),
        prior_value=values[0],
        proposed_value=values[1],
        metric_name=proposal["metric_name"],
        model_fingerprint=prior_fp,
    )


@router.post(
    "/semantic-models/{model_id}/rule-proposals/{proposal_id}/approve",
    response_model=RuleProposalView,
    include_in_schema=False,
)
async def approve_rule_proposal(
    model_id: str, proposal_id: str, user: dict = Depends(get_current_user)
):
    from app.modules.agents.semantic.compiler import SemanticCompiler
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.planning import SemanticPlan
    from app.modules.intelligence.semantic_views import (
        SemanticViewVersionCreate,
        semantic_view_service,
    )

    proposal, model = await _pending_rule_proposal(model_id, proposal_id, user)
    if proposal["previewed_at"] is None:
        raise HTTPException(status_code=409, detail="Preview the impact before approval")
    active_fingerprint = SemanticModelIR.from_ossie(model["definition"]).fingerprint
    if active_fingerprint == proposal["proposed_fingerprint"]:
        await rule_proposal_repository.set_status(
            proposal_id,
            owner_name=user["username"],
            role_name=session_security(user).active_role,
            status="approved",
        )
        updated = await rule_proposal_repository.get(
            proposal_id,
            owner_name=user["username"],
            role_name=session_security(user).active_role,
        )
        return RuleProposalView(**updated)
    try:
        candidate, _, prior_fp, proposed_fp, _, _ = candidate_definition(
            model.get("definition") or {},
            proposal["metric_name"],
            proposal["proposed_expression"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if prior_fp != proposal["prior_fingerprint"] or proposed_fp != proposal["proposed_fingerprint"]:
        raise HTTPException(
            status_code=409, detail="The Semantic View changed. Create a new proposal."
        )
    candidate_ir = SemanticModelIR.from_ossie(candidate)
    verified = candidate.get("verified_queries") or []
    try:
        for item in verified:
            SemanticCompiler().compile(candidate_ir, SemanticPlan.from_dict(item["semantic_plan"]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A verified query no longer compiles") from exc
    described = await semantic_view_service.describe(model_id, user)
    latest = max(described.get("versions") or [], key=lambda item: item["version"])
    if latest["version"] > model["version"]:
        if latest.get("fingerprint") != proposed_fp or latest.get("status") not in {
            "DRAFT", "VALIDATED"
        }:
            raise HTTPException(
                status_code=409, detail="Review the newer Semantic View draft first"
            )
        version = latest["version"]
    else:
        draft = await semantic_view_service.add_version(
            model_id,
            SemanticViewVersionCreate(definition=json.dumps(candidate, ensure_ascii=False)),
            user,
        )
        version = draft["version"]
    report = await semantic_view_service.validate(model_id, version, user)
    if not report.get("valid"):
        raise HTTPException(
            status_code=422,
            detail=f"Semantic View draft v{version} failed validation; review its quality report",
        )
    if (report.get("regression") or {}).get("changed", 0):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Semantic View draft v{version} needs explicit regression review "
                "and acknowledgement before publish"
            ),
        )
    await semantic_view_service.publish(model_id, version, user)
    role = session_security(user).active_role
    await rule_proposal_repository.set_status(
        proposal_id, owner_name=user["username"], role_name=role, status="approved"
    )
    await write_audit_log(
        event_type="AGENT_RULE_PROPOSAL",
        user_name=user["username"],
        action="APPROVE",
        object_type="SEMANTIC_RULE_PROPOSAL",
        object_name=proposal_id,
        status="SUCCESS",
        active_role=role,
        session_id=user.get("session_id"),
    )
    updated = await rule_proposal_repository.get(
        proposal_id, owner_name=user["username"], role_name=role
    )
    return RuleProposalView(**updated)


@router.post(
    "/semantic-models/{model_id}/rule-proposals/{proposal_id}/reject",
    response_model=RuleProposalView,
    include_in_schema=False,
)
async def reject_rule_proposal(
    model_id: str, proposal_id: str, user: dict = Depends(get_current_user)
):
    await _pending_rule_proposal(model_id, proposal_id, user)
    role = session_security(user).active_role
    await rule_proposal_repository.set_status(
        proposal_id, owner_name=user["username"], role_name=role, status="rejected"
    )
    await write_audit_log(
        event_type="AGENT_RULE_PROPOSAL",
        user_name=user["username"],
        action="REJECT",
        object_type="SEMANTIC_RULE_PROPOSAL",
        object_name=proposal_id,
        status="SUCCESS",
        active_role=role,
        session_id=user.get("session_id"),
    )
    updated = await rule_proposal_repository.get(
        proposal_id, owner_name=user["username"], role_name=role
    )
    return RuleProposalView(**updated)


@router.get(
    "/semantic-models/{model_id}", response_model=SemanticModelView,
    include_in_schema=False,
)
async def get_semantic_model(model_id: str, user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=410, detail="Use /api/v1/semantic-views")


@router.delete("/semantic-models/{model_id}", status_code=204, include_in_schema=False)
async def delete_semantic_model(model_id: str, user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=410, detail="Use Semantic View lifecycle actions")


# ── Skills ─────────────────────────────────────────────────────


@router.get("/skills", response_model=SkillListResponse)
async def list_skills(user: dict = Depends(get_current_user)):
    skills = await agent_repository.list_skills(owner_name=user["username"])
    views = [_skill_view(s) for s in merge_skill_rows(skills)]
    return SkillListResponse(skills=views, count=len(views))


@router.post("/skills", response_model=SkillView, status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    user: dict = Depends(get_current_user),
):
    from app.modules.agents.personal_skills import save_skill

    created = await save_skill(body.model_dump(), user=user)
    return _skill_view(created)


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(skill_id: str, user: dict = Depends(get_current_user)):
    from app.modules.agents.personal_skills import audit_skill

    if is_builtin_skill_id(skill_id):
        raise HTTPException(status_code=409, detail="Built-in skills are read-only.")
    if not await agent_repository.get_skill(skill_id, owner_name=user["username"]):
        raise HTTPException(status_code=404, detail="Skill not found")
    await agent_repository.delete_skill(skill_id, owner_name=user["username"])
    await audit_skill(user, "DELETE", skill_id)
    return None


# ── Agents by id (dynamic — declared last) ─────────────────────
#
# These carry a dynamic ``{agent_id}`` segment, so they must come after every
# literal-prefix route above ("semantic-models", "skills"). FastAPI matches in
# declaration order; registering these earlier would let ``/semantic-models`` be
# captured as an agent id.


@router.get("/auto/runs/{root_run_id}")
async def get_auto_run_tree(root_run_id: str, user: dict = Depends(get_current_user)):
    root = await _scoped_auto_root(root_run_id, user)
    tree = await harness_repository.tree(
        root_run_id, owner_name=user["username"], role_name=root["role_name"]
    )
    return {
        "runs": [
            {
                "run_id": item["run_id"],
                "root_run_id": root_run_id,
                "parent_run_id": item["parent_run_id"],
                "agent_id": item["agent_id"],
                "agent_name": str((item.get("payload") or {}).get("agent_name") or ""),
                "objective": item["objective"],
                "status": item["status"],
                "depth": item["depth"],
                "summary": item["result_summary"],
                "prompt_tokens": item["prompt_tokens"],
                "completion_tokens": item["completion_tokens"],
                "started_at": item["started_at"],
                "updated_at": item["updated_at"],
                "error_class": item["error_class"],
            }
            for item in tree
        ]
    }


@router.get("/auto/threads/{thread_id}/runs")
async def list_auto_thread_runs(thread_id: str, user: dict = Depends(get_current_user)):
    await _require_agent_thread(thread_id, AUTO_AGENT_ID, user["username"])
    roots = await harness_repository.roots_for_thread(
        thread_id,
        owner_name=user["username"],
        role_name=session_security(user).active_role,
    )
    return {
        "runs": [
            {
                "run_id": item["run_id"],
                "status": item["status"],
                "objective": item["objective"],
                "started_at": item["started_at"],
                "user_message_id": (item.get("payload") or {}).get("user_message_id"),
                "final_message_id": str(
                    uuid5(NAMESPACE_URL, f"nova:auto:final:{item['run_id']}")
                ),
            }
            for item in roots
        ]
    }


@router.get("/auto/runs/{root_run_id}/messages")
async def get_auto_messages(root_run_id: str, user: dict = Depends(get_current_user)):
    await _scoped_auto_root(root_run_id, user)
    return {"messages": await harness_repository.messages_for_tree(root_run_id)}


@router.get("/auto/runs/{root_run_id}/children/{child_run_id}/timeline")
async def get_auto_child_timeline(
    root_run_id: str,
    child_run_id: str,
    after: int = Query(default=-1, ge=-1, le=2**53 - 1),
    limit: int = Query(default=100, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    root = await _scoped_auto_root(root_run_id, user)
    child = await harness_repository.get(child_run_id)
    if (
        not child
        or child["run_id"] != child_run_id
        or child["root_run_id"] != root_run_id
        or child["parent_run_id"] != root_run_id
        or child["depth"] != 1
        or child["owner_name"] != root["owner_name"]
        or child["role_name"] != root["role_name"]
        or child["thread_id"] != root["thread_id"]
    ):
        raise HTTPException(status_code=404, detail="Child run not found in this role")
    if child["status"] in TERMINAL:
        await harness_repository.ensure_terminal_event(root_run_id, child)
    try:
        agent = await agent_repository.get_agent(
            child["agent_id"], owner_name=user["username"]
        )
        if agent is None:
            agent = await agent_repository.get_shared_agent(
                child["agent_id"], role_name=root["role_name"]
            )
    except AgentMetadataUnavailable:
        agent = None
    events_page, next_cursor, has_more = await harness_repository.child_events_page(
        root_run_id, child_run_id, after, limit=limit
    )
    return {
        "run": {
            "run_id": child_run_id,
            "root_run_id": root_run_id,
            "agent_id": child["agent_id"],
            "agent_name": str((child.get("payload") or {}).get("agent_name") or "")
            or (agent["name"] if agent else "Specialist"),
            "objective": child["objective"],
            "status": child["status"],
            "result_summary": child["result_summary"],
            "error_class": child["error_class"],
            "prompt_tokens": child["prompt_tokens"],
            "completion_tokens": child["completion_tokens"],
        },
        "events": events_page,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.post("/auto/runs/{root_run_id}/children/{child_run_id}/messages")
async def send_auto_child_message(
    root_run_id: str,
    child_run_id: str,
    body: AutoMessageRequest,
    user: dict = Depends(get_current_user),
):
    root = await _scoped_auto_root(root_run_id, user)
    child = await harness_repository.get(child_run_id)
    if not child or child["root_run_id"] != root_run_id or child["status"] in TERMINAL:
        raise HTTPException(status_code=404, detail="Active child not found")
    try:
        message_id = await harness_repository.send(
            sender=root,
            recipient=child,
            operation_id=f"user:{body.operation_id}",
            message_type="message",
            content=body.content,
            correlation_id=body.correlation_id,
            reply_to=body.reply_to,
            origin="user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"message_id": message_id, "status": "queued"}


@router.get("/auto/runs/{root_run_id}/events")
async def get_auto_events(
    root_run_id: str,
    after: str = "",
    user: dict = Depends(get_current_user),
):
    await _scoped_auto_root(root_run_id, user)
    return {
        "events": await harness_repository.events_after(
            root_run_id, after, ensure_complete=True
        )
    }


@router.post("/auto/runs/{root_run_id}/cancel")
async def cancel_auto_run(root_run_id: str, user: dict = Depends(get_current_user)):
    await _scoped_auto_root(root_run_id, user)
    if not await harness_repository.cancel_tree(root_run_id):
        raise HTTPException(status_code=409, detail="Auto run is already terminal")
    await harness_repository.reconcile_cancelled_children(root_run_id)
    await harness_repository.event(root_run_id, root_run_id, "agent_cancelled", {})
    await write_audit_log(
        event_type="AGENT_RUN",
        user_name=user["username"],
        action="CANCEL",
        object_type="AGENT_RUN",
        object_name=root_run_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=session_security(user).active_role,
    )
    return {"status": "cancelled"}


@router.post("/auto/runs/{root_run_id}/children/{child_run_id}/cancel")
async def cancel_auto_child(
    root_run_id: str,
    child_run_id: str,
    user: dict = Depends(get_current_user),
):
    await _scoped_auto_root(root_run_id, user)
    if not await harness_repository.cancel_child(root_run_id, child_run_id):
        raise HTTPException(status_code=404, detail="Active child not found")
    await harness_repository.event(root_run_id, child_run_id, "agent_cancelled", {})
    await harness_repository.wake_parent(root_run_id)
    await write_audit_log(
        event_type="AGENT_RUN",
        user_name=user["username"],
        action="CANCEL",
        object_type="AGENT_RUN",
        object_name=child_run_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=session_security(user).active_role,
    )
    return {"status": "cancelled"}


@router.get("/{agent_id}", response_model=AgentView)
async def get_agent(agent_id: str, user: dict = Depends(get_current_user)):
    if agent_id == SKILL_AUTHOR_ID:
        raise HTTPException(404, "Agent not found")
    agent = await agent_repository.get_agent(agent_id, owner_name=user["username"])
    if agent is None:
        agent = await _require_agent(agent_id, user)
    return _agent_view(agent)


@router.put("/{agent_id}", response_model=AgentView)
async def update_agent(
    agent_id: str,
    body: AgentUpdateRequest,
    user: dict = Depends(get_current_user),
):
    if agent_id == SKILL_AUTHOR_ID:
        raise HTTPException(404, "Agent not found")
    existing = await _require_agent(agent_id, user["username"])
    fields = body.model_dump(exclude_unset=True)
    await _normalize_view_binding(fields, provided=body.model_fields_set, user=user)
    fields["harness_mode"] = "auto"
    unknown = _unknown_tools(fields.get("default_tools") or [])
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown tool(s): {', '.join(sorted(unknown))}"
        )
    unavailable = await _unavailable_mcp_tools(fields.get("default_tools") or [])
    if unavailable:
        raise HTTPException(
            status_code=422, detail=f"Unavailable MCP tool(s): {', '.join(sorted(unavailable))}"
        )
    if {
        "instructions_response",
        "instructions_orchestration",
        "description",
        "response_style",
    } & fields.keys():
        merged = {**existing, **fields}
        try:
            fields["compiled_instructions"] = compile_agent_instructions(
                response=merged.get("instructions_response", ""),
                orchestration=merged.get("instructions_orchestration", ""),
                description=merged.get("description", ""),
                response_style=merged.get("response_style"),
            ).as_dict()
        except InstructionCompilationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated = await agent_repository.update_agent(
        agent_id, owner_name=user["username"], fields=fields
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_view(updated)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, user: dict = Depends(get_current_user)):
    if not await agent_repository.delete_agent(agent_id, owner_name=user["username"]):
        raise HTTPException(status_code=404, detail="Agent not found")
    await capability_repository.delete(agent_id, user["username"])
    await memory_repository.delete_agent(user_name=user["username"], agent_id=agent_id)
    await write_audit_log(
        event_type="AGENT_MEMORY",
        user_name=user["username"],
        action="DELETE_ALL",
        object_type="AGENT",
        object_name=agent_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return None


# ── Nova Studio runs (agent-scoped threads + the SSE turn) ─────


@router.get("/{agent_id}/memories")
async def list_agent_memories(
    agent_id: str,
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user)
    page_size = min(max(limit, 1), 100)
    offset = max(offset, 0)
    memories = await memory_repository.list(
        user_name=user["username"],
        agent_id=agent_id,
        role_name=session_security(user).active_role,
        limit=page_size + 1,
        offset=offset,
    )
    has_more = len(memories) > page_size
    return {
        "memories": memories[:page_size],
        "count": min(len(memories), page_size),
        "next_offset": offset + page_size if has_more else None,
    }


@router.delete("/{agent_id}/memories/{memory_id}", status_code=204)
async def delete_agent_memory(
    agent_id: str,
    memory_id: str,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user)
    deleted = await memory_repository.delete(
        memory_id,
        user_name=user["username"],
        agent_id=agent_id,
        role_name=session_security(user).active_role,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    await write_audit_log(
        event_type="AGENT_MEMORY",
        user_name=user["username"],
        action="DELETE",
        object_type="AGENT_MEMORY",
        object_name=memory_id,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return None


@router.get("/{agent_id}/threads", response_model=ThreadListResponse)
async def list_agent_threads(agent_id: str, user: dict = Depends(get_current_user)):
    await _require_agent(agent_id, user)
    try:
        threads = await assistant_repository.list_threads(
            user_name=user["username"], agent_id=agent_id
        )
    except AssistantThreadListUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Conversation history is temporarily unavailable"
        ) from exc
    views = [_thread_view(t) for t in threads]
    return ThreadListResponse(threads=views, count=len(views))


@router.post("/{agent_id}/threads", response_model=ThreadView, status_code=201)
async def create_agent_thread(
    agent_id: str,
    body: ThreadCreateRequest,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user)
    thread = await assistant_repository.create_thread(
        user_name=user["username"],
        title=body.title,
        workspace_file_id=body.workspace_file_id,
        agent_id=agent_id,
    )
    thread_store.register(
        thread_id=thread["thread_id"],
        user_name=user["username"],
        title=thread["title"],
        workspace_file_id=thread.get("workspace_file_id"),
    )
    return _thread_view(thread)


@router.get("/{agent_id}/threads/{thread_id}", response_model=ThreadDetailResponse)
async def get_agent_thread(
    agent_id: str,
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user)
    thread = await _require_agent_thread(thread_id, agent_id, user["username"])
    messages = await assistant_repository.list_messages(
        thread_id,
        user_name=user["username"],
        synchronize=agent_id == AUTO_AGENT_ID,
    )
    return ThreadDetailResponse(
        thread=_thread_view(thread),
        messages=[_message_view(m) for m in messages],
    )


@router.put(
    "/{agent_id}/threads/{thread_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackRequest,
)
async def update_message_feedback(
    agent_id: str,
    thread_id: str,
    message_id: str,
    body: MessageFeedbackRequest,
    user: dict = Depends(get_current_user),
) -> MessageFeedbackRequest:
    await _require_agent(agent_id, user)
    await _require_agent_thread(thread_id, agent_id, user["username"])
    updated = await assistant_repository.set_feedback(
        thread_id, message_id, body.feedback, user_name=user["username"]
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    await write_audit_log(
        event_type="ASSISTANT",
        user_name=user["username"],
        action="FEEDBACK",
        object_type="ASSISTANT_MESSAGE",
        object_name=message_id,
        status="SUCCESS",
        decision=body.feedback or "clear",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )
    return body


@router.delete("/{agent_id}/threads/{thread_id}", status_code=204)
async def delete_agent_thread(
    agent_id: str,
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user)
    await _require_agent_thread(thread_id, agent_id, user["username"])
    if agent_id == AUTO_AGENT_ID:
        async with _auto_admission(thread_id, user["username"]) as assert_owned:
            await _require_agent_thread(thread_id, agent_id, user["username"])
            if await harness_repository.active_for_thread(thread_id, user["username"]):
                raise HTTPException(status_code=409, detail="Cancel the active Auto run first")
            await assert_owned()
            await harness_repository.delete_thread(thread_id, owner_name=user["username"])
            await assert_owned()
            if not await assistant_repository.delete_thread(thread_id, user_name=user["username"]):
                raise HTTPException(status_code=404, detail="Thread not found")
    elif not await assistant_repository.delete_thread(thread_id, user_name=user["username"]):
        raise HTTPException(status_code=404, detail="Thread not found")
    await run_journal.delete_thread(thread_id, owner_name=user["username"])
    thread_store.remove(thread_id, user_name=user["username"])
    return None


@router.put("/{agent_id}/threads/{thread_id}", response_model=ThreadView)
async def rename_agent_thread(
    agent_id: str,
    thread_id: str,
    body: ThreadUpdateRequest,
    user: dict = Depends(get_current_user),
):
    """Rename a conversation. The generated title is a summary, not a name the
    user chose, so a title they can edit is the one that will actually be
    findable the next time they look for it."""
    await _require_agent(agent_id, user)
    await _require_agent_thread(thread_id, agent_id, user["username"])
    if body.title is None:
        thread = await _require_agent_thread(thread_id, agent_id, user["username"])
        return _thread_view(thread)
    renamed = await assistant_repository.rename_thread(
        thread_id, body.title, user_name=user["username"]
    )
    if renamed is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return _thread_view(renamed)


@router.get("/{agent_id}/threads/{thread_id}/runs/{run_id}/events")
async def replay_agent_run(
    agent_id: str,
    thread_id: str,
    run_id: str,
    after: int = Query(-1, ge=-1),
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user)
    await _require_agent_thread(thread_id, agent_id, user["username"])
    role = session_security(user).active_role
    if agent_id == AUTO_AGENT_ID:
        root = await harness_repository.get(run_id)
        if (
            not root
            or root["depth"] != 0
            or root["thread_id"] != thread_id
            or root["owner_name"] != user["username"]
            or root["role_name"] != role
        ):
            raise HTTPException(status_code=404, detail="Run not found in this role")
        return StreamingResponse(
            _stream_auto_events(run_id, after),
            media_type=events.SSE_MEDIA_TYPE,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    scope = dict(
        owner_name=user["username"],
        agent_id=agent_id,
        thread_id=thread_id,
        role_name=role,
    )
    if await run_journal.get(run_id, **scope) is None:
        raise HTTPException(status_code=404, detail="Run not found in this role")

    async def replay() -> AsyncIterator[str]:
        cursor = after
        terminal_seen = False
        deadline = asyncio.get_running_loop().time() + 3690
        while asyncio.get_running_loop().time() < deadline:
            state = await run_journal.get(run_id, **scope)
            if state is None:
                return
            frames = await run_journal.events_after(run_id, cursor)
            for frame in frames:
                payload = json.loads(frame.split("data: ", 1)[1])
                cursor = int(payload["sequence"])
                terminal_seen = terminal_seen or frame.startswith(f"event: {events.EVENT_DONE}\n")
                yield frame
            if (
                state["status"] == "running"
                and not frames
                and await run_journal.interrupt_stale(run_id, **scope)
            ):
                state = await run_journal.get(run_id, **scope)
            if (
                state is not None
                and state["status"] in {"interrupted", "failed"}
                and not terminal_seen
            ):
                code = "run_interrupted" if state["status"] == "interrupted" else "run_failed"
                yield events.format_sse(
                    events.EVENT_ERROR,
                    {
                        "run_id": run_id,
                        "sequence": cursor + 1,
                        "code": code,
                        "message": "The agent run stopped. Review saved steps before retrying.",
                    },
                )
                yield events.format_sse(
                    events.EVENT_DONE,
                    {
                        "run_id": run_id,
                        "sequence": cursor + 2,
                        "message_id": "",
                        "finish_reason": state["status"],
                    },
                )
                return
            if state["status"] != "running" and cursor >= state["last_sequence"]:
                return
            if not frames:
                await asyncio.sleep(1)

    return StreamingResponse(
        replay(),
        media_type=events.SSE_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{agent_id}/threads/{thread_id}/messages")
async def send_agent_message(
    agent_id: str,
    thread_id: str,
    body: AgentMessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Run one agent turn and stream it as SSE.

    Composes the Phase 10 bounded loop with the agent's own registry and system
    prompt. Everything the loop guarantees — consent, iteration cap, redaction,
    delegate-first execution — is unchanged; only the configuration differs.
    """
    security = session_security(user)
    stamp = observation_context(security)
    attachments = body.prepared_attachments
    prompt = attachment_prompt(body.content, attachments)
    agent = await _require_agent(agent_id, user)
    user_name = user["username"]
    thread_row = await _require_agent_thread(thread_id, agent_id, user_name)
    if agent_id == AUTO_AGENT_ID:
        if attachments:
            raise HTTPException(status_code=422, detail="Auto does not yet support attachments")
        from app.modules.assistant.skills import contains_credential_shape

        if not body.content.strip() or contains_credential_shape(body.content):
            raise HTTPException(status_code=422, detail="Invalid Auto message")
        async with _auto_admission(thread_id, user_name) as assert_owned:
            thread_row = await _require_agent_thread(thread_id, agent_id, user_name)
            if await harness_repository.active_for_thread(thread_id, user_name):
                raise HTTPException(status_code=409, detail="An Auto run is already active")
            await assert_owned()
            user_message = await assistant_repository.append_message(
                thread_id,
                user_name=user_name,
                role="user",
                content=body.content,
                security_context=stamp,
            )
            if thread_row["title"] in {"New chat", "New conversation"}:
                await assistant_repository.rename_thread(
                    thread_id, _thread_title(body.content), user_name=user_name
                )
            await assert_owned()
            root = await harness_repository.create_root(
                owner_name=user_name,
                thread_id=thread_id,
                role_name=security.active_role,
                session_id=user["session_id"],
                security_version=int(user.get("security_context_version") or 1),
                objective=body.content,
                provider_id=body.provider_id,
                model=body.model,
                user_message_id=user_message["message_id"],
            )
            await assert_owned()
        return StreamingResponse(
            _stream_auto_events(root["run_id"], -1),
            media_type=events.SSE_MEDIA_TYPE,
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Nova-Run-ID": root["run_id"],
            },
        )

    registry, system_prompt, budget, token_budget = await agent_service.build_loop_inputs(agent)

    runtime = thread_store.register(
        thread_id=thread_id,
        user_name=user_name,
        title=thread_row["title"],
        workspace_file_id=thread_row.get("workspace_file_id"),
    )
    # Apply the agent's HITL policy to this conversation's consent holder. An
    # ``auto_read_only`` agent pre-grants read-only tools so a business question
    # does not stall waiting for an approval no client will send; a destructive
    # call is still never covered (the grant is read-only-only), and
    # ``ask_every_tool`` leaves the grant off so every call prompts.
    # ``auto_read_only`` establishes a grant. ``ask_every_tool`` does not clear
    # an existing user-issued ``allow_session`` grant; it merely starts without
    # one. Clearing that grant is an explicit conversation action.
    if agent.get("policy") == "auto_read_only":
        runtime.consent.always_allow_read_only = True
    history = await assistant_repository.list_messages(thread_id, user_name=user_name)
    from app.modules.agents.personal_skills import SKILL_AUTHORING_PROMPT, is_skill_authoring
    from app.modules.assistant.tools import ToolRegistry

    if agent_id == SKILL_AUTHOR_ID or is_skill_authoring(body.content, history):
        from app.modules.agents.prompt import build_system_prompt

        registry = ToolRegistry()
        system_prompt = build_system_prompt({"name": "Nova Studio"}, actual_tools=[])
        system_prompt += "\n\n" + SKILL_AUTHORING_PROMPT
    elif agent_id != SKILL_AUTHOR_ID:
        try:
            memories = await memory_repository.list(
                user_name=user_name,
                agent_id=agent_id,
                role_name=security.active_role,
            )
            selected = select_memories(memories, body.content)
            if selected:
                system_prompt += "\n\n" + memory_prompt(selected)
        except Exception as exc:
            logger.warning("Could not load agent memory: %s", type(exc).__name__)
    runtime.messages = [
        AssistantMessage(
            message_id=row["message_id"],
            role=row["role"],
            content=attachment_prompt(row["content"], row.get("attachments") or [])
            if row["role"] == "user"
            else row["content"],
            steps=row.get("steps") or [],
            created_at=row["created_at"],
            security_context=row.get("security_context"),
            attachments=row.get("attachments") or [],
        )
        for row in history
    ]

    run_id = str(uuid4())
    await run_journal.start(
        run_id=run_id,
        owner_name=user_name,
        agent_id=agent_id,
        thread_id=thread_id,
        role_name=security.active_role,
    )

    await assistant_repository.append_message(
        thread_id,
        user_name=user_name,
        role="user",
        content=body.content,
        security_context=stamp,
        attachments=attachments,
    )
    if attachments:
        await write_audit_log(
            event_type="AGENT_ATTACHMENT",
            user_name=user_name,
            action="send",
            object_type="AGENT_THREAD",
            object_name=thread_id,
            status="SUCCESS",
            session_id=user.get("session_id"),
        )
    runtime.messages.append(
        AssistantMessage(
            message_id=str(uuid4()),
            role="user",
            content=prompt,
            security_context=stamp,
            attachments=attachments,
        )
    )

    # The first question names the conversation. A thread created as "New chat"
    # and never renamed is unfindable in a history list. The agent's model
    # summarizes the question into a concise title; if the call is unavailable,
    # the bounded question itself is the honest fallback.
    if not any(row["role"] == "user" for row in history):
        try:
            title = await _generate_thread_title(
                body.content or attachments[0]["name"],
                provider_id=agent.get("model_provider_id"),
                model=agent.get("model_name"),
            )
            await assistant_repository.rename_thread(thread_id, title, user_name=user_name)
        except Exception:  # noqa: BLE001 - a title is not worth failing a turn
            logger.warning("Could not set the thread title")

    context = LoopContext(
        user_name=user_name,
        database=await _resolve_database(agent, user),
        schema_name=agent.get("schema_name"),
        role=security.active_role,
        workspace_file_id=thread_row.get("workspace_file_id"),
        session_id=user.get("session_id"),
        thread_id=thread_id,
        user=user,
        attachments=attachments,
        has_attachment_history=any(row.get("attachments") for row in history),
        routing_content=body.content or "Review the attached files.",
        agent_id=agent_id,
        agent_owner_name=agent.get("owner_name"),
        semantic_model_id=agent.get("semantic_model_id"),
        semantic_model_ids=agent.get("semantic_model_ids") or [],
        semantic_view_ids=bound_view_ids(agent),
        model_provider_id=agent.get("model_provider_id"),
        model_name=agent.get("model_name"),
        harness_mode="auto",
        instructions=system_prompt,
        run_id=run_id,
    )

    # The agent's own budget replaces the loop default; the loop still caps it.
    # A per-agent context token budget (NOVA-124) curates the transcript so a
    # long conversation cannot overflow the model window; ``None`` uses the
    # loop's default.
    context_manager = (
        ContextManager(token_budget=token_budget) if token_budget is not None else None
    )
    loop = AssistantLoop(
        provider=assistant_provider,
        registry=registry,
        max_iterations=DEFAULT_MAX_ITERATIONS,
        time_budget_seconds=float(budget),
        system_prompt=system_prompt,
        context_manager=context_manager,
    )

    async def resolve_consent(invocation: ToolInvocation, classification: str) -> bool | None:
        future = consent_broker.open(
            invocation.tool_call_id,
            thread_id=thread_id,
            user_name=user_name,
            classification=classification,
        )

        try:
            return await future
        finally:
            consent_broker.resolve(invocation.tool_call_id, None, user_name=user_name)

    async def generate() -> AsyncIterator[str]:
        reply_parts: list[str] = []
        reply_stamp = stamp
        reply_message_id = str(uuid4())
        done_frame: str | None = None
        finish_reason: str | None = None
        try:
            async for frame in loop.run(
                thread=runtime,
                user_content=prompt,
                context=context,
                resolve_consent=resolve_consent,
                cancelled=lambda: False,
                model=body.model,
                provider_id=body.provider_id,
            ):
                if frame.startswith(f"event: {events.EVENT_TEXT_DELTA}"):
                    reply_parts.append(_text_from_frame(frame))
                if frame.startswith("event: role_changed\n"):
                    reply_parts.clear()
                    reply_stamp = observation_context(session_security(context.user or {}))
                if frame.startswith(f"event: {events.EVENT_DONE}\n"):
                    payload = json.loads(frame.split("data: ", 1)[1])
                    reply_message_id = payload["message_id"]
                    finish_reason = payload.get("finish_reason")
                    done_frame = frame
                    continue
                yield frame
        except Exception as exc:  # noqa: BLE001 - never leak an internal trace
            logger.exception("Agent stream failed")
            yield events.error("internal_error", f"The agent failed: {type(exc).__name__}")
            done_frame = events.done(reply_message_id, finish_reason="error")
        finally:
            if reply_parts or context.steps or context.pending_output:
                try:
                    steps = list(context.steps or [])
                    # A disconnect can happen after a query completed but before
                    # the final response compositor ran. Keep those redacted
                    # artifacts on the partial assistant turn instead of losing
                    # visible evidence on reload.
                    if context.pending_output:
                        steps.extend(context.pending_output)
                    # Record what context management did, so an Observability
                    # reader can explain a shrunken transcript (NOVA-124).
                    if context.context_stats and (
                        context.context_stats.get("dropped_turns")
                        or context.context_stats.get("cleared_tool_results")
                    ):
                        steps.insert(
                            0,
                            {"kind": "context", **context.context_stats},
                        )
                    await assistant_repository.append_message(
                        thread_id,
                        user_name=user_name,
                        role="assistant",
                        message_id=reply_message_id,
                        content="".join(reply_parts),
                        agent_id=agent_id,
                        model_name=agent.get("model_name"),
                        usage=context.usage,
                        steps=steps,
                        instructions=context.instructions,
                        security_context=(
                            reply_stamp
                            if reply_stamp
                            == observation_context(session_security(context.user or {}))
                            else None
                        ),
                    )
                except Exception:
                    logger.exception("Could not persist the agent reply")
            if (
                finish_reason
                in {
                    "stop",
                    "error",
                    "required_capability_incomplete",
                    "required_capability_unavailable",
                }
                and agent_id != SKILL_AUTHOR_ID
            ):
                try:
                    await asyncio.wait_for(
                        remember_user_message(
                            user_name=user_name,
                            agent_id=agent_id,
                            role_name=security.active_role,
                            thread_id=thread_id,
                            message=body.content,
                            provider_id=body.provider_id or agent.get("model_provider_id"),
                            model=body.model or agent.get("model_name"),
                            provider=assistant_provider,
                            session_id=user.get("session_id"),
                        ),
                        timeout=15.0,
                    )
                except Exception as exc:
                    logger.warning("Could not update agent memory: %s", type(exc).__name__)
        if done_frame is not None:
            yield done_frame

    stream_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=128)
    subscriber_connected = True

    def queue_frame(frame: str | None) -> None:
        nonlocal subscriber_connected
        if not subscriber_connected:
            return
        try:
            stream_queue.put_nowait(frame)
        except asyncio.QueueFull:
            # The saved journal is the source for a slow client's replay.
            subscriber_connected = False
            while not stream_queue.empty():
                stream_queue.get_nowait()
            stream_queue.put_nowait(None)

    async def produce() -> None:
        status = "failed"
        batch: list[str] = []

        async def keep_alive() -> None:
            while True:
                await asyncio.sleep(15)
                try:
                    await run_journal.heartbeat(run_id)
                except Exception:
                    logger.exception("Could not renew agent run lease")

        heartbeat_task = asyncio.create_task(keep_alive())

        async def flush() -> None:
            if batch:
                await run_journal.append_batch(run_id, list(batch))
                batch.clear()

        try:
            async for frame in generate():
                if frame.startswith("event: role_changed\n"):
                    await run_journal.block_replay_after_role_change(run_id)
                queue_frame(frame)
                batch.append(frame)
                if len(batch) >= 32 or frame.startswith(
                    (
                        "event: tool_call\n",
                        "event: tool_status\n",
                        "event: role_changed\n",
                        "event: error\n",
                        "event: done\n",
                    )
                ):
                    await flush()
                if frame.startswith(f"event: {events.EVENT_DONE}\n"):
                    payload = json.loads(frame.split("data: ", 1)[1])
                    if payload.get("finish_reason") == "stop":
                        status = "completed"
        except Exception:
            logger.exception("Agent run journal failed")
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            try:
                await flush()
            except Exception:
                logger.exception("Could not flush agent run events")
            try:
                await run_journal.finish(run_id, status)
                await write_audit_log(
                    event_type="AGENT_RUN",
                    user_name=user_name,
                    action="FINISH",
                    object_type="AGENT_RUN",
                    object_name=run_id,
                    status="SUCCESS" if status == "completed" else "FAILED",
                    active_role=security.active_role,
                    session_id=user.get("session_id"),
                )
            except Exception:
                logger.exception("Could not finalize agent run")
            queue_frame(None)

    task = asyncio.create_task(produce())
    _active_run_tasks.add(task)
    task.add_done_callback(_active_run_tasks.discard)

    async def stream() -> AsyncIterator[str]:
        nonlocal subscriber_connected
        try:
            while True:
                frame = await stream_queue.get()
                if frame is None:
                    return
                yield frame
        finally:
            subscriber_connected = False

    return StreamingResponse(
        stream(),
        media_type=events.SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Nova-Run-ID": run_id,
        },
    )


@router.post("/{agent_id}/tool-calls/{tool_call_id}/decision")
async def resolve_agent_tool_call(
    agent_id: str,
    tool_call_id: str,
    body: ConsentDecisionRequest,
    user: dict = Depends(get_current_user),
):
    """Resolve a pending agent tool call with owner and grant validation."""
    await _require_agent(agent_id, user)
    owner = consent_broker.owner_of(tool_call_id)
    if owner is None or owner[1] != user["username"]:
        raise HTTPException(status_code=404, detail="Tool call not found")

    thread_id, _owner_name = owner
    runtime = thread_store.get(thread_id, user_name=user["username"])
    if runtime is None:
        raise HTTPException(status_code=404, detail="Tool call not found")

    if body.decision == "deny":
        resolved = consent_broker.resolve(tool_call_id, False, user_name=user["username"])
        return ConsentDecisionResponse(
            tool_call_id=tool_call_id,
            status="denied" if resolved else "cancelled",
            grant_active=False,
        )

    grant_active = False
    if body.decision == "allow_session":
        if consent_broker.classification_of(tool_call_id) != "read_only":
            raise HTTPException(
                status_code=400,
                detail="allow_session is only valid for a read-only tool call",
            )
        runtime.consent.always_allow_read_only = True
        grant_active = True

    resolved = consent_broker.resolve(tool_call_id, True, user_name=user["username"])
    return ConsentDecisionResponse(
        tool_call_id=tool_call_id,
        status="approved" if resolved else "cancelled",
        grant_active=grant_active,
    )
