import copy

from tests.test_generation_pipeline import CoverageGenerator,SCHEMA,Xml,empty_requirements,f,n,v2
from src.generation.pipeline import WorkflowCompiler
from src.workflow.coverage_validator import CoverageValidator
from src.workflow.semantic_requirements import normalize_semantic_requirements


def compile_with(requirements,workflow,description):
    compiler=WorkflowCompiler(CoverageGenerator(requirements,workflow),SCHEMA,max_repair_attempts=0)
    compiler._bpmn_generator=Xml()
    return compiler.compile(description)


def test_action_at_start_and_end_becomes_task_with_structural_events():
    requirements=empty_requirements(activities=[{"name":"Review Document"},{"name":"Approve Document"}])
    workflow=v2(nodes=[n("review",t="startEvent"),n("approve",t="endEvent")],flows=[f("sequence","review","approve")])
    workflow["nodes"][0]["name"]="Review Document";workflow["nodes"][1]["name"]="Approve Document"
    result=compile_with(requirements,workflow,"A reviews the document. A approves the document and the process ends.")
    assert result["success"]
    nodes=result["normalized_workflow"]["nodes"]
    assert next(node for node in nodes if node["id"]=="review")["type"]=="serviceTask"
    assert next(node for node in nodes if node["id"]=="approve")["type"]=="serviceTask"
    assert any(node["type"]=="startEvent" and node["id"]!="review" for node in nodes)
    assert any(node["type"]=="endEvent" and node["id"]!="approve" for node in nodes)
    assert not CoverageValidator().validate(result["semantic_requirements"],result["normalized_workflow"])


def test_message_source_action_is_reclassified_as_send_task():
    workflow=v2(("pa","pb"),nodes=[n("send","pa","startEvent","actor_a"),n("a_end","pa","endEvent","actor_a"),n("receive","pb","receiveTask","actor_b"),n("b_end","pb","endEvent","actor_b")],
                flows=[f("a_flow","send","a_end","pa"),f("b_flow","receive","b_end","pb")],
                participants=[{"id":"actor_a","name":"A","processRef":"pa","properties":{}},{"id":"actor_b","name":"B","processRef":"pb","properties":{}}],
                messages=[{"id":"message","source":"send","target":"receive","properties":{}}])
    next(node for node in workflow["nodes"] if node["id"]=="send")["name"]="Send Document"
    requirements=empty_requirements(activities=[{"name":"Send Document"}],interactions=[{"source":"A","target":"B"}])
    aligned=WorkflowCompiler._align_semantic_roles(copy.deepcopy(workflow),requirements)
    assert next(node for node in aligned["nodes"] if node["id"]=="send")["type"]=="sendTask"
    assert any(node["type"]=="startEvent" and node["id"]!="send" for node in aligned["nodes"])


def test_passive_terminal_state_is_not_an_activity_requirement():
    for description,activity in (("The document is approved.","Approve Document"),("The request is rejected.","Reject Request")):
        raw=empty_requirements(activities=[{"name":activity}])
        normalized,_=normalize_semantic_requirements(raw,description)
        assert normalized["activities"]==[]


def test_active_terminal_action_remains_an_activity_requirement():
    raw=empty_requirements(activities=[{"name":"Approve Document"}])
    normalized,_=normalize_semantic_requirements(raw,"B approves the document and the process ends.")
    assert normalized["activities"]==[{"name":"Approve Document"}]


def test_message_start_trigger_remains_a_start_event():
    workflow=v2(nodes=[n("message_start",t="startEvent"),n("work"),n("end",t="endEvent")],flows=[f("one","message_start","work"),f("two","work","end")])
    workflow["nodes"][0]["name"]="Request Received"
    aligned=WorkflowCompiler._align_semantic_roles(workflow,empty_requirements())
    assert next(node for node in aligned["nodes"] if node["id"]=="message_start")["type"]=="startEvent"


def test_timer_start_trigger_remains_a_start_event():
    workflow=v2(nodes=[n("timer_start",t="startEvent",properties={"timer":{"kind":"cycle","value":"R/P1W"}}),n("work"),n("end",t="endEvent")],flows=[f("one","timer_start","work"),f("two","work","end")])
    workflow["nodes"][0]["name"]="Every Monday"
    aligned=WorkflowCompiler._align_semantic_roles(workflow,empty_requirements(timers=[{"kind":"cycle","value":"R/P1W"}]))
    assert next(node for node in aligned["nodes"] if node["id"]=="timer_start")["type"]=="startEvent"
