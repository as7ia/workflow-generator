"""Unit tests for deterministic BPMN graph analysis."""

from __future__ import annotations

import copy
import unittest

from src.workflow import WorkflowGraphAnalyzer


def node(node_id: str, node_type: str = "serviceTask", **extra) -> dict:
    value = {"id": node_id, "type": node_type}
    value.update(extra)
    return value


def flow(flow_id: str, source: str, target: str, flow_type: str | None = None) -> dict:
    value = {"id": flow_id, "sourceRef": source, "targetRef": target}
    if flow_type:
        value["type"] = flow_type
    return value


def workflow(nodes: list[dict], flows: list[dict], **extra) -> dict:
    value = {"nodes": nodes, "flows": flows, "participants": []}
    value.update(extra)
    return value


class WorkflowGraphAnalyzerTests(unittest.TestCase):
    """Cover graph algorithms, branches, collaboration and robustness."""

    def setUp(self) -> None:
        self.analyzer = WorkflowGraphAnalyzer()
        self.linear = workflow(
            [node("start", "startEvent"), node("task"), node("end", "endEvent")],
            [flow("f1", "start", "task"), flow("f2", "task", "end")],
        )

    def test_linear_workflow(self) -> None:
        result = self.analyzer.analyze(self.linear)
        self.assertEqual({"start": 0, "task": 1, "end": 2}, result["levels"])
        self.assertEqual(["start", "task", "end"], result["topological_order"])
        self.assertEqual(["start"], result["start_nodes"])
        self.assertEqual(["end"], result["end_nodes"])

    def test_exclusive_branch(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("g", "exclusiveGateway"), node("a"), node("b"), node("j", "exclusiveGateway"), node("e", "endEvent")],
            [flow("f1", "s", "g"), flow("f2", "g", "a"), flow("f3", "g", "b"), flow("f4", "a", "j"), flow("f5", "b", "j"), flow("f6", "j", "e")],
        )
        result = self.analyzer.analyze(graph)
        self.assertEqual(["g"], result["split_gateways"])
        self.assertEqual(["j"], result["join_gateways"])
        self.assertEqual("j", result["branches"][0]["join_node"])
        self.assertEqual("exclusiveGateway", result["branches"][0]["gateway_type"])

    def test_parallel_branch_with_convergence(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("split", "parallelGateway"), node("a"), node("b"), node("join", "parallelGateway"), node("e", "endEvent")],
            [flow("1", "s", "split"), flow("2", "split", "a"), flow("3", "split", "b"), flow("4", "a", "join"), flow("5", "b", "join"), flow("6", "join", "e")],
        )
        branch = self.analyzer.analyze(graph)["branches"][0]
        self.assertEqual("join", branch["join_node"])
        self.assertFalse(branch["branches_end_separately"])

    def test_branch_without_convergence(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("g", "exclusiveGateway"), node("a"), node("b"), node("ea", "endEvent"), node("eb", "endEvent")],
            [flow("1", "s", "g"), flow("2", "g", "a"), flow("3", "g", "b"), flow("4", "a", "ea"), flow("5", "b", "eb")],
        )
        branch = self.analyzer.analyze(graph)["branches"][0]
        self.assertIsNone(branch["join_node"])
        self.assertTrue(branch["branches_end_separately"])

    def test_nested_branches(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("outer", "parallelGateway"), node("a"), node("inner", "exclusiveGateway"), node("b"), node("c"), node("j", "parallelGateway"), node("e", "endEvent")],
            [flow("1", "s", "outer"), flow("2", "outer", "a"), flow("3", "outer", "inner"), flow("4", "inner", "b"), flow("5", "inner", "c"), flow("6", "a", "j"), flow("7", "b", "j"), flow("8", "c", "j"), flow("9", "j", "e")],
        )
        branches = {item["split_gateway"]: item for item in self.analyzer.analyze(graph)["branches"]}
        self.assertIn("inner", branches["outer"]["nested_splits"])
        self.assertEqual("j", branches["inner"]["join_node"])

    def test_workflow_with_loop(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("a"), node("b"), node("e", "endEvent")],
            [flow("1", "s", "a"), flow("2", "a", "b"), flow("3", "b", "a"), flow("4", "b", "e")],
        )
        result = self.analyzer.analyze(graph)
        self.assertEqual([["a", "b"]], result["cycles"])
        self.assertEqual("b", result["back_edges"][0]["sourceRef"])
        self.assertEqual(result["levels"]["a"], result["levels"]["b"])

    def test_multiple_cycles(self) -> None:
        graph = workflow(
            [node("a"), node("b"), node("c"), node("d")],
            [flow("1", "a", "b"), flow("2", "b", "a"), flow("3", "b", "c"), flow("4", "c", "d"), flow("5", "d", "c")],
        )
        self.assertEqual([["a", "b"], ["c", "d"]], self.analyzer.analyze(graph)["cycles"])

    def test_strongly_connected_components(self) -> None:
        graph = workflow(
            [node("a"), node("b"), node("c")],
            [flow("1", "a", "b"), flow("2", "b", "a"), flow("3", "b", "c")],
        )
        self.assertEqual([["a", "b"], ["c"]], self.analyzer.analyze(graph)["strongly_connected_components"])

    def test_disconnected_graph(self) -> None:
        graph = workflow([node("a"), node("b"), node("c")], [flow("1", "a", "b")])
        self.assertEqual([["a", "b"], ["c"]], self.analyzer.analyze(graph)["components"])

    def test_levels_use_all_incoming_paths(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("a"), node("b"), node("c"), node("j"), node("e", "endEvent")],
            [flow("1", "s", "a"), flow("2", "s", "b"), flow("3", "b", "c"), flow("4", "a", "j"), flow("5", "c", "j"), flow("6", "j", "e")],
        )
        self.assertEqual(3, self.analyzer.analyze(graph)["levels"]["j"])

    def test_json_order_does_not_change_analysis(self) -> None:
        reversed_graph = {
            **self.linear,
            "nodes": list(reversed(self.linear["nodes"])),
            "flows": list(reversed(self.linear["flows"])),
        }
        first = self.analyzer.analyze(self.linear)
        second = self.analyzer.analyze(reversed_graph)
        for key in ("adjacency", "levels", "components", "strongly_connected_components", "topological_order"):
            self.assertEqual(first[key], second[key])

    def test_multiple_pools_and_lanes(self) -> None:
        graph = workflow(
            [node("a"), node("b")], [],
            participants=[
                {"id": "p1", "name": "P1", "lanes": [{"id": "l1", "name": "L1", "nodeRefs": ["a"]}]},
                {"id": "p2", "name": "P2", "lanes": [{"id": "l2", "name": "L2", "nodeRefs": ["b"]}]},
            ],
        )
        result = self.analyzer.analyze(graph)
        self.assertEqual({"a": "p1", "b": "p2"}, result["node_to_participant"])
        self.assertEqual({"a": "l1", "b": "l2"}, result["node_to_lane"])

    def test_message_flow_between_pools_has_no_warning(self) -> None:
        graph = workflow(
            [node("a", participantRef="p1"), node("b", participantRef="p2")], [],
            participants=[{"id": "p1"}, {"id": "p2"}],
            messageFlows=[flow("m", "a", "b", "messageFlow")],
        )
        codes = {warning["code"] for warning in self.analyzer.analyze(graph)["warnings"]}
        self.assertNotIn("MESSAGE_FLOW_WITHIN_PARTICIPANT", codes)
        self.assertNotIn("MESSAGE_FLOW_PARTICIPANT_UNKNOWN", codes)

    def test_sequence_flow_between_pools_warns(self) -> None:
        graph = workflow(
            [node("a", participantRef="p1"), node("b", participantRef="p2")],
            [flow("cross", "a", "b")], participants=[{"id": "p1"}, {"id": "p2"}],
        )
        self.assertIn("SEQUENCE_FLOW_CROSSES_PARTICIPANTS", {w["code"] for w in self.analyzer.analyze(graph)["warnings"]})

    def test_node_without_participant_warns(self) -> None:
        graph = workflow([node("a")], [], participants=[{"id": "p1", "nodeRefs": []}])
        self.assertIn("NODE_WITHOUT_PARTICIPANT", {w["code"] for w in self.analyzer.analyze(graph)["warnings"]})

    def test_invalid_flow_is_ignored_with_warning(self) -> None:
        graph = workflow([node("a")], [flow("bad", "a", "missing")])
        result = self.analyzer.analyze(graph)
        self.assertEqual([], result["adjacency"]["a"])
        self.assertIn("INVALID_FLOW_REFERENCE", {w["code"] for w in result["warnings"]})

    def test_original_workflow_is_preserved(self) -> None:
        original = copy.deepcopy(self.linear)
        snapshot = copy.deepcopy(original)
        self.analyzer.analyze(original)
        self.assertEqual(snapshot, original)


if __name__ == "__main__":
    unittest.main()
