"""Deterministic construction invariants derived from grounded requirements."""
from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .coverage_validator import normalize_semantic_tokens, semantic_label_match


class GroundedWorkflowCanonicalizer:
    """Complete only canonical structure proved by grounded requirements."""

    @classmethod
    def canonicalize(
        cls, workflow: dict[str, Any], requirements: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        value = copy.deepcopy(workflow)
        report = {
            "participants_added": [], "processes_added": [],
            "timer_nodes_corrected": [], "message_flows_reclassified": [],
            "interaction_endpoints_added": [], "decision_outcomes_normalized": [],
            "participant_matches": [], "participants_created": [], "lanes_reused": [],
            "nodes_reused_for_interactions": [], "nodes_inserted": [], "flows_rewired": [],
            "participant_resolution_attempts": [], "node_ownership_changes": [],
            "interaction_endpoint_reuse": [], "interaction_endpoint_insertions": [],
            "interaction_endpoint_rollbacks": [],
            "interaction_endpoint_candidates": [], "interaction_order": [],
            "self_loop_rejections": [], "rewiring_validation_failures": [],
        }
        cls._ensure_participant_processes(value, requirements, report)
        cls._ensure_required_lanes(value, requirements, report)
        cls._normalize_timer_nodes(value, requirements, report)
        cls._reclassify_local_messages(value, requirements, report)
        cls._ensure_interactions(value, requirements, report)
        return value, report

    @classmethod
    def _ensure_participant_processes(cls, workflow, requirements, report):
        processes = workflow.setdefault("processes", [])
        participants = workflow.setdefault("participants", [])
        nodes = workflow.setdefault("nodes", [])
        flows = workflow.setdefault("flows", [])
        lanes = workflow.setdefault("lanes", [])
        if not all(isinstance(items, list) for items in (processes, participants, nodes, flows, lanes)):
            return
        used = cls._all_ids(workflow)
        process_ids = {item.get("id") for item in processes if isinstance(item, Mapping)}
        required = requirements.get("participants", []) if isinstance(requirements, Mapping) else []
        owned_processes = {p.get("processRef") for p in participants if isinstance(p, Mapping)}

        # Match labels, then guarantee one process per participant. The first
        # owner may keep an existing process; every additional owner gets a clone.
        for requirement in required if isinstance(required, list) else []:
            name = requirement.get("name") if isinstance(requirement, Mapping) else None
            if not isinstance(name, str) or not name.strip():
                continue
            participant = cls.resolve_participant(name, participants, report)
            if participant is not None:
                previous_name = participant.get("name")
                participant["name"] = name
                report["participant_matches"].append({
                    "requirement": name, "matched_existing_id": participant.get("id"),
                    "previous_name": previous_name, "canonical_name": name,
                })
                continue
            participant_id = cls._unique_id(f"participant_{cls._slug(name)}", used)
            process_matches = [p for p in processes if isinstance(p, Mapping) and p.get("id") not in owned_processes
                               and cls._process_matches_participant(p, name)]
            if len(process_matches) == 1:
                process_id = process_matches[0].get("id")
            else:
                process_id = cls._unique_id(f"process_{cls._slug(name)}", used)
                used.add(process_id); process_ids.add(process_id)
                processes.append(cls._process(process_id, name))
                report["processes_added"].append({"id": process_id, "participant": participant_id})
            used.add(participant_id); owned_processes.add(process_id)
            participants.append({"id": participant_id, "name": name, "processRef": process_id, "properties": {}})
            report["participants_added"].append({"id": participant_id, "name": name})
            report["participants_created"].append({"id": participant_id, "name": name})

        owners: dict[str, list[dict[str, Any]]] = {}
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            process_id = participant.get("processRef")
            if not isinstance(process_id, str) or process_id not in process_ids:
                process_id = cls._unique_id(f"process_{cls._slug(participant.get('name'))}", used)
                used.add(process_id); process_ids.add(process_id)
                processes.append(cls._process(process_id, str(participant.get("name") or process_id)))
                participant["processRef"] = process_id
                report["processes_added"].append({"id": process_id, "participant": participant.get("id")})
            owners.setdefault(process_id, []).append(participant)
        process_by_id = {p.get("id"): p for p in processes if isinstance(p, Mapping)}
        for process_id, members in owners.items():
            for participant in members[1:]:
                participant_id = participant.get("id")
                new_id = cls._unique_id(f"{process_id}_{cls._slug(participant_id)}", used); used.add(new_id)
                template = process_by_id.get(process_id, {})
                processes.append({**cls._process(new_id, str(participant.get("name") or new_id)),
                                  "description": str(template.get("description", "")),
                                  "executable": bool(template.get("executable", False)),
                                  "properties": copy.deepcopy(template.get("properties", {}))})
                participant["processRef"] = new_id
                for node in nodes:
                    if isinstance(node, dict) and node.get("participantRef") == participant_id:
                        node["processRef"] = new_id
                for lane in lanes:
                    if isinstance(lane, dict) and lane.get("participantRef") == participant_id:
                        lane["processRef"] = new_id
                node_process = {n.get("id"): n.get("processRef") for n in nodes if isinstance(n, Mapping)}
                for flow in flows:
                    if (isinstance(flow, dict) and node_process.get(flow.get("source")) == new_id
                            and node_process.get(flow.get("target")) == new_id):
                        flow["processRef"] = new_id
                report["processes_added"].append({"id": new_id, "participant": participant_id})

        # A grounded participant is valid even before an interaction endpoint is added.
        for participant in participants:
            if not isinstance(participant, Mapping):
                continue
            process_id, participant_id = participant.get("processRef"), participant.get("id")
            if not isinstance(process_id, str) or any(isinstance(n, Mapping) and n.get("processRef") == process_id for n in nodes):
                continue
            start = cls._add_node(nodes, used, f"start_{process_id}", "startEvent", "Start", process_id, participant_id)
            end = cls._add_node(nodes, used, f"end_{process_id}", "endEvent", "End", process_id, participant_id)
            cls._add_flow(flows, used, start["id"], end["id"], process_id)

        cls._complete_participant_processes(workflow, used)

    @classmethod
    def _ensure_required_lanes(cls, workflow, requirements, report):
        required = requirements.get("lanes", []) if isinstance(requirements, Mapping) else []
        lanes = workflow.get("lanes", []); participants = workflow.get("participants", [])
        if not isinstance(required, list) or not isinstance(lanes, list): return
        used = cls._all_ids(workflow)
        for requirement in required:
            if not isinstance(requirement, Mapping): continue
            name, owner = requirement.get("name"), requirement.get("participant")
            matches = [p for p in participants if isinstance(p, Mapping) and semantic_label_match(owner, p.get("name"))]
            if not isinstance(name, str) or len(matches) != 1: continue
            participant = matches[0]
            existing = next((lane for lane in lanes if isinstance(lane, dict)
                             and lane.get("participantRef") == participant.get("id")
                             and semantic_label_match(name, lane.get("name"))), None)
            if existing is not None:
                previous_name=existing.get("name");existing["name"]=name
                report["lanes_reused"].append({"id":existing.get("id"),"previous_name":previous_name,"canonical_name":name})
                continue
            lane_id = cls._unique_id(f"lane_{cls._slug(name)}", used); used.add(lane_id)
            lanes.append({"id": lane_id, "name": name, "processRef": participant.get("processRef"),
                          "participantRef": participant.get("id"), "properties": {}})

    @classmethod
    def _complete_participant_processes(cls, workflow, used):
        nodes, flows = workflow.get("nodes", []), workflow.get("flows", [])
        for participant in workflow.get("participants", []):
            if not isinstance(participant, Mapping): continue
            process_id, participant_id = participant.get("processRef"), participant.get("id")
            local = [n for n in nodes if isinstance(n, Mapping) and n.get("processRef") == process_id]
            if not local: continue
            local_ids = {n.get("id") for n in local}
            incoming = {node_id: 0 for node_id in local_ids}; outgoing = dict(incoming)
            for flow in flows:
                if isinstance(flow, Mapping) and flow.get("source") in local_ids and flow.get("target") in local_ids:
                    outgoing[flow.get("source")] += 1; incoming[flow.get("target")] += 1
            starts = [n for n in local if n.get("type") == "startEvent"]
            roots = [n for n in local if n.get("type") != "startEvent" and incoming[n.get("id")] == 0]
            if not starts and len(roots) == 1:
                start = cls._add_node(nodes, used, f"start_{process_id}", "startEvent", "Start", process_id, participant_id)
                cls._add_flow(flows, used, start["id"], roots[0]["id"], process_id)
            elif len(starts) == 1 and outgoing[starts[0].get("id")] == 0 and len(roots) == 1:
                cls._add_flow(flows, used, starts[0]["id"], roots[0]["id"], process_id)
            ends = [n for n in local if n.get("type") == "endEvent"]
            sinks = [n for n in local if n.get("type") != "endEvent" and outgoing[n.get("id")] == 0]
            if not ends and len(sinks) == 1:
                end = cls._add_node(nodes, used, f"end_{process_id}", "endEvent", "End", process_id, participant_id)
                cls._add_flow(flows, used, sinks[0]["id"], end["id"], process_id)
            elif len(ends) == 1 and incoming[ends[0].get("id")] == 0 and len(sinks) == 1:
                cls._add_flow(flows, used, sinks[0]["id"], ends[0]["id"], process_id)

    @classmethod
    def _normalize_timer_nodes(cls, workflow, requirements, report):
        nodes = workflow.get("nodes", [])
        timers = requirements.get("timers", []) if isinstance(requirements, Mapping) else []
        fallback = timers[0] if isinstance(timers, list) and len(timers) == 1 and isinstance(timers[0], Mapping) else {}
        if not isinstance(nodes, list): return
        for node in nodes:
            if not isinstance(node, dict) or str(node.get("type", "")).replace("_", "").lower() != "timerdefinition":
                continue
            properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            raw_timer = properties.get("timer") if isinstance(properties.get("timer"), Mapping) else {}
            kind = raw_timer.get("kind") or properties.get("kind") or fallback.get("kind") or "duration"
            value = raw_timer.get("value") or properties.get("value") or properties.get("duration") or fallback.get("value")
            node["type"] = "intermediateCatchEvent"
            node["properties"] = {**properties, "timer": {"kind": str(kind), "value": str(value)}} if value else properties
            report["timer_nodes_corrected"].append({"id": node.get("id"), "type": "intermediateCatchEvent"})

    @classmethod
    def _reclassify_local_messages(cls, workflow, requirements, report):
        # Preserve legacy validation behaviour when no grounded collaboration
        # fact exists. With grounded facts, a local message can only be an
        # incorrectly classified internal sequence.
        grounded_interactions = requirements.get("interactions", []) if isinstance(requirements, Mapping) else []
        if not grounded_interactions: return
        nodes, flows, messages = workflow.get("nodes", []), workflow.get("flows", []), workflow.get("messageFlows", [])
        if not all(isinstance(items, list) for items in (nodes, flows, messages)): return
        by_id = {n.get("id"): n for n in nodes if isinstance(n, Mapping)}
        used = cls._all_ids(workflow); retained = []
        pairs = {(f.get("source"), f.get("target")) for f in flows if isinstance(f, Mapping)}
        for message in messages:
            source = by_id.get(message.get("source")) if isinstance(message, Mapping) else None
            target = by_id.get(message.get("target")) if isinstance(message, Mapping) else None
            if not source or not target or source.get("processRef") != target.get("processRef"):
                retained.append(message); continue
            pair = (message.get("source"), message.get("target"))
            if pair[0] == pair[1]:
                report["self_loop_rejections"].append({"flow_id":message.get("id"),"source":pair[0],"target":pair[1]})
                continue
            if pair not in pairs:
                cls._add_flow(flows, used, pair[0], pair[1], source.get("processRef"), message.get("name"))
                pairs.add(pair)
            report["message_flows_reclassified"].append({"id": message.get("id"), "as": "sequenceFlow"})
        workflow["messageFlows"] = retained

    @classmethod
    def _ensure_interactions(cls, workflow, requirements, report):
        interactions = requirements.get("interactions", []) if isinstance(requirements, Mapping) else []
        if not isinstance(interactions, list): return
        participants = [p for p in workflow.get("participants", []) if isinstance(p, Mapping)]
        nodes, flows, messages = workflow.get("nodes", []), workflow.get("flows", []), workflow.get("messageFlows", [])
        if not all(isinstance(items, list) for items in (nodes, flows, messages)): return
        used = cls._all_ids(workflow)
        last_endpoint={};used_endpoints=set()
        for interaction_index,interaction in enumerate(interactions):
            if not isinstance(interaction, Mapping): continue
            nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[]);messages=workflow.get("messageFlows",[])
            source_participant=cls.resolve_participant(interaction.get("source"),participants)
            target_participant=cls.resolve_participant(interaction.get("target"),participants)
            if source_participant is None or target_participant is None or source_participant.get("processRef") == target_participant.get("processRef"): continue
            report["interaction_order"].append({"index":interaction_index,"source":interaction.get("source"),"target":interaction.get("target")})
            source_node = cls._message_endpoint(nodes,flows,source_participant,"source","sendTask",
                                                target_participant.get("name"),interaction_index,used_endpoints,report)
            target_node = cls._message_endpoint(nodes,flows,target_participant,"target","receiveTask",
                                                source_participant.get("name"),interaction_index,used_endpoints,report)
            if source_node is None:
                previous=last_endpoint.get(source_participant.get("id"))
                source_node = cls.insert_node_into_process_sequence(
                    workflow,source_participant,"sendTask",f"Send to {target_participant.get('name')}",used,
                    "after_node" if previous else "before_end",report,previous)
                if source_node is not None: report["interaction_endpoints_added"].append({"id": source_node["id"], "role": "source"})
            else:
                reuse={"id":source_node.get("id"),"role":"source"}
                report["nodes_reused_for_interactions"].append(reuse);report["interaction_endpoint_reuse"].append(reuse)
            if target_node is None:
                previous=last_endpoint.get(target_participant.get("id"))
                target_node = cls.insert_node_into_process_sequence(
                    workflow,target_participant,"receiveTask",f"Receive from {source_participant.get('name')}",used,
                    "after_node" if previous else "after_start",report,previous)
                if target_node is not None: report["interaction_endpoints_added"].append({"id": target_node["id"], "role": "target"})
            else:
                reuse={"id":target_node.get("id"),"role":"target"}
                report["nodes_reused_for_interactions"].append(reuse);report["interaction_endpoint_reuse"].append(reuse)
            if source_node is None or target_node is None: continue
            used_endpoints.update((source_node["id"],target_node["id"]))
            last_endpoint[source_participant.get("id")]=source_node["id"]
            last_endpoint[target_participant.get("id")]=target_node["id"]
            pair = (source_node["id"], target_node["id"])
            if pair[0]==pair[1]:
                report["self_loop_rejections"].append({"interaction_index":interaction_index,"source":pair[0],"target":pair[1]})
                continue
            if not any(isinstance(m, Mapping) and (m.get("source"), m.get("target")) == pair for m in messages):
                message_id = cls._unique_id("message_flow", used); used.add(message_id)
                messages.append({"id": message_id, "source": pair[0], "target": pair[1], "properties": {}})

    @classmethod
    def _message_endpoint(cls,nodes,flows,participant,side,node_type,counterpart_name,
                          interaction_index,used_endpoints,report):
        process_id = participant.get("processRef")
        role="source" if node_type=="sendTask" else "target";counterpart=normalize_semantic_tokens(counterpart_name)
        send_terms={"send","inform","notify","forward","submit"};receive_terms={"receive"}
        ranked=[];details=[]
        for node in nodes:
            if not isinstance(node,dict) or node.get("processRef")!=process_id or node.get("id") in used_endpoints:continue
            node_tokens=normalize_semantic_tokens(node.get("name"));kind=node.get("type");score=0
            if kind==node_type:score+=50
            if role=="source" and kind=="receiveTask":continue
            if role=="target" and kind=="sendTask":continue
            directional=bool(node_tokens&(send_terms if role=="source" else receive_terms|send_terms))
            if directional:score+=20
            if counterpart and counterpart&node_tokens:score+=20
            if semantic_label_match(f"{'send' if role=='source' else 'receive'} {counterpart_name}",node.get("name")):score+=10
            if cls._valid_existing_endpoint(nodes,flows,node,process_id):score+=5
            compatible=score>=50 or directional
            details.append({"node_id":node.get("id"),"type":kind,"semantic_match":compatible,"score":score})
            if compatible and cls._valid_existing_endpoint(nodes,flows,node,process_id):ranked.append((score,str(node.get("id")),node))
        ranked.sort(key=lambda item:(-item[0],item[1]));selected=ranked[0][2] if ranked and (len(ranked)==1 or ranked[0][0]>ranked[1][0]) else None
        report["interaction_endpoint_candidates"].append({"interaction_index":interaction_index,
            "participant":participant.get("id"),"role":role,"candidates":details,
            "selected":selected.get("id") if selected else None})
        return selected

    @staticmethod
    def _valid_existing_endpoint(nodes,flows,node,process_id):
        return (node.get("type") in {"sendTask","receiveTask","userTask","serviceTask","manualTask","scriptTask","businessRuleTask"}
                and GroundedWorkflowCanonicalizer._valid_local_topology(
                    {"nodes":nodes,"flows":flows},process_id,node.get("id")))

    @staticmethod
    def _has_message_between(messages,nodes,source_participant_id,target_participant_id):
        by_id={n.get("id"):n for n in nodes if isinstance(n,Mapping)}
        return any(isinstance(message,Mapping)
                   and by_id.get(message.get("source"),{}).get("participantRef")==source_participant_id
                   and by_id.get(message.get("target"),{}).get("participantRef")==target_participant_id
                   for message in messages)

    @classmethod
    def insert_node_into_process_sequence(cls,workflow,participant,node_type,name,used,position,report,previous_id=None):
        """Atomically insert an endpoint by splitting one local sequence flow."""
        candidate=copy.deepcopy(workflow);candidate_used=set(used)
        nodes, flows = candidate["nodes"], candidate["flows"]
        process_id, participant_id = participant.get("processRef"), participant.get("id")
        local = [n for n in nodes if isinstance(n, Mapping) and n.get("processRef") == process_id]
        local_ids={n.get("id") for n in local}
        local_flows=[f for f in flows if isinstance(f,dict) and f.get("source") in local_ids and f.get("target") in local_ids]
        if position=="after_node":
            anchors=[f for f in local_flows if f.get("source")==previous_id]
        else:
            boundary_type="startEvent" if position=="after_start" else "endEvent"
            boundaries=[n for n in local if n.get("type")==boundary_type]
            if len(boundaries)!=1:
                report["interaction_endpoint_rollbacks"].append({"node":name,"process":process_id,"reason":"No unique boundary event"})
                return None
            boundary_id=boundaries[0].get("id")
            anchors=[f for f in local_flows if (f.get("source")==boundary_id if position=="after_start" else f.get("target")==boundary_id)]
        if len(anchors)!=1:
            report["interaction_endpoint_rollbacks"].append({"node":name,"process":process_id,"reason":"No safe insertion point"})
            return None
        anchor=anchors[0]
        if anchor.get("source")==anchor.get("target") or previous_id==anchor.get("target"):
            report["self_loop_rejections"].append({"source":anchor.get("source"),"target":anchor.get("target"),"node":name})
            return None
        endpoint = cls._add_node(nodes, candidate_used, f"{node_type}_{process_id}", node_type, name, process_id, participant_id)
        if position in {"after_start","after_node"}:
            successor=anchor["target"];anchor["target"]=endpoint["id"]
            new_flow=cls._add_flow(flows,candidate_used,endpoint["id"],successor,process_id)
        else:
            predecessor=anchor["source"];anchor["source"]=endpoint["id"]
            new_flow=cls._add_flow(flows,candidate_used,predecessor,endpoint["id"],process_id)
        if not cls._valid_local_topology(candidate,process_id,endpoint["id"]):
            report["interaction_endpoint_rollbacks"].append({"node":name,"process":process_id,"reason":"Local topology validation failed"})
            report["rewiring_validation_failures"].append({"node":endpoint["id"],"process":process_id})
            return None
        workflow["nodes"]=candidate["nodes"];workflow["flows"]=candidate["flows"]
        used.clear();used.update(candidate_used)
        report["nodes_inserted"].append({"id":endpoint["id"],"processRef":process_id,"position":position})
        report["interaction_endpoint_insertions"].append({"id":endpoint["id"],"process":process_id,"position":position})
        report["flows_rewired"].append({"id":anchor.get("id"),"inserted_node":endpoint["id"],"new_flow":new_flow["id"]})
        return endpoint

    @staticmethod
    def _valid_local_topology(workflow,process_id,new_node_id):
        nodes=[n for n in workflow.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
        ids={n.get("id") for n in nodes};flows=[f for f in workflow.get("flows",[]) if isinstance(f,Mapping) and f.get("source") in ids and f.get("target") in ids]
        starts=[n.get("id") for n in nodes if n.get("type")=="startEvent"]
        ends={n.get("id") for n in nodes if n.get("type")=="endEvent"}
        if len(starts)!=1 or not ends:return False
        outgoing={node_id:[] for node_id in ids};incoming={node_id:[] for node_id in ids}
        pairs=set()
        for flow in flows:
            pair=(flow.get("source"),flow.get("target"))
            if pair in pairs or flow.get("processRef")!=process_id:return False
            pairs.add(pair);outgoing[pair[0]].append(pair[1]);incoming[pair[1]].append(pair[0])
        reachable=set();queue=[starts[0]]
        while queue:
            current=queue.pop(0)
            if current in reachable:continue
            reachable.add(current);queue.extend(outgoing.get(current,[]))
        reverse=set();queue=list(ends)
        while queue:
            current=queue.pop(0)
            if current in reverse:continue
            reverse.add(current);queue.extend(incoming.get(current,[]))
        business={n.get("id") for n in nodes if n.get("type") not in {"startEvent","endEvent"}}
        return (new_node_id in business and bool(incoming[new_node_id]) and bool(outgoing[new_node_id])
                and business<=reachable and business<=reverse)

    @staticmethod
    def resolve_participant(requirement_name, participants, report=None):
        """Resolve exactly one existing participant through the shared matcher."""
        candidates=[p for p in participants if isinstance(p,dict)]
        matches=[p for p in candidates if semantic_label_match(requirement_name,p.get("name"))]
        selected=matches[0] if len(matches)==1 else None
        if isinstance(report,dict):
            for candidate in candidates:
                matched=candidate in matches
                report["participant_resolution_attempts"].append({
                    "requirement":requirement_name,"candidate_id":candidate.get("id"),
                    "candidate_name":candidate.get("name"),"semantic_match":matched,
                    "normalized_requirement":sorted(normalize_semantic_tokens(requirement_name)),
                    "normalized_candidate":sorted(normalize_semantic_tokens(candidate.get("name"))),
                    "decision":"reuse" if candidate is selected else "reject",
                    "reason":"unique semantic match" if candidate is selected else
                             "ambiguous semantic match" if matched else "semantic labels differ",
                })
        return selected

    @staticmethod
    def _process(identifier, name):
        return {"id": identifier, "name": name, "description": "", "executable": False, "properties": {}}

    @classmethod
    def _add_node(cls, nodes, used, base, node_type, name, process_id, participant_id):
        identifier = cls._unique_id(base, used); used.add(identifier)
        node = {"id": identifier, "type": node_type, "name": name, "processRef": process_id,
                "participantRef": participant_id, "laneRef": None, "actor": None, "properties": {"synthetic": True}}
        nodes.append(node); return node

    @classmethod
    def _add_flow(cls, flows, used, source, target, process_id, name=None):
        if source==target:return None
        identifier = cls._unique_id(f"flow_{source}_{target}", used); used.add(identifier)
        flow = {"id": identifier, "source": source, "target": target, "processRef": process_id,
                "condition": None, "isDefault": False, "properties": {"synthetic": True}}
        if name is not None: flow["name"] = str(name)
        flows.append(flow); return flow

    @staticmethod
    def _all_ids(workflow):
        return {item.get("id") for key in ("processes", "participants", "lanes", "nodes", "flows", "messageFlows")
                for item in workflow.get(key, []) if isinstance(item, Mapping) and item.get("id")}

    @staticmethod
    def _slug(value):
        text = unicodedata.normalize("NFKD", str(value or "item")).encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "item"

    @classmethod
    def _process_matches_participant(cls, process, participant_name):
        participant = set(cls._slug(participant_name).split("_"))
        process_tokens = set(cls._slug(process.get("name") or process.get("id")).split("_")) - {"process", "proc"}
        return bool(participant) and participant <= process_tokens

    @staticmethod
    def _unique_id(base, used):
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", str(base)).strip("_") or "item"
        if base[0].isdigit(): base = f"id_{base}"
        candidate, suffix = base, 2
        while candidate in used: candidate, suffix = f"{base}_{suffix}", suffix + 1
        return candidate
