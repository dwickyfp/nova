"""Governed AI Search tool for the bounded Agent Studio loop."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.redaction import redact_rows
from app.modules.intelligence.search import SearchQuery, search_service

logger = logging.getLogger(__name__)


class AISearchTool:
    name = "ai_search"
    description = "Search a governed Nova AI Search index as the current user."
    parameters = {
        "type": "object",
        "properties": {
            "index": {"type": "string", "description": "The Nova AI Search index name."},
            "query": {"type": "string", "description": "Text to search for."},
            "mode": {"type": "string", "enum": ["LEXICAL", "SEMANTIC", "HYBRID"]},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            "filters": {
                "type": "object",
                "additionalProperties": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                        {"type": "number"},
                        {"type": "boolean"},
                    ]
                },
            },
        },
        "required": ["index", "query"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        name = str(invocation.arguments.get("index", ""))[:128]
        return f"ai_search: {name}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = getattr(context, "user", None) or {}
        if not user.get("username") or not user.get("encrypted_password"):
            return ToolOutcome(ok=False, summary="", error="User connection unavailable")
        name = invocation.arguments.get("index")
        if not isinstance(name, str) or not name:
            return ToolOutcome(ok=False, summary="", error="Search index is required")
        text = invocation.arguments.get("query")
        if not isinstance(text, str):
            return ToolOutcome(ok=False, summary="", error="Search query is required")
        try:
            query = SearchQuery(
                query=text,
                mode=invocation.arguments.get("mode", "HYBRID"),
                top_k=min(int(invocation.arguments.get("top_k", 5)), 10),
                filters=invocation.arguments.get("filters", {}),
            )
        except (ValidationError, ValueError, TypeError):
            return ToolOutcome(ok=False, summary="", error="Invalid search request")
        scoped_user = {
            **user,
            "active_role": getattr(context, "active_role", None) or user.get("active_role"),
            "session_id": getattr(context, "audit_session_id", None) or user.get("session_id"),
        }
        try:
            result = await search_service.query(name, query, scoped_user)
        except Exception as exc:
            logger.warning("ai_search failed: %s", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="Search is unavailable or unauthorized")
        hits = result["hits"][:10]
        redacted = redact_rows(["content"], [[hit["content"]] for hit in hits])
        safe = [
            {"source_key": hit["source_key"], "content": row[0], "rank": hit["rank"]}
            for hit, row in zip(hits, redacted, strict=True)
        ]
        return ToolOutcome(
            ok=True,
            summary=f"{len(safe)} search result(s) from {name}",
            data={"index": name, "version": result["version"], "hits": safe},
            evidence={"source": "nova_ai_search", "index": name, "version": result["version"]},
            trace_detail={"index": name, "version": result["version"], "result_count": len(safe)},
        )


ai_search_tool = AISearchTool()
