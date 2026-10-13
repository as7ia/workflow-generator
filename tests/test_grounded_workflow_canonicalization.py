from src.workflow.canonicalization import GroundedWorkflowCanonicalizer
from src.workflow.semantic_requirements import normalize_semantic_requirements
from src.workflow.semantic_validator import SemanticValidator


def workflow():
    return {
        "schemaVersion": "2.0",
        "processes": [{"id": "process_a", "name": "A process", "description": "", "executable": False, "properties": {}}],
        "participants": [{"id": "participant_a", "name": "A", "processRef": "process_a", "properties": {}}],
        "lanes": [],
        "nodes": [
            {"id": "a_start", "type": "startEvent", "name": "Start", "processRef": "process_a", "participantRef": "participant_a", "laneRef": None, "actor": None, "properties": {}},
            {"id": "a_end", "type": "endEvent", "name": "End", "processRef": "process_a", "participantRef": "participant_a", "laneRef": None, "actor": None, "properties": {}},
        ],
        "flows": [{"id": "a_flow", "source": "a_start", "target": "a_end", "processRef": "process_a", "condition": None, "isDefault": False, "properties": {}}],
        "messageFlows": [], "ambiguities": [],
    }


def requirements(**values):
    result = {key: [] for key in ("participants", "lanes", "timers", "parallelisms", "decisions", "interactions", "activities", "ambiguities")}
    result.update(values)
    return result


def test_all_grounded_participants_receive_unique_valid_processes():
    fixed, report = GroundedWorkflowCanonicalizer.canonicalize(
        workflow(), requirements(participants=[{"name": name} for name in ("A", "B", "C")])
    )
    assert {item["name"] for item in fixed["participants"]} == {"A", "B", "C"}
    assert len({item["processRef"] for item in fixed["participants"]}) == 3
    assert not SemanticValidator().validate(fixed)
    assert len(report["participants_added"]) == 2


def test_timer_definition_node_becomes_supported_catch_event():
    value = workflow()
    value["nodes"].insert(1, {"id": "wait", "type": "timerDefinition", "name": "Wait", "processRef": "process_a", "participantRef": "participant_a", "laneRef": None, "actor": None, "properties": {"duration": "P2D"}})
    fixed, report = GroundedWorkflowCanonicalizer.canonicalize(value, requirements(timers=[{"kind": "duration", "value": "P2D"}]))
    timer = next(node for node in fixed["nodes"] if node["id"] == "wait")
    assert timer["type"] == "intermediateCatchEvent"
    assert timer["properties"]["timer"] == {"kind": "duration", "value": "P2D"}
    assert report["timer_nodes_corrected"] == [{"id": "wait", "type": "intermediateCatchEvent"}]


def test_grounded_interaction_uses_endpoints_in_distinct_processes():
    fixed, _ = GroundedWorkflowCanonicalizer.canonicalize(
        workflow(), requirements(
            participants=[{"name": "A"}, {"name": "B"}],
            interactions=[{"source": "A", "target": "B"}],
        )
    )
    by_id = {node["id"]: node for node in fixed["nodes"]}
    message = fixed["messageFlows"][0]
    assert by_id[message["source"]]["type"] == "sendTask"
    assert by_id[message["target"]]["type"] == "receiveTask"
    assert by_id[message["source"]]["processRef"] != by_id[message["target"]]["processRef"]


def test_local_message_is_reclassified_as_sequence_when_collaboration_is_grounded():
    value = workflow()
    value["messageFlows"] = [{"id": "local", "source": "a_start", "target": "a_end", "properties": {}}]
    fixed, report = GroundedWorkflowCanonicalizer.canonicalize(
        value, requirements(participants=[{"name": "A"}, {"name": "B"}], interactions=[{"source": "A", "target": "B"}])
    )
    assert all(message["id"] != "local" for message in fixed["messageFlows"])
    assert any(item["id"] == "local" for item in report["message_flows_reclassified"])


def test_decision_outcomes_remain_structured_objects():
    normalized, issues = normalize_semantic_requirements({"decisions": [{
        "name": "Request complete", "outcomes": [{"name": "Yes", "sourceLane": "Review"}, {"name": "No"}]
    }]})
    assert not issues
    assert normalized["decisions"][0]["outcomes"] == [{"name": "Yes", "sourceLane": "Review"}, {"name": "No"}]
    assert all(isinstance(item, dict) for item in normalized["decisions"][0]["outcomes"])


def test_translated_department_participants_are_resolved_without_duplicates():
    value=workflow()
    value["processes"] += [
        {"id":"process_sales","name":"Sales process","description":"","executable":False,"properties":{}},
        {"id":"process_finance","name":"Finance process","description":"","executable":False,"properties":{}},
    ]
    value["participants"] += [
        {"id":"participant_sales","name":"Sales Department","processRef":"process_sales","properties":{}},
        {"id":"participant_finance","name":"Financial Department","processRef":"process_finance","properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"Service commercial"},{"name":"Service financier"}]))
    assert len(fixed["participants"])==3
    assert len({p["processRef"] for p in fixed["participants"]})==3
    assert {p["name"] for p in fixed["participants"]}=={"A","Service commercial","Service financier"}
    assert {item["matched_existing_id"] for item in report["participant_matches"]}=={
        "participant_a","participant_sales","participant_finance"}


def test_new_interaction_nodes_are_inserted_in_local_sequences():
    value=workflow()
    value["nodes"].insert(1,{"id":"a_task","type":"serviceTask","name":"Work","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[
        {"id":"a_1","source":"a_start","target":"a_task","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"a_2","source":"a_task","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"}]))
    by_id={node["id"]:node for node in fixed["nodes"]};message=fixed["messageFlows"][0]
    send=message["source"];receive=message["target"]
    edges={(flow["source"],flow["target"]) for flow in fixed["flows"]}
    assert ("a_task",send) in edges and (send,"a_end") in edges
    b_start=next(n["id"] for n in fixed["nodes"] if n["processRef"]==by_id[receive]["processRef"] and n["type"]=="startEvent")
    b_end=next(n["id"] for n in fixed["nodes"] if n["processRef"]==by_id[receive]["processRef"] and n["type"]=="endEvent")
    assert (b_start,receive) in edges and (receive,b_end) in edges
    assert {item["id"] for item in report["nodes_inserted"]}=={send,receive}
    forbidden={"ISOLATED_TASK","MISSING_INCOMING_FLOW","MISSING_OUTGOING_FLOW","UNREACHABLE_NODE"}
    assert forbidden.isdisjoint(issue["code"] for issue in SemanticValidator().validate(fixed))


def test_semantically_equivalent_receive_node_is_reused():
    value=workflow()
    value["nodes"].insert(1,{"id":"received","type":"serviceTask","name":"Order Received","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[
        {"id":"a_1","source":"a_start","target":"received","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"a_2","source":"received","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"B","target":"A"}]))
    assert fixed["messageFlows"][0]["target"]=="received"
    assert len([n for n in fixed["nodes"] if n["processRef"]=="process_a" and n["type"]=="receiveTask"])==0
    assert {item["id"] for item in report["nodes_reused_for_interactions"]}=={"received"}
