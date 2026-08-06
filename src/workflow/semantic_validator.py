"""Semantic validation of workflow graphs.

This module validates graph-level invariants after JSON Schema validation and
before BPMN generation.  It never mutates the supplied workflow.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from typing import Any


ValidationIssue = dict[str, str | None]


class SemanticValidator:
    """Validate the logical consistency of a BPMN-like workflow.

    Validation is deliberately split between element/reference checks and the
    sequence-flow control graph.  Collaboration elements (participants,
    pools, lanes and message flows) therefore cannot accidentally alter
    control-flow reachability.  Future collaboration and scope rules can be
    added as independent validation families.
    """

    _GATEWAY_TYPES = {"exclusiveGateway", "parallelGateway", "inclusiveGateway"}
    _CONDITIONAL_GATEWAY_TYPES = {"exclusiveGateway", "inclusiveGateway"}
    _TASK_TYPES = {"userTask", "serviceTask", "task", "manualTask", "scriptTask"}
    _SEQUENCE_FLOW_TYPES = {"sequenceFlow", "sequence"}
    _MESSAGE_FLOW_TYPES = {"messageFlow", "message"}

    def validate(self, workflow: dict[str, Any]) -> list[ValidationIssue]:
        """Return semantic issues found in *workflow* without modifying it.

        Errors have severity ``error``. Cycles are reported separately with
        severity ``warning`` because a loop may be intentional in BPMN.
        Malformed or absent structural keys are converted to issues rather
        than being allowed to raise an exception.
        """
        issues: list[ValidationIssue] = []
        if not isinstance(workflow, Mapping):
            return [self._issue("INVALID_WORKFLOW", "Le workflow doit être un objet.")]

        raw_nodes = workflow.get("nodes")
        raw_flows = workflow.get("flows")
        if not isinstance(raw_nodes, list):
            issues.append(self._issue("MISSING_NODES", "La clé 'nodes' doit être une liste."))
            raw_nodes = []
        if not isinstance(raw_flows, list):
            issues.append(self._issue("MISSING_FLOWS", "La clé 'flows' doit être une liste."))
            raw_flows = []

        nodes = self._read_nodes(raw_nodes, issues)
        flows = self._read_flows(raw_flows, issues)
        self._check_required_events(nodes, issues)
        self._check_unique_ids(nodes, flows, issues)

        node_ids = {node["id"] for node in nodes}
        valid_flows = self._check_references(flows, node_ids, issues)
        sequence_flows, message_flows = self._partition_flows(valid_flows, issues)
        self._check_control_flow_graph(nodes, sequence_flows, issues)
        self._check_collaboration_elements(workflow, nodes, message_flows, issues)
        return issues

    def _read_nodes(
        self, raw_nodes: list[Any], issues: list[ValidationIssue]
    ) -> list[dict[str, Any]]:
        """Extract nodes having a usable identifier."""
        nodes: list[dict[str, Any]] = []
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, Mapping):
                issues.append(self._issue("INVALID_NODE", f"Le nœud à l'index {index} doit être un objet."))
                continue
            node_id = raw_node.get("id")
            if not self._usable_id(node_id):
                issues.append(self._issue("MISSING_NODE_ID", f"Le nœud à l'index {index} n'a pas d'identifiant valide."))
                continue
            nodes.append(dict(raw_node))
        return nodes

    def _read_flows(
        self, raw_flows: list[Any], issues: list[ValidationIssue]
    ) -> list[dict[str, Any]]:
        """Extract flows and normalize reference keys in an internal copy."""
        flows: list[dict[str, Any]] = []
        for index, raw_flow in enumerate(raw_flows):
            if not isinstance(raw_flow, Mapping):
                issues.append(self._issue("INVALID_FLOW", f"Le flux à l'index {index} doit être un objet."))
                continue
            flow = dict(raw_flow)
            flow_id = flow.get("id")
            if not self._usable_id(flow_id):
                issues.append(self._issue("MISSING_FLOW_ID", f"Le flux à l'index {index} n'a pas d'identifiant valide."))
                continue
            flow["_source"] = flow.get("source", flow.get("sourceRef"))
            flow["_target"] = flow.get("target", flow.get("targetRef"))
            # The current schema predates explicit flow kinds, so an absent
            # discriminator remains a sequence flow for backward compatibility.
            flow["_kind"] = flow.get("type", flow.get("flowType", "sequenceFlow"))
            flows.append(flow)
        return flows

    def _partition_flows(
        self,
        flows: list[dict[str, Any]],
        issues: list[ValidationIssue],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Separate control-flow edges from collaboration edges.

        This boundary is important for pools: a message flow must never make a
        node reachable in the sequence-flow graph of another pool.
        """
        sequence_flows: list[dict[str, Any]] = []
        message_flows: list[dict[str, Any]] = []
        for flow in flows:
            kind = flow.get("_kind")
            if kind in self._SEQUENCE_FLOW_TYPES:
                sequence_flows.append(flow)
            elif kind in self._MESSAGE_FLOW_TYPES:
                message_flows.append(flow)
            else:
                issues.append(
                    self._issue(
                        "UNSUPPORTED_FLOW_TYPE",
                        f"Le flux '{flow['id']}' possède un type inconnu '{kind}'.",
                        flow_id=str(flow["id"]),
                    )
                )
        return sequence_flows, message_flows

    def _check_control_flow_graph(
        self,
        nodes: list[dict[str, Any]],
        sequence_flows: list[dict[str, Any]],
        issues: list[ValidationIssue],
    ) -> None:
        """Run graph rules using sequence flows only.

        The method is the future insertion point for per-pool and
        per-subprocess graph scopes; the individual graph algorithms do not
        need to know how those scopes are represented in JSON.
        """
        node_ids = {node["id"] for node in nodes}
        outgoing, incoming = self._build_adjacency(node_ids, sequence_flows)
        self._check_degrees(nodes, incoming, outgoing, issues)
        reachable = self._check_reachability(nodes, outgoing, issues)
        can_reach_end = self._check_paths_to_end(nodes, incoming, reachable, issues)
        self._check_gateways(nodes, sequence_flows, outgoing, issues)
        self._check_isolated_tasks(nodes, incoming, outgoing, issues)
        self._check_cycles(node_ids, outgoing, issues)
        self._check_main_branches(nodes, outgoing, can_reach_end, issues)

    def _check_collaboration_elements(
        self,
        workflow: Mapping[str, Any],
        nodes: list[dict[str, Any]],
        message_flows: list[dict[str, Any]],
        issues: list[ValidationIssue],
    ) -> None:
        """Extension point for pool, lane and message-flow semantic rules.

        Endpoint existence is already checked uniformly.  Pool membership and
        cross-pool constraints are intentionally deferred until the JSON
        contract defines their representation.  Layout and routing do not
        belong to semantic validation.
        """
        del workflow, nodes, message_flows, issues

    def _check_required_events(
        self, nodes: list[dict[str, Any]], issues: list[ValidationIssue]
    ) -> None:
        """Require at least one start and one end event."""
        if not any(node.get("type") == "startEvent" for node in nodes):
            issues.append(self._issue("MISSING_START_EVENT", "Le workflow ne contient aucun événement de début."))
        if not any(node.get("type") == "endEvent" for node in nodes):
            issues.append(self._issue("MISSING_END_EVENT", "Le workflow ne contient aucun événement de fin."))

    def _check_unique_ids(
        self,
        nodes: list[dict[str, Any]],
        flows: list[dict[str, Any]],
        issues: list[ValidationIssue],
    ) -> None:
        """Report every repeated node and flow identifier."""
        self._report_duplicates(nodes, "DUPLICATE_NODE_ID", "nœud", "node_id", issues)
        self._report_duplicates(flows, "DUPLICATE_FLOW_ID", "flux", "flow_id", issues)

    def _report_duplicates(
        self,
        items: list[dict[str, Any]],
        code: str,
        label: str,
        id_field: str,
        issues: list[ValidationIssue],
    ) -> None:
        """Append one issue for each identifier occurring more than once."""
        counts: dict[Hashable, int] = {}
        for item in items:
            item_id = item["id"]
            counts[item_id] = counts.get(item_id, 0) + 1
        for item_id, count in counts.items():
            if count > 1:
                kwargs = {id_field: str(item_id)}
                issues.append(self._issue(code, f"L'identifiant de {label} '{item_id}' n'est pas unique.", **kwargs))

    def _check_references(
        self,
        flows: list[dict[str, Any]],
        node_ids: set[Hashable],
        issues: list[ValidationIssue],
    ) -> list[dict[str, Any]]:
        """Validate flow endpoints and return flows safe for graph traversal."""
        valid: list[dict[str, Any]] = []
        for flow in flows:
            flow_id = str(flow["id"])
            source, target = flow.get("_source"), flow.get("_target")
            source_present = self._usable_id(source)
            target_present = self._usable_id(target)
            source_valid = source_present and source in node_ids
            target_valid = target_present and target in node_ids
            if not source_present:
                issues.append(self._issue("MISSING_FLOW_SOURCE", f"La source du flux '{flow_id}' est absente.", flow_id=flow_id))
            elif source not in node_ids:
                issues.append(self._issue("UNKNOWN_FLOW_SOURCE", f"La source '{source}' du flux '{flow_id}' ne correspond à aucun nœud.", flow_id=flow_id))
            if not target_present:
                issues.append(self._issue("MISSING_FLOW_TARGET", f"La cible du flux '{flow_id}' est absente.", flow_id=flow_id))
            elif target not in node_ids:
                issues.append(self._issue("UNKNOWN_FLOW_TARGET", f"La cible '{target}' du flux '{flow_id}' ne correspond à aucun nœud.", flow_id=flow_id))
            if source_valid and target_valid:
                valid.append(flow)
        return valid

    def _build_adjacency(
        self, node_ids: set[Hashable], flows: list[dict[str, Any]]
    ) -> tuple[dict[Hashable, list[Hashable]], dict[Hashable, list[Hashable]]]:
        """Build forward and reverse adjacency lists from valid flows."""
        outgoing = {node_id: [] for node_id in node_ids}
        incoming = {node_id: [] for node_id in node_ids}
        for flow in flows:
            source, target = flow["_source"], flow["_target"]
            outgoing[source].append(target)
            incoming[target].append(source)
        return outgoing, incoming

    def _check_degrees(self, nodes, incoming, outgoing, issues) -> None:
        """Check mandatory incoming and outgoing sequence flows."""
        for node in nodes:
            node_id, node_type = node["id"], node.get("type")
            if node_type != "startEvent" and not incoming[node_id]:
                issues.append(self._issue("MISSING_INCOMING_FLOW", f"Le nœud '{node_id}' n'a aucun flux entrant.", node_id=str(node_id)))
            if node_type != "endEvent" and not outgoing[node_id]:
                issues.append(self._issue("MISSING_OUTGOING_FLOW", f"Le nœud '{node_id}' n'a aucun flux sortant.", node_id=str(node_id)))

    def _check_reachability(self, nodes, outgoing, issues) -> set[Hashable]:
        """Report nodes that cannot be reached from any start event."""
        starts = {node["id"] for node in nodes if node.get("type") == "startEvent"}
        reachable = self._walk(starts, outgoing)
        for node in nodes:
            if node["id"] not in reachable:
                issues.append(self._issue("UNREACHABLE_NODE", f"Le nœud '{node['id']}' est inaccessible depuis un événement de début.", node_id=str(node["id"])))
        return reachable

    def _check_paths_to_end(self, nodes, incoming, reachable, issues) -> set[Hashable]:
        """Report reachable nodes whose branch never leads to an end event."""
        ends = {node["id"] for node in nodes if node.get("type") == "endEvent"}
        can_reach_end = self._walk(ends, incoming)
        for node_id in reachable - can_reach_end:
            issues.append(self._issue("NO_PATH_TO_END", f"La branche passant par '{node_id}' ne rejoint aucun événement de fin.", node_id=str(node_id)))
        return can_reach_end

    def _check_gateways(self, nodes, flows, outgoing, issues) -> None:
        """Validate split cardinality and outgoing gateway conditions."""
        by_source: dict[Hashable, list[dict[str, Any]]] = {}
        for flow in flows:
            by_source.setdefault(flow["_source"], []).append(flow)
        for node in nodes:
            node_id, node_type = node["id"], node.get("type")
            if node_type not in self._GATEWAY_TYPES:
                continue
            direction = node.get("properties", {})
            direction = direction.get("gatewayDirection") if isinstance(direction, Mapping) else None
            outputs = by_source.get(node_id, [])
            if direction == "diverging" and len(outputs) < 2:
                issues.append(self._issue("GATEWAY_SPLIT_TOO_FEW_OUTPUTS", f"La passerelle de séparation '{node_id}' doit avoir au moins deux sorties.", node_id=str(node_id)))
            if node_type in self._CONDITIONAL_GATEWAY_TYPES and len(outputs) > 1:
                for flow in outputs:
                    condition = flow.get("condition")
                    if not flow.get("isDefault", False) and (not isinstance(condition, str) or not condition.strip()):
                        code = "MISSING_EXCLUSIVE_CONDITION" if node_type == "exclusiveGateway" else "MISSING_INCLUSIVE_CONDITION"
                        label = "exclusive" if node_type == "exclusiveGateway" else "inclusive"
                        issues.append(self._issue(code, f"Le flux '{flow['id']}' sortant de la passerelle {label} '{node_id}' n'a pas de condition.", node_id=str(node_id), flow_id=str(flow["id"])))
            if node_type == "parallelGateway":
                for flow in outputs:
                    condition = flow.get("condition")
                    if isinstance(condition, str) and condition.strip():
                        issues.append(self._issue("PARALLEL_GATEWAY_CONDITION", f"Le flux '{flow['id']}' d'une passerelle parallèle ne doit pas avoir de condition.", node_id=str(node_id), flow_id=str(flow["id"])))

    def _check_isolated_tasks(self, nodes, incoming, outgoing, issues) -> None:
        """Report task nodes having neither incoming nor outgoing flows."""
        for node in nodes:
            node_id = node["id"]
            if node.get("type") in self._TASK_TYPES and not incoming[node_id] and not outgoing[node_id]:
                issues.append(self._issue("ISOLATED_TASK", f"La tâche '{node_id}' est isolée.", node_id=str(node_id)))

    def _check_cycles(self, node_ids, outgoing, issues) -> None:
        """Detect directed cycles and emit one warning per cyclic component."""
        state: dict[Hashable, int] = {}
        stack: list[Hashable] = []
        reported: set[frozenset[Hashable]] = set()

        def visit(node_id: Hashable) -> None:
            state[node_id] = 1
            stack.append(node_id)
            for target in outgoing[node_id]:
                if state.get(target, 0) == 0:
                    visit(target)
                elif state.get(target) == 1:
                    cycle = frozenset(stack[stack.index(target) :])
                    if cycle not in reported:
                        reported.add(cycle)
                        issues.append(self._issue("GRAPH_LOOP_DETECTED", f"Une boucle a été détectée autour du nœud '{target}'.", node_id=str(target), severity="warning"))
            stack.pop()
            state[node_id] = 2

        for node_id in node_ids:
            if state.get(node_id, 0) == 0:
                visit(node_id)

    def _check_main_branches(self, nodes, outgoing, can_reach_end, issues) -> None:
        """Identify outgoing branches of starts/splits that cannot reach an end."""
        node_types = {node["id"]: node.get("type") for node in nodes}
        for node_id, targets in outgoing.items():
            is_branch_origin = node_types.get(node_id) == "startEvent" or len(targets) > 1
            if not is_branch_origin:
                continue
            for target in targets:
                if target not in can_reach_end:
                    issues.append(self._issue("DEAD_END_BRANCH", f"La branche de '{node_id}' vers '{target}' ne rejoint aucun événement de fin.", node_id=str(target)))

    @staticmethod
    def _walk(starts: set[Hashable], adjacency: dict[Hashable, list[Hashable]]) -> set[Hashable]:
        """Return all vertices reachable from *starts*."""
        visited: set[Hashable] = set()
        pending = list(starts)
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, []))
        return visited

    @staticmethod
    def _usable_id(value: Any) -> bool:
        """Return whether *value* can safely identify a graph element."""
        return isinstance(value, Hashable) and not isinstance(value, (dict, list, set)) and value is not None and value != ""

    @staticmethod
    def _issue(
        code: str,
        message: str,
        node_id: str | None = None,
        flow_id: str | None = None,
        severity: str = "error",
    ) -> ValidationIssue:
        """Create an issue with the stable public result shape."""
        return {"code": code, "message": message, "node_id": node_id, "flow_id": flow_id, "severity": severity}
