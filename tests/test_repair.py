import copy
from src.workflow import WorkflowRepairer,SemanticValidator
from src.workflow.coverage_validator import CoverageValidator

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
    result,report=WorkflowRepairer(1).repair(v1(False),ERROR,Queue(worse));assert result["schemaVersion"]=="2.0" and result["nodes"] and report[0]["accepted_candidate"] is False and report[0]["errors_after"]>report[0]["errors_before"]
def test_equal_score_candidate_is_rejected_and_previous_workflow_is_kept():
    original=v1(False);candidate=copy.deepcopy(original);candidate["nodes"][1]["name"]="Changed without improvement"
    result,report=WorkflowRepairer(1).repair(original,ERROR,Queue(candidate))
    assert result["nodes"][1]["name"]=="T" and report[0]["accepted_candidate"] is False
    assert report[0]["errors_after"]==report[0]["errors_before"]
def test_zero_attempts_returns_prepared_workflow_without_generator_call():
    queue=Queue();result,report=WorkflowRepairer(0).repair(v1(False),ERROR,queue)
    assert result["schemaVersion"]=="2.0" and report==[] and queue.prompts==[]
def test_input_immutable():
    original=v1(False);before=copy.deepcopy(original);WorkflowRepairer().repair(original,ERROR,Queue(v1()));assert original==before
def test_prompt_teaches_v2_only():
    prompt=WorkflowRepairer().build_repair_prompt(v1(False),ERROR);assert 'schemaVersion vaut exactement "2.0"' in prompt and "processRef" in prompt and "nodeRefs" in prompt and "N'utilise jamais participant.nodeRefs" in prompt
def test_prompt_includes_error():
    assert "PROCESS_MISSING_END_EVENT" in WorkflowRepairer().build_repair_prompt(v1(False),ERROR)
def test_structural_prompt_contains_only_unique_blocking_errors():
    errors=[*ERROR,copy.deepcopy(ERROR[0]),{"code":"LOOP_DETECTED","message":"warning","severity":"warning"}]
    prompt=WorkflowRepairer().build_repair_prompt(v1(False),errors,description="ignored",requirements={"activities":[{"name":"ignored"}]})
    assert prompt.count("PROCESS_MISSING_END_EVENT")==1
    assert "LOOP_DETECTED" not in prompt and "ignored" not in prompt
def test_report_shape():
    _,report=WorkflowRepairer().repair(v1(False),ERROR,Queue(v1()));assert set(report[0])=={"attempt","status","input_errors","output_errors","message","accepted_candidate","errors_before","errors_after"}
def test_invalid_generator_output_reported():
    _,report=WorkflowRepairer(1).repair(v1(False),ERROR,Queue("bad"));assert any(e["code"]=="GENERATOR_INVALID_OUTPUT" for e in report[0]["output_errors"])
def test_generator_exception_reported():
    class Broken:
        def generate_json(self,prompt):raise RuntimeError("offline")
    _,report=WorkflowRepairer(1).repair(v1(False),ERROR,Broken());assert any(e["code"]=="GENERATOR_EXCEPTION" for e in report[0]["output_errors"])

def test_cuda_oom_stops_phase_and_preserves_best_workflow():
    class Oom:
        def __init__(self):self.calls=0
        def generate_json(self,prompt):
            self.calls+=1
            raise RuntimeError("CUDA out of memory")
    generator=Oom();original=v1(False)
    result,report=WorkflowRepairer(3).repair(original,ERROR,generator)
    assert generator.calls==1 and len(report)==1
    assert result["schemaVersion"]=="2.0" and result["nodes"]
    assert any(error["code"]=="GENERATOR_EXCEPTION" for error in report[0]["output_errors"])

def test_structural_prompt_omits_full_schema_and_compacts_workflow():
    prompt=WorkflowRepairer().build_repair_prompt(v1(False),ERROR)
    assert "JSON Schema officiel" not in prompt
    assert '\n  "nodes"' not in prompt

def canonical_three_processes():
    processes=[];participants=[];nodes=[];flows=[]
    for index,name in enumerate(("Client","Entreprise","Banque"),1):
        process=f"p{index}";participant=f"participant_{index}"
        processes.append({"id":process,"name":process,"description":"","executable":False,"properties":{}})
        participants.append({"id":participant,"name":name,"processRef":process,"properties":{}})
        local=[]
        for suffix,node_type in (("s","startEvent"),("a","serviceTask"),("e","endEvent")):
            node_id=f"{process}_{suffix}";local.append(node_id)
            nodes.append({"id":node_id,"type":node_type,"name":node_id,"processRef":process,"participantRef":participant,"laneRef":None,"actor":None,"properties":{}})
        flows.extend([
            {"id":f"{process}_f1","source":local[0],"target":local[1],"processRef":process,"condition":None,"isDefault":False,"properties":{}},
            {"id":f"{process}_f2","source":local[1],"target":local[2],"processRef":process,"condition":None,"isDefault":False,"properties":{}},
        ])
    nodes.insert(-1,{"id":"p3_b","type":"serviceTask","name":"p3_b","processRef":"p3","participantRef":"participant_3","laneRef":None,"actor":None,"properties":{}})
    flows[-1:] = [
        {"id":"p3_f2","source":"p3_a","target":"p3_b","processRef":"p3","condition":None,"isDefault":False,"properties":{}},
        {"id":"p3_f3","source":"p3_b","target":"p3_e","processRef":"p3","condition":None,"isDefault":False,"properties":{}},
    ]
    return {"schemaVersion":"2.0","processes":processes,"participants":participants,"lanes":[],"nodes":nodes,"flows":flows,"messageFlows":[],"ambiguities":[]}

def coverage_requirements():
    return {**{k:[] for k in ("participants","lanes","timers","parallelisms","decisions","interactions","ambiguities")},"activities":[{"name":"Required coverage"}]}

def test_coverage_repair_rejects_empty_candidate_and_preserves_current_workflow():
    current=canonical_three_processes()
    empty={"schemaVersion":"2.0","processes":[{"id":"p","name":"p","description":"","executable":False,"properties":{}}],"participants":[],"lanes":[],"nodes":[],"flows":[],"messageFlows":[],"ambiguities":[]}
    errors=CoverageValidator().validate(coverage_requirements(),current)
    result,report=WorkflowRepairer(1).repair(current,errors,Queue(empty),requirements=coverage_requirements(),repair_kind="coverage")
    assert len(result["processes"])==3 and len(result["participants"])==3 and len(result["nodes"])==10
    assert report[0]["coverage_repair"]["accepted_candidate"] is False

def test_coverage_repair_rejects_candidate_with_more_structural_errors():
    current=canonical_three_processes();candidate=copy.deepcopy(current)
    candidate["nodes"].append({"id":"covered","type":"invalid","name":"Required coverage","processRef":"p1","participantRef":"participant_1","laneRef":None,"actor":None,"properties":{}})
    candidate["flows"]=[]
    requirements=coverage_requirements();errors=CoverageValidator().validate(requirements,current)
    result,report=WorkflowRepairer(1).repair(current,errors,Queue(candidate),requirements=requirements,repair_kind="coverage")
    diagnostic=report[0]["coverage_repair"]
    assert result==current and diagnostic["errors_after"]<diagnostic["errors_before"]
    assert diagnostic["structure_errors_after"]>diagnostic["structure_errors_before"] and diagnostic["accepted_candidate"] is False
