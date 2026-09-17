"""Pure DAG validation — no I/O, no database, no imports from infrastructure.

Mirrors the Snowflake task-graph limits: at most 1000 nodes, 100 parents and
100 children per task, and no cycles. Every function here is deterministic and
testable without a StarRocks connection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

MAX_NODES = 1000
MAX_PARENTS = 100
MAX_CHILDREN = 100


class GraphValidationError(ValueError):
    """Raised when a task graph violates a structural limit."""


@dataclass(frozen=True)
class Edge:
    """A directed ``parent -> child`` dependency."""

    parent: str
    child: str


@dataclass
class Graph:
    """An adjacency representation of a task graph."""

    nodes: list[str] = field(default_factory=list)
    adjacency: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_edges(cls, node_names: Iterable[str], edges: Iterable[Edge]) -> Graph:
        nodes = list(dict.fromkeys(node_names))
        adjacency: dict[str, list[str]] = {n: [] for n in nodes}
        for edge in edges:
            if edge.parent not in adjacency:
                nodes.append(edge.parent)
                adjacency[edge.parent] = []
            if edge.child not in adjacency:
                nodes.append(edge.child)
                adjacency[edge.child] = []
            adjacency[edge.parent].append(edge.child)
        return cls(nodes=nodes, adjacency=adjacency)


def validate_graph(graph: Graph) -> None:
    """Validate size and acyclicity. Raises GraphValidationError on violation."""
    if len(graph.nodes) > MAX_NODES:
        raise GraphValidationError(
            f"graph has {len(graph.nodes)} nodes; the maximum is {MAX_NODES}"
        )

    indegree: dict[str, int] = {node: 0 for node in graph.nodes}
    for children in graph.adjacency.values():
        for child in set(children):
            indegree[child] = indegree.get(child, 0) + 1

    parents = _parent_counts(graph)
    children_count = {node: len(set(graph.adjacency.get(node, []))) for node in graph.nodes}
    for node in graph.nodes:
        if children_count[node] > MAX_CHILDREN:
            raise GraphValidationError(
                f"task {node!r} has {children_count[node]} children; "
                f"the maximum is {MAX_CHILDREN}"
            )
        if parents[node] > MAX_PARENTS:
            raise GraphValidationError(
                f"task {node!r} has {parents[node]} parents; the maximum is {MAX_PARENTS}"
            )

    queue = [node for node in graph.nodes if indegree[node] == 0]
    visited = 0
    remaining = dict(indegree)
    while queue:
        node = queue.pop()
        visited += 1
        for child in graph.adjacency.get(node, []):
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)

    if visited != len(graph.nodes):
        raise GraphValidationError("graph contains a cycle")


def is_acyclic(graph: Graph) -> bool:
    """Return True when the graph has no cycle."""
    try:
        validate_graph(graph)
    except GraphValidationError:
        return False
    return True


def parent_counts(graph: Graph) -> dict[str, int]:
    """Number of distinct parents per node."""
    return _parent_counts(graph)


def child_counts(graph: Graph) -> dict[str, int]:
    """Number of distinct children per node."""
    return {node: len(set(graph.adjacency.get(node, []))) for node in graph.nodes}


def _parent_counts(graph: Graph) -> dict[str, int]:
    counts: dict[str, int] = {node: 0 for node in graph.nodes}
    for children in graph.adjacency.values():
        for child in set(children):
            counts[child] = counts.get(child, 0) + 1
    return counts
