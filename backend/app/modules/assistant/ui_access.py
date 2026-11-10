"""Interactive answer blocks: their state, media and actions, for any thread.

Studio agents and Neva render the same Intelligence UI blocks. The checks that
make a block safe to read, change or act on live here, once: the block must be
the caller's, in the caller's current security context, from a supported
catalog and unchanged since it was stored; state is validated against the
program; public media must match the static program; an action is accepted
once. Each router first proves the caller owns the thread (an agent thread or
a Neva thread) and then calls these.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException

from app.common.audit import write_audit_log
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security


async def ui_denied(user: dict, resource: str, reason: str, status: int = 422) -> HTTPException:
    """Audit a refused UI request and return the error to raise."""
    await write_audit_log(
        event_type="ASSISTANT_UI", user_name=user["username"], action="REFUSE",
        object_type="UI_BLOCK", object_name=resource, status="DENIED",
        error_message=reason, session_id=user.get("session_id"),
        active_role=user.get("active_role"),
        security_context_version=user.get("security_context_version"),
    )
    return HTTPException(status, reason)


async def ui_source(thread_id: str, message_id: str, content_id: str, user: dict) -> dict:
    """The stored block, only when it is the caller's in this security context."""
    from app.modules.assistant.intelligent_ui import SUPPORTED_CATALOGS

    block = await assistant_repository.ui_source(
        thread_id, message_id, content_id, user_name=user["username"],
        security_context=observation_context(session_security(user)),
    )
    if (not block or block.get("catalog_version") not in SUPPORTED_CATALOGS
            or hashlib.sha256(str(block.get("program", "")).encode()).hexdigest()
            != block.get("program_sha256")):
        raise await ui_denied(user, content_id,
                              "UI source is unavailable in this security context", 404)
    return block


async def get_state(thread_id: str, message_id: str, content_id: str, user: dict) -> Any:
    block = await ui_source(thread_id, message_id, content_id, user)
    return await assistant_repository.get_ui_state(
        thread_id, message_id, content_id, user_name=user["username"],
        security_context=observation_context(session_security(user)), block=block,
    )


async def put_state(
    thread_id: str, message_id: str, content_id: str, user: dict, *,
    program_sha256: str, revision: int, state: dict,
) -> Any:
    from app.modules.assistant.intelligent_ui import UiValidationError, validate_source_state

    block = await ui_source(thread_id, message_id, content_id, user)
    if program_sha256 != block["program_sha256"]:
        raise await ui_denied(user, content_id, "UI program changed", 409)
    try:
        if block["catalog_version"] == "nova-openui-3":
            await validate_source_state(block, state)
        return await assistant_repository.put_ui_state(
            thread_id, message_id, content_id, user_name=user["username"],
            security_context=observation_context(session_security(user)), block=block,
            revision=revision, state=state,
        )
    except UiValidationError as exc:
        raise await ui_denied(user, content_id, str(exc)) from exc


async def media(
    thread_id: str, message_id: str, content_id: str, user: dict, *,
    media_type: str, props: dict,
) -> Any:
    from app.modules.assistant.intelligent_ui import UiValidationError, parse_program
    from app.modules.assistant.ui_media import public_media

    block = await ui_source(thread_id, message_id, content_id, user)
    if block.get("evidence") or len(json.dumps(props).encode()) > 64_000:
        raise await ui_denied(
            user, content_id, "Public media cannot receive business or form data"
        )
    try:
        parsed = await parse_program(block["program"], catalog_version=block["catalog_version"])
    except UiValidationError as exc:
        raise HTTPException(422, "Public media source is invalid") from exc
    if not any(item["type"] == media_type and item["props"] == props
               for item in parsed["media"]):
        raise await ui_denied(
            user, content_id, "Media parameters must match the static source program"
        )
    return await public_media(media_type, props)


async def map_metric(
    thread_id: str, message_id: str, content_id: str, user: dict, *,
    event: str, elapsed_ms: float,
) -> dict:
    from app.observability.metrics import STUDIO_WORKFLOW_DURATION, STUDIO_WORKFLOW_OPERATIONS

    await ui_source(thread_id, message_id, content_id, user)
    # Client observations are kept distinct from server validation outcomes.
    status = f"client_{event}"
    STUDIO_WORKFLOW_OPERATIONS.labels("ui", "map_render", status).inc()
    STUDIO_WORKFLOW_DURATION.labels("ui", "map_render", status).observe(elapsed_ms / 1000)
    return {"recorded": True}


async def accept_action(thread_id: str, action: Any, content: str, user: dict) -> tuple[str, dict]:
    """Accept a form or button action once; return the turn text and its trace step.

    The validated block provides context, not tool authority: the form data is
    ordinary untrusted user input and never becomes a system instruction.
    """
    from app.core.redis import session_store
    from app.modules.assistant.intelligent_ui import UiValidationError, action_context
    from app.modules.intelligence.engine_repository import metadata_lock

    user_name = user["username"]
    source = await ui_source(thread_id, action.message_id, action.content_id, user)
    if source["program_sha256"] != action.program_sha256:
        raise await ui_denied(user, action.content_id, "Invalid UI action source")
    try:
        context = await action_context(source, action.state, action_id=action.action_id)
    except UiValidationError as exc:
        raise await ui_denied(user, action.content_id, str(exc)) from exc
    turn_text = content + "\n\nUI form input (user supplied):\n" + json.dumps(
        {"action_id": action.action_id, **context}, ensure_ascii=False,
    )
    if len(turn_text) > 32_000:
        raise await ui_denied(user, action.content_id, "UI action input is too large")
    key = hashlib.sha256(f"{user_name}:{thread_id}:{action.operation_id}".encode()).hexdigest()
    async with metadata_lock(f"ui-action:{key}"):
        redis = session_store._redis  # noqa: SLF001 - the shared runtime connection
        if redis is None:
            raise HTTPException(503, "Metadata coordination is unavailable")
        if await assistant_repository.ui_action_seen(
            thread_id, action.operation_id, user_name=user_name
        ) or not await redis.set(
            f"nova:studio:ui-action:{key}", "accepted", nx=True, ex=86400
        ):
            raise await ui_denied(user, action.content_id,
                                  "This UI action has already been accepted", 409)
    security = session_security(user)
    await write_audit_log(
        event_type="ASSISTANT_UI", user_name=user_name, action="CONTINUE",
        object_type="UI_BLOCK", object_name=action.content_id, status="SUCCESS",
        session_id=user.get("session_id"), active_role=security.active_role,
        security_context_version=security.security_context_version,
    )
    return turn_text, {"kind": "ui_action", **action.model_dump()}


def visible_message(row: dict, user: dict | None) -> tuple[str, list]:
    """A stored message's text and trace as the caller may see them now.

    Interactive blocks stay only in the security context they were made in, the
    form input an action carried is not shown back as the person's words, and
    the trace is reduced to its public projection.
    """
    from app.modules.agents.public_projections import sanitize_workflow_payload
    from app.modules.assistant.security import same_ui_context

    steps = row.get("steps") or []
    stamp = row.get("security_context") or {}
    current = observation_context(session_security(user)) if user is not None and any(
        isinstance(item, dict) and item.get("kind") == "ui" for item in steps
    ) else {}
    if not (bool(current) and same_ui_context(stamp, current)):
        steps = [
            item for item in steps
            if not (isinstance(item, dict) and item.get("kind") == "ui")
        ]
    content = row.get("content") or ""
    if row.get("role") == "user" and any(
        isinstance(item, dict) and item.get("kind") == "ui_action" for item in steps
    ):
        content = content.split("\n\nUI form input (user supplied):\n", 1)[0]
    return content, sanitize_workflow_payload(steps)
