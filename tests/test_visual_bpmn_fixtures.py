"""Synthetic BPMN-DI fixtures intended for manual opening in bpmn.io."""
import xml.etree.ElementTree as ET
import pytest
from src.bpmn import BPMNGenerator,BPMNLayoutEngine
from src.workflow.graph_analyzer import WorkflowGraphAnalyzer

def n(i,p="p",kind="userTask",participant=None,lane=None):
    value={"id":i,"name":i,"type":kind,"processRef":p,"properties":{}}
    if participant:value["participantRef"]=participant
    if lane:value["laneRef"]=lane
    return value
def f(i,a,b,p="p"):return {"id":i,"source":a,"target":b,"processRef":p,"properties":{}}
def workflow(processes,nodes,flows,participants=(),lanes=(),messages=()):
    return {"schemaVersion":"2.0","processes":[{"id":p,"name":p,"properties":{}} for p in processes],
        "participants":list(participants),"lanes":list(lanes),"nodes":nodes,"flows":flows,"messageFlows":list(messages),"ambiguities":[]}

def fixtures():
    linear=workflow(["p"],[n("s",kind="startEvent"),n("a"),n("e",kind="endEvent")],[f("1","s","a"),f("2","a","e")])
    split=workflow(["p"],[n("s",kind="startEvent"),n("g",kind="parallelGateway"),n("a"),n("b"),n("j",kind="parallelGateway"),n("e",kind="endEvent")],
        [f("1","s","g"),f("2","g","a"),f("3","g","b"),f("4","a","j"),f("5","b","j"),f("6","j","e")])
    part={"id":"pa","name":"pa","processRef":"p","properties":{}}
    lanes=[{"id":x,"name":x,"processRef":"p","participantRef":"pa","properties":{}} for x in ("l1","l2","l3")]
    lane_nodes=[n("a",participant="pa",lane="l1"),n("b",participant="pa",lane="l2"),n("c",participant="pa",lane="l3")]
    lane_fixture=workflow(["p"],lane_nodes,[f("1","a","b"),f("2","b","c")],[part],lanes)
    loop=workflow(["p"],[n("a"),n("b"),n("c")],[f("1","a","b"),f("2","b","c"),f("loop","c","a")])
    participants=[{"id":"pa","name":"pa","processRef":"p1","properties":{}},{"id":"pb","name":"pb","processRef":"p2","properties":{}}]
    pool_nodes=[n("a","p1",participant="pa"),n("b","p2",participant="pb")]
    pools=workflow(["p1","p2"],pool_nodes,[],participants,[],[{"id":"m","source":"a","target":"b","properties":{}}])
    collaboration=workflow(["p1","p2"],pool_nodes,[],participants,[],
        [{"id":"m1","source":"a","target":"b","properties":{}},{"id":"m2","source":"b","target":"a","properties":{}}])
    return {"linear":linear,"split_join":split,"three_lanes":lane_fixture,"loop":loop,"multiple_pools":pools,"collaboration":collaboration}

@pytest.mark.parametrize("name",sorted(fixtures()))
def test_visual_fixture_generates_parseable_bpmn_di(name,tmp_path):
    model=fixtures()[name];analysis=WorkflowGraphAnalyzer().analyze(model);layout=BPMNLayoutEngine().layout(model,analysis)
    xml=BPMNGenerator().generate(model,layout);target=tmp_path/f"{name}.bpmn";target.write_text(xml,encoding="utf-8")
    root=ET.parse(target).getroot()
    assert root.tag.endswith("definitions") and "BPMNDiagram" in xml and "BPMNShape" in xml
