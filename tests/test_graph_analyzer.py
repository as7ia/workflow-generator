import copy
import json
import pytest
from src.workflow.graph_analyzer import WorkflowGraphAnalyzer

A=WorkflowGraphAnalyzer()
def wf(processes=("p1",),nodes=(),flows=(),participants=(),lanes=(),messages=()):
    return {"version":"2.0","processes":[{"id":p,"name":p} for p in processes],"participants":list(participants),"lanes":list(lanes),"nodes":list(nodes),"flows":list(flows),"messageFlows":list(messages)}
def n(i,p="p1",t="task",participant=None,lane=None):
    value={"id":i,"processRef":p,"type":t,"name":i}
    if participant: value["participantRef"]=participant
    if lane: value["laneRef"]=lane
    return value
def f(i,s,t,p="p1"): return {"id":i,"processRef":p,"source":s,"target":t}
def chain(p="p1",prefix=""):
    ids=[prefix+"s",prefix+"t",prefix+"e"]
    return [n(ids[0],p,"startEvent"),n(ids[1],p),n(ids[2],p,"endEvent")],[f(prefix+"f1",ids[0],ids[1],p),f(prefix+"f2",ids[1],ids[2],p)]
def proc(result,p="p1"): return result["processes"][p]

@pytest.mark.parametrize("length",range(1,13))
def test_linear_scenarios(length):
    nodes=[n(f"n{i}",t="startEvent" if i==0 else "endEvent" if i==length-1 else "task") for i in range(length)]
    flows=[f(f"f{i}",f"n{i}",f"n{i+1}") for i in range(length-1)]
    result=proc(A.analyze(wf(nodes=nodes,flows=flows)))
    assert result["node_ids"]==sorted(x["id"] for x in nodes)
    assert max(result["levels"].values(),default=0)==max(0,length-1)

def test_official_process_shape():
    nodes,flows=chain(); result=A.analyze(wf(nodes=nodes,flows=flows))
    assert set(result)>= {"processes","collaboration","warnings"}
    assert proc(result)["adjacency"]["s"]==["t"]

def test_two_processes_are_isolated():
    a,af=chain("p1","a"); b,bf=chain("p2","b"); result=A.analyze(wf(("p1","p2"),a+b,af+bf))
    assert set(proc(result,"p1")["node_ids"])=={"as","at","ae"}
    assert set(proc(result,"p2")["node_ids"])=={"bs","bt","be"}

def test_three_processes_are_independent():
    nodes=[n("a","p1"),n("b","p2"),n("c","p3")]
    result=A.analyze(wf(("p1","p2","p3"),nodes))
    assert [proc(result,p)["node_ids"] for p in ("p1","p2","p3")]==[["a"],["b"],["c"]]

def test_levels_restart_per_process():
    a,af=chain("p1","a"); b,bf=chain("p2","b"); result=A.analyze(wf(("p1","p2"),a+b,af+bf))
    assert proc(result,"p1")["levels"]["as"]==proc(result,"p2")["levels"]["bs"]==0

@pytest.mark.parametrize("gateway",["exclusiveGateway","parallelGateway","inclusiveGateway","eventBasedGateway"])
def test_split_gateway_types(gateway):
    nodes=[n("s",t="startEvent"),n("g",t=gateway),n("a"),n("b"),n("j",t=gateway),n("e",t="endEvent")]
    flows=[f("1","s","g"),f("2","g","a"),f("3","g","b"),f("4","a","j"),f("5","b","j"),f("6","j","e")]
    result=proc(A.analyze(wf(nodes=nodes,flows=flows)))
    assert result["split_gateways"]==["g"] and result["join_gateways"]==["j"]
    assert result["branches"][0]["process_id"]=="p1"

def test_branches_converge():
    nodes=[n("g",t="exclusiveGateway"),n("a"),n("b"),n("j")]
    flows=[f("1","g","a"),f("2","g","b"),f("3","a","j"),f("4","b","j")]
    assert proc(A.analyze(wf(nodes=nodes,flows=flows)))["branches"][0]["join_node"]=="j"

def test_branches_end_separately():
    nodes=[n("g",t="exclusiveGateway"),n("a"),n("b")]; flows=[f("1","g","a"),f("2","g","b")]
    assert proc(A.analyze(wf(nodes=nodes,flows=flows)))["branches"][0]["branches_end_separately"]

def test_nested_branches():
    nodes=[n("o",t="exclusiveGateway"),n("i",t="parallelGateway"),n("a"),n("b"),n("c"),n("j")]
    flows=[f("1","o","i"),f("2","o","c"),f("3","i","a"),f("4","i","b"),f("5","a","j"),f("6","b","j"),f("7","c","j")]
    branches={b["split_gateway"]:b for b in proc(A.analyze(wf(nodes=nodes,flows=flows)))["branches"]}
    assert "i" in branches["o"]["nested_splits"]

def test_simple_cycle_and_back_edge():
    nodes=[n("a"),n("b")]; flows=[f("1","a","b"),f("2","b","a")]; result=proc(A.analyze(wf(nodes=nodes,flows=flows)))
    assert result["cycles"]==[["a","b"]] and result["back_edges"][0]["process_id"]=="p1"

def test_self_loop_cycle():
    result=proc(A.analyze(wf(nodes=[n("a")],flows=[f("1","a","a")])))
    assert result["cycles"]==[["a"]]

def test_separate_sccs():
    nodes=[n(x) for x in "abcd"]; flows=[f("1","a","b"),f("2","b","a"),f("3","b","c"),f("4","c","d")]
    assert proc(A.analyze(wf(nodes=nodes,flows=flows)))["strongly_connected_components"]==[["a","b"],["c"],["d"]]

def test_multiple_cycles_in_one_process():
    nodes=[n(x) for x in "abcd"]
    flows=[f("1","a","b"),f("2","b","a"),f("3","b","c"),f("4","c","d"),f("5","d","c")]
    assert proc(A.analyze(wf(nodes=nodes,flows=flows)))["cycles"]==[["a","b"],["c","d"]]

def test_disconnected_components():
    result=proc(A.analyze(wf(nodes=[n(x) for x in "abc"],flows=[f("1","a","b")])))
    assert result["components"]==[["a","b"],["c"]]

def test_cycles_do_not_cross_processes():
    nodes=[n("a","p1"),n("b","p1"),n("c","p2"),n("d","p2")]; flows=[f("1","a","b","p1"),f("2","b","a","p1"),f("3","c","d","p2")]
    result=A.analyze(wf(("p1","p2"),nodes,flows)); assert proc(result,"p1")["cycles"]==[["a","b"]] and proc(result,"p2")["cycles"]==[]

def test_collaboration_indexes_explicit_refs():
    participants=[{"id":"part","processRef":"p1","name":"Team"}]; lanes=[{"id":"lane","processRef":"p1","participantRef":"part","name":"Ops"}]
    result=A.analyze(wf(nodes=[n("a",participant="part",lane="lane")],participants=participants,lanes=lanes))["collaboration"]
    assert result["node_to_process"]=={"a":"p1"} and result["node_to_participant"]=={"a":"part"} and result["node_to_lane"]=={"a":"lane"}
    assert result["participants"]["part"]["processRef"]==result["lanes"]["lane"]["processRef"]=="p1"

def test_multiple_participants_and_lanes():
    participants=[{"id":"pa","processRef":"p1","name":"A"},{"id":"pb","processRef":"p2","name":"B"}]
    lanes=[{"id":"la","processRef":"p1","participantRef":"pa","name":"LA"},{"id":"lb","processRef":"p2","participantRef":"pb","name":"LB"}]
    nodes=[n("a","p1",participant="pa",lane="la"),n("b","p2",participant="pb",lane="lb")]
    collab=A.analyze(wf(("p1","p2"),nodes,participants=participants,lanes=lanes))["collaboration"]
    assert set(collab["participants"])=={"pa","pb"} and set(collab["lanes"])=={"la","lb"}

def test_actor_does_not_create_ownership():
    node=n("a"); node["actor"]="part"
    assert A.analyze(wf(nodes=[node]))["collaboration"]["node_to_participant"]=={}

def test_message_flow_metadata_and_topology_exclusion():
    nodes=[n("a","p1",participant="pa"),n("b","p2",participant="pb")]
    msg={"id":"m","source":"a","target":"b","name":"hello"}; result=A.analyze(wf(("p1","p2"),nodes,messages=[msg]))
    analyzed=result["collaboration"]["message_flows"][0]
    assert analyzed["source_process"]=="p1" and analyzed["target_process"]=="p2"
    assert proc(result,"p1")["adjacency"]["a"]==[] and proc(result,"p2")["adjacency"]["b"]==[]

def test_multiple_message_flows_are_sorted():
    nodes=[n("a","p1"),n("b","p2")]; messages=[{"id":"z","source":"a","target":"b"},{"id":"a","source":"b","target":"a"}]
    assert [m["id"] for m in A.analyze(wf(("p1","p2"),nodes,messages=messages))["collaboration"]["message_flows"]]==["a","z"]

def test_unknown_message_endpoint_warns_without_crash():
    result=A.analyze(wf(nodes=[n("a")],messages=[{"id":"m","source":"a","target":"missing"}]))
    assert "UNKNOWN_MESSAGE_FLOW_ENDPOINT_DURING_ANALYSIS" in {w["code"] for w in result["warnings"]}

def test_cross_process_sequence_flow_is_excluded_and_warned():
    result=A.analyze(wf(("p1","p2"),[n("a","p1"),n("b","p2")],[f("bad","a","b","p1")]))
    assert proc(result,"p1")["adjacency"]["a"]==[] and proc(result,"p2")["reverse_adjacency"]["b"]==[]
    assert "FLOW_PROCESS_MISMATCH_DURING_ANALYSIS" in {w["code"] for w in result["warnings"]}

def test_unknown_flow_endpoint_warns():
    result=A.analyze(wf(nodes=[n("a")],flows=[f("bad","a","x")]))
    assert result["warnings"][0]["code"]=="UNKNOWN_FLOW_ENDPOINT_DURING_ANALYSIS"

def test_unknown_node_process_warns():
    result=A.analyze(wf(nodes=[n("a","missing")]))
    assert proc(result)["node_ids"]==[] and result["warnings"][0]["code"]=="UNKNOWN_NODE_PROCESS_DURING_ANALYSIS"

def test_empty_process_is_analyzed():
    assert proc(A.analyze(wf()))["topological_order"]==[]

def test_input_is_immutable():
    nodes,flows=chain(); model=wf(nodes=nodes,flows=flows); before=copy.deepcopy(model); A.analyze(model); assert model==before

def test_output_is_deterministic_under_input_reordering():
    nodes,flows=chain(); left=A.analyze(wf(nodes=nodes,flows=flows)); right=A.analyze(wf(nodes=reversed(nodes),flows=reversed(flows)))
    assert json.dumps(left,sort_keys=True)==json.dumps(right,sort_keys=True)

def test_repeated_analysis_is_identical():
    nodes,flows=chain(); model=wf(nodes=nodes,flows=flows); assert A.analyze(model)==A.analyze(model)
