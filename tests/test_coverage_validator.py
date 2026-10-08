from src.workflow.coverage_validator import CoverageValidator,normalize_semantic_label,semantic_label_match
from src.workflow.semantic_requirements import normalize_semantic_requirements

def node(i,name,t="serviceTask",participant=None,properties=None):
    return {"id":i,"name":name,"type":t,"processRef":"p","participantRef":participant,"laneRef":None,"actor":None,"properties":properties or {}}

def workflow():
    nodes=[node("s","Start","startEvent","pa"),node("g","Concurrent work","parallelGateway","pa"),
           node("review","Review request","serviceTask","pa"),node("archive","Archive request","serviceTask","pa"),
           node("decision","Choose outcome","exclusiveGateway","pa"),
           node("timer","Wait two days","intermediateCatchEvent","pa",{"timer":{"kind":"duration","value":"P2D"}}),
           node("receive","Receive notice","receiveTask","pb"),node("e","End","endEvent","pa")]
    flows=[{"id":i,"source":a,"target":b,"processRef":"p","condition":None,"isDefault":False,"properties":{}}
           for i,(a,b) in enumerate((("s","g"),("g","review"),("g","archive"),("review","decision"),("archive","decision"),("decision","timer"),("decision","e"),("timer","e")),1)]
    return {"participants":[{"id":"pa","name":"Actor A","processRef":"p","properties":{}},{"id":"pb","name":"Actor B","processRef":"pbp","properties":{}}],
            "lanes":[{"id":"lane_review","name":"Reviewer","processRef":"p","participantRef":"pa","properties":{}}],
            "nodes":nodes,"flows":flows,"messageFlows":[{"id":"m","source":"review","target":"receive","properties":{}}]}

def requirements(**overrides):
    value={k:[] for k in ("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")};value.update(overrides);return value

def codes(req,w=None):return {x["code"] for x in CoverageValidator().validate(req,w or workflow())}

def test_missing_timer():assert "MISSING_TIMER" in codes(requirements(timers=[{"duration":"P3D"}]))
def test_missing_parallelism():
    w=workflow();w["nodes"]=[n for n in w["nodes"] if n["type"]!="parallelGateway"]
    assert "MISSING_PARALLELISM" in codes(requirements(parallelisms=[{"activities":["Review","Archive"]}]),w)
def test_missing_lane():assert "MISSING_LANE" in codes(requirements(lanes=[{"name":"Auditor"}]))
def test_missing_interaction():assert "MISSING_INTERACTION" in codes(requirements(interactions=[{"source":"Actor B","target":"Actor A"}]))
def test_missing_participant_decision_and_activity_codes():
    req=requirements(participants=[{"name":"Actor C"}],decisions=[{"name":"Approval"}],activities=[{"name":"Ship parcel"}])
    w=workflow();w["nodes"]=[n for n in w["nodes"] if n["type"]!="exclusiveGateway"]
    assert {"MISSING_PARTICIPANT","MISSING_DECISION","MISSING_ACTIVITY"}<=codes(req,w)
def test_all_requirements_covered():
    req=requirements(participants=[{"name":"Actor A"}],lanes=[{"name":"Reviewer","participant":"Actor A"}],timers=[{"duration":"P2D"}],
        parallelisms=[{"activities":["Review request","Archive request"]}],decisions=[{"name":"Choose outcome"}],
        interactions=[{"source":"Actor A","target":"Actor B"}],activities=[{"name":"Review request"}])
    assert not [x for x in CoverageValidator().validate(req,workflow()) if x["severity"]=="error"]
def test_requirement_normalization_and_ambiguity_warning():
    normalized,issues=normalize_semantic_requirements({"timers":[{"duration":"2 days"}],"ambiguities":["unclear role"]})
    assert not issues and normalized["timers"][0]=={"kind":"duration","value":"P2D"}
    result=CoverageValidator().validate(normalized,workflow())
    assert any(x["severity"]=="warning" for x in result)

def test_participants_lanes_and_interactions_are_canonical_and_deduplicated():
    raw={"participants":["Client","Entreprise","Banque","Client"],
         "lanes":[{"name":"Service Client","participant":"Entreprise"},{"name":"Finance","participant":"Entreprise"}],
         "interactions":[{"source":"Entreprise (Service Finance)","target":"Banque"},{"source":"Entreprise (Service Finance)","target":"Banque"}]}
    normalized,issues=normalize_semantic_requirements(raw)
    assert not issues and [x["name"] for x in normalized["participants"]]==["Client","Entreprise","Banque"]
    assert normalized["lanes"]==[{"name":"Service Client","participant":"Entreprise"},{"name":"Finance","participant":"Entreprise"}]
    assert normalized["interactions"]==[{"source":"Entreprise","sourceLane":"Finance","target":"Banque"}]

def test_parallel_branches_are_one_requirement_and_false_decisions_are_dropped():
    raw={"parallelisms":[{"activity":"Check identity"},{"activity":"Check amount"}],
         "decisions":[{"name":"Request received"},{"name":"Completeness","outcomes":["complete","incomplete"]}]}
    normalized,_=normalize_semantic_requirements(raw)
    assert normalized["parallelisms"]==[{"branches":[{"activity":"Check identity"},{"activity":"Check amount"}]}]
    assert normalized["decisions"]==[{"name":"Completeness","outcomes":["complete","incomplete"]}]

def test_activity_matching_ignores_safe_function_words():
    assert "MISSING_ACTIVITY" not in codes(requirements(activities=[{"name":"Review the request"}]))

def test_timer_activity_is_not_duplicated_as_task_requirement():
    normalized,_=normalize_semantic_requirements({"timers":[{"value":"P2D","activity":"Wait two days"}],"activities":["Wait two days"]})
    assert normalized["activities"]==[]

def test_lane_context_interaction_matches_participants_not_node_names():
    w=workflow();w["participants"]=[{"id":"company","name":"Company","processRef":"p","properties":{}},{"id":"bank","name":"Bank","processRef":"bp","properties":{}}]
    w["lanes"]=[{"id":"finance","name":"Service Finance","processRef":"p","participantRef":"company","properties":{}}]
    w["nodes"]=[node("send_payment","Transmit file","sendTask","company"),node("receive_file","Process file","receiveTask","bank")]
    w["messageFlows"]=[{"id":"m","source":"send_payment","target":"receive_file","properties":{}}]
    req=requirements(lanes=[{"name":"Finance","participant":"Company"}],interactions=[{"source":"Company","target":"Bank","sourceLane":"Finance"}])
    assert not CoverageValidator().validate(req,w)

def test_participants_match_normalized_names_not_only_ids():
    w=workflow();w["participants"]=[
        {"id":"pool_customer","name":"Client","processRef":"p","properties":{}},
        {"id":"pool_company","name":"Entreprise","processRef":"p2","properties":{}},
        {"id":"pool_bank","name":"Banque","processRef":"p3","properties":{}},
    ]
    req=requirements(participants=[{"name":"client"},{"name":"ENTREPRISE"},{"name":"Bánque !"}])
    assert "MISSING_PARTICIPANT" not in codes(req,w)

def test_empty_parallelism_is_not_a_blocking_requirement():
    normalized,_=normalize_semantic_requirements({"parallelisms":[{"branches":[]}]})
    assert normalized["parallelisms"]==[]
    assert "MISSING_PARALLELISM" not in codes(requirements(parallelisms=[{"branches":[]}]))

def test_lane_with_same_name_under_wrong_participant_is_missing():
    assert "MISSING_LANE" in codes(requirements(lanes=[{"name":"Reviewer","participant":"Actor B"}]))

def test_timer_requires_kind_and_value_and_reports_closest_timer_node():
    issues=CoverageValidator().validate(requirements(timers=[{"kind":"date","value":"P2D"}]),workflow())
    missing=next(issue for issue in issues if issue["code"]=="MISSING_TIMER")
    assert missing["related_node_ids"]==["timer"]

def test_same_duration_can_be_matched_in_distinct_activity_contexts():
    w=workflow();w["nodes"].append(node("timer_other","Wait other","intermediateCatchEvent","pa",{"timer":{"kind":"duration","value":"P2D"}}))
    req=requirements(timers=[{"kind":"duration","value":"P2D","activity":"Wait other"}])
    assert "MISSING_TIMER" not in codes(req,w)

def test_timer_activity_does_not_create_duplicate_missing_activity():
    req=requirements(timers=[{"kind":"duration","value":"P3D","activity":"Wait three days"}],activities=[{"name":"Wait three days"}])
    result=CoverageValidator().validate(req,workflow())
    assert [issue["code"] for issue in result].count("MISSING_TIMER")==1
    assert "MISSING_ACTIVITY" not in {issue["code"] for issue in result}

def test_decision_requires_matching_gateway_and_distinct_outcomes():
    w=workflow();gateway=next(item for item in w["nodes"] if item["id"]=="decision");gateway["name"]="Completeness"
    decision_flows=[item for item in w["flows"] if item["source"]=="decision"]
    decision_flows[0]["condition"]="complete";decision_flows[1]["condition"]="incomplete"
    req=requirements(decisions=[{"name":"Completeness","outcomes":["complete","incomplete"]}])
    assert "MISSING_DECISION" not in codes(req,w)
    unrelated=requirements(decisions=[{"name":"Payment decision","outcomes":["paid","unpaid"]}])
    assert "MISSING_DECISION" in codes(unrelated,w)

def test_parallelism_requires_expected_activities_on_distinct_branches():
    covered=requirements(parallelisms=[{"branches":[{"activity":"Review request"},{"activity":"Archive request"}]}])
    wrong=requirements(parallelisms=[{"branches":[{"activity":"Review request"},{"activity":"Wait two days"}]}])
    assert "MISSING_PARALLELISM" not in codes(covered)
    assert "MISSING_PARALLELISM" in codes(wrong)

def test_interaction_lane_context_uses_node_lane_and_rejects_wrong_lane():
    w=workflow();w["nodes"]=[node("send","Send","sendTask","pa"),node("receive","Receive","receiveTask","pb")]
    w["nodes"][0]["laneRef"]="lane_review";w["messageFlows"]=[{"id":"m","source":"send","target":"receive","properties":{}}]
    good=requirements(interactions=[{"source":"Actor A","target":"Actor B","sourceLane":"Reviewer"}])
    bad=requirements(interactions=[{"source":"Actor A","target":"Actor B","sourceLane":"Finance"}])
    assert "MISSING_INTERACTION" not in codes(good,w)
    assert "MISSING_INTERACTION" in codes(bad,w)

def test_controlled_activity_lexical_variants_match_but_distinct_objects_do_not():
    w=workflow();w["nodes"].append(node("close","Clôture dossier"))
    assert "MISSING_ACTIVITY" not in codes(requirements(activities=[{"name":"Clôturer le dossier"}]),w)
    w["nodes"].append(node("identity","Vérifier identité"))
    issues=CoverageValidator().validate(requirements(activities=[{"name":"Vérifier montant"}]),w)
    missing=next(issue for issue in issues if issue["code"]=="MISSING_ACTIVITY")
    assert "identity" in missing["related_node_ids"]

def test_semantically_duplicate_requirements_create_one_error():
    normalized,_=normalize_semantic_requirements({"activities":["Clôturer dossier","Cloture dossier"]})
    assert len(normalized["activities"])==1
    assert [issue["code"] for issue in CoverageValidator().validate(normalized,workflow())].count("MISSING_ACTIVITY")==1

def test_malformed_requirements_become_ambiguities_not_blocking_errors():
    normalized,issues=normalize_semantic_requirements({"parallelisms":[{"branches":[]}],"decisions":[{"name":"State"}],"interactions":[{"source":"A"}],"timers":[{"kind":"unknown"}]})
    assert not [issue for issue in issues if issue["severity"]=="error"]
    assert normalized["parallelisms"]==[] and normalized["decisions"]==[] and normalized["interactions"]==[] and normalized["timers"]==[]
    assert normalized["ambiguities"]
    assert not [issue for issue in CoverageValidator().validate(normalized,workflow()) if issue["severity"]=="error"]

def test_multilingual_semantic_label_matching_is_controlled():
    assert normalize_semantic_label("Réception de la demande") == "receive request"
    assert semantic_label_match("Entreprise","Enterprise")
    assert semantic_label_match("Réception de la demande","Enterprise Receives Request")
    assert semantic_label_match("Acceptation de la demande","Accept Request")
    assert semantic_label_match("Rejet de la demande","Reject Request")
    assert not semantic_label_match("Vérifier le montant","Archive customer file")

def test_multilingual_participant_activities_and_interaction_are_covered():
    w=workflow()
    w["participants"]=[{"id":"client","name":"Client","processRef":"pc","properties":{}},{"id":"enterprise","name":"Enterprise","processRef":"pe","properties":{}}]
    w["nodes"]=[node("send","Send request","sendTask","client"),node("receive","Enterprise Receives Request","receiveTask","enterprise"),node("accept","Accept Request","serviceTask","enterprise"),node("reject","Reject Request","serviceTask","enterprise")]
    w["messageFlows"]=[{"id":"message","source":"send","target":"receive","properties":{}}]
    req=requirements(participants=[{"name":"Entreprise"}],activities=[{"name":"Réception de la demande"},{"name":"Acceptation de la demande"},{"name":"Rejet de la demande"}],interactions=[{"source":"Client","target":"Entreprise"}])
    assert not CoverageValidator().validate(req,w)

def test_multilingual_matching_does_not_relax_real_timer_requirement():
    req=requirements(timers=[{"kind":"duration","value":"P1D","activity":"Vérification de la demande"}])
    assert "MISSING_TIMER" in codes(req,workflow())
