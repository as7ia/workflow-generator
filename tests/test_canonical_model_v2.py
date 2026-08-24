"""Contract tests for the canonical workflow JSON model v2."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from src.workflow import WorkflowNormalizer
from src.workflow.repair import _StandardSchemaValidator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "workflow.schema.json").read_text(encoding="utf-8"))


def canonical_workflow() -> dict:
    return {
        "schemaVersion": "2.0",
        "processes": [
            {"id": "process_main", "name": "Main", "description": "", "executable": True, "properties": {}}
        ],
        "participants": [
            {"id": "participant_main", "name": "Company", "processRef": "process_main", "properties": {}}
        ],
        "lanes": [
            {"id": "lane_ops", "name": "Operations", "processRef": "process_main", "participantRef": "participant_main", "properties": {}}
        ],
        "nodes": [
            {"id": "start", "type": "startEvent", "name": "Start", "processRef": "process_main", "participantRef": "participant_main", "laneRef": "lane_ops", "actor": None, "properties": {}},
            {"id": "end", "type": "endEvent", "name": "End", "processRef": "process_main", "participantRef": "participant_main", "laneRef": "lane_ops", "actor": None, "properties": {}},
        ],
        "flows": [
            {"id": "flow_1", "source": "start", "target": "end", "processRef": "process_main", "condition": None, "isDefault": False, "properties": {}}
        ],
        "messageFlows": [],
        "ambiguities": [],
    }


class CanonicalModelV2Tests(unittest.TestCase):
    OFFICIAL_NODE_TYPES = {
        "startEvent", "endEvent", "userTask", "serviceTask", "manualTask",
        "scriptTask", "businessRuleTask", "sendTask", "receiveTask",
        "exclusiveGateway", "parallelGateway", "inclusiveGateway",
        "eventBasedGateway", "subProcess", "intermediateCatchEvent",
        "intermediateThrowEvent",
    }

    def setUp(self) -> None:
        self.normalizer = WorkflowNormalizer()
        self.validator = _StandardSchemaValidator(SCHEMA)

    def assert_schema_valid(self, workflow: dict) -> None:
        self.assertEqual([], self.validator.validate(workflow))

    def test_single_process_workflow(self) -> None:
        result = self.normalizer.normalize(canonical_workflow())
        self.assertEqual(["process_main"], [item["id"] for item in result["processes"]])
        self.assert_schema_valid(result)

    def test_two_processes_and_message_flow(self) -> None:
        workflow = canonical_workflow()
        workflow["processes"].append({"id": "process_client", "name": "Client", "description": "", "executable": False, "properties": {}})
        workflow["participants"].append({"id": "participant_client", "name": "Client", "processRef": "process_client", "properties": {}})
        workflow["nodes"].append({"id": "client_send", "type": "sendTask", "name": "Send", "processRef": "process_client", "participantRef": "participant_client", "laneRef": None, "actor": "Client", "properties": {}})
        workflow["messageFlows"].append({"id": "message_1", "source": "client_send", "target": "end", "properties": {}})
        result = self.normalizer.normalize(workflow)
        self.assertEqual({"process_main", "process_client"}, {item["id"] for item in result["processes"]})
        self.assertEqual(("client_send", "end"), (result["messageFlows"][0]["source"], result["messageFlows"][0]["target"]))
        self.assert_schema_valid(result)

    def test_three_participants_reference_processes(self) -> None:
        workflow = canonical_workflow()
        workflow["participants"] = [
            {"id": f"participant_{index}", "name": str(index), "processRef": "process_main", "properties": {}}
            for index in range(3)
        ]
        result = self.normalizer.normalize(workflow)
        self.assertEqual(3, len(result["participants"]))
        self.assertTrue(all(item["processRef"] == "process_main" for item in result["participants"]))

    def test_multiple_lanes_have_explicit_ownership(self) -> None:
        workflow = canonical_workflow()
        workflow["lanes"].append({"id": "lane_finance", "name": "Finance", "processRef": "process_main", "participantRef": "participant_main", "properties": {}})
        result = self.normalizer.normalize(workflow)
        self.assertEqual({"process_main"}, {item["processRef"] for item in result["lanes"]})
        self.assertEqual({"participant_main"}, {item["participantRef"] for item in result["lanes"]})

    def test_node_ownership_fields_are_preserved(self) -> None:
        node = self.normalizer.normalize(canonical_workflow())["nodes"][0]
        self.assertEqual("process_main", node["processRef"])
        self.assertEqual("participant_main", node["participantRef"])
        self.assertEqual("lane_ops", node["laneRef"])

    def test_sequence_flow_has_process_ref(self) -> None:
        flow = self.normalizer.normalize(canonical_workflow())["flows"][0]
        self.assertEqual("process_main", flow["processRef"])

    def test_actor_is_business_metadata_not_ownership(self) -> None:
        workflow = canonical_workflow()
        workflow["nodes"][0].update({"actor": "Service Finance", "participantRef": None, "laneRef": None})
        node = self.normalizer.normalize(workflow)["nodes"][0]
        self.assertEqual("Service Finance", node["actor"])
        self.assertIsNone(node["participantRef"])
        self.assertIsNone(node["laneRef"])
        self.assertEqual("process_main", node["processRef"])

    def test_legacy_structure_migrates_to_v2(self) -> None:
        legacy = {
            "schemaVersion": "1.0.0",
            "process": {"id": "Main Process", "name": "Main", "description": "", "executable": False},
            "participants": [{"id": "Company", "name": "Company", "nodeRefs": ["Task A"]}],
            "nodes": [{"id": "Task A", "type": "task", "actor": "Business owner"}],
            "flows": [],
            "ambiguities": [],
        }
        result = self.normalizer.normalize(legacy)
        self.assertEqual("2.0", result["schemaVersion"])
        self.assertNotIn("process", result)
        self.assertEqual("main_process", result["nodes"][0]["processRef"])
        self.assertEqual("company", result["nodes"][0]["participantRef"])
        self.assertEqual("Business owner", result["nodes"][0]["actor"])
        self.assert_schema_valid(result)

    def test_nested_legacy_lanes_are_flattened(self) -> None:
        legacy = {
            "process": {"id": "Company Process", "name": "Company", "description": "", "executable": True},
            "participants": [{"id": "Company", "name": "Company", "lanes": [{"id": "Support", "name": "Support", "nodeRefs": ["Review"]}]}],
            "nodes": [{"id": "Review", "type": "userTask", "actor": "Agent"}],
            "flows": [], "ambiguities": [],
        }
        result = self.normalizer.normalize(legacy)
        self.assertEqual("support", result["nodes"][0]["laneRef"])
        self.assertEqual("company", result["lanes"][0]["participantRef"])
        self.assertNotIn("lanes", result["participants"][0])

    def test_legacy_three_pools_receive_distinct_processes(self) -> None:
        legacy = {
            "process": {"id": "Collaboration", "name": "Collaboration", "description": "", "executable": False},
            "participants": [
                {"id": f"Pool {index}", "name": f"Pool {index}", "nodeRefs": [f"Node {index}"]}
                for index in range(3)
            ],
            "nodes": [{"id": f"Node {index}", "type": "serviceTask"} for index in range(3)],
            "flows": [], "ambiguities": [],
        }
        result = self.normalizer.normalize(legacy)
        self.assertEqual(3, len(result["processes"]))
        self.assertEqual(3, len({node["processRef"] for node in result["nodes"]}))

    def test_source_ref_target_ref_become_source_target(self) -> None:
        workflow = canonical_workflow()
        workflow["flows"][0]["sourceRef"] = workflow["flows"][0].pop("source")
        workflow["flows"][0]["targetRef"] = workflow["flows"][0].pop("target")
        flow = self.normalizer.normalize(workflow)["flows"][0]
        self.assertEqual(("start", "end"), (flow["source"], flow["target"]))
        self.assertNotIn("sourceRef", flow)
        self.assertNotIn("targetRef", flow)

    def test_ids_and_all_references_are_updated(self) -> None:
        workflow = canonical_workflow()
        workflow["processes"][0]["id"] = "Main Process!"
        workflow["participants"][0].update({"id": "Main Pool!", "processRef": "Main Process!"})
        workflow["lanes"][0].update({"id": "Ops Lane!", "processRef": "Main Process!", "participantRef": "Main Pool!"})
        for node in workflow["nodes"]:
            node.update({"processRef": "Main Process!", "participantRef": "Main Pool!", "laneRef": "Ops Lane!"})
        workflow["flows"][0]["processRef"] = "Main Process!"
        result = self.normalizer.normalize(workflow)
        self.assertEqual("main_process", result["flows"][0]["processRef"])
        self.assertEqual("main_pool", result["nodes"][0]["participantRef"])
        self.assertEqual("ops_lane", result["nodes"][0]["laneRef"])

    def test_reference_resolution_does_not_depend_on_json_order(self) -> None:
        first = canonical_workflow()
        second = copy.deepcopy(first)
        second["nodes"].reverse()
        first_result = self.normalizer.normalize(first)
        second_result = self.normalizer.normalize(second)
        first_nodes = {node["id"]: node for node in first_result["nodes"]}
        second_nodes = {node["id"]: node for node in second_result["nodes"]}
        self.assertEqual(first_nodes, second_nodes)
        self.assertEqual(first_result["flows"], second_result["flows"])

    def test_timer_event_migrates_to_intermediate_catch_event(self) -> None:
        legacy = {
            "process": {"id": "p", "name": "P", "description": "", "executable": False},
            "participants": [], "nodes": [{"id": "wait", "type": "timerEvent", "duration": "5 minutes"}],
            "flows": [], "ambiguities": [],
        }
        node = self.normalizer.normalize(legacy)["nodes"][0]
        self.assertEqual("intermediateCatchEvent", node["type"])
        self.assertEqual({"kind": "duration", "value": "PT5M"}, node["properties"]["timer"])

    def test_schema_uses_one_official_node_type_list(self) -> None:
        declared = set(SCHEMA["$defs"]["node"]["properties"]["type"]["enum"])
        self.assertEqual(self.OFFICIAL_NODE_TYPES, declared)
        self.assertNotIn("timerEvent", declared)

    def test_schema_validates_canonical_timer_definition(self) -> None:
        workflow = canonical_workflow()
        workflow["nodes"][0].update({
            "type": "intermediateCatchEvent",
            "properties": {"timer": {"kind": "duration", "value": "PT5M"}},
        })
        self.assert_schema_valid(self.normalizer.normalize(workflow))

    def test_unknown_process_ref_is_preserved_for_semantic_validator_v2(self) -> None:
        workflow = canonical_workflow()
        workflow["nodes"][0]["processRef"] = "missing_process"
        node = self.normalizer.normalize(workflow)["nodes"][0]
        self.assertEqual("missing_process", node["processRef"])

    def test_inconsistent_participant_ref_is_not_repaired(self) -> None:
        workflow = canonical_workflow()
        workflow["nodes"][0].update({"processRef": "process_other", "participantRef": "participant_main"})
        node = self.normalizer.normalize(workflow)["nodes"][0]
        self.assertEqual("process_other", node["processRef"])
        self.assertEqual("participant_main", node["participantRef"])

    def test_inconsistent_lane_ref_is_not_repaired(self) -> None:
        workflow = canonical_workflow()
        workflow["nodes"][0].update({"processRef": "process_other", "laneRef": "lane_ops"})
        node = self.normalizer.normalize(workflow)["nodes"][0]
        self.assertEqual("process_other", node["processRef"])
        self.assertEqual("lane_ops", node["laneRef"])

    def test_v2_flow_without_process_ref_is_schema_invalid(self) -> None:
        workflow = canonical_workflow()
        workflow["flows"][0].pop("processRef")
        result = self.normalizer.normalize(workflow)
        errors = self.validator.validate(result)
        self.assertIsNone(result["flows"][0]["processRef"])
        self.assertTrue(any(error["path"] == "flows[0].processRef" for error in errors))


if __name__ == "__main__":
    unittest.main()
