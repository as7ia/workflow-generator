"""Shared canonical v2 preparation used by compilation and repair."""
from __future__ import annotations
import copy
import re
import unicodedata
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

    def prepare_and_validate(self,workflow:dict[str,Any])->dict[str,Any]:
        """Run the sole normalize -> safe repair -> schema -> semantic chain."""
        normalized,normalization_report=self.normalizer.normalize_with_report(copy.deepcopy(workflow))
        return self.prepare_normalized_and_validate(normalized,normalization_report)

    def prepare_normalized_and_validate(self,workflow:dict[str,Any],normalization_report=None)->dict[str,Any]:
        """Apply safe repairs and validations to an already canonical workflow."""
        prepared,safe_report=self.apply_safe_repairs(workflow)
        return self._validate_prepared(prepared,normalization_report or [],safe_report)

    def validate_prepared(self,workflow:dict[str,Any])->dict[str,Any]:
        """Validate a workflow already normalized and safely repaired."""
        prepared=copy.deepcopy(workflow) if isinstance(workflow,Mapping) else {}
        return self._validate_prepared(prepared,[],[])

    def _validate_prepared(self,prepared,normalization_report,safe_report):
        from .repair import _StandardSchemaValidator
        schema_errors=_StandardSchemaValidator(self.schema).validate(prepared)
        semantic_errors=self.semantic.validate(prepared)
        return {"workflow":prepared,"normalization_report":normalization_report,"safe_repair_report":safe_report,
                "schema_errors":schema_errors,"semantic_errors":semantic_errors,"errors":[*schema_errors,*semantic_errors]}

    def prepare(self,workflow:dict[str,Any])->dict[str,Any]:
        """Backward-compatible alias for :meth:`prepare_and_validate`."""
        return self.prepare_and_validate(workflow)

    @classmethod
    def apply_safe_repairs(cls,workflow:dict[str,Any])->tuple[dict[str,Any],list[dict[str,Any]]]:
        """Repair representations and ownership proved by participantRef.

        Shared processes are split only if all their nodes have a valid explicit
        participantRef. IDs use ``<process-id>_<participant-id>`` and a stable
        numeric suffix if that ID already exists.
        """
        repaired=copy.deepcopy(workflow) if isinstance(workflow,Mapping) else {};report=[]
        cls._split_shared_processes(repaired,report)
        cls._repair_flows(repaired,report)
        cls._normalize_message_tasks(repaired,report)
        cls._normalize_timers(repaired,report)
        cls._insert_unambiguous_isolated_timers(repaired,report)
        cls.finalize_collaboration_process_structure(repaired,report)
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
    def _insert_unambiguous_isolated_timers(cls,workflow,report):
        """Insert an isolated timer only into one uniquely matching local flow."""
        nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[])
        if not isinstance(nodes,list) or not isinstance(flows,list):return
        node_by_id={n.get("id"):n for n in nodes if isinstance(n,Mapping) and isinstance(n.get("id"),str)}
        used_flow_ids={f.get("id") for f in flows if isinstance(f,Mapping)}
        for timer in nodes:
            if not isinstance(timer,Mapping) or timer.get("type")!="intermediateCatchEvent":continue
            properties=timer.get("properties");definition=properties.get("timer") if isinstance(properties,Mapping) else None
            if not isinstance(definition,Mapping):continue
            timer_id=timer.get("id");process_id=timer.get("processRef")
            if any(isinstance(f,Mapping) and timer_id in (f.get("source"),f.get("target")) for f in flows):continue
            timer_tokens=cls._semantic_tokens(timer.get("name"),timer_id)
            if not timer_tokens:continue
            named=[];inferred=[]
            for flow in flows:
                if not isinstance(flow,dict) or flow.get("processRef")!=process_id:continue
                source=node_by_id.get(flow.get("source"));target=node_by_id.get(flow.get("target"))
                if source is None or target is None:continue
                flow_tokens=cls._semantic_tokens(flow.get("name"))
                endpoint_tokens=cls._semantic_tokens(source.get("name"),source.get("id"),target.get("name"),target.get("id"))
                if timer_tokens & flow_tokens:named.append(flow)
                elif not flow_tokens and timer_tokens & endpoint_tokens:inferred.append(flow)
            local=named if named else inferred
            if len(local)!=1:continue
            direct=local[0];successor=direct["target"];direct["target"]=timer_id
            after_id=cls._unique_id(f"{direct.get('id')}_after_timer",used_flow_ids);used_flow_ids.add(after_id)
            flows.append({"id":after_id,"source":timer_id,"target":successor,"processRef":process_id,
                          "condition":None,"isDefault":False,"properties":{}})
            report.append(cls._entry("ISOLATED_TIMER_INSERTED","Isolated timer inserted into its unique matching sequence flow.",node_id=timer_id))

    @staticmethod
    def _semantic_tokens(*values):
        ignored={"node","timer","attente","attendre","waiting","wait","delay","event","flow","pour","apres","avant","the","and"}
        text=" ".join(str(value or "") for value in values)
        text=unicodedata.normalize("NFKD",text).encode("ascii","ignore").decode("ascii").lower()
        return {token for token in re.findall(r"[a-z0-9]+",text) if len(token)>=4 and token not in ignored and not token.isdigit()}

    @classmethod
    def _split_shared_processes(cls,workflow,report):
        processes=workflow.get("processes",[]);participants=workflow.get("participants",[]);nodes=workflow.get("nodes",[]);lanes=workflow.get("lanes",[])
        if not all(isinstance(x,list) for x in (processes,participants,nodes,lanes)):return set()
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
        return {clone["id"] for clones in replacements.values() for clone in clones}

    @classmethod
    def _repair_flows(cls,workflow,report):
        nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[]);raw_messages=workflow.get("messageFlows",[])
        if not isinstance(nodes,list) or not isinstance(flows,list):return set(),set()
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
        retained=[];converted_sources=set();converted_targets=set()
        for raw in flows:
            if not isinstance(raw,Mapping):retained.append(raw);continue
            flow=dict(raw);source,target=flow.get("source"),flow.get("target");source_node,target_node=node_by_id.get(source),node_by_id.get(target)
            if source in parallel_ids and (flow.get("condition") not in (None,"") or flow.get("conditionExpression") not in (None,"")):
                flow["condition"]=None;flow.pop("conditionExpression",None)
                report.append(cls._entry("PARALLEL_GATEWAY_CONDITION_REMOVED","Parallel gateway output condition removed.",flow_id=flow.get("id")))
            if source_node is None or target_node is None:retained.append(flow);continue
            source_process,target_process=source_node.get("processRef"),target_node.get("processRef")
            if source_process==target_process:flow["processRef"]=source_process;retained.append(flow);continue
            converted_sources.add(source);converted_targets.add(target)
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
        return converted_sources,converted_targets

    @classmethod
    def _normalize_message_tasks(cls,workflow,report):
        nodes=workflow.get("nodes",[]);messages=workflow.get("messageFlows",[])
        if not isinstance(nodes,list) or not isinstance(messages,list):return
        sources={m.get("source") for m in messages if isinstance(m,Mapping)}
        targets={m.get("target") for m in messages if isinstance(m,Mapping)}
        for node in nodes:
            if not isinstance(node,dict) or str(node.get("type","")).replace("_","").lower()!="messagetask":continue
            node_id=node.get("id");is_source=node_id in sources;is_target=node_id in targets
            if is_source and not is_target:
                node["type"]="sendTask";code="MESSAGE_TASK_NORMALIZED_TO_SEND_TASK"
            elif is_target and not is_source:
                node["type"]="receiveTask";code="MESSAGE_TASK_NORMALIZED_TO_RECEIVE_TASK"
            else:
                node["type"]="serviceTask";code="AMBIGUOUS_MESSAGE_TASK_NORMALIZED"
            report.append(cls._entry(code,f"messageTask '{node_id}' normalized to {node['type']}.",node_id=node_id))

    @classmethod
    def finalize_collaboration_process_structure(cls,workflow,report=None):
        """Complete locally unambiguous process chains around message endpoints.

        Message flows express collaboration causality, but never contribute a
        sequence-flow edge to a process graph.  Generated collaborations often
        use a cross-process sequence flow for that causality; after conversion
        its two local endpoints would otherwise be left disconnected.  This
        pass restores only edges proved by a unique local root/sink or by a
        unique causal path through message flows.
        """
        if report is None:report=[]
        nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[]);messages=workflow.get("messageFlows",[])
        participants=workflow.get("participants",[])
        processes=workflow.get("processes",[])
        if not all(isinstance(value,list) for value in (nodes,flows,messages,processes)):return workflow
        initial_flow_count=len(flows)
        used_node_ids={n.get("id") for n in nodes if isinstance(n,Mapping)}
        used_flow_ids={f.get("id") for f in flows if isinstance(f,Mapping)}
        message_endpoints={endpoint for m in messages if isinstance(m,Mapping) for endpoint in (m.get("source"),m.get("target"))}
        message_sources={m.get("source") for m in messages if isinstance(m,Mapping)}
        message_targets={m.get("target") for m in messages if isinstance(m,Mapping)}
        participant_by_process={p.get("processRef"):p.get("id") for p in participants if isinstance(p,Mapping)}

        process_ids=sorted(p.get("id") for p in processes if isinstance(p,Mapping) and isinstance(p.get("id"),str))
        for process_id in process_ids:
            local_nodes=[n for n in nodes if isinstance(n,dict) and n.get("processRef")==process_id]
            if not local_nodes:continue
            local_ids={n.get("id") for n in local_nodes}
            if not (local_ids & message_endpoints):continue
            local_flows=[f for f in flows if isinstance(f,Mapping) and f.get("source") in local_ids and f.get("target") in local_ids]
            incoming={node_id:0 for node_id in local_ids};outgoing={node_id:0 for node_id in local_ids}
            for flow in local_flows:
                outgoing[flow.get("source")]+=1;incoming[flow.get("target")]+=1
            isolated_message_ids={node_id for node_id in local_ids if node_id in message_endpoints and incoming[node_id]==0 and outgoing[node_id]==0}
            starts=sorted((n for n in local_nodes if n.get("type")=="startEvent"),key=lambda n:str(n.get("id")))
            ends=sorted((n for n in local_nodes if n.get("type")=="endEvent"),key=lambda n:str(n.get("id")))

            if not starts:
                start_id=cls._unique_id(f"start_{process_id}",used_node_ids);used_node_ids.add(start_id)
                start=cls._technical_event(start_id,"startEvent",process_id,participant_by_process.get(process_id));nodes.append(start);starts=[start]
                candidates=[n for n in local_nodes if incoming.get(n.get("id"),0)==0 and
                            (outgoing.get(n.get("id"),0)>0 or n.get("id") in message_endpoints)]
                roots=candidates if len(candidates)==1 else []
            else:
                all_roots=[n for n in local_nodes if n.get("type")!="startEvent" and incoming.get(n.get("id"),0)==0]
                eligible=[n for n in all_roots if n.get("id") in message_targets or n.get("id") in isolated_message_ids]
                start_has_outgoing=outgoing.get(starts[0]["id"],0)>0
                roots=[] if start_has_outgoing else (eligible if len(eligible)==1 else (all_roots if len(all_roots)==1 else []))
            for root in roots:
                cls._add_technical_flow(flows,used_flow_ids,starts[0]["id"],root["id"],process_id,report,"START_FLOW_ADDED")
                incoming[root["id"]]=incoming.get(root["id"],0)+1

            if not ends:
                end_id=cls._unique_id(f"end_{process_id}",used_node_ids);used_node_ids.add(end_id)
                end=cls._technical_event(end_id,"endEvent",process_id,participant_by_process.get(process_id));nodes.append(end);ends=[end]
                candidates=[n for n in local_nodes if outgoing.get(n.get("id"),0)==0 and
                            (incoming.get(n.get("id"),0)>0 or n.get("id") in message_endpoints)]
                sinks=candidates if len(candidates)==1 else []
                if not roots and len(local_nodes)==1 and local_nodes[0].get("type")=="startEvent":sinks=local_nodes
            else:
                all_sinks=[n for n in local_nodes if n.get("type")!="endEvent" and outgoing.get(n.get("id"),0)==0]
                eligible=[n for n in all_sinks if n.get("id") in message_sources or n.get("id") in isolated_message_ids]
                end_has_incoming=incoming.get(ends[0]["id"],0)>0
                sinks=[] if end_has_incoming else (eligible if len(eligible)==1 else (all_sinks if len(all_sinks)==1 else []))
            for sink in sinks:
                cls._add_technical_flow(flows,used_flow_ids,sink["id"],ends[0]["id"],process_id,report,"END_FLOW_ADDED")

        # Stitch response chains whose order is already proved by collaboration
        # causality (source -> message target -> local work -> message source ->
        # target). Recompute degrees after every edge so each choice stays unique.
        while True:
            node_by_id={n.get("id"):n for n in nodes if isinstance(n,Mapping)}
            combined={node_id:[] for node_id in node_by_id}
            for edge in [*flows,*messages]:
                if not isinstance(edge,Mapping):continue
                source,target=edge.get("source"),edge.get("target")
                if source in combined and target in combined:combined[source].append(target)
            added=False
            for process_id in process_ids:
                local_ids={node_id for node_id,node in node_by_id.items() if node.get("processRef")==process_id}
                incoming={node_id:0 for node_id in local_ids};outgoing={node_id:0 for node_id in local_ids}
                for edge in flows:
                    if not isinstance(edge,Mapping):continue
                    source,target=edge.get("source"),edge.get("target")
                    if source in local_ids and target in local_ids:
                        outgoing[source]+=1;incoming[target]+=1
                sinks=sorted(node_id for node_id in local_ids if node_id in message_sources and outgoing[node_id]==0)
                roots={node_id for node_id in local_ids if node_id in message_targets and incoming[node_id]==0}
                proposals=[]
                for sink in sinks:
                    distances=cls._causal_distances(sink,combined)
                    candidates=[(distances[root],root) for root in roots if root in distances and root!=sink]
                    if not candidates:continue
                    minimum=min(distance for distance,_ in candidates)
                    nearest=sorted(root for distance,root in candidates if distance==minimum)
                    if len(nearest)==1:proposals.append((sink,nearest[0]))
                # A target claimed by several equally valid local sinks is still
                # ambiguous; leave it for semantic/model repair.
                target_counts=Counter(target for _,target in proposals)
                unambiguous=[pair for pair in proposals if target_counts[pair[1]]==1]
                if unambiguous:
                    source,target=sorted(unambiguous)[0]
                    cls._add_technical_flow(flows,used_flow_ids,source,target,process_id,report,"COLLABORATION_CONTINUITY_FLOW_ADDED")
                    added=True;break
            if not added:break
        if len(flows)>initial_flow_count:
            return cls.finalize_collaboration_process_structure(workflow,report)
        return workflow

    @staticmethod
    def _causal_distances(source,adjacency):
        distances={source:0};queue=[source]
        while queue:
            current=queue.pop(0)
            for target in sorted(adjacency.get(current,[]),key=str):
                if target in distances:continue
                distances[target]=distances[current]+1;queue.append(target)
        return distances

    @staticmethod
    def _technical_event(identifier,node_type,process_id,participant_id):
        return {"id":identifier,"type":node_type,"name":identifier,"processRef":process_id,
                "participantRef":participant_id,"laneRef":None,"actor":None,"properties":{}}

    @classmethod
    def _add_technical_flow(cls,flows,used_ids,source,target,process_id,report,code):
        identifier=cls._unique_id(f"flow_{source}_{target}",used_ids);used_ids.add(identifier)
        flows.append({"id":identifier,"source":source,"target":target,"processRef":process_id,
                      "condition":None,"isDefault":False,"properties":{}})
        report.append(cls._entry(code,f"Technical sequence flow '{identifier}' added.",flow_id=identifier))

    @staticmethod
    def _unique_id(base,used):
        if base not in used:return base
        counter=1
        while f"{base}_{counter}" in used:counter+=1
        return f"{base}_{counter}"

    @staticmethod
    def _entry(code,message,flow_id=None,node_id=None):
        return {"code":code,"message":message,"flow_id":str(flow_id) if flow_id is not None else None,"node_id":str(node_id) if node_id is not None else None,"severity":"warning"}
