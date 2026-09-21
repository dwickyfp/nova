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

Every route requires ``get_current_user``. Threads are scoped to their owner;
an unknown or foreign id answers 404, never 403, so existence does not leak.
The scoping is enforced in SQL (``repository.py``), so one user's history can
never be returned to another.

Threads and messages are persisted in ``NOVA_SYSTEM`` and survive a reload.
Consent grants stay process-local (``state.py``): an always-allow grant is
deliberately ephemeral and is not revived by a restart.
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
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.schemas import (
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
)
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, thread_store
from app.modules.assistant.tools import ToolInvocation

logger = logging.getLogger(__name__)

router = APIRouter()

#: One loop instance is fine: it holds no per-request state.
_loop = AssistantLoop(provider=assistant_provider, registry=tool_registry)


def _thread_view(row: dict) -> ThreadView:
    return ThreadView(
        thread_id=row["thread_id"],
        title=row["title"],
        workspace_file_id=row.get("workspace_file_id"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=row.get("message_count", 0),
    )


def _message_view(row: dict) -> MessageView:
    return MessageView(
        message_id=row["message_id"],
        role=row["role"],
        content=row.get("content", ""),
        tool_call=None,
        created_at=row["created_at"],
        steps=row.get("steps") or [],
        prompt_tokens=row.get("prompt_tokens"),
        completion_tokens=row.get("completion_tokens"),
        total_tokens=row.get("total_tokens"),
        model_name=row.get("model_name"),
    )


async def _require_thread(thread_id: str, user_name: str) -> dict:
    """Fetch a thread owned by ``user_name``, or 404.

    The owner filter is in SQL, so a foreign or unknown id is indistinguishable
    from a missing one — existence never leaks.
    """
    thread = await assistant_repository.get_thread(thread_id, user_name=user_name)
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
    threads = await assistant_repository.list_threads(user_name=user["username"])
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
):
    thread = await _require_thread(thread_id, user["username"])
    messages = await assistant_repository.list_messages(
        thread_id, user_name=user["username"]
    )
    return ThreadDetailResponse(
        thread=_thread_view(thread),
        messages=[_message_view(m) for m in messages],
    )


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
    if not await assistant_repository.delete_thread(
        thread_id, user_name=user["username"]
    ):
        raise HTTPException(status_code=404, detail="Thread not found")
    # Drop the runtime entry too, so a lingering grant cannot outlive the thread.
    thread_store.remove(thread_id, user_name=user["username"])
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
        runtime.consent.always_allow_read_only = False
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
    runtime.consent.always_allow_read_only = body.always_allow_read_only
    return {"grant_active": runtime.consent.always_allow_read_only}


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

    # Store the user's message before the stream starts, so a disconnect still
    # leaves a coherent transcript.
    await assistant_repository.append_message(
        thread_id, user_name=user_name, role="user", content=body.content
    )
    runtime.messages.append(
        AssistantMessage(message_id=str(uuid4()), role="user", content=body.content)
    )

    context = LoopContext(
        user_name=user_name,
        database=body.database,
        schema_name=body.schema_name,
        role=body.role,
        workspace_file_id=thread_row.get("workspace_file_id"),
        session_id=user.get("session_id"),
        thread_id=thread_id,
        user=user,
    )

    async def resolve_consent(
        invocation: ToolInvocation, classification: str
    ) -> bool | None:
        # The loop has already emitted the tool_call frame; the client answers
        # out of band. If the stream is disconnected, treat it as cancelled.
        future = consent_broker.open(
            invocation.tool_call_id,
            thread_id=thread_id,
            user_name=user_name,
            classification=classification,
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
        try:
            return await future
        finally:
            watcher.cancel()

    async def generate() -> AsyncIterator[str]:
        reply_parts: list[str] = []
        try:
            async for frame in _loop.run(
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
            logger.exception("Assistant stream failed")
            yield events.error(
                "internal_error", f"The assistant failed: {type(exc).__name__}"
            )
            yield events.done(str(uuid4()), finish_reason="error")
        finally:
            # Fold the visible reply into the transcript. Tool frames are not
            # replayed; only the assistant's text is part of the thread. The
            # write is best-effort: a storage failure must not turn a completed
            # answer into a stream error the user cannot see the cause of.
            if reply_parts or context.steps or context.pending_output:
                try:
                    steps = list(context.steps or [])
                    if context.pending_output:
                        steps.extend(context.pending_output)
                    await assistant_repository.append_message(
                        thread_id,
                        user_name=user_name,
                        role="assistant",
                        content="".join(reply_parts),
                        model_name=body.model,
                        usage=context.usage,
                        steps=steps,
                        instructions=context.instructions,
                    )
                except Exception:
                    logger.exception("Could not persist the assistant reply")

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
