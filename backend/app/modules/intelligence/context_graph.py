"""Context references and bounded traversal over the authoritative semantic catalog."""

from __future__ import annotations

from collections import deque
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence.contracts import (
    ContextEdge,
    ContextNode,
    Contract,
    KnowledgeState,
    Scope,
    SemanticRef,
    fingerprint,
)
from app.modules.intelligence.engine import CycleBudget, intelligence_service


class ConceptCreate(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    kind: Literal["domain", "system", "deployment", "incident"]
    name: str = Field(min_length=1, max_length=256)


class EdgeCreate(Contract):
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    relationship: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")


def semantic_node_id(scope: Scope, ref: SemanticRef, kind: str, name: str) -> str:
    return fingerprint(
        [
            scope.principal,
            scope.active_role,
            scope.security_context_version,
            ref.model_dump(),
            kind,
            name,
        ]
    )


async def create_concept(body: ConceptCreate, user: dict) -> ContextNode:
    scope = Scope.from_user(user)
    node = ContextNode(
        id=fingerprint([scope.principal, scope.active_role, body.operation_id]),
        scope=scope,
        name=body.name,
        kind=body.kind,
        reference_id=body.operation_id,
        authority="user_statement",
    )
    saved = await intelligence_service.repository.save("nodes", node)
    await intelligence_service._audit("CREATE_CONTEXT", saved, user)
    return saved


async def create_edge(body: EdgeCreate, user: dict) -> ContextEdge:
    service, budget = intelligence_service, CycleBudget()
    for node_id in (body.source, body.target):
        await service.get("nodes", node_id, user, budget=budget)
    edge = ContextEdge(
        id=fingerprint(body.model_dump()),
        scope=Scope.from_user(user),
        source=body.source,
        target=body.target,
        relationship=body.relationship,
    )
    saved = await service.repository.save("edges", edge)
    await service._audit("CREATE_CONTEXT", saved, user)
    return saved


async def project_semantic_view(ref: SemanticRef, user: dict) -> dict:
    service, scope = intelligence_service, Scope.from_user(user)
    row = await service.authorize_semantic(ref, user, active=True)
    model = SemanticModelIR.from_ossie(row["definition"])
    nodes, edges = [], []

    def ident(kind, name):
        return semantic_node_id(scope, ref, kind, name)

    def node(kind, name, reference):
        record = ContextNode(
            id=ident(kind, name),
            scope=scope,
            kind=kind,
            name=name,
            reference_id=reference,
            semantic=ref,
            state=KnowledgeState.VERIFIED,
            authority="published_semantic_definition",
        )
        nodes.append(record)
        return record.id

    def edge(source, target, relationship):
        edges.append(
            ContextEdge(
                id=fingerprint([source, target, relationship]),
                scope=scope,
                source=source,
                target=target,
                relationship=relationship,
                state=KnowledgeState.VERIFIED,
            )
        )

    root = node("semantic_view", model.name, ref.view_id)
    for dataset in model.datasets:
        dataset_id = node("dataset", dataset.name, dataset.source)
        edge(root, dataset_id, "contains")
        for item in dataset.fields:
            field_id = node("field", f"{dataset.name}.{item.name}", f"{dataset.source}.{item.name}")
            edge(dataset_id, field_id, "contains")
    for metric in model.metrics:
        if metric.visibility != "public":
            continue
        metric_id = node("metric", metric.name, f"{ref.view_id}:{ref.version}:{metric.name}")
        edge(metric_id, root, "defined_in")
        edge(metric_id, ident("dataset", metric.base_dataset), "computed_from")
    for relationship in model.relationships:
        edge(
            ident("dataset", relationship.from_dataset),
            ident("dataset", relationship.to_dataset),
            "joins_to",
        )
    if len(nodes) > 500 or len(edges) > 1000:
        raise HTTPException(status_code=422, detail="Context projection exceeds its object budget")
    known = {item.id for item in nodes}
    edges = [item for item in edges if item.source in known and item.target in known]
    # Stable IDs make a interrupted projection resumable without duplicating the catalog.
    for kind, records in (("nodes", nodes), ("edges", edges)):
        for record in records:
            old = await service.repository.get(kind, record.id, scope, type(record))
            if old is None:
                await service.repository.save(kind, record)
    return {"root_id": root, "nodes": len(nodes), "edges": len(edges), "semantic": ref}


async def traverse_context(node_id: str, user: dict, *, depth: int = 2, limit: int = 50) -> dict:
    if not 0 <= depth <= 4 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="Graph depth or node budget is invalid")
    service, budget = intelligence_service, CycleBudget()
    root = await service.get("nodes", node_id, user, budget=budget)
    nodes, edges, visited = {root.id: root}, {}, set()
    queue = deque([(root.id, 0)])
    while queue:
        current, level = queue.popleft()
        if current in visited or level >= depth:
            continue
        visited.add(current)
        neighbors = await service.repository.page(
            "edges", Scope.from_user(user), ContextEdge, endpoint=current, limit=101
        )
        for edge in neighbors[:100]:
            if len(edges) >= 100 and edge.id not in edges:
                continue
            other = edge.target if edge.source == current else edge.source
            try:
                await service.authorize_record(edge, user, budget)
                candidate = nodes.get(other) or await service.get(
                    "nodes", other, user, budget=budget
                )
            except HTTPException as exc:
                if exc.status_code in {403, 404, 409}:
                    continue
                raise
            if len(nodes) >= limit and other not in nodes:
                continue
            nodes[other], edges[edge.id] = candidate, edge
            if other not in visited:
                queue.append((other, level + 1))
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "bounded": True}


async def resolve_metric(ref: SemanticRef, term: str, user: dict) -> dict:
    row = await intelligence_service.authorize_semantic(ref, user, active=True)
    model = SemanticModelIR.from_ossie(row["definition"])
    candidates = [
        metric
        for metric in model.metrics
        if metric.visibility == "public"
        and term.strip().casefold()
        in {metric.name.casefold(), *(name.casefold() for name in metric.synonyms)}
    ]
    definitions = {item["name"]: item for item in row["definition"].get("metrics", [])}
    exact = [metric for metric in candidates if metric.name.casefold() == term.strip().casefold()]
    canonical = [
        metric
        for metric in candidates
        if definitions[metric.name].get("authority") == "canonical"
        and definitions[metric.name].get("owner_domain")
    ]
    selected = (
        exact[0]
        if len(exact) == 1
        else canonical[0]
        if len(canonical) == 1
        else (candidates[0] if len(candidates) == 1 else None)
    )
    return {
        "status": "resolved"
        if selected is not None
        else ("ambiguous" if candidates else "unknown"),
        "selected_metric": selected.name if selected else None,
        "resolution_method": "published-name-and-domain-authority-v1",
        "candidates": [
            {
                "name": metric.name,
                "expression": metric.expression,
                "unit": metric.unit,
                "owner_domain": definitions[metric.name].get("owner_domain"),
                "authority": definitions[metric.name].get("authority"),
                "semantic": ref,
            }
            for metric in candidates
        ],
    }


async def project_decision(decision_id: str, user: dict) -> dict:
    service, scope = intelligence_service, Scope.from_user(user)
    decision = await service.get("decisions", decision_id, user)
    if decision.scope.principal != scope.principal:
        raise HTTPException(status_code=403, detail="The owner maintains decision context")
    lineage = await service.lineage(decision_id, user)
    records, edges = [], []

    def reference(kind, record, name):
        node = ContextNode(
            id=fingerprint(
                [scope.model_dump(exclude={"session_id"}), kind, record.id, record.revision]
            ),
            scope=scope,
            kind=kind,
            name=name,
            reference_id=record.id,
            reference_revision=record.revision,
            semantic=record.semantic,
            authority="recorded_lifecycle_evidence",
            state=KnowledgeState.INFERRED,
        )
        records.append(node)
        return node.id

    def link(source, target, relationship):
        edges.append(
            ContextEdge(
                id=fingerprint([source, target, relationship]),
                scope=scope,
                source=source,
                target=target,
                relationship=relationship,
            )
        )

    news = reference("news", lineage["news"], lineage["news"].title)
    investigation = reference("investigation", lineage["investigation"], "Investigation")
    root = reference("decision", decision, decision.title)
    link(news, investigation, "investigated_by")
    link(investigation, root, "informs")
    for outcome in lineage["outcomes"]:
        target = reference("outcome", outcome, f"Outcome: {outcome.status}")
        link(root, target, "evaluated_by")
    if len(records) > 100:
        raise HTTPException(status_code=422, detail="Decision context exceeds its object budget")
    for kind, items in (("nodes", records), ("edges", edges)):
        for record in items:
            existing = await service.repository.get(kind, record.id, scope, type(record))
            if existing is None:
                await service.repository.save(kind, record)
    await service._audit("PROJECT_CONTEXT", decision, user)
    return {"root_id": root, "nodes": len(records), "edges": len(edges)}
