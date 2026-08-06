
import json
import os
import sys
import xml.etree.ElementTree as ET

from bpmn.constants import (
    BPMN_NS,
    BPMNDI_NS,
    DC_NS,
    NODE_TAGS,
)

from bpmn.layout import (
    calculate_positions,
    normalize_flows,
)

from bpmn.nodes import add_timer_definition

from bpmn.routing import (
    calculate_edge_waypoints,
    create_edge_waypoints,
)

from bpmn.xml_helpers import (
    add_condition,
    add_edge_label,
    get_flow_label,
    tag,
)


def convert_json_to_bpmn(json_path, output_path):
    with open(json_path, "r", encoding="utf-8") as file:
        workflow = json.load(file)

    process_id = workflow.get("id", "Process_1")
    process_name = workflow.get("name", "Workflow")

    nodes = workflow.get("nodes", [])
    flows = normalize_flows(
        workflow.get("flows", [])
    )

    if not nodes:
        raise ValueError(
            "Le workflow ne contient aucun noeud."
        )

    definitions = ET.Element(
        tag(BPMN_NS, "definitions"),
        {
            "id": f"Definitions_{process_id}",
            "targetNamespace":
                "http://workflow-generator.local/bpmn",
        },
    )

    process = ET.SubElement(
        definitions,
        tag(BPMN_NS, "process"),
        {
            "id": process_id,
            "name": process_name,
            "isExecutable": "true",
        },
    )

    supported_nodes = []

    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        node_name = node.get("name") or node_id

        if not node_id:
            raise ValueError(
                f"Noeud sans identifiant : {node}"
            )

        if node_type not in NODE_TAGS:
            print(
                f"Type non pris en charge : {node_type}"
            )
            continue

        element = ET.SubElement(
            process,
            tag(BPMN_NS, NODE_TAGS[node_type]),
            {
                "id": node_id,
                "name": node_name,
            },
        )

        if node_type in {
            "timerEvent",
            "intermediateCatchEvent",
        }:
            add_timer_definition(element, node)

        supported_nodes.append(node)

    supported_ids = {
        node["id"]
        for node in supported_nodes
    }

    valid_flows = []

    for flow in flows:
        flow_id = flow.get("id")
        source = flow.get("source")
        target = flow.get("target")

        if (
            not flow_id
            or source not in supported_ids
            or target not in supported_ids
        ):
            continue

        condition = (
            flow.get("condition")
            or flow.get("conditionExpression")
        )

        flow_label = get_flow_label(flow)

        attributes = {
            "id": flow_id,
            "sourceRef": source,
            "targetRef": target,
        }

        if flow_label:
            attributes["name"] = flow_label

        sequence_flow = ET.SubElement(
            process,
            tag(BPMN_NS, "sequenceFlow"),
            attributes,
        )

        add_condition(
            sequence_flow,
            condition,
        )

        valid_flows.append(
            {
                "id": flow_id,
                "source": source,
                "target": target,
                "label": flow_label,
            }
        )

    node_positions = calculate_positions(
        supported_nodes,
        valid_flows,
    )

    diagram = ET.SubElement(
        definitions,
        tag(BPMNDI_NS, "BPMNDiagram"),
        {
            "id": f"BPMNDiagram_{process_id}",
        },
    )

    plane = ET.SubElement(
        diagram,
        tag(BPMNDI_NS, "BPMNPlane"),
        {
            "id": f"BPMNPlane_{process_id}",
            "bpmnElement": process_id,
        },
    )

    for node in supported_nodes:
        node_id = node["id"]
        position = node_positions[node_id]

        shape = ET.SubElement(
            plane,
            tag(BPMNDI_NS, "BPMNShape"),
            {
                "id": f"{node_id}_di",
                "bpmnElement": node_id,
            },
        )

        ET.SubElement(
            shape,
            tag(DC_NS, "Bounds"),
            {
                "x": str(position["x"]),
                "y": str(position["y"]),
                "width": str(position["width"]),
                "height": str(position["height"]),
            },
        )

    for flow in valid_flows:
        edge = ET.SubElement(
            plane,
            tag(BPMNDI_NS, "BPMNEdge"),
            {
                "id": f'{flow["id"]}_di',
                "bpmnElement": flow["id"],
            },
        )

        waypoints = calculate_edge_waypoints(
            flow["source"],
            flow["target"],
            node_positions,
        )

        create_edge_waypoints(
            edge,
            waypoints,
        )

        add_edge_label(
            edge,
            flow,
            waypoints,
        )

    output_directory = os.path.dirname(
        output_path
    )

    if output_directory:
        os.makedirs(
            output_directory,
            exist_ok=True,
        )

    tree = ET.ElementTree(definitions)
    ET.indent(tree, space="  ")

    tree.write(
        output_path,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(
        f"Fichier BPMN généré : {output_path}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Utilisation : python src/json_to_bpmn.py "
            "<workflow.json> <workflow.bpmn>"
        )
        sys.exit(1)

    convert_json_to_bpmn(
        sys.argv[1],
        sys.argv[2],
    )
