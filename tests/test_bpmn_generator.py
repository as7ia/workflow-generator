import copy
import xml.etree.ElementTree as ET
import pytest
from src.bpmn import BPMNGenerator,BPMNLayoutEngine
from src.bpmn.constants import BPMN_NS,BPMNDI_NS
from src.workflow import WorkflowGraphAnalyzer

NS={"bpmn":BPMN_NS,"bpmndi":BPMNDI_NS}
TYPES=["startEvent","endEvent","userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask","exclusiveGateway","parallelGateway","inclusiveGateway","eventBasedGateway","subProcess","intermediateCatchEvent","intermediateThrowEvent"]
def process(i):return {"id":i,"name":i,"description":"","executable":False,"properties":{}}
def node(i,p="p1",kind="serviceTask",participant=None,lane=None,properties=None):return {"id":i,"type":kind,"name":i,"processRef":p,"participantRef":participant,"laneRef":lane,"actor":None,"properties":properties or {}}
def flow(i,s,t,p="p1"):return {"id":i,"source":s,"target":t,"processRef":p,"condition":None,"isDefault":False,"properties":{}}
def workflow(processes=("p1",),nodes=None,flows=None,participants=None,lanes=None,messages=None):
    if nodes is None:nodes=[node("s",kind="startEvent"),node("e",kind="endEvent")]
    if flows is None:flows=[flow("f","s","e")]
    return {"schemaVersion":"2.0","processes":[process(p) for p in processes],"participants":participants or [],"lanes":lanes or [],"nodes":nodes,"flows":flows,"messageFlows":messages or [],"ambiguities":[]}
def generate(w):
    analysis=WorkflowGraphAnalyzer().analyze(w);layout=BPMNLayoutEngine().layout(w,analysis);xml=BPMNGenerator().generate(w,layout);return ET.fromstring(xml),layout,xml
def participant(i,p):return {"id":i,"name":i,"processRef":p,"properties":{}}
def lane(i,p,owner):return {"id":i,"name":i,"processRef":p,"participantRef":owner,"properties":{}}

def test_process_id_is_preserved():
    root,_,_=generate(workflow(("request_process",),[node("s","request_process","startEvent"),node("e","request_process","endEvent")],[flow("f","s","e","request_process")]));assert root.find("bpmn:process[@id='request_process']",NS) is not None
@pytest.mark.parametrize("count",[2,3])
def test_multiple_processes(count):
    ps=tuple(f"p{i}" for i in range(count));nodes=[];flows=[]
    for p in ps:nodes += [node(p+"s",p,"startEvent"),node(p+"e",p,"endEvent")];flows += [flow(p+"f",p+"s",p+"e",p)]
    root,_,_=generate(workflow(ps,nodes,flows));assert len(root.findall("bpmn:process",NS))==count
def test_participant_process_ref_exact():
    root,_,_=generate(workflow(participants=[participant("pa","p1")]));assert root.find("bpmn:collaboration/bpmn:participant",NS).get("processRef")=="p1"
def test_lanes_belong_to_declared_process():
    nodes=[node("s",kind="startEvent",participant="pa",lane="la"),node("e",kind="endEvent",participant="pa",lane="la")];root,_,_=generate(workflow(nodes=nodes,participants=[participant("pa","p1")],lanes=[lane("la","p1","pa")]));assert root.find("bpmn:process[@id='p1']/bpmn:laneSet/bpmn:lane[@id='la']",NS) is not None
def test_flow_node_refs_come_from_node_lane_ref():
    nodes=[node("s",kind="startEvent",participant="pa",lane="la"),node("e",kind="endEvent",participant="pa",lane="la")];root,_,_=generate(workflow(nodes=nodes,participants=[participant("pa","p1")],lanes=[lane("la","p1","pa")]));assert [x.text for x in root.findall(".//bpmn:lane[@id='la']/bpmn:flowNodeRef",NS)]==["e","s"]
def test_sequence_source_and_target_xml_attributes():
    root,_,_=generate(workflow());element=root.find(".//bpmn:sequenceFlow",NS);assert element.get("sourceRef")=="s" and element.get("targetRef")=="e"
def test_message_flow_is_in_collaboration_only():
    nodes=[node("a","p1","startEvent"),node("ae","p1","endEvent"),node("b","p2","startEvent"),node("be","p2","endEvent")];flows=[flow("1","a","ae","p1"),flow("2","b","be","p2")];msg={"id":"m","source":"a","target":"b","properties":{}};root,_,_=generate(workflow(("p1","p2"),nodes,flows,messages=[msg]));assert root.find("bpmn:collaboration/bpmn:messageFlow[@id='m']",NS) is not None and root.find("bpmn:process/bpmn:messageFlow",NS) is None
@pytest.mark.parametrize("kind",TYPES)
def test_all_canonical_node_types(kind):
    properties={"timer":{"kind":"duration","value":"PT5M"}} if kind=="intermediateCatchEvent" else {};root,_,_=generate(workflow(nodes=[node("x",kind=kind,properties=properties)],flows=[]));assert root.find(f".//bpmn:{kind}",NS) is not None
@pytest.mark.parametrize("kind,tag_name,value",[("duration","timeDuration","PT5M"),("date","timeDate","2027-01-01T00:00:00Z"),("cycle","timeCycle","R/PT1H")])
def test_timer_mapping(kind,tag_name,value):
    root,_,_=generate(workflow(nodes=[node("timer",kind="intermediateCatchEvent",properties={"timer":{"kind":kind,"value":value}})],flows=[]));assert root.find(f".//bpmn:timerEventDefinition/bpmn:{tag_name}",NS).text==value
def test_process_without_participant():
    root,_,_=generate(workflow());assert root.find("bpmn:process[@id='p1']",NS) is not None and root.find("bpmn:collaboration",NS) is None
def test_node_shapes_exist():
    root,_,_=generate(workflow());assert len(root.findall(".//bpmndi:BPMNShape",NS))==2
def test_participant_shape_exists():
    root,_,_=generate(workflow(participants=[participant("pa","p1")]));assert root.find(".//bpmndi:BPMNShape[@bpmnElement='pa']",NS) is not None
def test_lane_shape_exists():
    nodes=[node("s",kind="startEvent",participant="pa",lane="la"),node("e",kind="endEvent",participant="pa",lane="la")];root,_,_=generate(workflow(nodes=nodes,participants=[participant("pa","p1")],lanes=[lane("la","p1","pa")]));assert root.find(".//bpmndi:BPMNShape[@bpmnElement='la']",NS) is not None
def test_sequence_edge_exists():
    root,_,_=generate(workflow());assert root.find(".//bpmndi:BPMNEdge[@bpmnElement='f']",NS) is not None
def test_message_edge_exists():
    nodes=[node("a","p1"),node("b","p2")];msg={"id":"m","source":"a","target":"b","properties":{}};root,_,_=generate(workflow(("p1","p2"),nodes,[],messages=[msg]));assert root.find(".//bpmndi:BPMNEdge[@bpmnElement='m']",NS) is not None
def test_plane_references_collaboration():
    root,_,_=generate(workflow(participants=[participant("pa","p1")]));assert root.find(".//bpmndi:BPMNPlane",NS).get("bpmnElement").startswith("Collaboration_")
def test_plane_references_simple_process():
    root,_,_=generate(workflow());assert root.find(".//bpmndi:BPMNPlane",NS).get("bpmnElement")=="p1"
def test_generator_immutability():
    w=workflow();analysis=WorkflowGraphAnalyzer().analyze(w);layout=BPMNLayoutEngine().layout(w,analysis);wb=copy.deepcopy(w);lb=copy.deepcopy(layout);BPMNGenerator().generate(w,layout);assert w==wb and layout==lb
def test_xml_is_parseable():
    _,_,xml=generate(workflow());assert ET.fromstring(xml).tag.endswith("definitions")
def test_timer_event_type_is_rejected():
    with pytest.raises(ValueError):generate(workflow(nodes=[node("x",kind="timerEvent")],flows=[]))
