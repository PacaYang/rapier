"""Unit tests for ExecutionGraph operations (DAG, cycles, topological sort)."""

import pytest

from rapier.core.task import Task
from rapier.core.execution_graph import ExecutionGraph
from rapier.core.enums import TaskStatus
from rapier.core.exceptions import DependencyError


@pytest.fixture
def diamond_graph(dummy_method):
    """A diamond DAG:  a -> b, a -> c, b -> d, c -> d."""
    graph = ExecutionGraph()
    for tid in ("a", "b", "c", "d"):
        graph.add_node(Task(task_id=tid, method=dummy_method, inputs={}))
    graph.add_edge("a", "b")
    graph.add_edge("a", "c")
    graph.add_edge("b", "d")
    graph.add_edge("c", "d")
    return graph


class TestExecutionGraphDAG:
    def test_topological_order_valid(self, diamond_graph):
        order = diamond_graph.get_topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_edge_to_missing_node_raises(self, dummy_method):
        graph = ExecutionGraph()
        graph.add_node(Task(task_id="a", method=dummy_method, inputs={}))
        with pytest.raises(DependencyError):
            graph.add_edge("a", "missing")

    def test_self_loop_raises(self, dummy_method):
        graph = ExecutionGraph()
        graph.add_node(Task(task_id="a", method=dummy_method, inputs={}))
        with pytest.raises(DependencyError):
            graph.add_edge("a", "a")

    def test_diamond_ready_progression(self, diamond_graph):
        # Initially only 'a' is ready
        assert [t.task_id for t in diamond_graph.get_ready_tasks()] == ["a"]

        # After 'a', both b and c are ready
        diamond_graph.mark_complete("a", TaskStatus.COMPLETED)
        ready_ids = sorted(t.task_id for t in diamond_graph.get_ready_tasks())
        assert ready_ids == ["b", "c"]

        # 'd' not ready until both b and c complete
        diamond_graph.mark_complete("b", TaskStatus.COMPLETED)
        ready_ids = sorted(t.task_id for t in diamond_graph.get_ready_tasks())
        assert ready_ids == ["c"]

        diamond_graph.mark_complete("c", TaskStatus.COMPLETED)
        ready_ids = sorted(t.task_id for t in diamond_graph.get_ready_tasks())
        assert ready_ids == ["d"]

    def test_descendants(self, diamond_graph):
        assert diamond_graph.get_all_downstream_tasks("a") == {"b", "c", "d"}
        assert diamond_graph.get_all_downstream_tasks("d") == set()

    def test_is_complete_with_mixed_terminal_states(self, diamond_graph):
        diamond_graph.mark_complete("a", TaskStatus.COMPLETED)
        diamond_graph.mark_complete("b", TaskStatus.FAILED)
        diamond_graph.mark_complete("c", TaskStatus.SKIPPED)
        diamond_graph.mark_complete("d", TaskStatus.CANCELLED)
        assert diamond_graph.is_complete()

    def test_aliases(self, diamond_graph):
        assert diamond_graph.get_upstream("d") == diamond_graph.get_upstream_tasks("d")
        assert diamond_graph.get_downstream("a") == diamond_graph.get_downstream_tasks("a")
