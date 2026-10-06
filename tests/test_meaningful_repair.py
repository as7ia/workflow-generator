import copy
from pathlib import Path
from src.generation.pipeline import WorkflowCompiler
from src.workflow.meaningful import is_meaningful_workflow
from src.workflow.repair import WorkflowRepairer
from src.workflow.semantic_validator import SemanticValidator

SCHEMA=Path(__file__).parents[1]/"schemas"/"workflow.schema.json"
EMPTY_REQUIREMENTS={key:[] for key in ("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")}

def large_invalid_workflow():
    processes=[];nodes=[];flows=[]
    for process_index in range(3):
        process=f"p{process_index}";processes.append({"id":process,"name":process,"properties":{}});local=[]
        for node_index in range(7):
            identifier=f"n{process_index}_{node_index}";local.append(identifier)
            kind="startEvent" if node_index==0 else ("invalidType" if process_index==0 and node_index==1 else "serviceTask")
            nodes.append({"id":identifier,"type":kind,"name":identifier,"processRef":process,"participantRef":None,"laneRef":None,"actor":None,"properties":{}})
        flows.extend({"id":f"f{process_index}_{i}","source":a,"target":b,"processRef":process,"condition":None,"isDefault":False,"properties":{}}
            for i,(a,b) in enumerate(zip(local,local[1:])))
    return {"schemaVersion":"2.0","processes":processes,"participants":[],"lanes":[],"nodes":nodes,"flows":flows,"messageFlows":[],"ambiguities":[]}

def empty_candidate():
    return {"schemaVersion":"2.0","processes":[{"id":"placeholder","name":"Placeholder","properties":{}}],
        "participants":[],"lanes":[],"nodes":[],"flows":[],"messageFlows":[],"ambiguities":[]}

def exact_real_shape():
    process_ids=[f"process_{index}" for index in range(5)]
    processes=[{"id":process,"name":process,"properties":{}} for process in process_ids]
    participants=[{"id":f"participant_{index}","name":f"Actor {index}","processRef":process_ids[index],"properties":{}} for index in range(3)]
    lane_owners=(0,0,1,1,2)
    lanes=[{"id":f"lane_{index}","name":f"Role {index}","processRef":process_ids[owner],
        "participantRef":f"participant_{owner}","properties":{}} for index,owner in enumerate(lane_owners)]
    sizes=(3,3,3,2,2);nodes=[];flows=[];by_process=[]
    for process_index,size in enumerate(sizes):
        local=[]
        for node_index in range(size):
            identifier=f"node_{process_index}_{node_index}";local.append(identifier)
            kind="startEvent" if node_index==0 else ("endEvent" if node_index==size-1 else "serviceTask")
            if process_index==0 and node_index==1:kind="invalidType"
            participant=f"participant_{process_index}" if process_index<3 else None
            lane=("lane_0" if process_index==0 else "lane_2" if process_index==1 else "lane_4" if process_index==2 else None)
            nodes.append({"id":identifier,"type":kind,"name":identifier,"processRef":process_ids[process_index],
                "participantRef":participant,"laneRef":lane,"actor":None,"properties":{}})
        by_process.append(local)
        flows.extend({"id":f"flow_{process_index}_{edge}","source":source,"target":target,"processRef":process_ids[process_index],
            "condition":None,"isDefault":False,"properties":{}} for edge,(source,target) in enumerate(zip(local,local[1:])))
    for index in range(3):
        node=by_process[index][1];flows.append({"id":f"loop_{index}","source":node,"target":node,"processRef":process_ids[index],
            "condition":None,"isDefault":False,"properties":{}})
    messages=[{"id":f"message_{index}","source":by_process[index][0],"target":by_process[index+1][0],"properties":{}} for index in range(4)]
    return {"schemaVersion":"2.0","processes":processes,"participants":participants,"lanes":lanes,
        "nodes":nodes,"flows":flows,"messageFlows":messages,"ambiguities":[]}

class Queue:
    def __init__(self,*items):self.items=list(items)
    def generate_json(self,prompt):return copy.deepcopy(self.items.pop(0))

def test_empty_candidate_is_rejected_before_scoring_and_base_is_preserved():
    base=large_invalid_workflow();repaired,report=WorkflowRepairer(1).repair(base,[],Queue(empty_candidate()))
    assert len(repaired["nodes"])==21 and len(repaired["processes"])==3
    assert report[0]["accepted_candidate"] is False and report[0]["status"]=="rejected"
    assert "EMPTY_REPAIR_CANDIDATE" in {error["code"] for error in report[0]["output_errors"]}
    assert report[0]["candidate_counts"]["nodes"]==0

def test_zero_error_placeholder_is_never_meaningful():
    assert not is_meaningful_workflow(empty_candidate(),large_invalid_workflow())

class CompilerGenerator:
    def __init__(self):self.calls=0
    def generate_json(self,prompt):
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):return copy.deepcopy(EMPTY_REQUIREMENTS)
        self.calls+=1;return large_invalid_workflow() if self.calls==1 else empty_candidate()

class BpmnSpy:
    def __init__(self):self.called=False
    def generate(self,workflow,layout):self.called=True;return "<definitions/>"

def test_failed_repair_keeps_last_good_workflow_and_never_calls_bpmn():
    compiler=WorkflowCompiler(CompilerGenerator(),SCHEMA,max_structural_repair_attempts=1,max_coverage_repair_attempts=0)
    spy=BpmnSpy();compiler._bpmn_generator=spy;result=compiler.compile("A generic process performs several steps.")
    assert not result["success"] and result["error"]["stage"]=="repair" and not spy.called
    assert len(result["normalized_workflow"]["nodes"])==21
    assert result["pipeline_debug"]["after_initial_prepare"]["nodes"]==21
    assert result["pipeline_debug"]["structural_repair_candidate"]["nodes"]==0
    assert result["pipeline_debug"]["after_structural_repair"]["nodes"]==21

class ExactCompilerGenerator:
    def __init__(self):self.calls=0
    def generate_json(self,prompt):
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):return copy.deepcopy(EMPTY_REQUIREMENTS)
        self.calls+=1;return exact_real_shape() if self.calls==1 else empty_candidate()

def test_real_compiler_rejects_exact_empty_structural_candidate():
    compiler=WorkflowCompiler(ExactCompilerGenerator(),SCHEMA,max_structural_repair_attempts=1,max_coverage_repair_attempts=0)
    spy=BpmnSpy();compiler._bpmn_generator=spy;result=compiler.compile("A generic multi-process workflow.")
    expected={"processes":5,"participants":3,"lanes":5,"nodes":13,"flows":11,"messageFlows":4}
    assert result["pipeline_debug"]["after_initial_prepare"]==expected
    assert result["pipeline_debug"]["structural_repair_candidate"]=={"processes":1,"participants":0,"lanes":0,"nodes":0,"flows":0,"messageFlows":0}
    assert result["pipeline_debug"]["structural_repair_candidate_accepted"] is False
    assert result["pipeline_debug"]["after_structural_repair"]==expected
    assert len(result["normalized_workflow"]["nodes"])==13 and len(result["normalized_workflow"]["participants"])==3
    attempt=result["repair_report"][0]
    assert SemanticValidator().validate(empty_candidate())==[]
    assert attempt["accepted_candidate"] is False and attempt["errors_after"]==1
    assert attempt["status"]=="rejected" and attempt["reason"]=="EMPTY_REPAIR_CANDIDATE" and not spy.called
