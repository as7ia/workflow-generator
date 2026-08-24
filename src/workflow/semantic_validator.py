"""Semantic validation for canonical multi-process workflow JSON v2."""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping
from typing import Any


ValidationIssue = dict[str, Any]


class SemanticValidator:
    """Detect v2 ownership, collaboration and process-scoped graph errors."""

    _GATEWAY_TYPES = {
        "exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway"
    }
    _CONDITIONAL_GATEWAYS = {"exclusiveGateway", "inclusiveGateway"}
    _TASK_TYPES = {
        "userTask", "serviceTask", "manualTask", "scriptTask",
        "businessRuleTask", "sendTask", "receiveTask",
    }
    _TIMER_KINDS = {"duration", "date", "cycle"}
    _ISO_DURATION = re.compile(
        r"^P(?=\d|T\d)(?:\d+(?:[.,]\d+)?Y)?(?:\d+(?:[.,]\d+)?M)?"
        r"(?:\d+(?:[.,]\d+)?W)?(?:\d+(?:[.,]\d+)?D)?"
        r"(?:T(?=\d)(?:\d+(?:[.,]\d+)?H)?(?:\d+(?:[.,]\d+)?M)?"
        r"(?:\d+(?:[.,]\d+)?S)?)?$",
        re.IGNORECASE,
    )

    def validate(self, workflow: dict[str, Any]) -> list[ValidationIssue]:
        """Return deterministic issues without mutating or repairing *workflow*."""
        if not isinstance(workflow, Mapping):
            return [self._issue("INVALID_WORKFLOW", "Le workflow doit être un objet.")]

        issues: list[ValidationIssue] = []
        processes = self._read_collection(workflow, "processes", "PROCESS", issues)
        participants = self._read_collection(workflow, "participants", "PARTICIPANT", issues)
        lanes = self._read_collection(workflow, "lanes", "LANE", issues)
        nodes = self._read_collection(workflow, "nodes", "NODE", issues)
        flows = self._read_collection(workflow, "flows", "FLOW", issues)
        message_flows = self._read_collection(workflow, "messageFlows", "MESSAGE_FLOW", issues)

        process_by_id = self._index(processes, "DUPLICATE_PROCESS_ID", "process", issues)
        participant_by_id = self._index(participants, "DUPLICATE_PARTICIPANT_ID", "participant", issues)
        lane_by_id = self._index(lanes, "DUPLICATE_LANE_ID", "lane", issues)
        node_by_id = self._index(nodes, "DUPLICATE_NODE_ID", "node", issues)
        flow_by_id = self._index(flows, "DUPLICATE_FLOW_ID", "flow", issues)
        message_flow_by_id = self._index(
            message_flows, "DUPLICATE_MESSAGE_FLOW_ID", "message_flow", issues
        )

        self._validate_processes(process_by_id, issues)
        participants_by_process = self._validate_participants(
            participants, process_by_id, participant_by_id, issues
        )
        lanes_by_process = self._validate_lanes(
            lanes, process_by_id, participant_by_id, lane_by_id, issues
        )
        nodes_by_process = self._validate_nodes(
            nodes, process_by_id, participant_by_id, lane_by_id, issues
        )
        flows_by_process = self._validate_sequence_flows(
            flows, process_by_id, node_by_id, issues
        )
        self._validate_message_flows(message_flows, node_by_id, issues)
        self._validate_timers(nodes, issues)

        # Materialize every requested deterministic index, including empty scopes.
        for process_id in sorted(process_by_id):
            participants_by_process.setdefault(process_id, [])
            lanes_by_process.setdefault(process_id, [])
            nodes_by_process.setdefault(process_id, [])
            flows_by_process.setdefault(process_id, [])
        del flow_by_id, message_flow_by_id, participants_by_process, lanes_by_process

        for process_id in sorted(process_by_id):
            scoped_nodes = nodes_by_process[process_id]
            if scoped_nodes:
                self._validate_process_graph(
                    process_id, scoped_nodes, flows_by_process[process_id], issues
                )

        return sorted(issues, key=self._issue_sort_key)

    def _read_collection(self, workflow, key, label, issues):
        raw = workflow.get(key)
        if not isinstance(raw, list):
            missing_code = "MISSING_PROCESSES" if label == "PROCESS" else f"MISSING_{label}S"
            issues.append(self._issue(missing_code, f"La clé '{key}' doit être une liste."))
            return []
        result = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                issues.append(self._issue(f"INVALID_{label}", f"L'élément {key}[{index}] doit être un objet."))
                continue
            result.append(dict(item))
        return result

    def _index(self, items, duplicate_code, kind, issues):
        index: dict[str, dict[str, Any]] = {}
        counts: dict[Hashable, int] = {}
        for position, item in enumerate(items):
            identifier = item.get("id")
            if not self._usable_id(identifier):
                issues.append(self._issue(f"MISSING_{kind.upper()}_ID", f"Le {kind} à l'index {position} n'a pas d'identifiant valide."))
                continue
            counts[identifier] = counts.get(identifier, 0) + 1
            index.setdefault(str(identifier), item)
        for identifier in sorted(counts, key=str):
            if counts[identifier] > 1:
                kwargs = self._identifier_context(kind, str(identifier))
                issues.append(self._issue(duplicate_code, f"L'identifiant de {kind} '{identifier}' n'est pas unique.", **kwargs))
        return index

    @staticmethod
    def _identifier_context(kind, identifier):
        if kind == "process":
            return {"process_id": identifier}
        if kind == "participant":
            return {"participant_id": identifier}
        if kind == "lane":
            return {"lane_id": identifier}
        if kind == "node":
            return {"node_id": identifier}
        return {"flow_id": identifier}

    def _validate_processes(self, process_by_id, issues):
        for process_id in sorted(process_by_id):
            process = process_by_id[process_id]
            name = process.get("name")
            if not isinstance(name, str) or not name.strip():
                issues.append(self._issue("MISSING_PROCESS_NAME", f"Le process '{process_id}' n'a pas de nom.", process_id=process_id))

    def _validate_participants(self, participants, process_by_id, participant_by_id, issues):
        by_process: dict[str, list[dict[str, Any]]] = {}
        for participant in participants:
            participant_id = self._id(participant)
            if participant_id is None or participant_by_id.get(participant_id) is not participant:
                continue
            process_ref = participant.get("processRef")
            if process_ref not in process_by_id:
                issues.append(self._issue(
                    "UNKNOWN_PARTICIPANT_PROCESS",
                    f"Le participant '{participant_id}' référence un process inconnu '{process_ref}'.",
                    process_id=self._string_or_none(process_ref), participant_id=participant_id,
                ))
                continue
            by_process.setdefault(str(process_ref), []).append(participant)
        for process_id, members in sorted(by_process.items()):
            if len(members) > 1:
                issues.append(self._issue(
                    "MULTIPLE_PARTICIPANTS_FOR_PROCESS",
                    f"Le process '{process_id}' est référencé par plusieurs participants/pools.",
                    process_id=process_id,
                ))
        return by_process

    def _validate_lanes(self, lanes, process_by_id, participant_by_id, lane_by_id, issues):
        by_process: dict[str, list[dict[str, Any]]] = {}
        for lane in lanes:
            lane_id = self._id(lane)
            if lane_id is None or lane_by_id.get(lane_id) is not lane:
                continue
            process_ref = lane.get("processRef")
            participant_ref = lane.get("participantRef")
            if process_ref not in process_by_id:
                issues.append(self._issue(
                    "UNKNOWN_LANE_PROCESS", f"La lane '{lane_id}' référence un process inconnu '{process_ref}'.",
                    process_id=self._string_or_none(process_ref), lane_id=lane_id,
                ))
            else:
                by_process.setdefault(str(process_ref), []).append(lane)
            if participant_ref is None:
                continue
            participant = participant_by_id.get(str(participant_ref))
            if participant is None:
                issues.append(self._issue(
                    "UNKNOWN_LANE_PARTICIPANT", f"La lane '{lane_id}' référence un participant inconnu '{participant_ref}'.",
                    process_id=self._string_or_none(process_ref), participant_id=self._string_or_none(participant_ref), lane_id=lane_id,
                ))
            elif participant.get("processRef") != process_ref:
                issues.append(self._issue(
                    "LANE_PROCESS_MISMATCH", f"La lane '{lane_id}' et son participant n'appartiennent pas au même process.",
                    process_id=self._string_or_none(process_ref), participant_id=str(participant_ref), lane_id=lane_id,
                ))
        return by_process

    def _validate_nodes(self, nodes, process_by_id, participant_by_id, lane_by_id, issues):
        by_process: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for node in nodes:
            node_id = self._id(node)
            if node_id is None or node_id in seen:
                continue
            seen.add(node_id)
            process_ref = node.get("processRef")
            participant_ref = node.get("participantRef")
            lane_ref = node.get("laneRef")
            process_known = process_ref in process_by_id
            if not process_known:
                issues.append(self._issue(
                    "UNKNOWN_NODE_PROCESS", f"Le nœud '{node_id}' référence un process inconnu '{process_ref}'.",
                    node_id=node_id, process_id=self._string_or_none(process_ref),
                ))
            else:
                by_process.setdefault(str(process_ref), []).append(node)

            participant = participant_by_id.get(str(participant_ref)) if participant_ref is not None else None
            if participant_ref is not None and participant is None:
                issues.append(self._issue(
                    "UNKNOWN_NODE_PARTICIPANT", f"Le nœud '{node_id}' référence un participant inconnu '{participant_ref}'.",
                    node_id=node_id, process_id=self._string_or_none(process_ref), participant_id=self._string_or_none(participant_ref),
                ))
            elif participant is not None and participant.get("processRef") != process_ref:
                issues.append(self._issue(
                    "NODE_PARTICIPANT_PROCESS_MISMATCH", f"Le nœud '{node_id}' et son participant n'appartiennent pas au même process.",
                    node_id=node_id, process_id=self._string_or_none(process_ref), participant_id=str(participant_ref),
                ))

            lane = lane_by_id.get(str(lane_ref)) if lane_ref is not None else None
            if lane_ref is not None and lane is None:
                issues.append(self._issue(
                    "UNKNOWN_NODE_LANE", f"Le nœud '{node_id}' référence une lane inconnue '{lane_ref}'.",
                    node_id=node_id, process_id=self._string_or_none(process_ref), lane_id=self._string_or_none(lane_ref),
                ))
            elif lane is not None:
                if lane.get("processRef") != process_ref:
                    issues.append(self._issue(
                        "NODE_LANE_PROCESS_MISMATCH", f"Le nœud '{node_id}' et sa lane n'appartiennent pas au même process.",
                        node_id=node_id, process_id=self._string_or_none(process_ref), lane_id=str(lane_ref),
                    ))
                lane_participant = lane.get("participantRef")
                if lane_participant is not None and participant_ref is not None and lane_participant != participant_ref:
                    issues.append(self._issue(
                        "NODE_LANE_PARTICIPANT_MISMATCH", f"Le nœud '{node_id}' et sa lane ne référencent pas le même participant.",
                        node_id=node_id, process_id=self._string_or_none(process_ref), participant_id=str(participant_ref), lane_id=str(lane_ref),
                    ))
        return by_process

    def _validate_sequence_flows(self, flows, process_by_id, node_by_id, issues):
        by_process: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for flow in flows:
            flow_id = self._id(flow)
            if flow_id is None or flow_id in seen:
                continue
            seen.add(flow_id)
            source = flow.get("source")
            target = flow.get("target")
            process_ref = flow.get("processRef")
            source_node = self._validate_endpoint(flow_id, source, node_by_id, "SOURCE", False, issues)
            target_node = self._validate_endpoint(flow_id, target, node_by_id, "TARGET", False, issues)
            process_known = process_ref in process_by_id
            if not process_known:
                issues.append(self._issue(
                    "UNKNOWN_FLOW_PROCESS", f"Le sequence flow '{flow_id}' référence un process inconnu '{process_ref}'.",
                    flow_id=flow_id, process_id=self._string_or_none(process_ref),
                ))
            coherent = source_node is not None and target_node is not None and process_known
            if source_node is not None and target_node is not None:
                source_process = source_node.get("processRef")
                target_process = target_node.get("processRef")
                if source_process != target_process:
                    issues.append(self._issue(
                        "SEQUENCE_FLOW_CROSSES_PROCESSES", f"Le sequence flow '{flow_id}' traverse deux process.",
                        flow_id=flow_id, process_id=self._string_or_none(process_ref),
                    ))
                    coherent = False
                if source_process != process_ref or target_process != process_ref:
                    issues.append(self._issue(
                        "FLOW_PROCESS_MISMATCH", f"Le process du sequence flow '{flow_id}' ne correspond pas à ses extrémités.",
                        flow_id=flow_id, process_id=self._string_or_none(process_ref),
                    ))
                    coherent = False
            if coherent:
                by_process.setdefault(str(process_ref), []).append(flow)
        return by_process

    def _validate_message_flows(self, flows, node_by_id, issues):
        seen: set[str] = set()
        for flow in flows:
            flow_id = self._id(flow)
            if flow_id is None or flow_id in seen:
                continue
            seen.add(flow_id)
            source_node = self._validate_endpoint(flow_id, flow.get("source"), node_by_id, "SOURCE", True, issues)
            target_node = self._validate_endpoint(flow_id, flow.get("target"), node_by_id, "TARGET", True, issues)
            if source_node is not None and target_node is not None and source_node.get("processRef") == target_node.get("processRef"):
                issues.append(self._issue(
                    "MESSAGE_FLOW_WITHIN_SAME_PROCESS", f"Le message flow '{flow_id}' reste dans le même process.",
                    flow_id=flow_id, process_id=self._string_or_none(source_node.get("processRef")),
                ))

    def _validate_endpoint(self, flow_id, endpoint, node_by_id, side, message, issues):
        prefix = "MESSAGE_FLOW_" if message else "FLOW_"
        if not self._usable_id(endpoint):
            issues.append(self._issue(f"MISSING_{prefix}{side}", f"L'extrémité {side.lower()} du flux '{flow_id}' est absente.", flow_id=flow_id))
            return None
        node = node_by_id.get(str(endpoint))
        if node is None:
            issues.append(self._issue(f"UNKNOWN_{prefix}{side}", f"L'extrémité '{endpoint}' du flux '{flow_id}' est inconnue.", flow_id=flow_id))
        return node

    def _validate_timers(self, nodes, issues):
        for node in nodes:
            node_id = self._id(node)
            properties = node.get("properties")
            if node_id is None or not isinstance(properties, Mapping) or "timer" not in properties:
                continue
            timer = properties.get("timer")
            process_id = self._string_or_none(node.get("processRef"))
            if not isinstance(timer, Mapping):
                issues.append(self._issue("INVALID_TIMER_KIND", f"Le timer du nœud '{node_id}' doit être un objet.", node_id=node_id, process_id=process_id))
                issues.append(self._issue("MISSING_TIMER_VALUE", f"Le timer du nœud '{node_id}' ne possède pas de valeur.", node_id=node_id, process_id=process_id))
                continue
            kind = timer.get("kind")
            value = timer.get("value")
            if kind not in self._TIMER_KINDS:
                issues.append(self._issue("INVALID_TIMER_KIND", f"Le timer du nœud '{node_id}' possède un kind invalide '{kind}'.", node_id=node_id, process_id=process_id))
            if not isinstance(value, str) or not value.strip():
                issues.append(self._issue("MISSING_TIMER_VALUE", f"Le timer du nœud '{node_id}' ne possède pas de valeur.", node_id=node_id, process_id=process_id))
            elif kind == "duration" and self._ISO_DURATION.fullmatch(value) is None:
                issues.append(self._issue("INVALID_TIMER_DURATION", f"La durée du timer du nœud '{node_id}' n'est pas au format ISO 8601.", node_id=node_id, process_id=process_id))

    def _validate_process_graph(self, process_id, nodes, flows, issues):
        node_by_id = {str(node["id"]): node for node in nodes if self._usable_id(node.get("id"))}
        adjacency = {node_id: [] for node_id in node_by_id}
        reverse = {node_id: [] for node_id in node_by_id}
        by_source: dict[str, list[dict[str, Any]]] = {}
        for flow in flows:
            source, target = str(flow["source"]), str(flow["target"])
            adjacency[source].append(target)
            reverse[target].append(source)
            by_source.setdefault(source, []).append(flow)
        for mapping in (adjacency, reverse):
            for values in mapping.values():
                values.sort()
        for values in by_source.values():
            values.sort(key=lambda item: str(item.get("id", "")))

        starts = {node_id for node_id, node in node_by_id.items() if node.get("type") == "startEvent"}
        ends = {node_id for node_id, node in node_by_id.items() if node.get("type") == "endEvent"}
        if not starts:
            issues.append(self._issue("PROCESS_MISSING_START_EVENT", f"Le process '{process_id}' ne contient aucun startEvent.", process_id=process_id))
        if not ends:
            issues.append(self._issue("PROCESS_MISSING_END_EVENT", f"Le process '{process_id}' ne contient aucun endEvent.", process_id=process_id))

        for node_id, node in sorted(node_by_id.items()):
            node_type = node.get("type")
            if node_type != "startEvent" and not reverse[node_id]:
                issues.append(self._issue("MISSING_INCOMING_FLOW", f"Le nœud '{node_id}' n'a aucun flux entrant dans son process.", node_id=node_id, process_id=process_id))
            if node_type != "endEvent" and not adjacency[node_id]:
                issues.append(self._issue("MISSING_OUTGOING_FLOW", f"Le nœud '{node_id}' n'a aucun flux sortant dans son process.", node_id=node_id, process_id=process_id))
            if node_type in self._TASK_TYPES and not reverse[node_id] and not adjacency[node_id]:
                issues.append(self._issue("ISOLATED_TASK", f"La tâche '{node_id}' est isolée dans son process.", node_id=node_id, process_id=process_id))

        reachable = self._walk(starts, adjacency)
        for node_id in sorted(set(node_by_id) - reachable):
            issues.append(self._issue("UNREACHABLE_NODE", f"Le nœud '{node_id}' est inaccessible dans le process '{process_id}'.", node_id=node_id, process_id=process_id))
        can_reach_end = self._walk(ends, reverse)
        for node_id in sorted(reachable - can_reach_end):
            issues.append(self._issue("NO_PATH_TO_END", f"Le nœud '{node_id}' ne rejoint aucun endEvent de son process.", node_id=node_id, process_id=process_id))

        self._validate_gateways(process_id, node_by_id, adjacency, reverse, by_source, issues)
        self._validate_dead_branches(process_id, node_by_id, adjacency, can_reach_end, issues)
        self._validate_cycles(process_id, adjacency, issues)

    def _validate_gateways(self, process_id, nodes, adjacency, reverse, by_source, issues):
        for node_id, node in sorted(nodes.items()):
            node_type = node.get("type")
            if node_type not in self._GATEWAY_TYPES:
                continue
            properties = node.get("properties")
            direction = properties.get("gatewayDirection") if isinstance(properties, Mapping) else None
            outputs = by_source.get(node_id, [])
            if direction == "diverging" and len(outputs) < 2:
                issues.append(self._issue("GATEWAY_SPLIT_TOO_FEW_OUTPUTS", f"La passerelle '{node_id}' doit avoir au moins deux sorties.", node_id=node_id, process_id=process_id))
            if direction == "converging" and len(reverse[node_id]) < 2:
                issues.append(self._issue("GATEWAY_JOIN_TOO_FEW_INPUTS", f"La convergence '{node_id}' doit avoir au moins deux entrées.", node_id=node_id, process_id=process_id))
            if node_type in self._CONDITIONAL_GATEWAYS and len(outputs) > 1:
                for flow in outputs:
                    condition = flow.get("condition")
                    if not flow.get("isDefault", False) and (not isinstance(condition, str) or not condition.strip()):
                        code = "MISSING_EXCLUSIVE_CONDITION" if node_type == "exclusiveGateway" else "MISSING_INCLUSIVE_CONDITION"
                        issues.append(self._issue(code, f"Le flux '{flow['id']}' sortant de '{node_id}' n'a pas de condition.", node_id=node_id, flow_id=str(flow["id"]), process_id=process_id))
            if node_type == "parallelGateway":
                for flow in outputs:
                    if isinstance(flow.get("condition"), str) and flow["condition"].strip():
                        issues.append(self._issue("PARALLEL_GATEWAY_CONDITION", f"Le flux '{flow['id']}' d'une parallelGateway ne doit pas avoir de condition.", node_id=node_id, flow_id=str(flow["id"]), process_id=process_id))

    def _validate_dead_branches(self, process_id, nodes, adjacency, can_reach_end, issues):
        for node_id, targets in sorted(adjacency.items()):
            if nodes[node_id].get("type") != "startEvent" and len(targets) <= 1:
                continue
            for target in targets:
                if target not in can_reach_end:
                    issues.append(self._issue("DEAD_END_BRANCH", f"La branche de '{node_id}' vers '{target}' ne rejoint aucun endEvent du process.", node_id=target, process_id=process_id))

    def _validate_cycles(self, process_id, adjacency, issues):
        state: dict[str, int] = {}
        stack: list[str] = []
        reported: set[frozenset[str]] = set()

        def visit(node_id):
            state[node_id] = 1
            stack.append(node_id)
            for target in adjacency[node_id]:
                if state.get(target, 0) == 0:
                    visit(target)
                elif state.get(target) == 1:
                    cycle = frozenset(stack[stack.index(target):])
                    if cycle not in reported:
                        reported.add(cycle)
                        issues.append(self._issue("CYCLE_DETECTED", f"Une boucle a été détectée dans le process '{process_id}'.", node_id=target, process_id=process_id, severity="warning"))
            stack.pop()
            state[node_id] = 2

        for node_id in sorted(adjacency):
            if state.get(node_id, 0) == 0:
                visit(node_id)

    @staticmethod
    def _walk(starts, adjacency):
        visited = set()
        pending = sorted(starts, reverse=True)
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(reversed(adjacency.get(current, [])))
        return visited

    @staticmethod
    def _id(item):
        value = item.get("id")
        return str(value) if SemanticValidator._usable_id(value) else None

    @staticmethod
    def _usable_id(value):
        return isinstance(value, Hashable) and not isinstance(value, (dict, list, set)) and value not in (None, "")

    @staticmethod
    def _string_or_none(value):
        return str(value) if value is not None else None

    @staticmethod
    def _issue_sort_key(issue):
        return tuple(str(issue.get(key) or "") for key in (
            "code", "process_id", "participant_id", "lane_id", "node_id", "flow_id", "message"
        ))

    @staticmethod
    def _issue(
        code,
        message,
        node_id=None,
        flow_id=None,
        severity="error",
        process_id=None,
        participant_id=None,
        lane_id=None,
    ):
        return {
            "code": code,
            "message": message,
            "node_id": node_id,
            "flow_id": flow_id,
            "severity": severity,
            "process_id": process_id,
            "participant_id": participant_id,
            "lane_id": lane_id,
        }
