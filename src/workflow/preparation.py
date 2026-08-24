"""Shared canonical v2 preparation used by compilation and repair."""
from __future__ import annotations
import copy
from collections.abc import Mapping
from typing import Any
from .normalizer import WorkflowNormalizer
from .semantic_validator import SemanticValidator

class CanonicalWorkflowPreparer:
    """Normalize, safely repair, schema-check and semantically validate."""
    def __init__(self,schema:dict[str,Any]):
        self.schema=copy.deepcopy(schema); self.normalizer=WorkflowNormalizer(); self.semantic=SemanticValidator()

    def prepare(self,workflow:dict[str,Any])->dict[str,Any]:
        normalized,normalization_report=self.normalizer.normalize_with_report(copy.deepcopy(workflow))
        prepared,safe_report=self.apply_safe_repairs(normalized)
        # Lazy import avoids coupling the shared preparation policy to repair orchestration.
        from .repair import _StandardSchemaValidator
        schema_errors=_StandardSchemaValidator(self.schema).validate(prepared)
        semantic_errors=self.semantic.validate(prepared)
        return {"workflow":prepared,"normalization_report":normalization_report,"safe_repair_report":safe_report,
                "schema_errors":schema_errors,"semantic_errors":semantic_errors,
                "errors":[*schema_errors,*semantic_errors]}

    @staticmethod
    def apply_safe_repairs(workflow:dict[str,Any])->tuple[dict[str,Any],list[dict[str,Any]]]:
        repaired=copy.deepcopy(workflow) if isinstance(workflow,Mapping) else {}; report=[]
        nodes=repaired.get("nodes",[]) if isinstance(repaired.get("nodes",[]),list) else []
        flows=repaired.get("flows",[]) if isinstance(repaired.get("flows",[]),list) else []
        messages=repaired.get("messageFlows",[]) if isinstance(repaired.get("messageFlows",[]),list) else []
        node_by_id={n.get("id"):n for n in nodes if isinstance(n,Mapping) and isinstance(n.get("id"),str)}
        process_ids={p.get("id") for p in repaired.get("processes",[]) if isinstance(p,Mapping)}
        parallel_ids={nid for nid,node in node_by_id.items() if node.get("type")=="parallelGateway"}
        used_ids={str(m.get("id")) for m in messages if isinstance(m,Mapping) and m.get("id") is not None}
        retained=[]; counter=1
        for raw in flows:
            if not isinstance(raw,Mapping): retained.append(raw); continue
            flow=dict(raw); source,target=flow.get("source"),flow.get("target")
            source_node,target_node=node_by_id.get(source),node_by_id.get(target)
            if source in parallel_ids and (flow.get("condition") not in (None,"") or flow.get("conditionExpression") not in (None,"")):
                flow["condition"]=None; flow.pop("conditionExpression",None)
                report.append(CanonicalWorkflowPreparer._entry("PARALLEL_GATEWAY_CONDITION_REMOVED","Parallel gateway output condition removed.",flow_id=flow.get("id")))
            if source_node is None or target_node is None:
                retained.append(flow); continue
            source_process,target_process=source_node.get("processRef"),target_node.get("processRef")
            if source_process not in process_ids or target_process not in process_ids or source_process==target_process:
                retained.append(flow); continue
            candidate=str(flow.get("id") or "")
            if not candidate or candidate in used_ids:
                while f"message_flow_{counter}" in used_ids: counter+=1
                candidate=f"message_flow_{counter}"; counter+=1
            used_ids.add(candidate)
            message={"id":candidate,"source":source,"target":target,"properties":copy.deepcopy(flow.get("properties",{})) if isinstance(flow.get("properties"),Mapping) else {}}
            if flow.get("name") is not None: message["name"]=flow["name"]
            messages.append(message)
            report.append(CanonicalWorkflowPreparer._entry("SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW","Cross-process sequence flow converted to message flow.",flow_id=candidate))
        repaired["flows"]=retained; repaired["messageFlows"]=messages
        for message in messages:
            if not isinstance(message,Mapping): continue
            source_node,target_node=node_by_id.get(message.get("source")),node_by_id.get(message.get("target"))
            if source_node and target_node and source_node.get("processRef")==target_node.get("processRef"):
                report.append(CanonicalWorkflowPreparer._entry("MESSAGE_FLOW_WITHIN_SAME_PROCESS","Message flow remains inside one process.",flow_id=message.get("id")))
        return repaired,report

    @staticmethod
    def _entry(code,message,flow_id=None,node_id=None):
        return {"code":code,"message":message,"flow_id":str(flow_id) if flow_id is not None else None,
                "node_id":str(node_id) if node_id is not None else None,"severity":"warning"}
