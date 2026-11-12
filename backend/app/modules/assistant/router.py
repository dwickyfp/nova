"""Assistant API router — thread CRUD, the SSE turn endpoint, and consent.

Endpoints under ``/api/v1/assistant``:
  GET    /threads                                  → list the caller's threads
  POST   /threads                                  → create a thread
  GET    /threads/{thread_id}                      → thread + messages
  PATCH  /threads/{thread_id}                      → rename
  DELETE /threads/{thread_id}                      → delete
  POST   /threads/{thread_id}/messages             → run a turn (SSE stream)
  POST   /tool-calls/{tool_call_id}/decision       → resolve a pending tool call
  PUT    /threads/{thread_id}/grant                → set/clear the read-only grant
  DELETE /threads/{thread_id}/grant                → reset the conversation grant
  GET    /memories                                 → what Neva remembers (active role)
  DELETE /memories/{memory_id}                     → forget one remembered fact

Every route requires ``get_current_user``. Threads are scoped to their owner;
an unknown or foreign id answers 404, never 403, so existence does not leak.
The scoping is enforced in SQL (``repository.py``), so one user's history can
never be returned to another.

Threads and messages are persisted in ``NOVA_SYSTEM`` and survive a reload.
The read-only always-allow grant is kept in Redis per user, login and thread
(``read_grants.py``), stamped with the active role and security-context
version. Each turn reloads the grant, including expiry and revocation.
"""

# ruff: noqa: B008 — `Depends(...)` in a default is FastAPI's dependency
# injection idiom and is used by every router in this codebase; the rule's
# suggested "module-level singleton" rewrite would break the DI graph.

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.common.audit import write_audit_log
from app.core.database import configured_timezone
from app.core.deps import get_current_user
from app.modules.assistant import events, neva_memory, read_grants, ui_access
from app.modules.assistant.app_context import NevaApplicationEvent
from app.modules.assistant.application_events import application_event_broker
from app.modules.assistant.attachments import attachment_prompt
from app.modules.assistant.consent import ConsentApproval, consent_broker
from app.modules.assistant.history import history_repository
from app.modules.assistant.plain_language import plain_frames
from app.modules.assistant.provider import assistant_provider
from app.modules.assistant.repository import (
    AssistantThreadListUnavailable,
    assistant_repository,
)
from app.modules.assistant.schemas import (
    AttachmentView,
    ConsentDecisionRequest,
    ConsentDecisionResponse,
    GrantRequest,
    MessageRequest,
    MessageView,
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadListResponse,
    ThreadUpdateRequest,
    ThreadView,
    UiMapMetricInput,
    UiMediaInput,
    UiStateInput,
)
from app.modules.assistant.security import observation_context, session_security
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, thread_store
from app.modules.assistant.tools import ToolInvocation

logger = logging.getLogger(__name__)

router = APIRouter()

#: How Neva grounds business words; Neva only, after the shared SQL primer.
NEVA_GROUNDING = (
    "Business terms: when a question uses a business word (revenue, active customer, "
    "churn), first check its official meaning in the business glossary "
    "(ontology_resolve). Then answer from a Semantic View the active role can open, then "
    "from a verified query, and write new SQL only when none of these covers it; say "
    "where the answer comes from in plain words (Sources below). When a term has several "
    "official meanings, ask which one is meant instead of choosing, unless one reading is "
    "clearly meant or is the usual one: then answer with it, say which reading you used "
    "and offer the others. Never ask permission for a read-only lookup or calculation. A "
    "glossary suggestion (knowledge_propose) is only proposed until a steward approves it "
    "and a release is published: say proposed, never changed.\n"
    "Periods: before asking which period is meant, answer for the period the data covers, "
    "saying which dates it spans; a Semantic View answer over all the data reports those "
    "dates itself, so ask it for the measure only. If the comparison asked for is not "
    "possible (no data for last year), say so plainly. Name the dates the results give; "
    "do not tell the person that dates or data freshness could not be verified unless "
    "they asked about them.\n"
    "Sources: in an answer about business data, name the data it comes from by what it "
    "holds, in the person's language, the way the result's source line gives it (Retail "
    "Sales data, data penjualan ritel). Never write a view's internal name, the words "
    "Semantic View, a metric or column name with underscores, or SQL terms such as "
    "filter, join or query there; SQL the person asked to see or edit keeps its own words."
)


def _neva_loop(memory: str = "") -> AssistantLoop:
    """The shared engine with Studio's analyst limits for Neva.

    The limits are ceilings, not targets: a simple question still ends after
    one or two steps, and :func:`_neva_budget` lowers them once the plan shows
    a quick turn. Iterative mode lets Neva look further after a first result,
    and long conversations are summarized instead of cut. ``memory`` is what
    the person told Neva before, framed as untrusted data.
    """
    from app.modules.agents.service import BUDGET_PROFILES
    from app.modules.assistant.registry import build_neva_registry
    from app.modules.assistant.service import _default_skill_prompt

    limits = BUDGET_PROFILES["analyst"]
    return AssistantLoop(
        provider=assistant_provider,
        registry=build_neva_registry(),
        system_prompt=_default_skill_prompt() + "\n\n" + NEVA_GROUNDING
        + ("\n\n" + memory if memory else ""),
        max_iterations=limits.max_iterations,
        time_budget_seconds=float(limits.time_budget_seconds),
        max_calls_per_tool=limits.max_calls_per_tool,
        iterative=True,
        summarize_history=True,
        spend_limit=limits.spend_tokens,
    )


#: Slack on top of the loop's own time budget before Neva stops a stalled turn.
_STALL_MARGIN_SECONDS = 30.0


def _turn_ceiling() -> float:
    from app.modules.agents.service import BUDGET_PROFILES

    return float(BUDGET_PROFILES["analyst"].time_budget_seconds) + _STALL_MARGIN_SECONDS


async def _within_turn_budget(
    frames: AsyncIterator[str], context: Any, ceiling: float | None = None
) -> AsyncIterator[str]:
    """Pass the loop's frames on, ending a turn that outlives its time budget.

    The loop keeps its own deadline, but a step that blocks without returning
    (a remote catalog whose host cannot be resolved, say) would keep the person
    waiting with no answer. Time spent waiting for the person's approval does
    not count. When the ceiling passes, Neva says plainly that it stopped and
    the stalled work is cancelled without waiting for it.
    """
    from app.modules.assistant.messages import say
    from app.modules.assistant.service import _turn_language

    limit = _turn_ceiling() if ceiling is None else ceiling
    iterator = aiter(frames)
    used = 0.0
    awaiting_approval = False
    while True:
        step = asyncio.ensure_future(anext(iterator))
        started = asyncio.get_running_loop().time()
        done, _ = await asyncio.wait(
            {step}, timeout=None if awaiting_approval else max(0.0, limit - used)
        )
        if not awaiting_approval:
            used += asyncio.get_running_loop().time() - started
        if not done:
            step.cancel()
            yield events.text_delta(say("loop.timeout", _turn_language(context)))
            yield events.done(str(uuid4()), finish_reason="timeout")
            return
        try:
            frame = step.result()
        except StopAsyncIteration:
            return
        if frame.startswith(f"event: {events.EVENT_TOOL_CALL}\n"):
            awaiting_approval = _frame_data(frame).get("status") == "pending"
        elif frame.startswith(f"event: {events.EVENT_TOOL_STATUS}\n"):
            awaiting_approval = False
        yield frame


def _title_from(question: str, limit: int = 80) -> str:
    """The person's first question, on one line, as the conversation's name."""
    text = " ".join(question.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


#: Turns that explain, help or ask back: Studio's fast limits are enough.
_QUICK_INTENTS = frozenset({"direct_answer", "capability_help", "agent_catalog", "clarification"})
#: Background learning tasks, kept so they are not collected mid-flight.
_learning: set[asyncio.Task] = set()


def _neva_budget(route) -> tuple[int, float] | None:
    """Fast limits for a quick turn; analyst limits (the loop's own) otherwise."""
    from app.modules.agents.service import BUDGET_PROFILES

    if str(route.intent) not in _QUICK_INTENTS:
        return None
    fast = BUDGET_PROFILES["fast"]
    return fast.max_iterations, float(fast.time_budget_seconds)


async def _readable_views(context: LoopContext) -> list[str]:
    """The Semantic Views the caller's active role can open, checked once.

    The check runs as the caller through the Semantic View service; the loaded
    views stay on this turn's context, so the loop does not check them again.
    Nothing is kept for another request.
    """
    from app.modules.agents.semantic.access import load_authorized_models

    context.authorized_semantic_models = None
    context.semantic_view_ids = None
    try:
        models = await load_authorized_models(context)
    except Exception:  # noqa: BLE001 - fewer views, never other views
        logger.info("Neva could not list readable Semantic Views")
        context.authorized_semantic_models = []
        return []
    return [str(model["semantic_model_id"]) for model in models]


def _thread_view(row: dict) -> ThreadView:
    return ThreadView(
        thread_id=row["thread_id"],
        title=row["title"],
        workspace_file_id=row.get("workspace_file_id"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=row.get("message_count", 0),
    )


def _message_view(row: dict, user: dict | None = None) -> MessageView:
    # The same rules as Studio: interactive blocks only in their own security
    # context, no form input echoed as the person's words, public trace only.
    content, steps = ui_access.visible_message(row, user)
    return MessageView(
        message_id=row["message_id"],
        role=row["role"],
        content=content,
        tool_call=None,
        created_at=row["created_at"],
        steps=steps,
        prompt_tokens=row.get("prompt_tokens"),
        completion_tokens=row.get("completion_tokens"),
        total_tokens=row.get("total_tokens"),
        model_name=row.get("model_name"),
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


async def _require_thread(thread_id: str, user_name: str) -> dict:
    """Fetch a thread owned by ``user_name``, or 404.

    The owner filter is in SQL, so a foreign or unknown id is indistinguishable
    from a missing one — existence never leaks.
    """
    thread = await assistant_repository.get_thread(thread_id, user_name=user_name)
    if thread is None or thread.get("agent_id") is not None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


def _text_from_frame(frame: str) -> str:
    """Extract ``text`` from a ``text_delta`` SSE frame for the transcript.

    The frame is produced by ``events.text_delta`` in this process, so its shape
    is known; a malformed frame contributes nothing rather than raising inside
    the stream's ``finally``.
    """
    import json

    for line in frame.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip()).get("text", "")
            except (json.JSONDecodeError, AttributeError):
                return ""
    return ""


def _frame_data(frame: str) -> dict:
    import json

    for line in frame.splitlines():
        if line.startswith("data:"):
            try:
                value = json.loads(line[5:].strip())
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(
    user: dict = Depends(get_current_user),
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    try:
        if q and q.strip():
            # Search the caller's own conversations by title, newest first.
            needle = q.strip().casefold()
            found = [
                thread for thread in await assistant_repository.list_threads(
                    user_name=user["username"]
                )
                if needle in str(thread.get("title") or "").casefold()
            ][:50]
            return ThreadListResponse(
                threads=[_thread_view(t) for t in found], count=len(found), next_cursor=None,
            )
        if limit is not None or cursor is not None:
            threads, next_cursor = await history_repository.threads(
                user_name=user["username"], limit=limit or 50, cursor=cursor,
            )
            return ThreadListResponse(
                threads=[_thread_view(t) for t in threads], count=len(threads),
                next_cursor=next_cursor,
            )
        threads = await assistant_repository.list_threads(user_name=user["username"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AssistantThreadListUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Conversation history is temporarily unavailable"
        ) from exc
    views = [_thread_view(t) for t in threads]
    return ThreadListResponse(threads=views, count=len(views))


@router.post("/threads", response_model=ThreadView, status_code=201)
async def create_thread(
    body: ThreadCreateRequest,
    user: dict = Depends(get_current_user),
):
    thread = await assistant_repository.create_thread(
        user_name=user["username"],
        title=body.title,
        workspace_file_id=body.workspace_file_id,
    )
    # Register the runtime object so the first turn has a consent holder.
    thread_store.register(
        thread_id=thread["thread_id"],
        user_name=user["username"],
        title=thread["title"],
        workspace_file_id=thread.get("workspace_file_id"),
    )
    return _thread_view(thread)


@router.get("/threads/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: str,
    user: dict = Depends(get_current_user),
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
):
    thread = await _require_thread(thread_id, user["username"])
    if limit is not None or cursor is not None:
        try:
            messages, next_cursor = await history_repository.messages(
                thread_id, user_name=user["username"], limit=limit or 50, cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AssistantThreadListUnavailable as exc:
            raise HTTPException(
                status_code=503, detail="Conversation history is temporarily unavailable"
            ) from exc
        return ThreadDetailResponse(
            thread=_thread_view(thread), messages=[_message_view(m, user) for m in messages],
            next_cursor=next_cursor,
        )
    messages = await assistant_repository.list_messages(
        thread_id, user_name=user["username"]
    )
    return ThreadDetailResponse(
        thread=_thread_view(thread),
        messages=[_message_view(m, user) for m in messages],
    )


@router.post("/threads/{thread_id}/application-events")
async def publish_application_event(
    thread_id: str,
    body: NevaApplicationEvent,
    user: dict = Depends(get_current_user),
):
    """Acknowledge an application action and retain bounded outcome evidence."""
    await _require_thread(thread_id, user["username"])
    verification = application_event_broker.publish(thread_id, user["username"], body)
    return {
        "recorded": verification != "unmatched"
        or body.type not in {"ui_action_completed", "ui_action_failed"},
        "correlation_id": body.correlation_id,
        "verification": verification,
    }


@router.patch("/threads/{thread_id}", response_model=ThreadView)
async def rename_thread(
    thread_id: str,
    body: ThreadUpdateRequest,
    user: dict = Depends(get_current_user),
):
    thread = await _require_thread(thread_id, user["username"])
    if body.title is not None:
        updated = await assistant_repository.rename_thread(
            thread_id, body.title, user_name=user["username"]
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        thread = updated
    return _thread_view(thread)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    await _require_thread(thread_id, user["username"])
    if not await assistant_repository.delete_thread(
        thread_id, user_name=user["username"]
    ):
        raise HTTPException(status_code=404, detail="Thread not found")
    # Drop the runtime entry too, so a lingering grant cannot outlive the thread.
    thread_store.remove(thread_id, user_name=user["username"])
    application_event_broker.remove(thread_id, user["username"])
    await read_grants.remember(user, thread_id, False)
    return None


@router.get("/threads/{thread_id}/messages/{message_id}/ui-state/{content_id}")
async def get_ui_state(thread_id: str, message_id: str, content_id: str,
                       user: dict = Depends(get_current_user)):
    """State of an interactive answer block in the caller's own conversation."""
    await _require_thread(thread_id, user["username"])
    return await ui_access.get_state(thread_id, message_id, content_id, user)


@router.put("/threads/{thread_id}/messages/{message_id}/ui-state/{content_id}")
async def put_ui_state(thread_id: str, message_id: str, content_id: str, body: UiStateInput,
                       user: dict = Depends(get_current_user)):
    await _require_thread(thread_id, user["username"])
    return await ui_access.put_state(
        thread_id, message_id, content_id, user, program_sha256=body.program_sha256,
        revision=body.revision, state=body.state,
    )


@router.post("/threads/{thread_id}/messages/{message_id}/ui-state/{content_id}/media")
async def get_ui_media(thread_id: str, message_id: str, content_id: str, body: UiMediaInput,
                       user: dict = Depends(get_current_user)):
    await _require_thread(thread_id, user["username"])
    return await ui_access.media(
        thread_id, message_id, content_id, user, media_type=body.type, props=body.props,
    )


@router.post("/threads/{thread_id}/messages/{message_id}/ui-state/{content_id}/map-metric")
async def record_map_metric(thread_id: str, message_id: str, content_id: str,
                            body: UiMapMetricInput, user: dict = Depends(get_current_user)):
    await _require_thread(thread_id, user["username"])
    return await ui_access.map_metric(
        thread_id, message_id, content_id, user, event=body.event, elapsed_ms=body.elapsed_ms,
    )


@router.get("/memories")
async def list_memories(user: dict = Depends(get_current_user)):
    """What Neva remembers for the caller under the active role."""
    from app.modules.agents.memory import governed_memories, memory_repository

    memories = await memory_repository.list(
        user_name=user["username"], agent_id=neva_memory.NEVA_MEMORY_ID,
        role_name=session_security(user).active_role, limit=100,
    )
    visible = await governed_memories(memories, user)
    return {
        "memories": [
            {"memory_id": item["memory_id"], "fact": item["fact"],
             "updated_at": item.get("updated_at")}
            for item in visible
        ],
    }


@router.delete("/memories/{memory_id}", status_code=204)
async def forget_memory(memory_id: str, user: dict = Depends(get_current_user)):
    """Remove one remembered fact; only the caller's own, under the active role."""
    from app.modules.agents.memory import memory_repository

    security = session_security(user)
    deleted = await memory_repository.delete(
        memory_id, user_name=user["username"], agent_id=neva_memory.NEVA_MEMORY_ID,
        role_name=security.active_role,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    await write_audit_log(
        event_type="AGENT_MEMORY", user_name=user["username"], action="DELETE",
        object_type="AGENT_MEMORY", object_name=memory_id, status="SUCCESS",
        session_id=user.get("session_id"), active_role=security.active_role,
        security_context_version=security.security_context_version,
    )
    return None


@router.delete("/threads/{thread_id}/grant", status_code=204)
async def reset_grant(
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    """Revoke the conversation's read-only always-allow grant (E2b)."""
    await _require_thread(thread_id, user["username"])
    runtime = thread_store.get(thread_id, user_name=user["username"])
    if runtime is not None:
        await read_grants.grant(runtime, user, thread_id, False)
    else:
        await read_grants.remember(user, thread_id, False)
    return None


@router.put("/threads/{thread_id}/grant")
async def set_grant(
    thread_id: str,
    body: GrantRequest,
    user: dict = Depends(get_current_user),
):
    """Set or clear the conversation's read-only always-allow grant.

    The composer's approval-mode selector calls this before a turn so a
    read-only query runs without a per-call approval card. This is the UI
    presenting the grant up front, which is what NOVA-122 requires: the grant
    is only ever the read-only policy (never a way to auto-approve a
    destructive call), and the loop still consults the per-statement
    classification before skipping a card. Setting ``false`` is equivalent to
    the ``DELETE`` above.

    Owner-scoped: a foreign or unknown thread answers 404, never 403.

    The runtime entry is registered here (not only on the next turn) so the
    grant takes effect even if the user sets the mode before the first message
    of a fresh thread. Registering is idempotent — an existing entry is reused,
    so a live grant is never reset by this call.
    """
    thread_row = await _require_thread(thread_id, user["username"])
    runtime = thread_store.register(
        thread_id=thread_id,
        user_name=user["username"],
        title=thread_row["title"],
        workspace_file_id=thread_row.get("workspace_file_id"),
    )
    await read_grants.grant(runtime, user, thread_id, body.always_allow_read_only)
    return {"grant_active": runtime.consent.covers("read_only", user=user)}


@router.post("/threads/{thread_id}/messages")
async def send_message(
    thread_id: str,
    body: MessageRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Run one assistant turn and stream it as SSE.

    The user's message is stored before the stream starts, so an interrupted
    turn still leaves a coherent transcript. The assistant's reply is stored as
    one message when the stream ends. Both writes are user-scoped in SQL.
    """
    security = session_security(user)
    stamp = observation_context(security)
    thread_row = await _require_thread(thread_id, user["username"])
    user_name = user["username"]

    # The runtime object the loop reads: history loaded from the database plus
    # the process-local consent policy. Registered (not recreated) so a live
    # always-allow grant survives across turns.
    runtime = thread_store.register(
        thread_id=thread_id,
        user_name=user_name,
        title=thread_row["title"],
        workspace_file_id=thread_row.get("workspace_file_id"),
    )
    # The read-only grant follows the caller's current role and session.
    await read_grants.apply(runtime, user, thread_id)
    history = await assistant_repository.list_messages(thread_id, user_name=user_name)
    if thread_row.get("title") == "New conversation" and body.content.strip() and not any(
        row["role"] == "user" for row in history
    ):
        # The first question names the conversation, so history can be found.
        try:
            await assistant_repository.rename_thread(
                thread_id, _title_from(body.content), user_name=user_name,
            )
        except Exception:  # noqa: BLE001 - a title is not worth failing a turn
            logger.info("Could not name the conversation")
    runtime.messages = [
        AssistantMessage(
            message_id=row["message_id"],
            role=row["role"],
            # Earlier files stay part of what the person said, as data.
            content=attachment_prompt(row["content"], row.get("attachments") or [])
            if row["role"] == "user" else row["content"],
            steps=row.get("steps") or [],
            created_at=row["created_at"],
            security_context=row.get("security_context"),
            attachments=row.get("attachments") or [],
        )
        for row in history
    ]

    # Store the user's message before the stream starts, so a disconnect still
    # leaves a coherent transcript.
    attachments = body.prepared_attachments
    ui_trace: list[dict] = []
    if body.ui_action is not None:
        # A button or form of an earlier answer: accepted once, as user input.
        body.content, ui_step = await ui_access.accept_action(
            thread_id, body.ui_action, body.content, user,
        )
        ui_trace.append(ui_step)
    prompt = attachment_prompt(body.content, attachments)
    await assistant_repository.append_message(
        thread_id, user_name=user_name, role="user", content=body.content,
        security_context=stamp, attachments=attachments, steps=ui_trace,
    )
    if attachments:
        await write_audit_log(
            event_type="ASSISTANT_ATTACHMENT", user_name=user_name, action="send",
            object_type="ASSISTANT_THREAD", object_name=thread_id, status="SUCCESS",
            session_id=user.get("session_id"), active_role=security.active_role,
            security_context_version=security.security_context_version,
        )
    runtime.messages.append(
        AssistantMessage(
            message_id=str(uuid4()), role="user", content=prompt, security_context=stamp,
            attachments=attachments,
        )
    )

    app_domain = body.app_context.domain if body.app_context else None
    app_context = body.app_context
    if app_context is not None:
        merged_events = {
            event.id: event for event in app_context.current_events()
        }
        for event in application_event_broker.recent_events(
            thread_id, user_name, app_context.surface.id
        ):
            merged_events[event.id] = event
        app_context = app_context.model_copy(update={"events": list(merged_events.values())[-12:]})
    context = LoopContext(
        user_name=user_name,
        # Interactive answers (tables, charts, forms) as in Studio.
        intelligent_ui=True,
        ui_action_accepted=body.ui_action is not None,
        ui_private_context=bool(body.ui_action) or any(
            step.get("kind") == "ui_action" for row in history for step in row.get("steps") or []
        ),
        execution_timezone=configured_timezone(),
        database=body.database or (app_domain.database if app_domain else None),
        schema_name=body.schema_name or (app_domain.schema_name if app_domain else None),
        role=security.active_role,
        workspace_file_id=thread_row.get("workspace_file_id"),
        session_id=user.get("session_id"),
        thread_id=thread_id,
        user=user,
        app_context=app_context,
        # After an approved role switch the turn continues under the new role.
        readable_views=_readable_views,
        scale_budget=_neva_budget,
    )
    context.semantic_view_ids = await _readable_views(context)
    memory = await neva_memory.memory_note(user, body.content)

    def resolve_consent(
        invocation: ToolInvocation, classification: str
    ) -> asyncio.Future[bool | None | ConsentApproval]:
        # Registration precedes publication so an immediate decision can resolve.
        future = consent_broker.open(
            invocation.tool_call_id,
            thread_id=thread_id,
            user_name=user_name,
            classification=classification,
            secure_fields=(
                ("password",)
                if invocation.tool_name == "provision_user"
                else ()
            ),
        )

        async def _watch_disconnect() -> None:
            while not future.done():
                if await request.is_disconnected():
                    # The owner's stream went away: cancel the pending call as
                    # its owner, so the owner check is satisfied and the loop
                    # is released with ``None`` (cancelled).
                    consent_broker.resolve(
                        invocation.tool_call_id, None, user_name=user_name
                    )
                    return
                await asyncio.sleep(0.25)

        watcher = asyncio.ensure_future(_watch_disconnect())
        def clear_pending(_future: asyncio.Future) -> None:
            watcher.cancel()

        future.add_done_callback(clear_pending)
        return future

    async def generate() -> AsyncIterator[str]:
        reply_parts: list[str] = []
        reply_stamp = stamp
        # The reply is stored under the id the client receives in ``done``, and
        # ``done`` is sent only after it is stored, so the panel can open the
        # answer's interactive blocks at once.
        reply_message_id = str(uuid4())
        done_frame: str | None = None
        persisted = False

        async def persist() -> None:
            nonlocal persisted
            if persisted:
                return
            persisted = True
            if not runtime.consent.always_allow_read_only:
                # A role switch during the turn took the grant back.
                await read_grants.remember(user, thread_id, False)
            if body.content.strip() and reply_parts:
                # Durable facts the person stated are kept for later turns,
                # after the answer, so the reply is never slower for it.
                task = asyncio.create_task(neva_memory.learn(
                    context.user or user, thread_id=thread_id, message=body.content,
                    provider_id=body.provider_id, model=body.model,
                ))
                _learning.add(task)
                task.add_done_callback(_learning.discard)
            if reply_parts or context.steps or context.pending_output:
                # Best effort: a storage failure must not turn a completed
                # answer into a stream error the user cannot see the cause of.
                try:
                    steps = list(context.steps or [])
                    if context.pending_output:
                        steps.extend(context.pending_output)
                    await assistant_repository.append_message(
                        thread_id,
                        user_name=user_name,
                        role="assistant",
                        message_id=reply_message_id,
                        content="".join(reply_parts),
                        model_name=body.model,
                        usage=context.usage,
                        steps=steps,
                        instructions=context.instructions,
                        security_context=(
                            reply_stamp if reply_stamp == observation_context(
                                session_security(context.user or {})
                            ) else None
                        ),
                    )
                except Exception:
                    logger.exception("Could not persist the assistant reply")

        # Neva speaks to people who look after Nova: internal names never reach
        # the screen or the stored transcript.
        plain = plain_frames()
        frames = _within_turn_budget(_neva_loop(memory).run(
            thread=runtime,
            user_content=prompt,
            context=context,
            resolve_consent=resolve_consent,
            cancelled=lambda: False,
            model=body.model,
            provider_id=body.provider_id,
        ), context)
        try:
            async for raw in frames:
                if await request.is_disconnected():
                    break
                for frame in plain(raw):
                    if frame.startswith(f"event: {events.EVENT_TEXT_DELTA}"):
                        reply_parts.append(_text_from_frame(frame))
                    if frame.startswith(f"event: {events.EVENT_CLIENT_ACTION}"):
                        application_event_broker.register_action(
                            thread_id, user_name, _frame_data(frame)
                        )
                    if frame.startswith("event: role_changed\n"):
                        reply_parts.clear()
                        reply_stamp = observation_context(
                            session_security(context.user or {})
                        )
                    if frame.startswith(f"event: {events.EVENT_DONE}\n"):
                        reply_message_id = str(
                            _frame_data(frame).get("message_id") or reply_message_id
                        )
                        done_frame = frame
                        continue
                    yield frame
            for frame in plain.flush():
                reply_parts.append(_text_from_frame(frame))
                yield frame
        except Exception:  # noqa: BLE001 - never leak an internal trace
            logger.exception("Assistant stream failed")
            yield events.error(
                "internal_error", "Neva could not finish this answer. Please try again."
            )
            done_frame = events.done(reply_message_id, finish_reason="error")
        finally:
            # Runs on a disconnect too: the reply is kept either way.
            await persist()
        if done_frame is not None:
            yield done_frame

    return StreamingResponse(
        generate(),
        media_type=events.SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/tool-calls/{tool_call_id}/decision",
    response_model=ConsentDecisionResponse,
)
async def resolve_tool_call(
    tool_call_id: str,
    body: ConsentDecisionRequest,
    user: dict = Depends(get_current_user),
):
    """Resolve a pending tool call.

    Owner-scoped: only the user who owns the conversation that proposed the call
    may approve or deny it. A foreign or unknown id answers 404 (never 403), so
    existence does not leak — the same rule the rest of this router follows.
    Without this, any authenticated user who learned a ``tool_call_id`` could
    approve another user's call (NOVA-70).

    ``allow_session`` sets the conversation's read-only grant, and is accepted
    only when the pending call was classified ``read_only`` (spec §6.1). On any
    other class it answers 400 and sets nothing, so a direct API caller cannot
    record a grant the UI never presented (NOVA-122). The grant itself still
    only covers read-only statements — it is not a switch the client can use to
    auto-approve a destructive call (E2b).
    """
    owner = consent_broker.owner_of(tool_call_id)
    if owner is None or owner[1] != user["username"]:
        # Unknown id, already resolved, or someone else's pending call.
        raise HTTPException(status_code=404, detail="Tool call not found")

    thread_id, _owner_name = owner
    # The broker already proved ownership; resolve only against the owner's own
    # thread so the grant can never land on a foreign conversation.
    thread = thread_store.get(thread_id, user_name=user["username"])
    if thread is None:
        raise HTTPException(status_code=404, detail="Tool call not found")

    if body.decision == "deny":
        if body.secure_input:
            raise HTTPException(status_code=400, detail="Secure input is not valid for denial")
        resolved = consent_broker.resolve(
            tool_call_id, False, user_name=user["username"]
        )
        return ConsentDecisionResponse(
            tool_call_id=tool_call_id,
            status="denied" if resolved else "cancelled",
            grant_active=thread.consent.covers("read_only", user=user),
        )

    secure_fields = consent_broker.secure_fields_of(tool_call_id) or ()
    if consent_broker.upload_required_of(tool_call_id):
        raise HTTPException(status_code=400, detail="Choose a file for this action")
    if body.secure_input is not None:
        if (
            body.decision != "allow_once"
            or set(body.secure_input) != set(secure_fields)
            or any(not value or len(value) > 1024 for value in body.secure_input.values())
        ):
            raise HTTPException(status_code=400, detail="Invalid secure input")
    elif secure_fields and body.decision == "allow_once":
        raise HTTPException(status_code=400, detail="Secure input is required")
    if body.decision == "allow_session":
        # The grant only covers read-only statements (spec §6.1), and the UI
        # only offers always-allow for a read-only call. Refuse a session grant
        # on any other class rather than record consent the user's UI never
        # presented (NOVA-122). The call itself is left pending: the client can
        # still resolve it with allow_once or deny.
        if consent_broker.classification_of(tool_call_id) != "read_only":
            raise HTTPException(
                status_code=400,
                detail="allow_session is only valid for a read-only tool call",
            )
        await read_grants.grant(thread, user, thread_id, True)

    resolved = consent_broker.resolve(
        tool_call_id,
        ConsentApproval(body.secure_input) if body.secure_input is not None else True,
        user_name=user["username"],
    )
    return ConsentDecisionResponse(
        tool_call_id=tool_call_id,
        status="approved" if resolved else "cancelled",
        grant_active=thread.consent.covers("read_only", user=user),
    )


@router.post(
    "/tool-calls/{tool_call_id}/upload-decision",
    response_model=ConsentDecisionResponse,
)
async def resolve_upload_tool_call(
    tool_call_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Approve one stage upload with browser-owned bytes outside the model context."""
    owner = consent_broker.owner_of(tool_call_id)
    if owner is None or owner[1] != user["username"]:
        raise HTTPException(status_code=404, detail="Tool call not found")
    thread = thread_store.get(owner[0], user_name=user["username"])
    if thread is None:
        raise HTTPException(status_code=404, detail="Tool call not found")
    if not consent_broker.upload_required_of(tool_call_id):
        raise HTTPException(status_code=400, detail="A file is not valid for this action")

    from app.modules.stages.service import stage_service

    filename = file.filename or ""
    try:
        if stage_service._safe_relative_path(filename) != filename:
            raise ValueError("Filename must be normalized")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid filename") from exc

    max_bytes = 256 * 1024 * 1024
    total = 0
    # The paused tool consumes this stream after this request returns.
    stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)  # noqa: SIM115
    try:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="File exceeds the 256 MB limit")
            stream.write(chunk)
        stream.seek(0)
        resolved = consent_broker.resolve(
            tool_call_id,
            ConsentApproval(
                upload=(filename, stream, file.content_type or "application/octet-stream")
            ),
            user_name=user["username"],
        )
        if not resolved:
            raise HTTPException(status_code=404, detail="Tool call not found")
    except BaseException:
        stream.close()
        raise
    return ConsentDecisionResponse(
        tool_call_id=tool_call_id,
        status="approved",
        grant_active=thread.consent.covers("read_only", user=user),
    )
