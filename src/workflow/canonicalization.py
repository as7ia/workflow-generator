"""Deterministic construction invariants derived from grounded requirements."""
from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .activity_semantics import analyze_activity_semantics, normalize_semantic_tokens, semantic_analysis_for_report
from .coverage_validator import semantic_activity_score, semantic_label_match


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
            "decisions_materialized": [], "decision_materialization_diagnostics": [],
            "decision_activity_candidates": [],
            "interactions_materialized": [], "interaction_messageflows_created": [],
            "interaction_messageflow_rollbacks": [],
            "process_continuity_repairs": [], "premature_end_repairs": [],
            "orphan_business_components": [], "artificial_cycles_rejected": [],
            "process_boundary_report": [], "boundary_event_reassignments": [],
            "process_split_flows_removed": [],
            "boundary_lifecycle": [], "duplicate_message_flows_removed": [],
        }
        boundary_before=cls._boundary_snapshot(value)
        cls._ensure_participant_processes(value, requirements, report)
        cls._ensure_process_boundaries(value, report)
        cls._record_boundary_lifecycle(boundary_before,value,report)
        cls._ensure_required_lanes(value, requirements, report)
        cls._normalize_timer_nodes(value, requirements, report)
        cls._reclassify_local_messages(value, requirements, report)
        cls._ensure_interactions(value, requirements, report)
        cls._deduplicate_message_flows(value,report)
        cls._materialize_decisions(value,requirements,report)
        cls._repair_artificial_two_node_cycles(value, requirements, report)
        cls._ensure_local_process_continuity(value, report)
        return value, report

    @classmethod
    def _materialize_decisions(cls,workflow,requirements,report):
        decisions=requirements.get("decisions",[]) if isinstance(requirements,Mapping) else []
        if not isinstance(decisions,list):return
        for decision in decisions:
            if not isinstance(decision,Mapping):continue
            name=decision.get("name");raw_outcomes=decision.get("outcomes",[])
            outcomes=[value.get("name") if isinstance(value,Mapping) else value for value in raw_outcomes]
            outcomes=[str(value).strip() for value in outcomes if str(value or "").strip()]
            if not isinstance(name,str) or len(outcomes)<2:continue
            existing=[n for n in workflow.get("nodes",[]) if isinstance(n,Mapping) and n.get("type") in {"exclusiveGateway","inclusiveGateway"} and semantic_label_match(name,n.get("name"))]
            if existing:
                report["decision_materialization_diagnostics"].append({"decision":name,"status":"reused","gateway":existing[0].get("id")})
                continue
            candidate=copy.deepcopy(workflow);nodes=candidate.get("nodes",[]);flows=candidate.get("flows",[])
            participants=[p for p in candidate.get("participants",[]) if isinstance(p,Mapping)]
            lanes=[lane for lane in candidate.get("lanes",[]) if isinstance(lane,Mapping)]
            owner_labels=[decision.get(key) for key in ("participant","participantRef","lane","sourceLane") if decision.get(key)]
            for raw_outcome in raw_outcomes:
                if isinstance(raw_outcome,Mapping):owner_labels.extend(raw_outcome.get(key) for key in ("participant","participantRef","lane","sourceLane") if raw_outcome.get(key))
            expected_participants={p.get("id") for p in participants if any(label==p.get("id") or semantic_label_match(label,p.get("name")) for label in owner_labels)}
            expected_participants.update(lane.get("participantRef") for lane in lanes if any(label==lane.get("id") or semantic_label_match(label,lane.get("name")) for label in owner_labels))
            task_types={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask"}
            ranked=[];candidate_details=[];decision_tokens=normalize_semantic_tokens(name);decision_parts=analyze_activity_semantics(name)
            for node in nodes:
                if not isinstance(node,Mapping) or node.get("type") not in task_types:continue
                node_tokens=normalize_semantic_tokens(node.get("name"));activity=semantic_activity_score({"name":name},node)
                node_parts=analyze_activity_semantics(node)
                decision_support=(node_parts["action_family"]=="verify" and decision_parts["action_family"] not in {"send","receive","forward","notify","request"})
                action_match=activity["action_match"] or decision_support
                overlap=len(decision_tokens&node_tokens)/len(decision_tokens|node_tokens) if decision_tokens and node_tokens else 0
                outgoing_count=sum(isinstance(flow,Mapping) and flow.get("source")==node.get("id") for flow in flows)
                participant_match=(node.get("participantRef") in expected_participants) if owner_labels else None
                object_match=activity["object_match"]
                qualifier_match=decision_parts["qualifiers"]==node_parts["qualifiers"]
                score=((80 if action_match else -100)+(30 if object_match is True else -30 if object_match is False else 0)
                       +round(overlap*20)+(10 if outgoing_count==1 else 5 if outgoing_count else 0)
                       +(20 if participant_match is True else -20 if participant_match is False else 0)
                       +(20 if qualifier_match else -30))
                detail={"node_id":node.get("id"),"score":score,"semantic_match":action_match and object_match is not False,
                        "action_match":action_match,"participant_match":participant_match,"object_match":object_match,
                        "repetition_match":qualifier_match,"qualifier_match":qualifier_match,
                        "semantic_analysis":{"requirement":semantic_analysis_for_report(decision_parts),
                                             "candidate":semantic_analysis_for_report(node_parts)}}
                candidate_details.append(detail)
                if action_match and object_match is not False and participant_match is not False and score>0:
                    ranked.append((score,str(node.get("id")),node))
            ranked.sort(key=lambda item:(-item[0],item[1]))
            best=ranked[0][0] if ranked else None;second=ranked[1][0] if len(ranked)>1 else None
            threshold=30;required_margin=1;margin=(best-second) if best is not None and second is not None else best
            task=ranked[0][2] if ranked and best>=threshold and (second is None or margin>=required_margin) else None
            selection_reason="unique best decision activity" if task is not None else "equal best decision activities" if ranked and second==best else "no decision activity above threshold"
            report["decision_activity_candidates"].append({"decision":name,"candidates":candidate_details,
                "best_score":best,"second_best_score":second,"selection_threshold":threshold,
                "selection_margin":margin,"selected":task.get("id") if task else None,"reason":selection_reason})
            if task is None:
                report["decision_materialization_diagnostics"].append({"decision":name,"status":"ambiguous","reason":selection_reason})
                continue
            process_id=task.get("processRef");outgoing=[f for f in flows if isinstance(f,dict) and f.get("processRef")==process_id and f.get("source")==task.get("id")]
            if len(outgoing)!=1:
                report["decision_materialization_diagnostics"].append({"decision":name,"status":"ambiguous","reason":"decision activity has no unique continuation"})
                continue
            used=cls._all_ids(candidate);direct=outgoing[0];continuation=direct.get("target")
            gateway=cls._add_node(nodes,used,f"gateway_{cls._slug(name)}","exclusiveGateway",name,process_id,task.get("participantRef"))
            direct["target"]=gateway["id"]
            targets=[];created=[];failed=False
            for outcome in outcomes:
                matches=[n for n in nodes if isinstance(n,Mapping) and n.get("processRef")==process_id and n.get("id")!=gateway["id"] and semantic_label_match(outcome,n.get("name"))]
                if len(matches)>1:failed=True;break
                if matches:target=matches[0]
                else:
                    target=cls._add_node(nodes,used,f"outcome_{cls._slug(outcome)}","serviceTask",outcome,process_id,task.get("participantRef"));created.append(target)
                    cls._add_flow(flows,used,target["id"],continuation,process_id)
                if target["id"] in targets:failed=True;break
                targets.append(target["id"])
                branch=cls._add_flow(flows,used,gateway["id"],target["id"],process_id,outcome)
                if branch is not None:branch["condition"]=outcome
            if failed or not cls._valid_local_topology(candidate,process_id,gateway["id"]):
                report["decision_materialization_diagnostics"].append({"decision":name,"status":"rollback","reason":"branch topology is ambiguous or invalid"})
                continue
            workflow["nodes"]=nodes;workflow["flows"]=flows
            report["decisions_materialized"].append({"decision":name,"gateway":gateway["id"],"outcomes":outcomes,"created_nodes":[n["id"] for n in created]})

    @classmethod
    def _ensure_participant_processes(cls, workflow, requirements, report):
        processes = workflow.setdefault("processes", [])
        participants = workflow.setdefault("participants", [])
        nodes = workflow.setdefault("nodes", [])
        flows = workflow.setdefault("flows", [])
        messages = workflow.setdefault("messageFlows", [])
        lanes = workflow.setdefault("lanes", [])
        if not all(isinstance(items, list) for items in (processes, participants, nodes, flows, messages, lanes)):
            return
        original_node_process={n.get("id"):n.get("processRef") for n in nodes if isinstance(n,Mapping)}
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
        boundary_counts={}
        for node in nodes:
            if isinstance(node,Mapping) and node.get("type") in {"startEvent","endEvent"}:
                marker=(node.get("processRef"),node.get("type"));boundary_counts[marker]=boundary_counts.get(marker,0)+1
        for process_id, members in owners.items():
            primary=members[0]
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
                        if node.get("type") in {"startEvent","endEvent"}:
                            if boundary_counts.get((process_id,node.get("type")),0)>1:
                                node["processRef"]=new_id
                                report["boundary_event_reassignments"].append({
                                    "node_id":node.get("id"),"type":node.get("type"),
                                    "processRef_before":process_id,"processRef_after":new_id,
                                    "participantRef_before":participant_id,"participantRef_after":participant_id,
                                    "reason":"boundary ownership is unambiguous",
                                })
                            else:
                                previous_participant=node.get("participantRef")
                                node["participantRef"]=primary.get("id")
                                node["laneRef"]=None
                                report["boundary_event_reassignments"].append({
                                    "node_id":node.get("id"),"type":node.get("type"),
                                    "processRef_before":process_id,"processRef_after":process_id,
                                    "participantRef_before":previous_participant,"participantRef_after":primary.get("id"),
                                    "reason":"preserve unique source process boundary",
                                })
                        else:
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

        # A sequence flow cannot survive a split when its endpoints now belong
        # to different processes. Grounded interactions are materialized later.
        node_process={n.get("id"):n.get("processRef") for n in nodes if isinstance(n,Mapping)}
        retained=[]
        for flow in flows:
            if not isinstance(flow,dict):retained.append(flow);continue
            source=flow.get("source",flow.get("sourceRef"));target=flow.get("target",flow.get("targetRef"))
            source_process=node_process.get(source);target_process=node_process.get(target)
            split_created_crossing=(original_node_process.get(source)==original_node_process.get(target)
                                    and source_process and target_process and source_process!=target_process)
            if split_created_crossing:
                report["process_split_flows_removed"].append({"id":flow.get("id"),"source":source,"target":target,
                                                               "source_process":source_process,"target_process":target_process})
                message={"id":flow.get("id"),"source":source,"target":target,
                         "properties":copy.deepcopy(flow.get("properties",{}))}
                if flow.get("name") is not None:message["name"]=flow.get("name")
                messages.append(message)
                continue
            if source_process and source_process==target_process:flow["processRef"]=source_process
            retained.append(flow)
        flows[:]=retained

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


    @classmethod
    def _ensure_process_boundaries(cls,workflow,report):
        """Transactionally add only unambiguous local process boundaries."""
        used=cls._all_ids(workflow)
        participants=[p for p in workflow.get("participants",[]) if isinstance(p,Mapping)]
        for process in [p for p in workflow.get("processes",[]) if isinstance(p,Mapping)]:
            process_id=process.get("id");candidate=copy.deepcopy(workflow)
            nodes=[n for n in candidate.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
            if not nodes:continue
            ids={n.get("id") for n in nodes};all_flows=candidate.get("flows",[])
            def endpoints(flow):return flow.get("source",flow.get("sourceRef")),flow.get("target",flow.get("targetRef"))
            flows=[f for f in all_flows if isinstance(f,Mapping) and f.get("processRef")==process_id
                   and endpoints(f)[0] in ids and endpoints(f)[1] in ids]
            incoming={identifier:0 for identifier in ids};outgoing={identifier:0 for identifier in ids}
            for flow in flows:
                source,target=endpoints(flow);outgoing[source]+=1;incoming[target]+=1
            starts=[n for n in nodes if n.get("type")=="startEvent"]
            ends=[n for n in nodes if n.get("type")=="endEvent"]
            business=[n for n in nodes if n.get("type") not in {"startEvent","endEvent"}]
            roots=[n for n in business if incoming[n.get("id")]==0]
            terminals=[n for n in business if outgoing[n.get("id")]==0]
            detail={"process_id":process_id,"starts_before":[n.get("id") for n in starts],
                    "ends_before":[n.get("id") for n in ends],"root_candidates":[n.get("id") for n in roots],
                    "terminal_candidates":[n.get("id") for n in terminals],"created_start":None,"created_end":None}
            owner=next((p for p in participants if p.get("processRef")==process_id),None)
            candidate_used=set(used);changed=False;ambiguous=False
            if not starts:
                if len(roots)==1 and owner is not None:
                    start=cls._add_node(candidate["nodes"],candidate_used,f"start_{process_id}","startEvent","Start",process_id,owner.get("id"))
                    cls._add_flow(candidate["flows"],candidate_used,start["id"],roots[0]["id"],process_id)
                    detail["created_start"]=start["id"];changed=True
                else:ambiguous=True
            elif len(starts)==1 and outgoing[starts[0].get("id")]==0 and len(roots)==1:
                cls._add_flow(candidate["flows"],candidate_used,starts[0]["id"],roots[0]["id"],process_id);changed=True
            if not ends:
                if len(terminals)==1 and owner is not None:
                    end=cls._add_node(candidate["nodes"],candidate_used,f"end_{process_id}","endEvent","End",process_id,owner.get("id"))
                    cls._add_flow(candidate["flows"],candidate_used,terminals[0]["id"],end["id"],process_id)
                    detail["created_end"]=end["id"];changed=True
                else:ambiguous=True
            elif len(ends)==1 and incoming[ends[0].get("id")]==0 and len(terminals)==1:
                cls._add_flow(candidate["flows"],candidate_used,terminals[0]["id"],ends[0]["id"],process_id);changed=True
            candidate_nodes=[n for n in candidate.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
            candidate_starts=[n.get("id") for n in candidate_nodes if n.get("type")=="startEvent"]
            candidate_ends=[n.get("id") for n in candidate_nodes if n.get("type")=="endEvent"]
            valid=not ambiguous and cls._process_has_valid_boundaries(candidate,process_id)
            if valid and changed:
                workflow["nodes"]=candidate["nodes"];workflow["flows"]=candidate["flows"]
                used.clear();used.update(candidate_used)
            detail["starts_after"]=candidate_starts if valid else detail["starts_before"]
            detail["ends_after"]=candidate_ends if valid else detail["ends_before"]
            detail["status"]="repaired" if valid and changed else "valid" if valid else "ambiguous"
            if not valid:detail["created_start"]=None;detail["created_end"]=None
            report["process_boundary_report"].append(detail)

    @staticmethod
    def _process_has_valid_boundaries(workflow,process_id):
        nodes=[n for n in workflow.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
        ids={n.get("id") for n in nodes};starts=[n.get("id") for n in nodes if n.get("type")=="startEvent"];ends={n.get("id") for n in nodes if n.get("type")=="endEvent"}
        if not nodes or not starts or not ends:return False
        def endpoints(flow):return flow.get("source",flow.get("sourceRef")),flow.get("target",flow.get("targetRef"))
        flows=[f for f in workflow.get("flows",[]) if isinstance(f,Mapping) and f.get("processRef")==process_id
               and endpoints(f)[0] in ids and endpoints(f)[1] in ids]
        outgoing={identifier:[] for identifier in ids};incoming={identifier:[] for identifier in ids}
        for flow in flows:
            source,target=endpoints(flow);outgoing[source].append(target);incoming[target].append(source)
        reachable=set();queue=list(starts)
        while queue:
            current=queue.pop(0)
            if current in reachable:continue
            reachable.add(current);queue.extend(outgoing.get(current,[]))
        to_end=set();queue=list(ends)
        while queue:
            current=queue.pop(0)
            if current in to_end:continue
            to_end.add(current);queue.extend(incoming.get(current,[]))
        business={n.get("id") for n in nodes if n.get("type") not in {"startEvent","endEvent"}}
        return business<=reachable and business<=to_end and bool(reachable&ends)

    @staticmethod
    def _boundary_snapshot(workflow):
        return {n.get("id"):{"type":n.get("type"),"process":n.get("processRef"),"participant":n.get("participantRef")}
                for n in workflow.get("nodes",[]) if isinstance(n,Mapping)
                and n.get("type") in {"startEvent","endEvent"} and n.get("id")}

    @classmethod
    def _record_boundary_lifecycle(cls,before,workflow,report):
        after=cls._boundary_snapshot(workflow)
        for node_id,value in before.items():
            current=after.get(node_id)
            action="deleted" if current is None else "moved" if (current["process"],current["participant"])!=(value["process"],value["participant"]) else "preserved"
            report["boundary_lifecycle"].append({"node_id":node_id,"type":value["type"],
                "process_before":value["process"],"participant_before":value["participant"],
                "process_after":current.get("process") if current else None,
                "participant_after":current.get("participant") if current else None,"action":action})
        for node_id,value in after.items():
            if node_id not in before:
                report["boundary_lifecycle"].append({"node_id":node_id,"type":value["type"],
                    "process_before":None,"participant_before":None,"process_after":value["process"],
                    "participant_after":value["participant"],"action":"cloned"})

    @staticmethod
    def _deduplicate_message_flows(workflow,report):
        retained=[];seen={}
        for message in workflow.get("messageFlows",[]):
            if not isinstance(message,Mapping):retained.append(message);continue
            semantic=tuple(sorted(normalize_semantic_tokens(message.get("name"))))
            marker=(message.get("source"),message.get("target"),semantic)
            if marker in seen:
                report["duplicate_message_flows_removed"].append({"id":message.get("id"),"kept":seen[marker],
                                                                   "source":marker[0],"target":marker[1]})
                continue
            seen[marker]=message.get("id");retained.append(message)
        workflow["messageFlows"]=retained

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
            before_interaction=copy.deepcopy(workflow);before_used=set(used)
            tracked=("nodes_inserted","flows_rewired","interaction_endpoint_insertions","interaction_endpoints_added")
            report_lengths={key:len(report[key]) for key in tracked}
            nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[]);messages=workflow.get("messageFlows",[])
            source_participant=cls.resolve_participant(interaction.get("source"),participants)
            target_participant=cls.resolve_participant(interaction.get("target"),participants)
            if source_participant is None or target_participant is None or source_participant.get("processRef") == target_participant.get("processRef"):
                report["interaction_messageflow_rollbacks"].append({"interaction_index":interaction_index,"reason":"participants unresolved or share a process"});continue
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
            if target_node is not None:
                topology_valid,_=cls._valid_existing_endpoint(nodes,flows,target_node,target_participant.get("processRef"))
                if not topology_valid:
                    previous=last_endpoint.get(target_participant.get("id"))
                    if not cls._connect_resume_endpoint(workflow,target_participant,target_node,previous,used,report):
                        target_node=None
                    nodes=workflow.get("nodes",[]);flows=workflow.get("flows",[]);messages=workflow.get("messageFlows",[])
            if target_node is None:
                previous=last_endpoint.get(target_participant.get("id"))
                target_node = cls.insert_node_into_process_sequence(
                    workflow,target_participant,"receiveTask",f"Receive from {source_participant.get('name')}",used,
                    "after_node" if previous else "after_start",report,previous)
                if target_node is not None: report["interaction_endpoints_added"].append({"id": target_node["id"], "role": "target"})
            else:
                reuse={"id":target_node.get("id"),"role":"target"}
                report["nodes_reused_for_interactions"].append(reuse);report["interaction_endpoint_reuse"].append(reuse)
            if source_node is None or target_node is None:
                workflow.clear();workflow.update(before_interaction);used.clear();used.update(before_used)
                for key in tracked:del report[key][report_lengths[key]:]
                report["interaction_messageflow_rollbacks"].append({"interaction_index":interaction_index,"reason":"endpoint resolution failed"})
                continue
            used_endpoints.update((source_node["id"],target_node["id"]))
            last_endpoint[source_participant.get("id")]=source_node["id"]
            last_endpoint[target_participant.get("id")]=target_node["id"]
            pair = (source_node["id"], target_node["id"])
            if pair[0]==pair[1]:
                workflow.clear();workflow.update(before_interaction);used.clear();used.update(before_used)
                for key in tracked:del report[key][report_lengths[key]:]
                report["self_loop_rejections"].append({"interaction_index":interaction_index,"source":pair[0],"target":pair[1]})
                report["interaction_messageflow_rollbacks"].append({"interaction_index":interaction_index,"reason":"self messageFlow rejected"})
                continue
            existing=next((m for m in messages if isinstance(m,Mapping) and (m.get("source"),m.get("target"))==pair),None)
            if existing is None:
                message_id = cls._unique_id("message_flow", used); used.add(message_id)
                messages.append({"id": message_id, "source": pair[0], "target": pair[1], "properties": {}})
                report["interaction_messageflows_created"].append({"interaction_index":interaction_index,"id":message_id,"source":pair[0],"target":pair[1]})
            else:message_id=existing.get("id")
            report["interactions_materialized"].append({"interaction_index":interaction_index,"source_participant":source_participant.get("id"),
                "target_participant":target_participant.get("id"),"source_node":pair[0],"target_node":pair[1],"messageFlow":message_id})

    @classmethod
    def _message_endpoint(cls,nodes,flows,participant,side,node_type,counterpart_name,
                          interaction_index,used_endpoints,report):
        process_id = participant.get("processRef")
        role="source" if node_type=="sendTask" else "target";counterpart=normalize_semantic_tokens(counterpart_name)
        send_terms={"send","notify","forward"};receive_terms={"receive"}
        ranked=[];details=[]
        for node in nodes:
            if not isinstance(node,dict) or node.get("processRef")!=process_id or node.get("id") in used_endpoints:continue
            if node.get("participantRef")!=participant.get("id"):continue
            node_analysis=analyze_activity_semantics(node);node_tokens=node_analysis["tokens"];kind=node.get("type");score=0
            if kind==node_type:score+=50
            if role=="source" and kind=="receiveTask":continue
            if role=="target" and kind=="sendTask":continue
            directional=bool(node_tokens&(send_terms if role=="source" else receive_terms|send_terms))
            if directional:score+=20
            if counterpart and counterpart&node_tokens:score+=20
            if semantic_label_match(f"{'send' if role=='source' else 'receive'} {counterpart_name}",node.get("name")):score+=10
            topology_valid,topology_reason=cls._valid_existing_endpoint(nodes,flows,node,process_id)
            if topology_valid:score+=5
            compatible=score>=50 or directional
            details.append({"node_id":node.get("id"),"type":kind,"candidate_mode":"reuse",
                            "semantic_match":compatible,"score":score,"topology_valid":topology_valid,
                            "topology_reason":topology_reason,
                            "semantic_analysis":{"candidate":semantic_analysis_for_report(node_analysis)}})
            resume_eligible=(role=="target" and kind=="receiveTask"
                             and cls._orphan_resume_endpoint(nodes,flows,node,process_id))
            details[-1]["resume_eligible"]=resume_eligible
            if compatible and (topology_valid or resume_eligible):ranked.append((score,str(node.get("id")),node))
        ranked.sort(key=lambda item:(-item[0],item[1]))
        threshold=20;best_score=ranked[0][0] if ranked else None;second_score=ranked[1][0] if len(ranked)>1 else None
        margin=(best_score-second_score) if best_score is not None and second_score is not None else best_score
        selected=ranked[0][2] if ranked and best_score>=threshold and (second_score is None or margin>0) else None
        reason=("unique best compatible endpoint" if selected is not None else
                "no topologically valid compatible endpoint" if not ranked else
                "best score below selection threshold" if best_score<threshold else "ambiguous equal best endpoints")
        report["interaction_endpoint_candidates"].append({"interaction_index":interaction_index,
            "participant":participant.get("id"),"role":role,"candidates":details,
            "best_score":best_score,"second_best_score":second_score,"selection_threshold":threshold,
            "selection_margin":margin,"selected":selected.get("id") if selected else None,
            "selection_reason":reason})
        return selected

    @staticmethod
    def _orphan_resume_endpoint(nodes,flows,node,process_id):
        local={n.get("id"):n for n in nodes if isinstance(n,Mapping) and n.get("processRef")==process_id}
        node_id=node.get("id")
        if node_id not in local:return False
        local_flows=[f for f in flows if isinstance(f,Mapping) and f.get("source") in local and f.get("target") in local]
        incoming={identifier:[] for identifier in local};outgoing={identifier:[] for identifier in local}
        for flow in local_flows:
            incoming[flow.get("target")].append(flow.get("source"));outgoing[flow.get("source")].append(flow.get("target"))
        if incoming.get(node_id) or not outgoing.get(node_id):return False
        ends={identifier for identifier,value in local.items() if value.get("type")=="endEvent"}
        visited=set();queue=[node_id]
        while queue:
            current=queue.pop(0)
            if current in visited:continue
            visited.add(current);queue.extend(outgoing.get(current,[]))
        return bool(visited&ends)

    @classmethod
    def _connect_resume_endpoint(cls,workflow,participant,endpoint,previous_id,used,report):
        """Splice a proven orphan receive component after the prior local interaction."""
        candidate=copy.deepcopy(workflow);nodes=candidate.get("nodes",[]);flows=candidate.get("flows",[])
        process_id=participant.get("processRef");local={n.get("id"):n for n in nodes if isinstance(n,Mapping) and n.get("processRef")==process_id}
        endpoint_id=endpoint.get("id");starts=[identifier for identifier,node in local.items() if node.get("type")=="startEvent"]
        anchor_id=previous_id if previous_id in local else starts[0] if len(starts)==1 else None
        if anchor_id is None or endpoint_id not in local:return False
        local_flows=[f for f in flows if isinstance(f,dict) and f.get("source") in local and f.get("target") in local]
        incoming=[f for f in local_flows if f.get("target")==endpoint_id]
        anchors=[f for f in local_flows if f.get("source")==anchor_id and local.get(f.get("target"),{}).get("type")=="endEvent"]
        if incoming or len(anchors)!=1:return False
        outgoing={identifier:[] for identifier in local}
        for flow in local_flows:outgoing[flow.get("source")].append(flow.get("target"))
        reachable=set();queue=[endpoint_id]
        while queue:
            current=queue.pop(0)
            if current in reachable:continue
            reachable.add(current);queue.extend(outgoing.get(current,[]))
        if anchor_id in reachable or endpoint_id in outgoing.get(anchor_id,[]):
            report["artificial_cycles_rejected"].append({"process":process_id,"source":anchor_id,"target":endpoint_id,"status":"rollback"})
            return False
        anchor=anchors[0];old_target=anchor.get("target");anchor["target"]=endpoint_id
        if not cls._valid_local_topology(candidate,process_id,endpoint_id):return False
        workflow["flows"]=candidate["flows"];used.clear();used.update(cls._all_ids(workflow))
        detail={"process":process_id,"orphan_nodes":sorted(reachable-{old_target}),"resume_endpoint":endpoint_id,
                "previous_node":anchor_id,"next_node":endpoint_id,"removed_flow":anchor.get("id"),
                "created_flows":[anchor.get("id")],"status":"committed"}
        report["process_continuity_repairs"].append(detail)
        report["premature_end_repairs"].append({**detail,"previous_end":old_target})
        return True

    @classmethod
    def _ensure_local_process_continuity(cls,workflow,report):
        """Attach one uniquely evidenced continuation after an incoming receive."""
        process_ids=[p.get("id") for p in workflow.get("processes",[]) if isinstance(p,Mapping)]
        business_types={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask","subProcess"}
        for process_id in process_ids:
            candidate=copy.deepcopy(workflow);nodes=[n for n in candidate.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
            local={n.get("id"):n for n in nodes};flows=[f for f in candidate.get("flows",[]) if isinstance(f,dict) and f.get("source") in local and f.get("target") in local]
            starts=[n.get("id") for n in nodes if n.get("type")=="startEvent"]
            if not starts:continue
            outgoing={identifier:[] for identifier in local};incoming={identifier:[] for identifier in local}
            for flow in flows:outgoing[flow.get("source")].append(flow.get("target"));incoming[flow.get("target")].append(flow.get("source"))
            reachable=set();queue=list(starts)
            while queue:
                current=queue.pop(0)
                if current in reachable:continue
                reachable.add(current);queue.extend(outgoing.get(current,[]))
            orphan_business={identifier for identifier,node in local.items() if node.get("type") in business_types and identifier not in reachable}
            if not orphan_business:continue
            undirected={identifier:set() for identifier in orphan_business}
            for source in orphan_business:
                for target in outgoing.get(source,[]):
                    if target in orphan_business:undirected[source].add(target);undirected[target].add(source)
            components=[];remaining=set(orphan_business)
            while remaining:
                component=set();queue=[min(remaining)]
                while queue:
                    current=queue.pop(0)
                    if current in component:continue
                    component.add(current);queue.extend(undirected.get(current,set())-component)
                remaining-=component;components.append(component)
            report["orphan_business_components"].append({"process":process_id,"components":[sorted(c) for c in components]})
            if len(components)!=1:continue
            component=components[0];roots=[identifier for identifier in component if not (set(incoming.get(identifier,[]))&component)]
            if len(roots)!=1:continue
            root=roots[0]
            message_targets={m.get("target") for m in candidate.get("messageFlows",[]) if isinstance(m,Mapping)}
            resumes=[]
            for identifier in reachable&message_targets:
                node=local.get(identifier,{})
                end_flows=[f for f in flows if f.get("source")==identifier and local.get(f.get("target"),{}).get("type")=="endEvent"]
                if node.get("type")=="receiveTask" and len(outgoing.get(identifier,[]))==1 and len(end_flows)==1:
                    resumes.append((identifier,end_flows[0]))
            if len(resumes)!=1:continue
            resume,anchor=resumes[0]
            component_reachable=set();queue=[root]
            while queue:
                current=queue.pop(0)
                if current in component_reachable:continue
                component_reachable.add(current);queue.extend(outgoing.get(current,[]))
            if resume in component_reachable or resume in outgoing.get(root,[]):
                report["artificial_cycles_rejected"].append({"process":process_id,"source":resume,"target":root,"status":"rollback"})
                continue
            old_end=anchor.get("target");anchor["target"]=root
            if not cls._valid_local_topology(candidate,process_id,root):continue
            workflow["flows"]=candidate["flows"]
            detail={"process":process_id,"orphan_nodes":sorted(component),"resume_endpoint":resume,
                    "previous_node":resume,"next_node":root,"removed_flow":anchor.get("id"),
                    "created_flows":[anchor.get("id")],"status":"committed"}
            report["process_continuity_repairs"].append(detail)
            report["premature_end_repairs"].append({**detail,"previous_end":old_end})

    @classmethod
    def _repair_artificial_two_node_cycles(cls,workflow,requirements,report):
        """Remove only synthetic reverse edges when grounded activity order proves direction."""
        raw_activities=requirements.get("activities",[]) if isinstance(requirements,Mapping) else []
        activities=[item for item in raw_activities if isinstance(item,Mapping) and item.get("name")]
        if len(activities)<2:return
        for process in [p for p in workflow.get("processes",[]) if isinstance(p,Mapping)]:
            process_id=process.get("id");candidate=copy.deepcopy(workflow)
            nodes=[n for n in candidate.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
            by_id={n.get("id"):n for n in nodes};flows=[f for f in candidate.get("flows",[]) if isinstance(f,dict) and f.get("source") in by_id and f.get("target") in by_id]
            positions={}
            for index,activity in enumerate(activities):
                matches=[n for n in nodes if semantic_activity_score(activity,n).get("action_match") and semantic_activity_score(activity,n).get("object_match") is not False]
                if len(matches)==1:positions.setdefault(matches[0].get("id"),index)
            pairs={(f.get("source"),f.get("target")):f for f in flows}
            repaired=False
            for (source,target),forward in list(pairs.items()):
                reverse=pairs.get((target,source))
                if reverse is None or source not in positions or target not in positions or positions[source]>=positions[target]:continue
                removable=reverse if reverse.get("properties",{}).get("synthetic") else None
                if removable is None:continue
                candidate["flows"].remove(removable);repaired=True
                outgoing=[f for f in candidate["flows"] if isinstance(f,Mapping) and f.get("source")==target]
                ends=[n for n in nodes if n.get("type")=="endEvent"]
                if not outgoing and len(ends)==1:
                    cls._add_flow(candidate["flows"],cls._all_ids(candidate),target,ends[0].get("id"),process_id)
                if cls._valid_local_topology(candidate,process_id,target):
                    workflow["flows"]=candidate["flows"]
                    report["artificial_cycles_rejected"].append({"process":process_id,"source":target,"target":source,
                                                                  "flow":removable.get("id"),"status":"removed"})
                else:repaired=False
                break
            if repaired:continue

    @staticmethod
    def _valid_existing_endpoint(nodes,flows,node,process_id):
        if node.get("type") not in {"sendTask","receiveTask","userTask","serviceTask","manualTask","scriptTask","businessRuleTask"}:
            return False,"node type is not reusable"
        local={n.get("id"):n for n in nodes if isinstance(n,Mapping) and n.get("processRef")==process_id}
        def endpoints(flow):
            return flow.get("source",flow.get("sourceRef")),flow.get("target",flow.get("targetRef"))
        local_flows=[f for f in flows if isinstance(f,Mapping) and endpoints(f)[0] in local and endpoints(f)[1] in local]
        incoming={node_id:[] for node_id in local};outgoing={node_id:[] for node_id in local}
        for flow in local_flows:
            source,target=endpoints(flow);incoming[target].append(source);outgoing[source].append(target)
        node_id=node.get("id")
        if not incoming.get(node_id):return False,"existing endpoint has no local incoming sequenceFlow"
        if not outgoing.get(node_id):return False,"existing endpoint has no local outgoing sequenceFlow"
        starts=[identifier for identifier,value in local.items() if value.get("type")=="startEvent"]
        ends={identifier for identifier,value in local.items() if value.get("type")=="endEvent"}
        reachable=set();queue=list(starts)
        while queue:
            current=queue.pop(0)
            if current in reachable:continue
            reachable.add(current);queue.extend(outgoing.get(current,[]))
        to_end=set();queue=list(ends)
        while queue:
            current=queue.pop(0)
            if current in to_end:continue
            to_end.add(current);queue.extend(incoming.get(current,[]))
        if node_id not in reachable:return False,"existing node is not reachable from a start event"
        if node_id not in to_end:return False,"existing node has no path to an end event"
        return True,"existing node already connected"

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
        continuation=None
        if node_type=="receiveTask" and not cls._valid_local_topology(candidate,process_id,endpoint["id"]):
            continuation=cls._splice_unique_orphan_after_receive(candidate,process_id,endpoint["id"])
        if not cls._valid_local_topology(candidate,process_id,endpoint["id"]):
            report["interaction_endpoint_rollbacks"].append({"node":name,"process":process_id,"reason":"Local topology validation failed"})
            report["rewiring_validation_failures"].append({"node":endpoint["id"],"process":process_id})
            return None
        workflow["nodes"]=candidate["nodes"];workflow["flows"]=candidate["flows"]
        used.clear();used.update(candidate_used)
        report["nodes_inserted"].append({"id":endpoint["id"],"processRef":process_id,"position":position})
        report["interaction_endpoint_insertions"].append({"id":endpoint["id"],"process":process_id,"position":position})
        report["flows_rewired"].append({"id":anchor.get("id"),"inserted_node":endpoint["id"],"new_flow":new_flow["id"]})
        if continuation:
            detail={"process":process_id,"orphan_nodes":continuation["orphan_nodes"],"resume_endpoint":endpoint["id"],
                    "previous_node":endpoint["id"],"next_node":continuation["root"],"removed_flow":continuation["flow"],
                    "created_flows":[continuation["flow"]],"status":"committed"}
            report["process_continuity_repairs"].append(detail)
            report["premature_end_repairs"].append({**detail,"previous_end":continuation["end"]})
        return endpoint

    @staticmethod
    def _splice_unique_orphan_after_receive(workflow,process_id,receive_id):
        nodes=[n for n in workflow.get("nodes",[]) if isinstance(n,Mapping) and n.get("processRef")==process_id]
        local={n.get("id"):n for n in nodes};flows=[f for f in workflow.get("flows",[]) if isinstance(f,dict) and f.get("source") in local and f.get("target") in local]
        outgoing={identifier:[] for identifier in local};incoming={identifier:[] for identifier in local}
        for flow in flows:outgoing[flow.get("source")].append(flow.get("target"));incoming[flow.get("target")].append(flow.get("source"))
        starts=[identifier for identifier,node in local.items() if node.get("type")=="startEvent"]
        reachable=set();queue=list(starts)
        while queue:
            current=queue.pop(0)
            if current in reachable:continue
            reachable.add(current);queue.extend(outgoing.get(current,[]))
        business_types={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask","subProcess"}
        orphan={identifier for identifier,node in local.items() if node.get("type") in business_types and identifier not in reachable}
        if not orphan:return None
        undirected={identifier:set() for identifier in orphan}
        for source in orphan:
            for target in outgoing.get(source,[]):
                if target in orphan:undirected[source].add(target);undirected[target].add(source)
        visited=set();components=[]
        for identifier in sorted(orphan):
            if identifier in visited:continue
            component=set();queue=[identifier]
            while queue:
                current=queue.pop(0)
                if current in component:continue
                component.add(current);queue.extend(undirected.get(current,set())-component)
            visited|=component;components.append(component)
        if len(components)!=1:return None
        component=components[0];roots=[identifier for identifier in component if not (set(incoming.get(identifier,[]))&component)]
        anchors=[f for f in flows if f.get("source")==receive_id and local.get(f.get("target"),{}).get("type")=="endEvent"]
        if len(roots)!=1 or len(anchors)!=1:return None
        root=roots[0]
        if receive_id in outgoing.get(root,[]) or root==receive_id:return None
        anchor=anchors[0];old_end=anchor.get("target");anchor["target"]=root
        return {"root":root,"orphan_nodes":sorted(component),"flow":anchor.get("id"),"end":old_end}

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
