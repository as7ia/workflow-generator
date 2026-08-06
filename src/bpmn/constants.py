
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
    "sendTask": "sendTask",
    "receiveTask": "receiveTask",
    "exclusiveGateway": "exclusiveGateway",
    "parallelGateway": "parallelGateway",
    "inclusiveGateway": "inclusiveGateway",
    "intermediateCatchEvent": "intermediateCatchEvent",
    "timerEvent": "intermediateCatchEvent",
}


GATEWAY_TYPES = {
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
}


EVENT_TYPES = {
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "timerEvent",
}
