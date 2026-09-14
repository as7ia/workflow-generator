import copy
from pathlib import Path
from src.generation.pipeline import WorkflowCompiler

SCHEMA=Path(__file__).parents[1]/"schemas"/"workflow.schema.json"
class Generator:
    def __init__(self,*items):self.items=list(items);self.prompts=[]
    def generate_json(self,prompt):self.prompts.append(prompt);return copy.deepcopy(self.items.pop(0))
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
def test_invalid_calls_repair():
    bad=v2(nodes=[n("s",t="startEvent"),n("t")],flows=[f("f","s","t")]);c=compiler(bad,v2());result=c.compile("x");assert result["success"] and len(c.generator.prompts)==2
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
