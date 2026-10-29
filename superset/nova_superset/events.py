"""``EVENT_LOGGER``: Superset's action log, with governed actions sent to Nova.

Superset keeps its own log. Actions that create, change, delete or export BI
content are also written to Nova's ``AUDIT_LOG`` for the signed-in Nova
principal (S12), with object ids only: no SQL text, rows or parameters leave
Superset through this path. Reads are not sent. An audit failure is logged and
never fails the user's request.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import has_request_context, request
from superset.utils.log import DBEventLogger

from nova_superset import client, context

logger = logging.getLogger(__name__)

_CHANGES = (".post", ".put", ".delete", ".bulk_delete", ".import_", ".copy_dash")
_EXPORT_FORMATS = {"csv", "xlsx"}


def audited_action(action: str) -> str | None:
    """The Nova audit action for a Superset action, or None when it is a read."""
    name = action or ""
    lowered = name.lower()
    if "export" in lowered or "csv" in lowered:
        return "BI_EXPORT"
    if name.endswith(_CHANGES):
        return "BI_CHANGE"
    if name.endswith(".data") and has_request_context():
        body = request.get_json(silent=True) or {}
        fmt = str(body.get("result_format") or request.args.get("format") or "").lower()
        if fmt in _EXPORT_FORMATS:
            return "BI_EXPORT"
    return None


class NovaEventLogger(DBEventLogger):
    def log(  # type: ignore[override]
        self,
        user_id: int | None,
        action: str,
        dashboard_id: int | None,
        duration_ms: int | None,
        slice_id: int | None,
        referrer: str | None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().log(user_id, action, dashboard_id, duration_ms, slice_id, referrer, *args, **kwargs)
        kind = audited_action(action)
        handle = context.bi_handle.get()
        if kind is None or not handle:
            return
        objects = []
        if dashboard_id:
            objects.append(f"dashboard:{dashboard_id}")
        if slice_id:
            objects.append(f"chart:{slice_id}")
        if has_request_context() and request.view_args:
            objects.extend(f"{key}:{value}" for key, value in sorted(request.view_args.items()))
        try:
            client.audit(handle, kind=kind, action=action, objects=objects[:8])
        except (client.NovaUnauthorized, client.NovaUnavailable) as exc:
            logger.warning("Nova audit of %s failed: %s", action, type(exc).__name__)
