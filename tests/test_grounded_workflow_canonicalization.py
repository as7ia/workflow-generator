from src.workflow.canonicalization import GroundedWorkflowCanonicalizer
from src.workflow.semantic_requirements import normalize_semantic_requirements
from src.workflow.semantic_validator import SemanticValidator
from src.workflow.coverage_validator import CoverageValidator,normalize_semantic_tokens, semantic_label_match


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


def test_shared_semantic_matcher_resolves_roles_and_rejects_unrelated_roles():
    assert semantic_label_match("commercial department","sales department")
    assert semantic_label_match("financial service","finance department")
    assert not semantic_label_match("customer","finance")
    assert not semantic_label_match("supplier","sales")
    assert normalize_semantic_tokens("Service commercial")==normalize_semantic_tokens("Sales")


def test_bidirectional_interactions_are_inserted_in_causal_local_order():
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(workflow(),requirements(
        participants=[{"name":"A"},{"name":"B"}],
        interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}],
    ))
    participants={p["name"]:p for p in fixed["participants"]};nodes={n["id"]:n for n in fixed["nodes"]}
    messages=fixed["messageFlows"]
    assert len(messages)==2
    endpoints={(nodes[m["source"]]["participantRef"],nodes[m["target"]]["participantRef"]) for m in messages}
    assert endpoints=={(participants["A"]["id"],participants["B"]["id"]),(participants["B"]["id"],participants["A"]["id"])}
    forbidden={"ISOLATED_TASK","MISSING_INCOMING_FLOW","MISSING_OUTGOING_FLOW","UNREACHABLE_NODE"}
    assert forbidden.isdisjoint(issue["code"] for issue in SemanticValidator().validate(fixed))
    assert len(report["interaction_endpoint_insertions"])==4


def test_wrongly_owned_action_is_not_reused_as_other_participant_endpoint():
    value=workflow()
    value["processes"].append({"id":"process_b","name":"B","description":"","executable":False,"properties":{}})
    value["participants"].append({"id":"participant_b","name":"B","processRef":"process_b","properties":{}})
    value["nodes"] += [
        {"id":"b_start","type":"startEvent","name":"Start","processRef":"process_b","participantRef":"participant_b","laneRef":None,"actor":None,"properties":{}},
        {"id":"wrong","type":"sendTask","name":"A sends request","processRef":"process_b","participantRef":"participant_b","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_end","type":"endEvent","name":"End","processRef":"process_b","participantRef":"participant_b","laneRef":None,"actor":None,"properties":{}},
    ]
    value["flows"] += [
        {"id":"b1","source":"b_start","target":"wrong","processRef":"process_b","condition":None,"isDefault":False,"properties":{}},
        {"id":"b2","source":"wrong","target":"b_end","processRef":"process_b","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,_=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"}]))
    message=fixed["messageFlows"][0];nodes={n["id"]:n for n in fixed["nodes"]}
    assert message["source"]!="wrong"
    assert nodes[message["source"]]["participantRef"]=="participant_a"
    assert next(n for n in fixed["nodes"] if n["id"]=="wrong")["processRef"]=="process_b"


def test_failed_endpoint_insertion_is_atomic():
    value=workflow()
    value["nodes"].append({"id":"a_end_2","type":"endEvent","name":"Alternative end","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    before_nodes=list(value["nodes"]);before_flows=list(value["flows"])
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"}]))
    assert [n for n in fixed["nodes"] if n.get("processRef")=="process_a"]==before_nodes
    assert [f for f in fixed["flows"] if f.get("processRef")=="process_a"]==before_flows
    assert not any(n.get("type")=="sendTask" and n.get("processRef")=="process_a" for n in fixed["nodes"])
    assert report["interaction_endpoint_rollbacks"][0]["reason"]=="No unique boundary event"


def test_participant_resolution_report_exposes_every_candidate_decision():
    value=workflow();value["processes"].append({"id":"sales_process","name":"Sales","description":"","executable":False,"properties":{}})
    value["participants"].append({"id":"sales","name":"Sales","processRef":"sales_process","properties":{}})
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"Service commercial"}]))
    attempt=next(item for item in report["participant_resolution_attempts"] if item["candidate_id"]=="sales" and item["requirement"]=="Service commercial")
    assert attempt["semantic_match"] is True and attempt["decision"]=="reuse"
    assert attempt["normalized_requirement"]==attempt["normalized_candidate"]==["sales"]
    assert len(fixed["participants"])==2


def test_existing_business_message_endpoints_are_reused_in_both_directions():
    value=workflow();value["participants"][0]["name"]="Customer"
    value["processes"].append({"id":"department_process","name":"Operations","description":"","executable":False,"properties":{}})
    value["participants"].append({"id":"department","name":"Operations Department","processRef":"department_process","properties":{}})
    value["nodes"] += [
        {"id":"d_start","type":"startEvent","name":"Start","processRef":"department_process","participantRef":"department","laneRef":None,"actor":None,"properties":{}},
        {"id":"request_received","type":"receiveTask","name":"Customer submits document","processRef":"department_process","participantRef":"department","laneRef":None,"actor":None,"properties":{}},
        {"id":"notify_customer","type":"sendTask","name":"Notify customer","processRef":"department_process","participantRef":"department","laneRef":None,"actor":None,"properties":{}},
        {"id":"d_end","type":"endEvent","name":"End","processRef":"department_process","participantRef":"department","laneRef":None,"actor":None,"properties":{}},
    ]
    value["flows"] += [
        {"id":"d1","source":"d_start","target":"request_received","processRef":"department_process","condition":None,"isDefault":False,"properties":{}},
        {"id":"d2","source":"request_received","target":"notify_customer","processRef":"department_process","condition":None,"isDefault":False,"properties":{}},
        {"id":"d3","source":"notify_customer","target":"d_end","processRef":"department_process","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"Customer"},{"name":"Operations Department"}],
        interactions=[{"source":"Customer","target":"Operations Department"},{"source":"Operations Department","target":"Customer"}]))
    messages={(m["source"],m["target"]) for m in fixed["messageFlows"]}
    assert any(target=="request_received" for _,target in messages)
    assert any(source=="notify_customer" for source,_ in messages)
    assert {item["id"] for item in report["interaction_endpoint_reuse"]}>={"request_received","notify_customer"}


def test_three_ordered_interactions_append_endpoints_without_isolation():
    fixed,_=GroundedWorkflowCanonicalizer.canonicalize(workflow(),requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[
            {"source":"A","target":"B"},{"source":"B","target":"A"},{"source":"A","target":"B"}]))
    participant_a=next(p for p in fixed["participants"] if p["name"]=="A")
    local={n["id"]:n for n in fixed["nodes"] if n["processRef"]==participant_a["processRef"]}
    outgoing={f["source"]:f["target"] for f in fixed["flows"] if f["source"] in local and f["target"] in local}
    current=next(node_id for node_id,node in local.items() if node["type"]=="startEvent");types=[]
    while current in outgoing:
        current=outgoing[current];types.append(local[current]["type"])
    assert [kind for kind in types if kind in {"sendTask","receiveTask"}]==["sendTask","receiveTask","sendTask"]
    forbidden={"ISOLATED_TASK","MISSING_INCOMING_FLOW","MISSING_OUTGOING_FLOW","UNREACHABLE_NODE"}
    assert forbidden.isdisjoint(issue["code"] for issue in SemanticValidator().validate(fixed))


def test_local_self_message_is_rejected_instead_of_becoming_sequence_self_loop():
    value=workflow();value["messageFlows"]=[{"id":"self","source":"a_start","target":"a_start","properties":{}}]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"}]))
    assert not any(flow["source"]==flow["target"] for flow in fixed["flows"])
    assert not any(message["source"]==message["target"] for message in fixed["messageFlows"])
    assert report["self_loop_rejections"]==[{"flow_id":"self","source":"a_start","target":"a_start"}]

def test_unique_highest_endpoint_score_is_selected_and_explained():
    value=workflow();value["nodes"].insert(1,{"id":"notify_b","type":"sendTask","name":"Notify B","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[
        {"id":"x1","source":"a_start","target":"notify_b","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"x2","source":"notify_b","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"}]))
    source_report=next(item for item in report["interaction_endpoint_candidates"] if item["role"]=="source")
    assert source_report["selected"]=="notify_b"
    assert source_report["best_score"]>=source_report["selection_threshold"]
    assert source_report["selection_reason"]=="unique best compatible endpoint"
    assert fixed["messageFlows"][0]["source"]=="notify_b"

def test_simple_grounded_decision_is_materialized_with_conditioned_branches():
    value=workflow();value["nodes"].insert(1,{"id":"check","type":"serviceTask","name":"Check request","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["nodes"].insert(2,{"id":"continue","type":"serviceTask","name":"Continue","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[
        {"id":"d1","source":"a_start","target":"check","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"d2","source":"check","target":"continue","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"d3","source":"continue","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    req=requirements(decisions=[{"name":"Check request","outcomes":["Continue","Correct"]}])
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,req)
    gateway=next(n for n in fixed["nodes"] if n["type"]=="exclusiveGateway")
    branches=[f for f in fixed["flows"] if f["source"]==gateway["id"]]
    assert {flow["condition"] for flow in branches}=={"Continue","Correct"}
    assert report["decisions_materialized"][0]["gateway"]==gateway["id"]
    assert "MISSING_DECISION" not in {issue["code"] for issue in CoverageValidator().validate(req,fixed)}

def test_grounded_decision_branch_may_loop_to_the_check_activity():
    value=workflow();value["nodes"].insert(1,{"id":"check","type":"serviceTask","name":"Check request","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["nodes"].insert(2,{"id":"continue","type":"serviceTask","name":"Continue","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[
        {"id":"l1","source":"a_start","target":"check","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"l2","source":"check","target":"continue","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"l3","source":"continue","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,_=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        decisions=[{"name":"Check request","outcomes":["Continue","Check request"]}]))
    gateway=next(n for n in fixed["nodes"] if n["type"]=="exclusiveGateway")
    assert any(f["source"]==gateway["id"] and f["target"]=="check" for f in fixed["flows"])

def test_existing_endpoints_reuse_does_not_require_unique_boundary_or_rewire_flows():
    value=workflow();value["processes"].append({"id":"p_b","name":"B","description":"","executable":False,"properties":{}})
    value["participants"].append({"id":"p_b_owner","name":"B","processRef":"p_b","properties":{}})
    value["nodes"] += [
        {"id":"b_start","type":"startEvent","name":"Start","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_start_alt","type":"startEvent","name":"Alternative start","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_receive","type":"receiveTask","name":"A submits request","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_work","type":"serviceTask","name":"Work","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_send","type":"sendTask","name":"Notify A","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_end","type":"endEvent","name":"End","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
        {"id":"b_end_alt","type":"endEvent","name":"Alternative end","processRef":"p_b","participantRef":"p_b_owner","laneRef":None,"actor":None,"properties":{}},
    ]
    value["flows"] += [
        {"id":"bf1","source":"b_start","target":"b_receive","processRef":"p_b","condition":None,"isDefault":False,"properties":{}},
        {"id":"bf2","source":"b_receive","target":"b_work","processRef":"p_b","condition":None,"isDefault":False,"properties":{}},
        {"id":"bf3","source":"b_work","target":"b_send","processRef":"p_b","condition":None,"isDefault":False,"properties":{}},
        {"id":"bf4","source":"b_send","target":"b_end","processRef":"p_b","condition":None,"isDefault":False,"properties":{}},
        {"id":"bf5","source":"b_start_alt","target":"b_end_alt","processRef":"p_b","condition":None,"isDefault":False,"properties":{}},
    ]
    before=[dict(flow) for flow in value["flows"] if flow["processRef"]=="p_b"]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}]))
    assert [flow for flow in fixed["flows"] if flow["processRef"]=="p_b"]==before
    assert {(m["source"],m["target"]) for m in fixed["messageFlows"]}>={(next(m["source"] for m in fixed["messageFlows"] if m["target"]=="b_receive"),"b_receive"),("b_send",next(m["target"] for m in fixed["messageFlows"] if m["source"]=="b_send"))}
    reused={item["id"] for item in report["interaction_endpoint_reuse"]}
    assert {"b_receive","b_send"}<=reused
    details=[candidate for group in report["interaction_endpoint_candidates"] for candidate in group["candidates"] if candidate["node_id"] in reused]
    assert all(item["candidate_mode"]=="reuse" and item["topology_valid"] and item["topology_reason"]=="existing node already connected" for item in details)

def test_decision_activity_scoring_selects_unique_best_candidate():
    value=workflow();value["nodes"]=[value["nodes"][0],
        {"id":"best","type":"serviceTask","name":"Verify application","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}},
        {"id":"medium","type":"serviceTask","name":"Verify record","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}},
        value["nodes"][-1]]
    value["flows"]=[
        {"id":"s1","source":"a_start","target":"medium","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"s2","source":"medium","target":"best","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"s3","source":"best","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        decisions=[{"name":"Verify application","outcomes":["Accept","Reject"]}]))
    diagnostic=report["decision_activity_candidates"][0]
    assert diagnostic["selected"]=="best" and diagnostic["best_score"]>diagnostic["second_best_score"]
    assert any(node["type"]=="exclusiveGateway" for node in fixed["nodes"])

def test_equal_best_decision_activities_remain_ambiguous():
    value=workflow();value["nodes"]=[value["nodes"][0],
        {"id":"check_1","type":"serviceTask","name":"Verify application","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}},
        {"id":"check_2","type":"serviceTask","name":"Verify application","processRef":"process_a","participantRef":"participant_a","laneRef":None,"actor":None,"properties":{}},value["nodes"][-1]]
    value["flows"]=[
        {"id":"e1","source":"a_start","target":"check_1","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"e2","source":"check_1","target":"check_2","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
        {"id":"e3","source":"check_2","target":"a_end","processRef":"process_a","condition":None,"isDefault":False,"properties":{}},
    ]
    fixed,report=GroundedWorkflowCanonicalizer.canonicalize(value,requirements(
        decisions=[{"name":"Verify application","outcomes":["Accept","Reject"]}]))
    diagnostic=report["decision_activity_candidates"][0]
    assert diagnostic["selected"] is None and diagnostic["best_score"]==diagnostic["second_best_score"]
    assert diagnostic["reason"]=="equal best decision activities"
    assert not any(node["type"]=="exclusiveGateway" for node in fixed["nodes"])
