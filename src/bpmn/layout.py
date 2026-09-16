"""Deterministic process-scoped layout for canonical workflow model v2."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

try:
    from workflow.graph_analyzer import WorkflowGraphAnalyzer
except ModuleNotFoundError:  # pragma: no cover
    from src.workflow.graph_analyzer import WorkflowGraphAnalyzer

from .constants import (BACK_EDGE_MARGIN, BRANCH_MARGIN, CANVAS_MARGIN,
    COMPONENT_SPACING, END_EVENT_HEIGHT, END_EVENT_WIDTH, GATEWAY_HEIGHT,
    GATEWAY_WIDTH, GENERIC_NODE_HEIGHT, GENERIC_NODE_WIDTH, HORIZONTAL_SPACING,
    INTERMEDIATE_EVENT_HEIGHT, INTERMEDIATE_EVENT_WIDTH, LANE_MIN_HEIGHT,
    LANE_PADDING, POOL_HEADER_WIDTH, POOL_PADDING, POOL_SPACING,
    START_EVENT_HEIGHT, START_EVENT_WIDTH, SUBPROCESS_HEIGHT, SUBPROCESS_WIDTH,
    TASK_HEIGHT, TASK_WIDTH, VERTICAL_SPACING)

def _rectangles_overlap(a,b,margin=0.0):
    return (a["x"]<b["x"]+b["width"]+margin and a["x"]+a["width"]+margin>b["x"]
        and a["y"]<b["y"]+b["height"]+margin and a["y"]+a["height"]+margin>b["y"])

def detect_shape_collisions(shapes,margin=0.0):
    """Return stable pairs of overlapping BPMN shapes."""
    ids=sorted(shapes);collisions=[]
    for index,shape_id in enumerate(ids):
        for other_id in ids[index+1:]:
            if _rectangles_overlap(shapes[shape_id],shapes[other_id],margin):collisions.append((shape_id,other_id))
    return collisions

class BPMNLayoutEngine:
    _TASK_TYPES={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask"}
    _GATEWAY_TYPES={"exclusiveGateway","parallelGateway","inclusiveGateway","eventBasedGateway"}
    _INTERMEDIATE_EVENT_TYPES={"intermediateCatchEvent","intermediateThrowEvent"}

    def __init__(self): self._warnings=[]

    def layout(self,workflow:dict[str,Any],analysis:dict[str,Any]|None=None)->dict[str,Any]:
        """Lay out canonical nodes from process-scoped analysis without mutation."""
        self._warnings=[]; source=workflow if isinstance(workflow,Mapping) else {}
        graph=analysis if isinstance(analysis,Mapping) else WorkflowGraphAnalyzer().analyze(dict(source))
        process_analyses=graph.get("processes",{}) if isinstance(graph.get("processes",{}),Mapping) else {}
        collaboration=graph.get("collaboration",{}) if isinstance(graph.get("collaboration",{}),Mapping) else {}
        node_by_id=self._index(source.get("nodes")); node_process=collaboration.get("node_to_process",{})
        node_participant=collaboration.get("node_to_participant",{}); node_lane=collaboration.get("node_to_lane",{})
        participants=collaboration.get("participants",{}); lanes=collaboration.get("lanes",{})
        if not isinstance(participants,Mapping): participants={}
        if not isinstance(lanes,Mapping): lanes={}
        nodes={}; process_layout={}; participant_layout={}; lane_layout={}; cursor=float(CANVAS_MARGIN)

        for pid in sorted(process_analyses):
            pa=process_analyses[pid] if isinstance(process_analyses[pid],Mapping) else {}
            ids=sorted(n for n in pa.get("node_ids",[]) if n in node_by_id and node_process.get(n)==pid)
            branch=self._branch_indexes(pa,ids); components=self._component_indexes(pa.get("components",[]),ids)
            for nid in ids:
                width,height=self._get_node_size(node_by_id[nid].get("type"))
                nodes[nid]={"x":0.0,"y":0.0,"width":width,"height":height,
                    "process_id":pid,"participant_id":node_participant.get(nid),"lane_id":node_lane.get(nid),
                    "level":int(pa.get("levels",{}).get(nid,0)),"branch_index":branch.get(nid,0),"_component":components.get(nid,0)}
            pitch=max([GENERIC_NODE_WIDTH]+[nodes[n]["width"] for n in ids])+HORIZONTAL_SPACING
            process_participants=[p for p,v in participants.items() if isinstance(v,Mapping) and v.get("processRef")==pid]
            process_top=cursor
            if not process_participants:
                self._warn("PROCESS_WITHOUT_PARTICIPANT",f"Process '{pid}' has no participant.",process_id=pid)
            for participant_id in sorted(process_participants):
                pdata=participants[participant_id]; pnodes=[n for n in ids if node_participant.get(n)==participant_id]
                pool_x=float(CANVAS_MARGIN); pool_y=cursor
                max_level=max((nodes[n]["level"] for n in ids),default=0)
                pool_width=float(POOL_HEADER_WIDTH+2*POOL_PADDING+max_level*pitch+max([GENERIC_NODE_WIDTH]+[nodes[n]["width"] for n in ids]))
                lane_cursor=pool_y
                participant_lanes=[lid for lid,v in lanes.items() if isinstance(v,Mapping) and v.get("participantRef")==participant_id and v.get("processRef")==pid]
                for lane_id in sorted(participant_lanes):
                    members=[n for n in pnodes if node_lane.get(n)==lane_id]
                    height=self._place_group(members,nodes,lane_cursor,pool_x+POOL_HEADER_WIDTH+POOL_PADDING,pitch)
                    lane_layout[lane_id]={"processRef":pid,"participantRef":participant_id,"x":pool_x+POOL_HEADER_WIDTH,
                        "y":lane_cursor,"width":pool_width-POOL_HEADER_WIDTH,"height":height}
                    lane_cursor+=height
                unlaned=[n for n in pnodes if node_lane.get(n) not in participant_lanes]
                if unlaned:
                    for nid in unlaned: self._warn("NODE_WITHOUT_LANE",f"Node '{nid}' has no canonical lane.",node_id=nid,process_id=pid,container_id=participant_id)
                    height=self._place_group(unlaned,nodes,lane_cursor+POOL_PADDING,pool_x+POOL_HEADER_WIDTH+POOL_PADDING,pitch)
                    lane_cursor+=POOL_PADDING+height
                pool_height=max(float(LANE_MIN_HEIGHT),lane_cursor-pool_y)
                participant_layout[participant_id]={"processRef":pdata.get("processRef"),"x":pool_x,"y":pool_y,"width":pool_width,"height":pool_height}
                cursor=pool_y+pool_height+POOL_SPACING
            technical=[n for n in ids if node_participant.get(n) not in process_participants]
            if technical:
                for nid in technical:
                    if process_participants: self._warn("NODE_WITHOUT_PARTICIPANT",f"Node '{nid}' has no canonical participant.",node_id=nid,process_id=pid)
                top=cursor if process_participants else process_top
                height=self._place_group(technical,nodes,top+POOL_PADDING,CANVAS_MARGIN+POOL_PADDING,pitch)
                cursor=top+height+2*POOL_PADDING+POOL_SPACING
            owned_rects=[v for k,v in participant_layout.items() if v["processRef"]==pid]+[nodes[n] for n in ids]
            bounds=self._bounds(owned_rects,process_top)
            channels=self._back_channels(pa)
            process_layout[pid]={"bounds":bounds,"nodes":{n:nodes[n] for n in ids},"back_edge_channels":channels}
            cursor=max(cursor,bounds["y"]+bounds["height"]+POOL_SPACING)

        self._center_joins(process_analyses,nodes)
        self._validate(nodes,participant_layout,lane_layout)
        for entry in nodes.values(): entry.pop("_component",None)
        width,height=self._canvas_size(nodes,participant_layout,process_layout)
        messages=self._message_connections(collaboration)
        global_channels={fid:value for pid in sorted(process_layout) for fid,value in process_layout[pid]["back_edge_channels"].items()}
        warnings=sorted(self._warnings,key=lambda w:tuple(str(w.get(k) or "") for k in ("code","process_id","node_id","container_id")))
        shape_collisions=detect_shape_collisions(nodes)
        return {"processes":process_layout,"participants":{k:participant_layout[k] for k in sorted(participant_layout)},
            "lanes":{k:lane_layout[k] for k in sorted(lane_layout)},"nodes":{k:nodes[k] for k in sorted(nodes)},
            "metadata":{"orientation":"horizontal","width":width,"height":height,"warnings":warnings,
                "back_edge_channels":global_channels,"message_flow_connections":messages,
                "layout_metrics":{"shape_collisions":len(shape_collisions),"back_edges":len(global_channels),"message_flows":len(messages)}}}

    def _place_group(self,ids,nodes,top,left,pitch):
        if not ids: return float(LANE_MIN_HEIGHT)
        corridor=max(SUBPROCESS_HEIGHT,TASK_HEIGHT)+VERTICAL_SPACING; placed=[]
        min_branch=min(nodes[n]["branch_index"] for n in ids)
        for nid in sorted(ids,key=lambda n:(nodes[n]["_component"],nodes[n]["branch_index"],nodes[n]["level"],n)):
            e=nodes[nid]; e["x"]=float(left+e["level"]*pitch)
            e["y"]=float(top+LANE_PADDING+e["_component"]*COMPONENT_SPACING+(e["branch_index"]-min_branch)*max(corridor,2*BRANCH_MARGIN))
            while any(self._overlap(e,nodes[o]) for o in placed): e["y"]=max(nodes[o]["y"]+nodes[o]["height"]+VERTICAL_SPACING for o in placed if self._overlap(e,nodes[o]))
            placed.append(nid)
        bottom=max(nodes[n]["y"]+nodes[n]["height"] for n in ids)
        return max(float(LANE_MIN_HEIGHT),float(bottom-top+LANE_PADDING))

    @staticmethod
    def _branch_indexes(pa,ids):
        result={n:0 for n in ids}
        for branch in sorted((b for b in pa.get("branches",[]) if isinstance(b,Mapping)),key=lambda b:str(b.get("split_gateway",""))):
            starts=sorted(branch.get("branch_starts",[])); offsets=BPMNLayoutEngine._balanced_offsets(len(starts)); members=branch.get("branch_nodes",{})
            for start,offset in zip(starts,offsets):
                for nid in members.get(start,[]) if isinstance(members,Mapping) else []:
                    if nid in result: result[nid]+=offset
        return result
    @staticmethod
    def _balanced_offsets(count):
        if count<=1:return [0]*count
        half=count//2
        return list(range(-half,half+1)) if count%2 else list(range(-half,0))+list(range(1,half+1))
    @staticmethod
    def _component_indexes(components,ids):
        result={n:0 for n in ids}
        if isinstance(components,list):
            for i,component in enumerate(components):
                for n in component if isinstance(component,list) else []:
                    if n in result: result[n]=i
        return result

    @staticmethod
    def _center_joins(processes,nodes):
        for pa in processes.values():
            reverse=pa.get("reverse_adjacency",{}); degree=pa.get("in_degree",{})
            for nid in sorted(pa.get("join_gateways",[])):
                predecessors=[n for n in reverse.get(nid,[]) if n in nodes]
                if nid in nodes and degree.get(nid,0)>1 and predecessors:
                    centers=[nodes[n]["y"]+nodes[n]["height"]/2 for n in predecessors]
                    nodes[nid]["y"]=float(sum(centers)/len(centers)-nodes[nid]["height"]/2)

    @staticmethod
    def _back_channels(pa):
        edges=sorted((e for e in pa.get("back_edges",[]) if isinstance(e,Mapping) and e.get("flow_id")),key=lambda e:str(e["flow_id"]))
        return {str(e["flow_id"]):{"side":"bottom","channel_index":i,"margin":float(BACK_EDGE_MARGIN),
            "source":e.get("source"),"target":e.get("target"),"process_id":e.get("process_id")} for i,e in enumerate(edges)}
    @staticmethod
    def _message_connections(collaboration):
        result={}
        for msg in collaboration.get("message_flows",[]) if isinstance(collaboration.get("message_flows",[]),list) else []:
            if not isinstance(msg,Mapping) or not isinstance(msg.get("id"),str): continue
            result[msg["id"]]={"source":msg.get("source"),"target":msg.get("target"),"source_process":msg.get("source_process"),
                "target_process":msg.get("target_process"),"source_participant":msg.get("source_participant"),"target_participant":msg.get("target_participant")}
        return {k:result[k] for k in sorted(result)}

    def _get_node_size(self,node_type):
        if node_type=="startEvent": return float(START_EVENT_WIDTH),float(START_EVENT_HEIGHT)
        if node_type=="endEvent": return float(END_EVENT_WIDTH),float(END_EVENT_HEIGHT)
        if node_type in self._TASK_TYPES:return float(TASK_WIDTH),float(TASK_HEIGHT)
        if node_type in self._GATEWAY_TYPES:return float(GATEWAY_WIDTH),float(GATEWAY_HEIGHT)
        if node_type=="subProcess":return float(SUBPROCESS_WIDTH),float(SUBPROCESS_HEIGHT)
        if node_type in self._INTERMEDIATE_EVENT_TYPES:return float(INTERMEDIATE_EVENT_WIDTH),float(INTERMEDIATE_EVENT_HEIGHT)
        self._warn("UNKNOWN_NODE_TYPE",f"Generic dimensions used for type '{node_type}'."); return float(GENERIC_NODE_WIDTH),float(GENERIC_NODE_HEIGHT)
    @staticmethod
    def _index(raw): return {x["id"]:dict(x) for x in raw if isinstance(x,Mapping) and isinstance(x.get("id"),str)} if isinstance(raw,list) else {}
    @staticmethod
    def _overlap(a,b): return _rectangles_overlap(a,b)
    @staticmethod
    def _contains(a,b): return b["x"]>=a["x"] and b["y"]>=a["y"] and b["x"]+b["width"]<=a["x"]+a["width"] and b["y"]+b["height"]<=a["y"]+a["height"]
    def _validate(self,nodes,participants,lanes):
        ids=sorted(nodes)
        for i,n in enumerate(ids):
            for other in ids[i+1:]:
                if self._overlap(nodes[n],nodes[other]): self._warn("NODE_OVERLAP",f"Nodes '{n}' and '{other}' overlap.",node_id=n)
            lid=nodes[n]["lane_id"]; pid=nodes[n]["participant_id"]
            if lid in lanes and not self._contains(lanes[lid],nodes[n]): self._warn("NODE_OUTSIDE_LANE",f"Node '{n}' is outside its lane.",node_id=n,container_id=lid)
            if pid in participants and not self._contains(participants[pid],nodes[n]): self._warn("NODE_OUTSIDE_PARTICIPANT",f"Node '{n}' is outside its participant.",node_id=n,container_id=pid)
        lids=sorted(lanes)
        for i,lid in enumerate(lids):
            pid=lanes[lid]["participantRef"]
            if pid in participants and not self._contains(participants[pid],lanes[lid]): self._warn("LANE_OUTSIDE_PARTICIPANT",f"Lane '{lid}' is outside its participant.",container_id=lid)
            for other in lids[i+1:]:
                if lanes[lid]["participantRef"]==lanes[other]["participantRef"] and self._overlap(lanes[lid],lanes[other]): self._warn("LANE_OVERLAP",f"Lanes '{lid}' and '{other}' overlap.",container_id=lid)
        pids=sorted(participants)
        for i,pid in enumerate(pids):
            for other in pids[i+1:]:
                if self._overlap(participants[pid],participants[other]): self._warn("PARTICIPANT_OVERLAP",f"Participants '{pid}' and '{other}' overlap.",container_id=pid)
    @staticmethod
    def _bounds(rects,top):
        if not rects:return {"x":float(CANVAS_MARGIN),"y":float(top),"width":0.0,"height":0.0}
        left=min(r["x"] for r in rects); upper=min(r["y"] for r in rects); right=max(r["x"]+r["width"] for r in rects); bottom=max(r["y"]+r["height"] for r in rects)
        return {"x":float(left),"y":float(upper),"width":float(right-left),"height":float(bottom-upper)}
    @staticmethod
    def _canvas_size(nodes,participants,processes):
        rects=list(nodes.values())+list(participants.values())+[p["bounds"] for p in processes.values()]
        right=max([0.0]+[r["x"]+r["width"] for r in rects])
        bottom=max([0.0]+[r["y"]+r["height"] for r in rects])
        return float(right+CANVAS_MARGIN),float(bottom+CANVAS_MARGIN)
    def _warn(self,code,message,node_id=None,container_id=None,process_id=None):
        self._warnings.append({"code":code,"message":message,"node_id":node_id,"container_id":container_id,"process_id":process_id,"severity":"warning"})

def normalize_flows(flows):
    """Retained converter adapter; canonical flows already use source/target."""
    return [dict(flow) for flow in flows if isinstance(flow,Mapping)]

def calculate_positions(nodes,flows):
    """Deprecated adapter; callers must provide a canonical workflow."""
    return {}
