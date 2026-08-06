"""Unit tests for deterministic hierarchical BPMN layout."""

from __future__ import annotations

import copy
import unittest

from src.bpmn import BPMNLayoutEngine
from src.bpmn.constants import (
    END_EVENT_HEIGHT,
    END_EVENT_WIDTH,
    GATEWAY_HEIGHT,
    GATEWAY_WIDTH,
    START_EVENT_HEIGHT,
    START_EVENT_WIDTH,
    SUBPROCESS_HEIGHT,
    SUBPROCESS_WIDTH,
    TASK_HEIGHT,
    TASK_WIDTH,
)


def node(node_id: str, node_type: str = "serviceTask", **extra) -> dict:
    result = {"id": node_id, "type": node_type}
    result.update(extra)
    return result


def flow(flow_id: str, source: str, target: str, flow_type: str | None = None) -> dict:
    result = {"id": flow_id, "source": source, "target": target}
    if flow_type:
        result["type"] = flow_type
    return result


def workflow(nodes: list[dict], flows: list[dict], **extra) -> dict:
    result = {"nodes": nodes, "flows": flows, "participants": []}
    result.update(extra)
    return result


def assert_no_node_overlap(test: unittest.TestCase, layout: dict) -> None:
    engine = BPMNLayoutEngine()
    items = sorted(layout["nodes"].items())
    for index, (_, first) in enumerate(items):
        for _, second in items[index + 1 :]:
            test.assertFalse(engine._rectangles_overlap(first, second))


class BPMNLayoutEngineTests(unittest.TestCase):
    """Cover hierarchy, branches, cycles, containers and determinism."""

    def setUp(self) -> None:
        self.engine = BPMNLayoutEngine()
        self.linear = workflow(
            [node("start", "startEvent"), node("task"), node("end", "endEvent")],
            [flow("f1", "start", "task"), flow("f2", "task", "end")],
        )

    def test_linear_workflow_advances_left_to_right(self) -> None:
        layout = self.engine.layout(self.linear)
        self.assertLess(layout["nodes"]["start"]["x"], layout["nodes"]["task"]["x"])
        self.assertLess(layout["nodes"]["task"]["x"], layout["nodes"]["end"]["x"])
        self.assertEqual(0, layout["nodes"]["start"]["level"])

    def test_reversed_json_order_has_same_coordinates(self) -> None:
        reversed_workflow = {
            **self.linear,
            "nodes": list(reversed(self.linear["nodes"])),
            "flows": list(reversed(self.linear["flows"])),
        }
        self.assertEqual(self.engine.layout(self.linear), self.engine.layout(reversed_workflow))

    def _branched(self, gateway_type: str, count: int = 2) -> dict:
        branch_ids = [chr(ord("a") + index) for index in range(count)]
        nodes = [node("s", "startEvent"), node("split", gateway_type)]
        nodes += [node(branch_id) for branch_id in branch_ids]
        nodes += [node("join", gateway_type), node("e", "endEvent")]
        flows = [flow("in", "s", "split")]
        flows += [flow(f"out_{branch_id}", "split", branch_id) for branch_id in branch_ids]
        flows += [flow(f"join_{branch_id}", branch_id, "join") for branch_id in branch_ids]
        flows += [flow("end", "join", "e")]
        return workflow(nodes, flows)

    def test_exclusive_branches_use_distinct_corridors(self) -> None:
        layout = self.engine.layout(self._branched("exclusiveGateway"))
        self.assertNotEqual(layout["nodes"]["a"]["y"], layout["nodes"]["b"]["y"])
        self.assertEqual(0, layout["nodes"]["split"]["branch_index"])

    def test_parallel_branches_use_distinct_corridors(self) -> None:
        layout = self.engine.layout(self._branched("parallelGateway"))
        self.assertNotEqual(layout["nodes"]["a"]["branch_index"], layout["nodes"]["b"]["branch_index"])

    def test_three_branches_are_balanced(self) -> None:
        layout = self.engine.layout(self._branched("inclusiveGateway", 3))
        self.assertEqual([-1, 0, 1], sorted(layout["nodes"][item]["branch_index"] for item in ("a", "b", "c")))

    def test_join_is_centered_between_branches(self) -> None:
        layout = self.engine.layout(self._branched("exclusiveGateway"))
        centers = [layout["nodes"][item]["y"] + layout["nodes"][item]["height"] / 2 for item in ("a", "b")]
        join_center = layout["nodes"]["join"]["y"] + layout["nodes"]["join"]["height"] / 2
        self.assertAlmostEqual(sum(centers) / 2, join_center)

    def test_nested_branches_accumulate_corridors(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("outer", "parallelGateway"), node("a"), node("inner", "exclusiveGateway"), node("b"), node("c"), node("join", "parallelGateway"), node("e", "endEvent")],
            [flow("1", "s", "outer"), flow("2", "outer", "a"), flow("3", "outer", "inner"), flow("4", "inner", "b"), flow("5", "inner", "c"), flow("6", "a", "join"), flow("7", "b", "join"), flow("8", "c", "join"), flow("9", "join", "e")],
        )
        layout = self.engine.layout(graph)
        self.assertNotEqual(layout["nodes"]["b"]["branch_index"], layout["nodes"]["c"]["branch_index"])
        assert_no_node_overlap(self, layout)

    def test_simple_loop_reserves_back_edge_channel(self) -> None:
        graph = workflow(
            [node("s", "startEvent"), node("a"), node("b"), node("e", "endEvent")],
            [flow("1", "s", "a"), flow("2", "a", "b"), flow("back", "b", "a"), flow("4", "b", "e")],
        )
        channels = self.engine.layout(graph)["metadata"]["back_edge_channels"]
        self.assertEqual("bottom", channels["back"]["side"])

    def test_multiple_loops_get_distinct_channels(self) -> None:
        graph = workflow(
            [node("a"), node("b"), node("c"), node("d")],
            [flow("1", "a", "b"), flow("back1", "b", "a"), flow("3", "b", "c"), flow("4", "c", "d"), flow("back2", "d", "c")],
        )
        channels = self.engine.layout(graph)["metadata"]["back_edge_channels"]
        self.assertEqual({0, 1}, {channel["channel_index"] for channel in channels.values()})

    def test_disconnected_components_do_not_overlap(self) -> None:
        graph = workflow([node("a"), node("b"), node("c"), node("d")], [flow("1", "a", "b"), flow("2", "c", "d")])
        layout = self.engine.layout(graph)
        assert_no_node_overlap(self, layout)
        self.assertNotEqual(layout["nodes"]["a"]["y"], layout["nodes"]["c"]["y"])

    def test_participant_without_lane_gets_implicit_lane(self) -> None:
        graph = workflow([node("a")], [], participants=[{"id": "p", "nodeRefs": ["a"]}])
        layout = self.engine.layout(graph)
        self.assertEqual("p__implicit_lane", layout["nodes"]["a"]["lane_id"])
        self.assertIn("p__implicit_lane", layout["lanes"])

    def test_participant_with_lane_contains_node(self) -> None:
        graph = workflow(
            [node("a")], [],
            participants=[{"id": "p", "lanes": [{"id": "l", "nodeRefs": ["a"]}]}],
        )
        layout = self.engine.layout(graph)
        self.assertEqual("l", layout["nodes"]["a"]["lane_id"])
        self.assertNotIn("NODE_OUTSIDE_LANE", {w["code"] for w in layout["metadata"]["warnings"]})

    def test_multiple_lanes_are_stacked(self) -> None:
        graph = workflow(
            [node("a"), node("b")], [],
            participants=[{"id": "p", "lanes": [{"id": "l1", "nodeRefs": ["a"]}, {"id": "l2", "nodeRefs": ["b"]}]}],
        )
        layout = self.engine.layout(graph)
        self.assertLess(layout["lanes"]["l1"]["y"], layout["lanes"]["l2"]["y"])

    def test_multiple_participants_are_stacked(self) -> None:
        graph = workflow(
            [node("a"), node("b")], [],
            participants=[{"id": "p1", "nodeRefs": ["a"]}, {"id": "p2", "nodeRefs": ["b"]}],
        )
        layout = self.engine.layout(graph)
        self.assertLess(layout["participants"]["p1"]["y"], layout["participants"]["p2"]["y"])
        self.assertNotIn("PARTICIPANT_OVERLAP", {w["code"] for w in layout["metadata"]["warnings"]})

    def test_message_flow_metadata_is_recorded(self) -> None:
        graph = workflow(
            [node("a", participantRef="p1"), node("b", participantRef="p2")], [],
            participants=[{"id": "p1"}, {"id": "p2"}],
            messageFlows=[flow("message", "a", "b", "messageFlow")],
        )
        connection = self.engine.layout(graph)["metadata"]["message_flow_connections"][0]
        self.assertEqual("p1", connection["source_participant"])
        self.assertEqual("p2", connection["target_participant"])

    def test_node_without_participant_is_reported(self) -> None:
        graph = workflow([node("owned"), node("orphan")], [], participants=[{"id": "p", "nodeRefs": ["owned"]}])
        layout = self.engine.layout(graph)
        self.assertIsNone(layout["nodes"]["orphan"]["participant_id"])
        self.assertIn("NODE_OUTSIDE_PARTICIPANT", {w["code"] for w in layout["metadata"]["warnings"]})

    def test_node_without_lane_is_reported(self) -> None:
        graph = workflow([node("a")], [], participants=[{"id": "p", "nodeRefs": ["a"], "lanes": []}])
        codes = {warning["code"] for warning in self.engine.layout(graph)["metadata"]["warnings"]}
        self.assertIn("IMPLICIT_LANE_ASSIGNED", codes)

    def test_layout_has_no_node_overlaps(self) -> None:
        layout = self.engine.layout(self._branched("parallelGateway", 3))
        assert_no_node_overlap(self, layout)
        self.assertNotIn("NODE_OVERLAP", {w["code"] for w in layout["metadata"]["warnings"]})

    def test_dimensions_follow_bpmn_types(self) -> None:
        types = {
            "startEvent": (START_EVENT_WIDTH, START_EVENT_HEIGHT),
            "endEvent": (END_EVENT_WIDTH, END_EVENT_HEIGHT),
            "userTask": (TASK_WIDTH, TASK_HEIGHT),
            "serviceTask": (TASK_WIDTH, TASK_HEIGHT),
            "manualTask": (TASK_WIDTH, TASK_HEIGHT),
            "scriptTask": (TASK_WIDTH, TASK_HEIGHT),
            "businessRuleTask": (TASK_WIDTH, TASK_HEIGHT),
            "exclusiveGateway": (GATEWAY_WIDTH, GATEWAY_HEIGHT),
            "parallelGateway": (GATEWAY_WIDTH, GATEWAY_HEIGHT),
            "inclusiveGateway": (GATEWAY_WIDTH, GATEWAY_HEIGHT),
            "eventBasedGateway": (GATEWAY_WIDTH, GATEWAY_HEIGHT),
            "subProcess": (SUBPROCESS_WIDTH, SUBPROCESS_HEIGHT),
        }
        for node_type, dimensions in types.items():
            with self.subTest(node_type=node_type):
                self.assertEqual(tuple(map(float, dimensions)), self.engine._get_node_size(node_type))

    def test_unknown_type_uses_generic_size_and_warning(self) -> None:
        layout = self.engine.layout(workflow([node("x", "futureActivity")], []))
        self.assertEqual((100.0, 80.0), (layout["nodes"]["x"]["width"], layout["nodes"]["x"]["height"]))
        self.assertIn("UNKNOWN_NODE_TYPE", {w["code"] for w in layout["metadata"]["warnings"]})

    def test_original_workflow_is_preserved(self) -> None:
        original = copy.deepcopy(self.linear)
        snapshot = copy.deepcopy(original)
        self.engine.layout(original)
        self.assertEqual(snapshot, original)

    def test_repeated_runs_are_deterministic(self) -> None:
        graph = self._branched("parallelGateway", 3)
        self.assertEqual(BPMNLayoutEngine().layout(graph), BPMNLayoutEngine().layout(graph))


if __name__ == "__main__":
    unittest.main()
