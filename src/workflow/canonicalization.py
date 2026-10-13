"""Deterministic construction invariants derived from grounded requirements."""
from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .coverage_validator import semantic_label_match


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
        }
        cls._ensure_participant_processes(value, requirements, report)
        cls._ensure_required_lanes(value, requirements)
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
            matches = [p for p in participants if isinstance(p, dict) and semantic_label_match(name, p.get("name"))]
            if matches:
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
    def _ensure_required_lanes(cls, workflow, requirements):
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
            if any(isinstance(lane, Mapping) and lane.get("participantRef") == participant.get("id")
                   and semantic_label_match(name, lane.get("name")) for lane in lanes): continue
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
        for interaction in interactions:
            if not isinstance(interaction, Mapping): continue
            sources = [p for p in participants if semantic_label_match(interaction.get("source"), p.get("name"))]
            targets = [p for p in participants if semantic_label_match(interaction.get("target"), p.get("name"))]
            if len(sources) != 1 or len(targets) != 1 or sources[0].get("processRef") == targets[0].get("processRef"): continue
            source_node = cls._message_endpoint(nodes, messages, sources[0], "source", "sendTask")
            target_node = cls._message_endpoint(nodes, messages, targets[0], "target", "receiveTask")
            if source_node is None:
                source_node = cls._insert_endpoint(workflow, sources[0], "sendTask", f"Send to {targets[0].get('name')}", used)
                report["interaction_endpoints_added"].append({"id": source_node["id"], "role": "source"})
            if target_node is None:
                target_node = cls._insert_endpoint(workflow, targets[0], "receiveTask", f"Receive from {sources[0].get('name')}", used)
                report["interaction_endpoints_added"].append({"id": target_node["id"], "role": "target"})
            pair = (source_node["id"], target_node["id"])
            if not any(isinstance(m, Mapping) and (m.get("source"), m.get("target")) == pair for m in messages):
                message_id = cls._unique_id("message_flow", used); used.add(message_id)
                messages.append({"id": message_id, "source": pair[0], "target": pair[1], "properties": {}})

    @staticmethod
    def _message_endpoint(nodes, messages, participant, side, node_type):
        process_id = participant.get("processRef")
        endpoint_ids = {m.get(side) for m in messages if isinstance(m, Mapping)}
        candidates = [n for n in nodes if isinstance(n, dict) and n.get("processRef") == process_id
                      and (n.get("type") == node_type or n.get("id") in endpoint_ids)]
        return candidates[0] if len(candidates) == 1 else None

    @classmethod
    def _insert_endpoint(cls, workflow, participant, node_type, name, used):
        nodes, flows = workflow["nodes"], workflow["flows"]
        process_id, participant_id = participant.get("processRef"), participant.get("id")
        local = [n for n in nodes if isinstance(n, Mapping) and n.get("processRef") == process_id]
        start = next((n for n in local if n.get("type") == "startEvent"), None)
        end = next((n for n in local if n.get("type") == "endEvent"), None)
        endpoint = cls._add_node(nodes, used, f"{node_type}_{process_id}", node_type, name, process_id, participant_id)
        direct = next((f for f in flows if isinstance(f, dict) and start and end and
                       f.get("source") == start.get("id") and f.get("target") == end.get("id")), None)
        if direct:
            direct["target"] = endpoint["id"]
            cls._add_flow(flows, used, endpoint["id"], end["id"], process_id)
        elif start and not any(isinstance(f, Mapping) and f.get("source") == start.get("id") for f in flows):
            cls._add_flow(flows, used, start["id"], endpoint["id"], process_id)
        if end and not any(isinstance(f, Mapping) and f.get("target") == end.get("id") for f in flows):
            cls._add_flow(flows, used, endpoint["id"], end["id"], process_id)
        return endpoint

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
