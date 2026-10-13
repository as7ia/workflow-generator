import copy

from src.generation.pipeline import WorkflowCompiler
from src.workflow.semantic_requirements import normalize_semantic_requirements
from tests.test_generation_pipeline import empty_requirements,f,n,v2


def collaboration():
    participants=[{"id":"actor_a","name":"A","processRef":"pa","properties":{}},{"id":"actor_b","name":"B","processRef":"pb","properties":{}}]
    lanes=[{"id":"lane_a","name":"Sender","processRef":"pa","participantRef":"actor_a","properties":{}},
           {"id":"lane_b","name":"Reviewer","processRef":"pb","participantRef":"actor_b","properties":{}}]
    nodes=[n("send","pa","startEvent","actor_a","lane_a"),n("a_end","pa","endEvent","actor_a","lane_a"),
           n("receive","pb","receiveTask","actor_b","lane_b"),n("check","pb","serviceTask","actor_b","lane_b"),n("b_end","pb","endEvent","actor_b","lane_b")]
    nodes[0]["name"]="Send Request";nodes[2]["name"]="Receive Request";nodes[3]["name"]="Check Request"
    return v2(("pa","pb"),nodes,[f("a_flow","send","a_end","pa"),f("b_one","receive","check","pb"),f("b_two","check","b_end","pb")],
              participants,lanes,[{"id":"message","source":"send","target":"receive","properties":{}}])


def test_alignment_is_local_and_preserves_collaboration_identically():
    original=collaboration();requirements=empty_requirements(activities=[{"name":"Send Request"},{"name":"Check Request"}])
    aligned=WorkflowCompiler._align_semantic_roles_transactionally(original,requirements)
    for key in ("processes","participants","lanes","messageFlows"):
        assert aligned[key]==original[key]
    send=next(node for node in aligned["nodes"] if node["id"]=="send")
    assert send["type"]=="sendTask" and send["processRef"]=="pa" and send["participantRef"]=="actor_a" and send["laneRef"]=="lane_a"
    assert any(node["type"]=="startEvent" and node["processRef"]=="pa" and node["participantRef"]=="actor_a" for node in aligned["nodes"])


def test_explicit_actions_are_preserved_by_requirement_normalization():
    raw=empty_requirements(activities=[{"name":"Send Request"},{"name":"Check Request"}])
    normalized,_=normalize_semantic_requirements(raw,"A sends a request to B. B checks it.")
    assert normalized["activities"]==[{"name":"Send Request"},{"name":"Check Request"}]


def test_active_and_passive_statements_are_distinguished_conservatively():
    cases=(("A approves the request.","Approve Request",True),("The request is approved.","Approve Request",False),
           ("A rejects the request.","Reject Request",True),("The request is rejected.","Reject Request",False))
    for description,name,is_activity in cases:
        normalized,_=normalize_semantic_requirements(empty_requirements(activities=[{"name":name}]),description)
        assert bool(normalized["activities"]) is is_activity


def test_alignment_reuses_existing_message_flow_and_participants():
    original=collaboration();aligned=WorkflowCompiler._align_semantic_roles_transactionally(
        original,empty_requirements(activities=[{"name":"Send Request"}],interactions=[{"source":"A","target":"B"}]))
    assert {item["id"] for item in aligned["participants"]}=={"actor_a","actor_b"}
    assert {item["id"] for item in aligned["processes"]}=={"pa","pb"}
    assert aligned["messageFlows"]==original["messageFlows"]
    assert next(node for node in aligned["nodes"] if node["id"]=="send")["type"]=="sendTask"


def test_transaction_rejects_deleted_participant_or_message_flow():
    original=collaboration()
    without_participant=copy.deepcopy(original);without_participant["participants"]=without_participant["participants"][1:]
    without_message=copy.deepcopy(original);without_message["messageFlows"]=[]
    assert WorkflowCompiler._accept_semantic_alignment_candidate(original,without_participant)==original
    assert WorkflowCompiler._accept_semantic_alignment_candidate(original,without_message)==original
