"""Resolve caller-visible semantic catalogs before routing or provider calls."""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.expressions import quote_source
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.runtime import scope_semantic_model, semantic_ir_to_definition


async def load_authorized_models(context: Any) -> list[dict[str, Any]]:
    cached = getattr(context, "authorized_semantic_models", None)
    if cached is not None:
        return cached
    user = getattr(context, "user", None) or {}
    ids = getattr(context, "semantic_model_ids", None) or []
    scalar = getattr(context, "semantic_model_id", None)
    if not ids and scalar:
        ids = [scalar]
    models: list[dict[str, Any]] = []
    scope: dict[str, list[str]] = {}
    terms: list[str] = []
    if not user.get("username") or not user.get("encrypted_password"):
        return models
    from app.modules.query.service import query_service

    for model_id in ids[:16]:
        owner_name = getattr(context, "agent_owner_name", None) or user["username"]
        record = await agent_repository.get_semantic_model(model_id, owner_name=owner_name)
        if not record:
            continue
        model = SemanticModelIR.from_ossie(record.get("definition") or {})
        allowed: set[str] = set()
        for dataset in model.datasets[:64]:
            try:
                result = await asyncio.wait_for(
                    query_service.execute(
                        sql=f"SELECT * FROM {quote_source(dataset.source)} WHERE 1 = 0",
                        username=user["username"],
                        encrypted_password=user["encrypted_password"],
                        database=getattr(context, "database", None),
                        schema=getattr(context, "schema_name", None),
                        role=getattr(context, "role", None),
                        session_id=getattr(context, "audit_session_id", None),
                        max_rows=0,
                    ),
                    timeout=5,
                )
                if not result.error:
                    allowed.add(dataset.name)
            except Exception:
                continue
        scope[str(model_id)] = sorted(allowed)
        scoped = scope_semantic_model(model, allowed)
        if not scoped.datasets:
            continue
        definition = semantic_ir_to_definition(scoped)
        models.append({**record, "definition": definition, "_scoped_ir": scoped})
        for metric in scoped.metrics:
            terms.extend((metric.name, *metric.synonyms))
        for dataset in scoped.datasets:
            for field in dataset.fields:
                if field.kind.value == "dimension":
                    terms.extend((field.name, *field.synonyms))
        for named in scoped.named_filters:
            terms.extend((named.name, *named.synonyms))
        terms.extend(example.question for example in scoped.examples)
    context.authorized_semantic_models = models
    context.authorized_semantic_datasets = scope
    context.semantic_routing_terms = list(dict.fromkeys(terms))
    return models
