import copy
import pytest
from src.workflow import WorkflowNormalizer

N=WorkflowNormalizer()
def v1(**changes):
    value={"schemaVersion":"1.0.0","process":{"id":"P","name":"P"},"participants":[],"nodes":[{"id":"Start","type":"startEvent","name":"S","actor":None},{"id":"End","type":"endEvent","name":"E","actor":None}],"flows":[{"id":"F","source":"Start","target":"End"}],"ambiguities":[]};value.update(changes);return value
def test_v1_migrates_to_v2():
    result=N.normalize(v1());assert result["schemaVersion"]=="2.0" and result["processes"][0]["id"]=="p"
def test_v2_stays_canonical():
    result=N.normalize(N.normalize(v1()));assert result["schemaVersion"]=="2.0" and "process" not in result
def test_unknown_version_preserved():
    result,report=N.normalize_with_report(v1(schemaVersion="9.0"));assert result["schemaVersion"]=="9.0" and "UNSUPPORTED_SCHEMA_VERSION" in {x["code"] for x in report}
def test_actor_remains_business_data():
    raw=v1(nodes=[{"id":"A","type":"task","actor":"Team Manager!"}]);assert N.normalize(raw)["nodes"][0]["actor"]=="Team Manager!"
def test_v1_membership_migrates_to_explicit_refs():
    raw=v1(participants=[{"id":"pa","name":"P","nodeRefs":["Start","End"],"lanes":[{"id":"la","name":"L","nodeRefs":["Start","End"]}]}]);result=N.normalize(raw);assert all(x["participantRef"]=="pa" and x["laneRef"]=="la" for x in result["nodes"])
def test_canonical_participant_has_no_node_refs():
    raw=v1(participants=[{"id":"pa","name":"P","nodeRefs":["Start"]}]);assert "nodeRefs" not in N.normalize(raw)["participants"][0]
def test_canonical_lane_is_top_level_without_node_refs():
    raw=v1(participants=[{"id":"pa","name":"P","lanes":[{"id":"la","name":"L","nodeRefs":["Start"]}]}]);result=N.normalize(raw);assert result["lanes"][0]["participantRef"]=="pa" and "nodeRefs" not in result["lanes"][0]
def test_message_flow_has_no_redundant_type():
    raw=v1(messageFlows=[{"id":"m","source":"Start","target":"End","type":"messageFlow"}]);assert "type" not in N.normalize(raw)["messageFlows"][0]
def test_normalizer_does_not_remove_parallel_condition():
    raw=v1(nodes=[{"id":"g","type":"parallelGateway"},{"id":"e","type":"endEvent"}],flows=[{"id":"f","source":"g","target":"e","condition":"x"}]);assert N.normalize(raw)["flows"][0]["condition"]=="x"
@pytest.mark.parametrize("raw,iso",[("5 minutes","PT5M"),("2 hours","PT2H"),("3 days","P3D")])
def test_legacy_timer_migrates_to_properties_timer(raw,iso):
    result=N.normalize(v1(nodes=[{"id":"timer","type":"timerEvent","value":raw}]));timer=result["nodes"][0];assert timer["type"]=="intermediateCatchEvent" and timer["properties"]["timer"]=={"kind":"duration","value":iso}
def test_canonical_timer_preserved():
    raw=N.normalize(v1());raw["nodes"][0]["type"]="intermediateCatchEvent";raw["nodes"][0]["properties"]={"timer":{"kind":"cycle","value":"R/PT1H"}};assert N.normalize(raw)["nodes"][0]["properties"]["timer"]["value"]=="R/PT1H"
def test_source_ref_only_migrated_at_v1_boundary():
    raw=v1(flows=[{"id":"f","sourceRef":"Start","targetRef":"End"}]);flow=N.normalize(raw)["flows"][0];assert flow["source"]=="start" and flow["target"]=="end" and "sourceRef" not in flow
def test_immutability():
    raw=v1();before=copy.deepcopy(raw);N.normalize(raw);assert raw==before
