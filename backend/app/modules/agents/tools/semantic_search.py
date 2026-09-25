"""``semantic_search`` — native StarRocks full-text search (no LLM, no vectors).

StarRocks 4.1 has a native full-text inverted index (GIN). The predicate form is
``<col> MATCH_ANY 'k1 k2'`` / ``<col> MATCH_ALL '...'`` on a GIN-indexed
``STRING NOT NULL`` column in a ``WHERE`` clause. This was verified against the
pinned engine (4.1.4) by ``backend/tests/integration/test_inverted_index_l3.py``
and is documented in ``docs/24-advanced-indexes.md``.

Important, engine-verified limits this tool is honest about:

* **No relevance ranking.** ``ORDER BY score`` / BM25 is not available on the
  4.1.4 predicate path (``docs/gap-analysis.md`` §6). Results are *filtered, not
  ranked*, so this tool returns them unranked and says so.
* Predicates are pushdown-only: they must be in ``WHERE`` on an indexed column.
* Tokenisation lowercases English terms, so the terms are lowercased here.

The search runs through ``QueryService`` on the user's connection, like every
other read, so guard, redaction, and audit apply unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.semantic.compiler import (
    SemanticPlanError,
    quote_semantic_identifier,
    quote_semantic_source,
)
from app.modules.agents.semantic.runtime import (
    SemanticModelCandidate,
    SemanticModelRouter,
)
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, policy
from app.modules.assistant.tools.query_execute import (
    ASSISTANT_MAX_PREVIEW_CHARS,
    ASSISTANT_MAX_ROWS,
    _active_role,
)

logger = logging.getLogger(__name__)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "The keywords to search for. Multiple words are matched "
                "according to the chosen mode."
            ),
        },
        "column": {
            "type": "string",
            "description": (
                "The indexed text column to search. Must be one of the text "
                "fields defined in the semantic model."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["any", "all"],
            "description": (
                "'any' matches rows containing any keyword (MATCH_ANY); "
                "'all' requires every keyword (MATCH_ALL). Default 'any'."
            ),
        },
    },
    "required": ["query", "column"],
}


class SemanticSearchTool:
    """Full-text search over an indexed column of the semantic model."""

    name = "semantic_search"
    description = (
        "Full-text search over indexed text in the semantic model's datasets. "
        "Returns matching rows, unranked (the engine has no relevance score on "
        "this path)."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "read_only"
    requires_consent = True

    def __init__(self, *, max_rows: int = ASSISTANT_MAX_ROWS) -> None:
        self.max_rows = max_rows
        self._model_router = SemanticModelRouter()

    def preview(self, invocation: ToolInvocation) -> str:
        query = _arg(invocation, "query")
        column = _arg(invocation, "column")
        mode = _arg(invocation, "mode") or "any"
        return f"semantic_search({mode}): {query} in {column}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        query = _arg(invocation, "query")
        column = _arg(invocation, "column")
        mode = (_arg(invocation, "mode") or "any").lower()
        if not query:
            return ToolOutcome(ok=False, summary="", error="No query was provided.")
        if not column:
            return ToolOutcome(ok=False, summary="", error="No search column was provided.")
        if mode not in {"any", "all"}:
            mode = "any"

        user = getattr(context, "user", None) or {}
        username = user.get("username")
        encrypted_password = user.get("encrypted_password")
        if not username or not encrypted_password:
            return ToolOutcome(
                ok=False,
                summary="",
                error="No user connection is available for this tool call.",
            )

        semantic_model = await self._resolve_model(context, query)
        if semantic_model is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error="No published Semantic View is available to this agent.",
            )

        target = _resolve_target(semantic_model.get("definition") or {}, column)
        if target is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    f"Column {column!r} is not a text field in the Semantic View. "
                    "Pick a searchable text column."
                ),
            )
        source, physical_column = target

        terms = [t for t in query.lower().split() if t]
        if not terms:
            return ToolOutcome(ok=False, summary="", error="No search terms were provided.")
        predicate = "MATCH_ANY" if mode == "any" else "MATCH_ALL"
        keyword = " ".join(_sanitize_term(t) for t in terms)
        if not keyword:
            return ToolOutcome(ok=False, summary="", error="No usable search terms.")
        try:
            safe_source = quote_semantic_source(source)
            safe_column = quote_semantic_identifier(physical_column)
        except SemanticPlanError as exc:
            return ToolOutcome(
                ok=False,
                summary="",
                error="The semantic search target is not a safe SQL identifier.",
                safe_detail=str(exc),
            )
        sql = (
            f"SELECT * FROM {safe_source} "
            f"WHERE {safe_column} {predicate} '{keyword}' "
            f"LIMIT {int(self.max_rows)}"
        )

        statements = [sql]
        classification, decisions = policy.classify_statements(statements)
        if classification != "read_only":
            offending = next((d for d in decisions if not d.allowed), None)
            reason = offending.reason if offending else "The search is not read-only."
            return ToolOutcome(ok=False, summary="", error=reason)

        from app.modules.query.service import query_service

        try:
            results = await query_service.execute_statements(
                sql=sql,
                username=username,
                encrypted_password=encrypted_password,
                database=_context_value(context, "database"),
                schema=_context_value(context, "schema_name"),
                role=_active_role(context),
                max_rows=self.max_rows,
                session_id=_context_value(context, "audit_session_id"),
                confirm_destructive=False,
                file_id=_context_value(context, "workspace_file_id"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic_search execution failed: %s", type(exc).__name__)
            return ToolOutcome(
                ok=False,
                summary="",
                error="The search failed to run.",
            )

        failed = next((r for r in results if r.error), None)
        if failed is not None:
            # A GIN error (e.g. no index, or MATCH on a non-indexed column) is
            # surfaced verbatim but redacted of credentials by the pipeline.
            return ToolOutcome(
                ok=False,
                summary="",
                error=(
                    "The search failed. The column may not have a full-text index. "
                    "Create one under Advanced Indexes."
                ),
            )

        return ToolOutcome(ok=True, summary=_render(query, column, mode, results))

    async def _resolve_model(self, context: Any, query: str) -> dict[str, Any] | None:
        from app.modules.agents.semantic.access import load_authorized_models

        models = await load_authorized_models(context)
        candidates = [
            SemanticModelCandidate(str(model["semantic_model_id"]), model["_scoped_ir"])
            for model in models
        ]
        if len(models) == 1:
            return models[0]
        selection = self._model_router.route(query, candidates)
        return next(
            (model for model in models if str(model["semantic_model_id"]) == selection.model_id),
            None,
        )


def _resolve_target(definition: dict[str, Any], column: str) -> tuple[str, str] | None:
    """Map a logical text column to ``(physical_source, physical_column)``.

    A field's logical name may differ from its expression; for a plain column
    reference they match. The source is the dataset's physical source, which is
    a ``db.schema.table`` reference Nova trusts (it comes from the stored model,
    not the model's output).
    """
    wanted = column.strip().lower()
    matches: list[tuple[str, str]] = []
    for dataset in definition.get("datasets") or []:
        source = dataset.get("source")
        if not source:
            continue
        for field in dataset.get("fields") or []:
            name = str(field.get("name") or "").strip().lower()
            expression = str(field.get("expression") or "").strip()
            qualified = str(dataset.get("name") or "").lower() + "." + name
            if name == wanted or qualified == wanted:
                datatype = str(field.get("datatype") or "").lower()
                if datatype and datatype not in {"string", "text", "varchar", "char"}:
                    continue
                physical = expression or name
                # Only a plain identifier is safe to use unquoted; a computed
                # expression needs an alias the caller cannot supply here.
                if _is_identifier(physical):
                    matches.append((str(source), physical))
    return matches[0] if len(matches) == 1 else None


def _is_identifier(text: str) -> bool:
    return bool(text) and text.isidentifier()


def _sanitize_term(term: str) -> str:
    """Strip characters that would break a quoted string literal."""
    return term.replace("'", "").replace("\\", "").replace("%", "")


def _arg(invocation: ToolInvocation, name: str) -> str:
    value = invocation.arguments.get(name)
    return value.strip() if isinstance(value, str) else ""


def _render(query: str, column: str, mode: str, results: list[Any]) -> str:
    from app.modules.assistant.tools.redaction import redact_rows

    lines = [
        f"search: {query} (mode={mode}) in {column}",
        "note: results are unranked (the engine has no relevance score on this path)",
    ]
    for result in results:
        columns = list(getattr(result, "columns", []) or [])
        rows = redact_rows(columns, list(getattr(result, "rows", []) or []))
        lines.append(f"columns: {', '.join(columns)}")
        lines.append(f"row_count: {getattr(result, 'row_count', len(rows))}")
        if rows:
            header = " | ".join(columns)
            body = "\n".join(
                " | ".join("" if v is None else str(v) for v in row) for row in rows[:50]
            )
            lines.append(header + "\n" + body)
    text = "\n".join(lines)
    return text if len(text) <= ASSISTANT_MAX_PREVIEW_CHARS else text[:ASSISTANT_MAX_PREVIEW_CHARS]


def _context_value(context: Any, name: str) -> Any:
    return getattr(context, name, None)


semantic_search_tool = SemanticSearchTool()
