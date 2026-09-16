import copy,json
from pathlib import Path
from src.generation.prompts import build_generation_prompt,build_repair_prompt
from src.workflow import CanonicalWorkflowPreparer

SCHEMA=json.loads((Path(__file__).parents[1]/"schemas"/"workflow.schema.json").read_text(encoding="utf-8"))
def base():
    return {"schemaVersion":"2.0","processes":[{"id":"p","name":"P","description":"","executable":False,"properties":{}}],"participants":[],"lanes":[],"nodes":[{"id":"s","type":"startEvent","name":"S","processRef":"p","participantRef":None,"laneRef":None,"actor":None,"properties":{}},{"id":"e","type":"endEvent","name":"E","processRef":"p","participantRef":None,"laneRef":None,"actor":None,"properties":{}}],"flows":[{"id":"f","source":"s","target":"e","processRef":"p","condition":None,"isDefault":False,"properties":{}}],"messageFlows":[],"ambiguities":[]}
def test_preparation_returns_all_stages():
    assert set(CanonicalWorkflowPreparer(SCHEMA).prepare(base()))=={"workflow","normalization_report","safe_repair_report","schema_errors","semantic_errors","errors"}
def test_preparation_valid_candidate_has_no_blockers():
    result=CanonicalWorkflowPreparer(SCHEMA).prepare(base());assert not [e for e in result["errors"] if e.get("severity")=="error"]
def test_preparation_is_immutable():
    value=base();before=copy.deepcopy(value);CanonicalWorkflowPreparer(SCHEMA).prepare(value);assert value==before
def test_safe_repair_is_immutable():
    value=base();before=copy.deepcopy(value);CanonicalWorkflowPreparer.apply_safe_repairs(value);assert value==before
def test_preparing_an_already_canonical_workflow_is_idempotent():
    preparer=CanonicalWorkflowPreparer(SCHEMA);first=preparer.prepare_and_validate(base())["workflow"]
    second=preparer.prepare_normalized_and_validate(first)["workflow"]
    assert second==first
    assert {key:len(second[key]) for key in ("participants","nodes","flows")}=={
        key:len(first[key]) for key in ("participants","nodes","flows")}
def test_safe_repairs_are_idempotent_and_report_compact_phase_traces():
    value=base();value["nodes"][0]["type"]="parallelGateway";value["flows"][0]["condition"]="invalid"
    first,report=CanonicalWorkflowPreparer.apply_safe_repairs(value)
    second,_=CanonicalWorkflowPreparer.apply_safe_repairs(first)
    assert second==first
    assert report and all({"repair","applied","reason","before_error_count","after_error_count"}<=set(entry) for entry in report)

def test_convergent_participant_and_lane_ownership_repairs_node_process():
    value=base();value["processes"].append({"id":"wrong","name":"wrong","description":"","executable":False,"properties":{}})
    value["participants"]=[{"id":"pa","name":"A","processRef":"p","properties":{}}]
    value["lanes"]=[{"id":"la","name":"L","processRef":"wrong","participantRef":"pa","properties":{}}]
    value["nodes"][0].update(processRef="wrong",participantRef="pa",laneRef="la")
    fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(value)
    assert fixed["lanes"][0]["processRef"]=="p" and fixed["nodes"][0]["processRef"]=="p"

def test_contradictory_participant_and_lane_ownership_leaves_node_unchanged():
    value=base();value["processes"].append({"id":"q","name":"q","description":"","executable":False,"properties":{}})
    value["participants"]=[{"id":"pa","name":"A","processRef":"p","properties":{}},
                           {"id":"pb","name":"B","processRef":"q","properties":{}}]
    value["lanes"]=[{"id":"lb","name":"L","processRef":"q","participantRef":"pb","properties":{}}]
    value["nodes"][0].update(processRef="q",participantRef="pa",laneRef="lb")
    fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(value)
    assert fixed["nodes"][0]["processRef"]=="q"

def test_valid_cycle_is_not_removed():
    value=base();value["nodes"].insert(1,{"id":"task","type":"serviceTask","name":"T","processRef":"p","participantRef":None,"laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[{"id":"a","source":"s","target":"task","processRef":"p","condition":None,"isDefault":False,"properties":{}},
                    {"id":"loop","source":"task","target":"task","processRef":"p","condition":None,"isDefault":False,"properties":{}},
                    {"id":"b","source":"task","target":"e","processRef":"p","condition":None,"isDefault":False,"properties":{}}]
    fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(value)
    assert any(flow["id"]=="loop" for flow in fixed["flows"])

def test_guard_rolls_back_a_phase_that_increases_blocking_errors():
    def damaging(workflow,report):
        workflow["nodes"].append({"id":"orphan","type":"serviceTask","name":"O","processRef":"p","participantRef":None,"laneRef":None,"actor":None,"properties":{}})
    fixed,report=CanonicalWorkflowPreparer._apply_guarded_repair(base(),"damaging",damaging)
    assert fixed==base() and report[-1]["applied"] is False
    assert report[-1]["after_error_count"]>report[-1]["before_error_count"]

def test_message_flows_with_same_endpoints_but_different_data_are_not_deduplicated():
    value=base();value["messageFlows"]=[
        {"id":"m1","source":"s","target":"e","name":"one","properties":{}},
        {"id":"m2","source":"s","target":"e","name":"two","properties":{}},]
    fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(value)
    assert [message["id"] for message in fixed["messageFlows"]]==["m1","m2"]
def test_isolated_business_task_not_deleted():
    value=base();value["nodes"].append({"id":"business","type":"userTask","name":"Approve","processRef":"p","participantRef":None,"laneRef":None,"actor":"manager","properties":{}});fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(value);assert "business" in {n["id"] for n in fixed["nodes"]}
def test_generation_prompt_requires_process_ownership():
    prompt=build_generation_prompt("x",SCHEMA);assert "node.processRef est obligatoire" in prompt and "flow.processRef est obligatoire" in prompt
def test_generation_prompt_forbids_legacy_membership():
    prompt=build_generation_prompt("x",SCHEMA);assert "N'utilise jamais participant.nodeRefs" in prompt and "lanes imbriquées" in prompt
def test_generation_prompt_lists_send_and_receive_tasks():
    prompt=build_generation_prompt("x",SCHEMA);assert "sendTask" in prompt and "receiveTask" in prompt
def test_repair_prompt_contains_structured_error():
    prompt=build_repair_prompt(base(),[{"code":"PROCESS_MISSING_END_EVENT","message":"missing"}],SCHEMA);assert "PROCESS_MISSING_END_EVENT" in prompt
def test_repair_prompt_requires_json_only():
    assert "uniquement un objet JSON valide" in build_repair_prompt(base(),[],SCHEMA)
