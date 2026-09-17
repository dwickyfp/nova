"""Pure unit tests for DAG validation — no database, no network."""

from __future__ import annotations

import pytest

from app.modules.task_orchestration.graph import (
    MAX_CHILDREN,
    MAX_NODES,
    MAX_PARENTS,
    Edge,
    Graph,
    GraphValidationError,
    child_counts,
    is_acyclic,
    parent_counts,
    validate_graph,
)


def build(nodes: list[str], pairs: list[tuple[str, str]]) -> Graph:
    return Graph.from_edges(nodes, [Edge(parent=p, child=c) for p, c in pairs])


class TestAcceptsValidGraphs:
    def test_diamond_a_to_b_to_c_d(self):
        graph = build(["A", "B", "C", "D"], [("A", "B"), ("B", "C"), ("B", "D")])
        validate_graph(graph)
        assert is_acyclic(graph)
        assert parent_counts(graph) == {"A": 0, "B": 1, "C": 1, "D": 1}
        assert child_counts(graph) == {"A": 1, "B": 2, "C": 0, "D": 0}

    def test_multi_parent_join(self):
        graph = build(["A", "B", "C"], [("A", "C"), ("B", "C")])
        validate_graph(graph)
        assert parent_counts(graph)["C"] == 2

    def test_single_node(self):
        validate_graph(build(["A"], []))

    def test_isolated_nodes_are_allowed(self):
        validate_graph(build(["A", "B"], []))


class TestRejectsCycles:
    def test_direct_cycle(self):
        with pytest.raises(GraphValidationError, match="cycle"):
            validate_graph(build(["A", "B"], [("A", "B"), ("B", "A")]))

    def test_self_loop(self):
        with pytest.raises(GraphValidationError, match="cycle"):
            validate_graph(build(["A"], [("A", "A")]))

    def test_indirect_cycle(self):
        with pytest.raises(GraphValidationError, match="cycle"):
            validate_graph(build(["A", "B", "C"], [("A", "B"), ("B", "C"), ("C", "A")]))

    def test_is_acyclic_false_for_cycle(self):
        assert not is_acyclic(build(["A", "B"], [("A", "B"), ("B", "A")]))


class TestRejectsSizeLimits:
    def test_too_many_nodes(self):
        nodes = [f"t{i}" for i in range(MAX_NODES + 1)]
        with pytest.raises(GraphValidationError, match="nodes"):
            validate_graph(build(nodes, []))

    def test_exactly_max_nodes_is_accepted(self):
        nodes = [f"t{i}" for i in range(MAX_NODES)]
        validate_graph(build(nodes, []))

    def test_too_many_children(self):
        children = [("root", f"leaf{i}") for i in range(MAX_CHILDREN + 1)]
        graph = build(["root", *(c[1] for c in children)], children)
        with pytest.raises(GraphValidationError, match="children"):
            validate_graph(graph)

    def test_exactly_max_children_is_accepted(self):
        children = [("root", f"leaf{i}") for i in range(MAX_CHILDREN)]
        graph = build(["root", *(c[1] for c in children)], children)
        validate_graph(graph)

    def test_too_many_parents(self):
        parents = [(f"p{i}", "join") for i in range(MAX_PARENTS + 1)]
        graph = build([*(p[0] for p in parents), "join"], parents)
        with pytest.raises(GraphValidationError, match="parents"):
            validate_graph(graph)

    def test_exactly_max_parents_is_accepted(self):
        parents = [(f"p{i}", "join") for i in range(MAX_PARENTS)]
        graph = build([*(p[0] for p in parents), "join"], parents)
        validate_graph(graph)


class TestFromEdges:
    def test_edges_referencing_unknown_nodes_are_added(self):
        graph = Graph.from_edges([], [Edge(parent="A", child="B")])
        assert set(graph.nodes) == {"A", "B"}

    def test_duplicate_edges_do_not_duplicate_nodes(self):
        graph = Graph.from_edges(
            ["A", "B"], [Edge(parent="A", child="B"), Edge(parent="A", child="B")]
        )
        assert graph.nodes == ["A", "B"]
        assert child_counts(graph)["A"] == 1
