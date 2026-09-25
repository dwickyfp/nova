"""Resolve published Semantic Views visible to the caller's active role."""

from __future__ import annotations

from typing import Any

from app.modules.agents.semantic.ir import SemanticModelIR


def bound_view_ids(agent: dict[str, Any]) -> list[str]:
    """Read the canonical binding, with a bridge for pre-migration agents."""
    ids = agent.get("semantic_view_ids")
    if isinstance(ids, list):
        return list(dict.fromkeys(str(item) for item in ids if item))[:16]
    legacy = agent.get("semantic_model_ids") or (
        [agent["semantic_model_id"]] if agent.get("semantic_model_id") else []
    )
    return list(dict.fromkeys(str(item) for item in legacy if item))[:16]


def _context_ids(context: Any) -> list[str]:
    ids = getattr(context, "semantic_view_ids", None)
    if isinstance(ids, list):
        return list(dict.fromkeys(str(item) for item in ids if item))[:16]
    return bound_view_ids(
        {
            "semantic_model_ids": getattr(context, "semantic_model_ids", None),
            "semantic_model_id": getattr(context, "semantic_model_id", None),
        }
    )


def _caller(context: Any) -> dict[str, Any]:
    user = dict(getattr(context, "user", None) or {})
    role = getattr(context, "role", None)
    session_id = getattr(context, "audit_session_id", None)
    if role:
        user["active_role"] = role
    if session_id:
        user["session_id"] = session_id
    return user


async def load_authorized_models(context: Any) -> list[dict[str, Any]]:
    """Load only active versions after the View service checks source/entity access."""
    cached = getattr(context, "authorized_semantic_models", None)
    if cached is not None:
        return cached
    user = _caller(context)
    if not user.get("username") or not user.get("encrypted_password"):
        return []

    from app.modules.intelligence.semantic_views import semantic_view_service

    ids = _context_ids(context)
    if ids:
        records = []
        for view_id in ids:
            record = await semantic_view_service.get_active_for_agent(
                view_id, user, agent_id=getattr(context, "agent_id", None)
            )
            if record is not None:
                records.append(record)
    elif getattr(context, "agent_id", None):
        records = []
    else:
        records = await semantic_view_service.list_active_for_agent(user)

    models: list[dict[str, Any]] = []
    scope: dict[str, list[str]] = {}
    terms: list[str] = []
    for record in records[:16]:
        definition = record.get("definition") or {}
        try:
            model = SemanticModelIR.from_ossie(definition)
        except (TypeError, ValueError):
            continue
        if not model.datasets:
            continue
        view_id = str(record.get("id") or record.get("semantic_model_id") or "")
        if not view_id:
            continue
        models.append({**record, "semantic_model_id": view_id, "_scoped_ir": model})
        scope[view_id] = [dataset.name for dataset in model.datasets]
        for metric in model.metrics:
            terms.extend((metric.name, *metric.synonyms))
        for dataset in model.datasets:
            for field in dataset.fields:
                if field.kind.value == "dimension":
                    terms.extend((field.name, *field.synonyms))
        for named in model.named_filters:
            terms.extend((named.name, *named.synonyms))
        terms.extend(example.question for example in model.examples)

    context.authorized_semantic_models = models
    context.authorized_semantic_datasets = scope
    context.semantic_routing_terms = list(dict.fromkeys(terms))
    return models
