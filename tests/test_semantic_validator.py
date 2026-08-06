"""Unit tests for workflow semantic validation."""

from __future__ import annotations

import copy
import unittest

from src.workflow import SemanticValidator


def node(node_id: str, node_type: str, direction: str | None = None) -> dict:
    """Build a minimal node accepted by the semantic validator."""
    properties = {"gatewayDirection": direction} if direction else {}
    return {"id": node_id, "type": node_type, "properties": properties}


def flow(flow_id: str, source: str, target: str, condition=None, default=False, flow_type=None) -> dict:
    """Build a flow using official BPMN reference keys."""
    result = {"id": flow_id, "sourceRef": source, "targetRef": target, "condition": condition, "isDefault": default}
    if flow_type is not None:
        result["type"] = flow_type
    return result


class SemanticValidatorTests(unittest.TestCase):
    """Cover valid graphs and the principal semantic failures."""

    def setUp(self) -> None:
        self.validator = SemanticValidator()
        self.linear = {
            "nodes": [node("start", "startEvent"), node("task", "userTask"), node("end", "endEvent")],
            "flows": [flow("f1", "start", "task"), flow("f2", "task", "end")],
        }

    def codes(self, workflow: dict) -> list[str]:
        return [issue["code"] for issue in self.validator.validate(workflow)]

    def test_linear_workflow_is_valid(self) -> None:
        self.assertEqual([], self.validator.validate(self.linear))

    def test_missing_end_event(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"] = workflow["nodes"][:-1]
        workflow["flows"] = workflow["flows"][:-1]
        self.assertIn("MISSING_END_EVENT", self.codes(workflow))

    def test_flow_to_unknown_node(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["flows"][1]["targetRef"] = "unknown"
        issues = self.validator.validate(workflow)
        self.assertTrue(any(i["code"] == "INVALID_TARGET_REF" and i["flow_id"] == "f2" for i in issues))

    def test_unreachable_task(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"].append(node("orphan", "userTask"))
        workflow["flows"].append(flow("f3", "orphan", "end"))
        self.assertIn("UNREACHABLE_NODE", self.codes(workflow))

    def test_exclusive_gateway_requires_conditions(self) -> None:
        workflow = {
            "nodes": [node("start", "startEvent"), node("choice", "exclusiveGateway", "diverging"), node("a", "userTask"), node("b", "userTask"), node("end", "endEvent")],
            "flows": [flow("f1", "start", "choice"), flow("f2", "choice", "a"), flow("f3", "choice", "b", default=True), flow("f4", "a", "end"), flow("f5", "b", "end")],
        }
        issues = self.validator.validate(workflow)
        self.assertTrue(any(i["code"] == "MISSING_EXCLUSIVE_CONDITION" and i["flow_id"] == "f2" for i in issues))

    def test_parallel_gateway_is_valid_without_conditions(self) -> None:
        workflow = {
            "nodes": [node("start", "startEvent"), node("split", "parallelGateway", "diverging"), node("a", "serviceTask"), node("b", "serviceTask"), node("join", "parallelGateway", "converging"), node("end", "endEvent")],
            "flows": [flow("f1", "start", "split"), flow("f2", "split", "a"), flow("f3", "split", "b"), flow("f4", "a", "join"), flow("f5", "b", "join"), flow("f6", "join", "end")],
        }
        self.assertEqual([], self.validator.validate(workflow))

    def test_inclusive_gateway_supports_nested_conditional_branches(self) -> None:
        workflow = {
            "nodes": [node("start", "startEvent"), node("outer", "inclusiveGateway", "diverging"), node("a", "serviceTask"), node("inner", "exclusiveGateway", "diverging"), node("b", "serviceTask"), node("c", "serviceTask"), node("end", "endEvent")],
            "flows": [flow("f1", "start", "outer"), flow("f2", "outer", "a", "${a}"), flow("f3", "outer", "inner", "${other}"), flow("f4", "a", "end"), flow("f5", "inner", "b", "${b}"), flow("f6", "inner", "c", default=True), flow("f7", "b", "end"), flow("f8", "c", "end")],
        }
        self.assertEqual([], self.validator.validate(workflow))

    def test_message_flow_does_not_change_sequence_reachability(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"].append(node("remote", "serviceTask"))
        workflow["flows"].append(flow("message", "task", "remote", flow_type="messageFlow"))
        issues = self.validator.validate(workflow)
        self.assertTrue(any(i["code"] == "UNREACHABLE_NODE" and i["node_id"] == "remote" for i in issues))
        self.assertTrue(any(i["code"] == "ISOLATED_TASK" and i["node_id"] == "remote" for i in issues))

    def test_loop_is_a_warning(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["flows"].append(flow("loop", "task", "task"))
        issues = self.validator.validate(workflow)
        warning = next(issue for issue in issues if issue["code"] == "GRAPH_LOOP_DETECTED")
        self.assertEqual("warning", warning["severity"])
        self.assertFalse(any(i["severity"] == "error" for i in issues))

    def test_branch_without_path_to_end(self) -> None:
        workflow = {
            "nodes": [node("start", "startEvent"), node("split", "parallelGateway", "diverging"), node("ok", "serviceTask"), node("dead", "serviceTask"), node("end", "endEvent")],
            "flows": [flow("f1", "start", "split"), flow("f2", "split", "ok"), flow("f3", "split", "dead"), flow("f4", "ok", "end"), flow("f5", "dead", "dead")],
        }
        codes = self.codes(workflow)
        self.assertIn("NO_PATH_TO_END", codes)
        self.assertIn("DEAD_END_BRANCH", codes)

    def test_missing_keys_return_structured_errors(self) -> None:
        issues = self.validator.validate({})
        self.assertTrue(issues)
        self.assertTrue(all(set(i) == {"code", "message", "node_id", "flow_id", "severity"} for i in issues))


if __name__ == "__main__":
    unittest.main()
