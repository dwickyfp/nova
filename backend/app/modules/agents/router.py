"""Agent Studio API router — agent CRUD, semantic models, skills, Nova Studio runs.

Endpoints under ``/api/v1/agents``:
  GET    /agents                                  → list the caller's agents
  POST   /agents                                  → create an agent
  GET    /agents/{agent_id}                       → agent detail
  PUT    /agents/{agent_id}                       → update an agent
  DELETE /agents/{agent_id}                       → delete an agent
  GET    /agents/semantic-models                  → list semantic models
  POST   /agents/semantic-models                  → create (Ossie document)
  GET    /agents/semantic-models/{model_id}       → detail
  DELETE /agents/semantic-models/{model_id}       → delete
  POST   /agents/semantic-models/validate         → validate without saving
  GET    /agents/skills                           → list user skills
  POST   /agents/skills                           → create a SKILL.md-compatible skill
  DELETE /agents/skills/{skill_id}                → delete a skill

Every route requires ``get_current_user`` and is scoped to the caller. An
unknown or foreign id answers **404**, never 403, so existence does not leak.

Stage status: agent CRUD is N12-B2. Semantic models and skills are N12-C/G; this
router wires them, and the semantic parser is added in N12-C1.
"""

# ruff: noqa: B008 — `Depends(...)` in a default is FastAPI's dependency
# injection idiom used by every router in this codebase.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.modules.agents.repository import agent_repository
from app.modules.agents.schemas import (
    AgentCreateRequest,
    AgentListResponse,
    AgentUpdateRequest,
    AgentView,
    SemanticModelCreateRequest,
    SemanticModelListResponse,
    SemanticModelView,
    SemanticValidateRequest,
    SemanticValidateResponse,
    SkillCreateRequest,
    SkillListResponse,
    SkillView,
)
from app.modules.agents.service import agent_service
from app.modules.assistant import events
from app.modules.assistant.consent import consent_broker
from app.modules.assistant.context import ContextManager
from app.modules.assistant.provider import assistant_provider
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.schemas import (
    ConsentDecisionRequest,
    ConsentDecisionResponse,
    MessageRequest,
    MessageView,
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadListResponse,
    ThreadUpdateRequest,
    ThreadView,
)
from app.modules.assistant.service import (
    DEFAULT_MAX_ITERATIONS,
    AssistantLoop,
    LoopContext,
)
from app.modules.assistant.state import AssistantMessage, thread_store
from app.modules.assistant.tools import ToolInvocation

logger = logging.getLogger(__name__)

router = APIRouter()

#: The v1 tool surface an agent may bundle. A name outside this set is rejected
#: before storage so a typo cannot silently grant nothing.
KNOWN_TOOLS = {
    "load_skill",
    "query_execute",
    "semantic_query",
    "semantic_search",
    "data_to_chart",
}


def _unknown_tools(tools: list) -> list[str]:
    """Tool names a request carries that are not a builtin or ``custom:<name>``.

    Custom tools are selected by name with a ``custom:`` prefix; their existence
    is resolved at run time (a deleted tool is skipped, not rejected here).
    """
    return [
        t
        for t in tools
        if t not in KNOWN_TOOLS and not (isinstance(t, str) and t.startswith("custom:"))
    ]


def _agent_view(row: dict) -> AgentView:
    return AgentView(**row)


def _semantic_view(row: dict) -> SemanticModelView:
    return SemanticModelView(**row)


def _skill_view(row: dict) -> SkillView:
    return SkillView(**row)


async def _require_agent(agent_id: str, user_name: str) -> dict:
    agent = await agent_repository.get_agent(agent_id, owner_name=user_name)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def _resolve_database(agent: dict) -> str | None:
    """The default database a turn's SQL runs against.

    An explicit ``database_name`` wins. Otherwise it is derived from the first
    bound semantic model's datasets, so the user never types a database name:
    the model already knows which database its tables live in. ``None`` means
    the caller's session default applies.
    """
    explicit = agent.get("database_name")
    if explicit:
        return explicit
    owner = agent.get("owner_name")
    if not owner:
        return None
    ids = agent.get("semantic_model_ids") or (
        [agent["semantic_model_id"]] if agent.get("semantic_model_id") else []
    )
    for model_id in ids:
        model = await agent_repository.get_semantic_model(model_id, owner_name=owner)
        if not model:
            continue
        datasets = (model.get("definition") or {}).get("datasets") or []
        for dataset in datasets:
            source = dataset.get("source")
            if isinstance(source, str) and "." in source:
                return source.split(".")[0]
    return None


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


# ── Agents ─────────────────────────────────────────────────────


@router.get("", response_model=AgentListResponse)
async def list_agents(
    database: str | None = None,
    search: str | None = None,
    user: dict = Depends(get_current_user),
):
    agents = await agent_repository.list_agents(
        owner_name=user["username"], database_name=database, search=search
    )
    views = [_agent_view(a) for a in agents]
    return AgentListResponse(agents=views, count=len(views))


@router.post("", response_model=AgentView, status_code=201)
async def create_agent(
    body: AgentCreateRequest,
    user: dict = Depends(get_current_user),
):
    fields = body.model_dump()
    unknown = _unknown_tools(fields.get("default_tools", []))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown tool(s): {', '.join(sorted(unknown))}"
        )
    created = await agent_repository.create_agent(owner_name=user["username"], fields=fields)
    return _agent_view(created)


# ── Semantic models ────────────────────────────────────────────
#
# Declared before the dynamic ``/{agent_id}`` routes at the bottom of this file:
# FastAPI matches in declaration order, so a literal first segment
# ("semantic-models", "skills") must be registered before ``/{agent_id}`` or the
# dynamic route would capture it as an agent id.


@router.get("/semantic-models", response_model=SemanticModelListResponse)
async def list_semantic_models(user: dict = Depends(get_current_user)):
    models = await agent_repository.list_semantic_models(owner_name=user["username"])
    views = [_semantic_view(m) for m in models]
    return SemanticModelListResponse(models=views, count=len(views))


@router.post("/semantic-models", response_model=SemanticModelView, status_code=201)
async def create_semantic_model(
    body: SemanticModelCreateRequest,
    user: dict = Depends(get_current_user),
):
    from app.modules.agents.semantic.ossie import OssieParseError, parse_ossie

    try:
        parsed = parse_ossie(body.definition)
    except OssieParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    created = await agent_repository.create_semantic_model(
        owner_name=user["username"],
        fields={
            "name": body.name,
            "description": body.description,
            "database_name": body.database_name,
            "schema_name": body.schema_name,
            "ossie_version": parsed.version,
            "definition": parsed.as_dict(),
            "source_file_id": body.source_file_id,
        },
    )
    return _semantic_view(created)


@router.post("/semantic-models/validate", response_model=SemanticValidateResponse)
async def validate_semantic_model(
    body: SemanticValidateRequest,
    user: dict = Depends(get_current_user),
):
    del user  # validation is pure; the dependency enforces authentication only
    from app.modules.agents.semantic.ossie import parse_ossie

    result = parse_ossie(body.definition, raise_on_error=False)
    return SemanticValidateResponse(
        valid=result.valid,
        ossie_version=result.version,
        errors=result.errors,
        warnings=result.warnings,
        dataset_count=result.dataset_count,
        metric_count=result.metric_count,
        relationship_count=result.relationship_count,
    )


@router.get("/semantic-models/{model_id}", response_model=SemanticModelView)
async def get_semantic_model(model_id: str, user: dict = Depends(get_current_user)):
    model = await agent_repository.get_semantic_model(model_id, owner_name=user["username"])
    if model is None:
        raise HTTPException(status_code=404, detail="Semantic model not found")
    return _semantic_view(model)


@router.delete("/semantic-models/{model_id}", status_code=204)
async def delete_semantic_model(model_id: str, user: dict = Depends(get_current_user)):
    if not await agent_repository.delete_semantic_model(model_id, owner_name=user["username"]):
        raise HTTPException(status_code=404, detail="Semantic model not found")
    return None


# ── Skills ─────────────────────────────────────────────────────


@router.get("/skills", response_model=SkillListResponse)
async def list_skills(user: dict = Depends(get_current_user)):
    skills = await agent_repository.list_skills(owner_name=user["username"])
    views = [_skill_view(s) for s in skills]
    return SkillListResponse(skills=views, count=len(views))


@router.post("/skills", response_model=SkillView, status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    user: dict = Depends(get_current_user),
):
    from app.modules.assistant.skills import contains_credential_shape

    if contains_credential_shape(body.body):
        raise HTTPException(
            status_code=422, detail="Skill body contains a credential-shaped value."
        )
    created = await agent_repository.create_skill(
        owner_name=user["username"], fields=body.model_dump()
    )
    return _skill_view(created)


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(skill_id: str, user: dict = Depends(get_current_user)):
    if not await agent_repository.delete_skill(skill_id, owner_name=user["username"]):
        raise HTTPException(status_code=404, detail="Skill not found")
    return None


# ── Agents by id (dynamic — declared last) ─────────────────────
#
# These carry a dynamic ``{agent_id}`` segment, so they must come after every
# literal-prefix route above ("semantic-models", "skills"). FastAPI matches in
# declaration order; registering these earlier would let ``/semantic-models`` be
# captured as an agent id.


@router.get("/{agent_id}", response_model=AgentView)
async def get_agent(agent_id: str, user: dict = Depends(get_current_user)):
    agent = await _require_agent(agent_id, user["username"])
    return _agent_view(agent)


@router.put("/{agent_id}", response_model=AgentView)
async def update_agent(
    agent_id: str,
    body: AgentUpdateRequest,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user["username"])
    fields = body.model_dump(exclude_unset=True)
    unknown = _unknown_tools(fields.get("default_tools", []))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown tool(s): {', '.join(sorted(unknown))}"
        )
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
    return None


# ── Nova Studio runs (agent-scoped threads + the SSE turn) ─────


@router.get("/{agent_id}/threads", response_model=ThreadListResponse)
async def list_agent_threads(agent_id: str, user: dict = Depends(get_current_user)):
    await _require_agent(agent_id, user["username"])
    threads = await assistant_repository.list_threads(user_name=user["username"], agent_id=agent_id)
    views = [_thread_view(t) for t in threads]
    return ThreadListResponse(threads=views, count=len(views))


@router.post("/{agent_id}/threads", response_model=ThreadView, status_code=201)
async def create_agent_thread(
    agent_id: str,
    body: ThreadCreateRequest,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user["username"])
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
    await _require_agent(agent_id, user["username"])
    thread = await _require_agent_thread(thread_id, agent_id, user["username"])
    messages = await assistant_repository.list_messages(thread_id, user_name=user["username"])
    return ThreadDetailResponse(
        thread=_thread_view(thread),
        messages=[_message_view(m) for m in messages],
    )


@router.delete("/{agent_id}/threads/{thread_id}", status_code=204)
async def delete_agent_thread(
    agent_id: str,
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    await _require_agent(agent_id, user["username"])
    await _require_agent_thread(thread_id, agent_id, user["username"])
    if not await assistant_repository.delete_thread(thread_id, user_name=user["username"]):
        raise HTTPException(status_code=404, detail="Thread not found")
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
    await _require_agent(agent_id, user["username"])
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


@router.post("/{agent_id}/threads/{thread_id}/messages")
async def send_agent_message(
    agent_id: str,
    thread_id: str,
    body: MessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Run one agent turn and stream it as SSE.

    Composes the Phase 10 bounded loop with the agent's own registry and system
    prompt. Everything the loop guarantees — consent, iteration cap, redaction,
    delegate-first execution — is unchanged; only the configuration differs.
    """
    agent = await _require_agent(agent_id, user["username"])
    user_name = user["username"]
    thread_row = await _require_agent_thread(thread_id, agent_id, user_name)

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
    runtime.messages = [
        AssistantMessage(
            message_id=row["message_id"],
            role=row["role"],
            content=row["content"],
            steps=row.get("steps") or [],
            created_at=row["created_at"],
        )
        for row in history
    ]

    await assistant_repository.append_message(
        thread_id, user_name=user_name, role="user", content=body.content
    )
    runtime.messages.append(
        AssistantMessage(message_id=str(uuid4()), role="user", content=body.content)
    )

    # The first question names the conversation. A thread created as "New chat"
    # and never renamed is unfindable in a history list. The agent's model
    # summarizes the question into a concise title; if the call is unavailable,
    # the bounded question itself is the honest fallback.
    if not any(row["role"] == "user" for row in history):
        try:
            title = await _generate_thread_title(
                body.content,
                provider_id=agent.get("model_provider_id"),
                model=agent.get("model_name"),
            )
            await assistant_repository.rename_thread(
                thread_id, title, user_name=user_name
            )
        except Exception:  # noqa: BLE001 - a title is not worth failing a turn
            logger.warning("Could not set the thread title")

    context = LoopContext(
        user_name=user_name,
        database=await _resolve_database(agent),
        schema_name=agent.get("schema_name"),
        role=body.role,
        workspace_file_id=thread_row.get("workspace_file_id"),
        session_id=user.get("session_id"),
        thread_id=thread_id,
        user=user,
        agent_id=agent_id,
        semantic_model_id=agent.get("semantic_model_id"),
        semantic_model_ids=agent.get("semantic_model_ids") or [],
        model_provider_id=agent.get("model_provider_id"),
        model_name=agent.get("model_name"),
        instructions=system_prompt,
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

        async def _watch_disconnect() -> None:
            while not future.done():
                if await request.is_disconnected():
                    consent_broker.resolve(invocation.tool_call_id, None, user_name=user_name)
                    return
                await asyncio.sleep(0.25)

        watcher = asyncio.ensure_future(_watch_disconnect())
        try:
            return await future
        finally:
            watcher.cancel()

    async def generate() -> AsyncIterator[str]:
        reply_parts: list[str] = []
        try:
            async for frame in loop.run(
                thread=runtime,
                user_content=body.content,
                context=context,
                resolve_consent=resolve_consent,
                cancelled=lambda: False,
                model=body.model,
                provider_id=body.provider_id,
            ):
                if await request.is_disconnected():
                    break
                if frame.startswith(f"event: {events.EVENT_TEXT_DELTA}"):
                    reply_parts.append(_text_from_frame(frame))
                yield frame
        except Exception as exc:  # noqa: BLE001 - never leak an internal trace
            logger.exception("Agent stream failed")
            yield events.error("internal_error", f"The agent failed: {type(exc).__name__}")
            yield events.done(str(uuid4()), finish_reason="error")
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
                        content="".join(reply_parts),
                        agent_id=agent_id,
                        model_name=agent.get("model_name"),
                        usage=context.usage,
                        steps=steps,
                        instructions=context.instructions,
                    )
                except Exception:
                    logger.exception("Could not persist the agent reply")

    return StreamingResponse(
        generate(),
        media_type=events.SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
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
    await _require_agent(agent_id, user["username"])
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
