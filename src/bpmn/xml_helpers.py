
import xml.etree.ElementTree as ET

from .constants import (
    BPMN_NS,
    BPMNDI_NS,
    DC_NS,
    XSI_NS,
)


def tag(namespace, name):
    return f"{{{namespace}}}{name}"


def add_condition(sequence_flow, condition):
    if not condition:
        return

    if isinstance(condition, dict):
        expression = (
            condition.get("expression")
            or condition.get("value")
            or condition.get("text")
        )
    else:
        expression = condition

    if not expression:
        return

    condition_element = ET.SubElement(
        sequence_flow,
        tag(BPMN_NS, "conditionExpression"),
        {
            tag(XSI_NS, "type"): "bpmn:tFormalExpression",
        },
    )

    condition_element.text = str(expression)


def get_flow_label(flow):
    condition = (
        flow.get("condition")
        or flow.get("conditionExpression")
    )

    label = flow.get("name")

    if not label and condition:
        if isinstance(condition, dict):
            label = (
                condition.get("expression")
                or condition.get("value")
                or condition.get("text")
            )
        else:
            label = str(condition)

    return label


def add_edge_label(edge, flow, waypoints):
    label = flow.get("label")

    if not label or len(waypoints) < 2:
        return

    middle_index = (len(waypoints) - 1) // 2

    start = waypoints[middle_index]
    end = waypoints[middle_index + 1]

    label_x = (start[0] + end[0]) / 2 - 45
    label_y = (start[1] + end[1]) / 2 - 25

    bpmn_label = ET.SubElement(
        edge,
        tag(BPMNDI_NS, "BPMNLabel"),
    )

    ET.SubElement(
        bpmn_label,
        tag(DC_NS, "Bounds"),
        {
            "x": str(label_x),
            "y": str(label_y),
            "width": "90",
            "height": "20",
        },
    )
