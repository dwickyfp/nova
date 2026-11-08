"""Dataviz services shared by the HTTP API and the assistant's tools.

Both callers go through these functions, so a button and a prompt get the same
capability answer, the same diff and the same publication
(docs/arch-19-studio-dataviz.md, D2, D3 and D6).

A *change* is previewed first: :func:`preview_change` returns a human diff and
an expiring action token bound to the caller's principal, active role and
security-context version. :func:`apply_change` accepts only that token, checks
the context again, and publishes against the revision the user saw.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Literal

from . import capability as capabilities
from .previews import Locator, PreviewError, resolve, sign, verify
from .publication import PublishError, Target, describe_change, plan_for, publish

logger = logging.getLogger(__name__)

ACTION_TTL_SECONDS = 900
MAX_BATCH = 50


def _redis():
    from app.core.redis import session_store

    if session_store._redis is None:
        raise PublishError(
            "Dataviz changes are unavailable right now.",
            status=503,
            code="COORDINATION_UNAVAILABLE",
        )
    return session_store._redis


def _concepts(business: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {
            "qualified_name": item.get("qualified_name"),
            "label": item.get("label"),
            "definition": item.get("definition"),
            "metric": item.get("metric"),
        }
        for item in (business or {}).get("concepts") or []
        if isinstance(item, dict)
    ]


async def check_locator(
    locator: Locator, user: dict[str, Any], *, may_publish: bool | None = None
) -> dict[str, Any]:
    """The capability of one located result, with a signed reference to it."""
    try:
        preview = await resolve(locator, user)
    except PreviewError as exc:
        return {
            "preview_ref": None,
            "status": "UNKNOWN",
            "reason_code": "PREVIEW_NOT_FOUND",
            "message": str(exc),
            "can_add": False,
            "alternatives": [],
        }
    result = await capabilities.check(preview)
    if may_publish is None:
        may_publish = await capabilities.may_publish(user)
    public = result.public()
    return {
        **public,
        "preview_ref": sign(locator, user),
        "title": preview.title,
        "render_kind": preview.render_kind,
        "may_publish": may_publish,
        "can_add": result.status == "SUPPORTED" and may_publish,
        "concepts": _concepts(preview.business_context),
        "semantic_view": (preview.semantic or {}).get("view_id"),
    }


async def check_many(locators: list[Locator], user: dict[str, Any]) -> list[dict[str, Any]]:
    may_publish = await capabilities.may_publish(user)
    return [
        await check_locator(item, user, may_publish=may_publish) for item in locators[:MAX_BATCH]
    ]


async def check_ref(ref: str, user: dict[str, Any]) -> dict[str, Any]:
    try:
        locator = verify(ref, user)
    except PreviewError as exc:
        return {
            "preview_ref": None,
            "status": "UNKNOWN",
            "reason_code": "PREVIEW_NOT_FOUND",
            "message": str(exc),
            "can_add": False,
            "alternatives": [],
        }
    return await check_locator(locator, user)


def _binding(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "owner": user["username"],
        "role": user.get("active_role"),
        "version": user.get("security_context_version"),
        "session": user.get("session_id"),
    }


async def preview_change(
    user: dict[str, Any],
    *,
    ref: str,
    target: Target,
    approved_transform_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Plan an addition and describe it; nothing is changed."""
    if not await capabilities.may_publish(user):
        raise PublishError(
            "Your active role can view dashboards but cannot add charts. Nothing was changed.",
            status=403,
            code="NOT_AUTHOR",
        )
    plan = await plan_for(ref, user, approved_transform_id=approved_transform_id, title=title)
    diff = await describe_change(user, plan, target)
    token = secrets.token_urlsafe(24)
    record = {
        **_binding(user),
        "ref": ref,
        "mode": target.mode,
        "dashboard_id": target.dashboard_id,
        "title": target.title,
        "transform": approved_transform_id,
        "chart_title": plan.title,
        "revision": diff["revision"],
        "changes": diff["changes"],
    }
    await _redis().set(f"nova:dataviz:action:{token}", json.dumps(record), ex=ACTION_TTL_SECONDS)
    return {
        "action_token": token,
        "expires_in": ACTION_TTL_SECONDS,
        "chart": {"title": plan.title, "kind": plan.plan.kind},
        "strategy_id": plan.strategy_id,
        "concepts": _concepts(plan.preview.business_context),
        **diff,
    }


def _pending_key(user: dict[str, Any], thread_id: str) -> str:
    return f"nova:dataviz:pending:{user['username']}:{thread_id}"


async def remember_pending(user: dict[str, Any], thread_id: str | None, token: str) -> None:
    """The change prepared last in a conversation, so a later "yes" can apply it."""
    if not thread_id:
        return
    try:
        await _redis().set(_pending_key(user, thread_id), token, ex=ACTION_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - a convenience: the token still works on its own
        logger.warning("Could not remember the prepared Dataviz change for this conversation")


async def pending_change(user: dict[str, Any], thread_id: str | None) -> dict[str, Any] | None:
    """That prepared change, while it is still valid for this caller."""
    if not thread_id:
        return None
    try:
        token = await _redis().get(_pending_key(user, thread_id))
        if not token:
            return None
        token = token.decode() if isinstance(token, bytes) else str(token)
        record = await peek_change(token, user)
    except Exception:  # noqa: BLE001 - without Redis there is simply nothing pending
        return None
    return {"action_token": token, **record} if record else None


async def peek_change(token: str, user: dict[str, Any]) -> dict[str, Any] | None:
    """The stored preview behind a token, for the caller only."""
    raw = await _redis().get(f"nova:dataviz:action:{token}")
    if not raw:
        return None
    record = json.loads(raw)
    return record if record.get("owner") == user["username"] else None


async def apply_change(
    user: dict[str, Any],
    *,
    token: str,
    idempotency_key: str,
    editor_state: Literal["clean", "dirty", "unknown"] = "unknown",
    open_dashboard_id: int | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Apply a previewed change exactly as previewed, or refuse."""
    record = await peek_change(token, user)
    if record is None:
        raise PublishError(
            "This change expired or is not yours. Prepare it again.",
            status=410,
            code="ACTION_EXPIRED",
        )
    if any(record.get(key) != value for key, value in _binding(user).items() if key != "session"):
        raise PublishError(
            "Your role or session changed since this change was prepared. Prepare it again.",
            status=409,
            code="STALE_SECURITY_CONTEXT",
        )
    op = await publish(
        user,
        ref=record["ref"],
        idempotency_key=idempotency_key,
        target=Target(
            mode=record["mode"],
            dashboard_id=record.get("dashboard_id"),
            title=record.get("title"),
            expected_revision=record.get("revision"),
        ),
        approved_transform_id=record.get("transform"),
        title=record.get("chart_title"),
        editor_state=editor_state,
        open_dashboard_id=open_dashboard_id,
        agent_id=agent_id,
    )
    if op.status == "SUCCEEDED":
        await _redis().delete(f"nova:dataviz:action:{token}")
    return {**op.public(), "changes": record.get("changes") or []}
