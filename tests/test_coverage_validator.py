from src.workflow.coverage_validator import CoverageValidator
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
    req=requirements(participants=[{"name":"Actor A"}],lanes=[{"name":"Reviewer"}],timers=[{"duration":"P2D"}],
        parallelisms=[{"activities":["Review request","Archive request"]}],decisions=[{"name":"Choose outcome"}],
        interactions=[{"source":"Actor A","target":"Actor B"}],activities=[{"name":"Review request"}])
    assert not [x for x in CoverageValidator().validate(req,workflow()) if x["severity"]=="error"]
def test_requirement_normalization_and_ambiguity_warning():
    normalized,issues=normalize_semantic_requirements({"timers":[{"duration":"2 days"}],"ambiguities":["unclear role"]})
    assert not issues and normalized["timers"][0]["duration"]=="P2D"
    result=CoverageValidator().validate(normalized,workflow())
    assert any(x["severity"]=="warning" for x in result)
