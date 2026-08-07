"""Tests for deterministic sequence/message flow pool boundaries."""

from __future__ import annotations

import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from src.bpmn.constants import BPMN_NS
from src.generation import WorkflowCompiler
from src.workflow import SemanticValidator, WorkflowGraphAnalyzer


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "workflow.schema.json"


class FakeGenerator:
    """Return a deep-copied workflow for end-to-end compilation."""

    def __init__(self, workflow: dict) -> None:
        self.workflow = workflow

    def generate_json(self, prompt: str) -> dict:
        del prompt
        return copy.deepcopy(self.workflow)


def boundary_workflow() -> dict:
    """Return two explicitly owned pools with internal and crossing flows."""
    return {
        "nodes": [
            {"id": "a1", "type": "startEvent"},
            {"id": "a2", "type": "endEvent"},
            {"id": "b1", "type": "startEvent"},
            {"id": "b2", "type": "endEvent"},
        ],
        "participants": [
            {"id": "a", "nodeRefs": ["a1", "a2"]},
            {"id": "b", "lanes": [{"id": "b_lane", "nodeRefs": ["b1", "b2"]}]},
        ],
        "flows": [
            {"id": "inside", "source": "a1", "target": "a2", "condition": None},
            {"id": "cross", "name": "Notify", "source": "a2", "target": "b1", "condition": "approved", "isDefault": True},
        ],
        "messageFlows": [
            {"id": "existing_message", "source": "a1", "target": "b2", "type": "messageFlow"}
        ],
    }


def three_pool_workflow() -> dict:
    """Return a generic Client–Entreprise–Banque collaboration."""
    pools = (
        ("client", "Client"),
        ("company", "Entreprise"),
        ("bank", "Banque"),
    )
    nodes = []
    flows = []
    participants = []
    for participant_id, name in pools:
        start = f"{participant_id}_start"
        task = f"{participant_id}_task"
        end = f"{participant_id}_end"
        nodes.extend(
            [
                {"id": start, "type": "startEvent", "name": f"Start {name}", "actor": None, "properties": {}},
                {"id": task, "type": "userTask", "name": f"Action {name}", "actor": participant_id, "properties": {}},
                {"id": end, "type": "endEvent", "name": f"End {name}", "actor": None, "properties": {}},
            ]
        )
        flows.extend(
            [
                {"id": f"{participant_id}_flow_1", "source": start, "target": task, "condition": None, "isDefault": False},
                {"id": f"{participant_id}_flow_2", "source": task, "target": end, "condition": None, "isDefault": False},
            ]
        )
        participants.append(
            {
                "id": participant_id,
                "name": name,
                "type": "human" if participant_id != "bank" else "externalOrganization",
                "nodeRefs": [start, task, end],
                "lanes": [{"id": f"{participant_id}_lane", "name": name, "nodeRefs": [start, task, end]}],
            }
        )
    return {
        "schemaVersion": "1.0.0",
        "process": {"id": "business_exchange", "name": "Échange tripartite", "description": "", "executable": False},
        "participants": participants,
        "nodes": nodes,
        "flows": flows,
        "messageFlows": [
            {"id": "client_to_company", "name": "Demande", "source": "client_task", "target": "company_task", "type": "messageFlow"},
            {"id": "company_to_bank", "name": "Instruction", "source": "company_task", "target": "bank_task", "type": "messageFlow"},
        ],
        "ambiguities": [],
    }


class PoolBoundaryRepairTests(unittest.TestCase):
    """Cover safe repairs and collaboration semantic rules."""

    def setUp(self) -> None:
        self.compiler = WorkflowCompiler(FakeGenerator({}), SCHEMA_PATH)

    def test_cross_pool_sequence_becomes_message_and_preserves_endpoints(self) -> None:
        repaired, report = self.compiler._apply_safe_repairs(boundary_workflow())
        self.assertEqual(["inside"], [flow["id"] for flow in repaired["flows"]])
        converted = next(flow for flow in repaired["messageFlows"] if flow["id"] == "cross")
        self.assertEqual("a2", converted["source"])
        self.assertEqual("b1", converted["target"])
        self.assertEqual("Notify", converted["name"])
        self.assertEqual("messageFlow", converted["type"])
        self.assertNotIn("condition", converted)
        self.assertNotIn("isDefault", converted)
        self.assertIn("SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW", {item["code"] for item in report})

    def test_same_pool_sequence_and_cross_pool_message_are_preserved(self) -> None:
        original = boundary_workflow()
        repaired, _ = self.compiler._apply_safe_repairs(original)
        self.assertEqual("inside", repaired["flows"][0]["id"])
        existing = next(flow for flow in repaired["messageFlows"] if flow["id"] == "existing_message")
        self.assertEqual(("a1", "b2"), (existing["source"], existing["target"]))

    def test_same_pool_message_is_reported_by_repair_and_validator(self) -> None:
        workflow = boundary_workflow()
        workflow["messageFlows"] = [
            {"id": "local_message", "source": "a1", "target": "a2", "type": "messageFlow"}
        ]
        _, report = self.compiler._apply_safe_repairs(workflow)
        self.assertIn("MESSAGE_FLOW_WITHIN_SAME_PARTICIPANT", {item["code"] for item in report})
        semantic_codes = {item["code"] for item in SemanticValidator().validate(workflow)}
        self.assertIn("MESSAGE_FLOW_WITHIN_SAME_PARTICIPANT", semantic_codes)

    def test_semantic_validator_rejects_cross_pool_sequence(self) -> None:
        codes = {item["code"] for item in SemanticValidator().validate(boundary_workflow())}
        self.assertIn("SEQUENCE_FLOW_CROSSES_PARTICIPANTS", codes)

    def test_multiple_conversions_have_unique_deterministic_ids(self) -> None:
        workflow = boundary_workflow()
        workflow["messageFlows"] = [
            {"id": "cross", "source": "a1", "target": "b2", "type": "messageFlow"}
        ]
        workflow["flows"] = [
            {"id": "cross", "source": "a2", "target": "b1", "condition": "x"},
            {"id": "message_flow_1", "source": "b2", "target": "a1", "condition": "y"},
        ]
        repaired, report = self.compiler._apply_safe_repairs(workflow)
        self.assertEqual([], repaired["flows"])
        self.assertEqual(
            ["cross", "message_flow_1", "message_flow_2"],
            [flow["id"] for flow in repaired["messageFlows"]],
        )
        self.assertEqual(2, sum(item["code"] == "SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW" for item in report))

    def test_safe_repair_does_not_mutate_input(self) -> None:
        workflow = boundary_workflow()
        snapshot = copy.deepcopy(workflow)
        self.compiler._apply_safe_repairs(workflow)
        self.assertEqual(snapshot, workflow)


class PoolBoundaryAnalysisAndBPMNTests(unittest.TestCase):
    """Verify graph isolation and the final collaboration XML."""

    def test_graph_analyzer_ignores_message_flows_for_levels(self) -> None:
        workflow = three_pool_workflow()
        workflow["messageFlows"] = [
            {"id": "high_to_start", "source": "client_task", "target": "bank_start", "type": "messageFlow"}
        ]
        analysis = WorkflowGraphAnalyzer().analyze(workflow)
        self.assertEqual(0, analysis["levels"]["bank_start"])
        self.assertEqual(1, analysis["levels"]["bank_task"])
        self.assertEqual(0, analysis["in_degree"]["bank_start"])
        self.assertNotIn("bank_start", analysis["adjacency"]["client_task"])

    def test_three_pool_pipeline_saves_message_flows_in_bpmn(self) -> None:
        workflow = three_pool_workflow()
        misplaced = workflow["messageFlows"].pop(0)
        workflow["flows"].append(
            {
                **misplaced,
                "condition": "request sent",
                "isDefault": False,
                "type": "sequenceFlow",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            result = WorkflowCompiler(FakeGenerator(workflow), SCHEMA_PATH).compile_and_save(
                "Client, Entreprise et Banque communiquent", directory
            )
            target = Path(directory) / "workflow.bpmn"
            self.assertTrue(result["success"], result)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
            self.assertIn(
                "SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW",
                {warning["code"] for warning in result["warnings"]},
            )
            converted = next(
                flow
                for flow in result["normalized_workflow"]["messageFlows"]
                if flow["id"] == "client_to_company"
            )
            self.assertNotIn("condition", converted)
            root = ET.parse(target).getroot()
        namespaces = {"bpmn": BPMN_NS}
        messages = root.findall("bpmn:collaboration/bpmn:messageFlow", namespaces)
        self.assertEqual(2, len(messages))
        self.assertEqual(
            {"client_to_company", "company_to_bank"},
            {message.get("id") for message in messages},
        )
        self.assertTrue(all(message.get("sourceRef") and message.get("targetRef") for message in messages))
        self.assertEqual(6, len(root.findall(".//bpmn:sequenceFlow", namespaces)))


if __name__ == "__main__":
    unittest.main()
