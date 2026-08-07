"""Deterministic structural analysis of validated BPMN workflow graphs.

Only sequence flows form the control-flow graph.  Collaboration metadata and
message flows are analyzed separately so they cannot affect levels or cycles.
No coordinates, routing, XML generation, or example-specific rules live here.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any


WarningEntry = dict[str, Any]
Edge = tuple[str, str, str]


class WorkflowGraphAnalyzer:
    """Analyze graph, branching, cycle and collaboration structure."""

    _GATEWAY_TYPES = {
        "exclusiveGateway",
        "parallelGateway",
        "inclusiveGateway",
    }
    _MESSAGE_TYPES = {"messageFlow", "message"}

    def analyze(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """Return a deterministic analysis without mutating *workflow*."""
        warnings: list[WarningEntry] = []
        source = workflow if isinstance(workflow, Mapping) else {}
        if not isinstance(workflow, Mapping):
            warnings.append(self._warning("INVALID_WORKFLOW", "Le workflow doit être un objet."))

        nodes, node_types = self._read_nodes(source.get("nodes"), warnings)
        node_ids = sorted(nodes)
        adjacency, reverse, edges, embedded_messages = self._build_graph(
            node_ids, source.get("flows"), warnings
        )
        in_degree = {node_id: len(reverse[node_id]) for node_id in node_ids}
        out_degree = {node_id: len(adjacency[node_id]) for node_id in node_ids}
        starts, ends, without_predecessors, without_successors = self._find_start_and_end_nodes(
            node_ids, node_types, in_degree, out_degree
        )
        components = self._find_connected_components(node_ids, adjacency, reverse)
        sccs = self._find_strongly_connected_components(node_ids, adjacency)
        component_of, condensed = self._build_condensed_graph(sccs, adjacency)
        component_levels, component_order = self._calculate_levels(condensed)
        levels = {
            node_id: component_levels[component_of[node_id]] for node_id in node_ids
        }
        cycles = self._find_cycles(sccs, adjacency)
        back_edges = self._find_back_edges(node_ids, adjacency, edges)
        split_gateways, join_gateways = self._find_gateways(
            node_ids, node_types, in_degree, out_degree
        )
        branches = self._analyze_branches(
            split_gateways, node_types, adjacency, reverse, levels
        )
        topological_order = self._topological_sort(sccs, component_order)
        collaboration = self._analyze_participants_and_lanes(
            source,
            node_ids,
            edges,
            embedded_messages,
            warnings,
        )
        warnings.sort(
            key=lambda warning: (
                str(warning.get("code", "")),
                str(warning.get("node_id") or ""),
                str(warning.get("flow_id") or ""),
                str(warning.get("message", "")),
            )
        )

        return {
            "node_ids": node_ids,
            "start_nodes": starts,
            "end_nodes": ends,
            "nodes_without_predecessors": without_predecessors,
            "nodes_without_successors": without_successors,
            "adjacency": adjacency,
            "reverse_adjacency": reverse,
            "in_degree": in_degree,
            "out_degree": out_degree,
            "levels": levels,
            "components": components,
            "strongly_connected_components": sccs,
            "condensed_components": {
                str(index): members for index, members in enumerate(sccs)
            },
            "condensed_adjacency": {
                str(index): [str(target) for target in condensed[index]]
                for index in sorted(condensed)
            },
            "cycles": cycles,
            "split_gateways": split_gateways,
            "join_gateways": join_gateways,
            "branches": branches,
            "back_edges": back_edges,
            "topological_order": topological_order,
            "participants": collaboration["participants"],
            "lanes": collaboration["lanes"],
            "node_to_participant": collaboration["node_to_participant"],
            "node_to_lane": collaboration["node_to_lane"],
            "warnings": warnings,
        }

    def _read_nodes(
        self, raw_nodes: Any, warnings: list[WarningEntry]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Read unique, usable nodes while reporting malformed entries."""
        nodes: dict[str, dict[str, Any]] = {}
        if not isinstance(raw_nodes, list):
            warnings.append(self._warning("INVALID_NODES", "La clé 'nodes' doit être une liste."))
            return nodes, {}
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, Mapping) or not isinstance(raw_node.get("id"), str) or not raw_node.get("id"):
                warnings.append(self._warning("INVALID_NODE", f"Le nœud à l'index {index} est ignoré."))
                continue
            node_id = raw_node["id"]
            if node_id in nodes:
                warnings.append(self._warning("DUPLICATE_NODE", f"Le nœud dupliqué '{node_id}' est ignoré.", node_id=node_id))
                continue
            nodes[node_id] = dict(raw_node)
        return nodes, {node_id: node.get("type") for node_id, node in nodes.items()}

    def _build_graph(
        self,
        node_ids: list[str],
        raw_flows: Any,
        warnings: list[WarningEntry],
    ) -> tuple[dict[str, list[str]], dict[str, list[str]], list[Edge], list[dict[str, Any]]]:
        """Build sequence-flow adjacency and collect embedded message flows."""
        adjacency_sets = {node_id: set() for node_id in node_ids}
        reverse_sets = {node_id: set() for node_id in node_ids}
        edges: list[Edge] = []
        messages: list[dict[str, Any]] = []
        if raw_flows is None:
            raw_flows = []
        if not isinstance(raw_flows, list):
            warnings.append(self._warning("INVALID_FLOWS", "La clé 'flows' doit être une liste."))
            raw_flows = []
        known = set(node_ids)
        for index, raw_flow in enumerate(raw_flows):
            if not isinstance(raw_flow, Mapping):
                warnings.append(self._warning("INVALID_FLOW", f"Le flux à l'index {index} est ignoré."))
                continue
            flow = dict(raw_flow)
            kind = flow.get("type", flow.get("flowType", "sequenceFlow"))
            if kind in self._MESSAGE_TYPES:
                messages.append(flow)
                continue
            flow_id = str(flow.get("id", f"flow_{index}"))
            source = flow.get("source", flow.get("sourceRef"))
            target = flow.get("target", flow.get("targetRef"))
            if source is None:
                warnings.append(
                    self._warning(
                        "MISSING_FLOW_SOURCE",
                        f"Le flux '{flow_id}' ne possède pas de source.",
                        flow_id=flow_id,
                    )
                )
                continue
            if target is None:
                warnings.append(self._warning("MISSING_FLOW_TARGET", f"Le flux '{flow_id}' ne possède pas de cible.", flow_id=flow_id))
                continue
            if source not in known:
                warnings.append(self._warning("UNKNOWN_FLOW_SOURCE", f"La source '{source}' du flux '{flow_id}' est inconnue.", flow_id=flow_id))
                continue
            if target not in known:
                warnings.append(self._warning("UNKNOWN_FLOW_TARGET", f"La cible '{target}' du flux '{flow_id}' est inconnue.", flow_id=flow_id))
                continue
            adjacency_sets[source].add(target)
            reverse_sets[target].add(source)
            edges.append((flow_id, source, target))
        adjacency = {node_id: sorted(adjacency_sets[node_id]) for node_id in node_ids}
        reverse = {node_id: sorted(reverse_sets[node_id]) for node_id in node_ids}
        return adjacency, reverse, sorted(edges), messages

    @staticmethod
    def _find_start_and_end_nodes(
        node_ids: list[str],
        node_types: dict[str, Any],
        in_degree: dict[str, int],
        out_degree: dict[str, int],
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Identify BPMN events separately from graph roots and leaves."""
        starts = [node_id for node_id in node_ids if node_types[node_id] == "startEvent"]
        ends = [node_id for node_id in node_ids if node_types[node_id] == "endEvent"]
        roots = [node_id for node_id in node_ids if in_degree[node_id] == 0]
        leaves = [node_id for node_id in node_ids if out_degree[node_id] == 0]
        return starts, ends, roots, leaves

    @staticmethod
    def _find_connected_components(
        node_ids: list[str],
        adjacency: dict[str, list[str]],
        reverse: dict[str, list[str]],
    ) -> list[list[str]]:
        """Find weakly connected components with deterministic traversal."""
        unseen = set(node_ids)
        components: list[list[str]] = []
        while unseen:
            root = min(unseen)
            pending = [root]
            component: set[str] = set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                pending.extend(reversed(sorted(set(adjacency[current]) | set(reverse[current]))))
            unseen -= component
            components.append(sorted(component))
        return components

    @staticmethod
    def _find_strongly_connected_components(
        node_ids: list[str], adjacency: dict[str, list[str]]
    ) -> list[list[str]]:
        """Find strongly connected components using deterministic Tarjan DFS."""
        index = 0
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        components: list[list[str]] = []

        def visit(node_id: str) -> None:
            nonlocal index
            indices[node_id] = index
            lowlinks[node_id] = index
            index += 1
            stack.append(node_id)
            on_stack.add(node_id)
            for target in adjacency[node_id]:
                if target not in indices:
                    visit(target)
                    lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target])
                elif target in on_stack:
                    lowlinks[node_id] = min(lowlinks[node_id], indices[target])
            if lowlinks[node_id] == indices[node_id]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.remove(member)
                    component.append(member)
                    if member == node_id:
                        break
                components.append(sorted(component))

        for node_id in node_ids:
            if node_id not in indices:
                visit(node_id)
        return sorted(components, key=lambda component: component[0])

    @staticmethod
    def _build_condensed_graph(
        sccs: list[list[str]], adjacency: dict[str, list[str]]
    ) -> tuple[dict[str, int], dict[int, list[int]]]:
        """Condense SCCs into a deterministic directed acyclic graph."""
        component_of = {
            node_id: index for index, component in enumerate(sccs) for node_id in component
        }
        condensed_sets = {index: set() for index in range(len(sccs))}
        for source, targets in adjacency.items():
            source_component = component_of[source]
            for target in targets:
                target_component = component_of[target]
                if source_component != target_component:
                    condensed_sets[source_component].add(target_component)
        condensed = {index: sorted(targets) for index, targets in condensed_sets.items()}
        return component_of, condensed

    @staticmethod
    def _calculate_levels(
        adjacency: dict[int, list[int]]
    ) -> tuple[dict[int, int], list[int]]:
        """Calculate longest-path levels and stable Kahn order on a DAG."""
        in_degree = {node: 0 for node in adjacency}
        for targets in adjacency.values():
            for target in targets:
                in_degree[target] += 1
        ready = sorted(node for node, degree in in_degree.items() if degree == 0)
        levels = {node: 0 for node in adjacency}
        order: list[int] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for target in adjacency[current]:
                levels[target] = max(levels[target], levels[current] + 1)
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    ready.append(target)
                    ready.sort()
        return levels, order

    @staticmethod
    def _find_cycles(
        sccs: list[list[str]], adjacency: dict[str, list[str]]
    ) -> list[list[str]]:
        """Represent every cyclic SCC, including self-loops, as a cycle."""
        return [
            component
            for component in sccs
            if len(component) > 1 or component[0] in adjacency[component[0]]
        ]

    @staticmethod
    def _find_back_edges(
        node_ids: list[str], adjacency: dict[str, list[str]], edges: list[Edge]
    ) -> list[dict[str, str]]:
        """Find DFS back edges, which are useful hints for loop routing."""
        state: dict[str, int] = {}
        back_pairs: set[tuple[str, str]] = set()

        def visit(node_id: str) -> None:
            state[node_id] = 1
            for target in adjacency[node_id]:
                if state.get(target, 0) == 0:
                    visit(target)
                elif state.get(target) == 1:
                    back_pairs.add((node_id, target))
            state[node_id] = 2

        for node_id in node_ids:
            if state.get(node_id, 0) == 0:
                visit(node_id)
        return [
            {"flow_id": flow_id, "source": source, "target": target}
            for flow_id, source, target in edges
            if (source, target) in back_pairs
        ]

    def _find_gateways(
        self,
        node_ids: list[str],
        node_types: dict[str, Any],
        in_degree: dict[str, int],
        out_degree: dict[str, int],
    ) -> tuple[list[str], list[str]]:
        """Classify gateways by BPMN type and actual graph degrees."""
        gateways = [node_id for node_id in node_ids if node_types[node_id] in self._GATEWAY_TYPES]
        splits = [node_id for node_id in gateways if out_degree[node_id] > 1]
        joins = [node_id for node_id in gateways if in_degree[node_id] > 1]
        return splits, joins

    def _analyze_branches(
        self,
        split_gateways: list[str],
        node_types: dict[str, Any],
        adjacency: dict[str, list[str]],
        reverse: dict[str, list[str]],
        levels: dict[str, int],
    ) -> list[dict[str, Any]]:
        """Trace split branches and choose their nearest common convergence."""
        analyses: list[dict[str, Any]] = []
        split_set = set(split_gateways)
        for split in split_gateways:
            starts = adjacency[split]
            distances = {
                start: self._distances(start, adjacency, blocked={split}) for start in starts
            }
            common = set.intersection(*(set(values) for values in distances.values())) if distances else set()
            convergence = [
                node_id
                for node_id in common
                if len(reverse[node_id]) > 1 and node_id != split
            ]
            join = min(
                convergence,
                key=lambda node_id: (
                    max(distances[start][node_id] for start in starts),
                    sum(distances[start][node_id] for start in starts),
                    node_id,
                ),
                default=None,
            )
            branch_nodes: dict[str, list[str]] = {}
            for start in starts:
                visited = self._collect_until(start, join, adjacency, blocked={split})
                branch_nodes[start] = sorted(visited, key=lambda node_id: (levels[node_id], node_id))
            nested = sorted(
                (set().union(*map(set, branch_nodes.values())) if branch_nodes else set())
                & (split_set - {split})
            )
            analyses.append(
                {
                    "split_gateway": split,
                    "branch_starts": starts,
                    "join_node": join,
                    "branches_end_separately": join is None,
                    "branch_nodes": branch_nodes,
                    "nested_splits": nested,
                    "gateway_type": node_types[split],
                }
            )
        return analyses

    @staticmethod
    def _distances(
        start: str, adjacency: dict[str, list[str]], blocked: set[str]
    ) -> dict[str, int]:
        """Find shortest forward distances while avoiding known loop origins."""
        distances = {start: 0}
        pending = deque([start])
        while pending:
            current = pending.popleft()
            for target in adjacency[current]:
                if target in blocked or target in distances:
                    continue
                distances[target] = distances[current] + 1
                pending.append(target)
        return distances

    @staticmethod
    def _collect_until(
        start: str,
        stop: str | None,
        adjacency: dict[str, list[str]],
        blocked: set[str],
    ) -> set[str]:
        """Collect branch nodes without crossing its selected join."""
        visited: set[str] = set()
        pending = [start]
        while pending:
            current = pending.pop()
            if current in visited or current in blocked or current == stop:
                continue
            visited.add(current)
            pending.extend(reversed(adjacency[current]))
        return visited

    def _analyze_participants_and_lanes(
        self,
        workflow: Mapping[str, Any],
        node_ids: list[str],
        sequence_edges: list[Edge],
        embedded_messages: list[dict[str, Any]],
        warnings: list[WarningEntry],
    ) -> dict[str, Any]:
        """Map collaboration membership and check flow boundary conventions."""
        raw_participants = workflow.get("participants")
        if not raw_participants and isinstance(workflow.get("pools"), list):
            raw_participants = workflow["pools"]
        if raw_participants is None:
            raw_participants = []
        participants: dict[str, dict[str, Any]] = {}
        lanes: dict[str, dict[str, Any]] = {}
        node_to_participant: dict[str, str] = {}
        node_to_lane: dict[str, str] = {}
        known_nodes = set(node_ids)

        if isinstance(raw_participants, list):
            for item in raw_participants:
                if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                    continue
                participant_id = item["id"]
                members = self._valid_refs(item.get("nodeRefs"), known_nodes)
                participants[participant_id] = {
                    "id": participant_id,
                    "name": item.get("name"),
                    "node_ids": members,
                }
                for node_id in members:
                    self._assign_membership(node_to_participant, node_id, participant_id, "MULTIPLE_PARTICIPANTS", warnings)
                self._read_lanes(item.get("lanes"), participant_id, known_nodes, lanes, node_to_lane, warnings)

        raw_nodes = workflow.get("nodes", [])
        if isinstance(raw_nodes, list):
            for node in raw_nodes:
                if not isinstance(node, Mapping) or node.get("id") not in known_nodes:
                    continue
                node_id = node["id"]
                properties = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
                participant = node.get("participantRef", node.get("poolRef", properties.get("participantRef", properties.get("poolRef"))))
                if participant in participants:
                    self._assign_membership(node_to_participant, node_id, participant, "MULTIPLE_PARTICIPANTS", warnings)

        self._read_lanes(workflow.get("lanes"), None, known_nodes, lanes, node_to_lane, warnings)
        for lane in lanes.values():
            participant_id = lane["participant_id"]
            if participant_id in participants:
                for node_id in lane["node_ids"]:
                    self._assign_membership(
                        node_to_participant,
                        node_id,
                        participant_id,
                        "MULTIPLE_PARTICIPANTS",
                        warnings,
                    )
        for node_id, participant_id in node_to_participant.items():
            if node_id not in participants[participant_id]["node_ids"]:
                participants[participant_id]["node_ids"].append(node_id)
                participants[participant_id]["node_ids"].sort()

        if participants:
            for node_id in node_ids:
                if node_id not in node_to_participant:
                    warnings.append(self._warning("NODE_WITHOUT_PARTICIPANT", f"Le nœud '{node_id}' n'appartient à aucun participant.", node_id=node_id))

        for flow_id, source, target in sequence_edges:
            source_owner = node_to_participant.get(source)
            target_owner = node_to_participant.get(target)
            if source_owner and target_owner and source_owner != target_owner:
                warnings.append(self._warning("SEQUENCE_FLOW_CROSSES_PARTICIPANTS", f"Le sequence flow '{flow_id}' traverse deux participants.", flow_id=flow_id))

        raw_messages = workflow.get("messageFlows", [])
        messages = list(embedded_messages)
        if isinstance(raw_messages, list):
            messages.extend(item for item in raw_messages if isinstance(item, Mapping))
        for index, flow in enumerate(messages):
            flow_id = str(flow.get("id", f"message_flow_{index}"))
            source = flow.get("source", flow.get("sourceRef"))
            target = flow.get("target", flow.get("targetRef"))
            source_owner = source if source in participants else node_to_participant.get(source)
            target_owner = target if target in participants else node_to_participant.get(target)
            if source_owner is None or target_owner is None:
                warnings.append(self._warning("MESSAGE_FLOW_PARTICIPANT_UNKNOWN", f"Le message flow '{flow_id}' ne peut pas être rattaché à deux participants.", flow_id=flow_id))
            elif source_owner == target_owner:
                warnings.append(self._warning("MESSAGE_FLOW_WITHIN_SAME_PARTICIPANT", f"Le message flow '{flow_id}' reste dans le même participant.", flow_id=flow_id))

        return {
            "participants": {key: participants[key] for key in sorted(participants)},
            "lanes": {key: lanes[key] for key in sorted(lanes)},
            "node_to_participant": dict(sorted(node_to_participant.items())),
            "node_to_lane": dict(sorted(node_to_lane.items())),
        }

    def _read_lanes(
        self,
        raw_lanes: Any,
        participant_id: str | None,
        known_nodes: set[str],
        lanes: dict[str, dict[str, Any]],
        node_to_lane: dict[str, str],
        warnings: list[WarningEntry],
    ) -> None:
        """Read top-level or participant-nested lanes."""
        if not isinstance(raw_lanes, list):
            return
        for item in raw_lanes:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            lane_id = item["id"]
            members = self._valid_refs(item.get("nodeRefs"), known_nodes)
            lanes[lane_id] = {
                "id": lane_id,
                "name": item.get("name"),
                "participant_id": participant_id,
                "node_ids": members,
            }
            for node_id in members:
                self._assign_membership(node_to_lane, node_id, lane_id, "MULTIPLE_LANES", warnings)

    @staticmethod
    def _valid_refs(raw_refs: Any, known_nodes: set[str]) -> list[str]:
        """Return sorted known node references from a membership field."""
        return sorted({ref for ref in raw_refs if ref in known_nodes}) if isinstance(raw_refs, list) else []

    def _assign_membership(
        self,
        mapping: dict[str, str],
        node_id: str,
        owner_id: str,
        code: str,
        warnings: list[WarningEntry],
    ) -> None:
        """Assign first deterministic membership and warn on conflicts."""
        previous = mapping.get(node_id)
        if previous is None:
            mapping[node_id] = owner_id
        elif previous != owner_id:
            selected = min(previous, owner_id)
            mapping[node_id] = selected
            warnings.append(self._warning(code, f"Le nœud '{node_id}' possède plusieurs appartenances; '{selected}' est retenu.", node_id=node_id))

    @staticmethod
    def _topological_sort(sccs: list[list[str]], component_order: list[int]) -> list[str]:
        """Expand stable condensed-DAG order into stable node order."""
        return [node_id for component in component_order for node_id in sccs[component]]

    @staticmethod
    def _warning(
        code: str,
        message: str,
        node_id: str | None = None,
        flow_id: str | None = None,
    ) -> WarningEntry:
        """Build one stable non-blocking analysis warning."""
        return {
            "code": code,
            "message": message,
            "node_id": node_id,
            "flow_id": flow_id,
            "severity": "warning",
        }
