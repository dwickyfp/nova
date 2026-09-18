"""Pure DAG state machine for a task graph run.

No I/O, no database, no Redis. Given a graph's topology and the current state of
each node, these functions answer the only three questions a worker needs:

* which nodes are **ready** to run (every parent succeeded),
* which nodes must be **skipped** (a parent failed, or the node's own ``WHEN``
  is false), and
* whether the **graph** is terminal, and in which state.

Adopted Snowflake semantics (design §"Semantik DAG"):

* A failed parent fails the whole graph by default, and nothing below it runs.
* ``WHEN`` false skips the node *and* everything reachable from it.
* A suspended child does **not** hold the graph open: it is a terminal
  non-failure, so the graph proceeds as if it had succeeded (``SUSPENDED`` is
  reported separately for the surface, but never blocks a join).
* A join waits for **all** parents; siblings run in parallel.

Every function here is deterministic, so the unit suite can enumerate the
semantics without a StarRocks connection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.modules.task_orchestration.graph import Edge, Graph


class NodeState(StrEnum):
    """The lifecycle of one node within one graph run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    #: The native task was suspended by the engine. Terminal, and *not* a
    #: failure: the graph continues as though the node had succeeded.
    SUSPENDED = "suspended"
    #: A ``RUNNING`` row whose worker heartbeat lapsed. Treated as unknown and
    #: re-evaluated, never trusted (design §2, rule 3).
    ABANDONED = "abandoned"


class GraphState(StrEnum):
    """The lifecycle of a whole graph run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: A node in this state no longer changes on its own.
TERMINAL_NODE_STATES = frozenset(
    {
        NodeState.SUCCESS,
        NodeState.FAILED,
        NodeState.SKIPPED,
        NodeState.SUSPENDED,
    }
)

#: A parent in this state satisfies its children's dependency. ``SUSPENDED`` is
#: deliberately included: a suspended child must not hold a join open.
_SATISFIED_PARENT_STATES = frozenset({NodeState.SUCCESS, NodeState.SUSPENDED})

#: A parent in this state poisons everything below it.
_FAILED_PARENT_STATES = frozenset({NodeState.FAILED})

#: Nodes that have been resolved without producing a success (or a suspend that
#: counts as one). Their descendants are skipped rather than run.
_BLOCKING_PARENT_STATES = frozenset({NodeState.SKIPPED, NodeState.ABANDONED})


class DagError(ValueError):
    """Raised when a graph run is driven with an inconsistent topology."""


@dataclass(frozen=True)
class NodeDecision:
    """What the worker should do with one node on this transition."""

    node: str
    state: NodeState


@dataclass(frozen=True)
class GraphDecision:
    """The outcome of evaluating a graph run's current node states.

    ``ready`` is ordered so a deterministic test can assert it; the worker may
    execute them in any order. ``graph_state`` is ``None`` until the graph has
    settled, at which point it is the terminal state.
    """

    ready: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    graph_state: GraphState | None = None


def _descendants(graph: Graph, roots: Iterable[str]) -> set[str]:
    """Every node reachable from ``roots`` (excluding the roots themselves)."""
    reached: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        for child in graph.adjacency.get(node, []):
            if child not in reached:
                reached.add(child)
                stack.append(child)
    return reached


def resolve_skips(
    graph: Graph,
    states: dict[str, NodeState],
    *,
    when_false: Iterable[str] = (),
) -> dict[str, NodeState]:
    """Propagate skips: a skipped/failed parent skips every descendant.

    ``when_false`` names nodes whose ``WHEN`` evaluated false — they are skipped
    themselves, and so is everything below them (a conditional is a branch cut,
    not a no-op).

    Nodes already terminal keep their state: a node that succeeded before a
    sibling failed still reports success.
    """
    resolved = dict(states)
    seeds: list[str] = [node for node in when_false if node in graph.adjacency]
    seeds.extend(
        node
        for node, state in resolved.items()
        if state in ({NodeState.SKIPPED} | _FAILED_PARENT_STATES) and node in graph.adjacency
    )

    for node in _descendants(graph, seeds):
        if resolved.get(node, NodeState.PENDING) not in TERMINAL_NODE_STATES:
            resolved[node] = NodeState.SKIPPED

    for node in when_false:
        if resolved.get(node, NodeState.PENDING) not in TERMINAL_NODE_STATES:
            resolved[node] = NodeState.SKIPPED

    return resolved


def evaluate(
    graph: Graph,
    states: dict[str, NodeState],
    *,
    when_false: Iterable[str] = (),
) -> GraphDecision:
    """Evaluate one transition of a graph run.

    Preconditions: ``states`` holds the last persisted state of every node the
    worker knows about; nodes not present are treated as ``PENDING``. A node
    whose state is unknown to the caller (a fresh graph run) is ``PENDING``.

    The decision is idempotent: evaluating the same states twice yields the same
    answer, so an at-least-once delivery cannot advance a node twice.
    """
    when_false_set = set(when_false)
    resolved = resolve_skips(graph, states, when_false=when_false_set)

    ready: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    pending = False

    for node in graph.nodes:
        state = resolved.get(node, NodeState.PENDING)

        if state is NodeState.FAILED:
            failed.append(node)
            continue
        if state is NodeState.SKIPPED:
            skipped.append(node)
            continue
        if state in TERMINAL_NODE_STATES:
            continue

        parents = _parents_of(graph, node)
        parent_states = [resolved.get(parent, NodeState.PENDING) for parent in parents]

        if any(parent in _FAILED_PARENT_STATES for parent in parent_states):
            # A failed parent *by default* fails the graph. The node itself is
            # not failed — it never ran — but it cannot run, so it is skipped and
            # its own descendants inherit the cut.
            skipped.append(node)
            continue
        if any(parent in _BLOCKING_PARENT_STATES for parent in parent_states):
            skipped.append(node)
            continue
        if all(parent in _SATISFIED_PARENT_STATES for parent in parent_states):
            ready.append(node)
            continue
        pending = True

    graph_state: GraphState | None = None
    if failed or _has_failed_ancestor(graph, resolved):
        graph_state = GraphState.FAILED
    elif not pending and not ready:
        graph_state = GraphState.SUCCESS

    return GraphDecision(
        ready=tuple(sorted(ready)),
        skipped=tuple(sorted(skipped)),
        failed=tuple(sorted(failed)),
        graph_state=graph_state,
    )


def _parents_of(graph: Graph, node: str) -> list[str]:
    return [parent for parent, children in graph.adjacency.items() if node in children]


def _has_failed_ancestor(graph: Graph, states: dict[str, NodeState]) -> bool:
    """True when any node already failed — a failed node fails the graph.

    This is what makes a failure *global* rather than local to the failed
    node's own branch, matching the design's "Parent gagal → graph FAILED".
    """
    return any(state is NodeState.FAILED for state in states.values())


def graph_from_task_rows(
    node_names: Iterable[str], edges: Iterable[dict[str, object]]
) -> Graph:
    """Build a :class:`Graph` from ``CONFIG_TASK_EDGES`` rows.

    Edges store task *names* (design §5), so membership is resolved by name.

    Only ``edge_kind == 'after'`` edges become dependencies. A ``finalize`` edge
    means "run this after the target completes", not "the child waits for the
    parent", so folding it into the adjacency would make the finalizer run as an
    ordinary child at the wrong time. Its run semantics are wired in PR 3b; the
    row is stored now so nothing is lost, and it is filtered here so this PR does
    not silently mis-execute it.
    """
    return Graph.from_edges(
        node_names,
        [
            Edge(parent=str(edge["parent_task"]), child=str(edge["child_task"]))
            for edge in edges
            if str(edge.get("edge_kind") or "after") == "after"
        ],
    )
