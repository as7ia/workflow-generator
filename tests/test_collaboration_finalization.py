import copy

import pytest

from src.workflow import CanonicalWorkflowPreparer, SemanticValidator


def node(identifier, process, kind="serviceTask"):
    return {"id":identifier,"type":kind,"name":identifier,"processRef":process,
            "participantRef":f"participant_{process}","laneRef":None,"actor":None,"properties":{}}


def flow(identifier, source, target, process):
    return {"id":identifier,"source":source,"target":target,"processRef":process,
            "condition":None,"isDefault":False,"properties":{}}


def workflow(process_nodes, local_pairs=(), message_pairs=()):
    process_ids=tuple(process_nodes)
    nodes=[node(identifier,process,kind) for process,items in process_nodes.items() for identifier,kind in items]
    return {"schemaVersion":"2.0",
            "processes":[{"id":process,"name":process,"description":"","executable":False,"properties":{}} for process in process_ids],
            "participants":[{"id":f"participant_{process}","name":process,"processRef":process,"properties":{}} for process in process_ids],
            "lanes":[],"nodes":nodes,
            "flows":[flow(f"f{index}",source,target,process) for index,(process,source,target) in enumerate(local_pairs)],
            "messageFlows":[{"id":f"m{index}","source":source,"target":target,"properties":{}}
                            for index,(source,target) in enumerate(message_pairs)],"ambiguities":[]}


def repair(value):
    return CanonicalWorkflowPreparer.apply_safe_repairs(value)[0]


def pairs(value):
    return {(item["source"],item["target"]) for item in value["flows"]}


def test_two_participant_round_trip_is_connected_from_causal_context():
    value=workflow(
        {"p0":[("s0","startEvent"),("send0","sendTask"),("receive0","receiveTask"),("e0","endEvent")],
         "p1":[("s1","startEvent"),("receive1","receiveTask"),("send1","sendTask"),("e1","endEvent")]},
        [("p0","s0","send0"),("p0","receive0","e0"),("p1","s1","receive1"),
         ("p1","receive1","send1"),("p1","send1","e1")],
        [("send0","receive1"),("send1","receive0")])
    assert ("send0","receive0") in pairs(repair(value))


def test_three_participant_chain_and_multiple_message_tasks_are_connected():
    value=workflow(
        {"p0":[("s0","startEvent"),("x0","sendTask"),("y0","receiveTask"),("e0","endEvent")],
         "p1":[("s1","startEvent"),("x1","receiveTask"),("y1","sendTask"),("z1","receiveTask"),("w1","sendTask"),("e1","endEvent")],
         "p2":[("s2","startEvent"),("x2","receiveTask"),("y2","sendTask"),("e2","endEvent")]},
        [("p0","s0","x0"),("p0","y0","e0"),("p1","s1","x1"),("p1","x1","y1"),
         ("p1","z1","w1"),("p1","w1","e1"),("p2","s2","x2"),("p2","x2","y2"),("p2","y2","e2")],
        [("x0","x1"),("y1","x2"),("y2","z1"),("w1","y0")])
    fixed=repair(value)
    assert {("y1","z1"),("x0","y0")}.issubset(pairs(fixed))
    forbidden={"UNREACHABLE_NODE","ISOLATED_TASK","MISSING_INCOMING_FLOW","MISSING_OUTGOING_FLOW","NO_PATH_TO_END"}
    assert forbidden.isdisjoint({item["code"] for item in SemanticValidator().validate(fixed)})


def test_gateway_branches_and_valid_message_topology_are_unchanged():
    value=workflow(
        {"p0":[("s0","startEvent"),("g0","parallelGateway"),("a0","serviceTask"),("b0","serviceTask"),("e0","endEvent")],
         "p1":[("s1","startEvent"),("r1","receiveTask"),("e1","endEvent")]},
        [("p0","s0","g0"),("p0","g0","a0"),("p0","g0","b0"),("p0","a0","e0"),("p0","b0","e0"),
         ("p1","s1","r1"),("p1","r1","e1")],[("a0","r1")])
    before=copy.deepcopy(value)
    assert repair(value)==before


def test_multiple_roots_are_not_connected_arbitrarily():
    value=workflow(
        {"p0":[("s0","startEvent"),("r0a","receiveTask"),("r0b","receiveTask"),("e0","endEvent")],
         "p1":[("s1","startEvent"),("x1","sendTask"),("e1","endEvent")]},
        [("p1","s1","x1"),("p1","x1","e1")],[("x1","r0a"),("x1","r0b")])
    fixed=repair(value)
    assert not ({("s0","r0a"),("s0","r0b")} & pairs(fixed))


def test_multiple_sinks_are_not_connected_arbitrarily():
    value=workflow(
        {"p0":[("s0","startEvent"),("x0a","sendTask"),("x0b","sendTask"),("e0","endEvent")],
         "p1":[("s1","startEvent"),("r1","receiveTask"),("e1","endEvent")]},
        [("p0","s0","x0a"),("p0","s0","x0b"),("p1","s1","r1"),("p1","r1","e1")],
        [("x0a","r1"),("x0b","r1")])
    fixed=repair(value)
    assert not ({("x0a","e0"),("x0b","e0")} & pairs(fixed))


def test_already_valid_workflow_without_messages_is_unchanged():
    value=workflow({"p0":[("s0","startEvent"),("x0","serviceTask"),("e0","endEvent")]},
                   [("p0","s0","x0"),("p0","x0","e0")])
    assert repair(value)==value


@pytest.mark.parametrize("count",[2,4,7])
def test_number_of_processes_is_not_fixed(count):
    process_nodes={f"p{i}":[(f"s{i}","startEvent"),(f"x{i}","sendTask" if i<count-1 else "receiveTask"),(f"e{i}","endEvent")]
                   for i in range(count)}
    local=[(f"p{i}",f"s{i}",f"x{i}") for i in range(count)]+[(f"p{i}",f"x{i}",f"e{i}") for i in range(count)]
    messages=[(f"x{i}",f"x{i+1}") for i in range(count-1)]
    value=workflow(process_nodes,local,messages)
    assert repair(value)==value
