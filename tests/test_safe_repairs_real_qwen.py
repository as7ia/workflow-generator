import copy
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from src.generation.pipeline import WorkflowCompiler
from src.workflow import CanonicalWorkflowPreparer, SemanticValidator


SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "workflow.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def process(identifier="shared"):
    return {"id": identifier, "name": "Shared", "description": "", "executable": False, "properties": {}}


def participant(identifier):
    return {"id": identifier, "name": identifier, "processRef": "shared", "properties": {}}


def node(identifier, owner, kind):
    return {"id": identifier, "type": kind, "name": identifier, "processRef": "shared",
            "participantRef": owner, "laneRef": None, "actor": None, "properties": {}}


def flow(identifier, source, target):
    return {"id": identifier, "source": source, "target": target, "processRef": "shared",
            "condition": None, "isDefault": False, "properties": {}}


def three_pool_workflow():
    owners = ("participant_a", "participant_b", "participant_c")
    nodes = []
    flows = []
    for owner in owners:
        suffix = owner[-1]
        nodes.extend([node(f"{suffix}_start", owner, "startEvent"), node(f"{suffix}_task", owner, "serviceTask"), node(f"{suffix}_end", owner, "endEvent")])
        flows.extend([flow(f"{suffix}_local_1", f"{suffix}_start", f"{suffix}_task"), flow(f"{suffix}_local_2", f"{suffix}_task", f"{suffix}_end")])
    flows.extend([flow("a_to_b", "a_task", "b_task"), flow("b_to_c", "b_task", "c_task"),
                  flow("c_to_b", "c_task", "b_task"), flow("b_to_a", "b_task", "a_task")])
    return {"schemaVersion": "2.0", "processes": [process()],
            "participants": [participant(owner) for owner in owners],
            "lanes": [{"id": "lane_b", "name": "B lane", "processRef": "shared", "participantRef": "participant_b", "properties": {}}],
            "nodes": nodes, "flows": flows,
            "messageFlows": [{"id": "existing_a_to_b", "source": "a_task", "target": "b_task", "properties": {}}],
            "ambiguities": []}


def three_pool_without_events():
    workflow = {"schemaVersion": "2.0", "processes": [process()],
                "participants": [participant(owner) for owner in ("participant_a", "participant_b", "participant_c")],
                "lanes": [],
                "nodes": [node("a_message", "participant_a", "messageTask"),
                          node("b_receive", "participant_b", "messageTask"),
                          node("b_work", "participant_b", "serviceTask"),
                          node("b_send", "participant_b", "messageTask"),
                          node("c_message", "participant_c", "messageTask")],
                "flows": [flow("b_internal_1", "b_receive", "b_work"),
                          flow("b_internal_2", "b_work", "b_send"),
                          flow("a_to_b", "a_message", "b_receive"),
                          flow("b_to_c", "b_send", "c_message"),
                          flow("c_to_b", "c_message", "b_receive"),
                          flow("b_to_a", "b_send", "a_message")],
                "messageFlows": [], "ambiguities": []}
    return workflow


def test_real_qwen_shared_process_is_split_and_flows_are_reclassified():
    original = three_pool_workflow()
    fixed, report = CanonicalWorkflowPreparer.apply_safe_repairs(original)
    assert original["processes"] == [process()]
    assert len(fixed["processes"]) == 3
    process_refs = {p["id"]: p["processRef"] for p in fixed["participants"]}
    assert len(set(process_refs.values())) == 3
    assert all(n["processRef"] == process_refs[n["participantRef"]] for n in fixed["nodes"])
    assert fixed["lanes"][0]["processRef"] == process_refs["participant_b"]
    node_process = {n["id"]: n["processRef"] for n in fixed["nodes"]}
    assert all(node_process[f["source"]] == node_process[f["target"]] == f["processRef"] for f in fixed["flows"])
    assert {(m["source"], m["target"]) for m in fixed["messageFlows"]} == {
        ("a_task", "b_task"), ("b_task", "c_task"), ("c_task", "b_task"), ("b_task", "a_task")
    }
    assert len(fixed["messageFlows"]) == 4
    assert "SHARED_PROCESS_SPLIT_BY_PARTICIPANT" in {entry["code"] for entry in report}
    assert "SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW" in {entry["code"] for entry in report}
    forbidden = {"MULTIPLE_PARTICIPANTS_FOR_PROCESS", "MESSAGE_FLOW_WITHIN_SAME_PROCESS", "SEQUENCE_FLOW_CROSSES_PROCESSES"}
    assert forbidden.isdisjoint({issue["code"] for issue in SemanticValidator().validate(fixed)})


def test_prepare_and_validate_runs_safe_repairs_before_both_validators():
    prepared = CanonicalWorkflowPreparer(SCHEMA).prepare_and_validate(three_pool_workflow())
    fixed = prepared["workflow"]
    participant_by_id = {participant["id"]: participant for participant in fixed["participants"]}
    assert len(fixed["processes"]) == 3
    assert len({participant["processRef"] for participant in fixed["participants"]}) == 3
    assert all(node["processRef"] == participant_by_id[node["participantRef"]]["processRef"] for node in fixed["nodes"])
    assert all(lane["processRef"] == participant_by_id[lane["participantRef"]]["processRef"] for lane in fixed["lanes"])
    forbidden = {"MULTIPLE_PARTICIPANTS_FOR_PROCESS", "MESSAGE_FLOW_WITHIN_SAME_PROCESS", "SEQUENCE_FLOW_CROSSES_PROCESSES"}
    assert forbidden.isdisjoint({issue["code"] for issue in prepared["semantic_errors"]})


@pytest.mark.parametrize(("raw", "expected"), [("3 days", "P3D"), ("5 minutes", "PT5M"), ("2 hours", "PT2H")])
def test_v2_simple_timer_duration_is_normalized(raw, expected):
    workflow = three_pool_workflow()
    workflow["nodes"][1]["type"] = "intermediateCatchEvent"
    workflow["nodes"][1]["properties"] = {"timer": {"kind": "duration", "value": raw}}
    fixed, _ = CanonicalWorkflowPreparer.apply_safe_repairs(workflow)
    assert fixed["nodes"][1]["properties"]["timer"]["value"] == expected


def test_prepare_and_validate_normalizes_real_v2_timer_before_semantics():
    workflow = three_pool_workflow()
    workflow["nodes"][1]["type"] = "intermediateCatchEvent"
    workflow["nodes"][1]["properties"] = {"timer": {"kind": "duration", "value": "3 days"}}
    prepared = CanonicalWorkflowPreparer(SCHEMA).prepare_and_validate(workflow)
    assert prepared["workflow"]["nodes"][1]["properties"]["timer"]["value"] == "P3D"
    assert "INVALID_TIMER_DURATION" not in {issue["code"] for issue in prepared["semantic_errors"]}


def test_ambiguous_timer_is_left_for_semantic_repair():
    workflow = three_pool_workflow()
    workflow["nodes"][1]["properties"] = {"timer": {"kind": "duration", "value": "next business day"}}
    fixed, _ = CanonicalWorkflowPreparer.apply_safe_repairs(workflow)
    assert fixed["nodes"][1]["properties"]["timer"]["value"] == "next business day"
    assert "INVALID_TIMER_DURATION" in {issue["code"] for issue in SemanticValidator().validate(fixed)}


class QueueGenerator:
    def __init__(self, workflow):
        self.workflow = workflow
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        return copy.deepcopy(self.workflow)


class XmlGenerator:
    def generate(self, workflow, layout):
        return "<definitions/>"


def test_three_pool_pipeline_needs_no_qwen_repair_attempt():
    generator = QueueGenerator(three_pool_workflow())
    compiler = WorkflowCompiler(generator, SCHEMA_PATH)
    compiler._bpmn_generator = XmlGenerator()
    result = compiler.compile("generic three-pool collaboration")
    assert result["success"]
    assert result["repair_report"] == []
    assert len(generator.prompts) == 1


def test_compiler_returns_post_safe_repair_normalized_workflow():
    workflow = three_pool_workflow()
    workflow["nodes"][1]["type"] = "intermediateCatchEvent"
    workflow["nodes"][1]["properties"] = {"timer": {"kind": "duration", "value": "3 days"}}
    generator = QueueGenerator(workflow)
    compiler = WorkflowCompiler(generator, SCHEMA_PATH)
    compiler._bpmn_generator = XmlGenerator()
    result = compiler.compile("real qwen-shaped collaboration")
    fixed = result["normalized_workflow"]
    participant_by_id = {participant["id"]: participant for participant in fixed["participants"]}
    node_by_id = {node["id"]: node for node in fixed["nodes"]}
    assert result["success"]
    assert len(fixed["processes"]) == 3
    assert len({participant["processRef"] for participant in fixed["participants"]}) == 3
    assert all(node["processRef"] == participant_by_id[node["participantRef"]]["processRef"] for node in fixed["nodes"])
    assert all(lane["processRef"] == participant_by_id[lane["participantRef"]]["processRef"] for lane in fixed["lanes"])
    assert all(node_by_id[flow["source"]]["processRef"] == node_by_id[flow["target"]]["processRef"] for flow in fixed["flows"])
    assert {("a_task", "b_task"), ("b_task", "c_task"), ("c_task", "b_task"), ("b_task", "a_task")} == {
        (message["source"], message["target"]) for message in fixed["messageFlows"]
    }
    assert fixed["nodes"][1]["properties"]["timer"]["value"] == "P3D"
    assert result["repair_report"] == []
    assert len(generator.prompts) == 1


def test_compiler_stabilizes_split_processes_without_explicit_events():
    generator = QueueGenerator(three_pool_without_events())
    compiler = WorkflowCompiler(generator, SCHEMA_PATH)
    compiler._bpmn_generator = XmlGenerator()
    result = compiler.compile("generic A B C collaboration without explicit events")
    fixed = result["normalized_workflow"]
    node_by_id = {node["id"]: node for node in fixed["nodes"]}
    assert result["success"]
    assert len(fixed["processes"]) == 3
    assert len({participant["processRef"] for participant in fixed["participants"]}) == 3
    assert "messageTask" not in {node["type"] for node in fixed["nodes"]}
    assert node_by_id["b_receive"]["type"] == "receiveTask"
    assert node_by_id["b_send"]["type"] == "sendTask"
    assert node_by_id["a_message"]["type"] == "serviceTask"
    process_ids={process["id"] for process in fixed["processes"]}
    assert process_ids == {node["processRef"] for node in fixed["nodes"] if node["type"] == "startEvent"}
    assert process_ids == {node["processRef"] for node in fixed["nodes"] if node["type"] == "endEvent"}
    assert all(node_by_id[item["source"]]["processRef"] == node_by_id[item["target"]]["processRef"] for item in fixed["flows"])
    assert {(message["source"],message["target"]) for message in fixed["messageFlows"]} == {
        ("a_message","b_receive"),("b_send","c_message"),("c_message","b_receive"),("b_send","a_message")
    }
    forbidden={"PROCESS_MISSING_START_EVENT","PROCESS_MISSING_END_EVENT","UNREACHABLE_NODE","NO_PATH_TO_END","DEAD_END_BRANCH"}
    assert forbidden.isdisjoint({issue["code"] for issue in result["semantic_errors"]})
    assert result["repair_report"] == []
    assert len(generator.prompts) == 1


def test_split_does_not_connect_a_truly_isolated_business_node():
    workflow = three_pool_without_events()
    workflow["nodes"].append(node("b_orphan", "participant_b", "serviceTask"))
    prepared = CanonicalWorkflowPreparer(SCHEMA).prepare_and_validate(workflow)
    fixed = prepared["workflow"]
    assert "b_orphan" in {item["id"] for item in fixed["nodes"]}
    assert not any(flow["source"] == "b_orphan" or flow["target"] == "b_orphan" for flow in fixed["flows"])
    assert "ISOLATED_TASK" in {issue["code"] for issue in prepared["semantic_errors"]}


def test_prompts_state_collaboration_and_targeted_repair_rules():
    from src.generation.prompts import build_generation_prompt, build_repair_prompt

    generation = build_generation_prompt("x", SCHEMA)
    repair = build_repair_prompt({}, [{"code": "MULTIPLE_PARTICIPANTS_FOR_PROCESS", "message": "x"}], SCHEMA)
    assert "IMPORTANT BPMN COLLABORATION RULES" in generation
    assert "Two participants MUST NOT share the same processRef" in generation
    assert "3 days = P3D" in generation
    assert "Create one distinct process per participant" in repair
    assert "INVALID_TIMER_DURATION" in repair


def test_qwen_cuda_cache_cleanup_is_between_generations(monkeypatch):
    from src.generation.qwen_generator import QwenWorkflowGenerator

    calls = []
    fake_cuda = SimpleNamespace(is_available=lambda: True, empty_cache=lambda: calls.append("empty"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=fake_cuda))
    QwenWorkflowGenerator._release_cuda_cache()
    assert calls == ["empty"]
