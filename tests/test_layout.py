import copy
import json
import pytest
from src.bpmn.layout import BPMNLayoutEngine
from src.bpmn.constants import (START_EVENT_WIDTH,START_EVENT_HEIGHT,END_EVENT_WIDTH,END_EVENT_HEIGHT,TASK_WIDTH,TASK_HEIGHT,GATEWAY_WIDTH,GATEWAY_HEIGHT,SUBPROCESS_WIDTH,SUBPROCESS_HEIGHT,INTERMEDIATE_EVENT_WIDTH,INTERMEDIATE_EVENT_HEIGHT)
from src.workflow.graph_analyzer import WorkflowGraphAnalyzer

def node(i,p="p1",t="task",participant=None,lane=None,properties=None):
    value={"id":i,"processRef":p,"type":t,"name":i,"properties":properties or {}}
    if participant:value["participantRef"]=participant
    if lane:value["laneRef"]=lane
    return value
def flow(i,s,t,p="p1"):return {"id":i,"processRef":p,"source":s,"target":t}
def model(processes=("p1",),nodes=(),flows=(),participants=(),lanes=(),messages=()):
    return {"schemaVersion":"2.0","processes":[{"id":p,"name":p,"properties":{}} for p in processes],"participants":list(participants),"lanes":list(lanes),"nodes":list(nodes),"flows":list(flows),"messageFlows":list(messages),"ambiguities":[]}
def participant(i,p):return {"id":i,"name":i,"processRef":p,"properties":{}}
def lane(i,p,part):return {"id":i,"name":i,"processRef":p,"participantRef":part,"properties":{}}
def chain(p="p1",prefix="",part=None,lane_id=None):
    ids=[prefix+"s",prefix+"t",prefix+"e"]
    return [node(ids[0],p,"startEvent",part,lane_id),node(ids[1],p,"userTask",part,lane_id),node(ids[2],p,"endEvent",part,lane_id)],[flow(prefix+"1",ids[0],ids[1],p),flow(prefix+"2",ids[1],ids[2],p)]
def layout(w):return BPMNLayoutEngine().layout(w,WorkflowGraphAnalyzer().analyze(w))
def contains(a,b):return b["x"]>=a["x"] and b["y"]>=a["y"] and b["x"]+b["width"]<=a["x"]+a["width"] and b["y"]+b["height"]<=a["y"]+a["height"]
def overlap(a,b):return a["x"]<b["x"]+b["width"] and a["x"]+a["width"]>b["x"] and a["y"]<b["y"]+b["height"] and a["y"]+a["height"]>b["y"]

def test_linear_process():
    nodes,flows=chain(); out=layout(model(nodes=nodes,flows=flows)); assert out["nodes"]["s"]["x"]<out["nodes"]["t"]["x"]<out["nodes"]["e"]["x"]
def test_two_independent_processes():
    a,af=chain("p1","a");b,bf=chain("p2","b");out=layout(model(("p1","p2"),a+b,af+bf));assert set(out["processes"])=={"p1","p2"} and not overlap(out["processes"]["p1"]["bounds"],out["processes"]["p2"]["bounds"])
def test_three_processes():
    nodes=[node("a","p1"),node("b","p2"),node("c","p3")];out=layout(model(("p1","p2","p3"),nodes));assert len(out["processes"])==3
def test_levels_independent():
    a,af=chain("p1","a");b,bf=chain("p2","b");out=layout(model(("p1","p2"),a+b,af+bf));assert out["nodes"]["as"]["level"]==out["nodes"]["bs"]["level"]==0

@pytest.mark.parametrize("gateway",["exclusiveGateway","parallelGateway","inclusiveGateway"])
def test_two_branch_corridors(gateway):
    nodes=[node("g",t=gateway),node("a"),node("b"),node("j",t=gateway)];flows=[flow("1","g","a"),flow("2","g","b"),flow("3","a","j"),flow("4","b","j")]
    out=layout(model(nodes=nodes,flows=flows));assert out["nodes"]["a"]["branch_index"]!=out["nodes"]["b"]["branch_index"]
def test_three_balanced_branches():
    nodes=[node("g",t="inclusiveGateway")]+[node(x) for x in "abc"]+[node("j",t="inclusiveGateway")];flows=[flow(str(i),"g",x) for i,x in enumerate("abc")]+[flow("x"+str(i),x,"j") for i,x in enumerate("abc")]
    out=layout(model(nodes=nodes,flows=flows));assert sorted(out["nodes"][x]["branch_index"] for x in "abc")==[-1,0,1]
def test_join_is_centered():
    nodes=[node("g",t="exclusiveGateway"),node("a"),node("b"),node("j",t="exclusiveGateway")];flows=[flow("1","g","a"),flow("2","g","b"),flow("3","a","j"),flow("4","b","j")];out=layout(model(nodes=nodes,flows=flows));centers=[out["nodes"][x]["y"]+out["nodes"][x]["height"]/2 for x in "ab"];assert out["nodes"]["j"]["y"]+out["nodes"]["j"]["height"]/2==pytest.approx(sum(centers)/2)
def test_nested_branches():
    nodes=[node("o",t="parallelGateway"),node("i",t="exclusiveGateway"),node("a"),node("b"),node("c"),node("j")];flows=[flow("1","o","i"),flow("2","o","c"),flow("3","i","a"),flow("4","i","b"),flow("5","a","j"),flow("6","b","j"),flow("7","c","j")];out=layout(model(nodes=nodes,flows=flows));assert out["nodes"]["a"]["branch_index"]!=out["nodes"]["b"]["branch_index"]
def test_cycle_channel():
    out=layout(model(nodes=[node("a"),node("b")],flows=[flow("f","a","b"),flow("back","b","a")]));assert out["processes"]["p1"]["back_edge_channels"]["back"]["process_id"]=="p1"
def test_multiple_cycle_channels():
    nodes=[node(x) for x in "abcd"];flows=[flow("1","a","b"),flow("b1","b","a"),flow("2","b","c"),flow("3","c","d"),flow("b2","d","c")];channels=layout(model(nodes=nodes,flows=flows))["processes"]["p1"]["back_edge_channels"];assert {v["channel_index"] for v in channels.values()}=={0,1}
def test_disconnected_components_have_bands():
    out=layout(model(nodes=[node(x) for x in "abcd"],flows=[flow("1","a","b"),flow("2","c","d")]));assert out["nodes"]["a"]["y"]!=out["nodes"]["c"]["y"]

def test_single_participant():
    nodes,flows=chain(part="pa");out=layout(model(nodes=nodes,flows=flows,participants=[participant("pa","p1")]));assert set(out["participants"])=={"pa"}
def test_multiple_participants_no_collision():
    nodes=[node("a","p1",participant="pa"),node("b","p2",participant="pb")];out=layout(model(("p1","p2"),nodes,participants=[participant("pa","p1"),participant("pb","p2")]));assert not overlap(out["participants"]["pa"],out["participants"]["pb"])
def test_multiple_lanes_are_stacked():
    nodes=[node("a",participant="pa",lane="la"),node("b",participant="pa",lane="lb")];out=layout(model(nodes=nodes,participants=[participant("pa","p1")],lanes=[lane("la","p1","pa"),lane("lb","p1","pa")]));assert out["lanes"]["la"]["y"]<out["lanes"]["lb"]["y"] and not overlap(out["lanes"]["la"],out["lanes"]["lb"])
def test_node_contained_in_lane():
    out=layout(model(nodes=[node("a",participant="pa",lane="la")],participants=[participant("pa","p1")],lanes=[lane("la","p1","pa")]));assert contains(out["lanes"]["la"],out["nodes"]["a"])
def test_lane_contained_in_participant():
    out=layout(model(nodes=[node("a",participant="pa",lane="la")],participants=[participant("pa","p1")],lanes=[lane("la","p1","pa")]));assert contains(out["participants"]["pa"],out["lanes"]["la"])
def test_unlaned_node_uses_technical_band():
    out=layout(model(nodes=[node("a",participant="pa")],participants=[participant("pa","p1")]));assert out["nodes"]["a"]["lane_id"] is None and "NODE_WITHOUT_LANE" in {w["code"] for w in out["metadata"]["warnings"]}
def test_process_without_participant():
    out=layout(model(nodes=[node("a")]));assert "a" in out["nodes"] and "PROCESS_WITHOUT_PARTICIPANT" in {w["code"] for w in out["metadata"]["warnings"]}

def test_message_does_not_change_levels():
    nodes=[node("a","p1"),node("b","p2")];base=layout(model(("p1","p2"),nodes));withmsg=layout(model(("p1","p2"),nodes,messages=[{"id":"m","source":"a","target":"b"}]));assert base["nodes"]["a"]["level"]==withmsg["nodes"]["a"]["level"]
def test_message_metadata():
    nodes=[node("a","p1",participant="pa"),node("b","p2",participant="pb")];out=layout(model(("p1","p2"),nodes,messages=[{"id":"m","source":"a","target":"b"}]));assert out["metadata"]["message_flow_connections"]["m"]["source_process"]=="p1"
def test_no_node_collisions():
    nodes=[node("g",t="exclusiveGateway"),node("a"),node("b")];out=layout(model(nodes=nodes,flows=[flow("1","g","a"),flow("2","g","b")]));values=list(out["nodes"].values());assert all(not overlap(a,b) for i,a in enumerate(values) for b in values[i+1:])

@pytest.mark.parametrize("kind,size",[("startEvent",(START_EVENT_WIDTH,START_EVENT_HEIGHT)),("endEvent",(END_EVENT_WIDTH,END_EVENT_HEIGHT)),("userTask",(TASK_WIDTH,TASK_HEIGHT)),("serviceTask",(TASK_WIDTH,TASK_HEIGHT)),("manualTask",(TASK_WIDTH,TASK_HEIGHT)),("scriptTask",(TASK_WIDTH,TASK_HEIGHT)),("businessRuleTask",(TASK_WIDTH,TASK_HEIGHT)),("sendTask",(TASK_WIDTH,TASK_HEIGHT)),("receiveTask",(TASK_WIDTH,TASK_HEIGHT)),("exclusiveGateway",(GATEWAY_WIDTH,GATEWAY_HEIGHT)),("parallelGateway",(GATEWAY_WIDTH,GATEWAY_HEIGHT)),("inclusiveGateway",(GATEWAY_WIDTH,GATEWAY_HEIGHT)),("eventBasedGateway",(GATEWAY_WIDTH,GATEWAY_HEIGHT)),("subProcess",(SUBPROCESS_WIDTH,SUBPROCESS_HEIGHT)),("intermediateCatchEvent",(INTERMEDIATE_EVENT_WIDTH,INTERMEDIATE_EVENT_HEIGHT)),("intermediateThrowEvent",(INTERMEDIATE_EVENT_WIDTH,INTERMEDIATE_EVENT_HEIGHT))])
def test_canonical_node_sizes(kind,size):
    entry=layout(model(nodes=[node("a",t=kind)]))["nodes"]["a"];assert (entry["width"],entry["height"])==tuple(map(float,size))
def test_timer_catch_event_standard_size():
    entry=layout(model(nodes=[node("a",t="intermediateCatchEvent",properties={"eventDefinition":"timer"})]))["nodes"]["a"];assert (entry["width"],entry["height"])==(float(INTERMEDIATE_EVENT_WIDTH),float(INTERMEDIATE_EVENT_HEIGHT))
def test_json_order_does_not_change_layout():
    nodes,flows=chain();a=model(nodes=nodes,flows=flows);b=model(nodes=reversed(nodes),flows=reversed(flows));assert json.dumps(layout(a),sort_keys=True)==json.dumps(layout(b),sort_keys=True)
def test_determinism():
    nodes,flows=chain();w=model(nodes=nodes,flows=flows);assert layout(w)==layout(w)
def test_workflow_immutability():
    nodes,flows=chain();w=model(nodes=nodes,flows=flows);before=copy.deepcopy(w);layout(w);assert w==before
def test_analysis_immutability():
    nodes,flows=chain();w=model(nodes=nodes,flows=flows);analysis=WorkflowGraphAnalyzer().analyze(w);before=copy.deepcopy(analysis);BPMNLayoutEngine().layout(w,analysis);assert analysis==before
