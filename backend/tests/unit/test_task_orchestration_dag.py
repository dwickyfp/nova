"""Unit tests for the DAG state machine (NOVA-36).

These pin the Snowflake-derived semantics the design mandates, with no engine
and no Redis:

* a failed parent fails the graph and nothing below it runs,
* ``WHEN`` false skips the node and its descendants,
* a suspended child does not hold the graph open,
* a join waits for **all** parents while siblings run in parallel,
* evaluation is idempotent, so at-least-once delivery cannot double-advance.
"""

from __future__ import annotations

from app.modules.task_orchestration.dag import (
    GraphState,
    NodeState,
    evaluate,
    graph_from_task_rows,
    resolve_skips,
)
from app.modules.task_orchestration.graph import Edge, Graph


def chain() -> Graph:
    """A -> B -> [C, D] — the design's canonical graph."""
    return Graph.from_edges(
        ["A", "B", "C", "D"],
        [Edge("A", "B"), Edge("B", "C"), Edge("B", "D")],
    )


def diamond() -> Graph:
    """A -> B, A -> C, [B, C] -> D — a real join."""
    return Graph.from_edges(
        ["A", "B", "C", "D"],
        [Edge("A", "B"), Edge("A", "C"), Edge("B", "D"), Edge("C", "D")],
    )


def success(*nodes: str) -> dict[str, NodeState]:
    return {node: NodeState.SUCCESS for node in nodes}


class TestSequentialAndParallel:
    def test_only_roots_start(self):
        assert evaluate(chain(), {}).ready == ("A",)

    def test_a_runs_then_b(self):
        assert evaluate(chain(), success("A")).ready == ("B",)

    def test_b_runs_c_and_d_in_parallel(self):
        assert evaluate(chain(), success("A", "B")).ready == ("C", "D")

    def test_whole_chain_settles_success(self):
        decision = evaluate(chain(), success("A", "B", "C", "D"))
        assert decision.ready == ()
        assert decision.graph_state == GraphState.SUCCESS

    def test_not_terminal_while_a_node_is_pending(self):
        assert evaluate(chain(), success("A", "B")).graph_state is None


class TestJoinWaitsForAllParents:
    def test_join_not_ready_with_one_parent(self):
        decision = evaluate(diamond(), success("A", "B"))
        assert "D" not in decision.ready
        assert "C" in decision.ready

    def test_join_ready_when_both_parents_succeeded(self):
        assert "D" in evaluate(diamond(), success("A", "B", "C")).ready

    def test_join_not_ready_when_one_parent_pending(self):
        decision = evaluate(diamond(), success("A", "B"))
        assert decision.ready == ("C",)
        assert decision.graph_state is None


class TestFailureSemantics:
    def test_failed_parent_fails_the_graph(self):
        decision = evaluate(chain(), {"A": NodeState.SUCCESS, "B": NodeState.FAILED})
        assert decision.graph_state == GraphState.FAILED

    def test_descendants_of_failed_parent_are_skipped_not_run(self):
        decision = evaluate(chain(), {"A": NodeState.SUCCESS, "B": NodeState.FAILED})
        assert decision.ready == ()
        assert set(decision.skipped) == {"C", "D"}

    def test_failure_above_a_succeeded_sibling_still_fails_graph(self):
        """A failure is global — a branch that already succeeded does not save it."""
        decision = evaluate(
            diamond(),
            {"A": NodeState.SUCCESS, "B": NodeState.SUCCESS, "C": NodeState.FAILED},
        )
        assert decision.graph_state == GraphState.FAILED

    def test_failed_node_is_reported(self):
        decision = evaluate(chain(), {"A": NodeState.SUCCESS, "B": NodeState.FAILED})
        assert decision.failed == ("B",)


class TestWhenFalse:
    def test_when_false_skips_the_node_and_descendants(self):
        decision = evaluate(chain(), success("A"), when_false=["B"])
        assert "B" in decision.skipped
        assert set(decision.skipped) == {"B", "C", "D"}
        assert decision.ready == ()

    def test_when_false_on_a_leaf_does_not_touch_its_parents(self):
        decision = evaluate(chain(), success("A", "B"), when_false=["C"])
        assert "C" in decision.skipped
        assert "D" in decision.ready

    def test_when_false_leader_still_settles_success(self):
        decision = evaluate(chain(), success("A", "C", "D"), when_false=["B"])
        assert decision.graph_state == GraphState.SUCCESS

    def test_explicit_skip_propagates_without_when(self):
        states = resolve_skips(chain(), {"A": NodeState.SKIPPED})
        assert set(states.values()) == {NodeState.SKIPPED}


class TestSuspendDoesNotHangTheGraph:
    def test_suspended_child_does_not_block_the_graph(self):
        decision = evaluate(
            chain(),
            {
                "A": NodeState.SUCCESS,
                "B": NodeState.SUSPENDED,
                "C": NodeState.SUCCESS,
                "D": NodeState.SUSPENDED,
            },
        )
        assert decision.graph_state == GraphState.SUCCESS

    def test_suspended_parent_still_satisfies_a_join(self):
        """A suspended parent counts as satisfied so the join can proceed."""
        decision = evaluate(
            diamond(),
            {"A": NodeState.SUCCESS, "B": NodeState.SUSPENDED, "C": NodeState.SUCCESS},
        )
        assert "D" in decision.ready


class TestIdempotency:
    def test_same_states_yield_the_same_decision(self):
        states = success("A", "B")
        assert evaluate(chain(), states) == evaluate(chain(), states)

    def test_terminal_states_do_not_reopen_work(self):
        states = success("A", "B", "C", "D")
        decision = evaluate(chain(), states)
        assert decision.ready == ()
        assert decision.graph_state == GraphState.SUCCESS


class TestGraphFromRows:
    def test_edges_by_name_build_the_topology(self):
        graph = graph_from_task_rows(
            ["A", "B"],
            [
                {"parent_task": "A", "child_task": "B"},
            ],
        )
        assert graph.adjacency["A"] == ["B"]
        assert set(graph.nodes) == {"A", "B"}

    def test_standalone_node_has_no_edges(self):
        graph = graph_from_task_rows(["solo"], [])
        assert graph.adjacency["solo"] == []
        assert evaluate(graph, {}).ready == ("solo",)
