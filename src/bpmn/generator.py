"""Deterministic generation of BPMN 2.0 XML and BPMN DI."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

from .constants import BPMN_NS, BPMNDI_NS, DC_NS, NODE_TAGS
from .nodes import add_timer_definition, get_timer_value
from .routing import calculate_edge_waypoints, create_edge_waypoints
from .xml_helpers import add_condition, add_edge_label, get_flow_label, tag


class BPMNGenerator:
    """Generate complete BPMN XML from a normalized workflow and its layout."""

    def generate(self, workflow: dict[str, Any], layout: dict[str, Any]) -> str:
        """Return BPMN 2.0 XML using the supplied deterministic geometry.

        JSON flow endpoints remain ``source`` and ``target``.  BPMN's
        ``sourceRef`` and ``targetRef`` names are introduced only here, at the
        XML boundary.
        """
        if not isinstance(workflow, Mapping):
            raise TypeError("Le workflow doit être un objet JSON.")
        if not isinstance(layout, Mapping):
            raise TypeError("Le layout BPMN doit être un objet.")

        process_data = workflow.get("process")
        process_data = process_data if isinstance(process_data, Mapping) else {}
        process_id = str(process_data.get("id") or workflow.get("id") or "Process_1")
        process_name = str(process_data.get("name") or workflow.get("name") or "Workflow")
        executable = bool(process_data.get("executable", True))

        nodes = self._objects(workflow.get("nodes"))
        flows = self._objects(workflow.get("flows"))
        message_flows = self._objects(workflow.get("messageFlows"))
        participants = self._objects(workflow.get("participants"))
        participants = self._pool_participants(participants, message_flows)
        node_geometry = self._geometry(layout.get("nodes"), "nœuds")
        participant_geometry = self._geometry(layout.get("participants"), "participants")
        lane_geometry = self._geometry(layout.get("lanes"), "lanes")
        participant_ids = {str(item["id"]) for item in participants}
        participant_geometry = {
            key: value for key, value in participant_geometry.items() if key in participant_ids
        }
        lane_geometry = {
            key: value
            for key, value in lane_geometry.items()
            if self._lane_owner(layout, key) in participant_ids
            or (not participants and self._lane_owner(layout, key) is None)
        }

        if not nodes:
            raise ValueError("Le workflow ne contient aucun nœud.")
        node_by_id = self._index(nodes, "nœud")
        for node_id in node_by_id:
            if node_id not in node_geometry:
                raise ValueError(f"Coordonnées absentes pour le nœud '{node_id}'.")

        definitions = ET.Element(
            tag(BPMN_NS, "definitions"),
            {
                "id": f"Definitions_{process_id}",
                "targetNamespace": "http://workflow-generator.local/bpmn",
            },
        )

        ownership = self._node_ownership(nodes, participants, layout)
        process_elements, process_for_node, collaboration_id = self._create_processes(
            definitions,
            process_id,
            process_name,
            executable,
            participants,
            ownership,
            force_collaboration=bool(message_flows),
        )
        self._create_lanes(
            process_elements,
            process_for_node,
            participants,
            workflow,
            layout,
        )
        self._create_nodes(process_elements, process_for_node, node_by_id)
        valid_flows = self._create_sequence_flows(
            process_elements, process_for_node, node_by_id, flows
        )
        valid_messages = self._create_message_flows(
            definitions, collaboration_id, node_by_id, participants, message_flows
        )
        self._create_diagram(
            definitions,
            collaboration_id or process_id,
            process_id,
            node_geometry,
            participant_geometry,
            lane_geometry,
            valid_flows,
            valid_messages,
        )
        return ET.tostring(definitions, encoding="unicode", xml_declaration=True)

    @staticmethod
    def _objects(value: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    @staticmethod
    def _pool_participants(
        participants: list[dict[str, Any]], message_flows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Separate explicitly modelled pools from legacy task actors."""
        message_endpoints = {
            endpoint
            for flow in message_flows
            for endpoint in (flow.get("source"), flow.get("target"))
            if isinstance(endpoint, str)
        }
        return [
            participant
            for participant in participants
            if participant.get("nodeRefs")
            or participant.get("lanes")
            or participant.get("id") in message_endpoints
        ]

    @staticmethod
    def _lane_owner(layout: Mapping[str, Any], lane_id: str) -> Any:
        lanes = layout.get("lanes", {})
        lane = lanes.get(lane_id, {}) if isinstance(lanes, Mapping) else {}
        return lane.get("participant_id") if isinstance(lane, Mapping) else None

    @staticmethod
    def _index(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError(f"Un {label} ne possède pas d'identifiant valide.")
            if identifier in result:
                raise ValueError(f"Identifiant de {label} dupliqué : '{identifier}'.")
            result[identifier] = item
        return result

    @staticmethod
    def _geometry(value: Any, label: str) -> dict[str, dict[str, float]]:
        if not isinstance(value, Mapping):
            raise ValueError(f"Le layout des {label} est absent.")
        result: dict[str, dict[str, float]] = {}
        for identifier, rectangle in value.items():
            if not isinstance(rectangle, Mapping):
                continue
            try:
                result[str(identifier)] = {
                    key: float(rectangle[key]) for key in ("x", "y", "width", "height")
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Géométrie invalide pour '{identifier}'.") from exc
        return result

    @staticmethod
    def _node_ownership(
        nodes: list[dict[str, Any]],
        participants: list[dict[str, Any]],
        layout: Mapping[str, Any],
    ) -> dict[str, str | None]:
        owners: dict[str, str | None] = {str(node["id"]): None for node in nodes}
        participant_ids = {item.get("id") for item in participants}
        for participant in participants:
            participant_id = participant.get("id")
            for node_id in participant.get("nodeRefs", []):
                if node_id in owners:
                    owners[node_id] = participant_id
            for lane in participant.get("lanes", []):
                if isinstance(lane, Mapping):
                    for node_id in lane.get("nodeRefs", []):
                        if node_id in owners:
                            owners[node_id] = participant_id
        raw_layout_nodes = layout.get("nodes", {})
        if isinstance(raw_layout_nodes, Mapping):
            for node_id, geometry in raw_layout_nodes.items():
                if isinstance(geometry, Mapping) and geometry.get("participant_id") in participant_ids:
                    owners[str(node_id)] = str(geometry["participant_id"])
        return owners

    @staticmethod
    def _create_processes(
        definitions: ET.Element,
        process_id: str,
        process_name: str,
        executable: bool,
        participants: list[dict[str, Any]],
        ownership: Mapping[str, str | None],
        force_collaboration: bool = False,
    ) -> tuple[dict[str | None, ET.Element], dict[str, str | None], str | None]:
        process_elements: dict[str | None, ET.Element] = {}
        process_for_node = dict(ownership)
        if not participants:
            process_elements[None] = ET.SubElement(
                definitions,
                tag(BPMN_NS, "process"),
                {"id": process_id, "name": process_name, "isExecutable": str(executable).lower()},
            )
            if not force_collaboration:
                return process_elements, process_for_node, None
            collaboration_id = f"Collaboration_{process_id}"
            ET.SubElement(
                definitions, tag(BPMN_NS, "collaboration"), {"id": collaboration_id}
            )
            return process_elements, process_for_node, collaboration_id

        collaboration_id = f"Collaboration_{process_id}"
        collaboration = ET.SubElement(
            definitions, tag(BPMN_NS, "collaboration"), {"id": collaboration_id}
        )
        for index, participant in enumerate(participants):
            participant_id = str(participant["id"])
            owned_process_id = process_id if index == 0 else f"{process_id}_{participant_id}"
            process_elements[participant_id] = ET.SubElement(
                definitions,
                tag(BPMN_NS, "process"),
                {
                    "id": owned_process_id,
                    "name": process_name if index == 0 else str(participant.get("name") or process_name),
                    "isExecutable": str(executable).lower(),
                },
            )
            ET.SubElement(
                collaboration,
                tag(BPMN_NS, "participant"),
                {
                    "id": participant_id,
                    "name": str(participant.get("name") or participant_id),
                    "processRef": owned_process_id,
                },
            )
        if any(owner is None for owner in ownership.values()):
            unowned_id = f"{process_id}_unassigned"
            process_elements[None] = ET.SubElement(
                definitions,
                tag(BPMN_NS, "process"),
                {"id": unowned_id, "name": f"{process_name} - unassigned", "isExecutable": "false"},
            )
        return process_elements, process_for_node, collaboration_id

    @staticmethod
    def _create_lanes(
        processes: Mapping[str | None, ET.Element],
        process_for_node: Mapping[str, str | None],
        participants: list[dict[str, Any]],
        workflow: Mapping[str, Any],
        layout: Mapping[str, Any],
    ) -> None:
        lane_names: dict[str, str] = {}
        for participant in participants:
            for lane in participant.get("lanes", []):
                if isinstance(lane, Mapping) and isinstance(lane.get("id"), str):
                    lane_names[lane["id"]] = str(lane.get("name") or lane["id"])
        for lane in workflow.get("lanes", []):
            if isinstance(lane, Mapping) and isinstance(lane.get("id"), str):
                lane_names[lane["id"]] = str(lane.get("name") or lane["id"])

        raw_lanes = layout.get("lanes", {})
        raw_nodes = layout.get("nodes", {})
        if not isinstance(raw_lanes, Mapping) or not isinstance(raw_nodes, Mapping):
            return
        grouped: dict[str | None, list[tuple[str, Mapping[str, Any]]]] = {}
        for lane_id, lane_geometry in raw_lanes.items():
            if not isinstance(lane_geometry, Mapping):
                continue
            owner = lane_geometry.get("participant_id")
            grouped.setdefault(owner, []).append((str(lane_id), lane_geometry))
        for owner, lanes in grouped.items():
            process = processes.get(owner)
            if process is None:
                continue
            lane_set = ET.SubElement(process, tag(BPMN_NS, "laneSet"), {"id": f"LaneSet_{owner or 'main'}"})
            for lane_id, _ in sorted(lanes):
                lane_element = ET.SubElement(
                    lane_set,
                    tag(BPMN_NS, "lane"),
                    {"id": lane_id, "name": lane_names.get(lane_id, lane_id)},
                )
                for node_id, geometry in sorted(raw_nodes.items()):
                    if (
                        isinstance(geometry, Mapping)
                        and geometry.get("lane_id") == lane_id
                        and process_for_node.get(str(node_id)) == owner
                    ):
                        reference = ET.SubElement(lane_element, tag(BPMN_NS, "flowNodeRef"))
                        reference.text = str(node_id)

    @staticmethod
    def _create_nodes(
        processes: Mapping[str | None, ET.Element],
        process_for_node: Mapping[str, str | None],
        nodes: Mapping[str, dict[str, Any]],
    ) -> None:
        for node_id, node in nodes.items():
            node_type = node.get("type")
            element_name = NODE_TAGS.get(str(node_type))
            if element_name is None:
                raise ValueError(f"Type BPMN non pris en charge : '{node_type}'.")
            process = processes.get(process_for_node.get(node_id))
            if process is None:
                raise ValueError(f"Aucun processus ne contient le nœud '{node_id}'.")
            attributes = {"id": node_id, "name": str(node.get("name") or node_id)}
            element = ET.SubElement(process, tag(BPMN_NS, element_name), attributes)
            if node_type == "timerEvent" or (
                node_type == "intermediateCatchEvent" and get_timer_value(node)
            ):
                add_timer_definition(element, node)

    @staticmethod
    def _create_sequence_flows(
        processes: Mapping[str | None, ET.Element],
        process_for_node: Mapping[str, str | None],
        nodes: Mapping[str, dict[str, Any]],
        flows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for flow in flows:
            flow_id = flow.get("id")
            source = flow.get("source")
            target = flow.get("target")
            if not all(isinstance(value, str) and value for value in (flow_id, source, target)):
                raise ValueError("Un sequence flow possède un identifiant ou des références invalides.")
            if source not in nodes or target not in nodes:
                raise ValueError(f"Le sequence flow '{flow_id}' référence un nœud inconnu.")
            owner = process_for_node.get(source)
            if process_for_node.get(target) != owner:
                raise ValueError(f"Le sequence flow '{flow_id}' traverse deux pools.")
            process = processes.get(owner)
            if process is None:
                raise ValueError(f"Aucun processus ne contient le sequence flow '{flow_id}'.")
            label = get_flow_label(flow)
            attributes = {"id": flow_id, "sourceRef": source, "targetRef": target}
            if label:
                attributes["name"] = str(label)
            element = ET.SubElement(process, tag(BPMN_NS, "sequenceFlow"), attributes)
            add_condition(element, flow.get("condition"))
            result.append({**flow, "label": label})

        defaults = {flow["source"]: flow["id"] for flow in result if flow.get("isDefault")}
        for process in processes.values():
            for element in process.iter():
                if element.get("id") in defaults:
                    element.set("default", defaults[element.get("id")])
        return result

    @staticmethod
    def _create_message_flows(
        definitions: ET.Element,
        collaboration_id: str | None,
        nodes: Mapping[str, dict[str, Any]],
        participants: list[dict[str, Any]],
        message_flows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not message_flows:
            return []
        if collaboration_id is None:
            raise ValueError("Les message flows nécessitent au moins une collaboration.")
        collaboration = next(
            element for element in definitions if element.get("id") == collaboration_id
        )
        endpoints = set(nodes) | {str(item.get("id")) for item in participants}
        result = []
        for flow in message_flows:
            flow_id = flow.get("id")
            source = flow.get("source")
            target = flow.get("target")
            if not all(isinstance(value, str) and value for value in (flow_id, source, target)):
                raise ValueError("Un message flow possède un identifiant ou des références invalides.")
            if source not in endpoints or target not in endpoints:
                raise ValueError(f"Le message flow '{flow_id}' référence un élément inconnu.")
            attributes = {"id": flow_id, "sourceRef": source, "targetRef": target}
            if flow.get("name"):
                attributes["name"] = str(flow["name"])
            ET.SubElement(collaboration, tag(BPMN_NS, "messageFlow"), attributes)
            result.append(flow)
        return result

    @staticmethod
    def _create_diagram(
        definitions: ET.Element,
        plane_element: str,
        process_id: str,
        nodes: Mapping[str, Mapping[str, float]],
        participants: Mapping[str, Mapping[str, float]],
        lanes: Mapping[str, Mapping[str, float]],
        flows: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> None:
        diagram = ET.SubElement(
            definitions, tag(BPMNDI_NS, "BPMNDiagram"), {"id": f"BPMNDiagram_{process_id}"}
        )
        plane = ET.SubElement(
            diagram,
            tag(BPMNDI_NS, "BPMNPlane"),
            {"id": f"BPMNPlane_{process_id}", "bpmnElement": plane_element},
        )
        for collection, prefix, horizontal in (
            (participants, "Participant", True),
            (lanes, "Lane", True),
            (nodes, "Shape", False),
        ):
            for identifier, rectangle in sorted(collection.items()):
                attributes = {"id": f"{prefix}_{identifier}", "bpmnElement": identifier}
                if horizontal:
                    attributes["isHorizontal"] = "true"
                shape = ET.SubElement(plane, tag(BPMNDI_NS, "BPMNShape"), attributes)
                ET.SubElement(
                    shape,
                    tag(DC_NS, "Bounds"),
                    {key: str(rectangle[key]) for key in ("x", "y", "width", "height")},
                )

        geometry = {**participants, **nodes}
        for flow in [*flows, *messages]:
            source = str(flow["source"])
            target = str(flow["target"])
            if source not in geometry or target not in geometry:
                raise ValueError(f"Géométrie absente pour le flux '{flow.get('id')}'.")
            waypoints = calculate_edge_waypoints(source, target, geometry)
            edge = ET.SubElement(
                plane,
                tag(BPMNDI_NS, "BPMNEdge"),
                {"id": f"Edge_{flow['id']}", "bpmnElement": str(flow["id"])},
            )
            create_edge_waypoints(edge, waypoints)
            add_edge_label(edge, flow, waypoints)
