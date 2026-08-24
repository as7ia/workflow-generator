import copy
from src.workflow import WorkflowRepairer,SemanticValidator

class Queue:
    def __init__(self,*items):self.items=list(items);self.prompts=[]
    def generate_json(self,prompt):self.prompts.append(prompt);item=self.items.pop(0);return copy.deepcopy(item)
def v1(valid=True):
    nodes=[{"id":"s","type":"startEvent","name":"S","actor":None,"properties":{}},{"id":"t","type":"serviceTask","name":"T","actor":"system","properties":{}}]
    flows=[{"id":"f1","source":"s","target":"t","condition":None,"isDefault":False}]
    if valid:nodes.append({"id":"e","type":"endEvent","name":"E","actor":None,"properties":{}});flows.append({"id":"f2","source":"t","target":"e","condition":None,"isDefault":False})
    return {"schemaVersion":"1.0.0","process":{"id":"p","name":"P","description":"","executable":False},"participants":[],"nodes":nodes,"flows":flows,"ambiguities":[]}
ERROR=[{"code":"PROCESS_MISSING_END_EVENT","message":"End missing","severity":"error","process_id":"p"}]

def test_valid_v1_stops_without_qwen_and_returns_v2():
    q=Queue();result,report=WorkflowRepairer().repair(v1(),[],q);assert report==[] and q.prompts==[] and result["schemaVersion"]=="2.0" and not [e for e in SemanticValidator().validate(result) if e["severity"]=="error"]
def test_first_attempt_success():
    result,report=WorkflowRepairer().repair(v1(False),ERROR,Queue(v1()));assert result["schemaVersion"]=="2.0" and [x["status"] for x in report]==["success"]
def test_second_attempt_success():
    _,report=WorkflowRepairer().repair(v1(False),ERROR,Queue(v1(False),v1()));assert [x["status"] for x in report]==["failed","success"]
def test_attempt_limit():
    q=Queue(v1(False),v1(False));_,report=WorkflowRepairer(2).repair(v1(False),ERROR,q);assert len(report)==2
def test_generated_candidate_runs_safe_repairs():
    candidate=v1();candidate["nodes"].insert(1,{"id":"g","type":"parallelGateway","name":"G","actor":None,"properties":{}});candidate["flows"]=[{"id":"a","source":"s","target":"g","condition":None,"isDefault":False},{"id":"b","source":"g","target":"t","condition":"x","isDefault":False},candidate["flows"][1]]
    result,_=WorkflowRepairer().repair(v1(False),ERROR,Queue(candidate));assert next(x for x in result["flows"] if x["id"]=="b")["condition"] is None
def test_initial_candidate_can_remain_best():
    worse={"schemaVersion":"2.0","processes":[{"id":"p","name":"P","description":"","executable":False,"properties":{}}],"participants":[],"lanes":[],"nodes":[{"id":f"x{i}","type":"invalid","name":"X","processRef":"p","participantRef":None,"laneRef":None,"actor":None,"properties":{}} for i in range(10)],"flows":[],"messageFlows":[],"ambiguities":[]}
    result,_=WorkflowRepairer(1).repair(v1(False),ERROR,Queue(worse));assert result["schemaVersion"]=="2.0" and result["nodes"]
def test_input_immutable():
    original=v1(False);before=copy.deepcopy(original);WorkflowRepairer().repair(original,ERROR,Queue(v1()));assert original==before
def test_prompt_teaches_v2_only():
    prompt=WorkflowRepairer().build_repair_prompt(v1(False),ERROR);assert 'schemaVersion vaut exactement "2.0"' in prompt and "processRef" in prompt and "nodeRefs" in prompt and "N'utilise jamais participant.nodeRefs" in prompt
def test_prompt_includes_error():
    assert "PROCESS_MISSING_END_EVENT" in WorkflowRepairer().build_repair_prompt(v1(False),ERROR)
def test_report_shape():
    _,report=WorkflowRepairer().repair(v1(False),ERROR,Queue(v1()));assert set(report[0])=={"attempt","status","input_errors","output_errors","message"}
def test_invalid_generator_output_reported():
    _,report=WorkflowRepairer(1).repair(v1(False),ERROR,Queue("bad"));assert any(e["code"]=="GENERATOR_INVALID_OUTPUT" for e in report[0]["output_errors"])
def test_generator_exception_reported():
    class Broken:
        def generate_json(self,prompt):raise RuntimeError("offline")
    _,report=WorkflowRepairer(1).repair(v1(False),ERROR,Broken());assert any(e["code"]=="GENERATOR_EXCEPTION" for e in report[0]["output_errors"])
