"""Deterministic FR/EN acceptance matrix; no remote generator is used."""
from __future__ import annotations

import json
from pathlib import Path

from src.workflow.activity_semantics import analyze_activity_semantics, normalize_semantic_tokens
from src.workflow.canonicalization import GroundedWorkflowCanonicalizer
from src.workflow.coverage_validator import CoverageValidator
from src.workflow.repair import _StandardSchemaValidator
from src.workflow.semantic_validator import SemanticValidator


SCHEMA=json.loads((Path(__file__).parents[2]/"schemas"/"workflow.schema.json").read_text(encoding="utf-8"))


def requirements(**overrides):
    value={key:[] for key in ("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")}
    value.update(overrides);return value


def process_fixture(task_name="Verify document",task_type="serviceTask"):
    return {"schemaVersion":"2.0","processes":[{"id":"p","name":"Process","description":"","executable":False,"properties":{}}],
        "participants":[{"id":"owner","name":"Company","processRef":"p","properties":{}}],"lanes":[],
        "nodes":[
            {"id":"start","type":"startEvent","name":"Start","processRef":"p","participantRef":"owner","laneRef":None,"actor":None,"properties":{}},
            {"id":"task","type":task_type,"name":task_name,"processRef":"p","participantRef":"owner","laneRef":None,"actor":None,"properties":{}},
            {"id":"end","type":"endEvent","name":"End","processRef":"p","participantRef":"owner","laneRef":None,"actor":None,"properties":{}}],
        "flows":[{"id":"f1","source":"start","target":"task","processRef":"p","condition":None,"isDefault":False,"properties":{}},
                 {"id":"f2","source":"task","target":"end","processRef":"p","condition":None,"isDefault":False,"properties":{}}],
        "messageFlows":[],"ambiguities":[]}


def assert_accepted(workflow,req):
    fixed,_=GroundedWorkflowCanonicalizer.canonicalize(workflow,req)
    assert _StandardSchemaValidator(SCHEMA).validate(fixed)==[]
    structural=[issue for issue in SemanticValidator().validate(fixed) if issue.get("severity")=="error"]
    coverage=[issue for issue in CoverageValidator().validate(req,fixed) if issue.get("severity")=="error"]
    assert structural==[];assert coverage==[]
    for process in fixed["processes"]:
        local=[node for node in fixed["nodes"] if node["processRef"]==process["id"]]
        assert any(node["type"]=="startEvent" for node in local)
        assert any(node["type"]=="endEvent" for node in local)
    pairs=[(m["source"],m["target"],tuple(sorted(normalize_semantic_tokens(m.get("name"))))) for m in fixed["messageFlows"]]
    assert len(pairs)==len(set(pairs))
    return fixed


def collaboration_fixture():
    value=process_fixture("Work","serviceTask");value["participants"][0]["name"]="A"
    value["processes"].append({"id":"pb","name":"B","description":"","executable":False,"properties":{}})
    value["participants"].append({"id":"b","name":"B","processRef":"pb","properties":{}})
    value["nodes"] += [
        {"id":"bs","type":"startEvent","name":"Start","processRef":"pb","participantRef":"b","laneRef":None,"actor":None,"properties":{}},
        {"id":"bw","type":"serviceTask","name":"Work","processRef":"pb","participantRef":"b","laneRef":None,"actor":None,"properties":{}},
        {"id":"be","type":"endEvent","name":"End","processRef":"pb","participantRef":"b","laneRef":None,"actor":None,"properties":{}}]
    value["flows"] += [{"id":"bf1","source":"bs","target":"bw","processRef":"pb","condition":None,"isDefault":False,"properties":{}},
                       {"id":"bf2","source":"bw","target":"be","processRef":"pb","condition":None,"isDefault":False,"properties":{}}]
    return value


def test_linear_fr():
    assert_accepted(process_fixture("Vérifier document"),requirements(activities=[{"name":"Vérification du document"}]))

def test_linear_en():
    assert_accepted(process_fixture("Check document"),requirements(activities=[{"name":"Verify document"}]))

def test_decision_fr():
    fixed=assert_accepted(process_fixture("Vérifier document"),requirements(
        activities=[{"name":"Vérifier document"}],decisions=[{"name":"Vérifier document","outcomes":["Valide","Invalide"]}]))
    assert any(node["type"]=="exclusiveGateway" for node in fixed["nodes"])

def test_decision_en():
    fixed=assert_accepted(process_fixture("Check document"),requirements(
        activities=[{"name":"Verify document"}],decisions=[{"name":"Verify document","outcomes":["Valid","Invalid"]}]))
    assert any(node["type"]=="exclusiveGateway" for node in fixed["nodes"])

def _timer(label):
    value=process_fixture(label,"intermediateCatchEvent");value["nodes"][1]["properties"]={"timer":{"kind":"duration","value":"P1D"}}
    return value

def test_timer_fr():
    assert_accepted(_timer("Attendre un jour"),requirements(timers=[{"kind":"duration","value":"P1D","activity":"Attendre"}],activities=[{"name":"Attendre"}]))

def test_timer_en():
    assert_accepted(_timer("Wait one day"),requirements(timers=[{"kind":"duration","value":"P1D","activity":"Wait"}],activities=[{"name":"Wait"}]))

def _loop(check,correction):
    value=process_fixture(check);value["nodes"].insert(2,{"id":"correction","type":"serviceTask","name":correction,"processRef":"p","participantRef":"owner","laneRef":None,"actor":None,"properties":{}})
    value["flows"]=[{"id":"l1","source":"start","target":"task","processRef":"p","condition":None,"isDefault":False,"properties":{}},
                    {"id":"l2","source":"task","target":"correction","processRef":"p","condition":None,"isDefault":False,"properties":{}},
                    {"id":"l3","source":"correction","target":"task","processRef":"p","condition":"retry","isDefault":False,"properties":{}},
                    {"id":"l4","source":"correction","target":"end","processRef":"p","condition":"done","isDefault":False,"properties":{}}]
    return value

def test_loop_fr():
    assert_accepted(_loop("Vérifier document","Corriger document"),requirements(activities=[{"name":"Vérifier document"},{"name":"Corriger document"}]))

def test_loop_en():
    assert_accepted(_loop("Check document","Correct document"),requirements(activities=[{"name":"Check document"},{"name":"Correct document"}]))

def test_multi_participant_fr():
    assert_accepted(collaboration_fixture(),requirements(participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"}]))

def test_multi_participant_en():
    assert_accepted(collaboration_fixture(),requirements(participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"B","target":"A"}]))

def test_bidirectional_interaction():
    fixed=assert_accepted(collaboration_fixture(),requirements(participants=[{"name":"A"},{"name":"B"}],interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}]))
    assert len(fixed["messageFlows"])==2

def test_complex_fr():
    fixed=assert_accepted(collaboration_fixture(),requirements(participants=[{"name":"A"},{"name":"B"}],activities=[{"name":"Work","participant":"A"}],interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}]))
    assert len(fixed["processes"])==2 and len(fixed["messageFlows"])==2

def test_complex_en():
    fixed=assert_accepted(collaboration_fixture(),requirements(participants=[{"name":"A"},{"name":"B"}],activities=[{"name":"Work","participant":"A"}],interactions=[{"source":"B","target":"A"},{"source":"A","target":"B"}]))
    assert len(fixed["processes"])==2 and len(fixed["messageFlows"])==2

def _fingerprint(req):
    return {"participants":sorted(normalize_semantic_tokens(x["name"]).pop() for x in req["participants"]),
            "activity_families":sorted(analyze_activity_semantics(x)["action_family"] for x in req["activities"]),
            "decisions":sorted(analyze_activity_semantics(x)["action_family"] for x in req["decisions"]),
            "timers":sorted((x["kind"],x["value"]) for x in req["timers"]),
            "interactions":sorted((tuple(sorted(normalize_semantic_tokens(x["source"]))),tuple(sorted(normalize_semantic_tokens(x["target"])))) for x in req["interactions"])}

def test_fr_en_equivalence():
    fr=requirements(participants=[{"name":"Entreprise"}],activities=[{"name":"Vérifier document"}],decisions=[{"name":"Vérification document"}],timers=[{"kind":"duration","value":"P1D"}],interactions=[])
    en=requirements(participants=[{"name":"Company"}],activities=[{"name":"Check document"}],decisions=[{"name":"Verify document"}],timers=[{"kind":"duration","value":"P1D"}],interactions=[])
    assert _fingerprint(fr)==_fingerprint(en)
