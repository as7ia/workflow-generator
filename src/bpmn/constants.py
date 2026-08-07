
import xml.etree.ElementTree as ET


BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


ET.register_namespace("bpmn", BPMN_NS)
ET.register_namespace("bpmndi", BPMNDI_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("di", DI_NS)
ET.register_namespace("xsi", XSI_NS)


NODE_TAGS = {
    "startEvent": "startEvent",
    "endEvent": "endEvent",
    "userTask": "userTask",
    "serviceTask": "serviceTask",
    "manualTask": "manualTask",
    "scriptTask": "scriptTask",
    "businessRuleTask": "businessRuleTask",
    "sendTask": "sendTask",
    "receiveTask": "receiveTask",
    "exclusiveGateway": "exclusiveGateway",
    "parallelGateway": "parallelGateway",
    "inclusiveGateway": "inclusiveGateway",
    "eventBasedGateway": "eventBasedGateway",
    "intermediateCatchEvent": "intermediateCatchEvent",
    "intermediateThrowEvent": "intermediateThrowEvent",
    "timerEvent": "intermediateCatchEvent",
    "subProcess": "subProcess",
}


GATEWAY_TYPES = {
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
    "eventBasedGateway",
}


EVENT_TYPES = {
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
    "timerEvent",
}


# BPMN diagram dimensions.  Keeping geometry here makes layout policy
# configurable without coupling it to XML generation or routing.
START_EVENT_WIDTH = 36
START_EVENT_HEIGHT = 36
END_EVENT_WIDTH = 36
END_EVENT_HEIGHT = 36

TASK_WIDTH = 120
TASK_HEIGHT = 80

GATEWAY_WIDTH = 50
GATEWAY_HEIGHT = 50

SUBPROCESS_WIDTH = 180
SUBPROCESS_HEIGHT = 120

GENERIC_NODE_WIDTH = 100
GENERIC_NODE_HEIGHT = 80
INTERMEDIATE_EVENT_WIDTH = 36
INTERMEDIATE_EVENT_HEIGHT = 36

HORIZONTAL_SPACING = 100
VERTICAL_SPACING = 70

POOL_HEADER_WIDTH = 30
POOL_PADDING = 40
LANE_PADDING = 30
LANE_MIN_HEIGHT = 150
POOL_SPACING = 80

BACK_EDGE_MARGIN = 80
BRANCH_MARGIN = 50

CANVAS_MARGIN = 40
COMPONENT_SPACING = 150
