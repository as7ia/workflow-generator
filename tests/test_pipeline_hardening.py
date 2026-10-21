import copy
import json
from pathlib import Path

import pytest

from src.generation.pipeline import WorkflowCompiler
from src.generation.prompts import build_generation_prompt, build_repair_prompt
from src.workflow.repair import WorkflowRepairer


SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "workflow.schema.json"


class DeterministicGenerator:
    def __init__(self, workflow):
        self.workflow = workflow

    def generate_json(self, prompt, max_new_tokens=None):
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):
            return {
                key: []
                for key in (
                    "participants", "lanes", "timers", "parallelisms",
                    "decisions", "interactions", "activities", "ambiguities",
                )
            }
        return copy.deepcopy(self.workflow)


def linear_workflow(task_name):
    return {
        "schemaVersion": "2.0",
        "processes": [{"id": "process", "name": "Process", "description": "", "executable": False, "properties": {}}],
        "participants": [],
        "lanes": [],
        "nodes": [
            {"id": "start", "type": "startEvent", "name": "Start", "processRef": "process", "participantRef": None, "laneRef": None, "actor": None, "properties": {}},
            {"id": "task", "type": "serviceTask", "name": task_name, "processRef": "process", "participantRef": None, "laneRef": None, "actor": None, "properties": {}},
            {"id": "end", "type": "endEvent", "name": "End", "processRef": "process", "participantRef": None, "laneRef": None, "actor": None, "properties": {}},
        ],
        "flows": [
            {"id": "flow_start", "source": "start", "target": "task", "processRef": "process", "condition": None, "isDefault": False, "properties": {}},
            {"id": "flow_end", "source": "task", "target": "end", "processRef": "process", "condition": None, "isDefault": False, "properties": {}},
        ],
        "messageFlows": [],
        "ambiguities": [],
    }


def test_generation_and_repair_prompts_require_graph_coherence():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    generation = build_generation_prompt("A request is processed.", schema)
    repair = build_repair_prompt(linear_workflow("Process request"), [], schema)
    for prompt in (generation, repair):
        assert "messageFlow ne remplace jamais" in prompt
        assert "start→activités→end" in prompt
        assert "sequenceFlow inter-process" in prompt
    assert "patch ciblé" in repair
    assert "Ne supprime aucune activité métier" in repair


def test_repair_preservation_rejects_changed_business_activity():
    original = linear_workflow("Review request")
    candidate = copy.deepcopy(original)
    candidate["nodes"][1]["name"] = "Discard request"
    report = WorkflowRepairer.structural_preservation_report(original, candidate)
    assert report["destructive_candidate"]
    assert report["modified_business_node_ids"] == ["task"]


@pytest.mark.parametrize(
    ("description", "task_name"),
    [
        ("A security team contains a cyber incident.", "Contain cyber incident"),
        ("An insurer reimburses an accepted claim.", "Reimburse claim"),
        ("A factory repeats its quality inspection.", "Inspect product quality"),
        ("Customer service resolves a complaint.", "Resolve complaint"),
        ("A supplier prepares goods for a carrier.", "Prepare supplier shipment"),
    ],
)
def test_compile_and_save_produces_valid_bpmn_di(tmp_path, description, task_name):
    destination = tmp_path / task_name.replace(" ", "_")
    compiler = WorkflowCompiler(DeterministicGenerator(linear_workflow(task_name)), SCHEMA_PATH)
    result = compiler.compile_and_save(description, str(destination))
    assert result["success"], result["error"]
    assert result["bpmn_di_validation"]["valid"]
    assert result["bpmn_di_validation"]["applicable"]
    assert result["bpmn_di_validation"]["shape_count"] == 3
    assert result["bpmn_di_validation"]["edge_count"] == 2
    assert (destination / "workflow.bpmn").is_file()
    assert (destination / "compilation_report.json").is_file()


def test_bpmn_di_validation_reports_missing_geometry():
    namespace = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    report = WorkflowCompiler._validate_bpmn_di(
        linear_workflow("Review request"),
        f'<bpmn:definitions xmlns:bpmn="{namespace}"/>',
    )
    assert not report["valid"]
    assert report["applicable"]
    assert any("BPMNShape manquant" in error for error in report["errors"])
    assert any("BPMNEdge manquant" in error for error in report["errors"])
