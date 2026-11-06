"""Typed relationship registry and graph consistency checks.

Relations describe meaning between concepts. ``RELATED_TO`` is relevance, not
causation, and no relation type here asserts cause and effect.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from app.modules.ontology.contracts import Concept, ConceptKind, Relation, RelationType

K = ConceptKind
_ANY = frozenset(ConceptKind)
_QUANTITIES = frozenset({K.METRIC, K.MEASURE})


@dataclass(frozen=True)
class RelationRule:
    sources: frozenset[ConceptKind]
    targets: frozenset[ConceptKind]
    acyclic: bool = False
    same_kind: bool = False
    symmetric: bool = False
    description: str = ""


RULES: dict[RelationType, RelationRule] = {
    RelationType.IS_A: RelationRule(
        _ANY, _ANY, acyclic=True, same_kind=True, description="Taxonomy; never a cycle"
    ),
    RelationType.HAS_PART: RelationRule(
        frozenset({K.BUSINESS_ENTITY, K.PROCESS}),
        frozenset({K.BUSINESS_ENTITY, K.PROCESS}),
        acyclic=True,
        description="Part-of between entities or processes",
    ),
    RelationType.DERIVES: RelationRule(
        frozenset({K.METRIC, K.MEASURE, K.DIMENSION, K.TERM, K.BUSINESS_ENTITY}),
        frozenset({K.METRIC, K.MEASURE, K.DIMENSION, K.TERM, K.BUSINESS_ENTITY}),
        acyclic=True,
        description="Business lineage from a source concept to a derived one",
    ),
    RelationType.MEASURES: RelationRule(
        _QUANTITIES,
        frozenset({K.BUSINESS_ENTITY, K.PROCESS, K.GOAL}),
        description="A metric measures an entity, process or goal",
    ),
    RelationType.APPLIES_TO: RelationRule(
        frozenset({K.POLICY}), _ANY, description="A policy applies to a concept"
    ),
    RelationType.EQUIVALENT_TO: RelationRule(
        _ANY, _ANY, same_kind=True, symmetric=True,
        description="Approved semantic equivalence; every affected steward approves",
    ),
    RelationType.RELATED_TO: RelationRule(
        _ANY, _ANY, symmetric=True, description="General relevance; not causation"
    ),
    RelationType.AFFECTS_GOAL: RelationRule(
        frozenset({K.METRIC, K.MEASURE, K.BUSINESS_EVENT_TYPE}),
        frozenset({K.GOAL}),
        description="Business relevance to a goal; not causation",
    ),
}


def relation_issues(relation: Relation, concepts: dict[str, Concept]) -> list[str]:
    """Problems with one active relation in a given concept state."""
    issues = []
    source = concepts.get(relation.source_id)
    target = concepts.get(relation.target_id)
    for label, concept, concept_id in (
        ("source", source, relation.source_id),
        ("target", target, relation.target_id),
    ):
        if concept is None or concept.status != "APPROVED":
            issues.append(f"{relation.type.value} {label} {concept_id} is not an approved concept")
    if source is None or target is None or issues:
        return issues
    rule = RULES[relation.type]
    if source.kind not in rule.sources or target.kind not in rule.targets:
        issues.append(
            f"{relation.type.value} cannot link {source.kind.value} to {target.kind.value}"
        )
    if rule.same_kind and source.kind != target.kind:
        issues.append(f"{relation.type.value} links concepts of the same kind only")
    return issues


def cycle_issues(relations: list[Relation]) -> list[str]:
    issues = []
    for relation_type, rule in RULES.items():
        if not rule.acyclic:
            continue
        graph: dict[str, list[str]] = defaultdict(list)
        for relation in relations:
            if relation.type == relation_type and relation.status == "ACTIVE":
                graph[relation.source_id].append(relation.target_id)
        if _has_cycle(graph):
            issues.append(f"{relation_type.value} relations form a cycle")
    return issues


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}
    for start in list(graph):
        if state.get(start):
            continue
        stack = [(start, iter(graph.get(start, ())))]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
            elif state.get(child) == 1:
                return True
            elif not state.get(child):
                state[child] = 1
                stack.append((child, iter(graph.get(child, ()))))
    return False


def graph_issues(concepts: dict[str, Concept], relations: list[Relation]) -> list[str]:
    issues = []
    active = [relation for relation in relations if relation.status == "ACTIVE"]
    for relation in active:
        issues.extend(relation_issues(relation, concepts))
    issues.extend(cycle_issues(active))
    return issues


def neighbourhood(
    start: str,
    relations: list[Relation],
    visible: set[str],
    *,
    depth: int = 1,
    fanout: int = 50,
) -> tuple[list[str], list[Relation]]:
    """Visible concepts within ``depth`` hops; hidden ones are never crossed."""
    if not 1 <= depth <= 2:
        raise ValueError("Traversal depth is 1 or 2")
    adjacency: dict[str, list[Relation]] = defaultdict(list)
    for relation in relations:
        if relation.status != "ACTIVE":
            continue
        if relation.source_id in visible and relation.target_id in visible:
            adjacency[relation.source_id].append(relation)
            adjacency[relation.target_id].append(relation)
    reached = {start}
    edges: dict[str, Relation] = {}
    queue = deque([(start, 0)])
    while queue:
        node, distance = queue.popleft()
        if distance == depth:
            continue
        for relation in sorted(adjacency[node], key=lambda item: item.id)[:fanout]:
            edges[relation.id] = relation
            other = relation.target_id if relation.source_id == node else relation.source_id
            if other not in reached:
                reached.add(other)
                queue.append((other, distance + 1))
    reached.discard(start)
    return sorted(reached), sorted(edges.values(), key=lambda item: item.id)
