import copy
from pathlib import Path
from src.generation.pipeline import WorkflowCompiler

SCHEMA=Path(__file__).parents[1]/"schemas"/"workflow.schema.json"
class Generator:
    def __init__(self,*items):self.items=list(items);self.prompts=[]
    def generate_json(self,prompt):
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):return {k:[] for k in ("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")}
        self.prompts.append(prompt);return copy.deepcopy(self.items.pop(0))
class Xml:
    def generate(self,workflow,layout):return "<definitions/>"
def n(i,p="p1",t="serviceTask",participant=None,lane=None,actor=None,properties=None):
    return {"id":i,"type":t,"name":i,"processRef":p,"participantRef":participant,"laneRef":lane,"actor":actor,"properties":properties or {}}
def f(i,s,t,p="p1",condition=None):return {"id":i,"source":s,"target":t,"processRef":p,"condition":condition,"isDefault":False,"properties":{}}
def v2(processes=("p1",),nodes=None,flows=None,participants=None,lanes=None,messages=None,version="2.0"):
    if nodes is None:nodes=[n("s",t="startEvent"),n("t"),n("e",t="endEvent")]
    if flows is None:flows=[f("f1","s","t"),f("f2","t","e")]
    return {"schemaVersion":version,"processes":[{"id":p,"name":p,"description":"","executable":False,"properties":{}} for p in processes],"participants":participants or [],"lanes":lanes or [],"nodes":nodes,"flows":flows,"messageFlows":messages or [],"ambiguities":[]}
def v1():
    w=v2();return {"schemaVersion":"1.0.0","process":w["processes"][0],"participants":[],"nodes":[{k:v for k,v in x.items() if k not in ("processRef","participantRef","laneRef")} for x in w["nodes"]],"flows":[{k:v for k,v in x.items() if k!="processRef"} for x in w["flows"]],"ambiguities":[]}
def compiler(*responses):
    c=WorkflowCompiler(Generator(*responses),SCHEMA);c._bpmn_generator=Xml();return c

def test_v1_normalized_before_validation():
    result=compiler(v1()).compile("x");assert result["success"] and result["normalized_workflow"]["schemaVersion"]=="2.0"
def test_v2_remains_v2():
    result=compiler(v2()).compile("x");assert result["success"] and result["normalized_workflow"]["schemaVersion"]=="2.0"
def test_unknown_version_rejected():
    bad=v2(version="9.0");result=compiler(bad,bad,bad,bad).compile("x");assert result["schema_errors"] and result["normalized_workflow"]["schemaVersion"]=="9.0"
def test_valid_does_not_call_repair():
    c=compiler(v2());result=c.compile("x");assert result["repair_report"]==[] and len(c.generator.prompts)==1
    assert result["generation_stats"]=={
        "initial_generation_calls":1,"structural_repair_calls":0,
        "coverage_repair_calls":0,"semantic_requirement_calls":1,"total_qwen_calls":2}
    assert result["prompt_stats"]["initial_prompt_chars"]>0
def test_invalid_calls_repair():
    bad=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("f","s","t")]);c=compiler(bad,v2());result=c.compile("x");assert result["success"] and len(c.generator.prompts)==2
def test_zero_repair_attempts_never_calls_generator_for_repair():
    bad=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("f","s","t")]);g=Generator(bad)
    c=WorkflowCompiler(g,SCHEMA,max_repair_attempts=0);c._bpmn_generator=Xml();result=c.compile("x")
    assert result["error"]["stage"]=="repair" and len(g.prompts)==1 and result["repair_report"]==[]
    assert result["pipeline_debug"]["after_structural_repair"]==result["pipeline_debug"]["final_current_workflow"]

def test_global_generation_budget_stops_before_extra_repair_call():
    bad=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("f","s","t")]);g=Generator(bad,v2())
    c=WorkflowCompiler(g,SCHEMA,max_repair_attempts=3,max_total_generation_calls=2);c._bpmn_generator=Xml()
    result=c.compile("x")
    assert result["error"]["stage"]=="repair" and len(g.prompts)==1
    assert result["generation_stats"]["total_qwen_calls"]==2
    assert result["generation_stats"]["structural_repair_calls"]==0
    assert result["normalized_workflow"]["nodes"]

def test_phase_token_limits_are_forwarded_when_generator_supports_them():
    class TokenAware:
        def __init__(self):self.limits=[]
        def generate_json(self,prompt,max_new_tokens=None):
            self.limits.append(max_new_tokens)
            return empty_requirements() if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION") else v2()
    generator=TokenAware();compiler=WorkflowCompiler(
        generator,SCHEMA,requirements_max_new_tokens=111,
        initial_max_new_tokens=222,repair_max_new_tokens=333)
    compiler._bpmn_generator=Xml();result=compiler.compile("x")
    assert result["success"] and generator.limits==[111,222]
def test_partially_improved_structural_candidate_becomes_current_source_of_truth():
    broken=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[])
    improved=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("f","s","t")])
    c=WorkflowCompiler(Generator(broken,improved),SCHEMA,max_repair_attempts=1);c._bpmn_generator=Xml();result=c.compile("x")
    assert result["error"]["stage"]=="repair"
    assert {(item["source"],item["target"]) for item in result["normalized_workflow"]["flows"]}=={("s","t")}
    assert result["pipeline_debug"]["after_structural_repair"]==result["pipeline_debug"]["final_current_workflow"]
def test_repair_uses_safe_repairs():
    bad=v2(nodes=[n("s",t="startEvent"),n("g",t="parallelGateway"),n("e",t="endEvent")],flows=[f("1","s","g"),f("2","g","e","p1","x")]);result=compiler(bad).compile("x");assert result["normalized_workflow"]["flows"][1]["condition"] is None
def test_cross_process_sequence_becomes_message():
    nodes=[n("a","p1","startEvent"),n("ae","p1","endEvent"),n("b","p2","startEvent"),n("be","p2","endEvent")];flows=[f("a1","a","ae","p1"),f("b1","b","be","p2"),f("cross","a","b","p1","x")];result=compiler(v2(("p1","p2"),nodes,flows)).compile("x");assert "cross" not in {x["id"] for x in result["normalized_workflow"]["flows"]} and "cross" in {x["id"] for x in result["normalized_workflow"]["messageFlows"]}
def test_converted_message_drops_sequence_fields():
    w=v2(("p1","p2"),[n("a","p1"),n("b","p2")],[f("x","a","b","p1","cond")]);fixed,_=compiler()._apply_safe_repairs(w);message=fixed["messageFlows"][0];assert "condition" not in message and "isDefault" not in message and "processRef" not in message
def test_intra_process_sequence_stays_sequence():
    w=v2();fixed,_=compiler()._apply_safe_repairs(w);assert len(fixed["flows"])==2 and fixed["messageFlows"]==[]
def test_parallel_conditions_removed_by_safe_repair():
    w=v2(nodes=[n("g",t="parallelGateway"),n("a"),n("b")],flows=[f("1","g","a",condition="x"),f("2","g","b",condition="y")]);fixed,report=compiler()._apply_safe_repairs(w);assert all(x["condition"] is None for x in fixed["flows"]) and {r["code"] for r in report}=={"PARALLEL_GATEWAY_CONDITION_REMOVED"}
def test_actor_does_not_change_ownership():
    result=compiler(v2(nodes=[n("s",t="startEvent",actor="pa"),n("e",t="endEvent",actor="pa")],flows=[f("x","s","e")])).compile("x");assert result["analysis"]["collaboration"]["node_to_participant"]=={}
def test_two_processes_independent():
    nodes=[n("a","p1","startEvent"),n("ae","p1","endEvent"),n("b","p2","startEvent"),n("be","p2","endEvent")];flows=[f("1","a","ae","p1"),f("2","b","be","p2")];result=compiler(v2(("p1","p2"),nodes,flows)).compile("x");assert set(result["analysis"]["processes"])=={"p1","p2"}
def test_three_processes():
    ps=("p1","p2","p3");nodes=[];flows=[]
    for p in ps:nodes += [n(p+"s",p,"startEvent"),n(p+"e",p,"endEvent")];flows += [f(p+"f",p+"s",p+"e",p)]
    assert len(compiler(v2(ps,nodes,flows)).compile("x")["analysis"]["processes"])==3
def test_valid_message_between_processes():
    nodes=[n("a","p1","startEvent"),n("ae","p1","endEvent"),n("b","p2","startEvent"),n("be","p2","endEvent")];flows=[f("1","a","ae","p1"),f("2","b","be","p2")];msg={"id":"m","source":"a","target":"b","properties":{}};assert compiler(v2(("p1","p2"),nodes,flows,messages=[msg])).compile("x")["success"]
def test_message_inside_process_rejected():
    bad=v2(messages=[{"id":"m","source":"s","target":"t","properties":{}}]);result=compiler(bad,bad,bad,bad).compile("x");assert "MESSAGE_FLOW_WITHIN_SAME_PROCESS" in {e["code"] for e in result["semantic_errors"]}
def test_missing_end_uses_v2_code():
    bad=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("x","s","t")]);result=compiler(bad,bad,bad,bad).compile("x");assert "PROCESS_MISSING_END_EVENT" in {e["code"] for e in result["semantic_errors"]}
def test_analysis_v2_returned():
    result=compiler(v2()).compile("x");assert result["analysis"]["processes"]["p1"]["topological_order"]==["s","t","e"]
def test_layout_v2_returned():
    result=compiler(v2()).compile("x");assert "p1" in result["layout"]["processes"] and "s" in result["layout"]["nodes"]
def test_loop_warning_preserved():
    w=v2(nodes=[n("s",t="startEvent"),n("a"),n("e",t="endEvent")],flows=[f("1","s","a"),f("2","a","a"),f("3","a","e")]);c=compiler(w);result=c.compile("x");assert any(e["severity"]=="warning" for e in result["warnings"]) and len(c.generator.prompts)==1
def test_timer_v2_compiles():
    w=v2(nodes=[n("s",t="startEvent"),n("timer",t="intermediateCatchEvent",properties={"timer":{"kind":"duration","value":"PT5M"}}),n("e",t="endEvent")],flows=[f("1","s","timer"),f("2","timer","e")]);assert compiler(w).compile("x")["success"]
def test_original_not_mutated():
    w=v1();before=copy.deepcopy(w);compiler(w).compile("x");assert w==before
def test_message_metadata_is_dictionary():
    nodes=[n("a","p1","startEvent"),n("ae","p1","endEvent"),n("b","p2","startEvent"),n("be","p2","endEvent")];flows=[f("1","a","ae","p1"),f("2","b","be","p2")];msg={"id":"m","source":"a","target":"b","properties":{}};result=compiler(v2(("p1","p2"),nodes,flows,messages=[msg])).compile("x");assert result["layout"]["metadata"]["message_flow_connections"]["m"]["source_process"]=="p1"

class CoverageGenerator(Generator):
    def __init__(self,requirements,*items):super().__init__(*items);self.requirements=requirements
    def generate_json(self,prompt):
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):return copy.deepcopy(self.requirements)
        return super().generate_json(prompt)

def empty_requirements(**values):
    result={k:[] for k in ("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")};result.update(values);return result

def test_coverage_error_is_repaired_with_description_and_requirements():
    missing=v2();covered=v2(nodes=[n("s",t="startEvent"),n("t",t="intermediateCatchEvent",properties={"timer":{"kind":"duration","value":"P2D"}}),n("e",t="endEvent")])
    g=CoverageGenerator(empty_requirements(timers=[{"duration":"P2D"}]),missing,covered);c=WorkflowCompiler(g,SCHEMA);c._bpmn_generator=Xml()
    result=c.compile("wait two days")
    assert result["success"] and result["coverage_errors"]==[] and result["semantic_requirements"]["timers"]==[{"kind":"duration","value":"P2D"}]
    assert len(g.prompts)==2 and "MISSING_TIMER" in g.prompts[1] and "P2D" in g.prompts[1]
    assert "JSON Schema officiel" not in g.prompts[1] and "ambiguities" in g.prompts[1]
    assert result["repair_report"][-1]["coverage_repair"]["accepted_candidate"] is True
    assert result["generation_stats"]["coverage_repair_calls"]==1
    assert result["prompt_stats"]["last_coverage_repair_prompt_chars"]==len(g.prompts[1])

def test_coverage_warnings_do_not_block_compilation():
    g=CoverageGenerator(empty_requirements(ambiguities=[{"message":"unclear optional role"}]),v2());c=WorkflowCompiler(g,SCHEMA);c._bpmn_generator=Xml()
    result=c.compile("valid but ambiguous")
    assert result["success"] and any(x["code"]=="SEMANTIC_AMBIGUITY" for x in result["coverage_errors"]) and len(g.prompts)==1

def test_zero_repair_attempts_never_calls_generator_for_coverage_repair():
    g=CoverageGenerator(empty_requirements(timers=[{"duration":"P2D"}]),v2())
    c=WorkflowCompiler(g,SCHEMA,max_repair_attempts=0);c._bpmn_generator=Xml();result=c.compile("wait two days")
    assert result["error"]["stage"]=="coverage_repair" and len(g.prompts)==1 and result["repair_report"]==[]

def test_structural_repair_precedes_coverage_repair():
    broken=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("f","s","t")])
    structural=v2();covered=v2(nodes=[n("s",t="startEvent"),n("t",t="intermediateCatchEvent",properties={"timer":{"kind":"duration","value":"P2D"}}),n("e",t="endEvent")])
    g=CoverageGenerator(empty_requirements(timers=[{"duration":"P2D"}]),broken,structural,covered);c=WorkflowCompiler(g,SCHEMA);c._bpmn_generator=Xml()
    result=c.compile("wait two days")
    assert result["success"] and len(g.prompts)==3
    assert "PROCESS_MISSING_END_EVENT" in g.prompts[1] and "Exigences sémantiques" not in g.prompts[1]
    assert "MISSING_TIMER" in g.prompts[2] and "P2D" in g.prompts[2]

def test_rejected_coverage_candidate_does_not_replace_structural_success():
    broken=v2(nodes=[n("s",t="startEvent"),n("task")],flows=[f("f","s","task")])
    processes=("client_process","company_process","bank_process")
    participant_names=("Client","Entreprise","Banque")
    participants=[];nodes=[];flows=[]
    for process,name in zip(processes,participant_names):
        participant=f"participant_{process}"
        participants.append({"id":participant,"name":name,"processRef":process,"properties":{}})
        local=[]
        for index,node_type in enumerate(("startEvent","serviceTask","serviceTask","endEvent")):
            node_id=f"{process}_{index}";local.append(node_id)
            nodes.append(n(node_id,process,node_type,participant))
        flows.extend(f(f"{process}_f{index}",source,target,process) for index,(source,target) in enumerate(zip(local,local[1:])))
    lanes=[
        {"id":"lane_client","name":"Accueil","processRef":processes[0],"participantRef":participants[0]["id"],"properties":{}},
        {"id":"lane_company","name":"Finance","processRef":processes[1],"participantRef":participants[1]["id"],"properties":{}},
    ]
    structural=v2(processes,nodes,flows,participants,lanes)
    invalid={"schemaVersion":"2.0","processes":[{"id":1,"name":"bad","description":"","executable":False,"properties":{}}],"participants":[],"lanes":[],"nodes":[],"flows":[],"messageFlows":[],"ambiguities":[]}
    req=empty_requirements(participants=[{"name":name} for name in participant_names],activities=[{"name":"Missing required step"}])
    g=CoverageGenerator(req,broken,structural,invalid)
    c=WorkflowCompiler(g,SCHEMA,max_repair_attempts=1);c._bpmn_generator=Xml()
    result=c.compile("Client, Entreprise and Banque perform the missing required step during critical state propagation")
    assert result["error"]["stage"]=="coverage_repair"
    assert len(result["normalized_workflow"]["participants"])==3
    assert len(result["normalized_workflow"]["nodes"])==12
    assert result["pipeline_debug"]["before_coverage_validation"]["participants"]==3
    assert result["pipeline_debug"]["before_coverage_validation"]["nodes"]==12
    assert result["pipeline_debug"]["coverage_candidate"]["nodes"]==0
    assert result["pipeline_debug"]["final_current_workflow"]["nodes"]==12
    assert result["repair_report"][-1]["coverage_repair"]["accepted_candidate"] is False
