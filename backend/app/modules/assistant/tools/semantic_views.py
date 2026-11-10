"""List the published Semantic Views the caller's active role can open.

Neva answers "which views do we have" from this, never from memory. The list
comes from the same check the governed metric tool uses (the Semantic View
service, as the caller), so a view the role cannot open is never named.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

#: The most views one answer lists; the rest are counted, not named.
MAX_VIEWS = 16


class ListSemanticViewsTool:
    name = "list_semantic_views"
    description = (
        "List the published Semantic Views the user's active role can open, with what "
        "each covers and its main metrics and breakdowns. Use it to answer which views "
        "exist or which one covers a topic, and to pick the view for semantic_query."
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    classification: ToolClassification = "read_only"
    #: Names and descriptions the role can already open; no data rows.
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return "List the Semantic Views your role can open"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.agents.semantic.access import load_authorized_models
        from app.modules.agents.tools.semantic_query import readable_view_summaries

        user = getattr(context, "user", None) or {}
        if not user.get("username") or not user.get("encrypted_password"):
            return ToolOutcome(
                ok=False, summary="", error="Sign in again to list Semantic Views.",
                error_class="AUTHORIZATION",
            )
        if not getattr(context, "semantic_view_ids", None):
            views: list[dict[str, Any]] = []
        else:
            try:
                views = readable_view_summaries(await load_authorized_models(context))
            except Exception:  # noqa: BLE001 - fewer views, never other views
                return ToolOutcome(
                    ok=False, summary="", error="The Semantic View list is unavailable.",
                )
        payload = {
            "semantic_views": views[:MAX_VIEWS],
            "count": len(views),
            "note": (
                "Only views the active role can open are listed. A view that is not "
                "listed may exist for another role."
            ),
        }
        return ToolOutcome(
            ok=True,
            summary=json.dumps(payload, ensure_ascii=False)[:6000],
            data=payload,
            metadata={"kind": "semantic_view_catalog", "runtime_evidence": False},
            evidence={"source": "semantic_views", "kind": "metadata"},
        )


list_semantic_views_tool = ListSemanticViewsTool()
