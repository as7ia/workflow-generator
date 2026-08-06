"""Deterministic hierarchical BPMN node and container layout.

The engine consumes :class:`WorkflowGraphAnalyzer` output and deliberately
does not route flows or generate XML.  Legacy function adapters are retained
for the existing JSON-to-BPMN converter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:  # Script execution exposes ``workflow``; package tests expose ``src.workflow``.
    from workflow.graph_analyzer import WorkflowGraphAnalyzer
except ModuleNotFoundError:  # pragma: no cover - depends on import entry point.
    from src.workflow.graph_analyzer import WorkflowGraphAnalyzer

from .constants import (
    BACK_EDGE_MARGIN,
    BRANCH_MARGIN,
    CANVAS_MARGIN,
    COMPONENT_SPACING,
    END_EVENT_HEIGHT,
    END_EVENT_WIDTH,
    GATEWAY_HEIGHT,
    GATEWAY_WIDTH,
    GENERIC_NODE_HEIGHT,
    GENERIC_NODE_WIDTH,
    HORIZONTAL_SPACING,
    INTERMEDIATE_EVENT_HEIGHT,
    INTERMEDIATE_EVENT_WIDTH,
    LANE_MIN_HEIGHT,
    LANE_PADDING,
    POOL_HEADER_WIDTH,
    POOL_PADDING,
    POOL_SPACING,
    START_EVENT_HEIGHT,
    START_EVENT_WIDTH,
    SUBPROCESS_HEIGHT,
    SUBPROCESS_WIDTH,
    TASK_HEIGHT,
    TASK_WIDTH,
    VERTICAL_SPACING,
)


Rectangle = dict[str, float]
LayoutWarning = dict[str, Any]


class BPMNLayoutEngine:
    """Place BPMN nodes, participants and lanes in a horizontal diagram."""

    _TASK_TYPES = {
        "userTask",
        "serviceTask",
        "manualTask",
        "scriptTask",
        "businessRuleTask",
        "sendTask",
        "receiveTask",
    }
    _GATEWAY_TYPES = {
        "exclusiveGateway",
        "parallelGateway",
        "inclusiveGateway",
        "eventBasedGateway",
    }
    _INTERMEDIATE_EVENT_TYPES = {
        "intermediateCatchEvent",
        "intermediateThrowEvent",
        "timerEvent",
    }

    def __init__(self) -> None:
        self._warnings: list[LayoutWarning] = []

    def layout(
        self,
        workflow: dict[str, Any],
        analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return deterministic geometry without modifying *workflow*."""
        self._warnings = []
        source = workflow if isinstance(workflow, Mapping) else {}
        if not isinstance(workflow, Mapping):
            self._warn("INVALID_WORKFLOW", "Le workflow doit être un objet.")
        graph = analysis if isinstance(analysis, Mapping) else WorkflowGraphAnalyzer().analyze(dict(source))
        node_by_id = self._node_index(source)
        node_ids = sorted(node_id for node_id in graph.get("node_ids", []) if node_id in node_by_id)
        levels = {node_id: int(graph.get("levels", {}).get(node_id, 0)) for node_id in node_ids}
        branch_indexes = self._place_branches(graph, node_ids)
        component_indexes = self._component_indexes(graph.get("components", []), node_ids)

        node_layout: dict[str, dict[str, Any]] = {}
        for node_id in node_ids:
            node_type = node_by_id[node_id].get("type")
            width, height = self._get_node_size(node_type)
            node_layout[node_id] = {
                "x": 0.0,
                "y": 0.0,
                "width": width,
                "height": height,
                "participant_id": graph.get("node_to_participant", {}).get(node_id),
                "lane_id": graph.get("node_to_lane", {}).get(node_id),
                "level": levels[node_id],
                "branch_index": branch_indexes.get(node_id, 0),
                "_component_index": component_indexes.get(node_id, 0),
            }

        column_width = max((entry["width"] for entry in node_layout.values()), default=float(GENERIC_NODE_WIDTH))
        pitch = column_width + HORIZONTAL_SPACING
        for entry in node_layout.values():
            entry["x"] = float(CANVAS_MARGIN + entry["level"] * pitch)

        participant_layout, lane_layout = self._place_participants(
            source, graph, node_layout, pitch
        )
        self._place_uncontained_nodes(node_layout, participant_layout, lane_layout)
        self._place_join_nodes(graph, node_layout)
        self._resolve_overlaps(node_layout)
        self._resize_containers(node_layout, participant_layout, lane_layout, pitch)
        self._resolve_participant_overlaps(node_layout, participant_layout, lane_layout)

        back_channels = self._reserve_back_edge_channels(graph)
        message_connections = self._message_flow_connections(source, graph)
        self._validate_layout(node_layout, participant_layout, lane_layout)

        for entry in node_layout.values():
            entry.pop("_component_index", None)
        width, height = self._canvas_size(node_layout, participant_layout)
        warnings = sorted(
            self._warnings,
            key=lambda warning: (
                str(warning.get("code", "")),
                str(warning.get("node_id") or ""),
                str(warning.get("container_id") or ""),
            ),
        )
        return {
            "nodes": {node_id: node_layout[node_id] for node_id in sorted(node_layout)},
            "participants": {key: participant_layout[key] for key in sorted(participant_layout)},
            "lanes": {key: lane_layout[key] for key in sorted(lane_layout)},
            "metadata": {
                "orientation": "horizontal",
                "width": width,
                "height": height,
                "warnings": warnings,
                "back_edge_channels": back_channels,
                "message_flow_connections": message_connections,
            },
        }

    def _get_node_size(self, node_type: str) -> tuple[float, float]:
        """Return configured dimensions for a BPMN node type."""
        if node_type == "startEvent":
            return float(START_EVENT_WIDTH), float(START_EVENT_HEIGHT)
        if node_type == "endEvent":
            return float(END_EVENT_WIDTH), float(END_EVENT_HEIGHT)
        if node_type in self._TASK_TYPES:
            return float(TASK_WIDTH), float(TASK_HEIGHT)
        if node_type in self._GATEWAY_TYPES:
            return float(GATEWAY_WIDTH), float(GATEWAY_HEIGHT)
        if node_type == "subProcess":
            return float(SUBPROCESS_WIDTH), float(SUBPROCESS_HEIGHT)
        if node_type in self._INTERMEDIATE_EVENT_TYPES:
            return float(INTERMEDIATE_EVENT_WIDTH), float(INTERMEDIATE_EVENT_HEIGHT)
        self._warn("UNKNOWN_NODE_TYPE", f"Dimensions génériques utilisées pour le type '{node_type}'.")
        return float(GENERIC_NODE_WIDTH), float(GENERIC_NODE_HEIGHT)

    @staticmethod
    def _node_index(workflow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Build a safe node lookup from usable identifiers."""
        raw_nodes = workflow.get("nodes", [])
        if not isinstance(raw_nodes, list):
            return {}
        return {
            item["id"]: dict(item)
            for item in raw_nodes
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }

    def _place_branches(
        self, analysis: Mapping[str, Any], node_ids: list[str]
    ) -> dict[str, int]:
        """Assign balanced vertical corridors, including nested branches."""
        indexes = {node_id: 0 for node_id in node_ids}
        branches = analysis.get("branches", [])
        if not isinstance(branches, list):
            return indexes
        ordered = sorted(
            (branch for branch in branches if isinstance(branch, Mapping)),
            key=lambda branch: str(branch.get("split_gateway", "")),
        )
        for branch in ordered:
            starts = sorted(branch.get("branch_starts", []))
            offsets = self._balanced_offsets(len(starts))
            branch_nodes = branch.get("branch_nodes", {})
            for start, offset in zip(starts, offsets):
                members = branch_nodes.get(start, []) if isinstance(branch_nodes, Mapping) else []
                for node_id in members:
                    if node_id in indexes:
                        indexes[node_id] += offset
        return indexes

    @staticmethod
    def _balanced_offsets(count: int) -> list[int]:
        """Return stable integer corridors balanced around zero."""
        if count <= 1:
            return [0] * count
        if count % 2:
            half = count // 2
            return list(range(-half, half + 1))
        half = count // 2
        return list(range(-half, 0)) + list(range(1, half + 1))

    @staticmethod
    def _component_indexes(raw_components: Any, node_ids: list[str]) -> dict[str, int]:
        """Map disconnected components to deterministic vertical bands."""
        result = {node_id: 0 for node_id in node_ids}
        if isinstance(raw_components, list):
            for index, component in enumerate(raw_components):
                if isinstance(component, list):
                    for node_id in component:
                        if node_id in result:
                            result[node_id] = index
        return result

    def _place_participants(
        self,
        workflow: Mapping[str, Any],
        analysis: Mapping[str, Any],
        nodes: dict[str, dict[str, Any]],
        pitch: float,
    ) -> tuple[dict[str, Rectangle], dict[str, dict[str, Any]]]:
        """Create stacked participant and lane containers, including implicit lanes."""
        analyzed_participants = analysis.get("participants", {})
        analyzed_lanes = analysis.get("lanes", {})
        participants: dict[str, Rectangle] = {}
        lanes: dict[str, dict[str, Any]] = {}
        if not isinstance(analyzed_participants, Mapping):
            analyzed_participants = {}
        if not isinstance(analyzed_lanes, Mapping):
            analyzed_lanes = {}

        max_level = max((entry["level"] for entry in nodes.values()), default=0)
        pool_width = float(
            POOL_HEADER_WIDTH + 2 * POOL_PADDING + max_level * pitch + max(
                (entry["width"] for entry in nodes.values()), default=GENERIC_NODE_WIDTH
            )
        )
        pool_y = float(CANVAS_MARGIN)
        for participant_id in sorted(analyzed_participants):
            participant_nodes = sorted(
                node_id for node_id, entry in nodes.items() if entry["participant_id"] == participant_id
            )
            lane_ids = sorted(
                lane_id
                for lane_id, lane in analyzed_lanes.items()
                if isinstance(lane, Mapping) and lane.get("participant_id") == participant_id
            )
            assigned = {
                node_id
                for lane_id in lane_ids
                for node_id in analyzed_lanes[lane_id].get("node_ids", [])
            }
            unassigned = sorted(set(participant_nodes) - assigned)
            if unassigned or not lane_ids:
                implicit_id = f"{participant_id}__implicit_lane"
                lane_ids.append(implicit_id)
                analyzed_lane = {
                    "id": implicit_id,
                    "name": "Implicit lane",
                    "participant_id": participant_id,
                    "node_ids": unassigned,
                }
                analyzed_lanes = dict(analyzed_lanes)
                analyzed_lanes[implicit_id] = analyzed_lane
                if unassigned:
                    for node_id in unassigned:
                        nodes[node_id]["lane_id"] = implicit_id
                        self._warn(
                            "IMPLICIT_LANE_ASSIGNED",
                            f"Le nœud '{node_id}' est placé dans une lane implicite.",
                            node_id=node_id,
                            container_id=implicit_id,
                        )

            lane_y = pool_y
            for lane_id in lane_ids:
                lane_nodes = sorted(
                    node_id for node_id in analyzed_lanes[lane_id].get("node_ids", []) if node_id in nodes
                )
                lane_height = self._place_nodes_in_lane(
                    lane_nodes, nodes, lane_y, POOL_HEADER_WIDTH + POOL_PADDING
                )
                lanes[lane_id] = {
                    "x": float(POOL_HEADER_WIDTH),
                    "y": lane_y,
                    "width": pool_width - POOL_HEADER_WIDTH,
                    "height": lane_height,
                    "participant_id": participant_id,
                }
                lane_y += lane_height
            pool_height = max(float(LANE_MIN_HEIGHT), lane_y - pool_y)
            participants[participant_id] = {
                "x": 0.0,
                "y": pool_y,
                "width": pool_width,
                "height": pool_height,
            }
            pool_y += pool_height + POOL_SPACING

        # Top-level lanes can be associated with their nodes' participant when
        # all members share one; otherwise they remain canvas-level containers.
        for lane_id in sorted(analyzed_lanes):
            if lane_id in lanes or not isinstance(analyzed_lanes[lane_id], Mapping):
                continue
            lane_nodes = sorted(node_id for node_id in analyzed_lanes[lane_id].get("node_ids", []) if node_id in nodes)
            owners = {nodes[node_id]["participant_id"] for node_id in lane_nodes if nodes[node_id]["participant_id"]}
            owner = next(iter(owners)) if len(owners) == 1 else None
            if owner in participants:
                continue
            lane_y = pool_y
            lane_height = self._place_nodes_in_lane(lane_nodes, nodes, lane_y, CANVAS_MARGIN)
            lanes[lane_id] = {
                "x": float(CANVAS_MARGIN),
                "y": lane_y,
                "width": pool_width,
                "height": lane_height,
                "participant_id": owner,
            }
            pool_y += lane_height + POOL_SPACING
        return participants, lanes

    def _place_nodes_in_lane(
        self,
        node_ids: list[str],
        nodes: dict[str, dict[str, Any]],
        lane_y: float,
        left_offset: float,
    ) -> float:
        """Place lane members by level and balanced branch corridor."""
        if not node_ids:
            return float(LANE_MIN_HEIGHT)
        corridor = max(
            max((nodes[node_id]["height"] for node_id in node_ids), default=TASK_HEIGHT)
            + VERTICAL_SPACING,
            2 * BRANCH_MARGIN,
        )
        min_branch = min(nodes[node_id]["branch_index"] for node_id in node_ids)
        component_base: dict[int, float] = {}
        cursor = float(LANE_PADDING)
        for component in sorted({nodes[node_id]["_component_index"] for node_id in node_ids}):
            component_base[component] = cursor
            members = [node_id for node_id in node_ids if nodes[node_id]["_component_index"] == component]
            span = max(nodes[node_id]["branch_index"] - min_branch for node_id in members) * corridor
            cursor += span + max(nodes[node_id]["height"] for node_id in members) + COMPONENT_SPACING
        for node_id in node_ids:
            entry = nodes[node_id]
            entry["x"] += float(left_offset)
            entry["y"] = float(
                lane_y
                + component_base[entry["_component_index"]]
                + (entry["branch_index"] - min_branch) * corridor
            )
        bottom = max(nodes[node_id]["y"] + nodes[node_id]["height"] for node_id in node_ids)
        return max(float(LANE_MIN_HEIGHT), bottom - lane_y + LANE_PADDING)

    def _place_uncontained_nodes(
        self,
        nodes: dict[str, dict[str, Any]],
        participants: dict[str, Rectangle],
        lanes: dict[str, dict[str, Any]],
    ) -> None:
        """Place nodes outside declared pools in separated component bands."""
        uncontained = [node_id for node_id, entry in nodes.items() if entry["participant_id"] not in participants]
        if not uncontained:
            return
        start_y = max(
            [CANVAS_MARGIN]
            + [rectangle["y"] + rectangle["height"] + POOL_SPACING for rectangle in participants.values()]
            + [rectangle["y"] + rectangle["height"] + POOL_SPACING for rectangle in lanes.values() if rectangle["participant_id"] is None]
        )
        corridor = max(TASK_HEIGHT, SUBPROCESS_HEIGHT) + VERTICAL_SPACING
        min_branch = min(nodes[node_id]["branch_index"] for node_id in uncontained)
        for node_id in sorted(uncontained):
            entry = nodes[node_id]
            entry["y"] = float(
                start_y
                + entry["_component_index"] * COMPONENT_SPACING
                + (entry["branch_index"] - min_branch) * corridor
            )
            if participants:
                self._warn(
                    "NODE_OUTSIDE_PARTICIPANT",
                    f"Le nœud '{node_id}' ne possède aucun participant.",
                    node_id=node_id,
                )

    @staticmethod
    def _place_join_nodes(
        analysis: Mapping[str, Any], nodes: dict[str, dict[str, Any]]
    ) -> None:
        """Center convergence nodes between their incoming branch centers."""
        reverse = analysis.get("reverse_adjacency", {})
        in_degree = analysis.get("in_degree", {})
        for node_id in sorted(nodes, key=lambda value: (nodes[value]["level"], value)):
            if in_degree.get(node_id, 0) <= 1:
                continue
            predecessors = [source for source in reverse.get(node_id, []) if source in nodes]
            if not predecessors:
                continue
            centers = [nodes[source]["y"] + nodes[source]["height"] / 2 for source in predecessors]
            nodes[node_id]["y"] = float(sum(centers) / len(centers) - nodes[node_id]["height"] / 2)

    @staticmethod
    def _rectangles_overlap(rect_a: Mapping[str, float], rect_b: Mapping[str, float]) -> bool:
        """Return whether two rectangles have a positive-area intersection."""
        return (
            rect_a["x"] < rect_b["x"] + rect_b["width"]
            and rect_a["x"] + rect_a["width"] > rect_b["x"]
            and rect_a["y"] < rect_b["y"] + rect_b["height"]
            and rect_a["y"] + rect_a["height"] > rect_b["y"]
        )

    def _resolve_overlaps(self, nodes: dict[str, dict[str, Any]]) -> None:
        """Move colliding nodes downward using a stable deterministic order."""
        placed: list[str] = []
        for node_id in sorted(nodes, key=lambda value: (nodes[value]["y"], nodes[value]["x"], value)):
            current = nodes[node_id]
            while True:
                collisions = [other for other in placed if self._rectangles_overlap(current, nodes[other])]
                if not collisions:
                    break
                current["y"] = float(
                    max(nodes[other]["y"] + nodes[other]["height"] for other in collisions)
                    + VERTICAL_SPACING
                )
            placed.append(node_id)

    def _resize_containers(
        self,
        nodes: dict[str, dict[str, Any]],
        participants: dict[str, Rectangle],
        lanes: dict[str, dict[str, Any]],
        pitch: float,
    ) -> None:
        """Expand lanes and pools after deterministic overlap resolution."""
        max_level = max((entry["level"] for entry in nodes.values()), default=0)
        required_width = float(
            POOL_HEADER_WIDTH + 2 * POOL_PADDING + max_level * pitch
            + max((entry["width"] for entry in nodes.values()), default=GENERIC_NODE_WIDTH)
        )
        for lane_id, lane in lanes.items():
            members = [entry for entry in nodes.values() if entry["lane_id"] == lane_id]
            if members:
                bottom = max(entry["y"] + entry["height"] for entry in members)
                lane["height"] = max(lane["height"], float(bottom - lane["y"] + LANE_PADDING))
            lane["width"] = max(lane["width"], required_width - lane["x"])
        for participant_id, participant in participants.items():
            participant_lanes = [lane for lane in lanes.values() if lane["participant_id"] == participant_id]
            members = [entry for entry in nodes.values() if entry["participant_id"] == participant_id]
            bottom = max(
                [participant["y"] + LANE_MIN_HEIGHT]
                + [lane["y"] + lane["height"] for lane in participant_lanes]
                + [entry["y"] + entry["height"] + POOL_PADDING for entry in members]
            )
            participant["height"] = float(bottom - participant["y"])
            participant["width"] = max(participant["width"], required_width)

    def _resolve_participant_overlaps(
        self,
        nodes: dict[str, dict[str, Any]],
        participants: dict[str, Rectangle],
        lanes: dict[str, dict[str, Any]],
    ) -> None:
        """Restack participant rectangles and move all their contents together."""
        for participant_id in sorted(participants):
            participant = participants[participant_id]
            lane_cursor = participant["y"]
            participant_lanes = sorted(
                (
                    (lane_id, lane)
                    for lane_id, lane in lanes.items()
                    if lane["participant_id"] == participant_id
                ),
                key=lambda item: (item[1]["y"], item[0]),
            )
            for lane_id, lane in participant_lanes:
                delta = lane_cursor - lane["y"]
                lane["y"] += delta
                for entry in nodes.values():
                    if entry["lane_id"] == lane_id:
                        entry["y"] += delta
                lane_cursor += lane["height"]
            participant["height"] = max(
                float(LANE_MIN_HEIGHT), lane_cursor - participant["y"]
            )

        cursor = float(CANVAS_MARGIN)
        for participant_id in sorted(participants):
            participant = participants[participant_id]
            delta = cursor - participant["y"]
            participant["y"] += delta
            for lane in lanes.values():
                if lane["participant_id"] == participant_id:
                    lane["y"] += delta
            for entry in nodes.values():
                if entry["participant_id"] == participant_id:
                    entry["y"] += delta
            cursor = participant["y"] + participant["height"] + POOL_SPACING

    @staticmethod
    def _reserve_back_edge_channels(analysis: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Reserve deterministic routing channels without calculating waypoints."""
        raw_edges = analysis.get("back_edges", [])
        edges = sorted(
            (edge for edge in raw_edges if isinstance(edge, Mapping) and edge.get("flow_id")),
            key=lambda edge: str(edge["flow_id"]),
        )
        return {
            str(edge["flow_id"]): {
                "side": "bottom",
                "channel_index": index,
                "margin": float(BACK_EDGE_MARGIN),
                "source": edge.get("source", edge.get("sourceRef")),
                "target": edge.get("target", edge.get("targetRef")),
            }
            for index, edge in enumerate(edges)
        }

    @staticmethod
    def _message_flow_connections(
        workflow: Mapping[str, Any], analysis: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Record message endpoints and participant ownership for future routing."""
        raw = workflow.get("messageFlows", [])
        if not isinstance(raw, list):
            return []
        ownership = analysis.get("node_to_participant", {})
        result = []
        for index, flow in enumerate(raw):
            if not isinstance(flow, Mapping):
                continue
            source = flow.get("source", flow.get("sourceRef"))
            target = flow.get("target", flow.get("targetRef"))
            result.append(
                {
                    "flow_id": str(flow.get("id", f"message_flow_{index}")),
                    "source": source,
                    "target": target,
                    "source_participant": ownership.get(source, source if source in analysis.get("participants", {}) else None),
                    "target_participant": ownership.get(target, target if target in analysis.get("participants", {}) else None),
                }
            )
        return sorted(result, key=lambda item: item["flow_id"])

    def _validate_layout(
        self,
        nodes: dict[str, dict[str, Any]],
        participants: dict[str, Rectangle],
        lanes: dict[str, dict[str, Any]],
    ) -> None:
        """Record residual containment or overlap problems without raising."""
        node_ids = sorted(nodes)
        for index, node_id in enumerate(node_ids):
            for other_id in node_ids[index + 1 :]:
                if self._rectangles_overlap(nodes[node_id], nodes[other_id]):
                    self._warn("NODE_OVERLAP", f"Les nœuds '{node_id}' et '{other_id}' se chevauchent.", node_id=node_id)
        for node_id, entry in nodes.items():
            lane_id = entry["lane_id"]
            if lane_id in lanes and not self._contains(lanes[lane_id], entry):
                self._warn("NODE_OUTSIDE_LANE", f"Le nœud '{node_id}' sort de sa lane.", node_id=node_id, container_id=lane_id)
        for lane_id, lane in lanes.items():
            participant_id = lane["participant_id"]
            if participant_id in participants and not self._contains(participants[participant_id], lane):
                self._warn("LANE_OUTSIDE_PARTICIPANT", f"La lane '{lane_id}' sort de son participant.", container_id=lane_id)
        participant_ids = sorted(participants)
        for index, participant_id in enumerate(participant_ids):
            for other_id in participant_ids[index + 1 :]:
                if self._rectangles_overlap(participants[participant_id], participants[other_id]):
                    self._warn("PARTICIPANT_OVERLAP", f"Les participants '{participant_id}' et '{other_id}' se chevauchent.", container_id=participant_id)

    @staticmethod
    def _contains(container: Mapping[str, float], item: Mapping[str, float]) -> bool:
        return (
            item["x"] >= container["x"]
            and item["y"] >= container["y"]
            and item["x"] + item["width"] <= container["x"] + container["width"]
            and item["y"] + item["height"] <= container["y"] + container["height"]
        )

    @staticmethod
    def _canvas_size(
        nodes: dict[str, dict[str, Any]], participants: dict[str, Rectangle]
    ) -> tuple[float, float]:
        """Calculate diagram bounds including configured outer margins."""
        right = max(
            [0.0]
            + [entry["x"] + entry["width"] for entry in nodes.values()]
            + [entry["x"] + entry["width"] for entry in participants.values()]
        )
        bottom = max(
            [0.0]
            + [entry["y"] + entry["height"] for entry in nodes.values()]
            + [entry["y"] + entry["height"] for entry in participants.values()]
        )
        return float(right + CANVAS_MARGIN), float(bottom + CANVAS_MARGIN)

    def _warn(
        self,
        code: str,
        message: str,
        node_id: str | None = None,
        container_id: str | None = None,
    ) -> None:
        self._warnings.append(
            {
                "code": code,
                "message": message,
                "node_id": node_id,
                "container_id": container_id,
                "severity": "warning",
            }
        )


def normalize_flows(flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy adapter normalizing reference names for the XML converter."""
    return [
        {
            **flow,
            "source": flow.get("source") or flow.get("sourceRef"),
            "target": flow.get("target") or flow.get("targetRef"),
        }
        for flow in flows
        if isinstance(flow, Mapping)
    ]


def calculate_positions(
    nodes: list[dict[str, Any]], flows: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Legacy adapter returning only node geometry for existing callers."""
    workflow = {"nodes": nodes, "flows": flows, "participants": []}
    return BPMNLayoutEngine().layout(workflow)["nodes"]
