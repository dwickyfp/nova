"""Context references and bounded traversal over the authoritative semantic catalog."""

from __future__ import annotations

from collections import deque
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from app.core.config import settings
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.intelligence.context_sources import (
    ContextSourceRef,
    authorize_context_reference,
    context_reference_id,
    read_context_source,
)
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
        source_kind="user_statement",
        authority_basis={"method": "explicit-user-statement-v1"},
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
    row = await service.authorize_semantic(ref, user)
    if row.get("status") not in {"ACTIVE", "DEPRECATED", "SUPERSEDED"}:
        raise HTTPException(status_code=404, detail="Published semantic context unavailable")
    model = SemanticModelIR.from_ossie(row["definition"])
    nodes, edges = [], []

    def ident(kind, name):
        return semantic_node_id(scope, ref, kind, name)

    def node(kind, name, reference, definition=None):
        metadata = _semantic_authority(ref, row, definition or {})
        record = ContextNode(
            id=ident(kind, name),
            scope=scope,
            kind=kind,
            name=name,
            reference_id=reference,
            semantic=ref,
            state=KnowledgeState.VERIFIED,
            authority="published_semantic_definition",
            **metadata,
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
        definition = next(
            item for item in row["definition"].get("metrics", []) if item["name"] == metric.name
        )
        metric_id = node(
            "metric", metric.name, f"{ref.view_id}:{ref.version}:{metric.name}", definition
        )
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
    nodes, _ = mark_conflicts(nodes)
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
    if getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
        usage_cache = {}
        resolved = [
            await derive_authority(node, user, budget, usage_cache=usage_cache)
            for node in nodes.values()
        ]
        resolved, conflicts = mark_conflicts(resolved)
        return {
            "nodes": resolved, "edges": list(edges.values()), "bounded": True,
            "conflicts": conflicts[:100], "conflict_count": len(conflicts),
            "authority_method": "validated-source-authority-v1",
        }
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "bounded": True}


async def resolve_metric(
    ref: SemanticRef, term: str, user: dict, *, exact: bool = False,
    include_context: bool = False,
) -> dict:
    row = await intelligence_service.authorize_semantic(ref, user)
    if include_context and row.get("status") not in {"ACTIVE", "DEPRECATED", "SUPERSEDED"}:
        raise HTTPException(status_code=404, detail="Published semantic context unavailable")
    model = SemanticModelIR.from_ossie(row["definition"])
    candidates = [
        metric
        for metric in model.metrics
        if metric.visibility == "public"
        and (
            metric.name == term if exact else term.strip().casefold()
            in {metric.name.casefold(), *(name.casefold() for name in metric.synonyms)}
        )
    ]
    definitions = {item["name"]: item for item in row["definition"].get("metrics", [])}
    exact = [metric for metric in candidates if metric.name.casefold() == term.strip().casefold()]
    canonical = [
        metric
        for metric in candidates
        if definitions[metric.name].get("authority") == "canonical"
        and definitions[metric.name].get("owner_domain")
    ]
    conflict = len(canonical) > 1 and len({
        _claim_fingerprint(definitions[metric.name]) for metric in canonical
    }) > 1
    selected = (
        None if conflict else
        exact[0]
        if len(exact) == 1
        else canonical[0]
        if len(canonical) == 1
        else (candidates[0] if len(candidates) == 1 else None)
    )
    result = {
        "status": "resolved"
        if selected is not None
        else ("ambiguous" if candidates else "unknown"),
        "selected_metric": selected.name if selected else None,
        "resolution_method": "published-name-and-domain-authority-v1",
        "conflicts": [metric.name for metric in canonical] if conflict else [],
        "candidates": [
            {
                "name": metric.name,
                "expression": metric.expression,
                "unit": metric.unit,
                "owner_domain": definitions[metric.name].get("owner_domain"),
                "authority": definitions[metric.name].get("authority"),
                "source_kind": "published_semantic",
                "authority_basis": {"method": "authorized-published-definition-v1"},
                "semantic": ref,
            }
            for metric in candidates
        ],
    }
    if include_context:
        result.update(node_id=None, selected_node=None, graph=None, semantic=ref)
        if selected is not None:
            await project_semantic_view(ref, user)
            node_id = semantic_node_id(Scope.from_user(user), ref, "metric", selected.name)
            graph = await traverse_context(node_id, user)
            result.update(
                node_id=node_id, graph=graph,
                selected_node=next(node for node in graph["nodes"] if node.id == node_id),
            )
    return result


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
            source_kind="lifecycle_evidence",
            authority_basis={"method": "authorized-lifecycle-record-v1", "kind": kind},
            validity="historical",
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
    actions = {}
    for action in lineage.get("actions", []):
        pinned = ContextSourceRef(
            kind="action", id=action.id, revision=action.revision,
            fingerprint=fingerprint(action.model_dump(mode="json")),
        )
        record = ContextNode(
            id=context_reference_id(scope, pinned), scope=scope, kind="action",
            name=f"Action: {action.status}", reference_id=action.id,
            reference_revision=action.revision, reference_fingerprint=pinned.fingerprint,
            semantic=action.semantic, state=KnowledgeState.INFERRED,
            source_kind="lifecycle_evidence", authority="recorded_lifecycle_evidence",
            authority_basis={"method": "authorized-canonical-source-v1", "kind": "action"},
            validity="historical",
        )
        records.append(record)
        actions[action.id] = record.id
        link(root, record.id, "executed_by")
    for outcome in lineage["outcomes"]:
        target = reference("outcome", outcome, f"Outcome: {outcome.status}")
        link(root, target, "evaluated_by")
        for action_id in outcome.action_ids:
            if action_id in actions:
                link(actions[action_id], target, "observed_in")
    if len(records) > 100:
        raise HTTPException(status_code=422, detail="Decision context exceeds its object budget")
    for kind, items in (("nodes", records), ("edges", edges)):
        for record in items:
            existing = await service.repository.get(kind, record.id, scope, type(record))
            if existing is None:
                await service.repository.save(kind, record)
    await service._audit("PROJECT_CONTEXT", decision, user)
    return {"root_id": root, "nodes": len(records), "edges": len(edges)}


async def project_canonical_context(ref: ContextSourceRef, user: dict) -> dict:
    if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
        raise HTTPException(404, "Lifecycle Context is unavailable")
    scope, budget = Scope.from_user(user), CycleBudget()
    nodes, edges = {}, {}

    async def add(source_ref):
        source = await read_context_source(source_ref, user, budget)
        pinned = source_ref.model_copy(update={"fingerprint": source["fingerprint"]})
        record = ContextNode(
            id=context_reference_id(scope, pinned), scope=scope, kind=pinned.kind,
            name=source["name"], reference_id=pinned.id, reference_revision=pinned.revision,
            reference_agent_id=pinned.agent_id, reference_parent_id=pinned.parent_id,
            reference_run_id=pinned.run_id, reference_fingerprint=pinned.fingerprint,
            semantic=source["semantic"], state=KnowledgeState.INFERRED,
            source_kind=source["source_kind"], authority=source["authority"],
            authority_basis={"method": "authorized-canonical-source-v1", "kind": pinned.kind},
            validity="historical" if pinned.kind not in {"dashboard", "artifact", "agent"}
            else "current",
        )
        nodes[record.id] = record
        return record.id, source["value"]

    def link(source, target, relationship):
        edge = ContextEdge(
            id=fingerprint([source, target, relationship]), scope=scope,
            source=source, target=target, relationship=relationship,
            state=KnowledgeState.INFERRED,
        )
        edges[edge.id] = edge

    async def semantic_link(parent, pin):
        row = await intelligence_service.authorize_semantic(pin, user, budget=budget)
        if row.get("status") not in {"ACTIVE", "DEPRECATED", "SUPERSEDED"}:
            raise HTTPException(404, "Published semantic Context unavailable")
        model = SemanticModelIR.from_ossie(row["definition"])
        target = semantic_node_id(scope, pin, "semantic_view", model.name)
        nodes[target] = ContextNode(
            id=target, scope=scope, kind="semantic_view", name=model.name,
            reference_id=pin.view_id, semantic=pin, state=KnowledgeState.VERIFIED,
            authority="published_semantic_definition", **_semantic_authority(pin, row, {}),
        )
        link(parent, target, "uses_semantic_version")

    root, value = await add(ref)
    if ref.kind == "mission":
        for anchor in value.get("semantic_anchors", [])[:16]:
            await semantic_link(root, SemanticRef.model_validate(anchor["semantic"]))
        for pin in value.get("release_pins", [])[:10]:
            child, release = await add(ContextSourceRef(
                kind="agent_release", id=pin["manifest_id"], agent_id=pin["agent_id"],
                run_id=pin["run_id"], fingerprint=pin["fingerprint"], parent_id=ref.id,
            ))
            link(root, child, "executed_with")
            for semantic in release["dependencies"].get("semantic_views", [])[:16]:
                await semantic_link(child, SemanticRef.model_validate(semantic))
        for obj in value.get("object_refs", [])[:30]:
            child, record = await add(ContextSourceRef(
                kind=obj["kind"], id=obj["id"], revision=obj["revision"], parent_id=ref.id,
            ))
            link(root, child, "has_lifecycle_object")
            if record.get("semantic"):
                await semantic_link(child, SemanticRef.model_validate(record["semantic"]))
        from app.modules.agents.mission import mission_service

        for deliverable in (await mission_service.deliverables(ref.id, user, budget=budget))[:10]:
            child, _ = await add(ContextSourceRef(
                kind="deliverable", id=deliverable.deliverable_id,
                parent_id=ref.id, revision=deliverable.mission_revision,
            ))
            link(root, child, "produced")
        if len(value.get("object_refs", [])) > 30:
            raise HTTPException(422, "Mission Context exceeds its projection budget")
    elif ref.kind == "agent_release":
        child, _ = await add(ContextSourceRef(kind="agent", id=ref.agent_id))
        link(child, root, "has_release")
        for pin in value["dependencies"].get("semantic_views", [])[:16]:
            await semantic_link(root, SemanticRef.model_validate(pin))
        for kind in ("skill", "tool"):
            for dependency in value["dependencies"].get(f"{kind}s", [])[:10]:
                child, _ = await add(ContextSourceRef(
                    kind=kind, id=dependency["name"], parent_id=ref.id, agent_id=ref.agent_id,
                ))
                link(root, child, "pins_dependency")
        if value["dependencies"].get("policy_fingerprint"):
            child, _ = await add(ContextSourceRef(
                kind="policy", id="agent_policy", parent_id=ref.id, agent_id=ref.agent_id,
            ))
            link(root, child, "pins_policy")
    elif value.get("semantic"):
        await semantic_link(root, SemanticRef.model_validate(value["semantic"]))
    if len(nodes) > 100 or len(edges) > 100:
        raise HTTPException(422, "Lifecycle Context exceeds its projection budget")
    for kind, records in (("nodes", nodes.values()), ("edges", edges.values())):
        for record in records:
            prior = await intelligence_service.repository.get(kind, record.id, scope, type(record))
            if prior is None:
                await intelligence_service.repository.save(kind, record)
    await intelligence_service._audit("PROJECT_CONTEXT", nodes[root], user)
    return {"root_id": root, "nodes": len(nodes), "edges": len(edges), "bounded": True}


def _claim_fingerprint(definition: dict) -> str:
    return fingerprint({
        key: definition.get(key) for key in (
            "base_dataset", "expression", "unit", "default_time_dimension", "filter", "type",
        )
    })


def _semantic_authority(ref: SemanticRef, row: dict, definition: dict) -> dict:
    return {
        "source_kind": "published_semantic",
        "authority_basis": {
            "method": "authorized-published-definition-v1", "version": ref.version,
            "fingerprint": ref.fingerprint,
            "designation": str(definition.get("authority") or "published"),
            "owner_domain": str(definition.get("owner_domain") or ""),
        },
        "validity": "current" if row.get("status") == "ACTIVE" else "historical",
        "freshness": "unknown",
        "aliases": list(dict.fromkeys(
            str(value) for value in definition.get("synonyms") or []
        ))[:32],
        "claim_fingerprint": _claim_fingerprint(definition) if definition else None,
    }


async def derive_authority(
    node: ContextNode, user: dict, budget: CycleBudget, *, usage_cache: dict | None = None,
) -> ContextNode:
    """Stored assertions and usage counts cannot assign their own authority."""
    if node.scope.model_dump(exclude={"session_id"}) != Scope.from_user(user).model_dump(
        exclude={"session_id"}
    ):
        raise HTTPException(status_code=404, detail="Context unavailable in this security context")
    update = {
        "source_kind": "unknown", "authority": None, "authority_basis": {},
        "validity": "unknown", "freshness": "unknown", "usage_count": None,
        "aliases": [], "claim_fingerprint": None, "contradictions": [],
        "state": KnowledgeState.HYPOTHESIS, "valid_from": None, "valid_until": None,
    }
    if node.semantic and node.kind in {
        "metric", "dataset", "field", "semantic_view", "query_pattern",
    }:
        ref = node.semantic
        row = await intelligence_service.authorize_semantic(ref, user, budget=budget)
        definition = row["definition"]
        model = SemanticModelIR.from_ossie(definition)
        if (
            row.get("status") in {"ACTIVE", "DEPRECATED", "SUPERSEDED"}
            and node.kind != "query_pattern"
            and node.id == semantic_node_id(node.scope, ref, node.kind, node.name)
        ):
            item = None
            if node.kind == "metric":
                item = next((
                    item for item in definition.get("metrics", [])
                    if item["name"] == node.name and item.get("visibility", "public") == "public"
                ), None)
            elif node.kind == "dataset":
                item = next((item for item in definition.get("datasets", [])
                             if item["name"] == node.name), None)
            elif node.kind == "field":
                item = next((field for dataset in definition.get("datasets", [])
                             for field in dataset.get("fields", [])
                             if f"{dataset['name']}.{field['name']}" == node.name), None)
            elif node.name == model.name:
                item = {}
            if item is not None:
                update.update(
                    _semantic_authority(ref, row, item), authority="published_semantic_definition",
                    state=KnowledgeState.VERIFIED,
                )
        elif node.id == usage_node_id(node.scope, ref, node.reference_id):
            from app.modules.agents.semantic.usage_learning import load_scoped_usage

            usage_cache = usage_cache if usage_cache is not None else {}
            key = fingerprint(ref.model_dump())
            if key not in usage_cache:
                budget.consume("items", 1)
                usage_cache[key] = await load_scoped_usage(node.scope, ref, definition)
            pattern = next((item for item in usage_cache[key]["patterns"]
                            if item["pattern_id"] == node.reference_id), None)
            if pattern:
                update.update(
                    source_kind="usage", authority="usage_observation",
                    authority_basis={
                        "method": "scoped-semantic-usage-v1", "digest": usage_cache[key]["digest"],
                    },
                    usage_count=pattern["observations"], state=KnowledgeState.INFERRED,
                    validity="historical",
                )
    elif node.kind == "rule" and node.reference_agent_id and node.reference_revision:
        from app.modules.agents.memory import governed_memories, memory_repository

        history = await memory_repository.revisions(
            node.reference_id, user_name=node.scope.principal,
            agent_id=node.reference_agent_id, role_name=node.scope.active_role,
        )
        source = next((item for item in history if item.revision == node.reference_revision), None)
        if source and await governed_memories(
            [{"knowledge": source.model_dump(mode="json")}], user
        ):
            reviewed = (
                source.state == KnowledgeState.VERIFIED and source.reviewed_by is not None
                and not source.needs_revalidation
            )
            update.update(
                source_kind="reviewed_rule" if reviewed else "user_statement",
                authority="reviewed_knowledge" if reviewed else "user_statement",
                authority_basis={
                    "method": "authorized-knowledge-revision-v1", "revision": source.revision,
                },
                state=source.state, validity="historical",
                claim_fingerprint=fingerprint([source.fact, source.definition]),
            )
    elif node.kind in {
        "mission", "action", "deliverable", "agent_release", "dashboard", "artifact",
        "document", "agent",
        "skill", "tool",
    } or (node.kind == "policy" and node.reference_parent_id):
        source = await authorize_context_reference(node, user, budget)
        update.update(
            source_kind=source["source_kind"], authority=source["authority"],
            authority_basis={"method": "authorized-canonical-source-v1", "kind": node.kind},
            state=KnowledgeState.INFERRED, validity="historical",
        )
    elif node.kind in {"decision", "outcome", "news", "investigation"} and node.reference_parent_id:
        await authorize_context_reference(node, user, budget)
        update.update(
            source_kind="lifecycle_evidence", authority="recorded_lifecycle_evidence",
            authority_basis={"method": "authorized-mission-canonical-source-v1"},
            state=KnowledgeState.INFERRED, validity="historical",
        )
    elif node.kind in {"decision", "outcome", "news", "investigation"}:
        kind = {
            "decision": "decisions", "outcome": "outcomes",
            "news": "news", "investigation": "investigations",
        }[node.kind]
        source = await intelligence_service.get(
            kind, node.reference_id, user, budget=budget, revision=node.reference_revision,
        )
        update.update(
            source_kind="lifecycle_evidence", authority="recorded_lifecycle_evidence",
            authority_basis={
                "method": "authorized-lifecycle-record-v1", "revision": source.revision,
            },
            state=KnowledgeState.INFERRED, validity="historical",
        )
    elif node.kind in {"domain", "system", "deployment", "incident"} and node.id == fingerprint(
        [node.scope.principal, node.scope.active_role, node.reference_id]
    ):
        update.update(
            source_kind="user_statement", authority="user_statement",
            authority_basis={"method": "explicit-user-statement-v1"},
        )
    return node.model_copy(update=update)


def authority_precedence(node: ContextNode) -> tuple[int, int]:
    return (
        {"published_semantic": 4, "reviewed_rule": 3, "lifecycle_evidence": 2,
         "agent_release": 2, "studio_reference": 1, "user_statement": 1,
         "usage": 0, "unknown": -1}[node.source_kind],
        int(node.authority_basis.get("designation") == "canonical"),
    )


def mark_conflicts(nodes: list[ContextNode]) -> tuple[list[ContextNode], list[dict]]:
    terms: dict[tuple[str, str], list[ContextNode]] = {}
    for node in nodes:
        if node.claim_fingerprint and node.source_kind in {"published_semantic", "reviewed_rule"}:
            for term in {node.name.casefold(), *(alias.casefold() for alias in node.aliases)}:
                terms.setdefault((node.kind, term), []).append(node)
    contradictions: dict[str, set[str]] = {}
    conflicts = []
    for (kind, term), candidates in sorted(terms.items()):
        precedence = max(authority_precedence(node) for node in candidates)
        authoritative = [node for node in candidates if authority_precedence(node) == precedence]
        if len({node.claim_fingerprint for node in authoritative}) < 2:
            continue
        ids = sorted(node.id for node in authoritative)
        conflicts.append({"kind": kind, "term": term, "node_ids": ids, "resolved": False})
        for node in authoritative:
            contradictions.setdefault(node.id, set()).update(
                other for other in ids if other != node.id
            )
    return [
        node.model_copy(update={
            "state": KnowledgeState.CONFLICTED,
            "contradictions": sorted(contradictions[node.id])[:100],
        }) if node.id in contradictions else node for node in nodes
    ], conflicts


def usage_node_id(scope: Scope, ref: SemanticRef, pattern_id: str) -> str:
    return fingerprint([
        scope.model_dump(exclude={"session_id"}), ref.model_dump(), "usage", pattern_id,
    ])


async def project_usage_context(ref: SemanticRef, user: dict) -> dict:
    if not getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False):
        raise HTTPException(status_code=404, detail="Usage learning is unavailable")
    from app.modules.agents.semantic.usage_learning import load_scoped_usage

    scope = Scope.from_user(user)
    row = await intelligence_service.authorize_semantic(ref, user, active=True)
    summary = await load_scoped_usage(scope, ref, row["definition"])
    ids = []
    for pattern in summary["patterns"][:100]:
        node = ContextNode(
            id=usage_node_id(scope, ref, pattern["pattern_id"]), scope=scope, kind="query_pattern",
            name=", ".join(pattern["metrics"])[:256], reference_id=pattern["pattern_id"],
            semantic=ref, state=KnowledgeState.INFERRED, authority="usage_observation",
            source_kind="usage", authority_basis={
                "method": "scoped-semantic-usage-v1", "digest": summary["digest"],
            }, validity="historical", usage_count=pattern["observations"],
        )
        prior = await intelligence_service.repository.get("nodes", node.id, scope, ContextNode)
        saved = await intelligence_service.repository.save(
            "nodes", node, expected_revision=prior.revision if prior else 0,
        )
        ids.append(saved.id)
    if ids:
        await intelligence_service._audit("PROJECT_USAGE_CONTEXT", node, user)
    return {"node_ids": ids, "usage_digest": summary["digest"], "bounded": True}
