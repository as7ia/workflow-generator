"""Tests for canonical v2 semantic validation."""

from __future__ import annotations

import copy
import unittest

from src.workflow import SemanticValidator


def process(process_id: str) -> dict:
    return {"id": process_id, "name": process_id, "description": "", "executable": False, "properties": {}}


def participant(participant_id: str, process_id: str) -> dict:
    return {"id": participant_id, "name": participant_id, "processRef": process_id, "properties": {}}


def lane(lane_id: str, process_id: str, participant_id: str | None = None) -> dict:
    return {"id": lane_id, "name": lane_id, "processRef": process_id, "participantRef": participant_id, "properties": {}}


def node(node_id: str, node_type: str, process_id: str, participant_id=None, lane_id=None, actor=None, **properties) -> dict:
    return {
        "id": node_id, "type": node_type, "name": node_id,
        "processRef": process_id, "participantRef": participant_id,
        "laneRef": lane_id, "actor": actor, "properties": properties,
    }


def flow(flow_id: str, source: str, target: str, process_id: str, condition=None, default=False) -> dict:
    return {
        "id": flow_id, "source": source, "target": target, "processRef": process_id,
        "condition": condition, "isDefault": default, "properties": {},
    }


def message(flow_id: str, source: str, target: str) -> dict:
    return {"id": flow_id, "source": source, "target": target, "properties": {}}


def empty_workflow() -> dict:
    return {
        "schemaVersion": "2.0", "processes": [], "participants": [], "lanes": [],
        "nodes": [], "flows": [], "messageFlows": [], "ambiguities": [],
    }


def add_linear(workflow: dict, process_id: str, prefix: str, with_participant: bool = True) -> None:
    workflow["processes"].append(process(process_id))
    participant_id = f"{prefix}_pool" if with_participant else None
    if participant_id:
        workflow["participants"].append(participant(participant_id, process_id))
    workflow["nodes"].extend([
        node(f"{prefix}_start", "startEvent", process_id, participant_id),
        node(f"{prefix}_task", "userTask", process_id, participant_id, actor="Business role"),
        node(f"{prefix}_end", "endEvent", process_id, participant_id),
    ])
    workflow["flows"].extend([
        flow(f"{prefix}_f1", f"{prefix}_start", f"{prefix}_task", process_id),
        flow(f"{prefix}_f2", f"{prefix}_task", f"{prefix}_end", process_id),
    ])


class SemanticValidatorV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = SemanticValidator()
        self.linear = empty_workflow()
        add_linear(self.linear, "process_main", "main")

    def issues(self, workflow: dict) -> list[dict]:
        return self.validator.validate(workflow)

    def codes(self, workflow: dict) -> list[str]:
        return [issue["code"] for issue in self.issues(workflow)]

    def issue(self, workflow: dict, code: str) -> dict:
        return next(issue for issue in self.issues(workflow) if issue["code"] == code)

    def test_single_process_is_valid(self) -> None:
        self.assertEqual([], self.issues(self.linear))

    def test_two_independent_processes_are_valid(self) -> None:
        workflow = copy.deepcopy(self.linear)
        add_linear(workflow, "process_client", "client")
        self.assertEqual([], self.issues(workflow))

    def test_three_independent_processes_are_valid(self) -> None:
        workflow = empty_workflow()
        for index in range(3):
            add_linear(workflow, f"process_{index}", f"p{index}")
        self.assertEqual([], self.issues(workflow))

    def test_process_without_start_event(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][0]["type"] = "userTask"
        issue = self.issue(workflow, "PROCESS_MISSING_START_EVENT")
        self.assertEqual("process_main", issue["process_id"])

    def test_process_without_end_event(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][-1]["type"] = "userTask"
        self.assertIn("PROCESS_MISSING_END_EVENT", self.codes(workflow))

    def test_unknown_node_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1]["processRef"] = "missing"
        self.assertIn("UNKNOWN_NODE_PROCESS", self.codes(workflow))

    def test_unknown_participant_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["participants"][0]["processRef"] = "missing"
        self.assertIn("UNKNOWN_PARTICIPANT_PROCESS", self.codes(workflow))

    def test_multiple_participants_for_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["participants"].append(participant("second_pool", "process_main"))
        self.assertIn("MULTIPLE_PARTICIPANTS_FOR_PROCESS", self.codes(workflow))

    def test_unknown_lane_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["lanes"].append(lane("lane_bad", "missing"))
        self.assertIn("UNKNOWN_LANE_PROCESS", self.codes(workflow))

    def test_unknown_lane_participant(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["lanes"].append(lane("lane_bad", "process_main", "missing"))
        self.assertIn("UNKNOWN_LANE_PARTICIPANT", self.codes(workflow))

    def test_lane_process_mismatch(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("process_other"))
        workflow["lanes"].append(lane("lane_bad", "process_other", "main_pool"))
        self.assertIn("LANE_PROCESS_MISMATCH", self.codes(workflow))

    def test_unknown_node_participant(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1]["participantRef"] = "missing"
        self.assertIn("UNKNOWN_NODE_PARTICIPANT", self.codes(workflow))

    def test_node_participant_process_mismatch(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("process_other"))
        workflow["participants"].append(participant("other_pool", "process_other"))
        workflow["nodes"][1]["participantRef"] = "other_pool"
        self.assertIn("NODE_PARTICIPANT_PROCESS_MISMATCH", self.codes(workflow))

    def test_unknown_node_lane(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1]["laneRef"] = "missing"
        self.assertIn("UNKNOWN_NODE_LANE", self.codes(workflow))

    def test_node_lane_process_mismatch(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("process_other"))
        workflow["lanes"].append(lane("other_lane", "process_other"))
        workflow["nodes"][1]["laneRef"] = "other_lane"
        self.assertIn("NODE_LANE_PROCESS_MISMATCH", self.codes(workflow))

    def test_node_lane_participant_mismatch(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("process_other"))
        workflow["participants"].append(participant("other_pool", "process_other"))
        workflow["lanes"].append(lane("main_lane", "process_main", "other_pool"))
        workflow["nodes"][1]["laneRef"] = "main_lane"
        self.assertIn("NODE_LANE_PARTICIPANT_MISMATCH", self.codes(workflow))

    def test_valid_sequence_flow_stays_in_process(self) -> None:
        self.assertNotIn("FLOW_PROCESS_MISMATCH", self.codes(self.linear))

    def test_sequence_flow_between_processes(self) -> None:
        workflow = copy.deepcopy(self.linear)
        add_linear(workflow, "process_other", "other")
        workflow["flows"].append(flow("cross", "main_task", "other_task", "process_main"))
        codes = self.codes(workflow)
        self.assertIn("SEQUENCE_FLOW_CROSSES_PROCESSES", codes)
        self.assertIn("FLOW_PROCESS_MISMATCH", codes)

    def test_flow_process_mismatch(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("process_other"))
        workflow["flows"][0]["processRef"] = "process_other"
        self.assertIn("FLOW_PROCESS_MISMATCH", self.codes(workflow))

    def test_unknown_flow_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["flows"][0]["processRef"] = "missing"
        self.assertIn("UNKNOWN_FLOW_PROCESS", self.codes(workflow))

    def test_missing_and_unknown_flow_endpoints(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["flows"] = [
            flow("missing_source", "", "main_task", "process_main"),
            flow("missing_target", "main_start", "", "process_main"),
            flow("unknown_source", "ghost", "main_task", "process_main"),
            flow("unknown_target", "main_task", "ghost", "process_main"),
        ]
        codes = set(self.codes(workflow))
        self.assertTrue({"MISSING_FLOW_SOURCE", "MISSING_FLOW_TARGET", "UNKNOWN_FLOW_SOURCE", "UNKNOWN_FLOW_TARGET"}.issubset(codes))

    def test_valid_message_flow_between_processes(self) -> None:
        workflow = copy.deepcopy(self.linear)
        add_linear(workflow, "process_other", "other")
        workflow["messageFlows"].append(message("message_1", "main_task", "other_task"))
        self.assertNotIn("MESSAGE_FLOW_WITHIN_SAME_PROCESS", self.codes(workflow))

    def test_message_flow_within_same_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["messageFlows"].append(message("message_1", "main_start", "main_task"))
        self.assertIn("MESSAGE_FLOW_WITHIN_SAME_PROCESS", self.codes(workflow))

    def test_missing_and_unknown_message_endpoints(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["messageFlows"] = [
            message("missing_source", "", "main_task"),
            message("missing_target", "main_task", ""),
            message("unknown_source", "ghost", "main_task"),
            message("unknown_target", "main_task", "ghost"),
        ]
        codes = set(self.codes(workflow))
        expected = {"MISSING_MESSAGE_FLOW_SOURCE", "MISSING_MESSAGE_FLOW_TARGET", "UNKNOWN_MESSAGE_FLOW_SOURCE", "UNKNOWN_MESSAGE_FLOW_TARGET"}
        self.assertTrue(expected.issubset(codes))

    def test_unreachable_node_is_scoped_to_process(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"].append(node("orphan", "serviceTask", "process_main"))
        issue = self.issue(workflow, "UNREACHABLE_NODE")
        self.assertEqual("process_main", issue["process_id"])

    def test_dead_branch_is_scoped_to_process(self) -> None:
        workflow = empty_workflow()
        workflow["processes"].append(process("p"))
        workflow["nodes"] = [
            node("start", "startEvent", "p"), node("split", "parallelGateway", "p", gatewayDirection="diverging"),
            node("ok", "serviceTask", "p"), node("dead", "serviceTask", "p"), node("end", "endEvent", "p"),
        ]
        workflow["flows"] = [
            flow("f1", "start", "split", "p"), flow("f2", "split", "ok", "p"),
            flow("f3", "split", "dead", "p"), flow("f4", "ok", "end", "p"), flow("f5", "dead", "dead", "p"),
        ]
        codes = self.codes(workflow)
        self.assertIn("NO_PATH_TO_END", codes)
        self.assertIn("DEAD_END_BRANCH", codes)

    def test_exclusive_gateway_requires_condition(self) -> None:
        workflow = self._split_workflow("exclusiveGateway")
        self.assertIn("MISSING_EXCLUSIVE_CONDITION", self.codes(workflow))

    def test_parallel_gateway_rejects_condition(self) -> None:
        workflow = self._split_workflow("parallelGateway")
        workflow["flows"][1]["condition"] = "${x}"
        self.assertIn("PARALLEL_GATEWAY_CONDITION", self.codes(workflow))

    def test_event_based_gateway_does_not_require_conditions(self) -> None:
        self.assertNotIn("MISSING_EXCLUSIVE_CONDITION", self.codes(self._split_workflow("eventBasedGateway")))

    def test_cycle_is_warning_with_process_id(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["flows"].append(flow("loop", "main_task", "main_task", "process_main"))
        issue = self.issue(workflow, "CYCLE_DETECTED")
        self.assertEqual("warning", issue["severity"])
        self.assertEqual("process_main", issue["process_id"])

    def test_cycles_in_two_processes_are_independent(self) -> None:
        workflow = copy.deepcopy(self.linear)
        add_linear(workflow, "process_other", "other")
        workflow["flows"].extend([
            flow("loop_main", "main_task", "main_task", "process_main"),
            flow("loop_other", "other_task", "other_task", "process_other"),
        ])
        cycles = [issue for issue in self.issues(workflow) if issue["code"] == "CYCLE_DETECTED"]
        self.assertEqual({"process_main", "process_other"}, {issue["process_id"] for issue in cycles})

    def test_valid_timer_duration(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1].update({"type": "intermediateCatchEvent", "properties": {"timer": {"kind": "duration", "value": "PT5M"}}})
        self.assertFalse({"INVALID_TIMER_KIND", "MISSING_TIMER_VALUE", "INVALID_TIMER_DURATION"} & set(self.codes(workflow)))

    def test_invalid_timer(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1].update({"type": "intermediateCatchEvent", "properties": {"timer": {"kind": "duration", "value": "five minutes"}}})
        self.assertIn("INVALID_TIMER_DURATION", self.codes(workflow))

    def test_invalid_timer_kind_and_missing_value(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1]["properties"] = {"timer": {"kind": "delay"}}
        codes = self.codes(workflow)
        self.assertIn("INVALID_TIMER_KIND", codes)
        self.assertIn("MISSING_TIMER_VALUE", codes)

    def test_actor_never_changes_ownership(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["nodes"][1]["actor"] = "other_pool"
        self.assertEqual([], self.issues(workflow))

    def test_message_flow_does_not_make_node_reachable(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("remote_process"))
        workflow["participants"].append(participant("remote_pool", "remote_process"))
        workflow["nodes"].extend([
            node("remote_start", "startEvent", "remote_process", "remote_pool"),
            node("remote_orphan", "serviceTask", "remote_process", "remote_pool"),
            node("remote_end", "endEvent", "remote_process", "remote_pool"),
        ])
        workflow["flows"].extend([
            flow("remote_direct", "remote_start", "remote_end", "remote_process"),
            flow("remote_to_end", "remote_orphan", "remote_end", "remote_process"),
        ])
        workflow["messageFlows"].append(message("message_1", "main_task", "remote_orphan"))
        unreachable = [issue for issue in self.issues(workflow) if issue["code"] == "UNREACHABLE_NODE"]
        self.assertTrue(any(issue["node_id"] == "remote_orphan" for issue in unreachable))

    def test_start_events_from_other_processes_do_not_mix(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(process("process_other"))
        workflow["participants"].append(participant("other_pool", "process_other"))
        workflow["nodes"].extend([
            node("other_task", "serviceTask", "process_other", "other_pool"),
            node("other_end", "endEvent", "process_other", "other_pool"),
        ])
        workflow["flows"].append(flow("other_f", "other_task", "other_end", "process_other"))
        issues = self.issues(workflow)
        self.assertTrue(any(issue["code"] == "PROCESS_MISSING_START_EVENT" and issue["process_id"] == "process_other" for issue in issues))
        self.assertTrue(any(issue["code"] == "UNREACHABLE_NODE" and issue["node_id"] == "other_task" for issue in issues))

    def test_validation_is_independent_of_json_order(self) -> None:
        workflow = copy.deepcopy(self.linear)
        baseline = self.issues(workflow)
        workflow["processes"].reverse()
        workflow["participants"].reverse()
        workflow["nodes"].reverse()
        workflow["flows"].reverse()
        self.assertEqual(baseline, self.issues(workflow))

    def test_duplicate_identifiers_have_specific_codes(self) -> None:
        workflow = copy.deepcopy(self.linear)
        workflow["processes"].append(copy.deepcopy(workflow["processes"][0]))
        workflow["participants"].append(copy.deepcopy(workflow["participants"][0]))
        workflow["lanes"] = [lane("l", "process_main"), lane("l", "process_main")]
        workflow["nodes"].append(copy.deepcopy(workflow["nodes"][0]))
        workflow["flows"].append(copy.deepcopy(workflow["flows"][0]))
        workflow["messageFlows"] = [message("m", "main_start", "main_end"), message("m", "main_start", "main_end")]
        codes = set(self.codes(workflow))
        expected = {"DUPLICATE_PROCESS_ID", "DUPLICATE_PARTICIPANT_ID", "DUPLICATE_LANE_ID", "DUPLICATE_NODE_ID", "DUPLICATE_FLOW_ID", "DUPLICATE_MESSAGE_FLOW_ID"}
        self.assertTrue(expected.issubset(codes))

    def test_issue_shape_is_stable_and_extended(self) -> None:
        issues = self.issues({})
        expected = {"code", "message", "node_id", "flow_id", "severity", "process_id", "participant_id", "lane_id"}
        self.assertTrue(issues)
        self.assertTrue(all(set(issue) == expected for issue in issues))

    @staticmethod
    def _split_workflow(gateway_type: str) -> dict:
        workflow = empty_workflow()
        workflow["processes"].append(process("p"))
        workflow["nodes"] = [
            node("start", "startEvent", "p"), node("gateway", gateway_type, "p", gatewayDirection="diverging"),
            node("a", "serviceTask", "p"), node("b", "serviceTask", "p"), node("end", "endEvent", "p"),
        ]
        workflow["flows"] = [
            flow("f1", "start", "gateway", "p"), flow("f2", "gateway", "a", "p"),
            flow("f3", "gateway", "b", "p", default=True), flow("f4", "a", "end", "p"),
            flow("f5", "b", "end", "p"),
        ]
        return workflow


if __name__ == "__main__":
    unittest.main()
