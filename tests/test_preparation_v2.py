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
