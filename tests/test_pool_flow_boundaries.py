from src.generation.pipeline import WorkflowCompiler
from src.workflow import CanonicalWorkflowPreparer,SemanticValidator,WorkflowGraphAnalyzer
import json
from pathlib import Path

SCHEMA=json.loads((Path(__file__).parents[1]/"schemas"/"workflow.schema.json").read_text(encoding="utf-8"))
def workflow():
    return {"schemaVersion":"2.0","processes":[{"id":p,"name":p,"description":"","executable":False,"properties":{}} for p in ("client","company")],
      "participants":[{"id":"pc","name":"Client","processRef":"client","properties":{}},{"id":"po","name":"Company","processRef":"company","properties":{}}],
      "lanes":[],"nodes":[{"id":"cs","type":"startEvent","name":"S","processRef":"client","participantRef":"pc","laneRef":None,"actor":None,"properties":{}},{"id":"ce","type":"endEvent","name":"E","processRef":"client","participantRef":"pc","laneRef":None,"actor":None,"properties":{}},{"id":"os","type":"startEvent","name":"S","processRef":"company","participantRef":"po","laneRef":None,"actor":None,"properties":{}},{"id":"oe","type":"endEvent","name":"E","processRef":"company","participantRef":"po","laneRef":None,"actor":None,"properties":{}}],
      "flows":[{"id":"cf","source":"cs","target":"ce","processRef":"client","condition":None,"isDefault":False,"properties":{}},{"id":"of","source":"os","target":"oe","processRef":"company","condition":None,"isDefault":False,"properties":{}}],"messageFlows":[],"ambiguities":[]}
def test_cross_process_sequence_converted():
    w=workflow();w["flows"].append({"id":"cross","source":"cs","target":"os","processRef":"client","condition":"x","isDefault":True,"properties":{"k":"v"}});fixed,report=CanonicalWorkflowPreparer.apply_safe_repairs(w);assert "cross" in {m["id"] for m in fixed["messageFlows"]} and "SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW" in {r["code"] for r in report}
def test_conversion_preserves_endpoints_and_useful_properties():
    w=workflow();w["flows"].append({"id":"cross","source":"cs","target":"os","processRef":"client","condition":"x","isDefault":True,"properties":{"k":"v"}});fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(w);m=fixed["messageFlows"][0];assert (m["source"],m["target"],m["properties"])==("cs","os",{"k":"v"}) and "condition" not in m
def test_duplicate_message_id_gets_deterministic_unique_id():
    w=workflow();w["messageFlows"]=[{"id":"cross","source":"cs","target":"os","properties":{}}];w["flows"].append({"id":"cross","source":"ce","target":"oe","processRef":"client","condition":None,"isDefault":False,"properties":{}});fixed,_=CanonicalWorkflowPreparer.apply_safe_repairs(w);assert [m["id"] for m in fixed["messageFlows"]]==["cross","message_flow_1"]
def test_same_process_message_is_not_repaired():
    w=workflow();w["messageFlows"]=[{"id":"local","source":"cs","target":"ce","properties":{}}];fixed,report=CanonicalWorkflowPreparer.apply_safe_repairs(w);assert fixed["messageFlows"][0]["id"]=="local" and "MESSAGE_FLOW_WITHIN_SAME_PROCESS" in {r["code"] for r in report}
def test_validator_rejects_same_process_message():
    w=workflow();w["messageFlows"]=[{"id":"local","source":"cs","target":"ce","properties":{}}];assert "MESSAGE_FLOW_WITHIN_SAME_PROCESS" in {e["code"] for e in SemanticValidator().validate(w)}
def test_validator_rejects_cross_process_sequence():
    w=workflow();w["flows"].append({"id":"cross","source":"cs","target":"os","processRef":"client","condition":None,"isDefault":False,"properties":{}});assert "SEQUENCE_FLOW_CROSSES_PROCESSES" in {e["code"] for e in SemanticValidator().validate(w)}
def test_analyzer_message_does_not_change_levels():
    w=workflow();w["messageFlows"]=[{"id":"m","source":"ce","target":"os","properties":{}}];analysis=WorkflowGraphAnalyzer().analyze(w);assert analysis["processes"]["client"]["levels"]["cs"]==0 and analysis["processes"]["company"]["levels"]["os"]==0
def test_participant_identity_does_not_define_control_boundary():
    w=workflow();w["nodes"][0]["participantRef"]="po";codes={e["code"] for e in SemanticValidator().validate(w)};assert "SEQUENCE_FLOW_CROSSES_PROCESSES" not in codes
