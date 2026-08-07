"""End-to-end tests for deterministic BPMN generation."""

from __future__ import annotations

import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from src.bpmn.constants import BPMN_NS, BPMNDI_NS, DC_NS, DI_NS
from src.generation import WorkflowCompiler


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "workflow.schema.json"


class FakeGenerator:
    """Return one reusable workflow without any BPMN responsibility."""

    def __init__(self, workflow: dict) -> None:
        self.workflow = workflow

    def generate_json(self, prompt: str) -> dict:
        del prompt
        return copy.deepcopy(self.workflow)


def complex_collaboration() -> dict:
    """Build a generic multi-pool workflow with branches, joins and a timer."""
    nodes = [
        {"id": "customer_start", "type": "startEvent", "name": "Start request", "actor": None, "properties": {}},
        {"id": "submit", "type": "userTask", "name": "Submit request", "actor": "customer", "properties": {}},
        {"id": "customer_end", "type": "endEvent", "name": "Request complete", "actor": None, "properties": {}},
        {"id": "ops_start", "type": "startEvent", "name": "Start handling", "actor": None, "properties": {}},
        {"id": "review", "type": "userTask", "name": "Review", "actor": "operations", "properties": {}},
        {"id": "decision", "type": "exclusiveGateway", "name": "Eligible?", "actor": None, "properties": {}},
        {"id": "parallel_split", "type": "parallelGateway", "name": "Prepare", "actor": None, "properties": {}},
        {"id": "timer", "type": "intermediateCatchEvent", "name": "Wait", "actor": None, "properties": {"duration": "PT5M"}},
        {"id": "automatic_check", "type": "serviceTask", "name": "Automatic check", "actor": "system", "properties": {}},
        {"id": "parallel_join", "type": "parallelGateway", "name": "Prepared", "actor": None, "properties": {}},
        {"id": "approve", "type": "userTask", "name": "Approve", "actor": "operations", "properties": {}},
        {"id": "reject", "type": "userTask", "name": "Reject", "actor": "operations", "properties": {}},
        {"id": "ops_end", "type": "endEvent", "name": "Handling complete", "actor": None, "properties": {}},
    ]
    edges = [
        ("c1", "customer_start", "submit", None),
        ("c2", "submit", "customer_end", None),
        ("o1", "ops_start", "review", None),
        ("o2", "review", "decision", None),
        ("o3", "decision", "parallel_split", "eligible"),
        ("o4", "decision", "reject", "not eligible"),
        ("o5", "parallel_split", "timer", None),
        ("o6", "parallel_split", "automatic_check", None),
        ("o7", "timer", "parallel_join", None),
        ("o8", "automatic_check", "parallel_join", None),
        ("o9", "parallel_join", "approve", None),
        ("o10", "approve", "ops_end", None),
        ("o11", "reject", "ops_end", None),
    ]
    return {
        "schemaVersion": "1.0.0",
        "process": {
            "id": "request_process",
            "name": "Request decision process",
            "description": "Generic collaboration",
            "executable": False,
        },
        "participants": [
            {
                "id": "customer",
                "name": "Customer",
                "type": "human",
                "nodeRefs": ["customer_start", "submit", "customer_end"],
                "lanes": [{"id": "customer_lane", "name": "Requester", "nodeRefs": ["customer_start", "submit", "customer_end"]}],
            },
            {
                "id": "operations",
                "name": "Operations",
                "type": "human",
                "nodeRefs": [node["id"] for node in nodes[3:]],
                "lanes": [
                    {"id": "intake_lane", "name": "Intake", "nodeRefs": ["ops_start", "review", "decision"]},
                    {"id": "processing_lane", "name": "Processing", "nodeRefs": [node["id"] for node in nodes[6:]]},
                ],
            },
        ],
        "nodes": nodes,
        "flows": [
            {"id": identifier, "source": source, "target": target, "condition": condition, "isDefault": False}
            for identifier, source, target, condition in edges
        ],
        "messageFlows": [
            {"id": "message_request", "name": "Request", "source": "submit", "target": "review", "type": "messageFlow"},
            {"id": "message_result", "name": "Result", "source": "approve", "target": "customer_end", "type": "messageFlow"},
        ],
        "ambiguities": [],
    }


class BPMNGeneratorIntegrationTests(unittest.TestCase):
    """Require a complete, parseable and persisted BPMN result."""

    def test_complete_pipeline_generates_and_saves_bpmn(self) -> None:
        workflow = complex_collaboration()
        original = copy.deepcopy(workflow)
        with tempfile.TemporaryDirectory() as directory:
            result = WorkflowCompiler(FakeGenerator(workflow), SCHEMA_PATH).compile_and_save(
                "Compile a collaborative request", directory
            )
            target = Path(directory) / "workflow.bpmn"
            self.assertTrue(result["success"], result)
            self.assertIsInstance(result["bpmn_xml"], str)
            self.assertTrue(result["bpmn_xml"].strip())
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
            self.assertEqual(result["bpmn_xml"], target.read_text(encoding="utf-8"))
        self.assertEqual(original, workflow)

    def test_complex_xml_contains_collaboration_di_and_correct_process(self) -> None:
        workflow = complex_collaboration()
        result = WorkflowCompiler(FakeGenerator(workflow), SCHEMA_PATH).compile("Complex")
        self.assertTrue(result["success"], result)
        root = ET.fromstring(result["bpmn_xml"])
        namespaces = {"bpmn": BPMN_NS, "bpmndi": BPMNDI_NS, "dc": DC_NS, "di": DI_NS}

        process = root.find("bpmn:process[@id='request_process']", namespaces)
        self.assertIsNotNone(process)
        self.assertEqual("Request decision process", process.get("name"))
        self.assertEqual(2, len(root.findall("bpmn:collaboration/bpmn:participant", namespaces)))
        self.assertEqual(3, len(root.findall(".//bpmn:lane", namespaces)))
        self.assertEqual(2, len(root.findall("bpmn:collaboration/bpmn:messageFlow", namespaces)))
        self.assertEqual(13, len(root.findall(".//bpmn:sequenceFlow", namespaces)))
        self.assertIsNotNone(root.find(".//bpmn:timerEventDefinition/bpmn:timeDuration", namespaces))
        self.assertIsNotNone(root.find("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", namespaces))
        self.assertGreaterEqual(len(root.findall(".//bpmndi:BPMNShape", namespaces)), 18)
        self.assertEqual(15, len(root.findall(".//bpmndi:BPMNEdge", namespaces)))
        for edge in root.findall(".//bpmndi:BPMNEdge", namespaces):
            waypoints = edge.findall("di:waypoint", namespaces)
            self.assertGreaterEqual(len(waypoints), 2)
            self.assertTrue(all(point.get("x") is not None and point.get("y") is not None for point in waypoints))

        for sequence in root.findall(".//bpmn:sequenceFlow", namespaces):
            self.assertIsNotNone(sequence.get("sourceRef"))
            self.assertIsNotNone(sequence.get("targetRef"))
        self.assertNotIn("sourceRef", str(result["normalized_workflow"]))
        self.assertNotIn("targetRef", str(result["normalized_workflow"]))

    def test_success_is_false_when_bpmn_generation_fails(self) -> None:
        compiler = WorkflowCompiler(FakeGenerator(complex_collaboration()), SCHEMA_PATH)
        compiler._bpmn_generator.generate = lambda workflow, layout: ""
        result = compiler.compile("Empty BPMN")
        self.assertFalse(result["success"])
        self.assertEqual("bpmn", result["error"]["stage"])
        self.assertIsNone(result["bpmn_xml"])


if __name__ == "__main__":
    unittest.main()
