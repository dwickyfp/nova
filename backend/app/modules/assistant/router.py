"""Assistant API router — thread CRUD, the SSE turn endpoint, and consent.

Endpoints under ``/api/v1/assistant``:
  GET    /threads                                  → list the caller's threads
  POST   /threads                                  → create a thread
  GET    /threads/{thread_id}                      → thread + messages
  PATCH  /threads/{thread_id}                      → rename
  DELETE /threads/{thread_id}                      → delete
  POST   /threads/{thread_id}/messages             → run a turn (SSE stream)
  POST   /tool-calls/{tool_call_id}/decision       → resolve a pending tool call
  DELETE /threads/{thread_id}/grant                → reset the conversation grant

Every route requires ``get_current_user``. Threads are scoped to their owner;
an unknown or foreign id answers 404, never 403, so existence does not leak.

State is process-local (E5a): a restart clears threads and grants.
"""

# ruff: noqa: B008 — `Depends(...)` in a default is FastAPI's dependency
# injection idiom and is used by every router in this codebase; the rule's
# suggested "module-level singleton" rewrite would break the DI graph.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.deps import get_current_user
from app.modules.assistant import events
from app.modules.assistant.consent import consent_broker
from app.modules.assistant.provider import assistant_provider
from app.modules.assistant.registry import tool_registry
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
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread, thread_store
from app.modules.assistant.tools import ToolInvocation

logger = logging.getLogger(__name__)

router = APIRouter()

#: One loop instance is fine: it holds no per-request state.
_loop = AssistantLoop(provider=assistant_provider, registry=tool_registry)


def _thread_view(thread: AssistantThread) -> ThreadView:
    return ThreadView(
        thread_id=thread.thread_id,
        title=thread.title,
        workspace_file_id=thread.workspace_file_id,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        message_count=len(thread.messages),
    )


def _message_view(message) -> MessageView:
    return MessageView(
        message_id=message.message_id,
        role=message.role,
        content=message.content,
        tool_call=message.tool_call,
        created_at=message.created_at,
    )


def _require_thread(thread_id: str, user_name: str) -> AssistantThread:
    thread = thread_store.get(thread_id, user_name=user_name)
    if thread is None:
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


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(user: dict = Depends(get_current_user)):
    threads = thread_store.list_for_user(user["username"])
    views = [_thread_view(t) for t in threads]
    return ThreadListResponse(threads=views, count=len(views))


@router.post("/threads", response_model=ThreadView, status_code=201)
async def create_thread(
    body: ThreadCreateRequest,
    user: dict = Depends(get_current_user),
):
    thread = thread_store.create(
        user_name=user["username"],
        title=body.title,
        workspace_file_id=body.workspace_file_id,
    )
    return _thread_view(thread)


@router.get("/threads/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    thread = _require_thread(thread_id, user["username"])
    return ThreadDetailResponse(
        thread=_thread_view(thread),
        messages=[_message_view(m) for m in thread.messages],
    )


@router.patch("/threads/{thread_id}", response_model=ThreadView)
async def rename_thread(
    thread_id: str,
    body: ThreadUpdateRequest,
    user: dict = Depends(get_current_user),
):
    thread = _require_thread(thread_id, user["username"])
    if body.title is not None:
        thread.title = body.title
        thread.touch()
    return _thread_view(thread)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    if not thread_store.delete(thread_id, user_name=user["username"]):
        raise HTTPException(status_code=404, detail="Thread not found")
    return None


@router.delete("/threads/{thread_id}/grant", status_code=204)
async def reset_grant(
    thread_id: str,
    user: dict = Depends(get_current_user),
):
    """Revoke the conversation's read-only always-allow grant (E2b)."""
    thread = _require_thread(thread_id, user["username"])
    thread.consent.always_allow_read_only = False
    return None


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
    one message when the stream ends.
    """
    thread = _require_thread(thread_id, user["username"])

    user_message = AssistantMessage(
        message_id=str(uuid4()), role="user", content=body.content
    )
    thread.messages.append(user_message)
    thread.touch()

    context = LoopContext(
        user_name=user["username"],
        database=body.database,
        schema_name=body.schema_name,
        role=body.role,
        workspace_file_id=thread.workspace_file_id,
        session_id=user.get("session_id"),
        thread_id=thread.thread_id,
        user=user,
    )

    async def resolve_consent(
        invocation: ToolInvocation, classification: str
    ) -> bool | None:
        # The loop has already emitted the tool_call frame; the client answers
        # out of band. If the stream is disconnected, treat it as cancelled.
        future = consent_broker.open(
            invocation.tool_call_id,
            thread_id=thread.thread_id,
            user_name=thread.user_name,
            classification=classification,
        )

        async def _watch_disconnect() -> None:
            while not future.done():
                if await request.is_disconnected():
                    # The owner's stream went away: cancel the pending call as
                    # its owner, so the owner check is satisfied and the loop
                    # is released with ``None`` (cancelled).
                    consent_broker.resolve(
                        invocation.tool_call_id, None, user_name=thread.user_name
                    )
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
            async for frame in _loop.run(
                thread=thread,
                user_content=body.content,
                context=context,
                resolve_consent=resolve_consent,
                cancelled=lambda: False,
            ):
                if await request.is_disconnected():
                    break
                if frame.startswith(f"event: {events.EVENT_TEXT_DELTA}"):
                    reply_parts.append(_text_from_frame(frame))
                yield frame
        except Exception as exc:  # noqa: BLE001 - never leak an internal trace
            logger.exception("Assistant stream failed")
            yield events.error(
                "internal_error", f"The assistant failed: {type(exc).__name__}"
            )
            yield events.done(str(uuid4()), finish_reason="error")
        finally:
            # Fold the visible reply into the transcript. Tool frames are not
            # replayed; only the assistant's text is part of the thread.
            if reply_parts:
                thread.messages.append(
                    AssistantMessage(
                        message_id=str(uuid4()),
                        role="assistant",
                        content="".join(reply_parts),
                    )
                )
                thread.touch()

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
        resolved = consent_broker.resolve(
            tool_call_id, False, user_name=user["username"]
        )
        return ConsentDecisionResponse(
            tool_call_id=tool_call_id,
            status="denied" if resolved else "cancelled",
            grant_active=False,
        )

    grant_active = False
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
        thread.consent.always_allow_read_only = True
        grant_active = True

    resolved = consent_broker.resolve(
        tool_call_id, True, user_name=user["username"]
    )
    return ConsentDecisionResponse(
        tool_call_id=tool_call_id,
        status="approved" if resolved else "cancelled",
        grant_active=grant_active,
    )
