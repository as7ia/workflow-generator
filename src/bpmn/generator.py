"""Deterministic BPMN 2.0 XML generation from canonical workflow v2."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any
from .constants import BPMN_NS,BPMNDI_NS,DC_NS,NODE_TAGS
from .routing import calculate_routes,create_edge_waypoints
from .xml_helpers import add_condition,add_edge_label,get_flow_label,tag

class BPMNGenerator:
    """Serialize canonical processes and collaboration without ownership inference."""
    def generate(self,workflow:dict[str,Any],layout:dict[str,Any])->str:
        if not isinstance(workflow,Mapping):raise TypeError("Le workflow doit être un objet JSON.")
        if not isinstance(layout,Mapping):raise TypeError("Le layout BPMN doit être un objet.")
        processes=self._index(self._objects(workflow.get("processes")),"process")
        participants=self._index(self._objects(workflow.get("participants")),"participant")
        lanes=self._index(self._objects(workflow.get("lanes")),"lane")
        nodes=self._index(self._objects(workflow.get("nodes")),"node")
        flows=self._index(self._objects(workflow.get("flows")),"sequence flow")
        messages=self._index(self._objects(workflow.get("messageFlows")),"message flow")
        if not processes:raise ValueError("Le workflow canonique ne contient aucun process.")
        if not nodes:raise ValueError("Le workflow ne contient aucun nœud.")
        node_geometry=self._geometry(layout.get("nodes"),"nœuds");participant_geometry=self._geometry(layout.get("participants"),"participants");lane_geometry=self._geometry(layout.get("lanes"),"lanes")
        for nid in nodes:
            if nid not in node_geometry:raise ValueError(f"Coordonnées absentes pour le nœud '{nid}'.")
        first_process=next(iter(processes));definitions=ET.Element(tag(BPMN_NS,"definitions"),{"id":f"Definitions_{first_process}","targetNamespace":"http://workflow-generator.local/bpmn"})
        process_elements={pid:ET.SubElement(definitions,tag(BPMN_NS,"process"),{"id":pid,"name":str(p.get("name") or pid),"isExecutable":str(bool(p.get("executable",False))).lower()}) for pid,p in processes.items()}
        collaboration_id=None
        if participants or messages:
            collaboration_id=f"Collaboration_{first_process}";collaboration=ET.SubElement(definitions,tag(BPMN_NS,"collaboration"),{"id":collaboration_id})
            for participant_id,p in participants.items():
                process_ref=p.get("processRef")
                if process_ref not in processes:raise ValueError(f"Participant '{participant_id}' référence un process inconnu.")
                ET.SubElement(collaboration,tag(BPMN_NS,"participant"),{"id":participant_id,"name":str(p.get("name") or participant_id),"processRef":str(process_ref)})
        self._create_lanes(process_elements,lanes,nodes)
        self._create_nodes(process_elements,nodes)
        sequence=self._create_sequence_flows(process_elements,nodes,flows)
        message=self._create_message_flows(definitions,collaboration_id,nodes,participants,messages)
        plane_element=collaboration_id or first_process
        self._create_diagram(definitions,plane_element,first_process,node_geometry,
            {k:v for k,v in participant_geometry.items() if k in participants},
            {k:v for k,v in lane_geometry.items() if k in lanes},sequence,message,layout)
        return ET.tostring(definitions,encoding="unicode",xml_declaration=True)

    @staticmethod
    def _objects(value):return [dict(x) for x in value if isinstance(x,Mapping)] if isinstance(value,list) else []
    @staticmethod
    def _index(items,label):
        result={}
        for item in items:
            iid=item.get("id")
            if not isinstance(iid,str) or not iid:raise ValueError(f"Un {label} ne possède pas d'identifiant valide.")
            if iid in result:raise ValueError(f"Identifiant de {label} dupliqué : '{iid}'.")
            result[iid]=item
        return {k:result[k] for k in sorted(result)}
    @staticmethod
    def _geometry(value,label):
        if not isinstance(value,Mapping):raise ValueError(f"Le layout des {label} est absent.")
        result={}
        for iid,rect in value.items():
            if not isinstance(rect,Mapping):continue
            try:result[str(iid)]={k:float(rect[k]) for k in ("x","y","width","height")}
            except (KeyError,TypeError,ValueError) as exc:raise ValueError(f"Géométrie invalide pour '{iid}'.") from exc
        return result

    @staticmethod
    def _create_lanes(process_elements,lanes,nodes):
        by_process={pid:[] for pid in process_elements}
        for lane_id,lane in lanes.items():
            pid=lane.get("processRef")
            if pid not in process_elements:raise ValueError(f"Lane '{lane_id}' référence un process inconnu.")
            by_process[pid].append((lane_id,lane))
        for pid,items in by_process.items():
            if not items:continue
            lane_set=ET.SubElement(process_elements[pid],tag(BPMN_NS,"laneSet"),{"id":f"LaneSet_{pid}"})
            for lane_id,lane in sorted(items):
                element=ET.SubElement(lane_set,tag(BPMN_NS,"lane"),{"id":lane_id,"name":str(lane.get("name") or lane_id)})
                for nid,node in nodes.items():
                    if node.get("processRef")==pid and node.get("laneRef")==lane_id:
                        ref=ET.SubElement(element,tag(BPMN_NS,"flowNodeRef"));ref.text=nid

    @staticmethod
    def _create_nodes(process_elements,nodes):
        for nid,node in nodes.items():
            pid=node.get("processRef")
            if pid not in process_elements:raise ValueError(f"Node '{nid}' référence un process inconnu.")
            node_type=node.get("type");element_name=NODE_TAGS.get(str(node_type))
            if element_name is None:raise ValueError(f"Type BPMN v2 non pris en charge : '{node_type}'.")
            element=ET.SubElement(process_elements[pid],tag(BPMN_NS,element_name),{"id":nid,"name":str(node.get("name") or nid)})
            timer=node.get("properties",{}).get("timer") if isinstance(node.get("properties"),Mapping) else None
            if node_type=="intermediateCatchEvent" and isinstance(timer,Mapping):
                kind=timer.get("kind");value=timer.get("value");mapping={"duration":"timeDuration","date":"timeDate","cycle":"timeCycle"}
                if kind not in mapping or not isinstance(value,str) or not value:raise ValueError(f"Timer invalide pour le node '{nid}'.")
                definition=ET.SubElement(element,tag(BPMN_NS,"timerEventDefinition"));time=ET.SubElement(definition,tag(BPMN_NS,mapping[kind]));time.text=value

    @staticmethod
    def _create_sequence_flows(process_elements,nodes,flows):
        result=[]
        for fid,flow in flows.items():
            source,target,pid=flow.get("source"),flow.get("target"),flow.get("processRef")
            if source not in nodes or target not in nodes:raise ValueError(f"Le sequence flow '{fid}' référence un node inconnu.")
            if pid not in process_elements or nodes[source].get("processRef")!=pid or nodes[target].get("processRef")!=pid:raise ValueError(f"Le sequence flow '{fid}' ne reste pas dans son process.")
            label=get_flow_label(flow);attributes={"id":fid,"sourceRef":source,"targetRef":target}
            if label:attributes["name"]=str(label)
            element=ET.SubElement(process_elements[pid],tag(BPMN_NS,"sequenceFlow"),attributes);add_condition(element,flow.get("condition"));result.append({**flow,"label":label})
        defaults={f["source"]:f["id"] for f in result if f.get("isDefault")}
        for process in process_elements.values():
            for element in process.iter():
                if element.get("id") in defaults:element.set("default",defaults[element.get("id")])
        return result

    @staticmethod
    def _create_message_flows(definitions,collaboration_id,nodes,participants,messages):
        if not messages:return []
        if collaboration_id is None:raise ValueError("Les message flows nécessitent une collaboration.")
        collaboration=next(x for x in definitions if x.get("id")==collaboration_id);endpoints=set(nodes)|set(participants);result=[]
        for fid,flow in messages.items():
            source,target=flow.get("source"),flow.get("target")
            if source not in endpoints or target not in endpoints:raise ValueError(f"Le message flow '{fid}' référence un élément inconnu.")
            attributes={"id":fid,"sourceRef":source,"targetRef":target}
            if flow.get("name"):attributes["name"]=str(flow["name"])
            ET.SubElement(collaboration,tag(BPMN_NS,"messageFlow"),attributes);result.append(flow)
        return result

    @staticmethod
    def _create_diagram(definitions,plane_element,base_id,nodes,participants,lanes,flows,messages,layout):
        diagram=ET.SubElement(definitions,tag(BPMNDI_NS,"BPMNDiagram"),{"id":f"BPMNDiagram_{base_id}"});plane=ET.SubElement(diagram,tag(BPMNDI_NS,"BPMNPlane"),{"id":f"BPMNPlane_{base_id}","bpmnElement":plane_element})
        for collection,prefix,horizontal in ((participants,"Participant",True),(lanes,"Lane",True),(nodes,"Shape",False)):
            for iid,rect in sorted(collection.items()):
                attrs={"id":f"{prefix}_{iid}","bpmnElement":iid}
                if horizontal:attrs["isHorizontal"]="true"
                shape=ET.SubElement(plane,tag(BPMNDI_NS,"BPMNShape"),attrs);ET.SubElement(shape,tag(DC_NS,"Bounds"),{k:str(rect[k]) for k in ("x","y","width","height")})
        geometry={**participants,**nodes};routes=calculate_routes(flows,messages,geometry,layout)
        for flow in [*flows,*messages]:
            source,target=str(flow["source"]),str(flow["target"])
            if source not in geometry or target not in geometry:raise ValueError(f"Géométrie absente pour le flux '{flow.get('id')}'.")
            waypoints=routes[str(flow["id"])];edge=ET.SubElement(plane,tag(BPMNDI_NS,"BPMNEdge"),{"id":f"Edge_{flow['id']}","bpmnElement":str(flow["id"])});create_edge_waypoints(edge,waypoints);add_edge_label(edge,flow,waypoints)
