
import xml.etree.ElementTree as ET

from .constants import BPMN_NS, EVENT_TYPES, GATEWAY_TYPES
from .xml_helpers import tag


def get_node_size(node_type):
    if node_type in EVENT_TYPES:
        return 36, 36

    if node_type in GATEWAY_TYPES:
        return 50, 50

    return 120, 80


def get_timer_value(node):
    possible_keys = {
        "duration",
        "value",
        "expression",
        "timeDuration",
        "isoDuration",
        "timerDuration",
        "timeCycle",
        "timeDate",
    }

    def search_value(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if (
                    key in possible_keys
                    and isinstance(value, (str, int, float))
                ):
                    return str(value)

            for value in data.values():
                result = search_value(value)

                if result is not None:
                    return result

        elif isinstance(data, list):
            for item in data:
                result = search_value(item)

                if result is not None:
                    return result

        return None

    return search_value(node)


def add_timer_definition(element, node):
    timer_definition = ET.SubElement(
        element,
        tag(BPMN_NS, "timerEventDefinition"),
    )

    timer_value = get_timer_value(node)

    if not timer_value:
        print(
            f"Avertissement : aucune durée trouvée "
            f"pour le timer {node.get('id')}"
        )
        return

    duration_element = ET.SubElement(
        timer_definition,
        tag(BPMN_NS, "timeDuration"),
    )

    duration_element.text = str(timer_value)
