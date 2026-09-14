"""Shared canonical v2 preparation used by compilation and repair."""
from __future__ import annotations
import copy
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any
from .normalizer import WorkflowNormalizer
from .semantic_validator import SemanticValidator

class CanonicalWorkflowPreparer:
    """Normalize, safely repair, schema-check and semantically validate."""
    _SIMPLE_DURATION=re.compile(r"^\s*(\d+)\s*(minute|minutes|hour|hours|day|days)\s*$",re.I)

    def __init__(self,schema:dict[str,Any]):
        self.schema=copy.deepcopy(schema);self.normalizer=WorkflowNormalizer();self.semantic=SemanticValidator()

    def prepare(self,workflow:dict[str,Any])->dict[str,Any]:
        normalized,normalization_report=self.normalizer.normalize_with_report(copy.deepcopy(workflow))
        prepared,safe_report=self.apply_safe_repairs(normalized)
        from .repair import _StandardSchemaValidator
        schema_errors=_StandardSchemaValidator(self.schema).validate(prepared)
        semantic_errors=self.semantic.validate(prepared)
        return {"workflow":prepared,"normalization_report":normalization_report,"safe_repair_report":safe_report,
                "schema_errors":schema_errors,"semantic_errors":semantic_errors,"errors":[*schema_errors,*semantic_errors]}

    @classmethod
    def apply_safe_repairs(cls,workflow:dict[str,Any])->tuple[dict[str,Any],list[dict[str,Any]]]:
        """Repair representations and ownership proved by participantRef.

        Shared processes are split only if all their nodes have a valid explicit
        participantRef. IDs use ``<process-id>_<participant-id>`` and a stable
        numeric suffix if that ID already exists.
        """
        repaired=copy.deepcopy(workflow) if isinstance(workflow,Mapping) else {};report=[]
        cls._normalize_timers(repaired,report);cls._split_shared_processes(repaired,report);cls._repair_flows(repaired,report)
        return repaired,report

    @classmethod
    def _normalize_timers(cls,workflow,report):
        nodes=workflow.get("nodes",[])
        if not isinstance(nodes,list):return
        for node in nodes:
            if not isinstance(node,Mapping):continue
            properties=node.get("properties");timer=properties.get("timer") if isinstance(properties,Mapping) else None
            if not isinstance(timer,dict) or timer.get("kind")!="duration":continue
            value=timer.get("value");match=cls._SIMPLE_DURATION.fullmatch(value) if isinstance(value,str) else None
            if not match:continue
            amount,unit=int(match.group(1)),match.group(2).lower()
            normalized=f"P{amount}D" if unit.startswith("day") else f"PT{amount}{'H' if unit.startswith('hour') else 'M'}"
            timer["value"]=normalized
            report.append(cls._entry("TIMER_DURATION_NORMALIZED",f"Timer duration '{value}' normalized to '{normalized}'.",node_id=node.get("id")))

    @classmethod
    def _split_shared_processes(cls,workflow,report):
        processes=workflow.get("processes",[]);participants=workflow.get("participants",[]);nodes=workflow.get("nodes",[]);lanes=workflow.get("lanes",[])
        if not all(isinstance(x,list) for x in (processes,participants,nodes,lanes)):return
        process_by_id={p.get("id"):p for p in processes if isinstance(p,Mapping) and isinstance(p.get("id"),str)}
        counts=Counter(p.get("processRef") for p in participants if isinstance(p,Mapping) and isinstance(p.get("processRef"),str))
        used=set(process_by_id);replacements={}
        for original_id,count in counts.items():
            if count<2 or original_id not in process_by_id:continue
            members=[p for p in participants if isinstance(p,dict) and p.get("processRef")==original_id and isinstance(p.get("id"),str)]
            member_ids={p["id"] for p in members};scoped=[n for n in nodes if isinstance(n,Mapping) and n.get("processRef")==original_id]
            if not scoped or any(n.get("participantRef") not in member_ids for n in scoped):continue
            used.discard(original_id);clones=[];assignment={}
            for participant in members:
                participant_id=participant["id"];process_id=cls._unique_id(f"{original_id}_{participant_id}",used);used.add(process_id)
                clone=copy.deepcopy(dict(process_by_id[original_id]));clone["id"]=process_id;clones.append(clone)
                participant["processRef"]=process_id;assignment[participant_id]=process_id
            replacements[original_id]=clones
            for node in nodes:
                if isinstance(node,dict) and node.get("participantRef") in assignment:node["processRef"]=assignment[node["participantRef"]]
            for lane in lanes:
                if isinstance(lane,dict) and lane.get("participantRef") in assignment:lane["processRef"]=assignment[lane["participantRef"]]
            report.append(cls._entry("SHARED_PROCESS_SPLIT_BY_PARTICIPANT",f"Shared process '{original_id}' split into one process per participant."))
        if replacements:
            workflow["processes"]=[clone for process in processes for clone in replacements.get(process.get("id") if isinstance(process,Mapping) else None,[process])]

    @classmethod
    def _repair_flows(cls,workflow,report):
        nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[]);raw_messages=workflow.get("messageFlows",[])
        if not isinstance(nodes,list) or not isinstance(flows,list):return
        messages=raw_messages if isinstance(raw_messages,list) else []
        node_by_id={n.get("id"):n for n in nodes if isinstance(n,Mapping) and isinstance(n.get("id"),str)}
        parallel_ids={i for i,n in node_by_id.items() if n.get("type")=="parallelGateway"}
        deduped=[];pairs=set();used_ids=set()
        for raw in messages:
            if not isinstance(raw,Mapping):deduped.append(raw);continue
            message=dict(raw);pair=(message.get("source"),message.get("target"))
            if pair in pairs:
                report.append(cls._entry("DUPLICATE_MESSAGE_FLOW_REMOVED","Duplicate message flow removed.",flow_id=message.get("id")));continue
            pairs.add(pair);identifier=str(message.get("id") or "")
            if not identifier or identifier in used_ids:identifier=cls._unique_id("message_flow_1",used_ids);message["id"]=identifier
            used_ids.add(identifier);deduped.append(message)
        retained=[]
        for raw in flows:
            if not isinstance(raw,Mapping):retained.append(raw);continue
            flow=dict(raw);source,target=flow.get("source"),flow.get("target");source_node,target_node=node_by_id.get(source),node_by_id.get(target)
            if source in parallel_ids and (flow.get("condition") not in (None,"") or flow.get("conditionExpression") not in (None,"")):
                flow["condition"]=None;flow.pop("conditionExpression",None)
                report.append(cls._entry("PARALLEL_GATEWAY_CONDITION_REMOVED","Parallel gateway output condition removed.",flow_id=flow.get("id")))
            if source_node is None or target_node is None:retained.append(flow);continue
            source_process,target_process=source_node.get("processRef"),target_node.get("processRef")
            if source_process==target_process:flow["processRef"]=source_process;retained.append(flow);continue
            pair=(source,target)
            if pair not in pairs:
                identifier=str(flow.get("id") or "")
                if not identifier or identifier in used_ids:identifier=cls._unique_id("message_flow_1",used_ids)
                used_ids.add(identifier);message={"id":identifier,"source":source,"target":target,"properties":copy.deepcopy(flow.get("properties",{})) if isinstance(flow.get("properties"),Mapping) else {}}
                if flow.get("name") is not None:message["name"]=flow["name"]
                deduped.append(message);pairs.add(pair)
            report.append(cls._entry("SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW","Cross-process sequence flow converted to message flow.",flow_id=flow.get("id")))
        workflow["flows"]=retained;workflow["messageFlows"]=deduped
        for message in deduped:
            if not isinstance(message,Mapping):continue
            source_node=node_by_id.get(message.get("source"));target_node=node_by_id.get(message.get("target"))
            if source_node and target_node and source_node.get("processRef")==target_node.get("processRef"):
                report.append(cls._entry("MESSAGE_FLOW_WITHIN_SAME_PROCESS","Message flow remains inside one process.",flow_id=message.get("id")))

    @staticmethod
    def _unique_id(base,used):
        if base not in used:return base
        counter=1
        while f"{base}_{counter}" in used:counter+=1
        return f"{base}_{counter}"

    @staticmethod
    def _entry(code,message,flow_id=None,node_id=None):
        return {"code":code,"message":message,"flow_id":str(flow_id) if flow_id is not None else None,"node_id":str(node_id) if node_id is not None else None,"severity":"warning"}
