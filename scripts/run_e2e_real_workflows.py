"""Run the real Qwen-to-BPMN pipeline against diverse end-to-end fixtures."""
from __future__ import annotations
import argparse,copy,json,sys,traceback,xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from src.bpmn.constants import BPMN_NS,BPMNDI_NS,DC_NS
from src.bpmn.routing import calculate_routes,routing_metrics
from src.generation import QwenWorkflowGenerator,WorkflowCompiler
from src.generation.model_loader import DEFAULT_QWEN_MODEL,load_qwen_model

SCENARIOS={
 "linear_simple":"Un employé reçoit une demande, vérifie les informations, enregistre la demande puis clôture le dossier. Le processus possède un début et une fin.",
 "exclusive_decision":"Un analyste reçoit une demande puis décide si elle est complète. Si elle est complète, il l'approuve. Sinon, il demande une correction. Les deux branches convergent avant la clôture.",
 "parallel_work":"Après réception d'un dossier, trois contrôles sont lancés en parallèle : identité, conformité et solvabilité. Le processus attend la fin des trois contrôles, consolide les résultats puis se termine.",
 "explicit_timer":"Un agent envoie une demande de pièce, attend explicitement deux jours (durée ISO P2D), puis vérifie la réponse et clôture le dossier. L'attente est un événement intermédiaire timer.",
 "loop_with_return":"Un réviseur contrôle un document. Si le document contient des erreurs, il le renvoie pour correction puis le contrôle à nouveau. La boucle continue jusqu'à validation, puis le processus se termine.",
 "three_participants":"Un client envoie une commande au vendeur. Le vendeur demande une autorisation à la banque. La banque répond au vendeur, puis le vendeur confirme ou refuse la commande au client. Client, vendeur et banque sont trois participants distincts.",
 "multiple_lanes":"Dans l'entreprise, la lane Accueil reçoit le dossier, la lane Analyse vérifie son contenu et la lane Direction approuve la décision avant que l'Accueil informe le demandeur.",
 "collaboration_decision_timer":"Un client transmet une demande à une entreprise. Le service Instruction contrôle le dossier. S'il manque une pièce, l'entreprise informe le client, attend trois jours avec un timer P3D puis réévalue le dossier. Sinon la Direction accepte ou refuse et l'entreprise notifie le client.",
 "large_workflow":"Un dossier démarre puis passe successivement par vingt activités distinctes : réception, indexation, contrôle de format, contrôle d'identité, contrôle d'adresse, contrôle de signature, analyse financière, analyse juridique, analyse de risque, consultation interne, collecte d'avis, consolidation, revue qualité, demande de validation, validation, préparation de décision, notification, archivage, mise à jour du registre et clôture. Toutes sont représentées dans l'ordre.",
 "intentional_ambiguity":"Une équipe reçoit une demande et effectue un contrôle. Une personne compétente intervient éventuellement si nécessaire, mais son rôle exact et le délai applicable ne sont pas précisés. Le dossier est ensuite clôturé. Conserver ces informations incomplètes comme ambiguïtés non bloquantes."
}

class ObservedGenerator:
    def __init__(self,delegate):self.delegate=delegate;self.prompt_lengths=[]
    @property
    def last_generation_metadata(self):return self.delegate.last_generation_metadata
    def generate_json(self,prompt,max_new_tokens=None):
        self.prompt_lengths.append(len(prompt))
        return self.delegate.generate_json(prompt,max_new_tokens=max_new_tokens)

def blocking(items):return [x for x in items or [] if x.get("severity","error")=="error"]
def counts(workflow):
    return {key:len(workflow.get(key,[])) if isinstance(workflow.get(key),list) else 0 for key in ("processes","participants","lanes","nodes","flows","messageFlows")}
def failure_stage(result):
    raw=str((result.get("error") or {}).get("stage") or "GENERATION").upper()
    mapping={"REQUIREMENTS_EXTRACTION":"GENERATION","SCHEMA_VALIDATION":"STRUCTURAL","REPAIR":"STRUCTURAL",
        "COVERAGE_REPAIR":"COVERAGE","ANALYSIS":"GRAPH","BPMN":"BPMN"}
    return mapping.get(raw,raw)

def validate_xml(result):
    issues=[];xml=result.get("bpmn_xml");workflow=result.get("normalized_workflow") or {}
    try:root=ET.fromstring(xml)
    except Exception as exc:return [f"XML mal formé: {exc}"]
    ns={"bpmn":BPMN_NS,"bpmndi":BPMNDI_NS,"dc":DC_NS};ids={element.get("id") for element in root.iter() if element.get("id")}
    for element in root.iter():
        for attr in ("processRef","sourceRef","targetRef","bpmnElement"):
            ref=element.get(attr)
            if ref and ref not in ids:issues.append(f"Référence {attr} inconnue: {ref}")
    shapes={x.get("bpmnElement") for x in root.findall(".//bpmndi:BPMNShape",ns)}
    edges={x.get("bpmnElement") for x in root.findall(".//bpmndi:BPMNEdge",ns)}
    expected_shapes={x["id"] for key in ("nodes","participants","lanes") for x in workflow.get(key,[]) if isinstance(x,dict) and x.get("id")}
    expected_edges={x["id"] for key in ("flows","messageFlows") for x in workflow.get(key,[]) if isinstance(x,dict) and x.get("id")}
    if missing:=sorted(expected_shapes-shapes):issues.append(f"BPMNShape manquants: {missing}")
    if missing:=sorted(expected_edges-edges):issues.append(f"BPMNEdge manquants: {missing}")
    return issues

def collaboration_issues(workflow):
    issues=[];node_process={n.get("id"):n.get("processRef") for n in workflow.get("nodes",[]) if isinstance(n,dict)}
    participant_process={p.get("id"):p.get("processRef") for p in workflow.get("participants",[]) if isinstance(p,dict)}
    lane_process={lane.get("id"):lane.get("processRef") for lane in workflow.get("lanes",[]) if isinstance(lane,dict)}
    for node in workflow.get("nodes",[]):
        if node.get("participantRef") and participant_process.get(node["participantRef"])!=node.get("processRef"):issues.append("NODE_PARTICIPANT_PROCESS_MISMATCH")
        if node.get("laneRef") and lane_process.get(node["laneRef"])!=node.get("processRef"):issues.append("NODE_LANE_PROCESS_MISMATCH")
    for flow in workflow.get("flows",[]):
        if node_process.get(flow.get("source"))!=flow.get("processRef") or node_process.get(flow.get("target"))!=flow.get("processRef"):issues.append("SEQUENCE_FLOW_CROSSES_PROCESS")
    for flow in workflow.get("messageFlows",[]):
        if node_process.get(flow.get("source"))==node_process.get(flow.get("target")):issues.append("MESSAGE_FLOW_WITHIN_SAME_PROCESS")
    return sorted(set(issues))

def edge_metrics(result):
    workflow=result.get("normalized_workflow") or {};layout=result.get("layout") or {};geometry={**layout.get("participants",{}),**layout.get("nodes",{})}
    flows=workflow.get("flows",[]);messages=workflow.get("messageFlows",[])
    if not geometry:return {"edge_shape_collisions":None,"edge_count":0}
    routes=calculate_routes(flows,messages,geometry,layout);endpoints={str(f["id"]):(str(f["source"]),str(f["target"])) for f in [*flows,*messages]}
    return routing_metrics(routes,geometry,endpoints)

def compact_report(name,result,prompt_lengths,extra_issues=None):
    workflow=result.get("normalized_workflow") or result.get("raw_workflow") or {};error=result.get("error") or {}
    issues=list(extra_issues or []);issues.extend(validate_xml(result) if result.get("bpmn_xml") else [])
    issues.extend(collaboration_issues(workflow));schema=blocking(result.get("schema_errors"));semantic=blocking(result.get("semantic_errors"));coverage=blocking(result.get("coverage_errors"))
    layout_metrics=(result.get("layout") or {}).get("metadata",{}).get("layout_metrics",{})
    route_metrics=edge_metrics(result) if result.get("layout") and workflow else {"edge_shape_collisions":None,"edge_count":0}
    success=bool(result.get("success") and not schema and not semantic and not coverage and not issues and result.get("bpmn_xml"))
    return {"name":name,"success":success,"stage":"COMPLETE" if success else failure_stage(result),"failure_message":None if success else error.get("message"),
        "workflow_counts":counts(workflow),"generation_stats":copy.deepcopy(result.get("generation_stats",{})),"pipeline_debug":copy.deepcopy(result.get("pipeline_debug",{})),
        "semantic_error_count":len(semantic),"coverage_error_count":len(coverage),"schema_error_count":len(schema),
        "repair_attempts":len(result.get("repair_report",[])),"bpmn_generated":bool(result.get("bpmn_xml")),
        "layout_metrics":layout_metrics,"routing_metrics":route_metrics,"prompt_lengths":prompt_lengths,
        "ambiguity_count":len((result.get("semantic_requirements") or {}).get("ambiguities",[])),"validation_issues":issues,
        "generator_exception":error.get("message") if "GENERATOR" in str(error.get("message","")).upper() else None}

def save_scenario(base,name,result,report):
    destination=base/name;destination.mkdir(parents=True,exist_ok=True);workflow=result.get("normalized_workflow") or result.get("raw_workflow") or {}
    workflow_name="workflow.json" if report["success"] else "workflow_last_good.json"
    (destination/workflow_name).write_text(json.dumps(workflow,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    if report["success"]:(destination/"workflow.bpmn").write_text(result["bpmn_xml"],encoding="utf-8")
    (destination/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")

def print_table(reports):
    headers=("Scenario","Success","Failure Stage","Qwen Calls","Nodes","Processes","Participants","Semantic Errors","Coverage Errors","BPMN")
    rows=[headers]+[(r["name"],str(r["success"]),"-" if r["success"] else r["stage"],str(r["generation_stats"].get("total_qwen_calls",0)),
        str(r["workflow_counts"]["nodes"]),str(r["workflow_counts"]["processes"]),str(r["workflow_counts"]["participants"]),
        str(r["semantic_error_count"]),str(r["coverage_error_count"]),str(r["bpmn_generated"])) for r in reports]
    widths=[max(len(row[i]) for row in rows) for i in range(len(headers))]
    for index,row in enumerate(rows):print(" | ".join(value.ljust(widths[i]) for i,value in enumerate(row)));index==0 and print("-+-".join("-"*width for width in widths))

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--model",default=DEFAULT_QWEN_MODEL);parser.add_argument("--load-in-4bit",action="store_true")
    parser.add_argument("--output",default=str(ROOT/"outputs"/"e2e"));parser.add_argument("--scenario",action="append",choices=sorted(SCENARIOS));args=parser.parse_args(argv)
    selected=args.scenario or list(SCENARIOS);base=Path(args.output);base.mkdir(parents=True,exist_ok=True);reports=[]
    try:tokenizer,model=load_qwen_model(args.model,args.load_in_4bit);observed=ObservedGenerator(QwenWorkflowGenerator(tokenizer,model))
    except Exception as exc:
        for name in selected:
            result={"success":False,"error":{"stage":"generation","message":str(exc)},"generation_stats":{"initial_generation_calls":0,"structural_repair_calls":0,"coverage_repair_calls":0,"semantic_requirement_calls":0,"total_qwen_calls":0},"pipeline_debug":{},"schema_errors":[],"semantic_errors":[],"coverage_errors":[],"repair_report":[],"normalized_workflow":{},"raw_workflow":{},"layout":{},"bpmn_xml":None,"semantic_requirements":{}}
            report=compact_report(name,result,[],["MODEL_LOAD_FAILED"]);save_scenario(base,name,result,report);reports.append(report)
        print_table(reports);(base/"summary.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8");return 1
    for name in selected:
        start=len(observed.prompt_lengths)
        try:
            compiler=WorkflowCompiler(observed,ROOT/"schemas"/"workflow.schema.json",max_repair_attempts=2,max_total_generation_calls=4)
            result=compiler.compile(SCENARIOS[name]);report=compact_report(name,result,observed.prompt_lengths[start:]);save_scenario(base,name,result,report)
        except Exception as exc:
            result={"success":False,"error":{"stage":"generation","message":str(exc)},"generation_stats":{},"pipeline_debug":{},"schema_errors":[],"semantic_errors":[],"coverage_errors":[],"repair_report":[],"normalized_workflow":{},"raw_workflow":{},"layout":{},"bpmn_xml":None,"semantic_requirements":{}}
            report=compact_report(name,result,observed.prompt_lengths[start:],[traceback.format_exc(limit=3)]);save_scenario(base,name,result,report)
        reports.append(report)
    print_table(reports);(base/"summary.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    return 0 if all(r["success"] for r in reports) else 1

if __name__=="__main__":raise SystemExit(main())
