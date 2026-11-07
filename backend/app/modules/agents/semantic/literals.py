"""Bounded literal lookup over an already authorized semantic catalog."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.agents.semantic.ir import SemanticModelIR


async def search_literal_candidates(
    model: SemanticModelIR, literal: str, context: Any
) -> dict[str, list[str]]:
    from app.modules.assistant.tools.redaction import redact_rows
    from app.modules.query.service import query_service

    user = getattr(context, "user", None) or {}
    tokens = re.findall(r"[\w]+", literal.lower())[:8]
    if not tokens or not user.get("encrypted_password"):
        return {}
    output: dict[str, list[str]] = {}
    fields = [
        field
        for dataset in model.datasets
        for field in dataset.fields
        if field.search_strategy and field.kind.value == "dimension"
    ]
    for field in fields[:4]:
        dataset = model.dataset(field.dataset)
        if dataset is None or not re.fullmatch(r"`?[A-Za-z_][\w$ ]*`?", field.expression):
            continue
        column = quote_identifier(field.expression)
        predicates = " OR ".join(
            f"LOWER({column}) LIKE '%{token.replace('_', '')}%'" for token in tokens
        )
        try:
            results = await asyncio.wait_for(
                query_service.execute_statements(
                    sql=f"SELECT DISTINCT {column} FROM {quote_source(dataset.source)} "
                    f"WHERE ({predicates}) LIMIT 50",
                    username=user["username"],
                    encrypted_password=user["encrypted_password"],
                    database=getattr(context, "database", None),
                    schema=getattr(context, "schema_name", None),
                    role=getattr(context, "role", None),
                    session_id=getattr(context, "audit_session_id", None),
                    max_rows=50,
                    confirm_destructive=False,
                ),
                timeout=5,
            )
        except Exception:
            continue
        for result in results:
            if not result.error:
                rows = redact_rows(result.columns, result.rows)
                output[field.name] = [str(row[0]) for row in rows if row and row[0] is not None]
    return output


async def field_values(
    model: SemanticModelIR, field_name: str, words: list[str], context: Any
) -> list[str]:
    """Distinct values of one searchable dimension containing any of ``words``, as the caller.

    The lookup runs through ``query_service`` with the caller's session and active role, so
    row filters and masks decide which values exist. Only plain-column fields with a search
    strategy are searched; anything else returns nothing.
    """
    from app.modules.assistant.tools.redaction import redact_rows
    from app.modules.query.service import query_service

    user = getattr(context, "user", None) or {}
    field = model.field(field_name)
    tokens = [token for token in dict.fromkeys(
        re.findall(r"\w+", " ".join(words).lower())) if len(token) > 1][:8]
    if (field is None or not field.search_strategy or field.kind.value != "dimension"
            or not tokens or not user.get("encrypted_password")):
        return []
    dataset = model.dataset(field.dataset)
    if dataset is None or not re.fullmatch(r"`?[A-Za-z_][\w$ ]*`?", field.expression):
        return []
    column = quote_identifier(field.expression)
    predicates = " OR ".join(f"LOWER({column}) LIKE '%{token}%'" for token in tokens)
    try:
        results = await asyncio.wait_for(
            query_service.execute_statements(
                sql=f"SELECT DISTINCT {column} FROM {quote_source(dataset.source)} "
                f"WHERE ({predicates}) LIMIT 50",
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                database=getattr(context, "database", None),
                schema=getattr(context, "schema_name", None),
                role=getattr(context, "role", None) or user.get("active_role"),
                session_id=getattr(context, "audit_session_id", None) or user.get("session_id"),
                max_rows=50,
                confirm_destructive=False,
            ),
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - no lookup means no repair, never a failure
        return []
    values: list[str] = []
    for result in results:
        if not result.error:
            rows = redact_rows(result.columns, result.rows)
            values += [str(row[0]) for row in rows if row and row[0] is not None]
    return values
