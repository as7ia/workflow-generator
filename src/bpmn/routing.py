"""Deterministic orthogonal routing for BPMN sequence and message flows."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from .constants import BACK_EDGE_MARGIN,DI_NS
from .xml_helpers import tag

def expand_rectangle(position,margin=15):
    return {"left":float(position["x"])-margin,"right":float(position["x"])+float(position["width"])+margin,
        "top":float(position["y"])-margin,"bottom":float(position["y"])+float(position["height"])+margin}

def segment_intersects_rectangle(start,end,rectangle):
    x1,y1=start;x2,y2=end
    if abs(x1-x2)<.001:return rectangle["left"]<x1<rectangle["right"] and max(y1,y2)>rectangle["top"] and min(y1,y2)<rectangle["bottom"]
    if abs(y1-y2)<.001:return rectangle["top"]<y1<rectangle["bottom"] and max(x1,x2)>rectangle["left"] and min(x1,x2)<rectangle["right"]
    return True

def edge_intersects_shape(points,shape,margin=0):
    rectangle=expand_rectangle(shape,margin)
    return any(segment_intersects_rectangle(a,b,rectangle) for a,b in zip(points,points[1:]))

def _contains(container,shape):
    return (shape["x"]>=container["x"] and shape["y"]>=container["y"]
        and shape["x"]+shape["width"]<=container["x"]+container["width"]
        and shape["y"]+shape["height"]<=container["y"]+container["height"])

def simplify_path(points):
    rounded=[]
    for point in points:
        value=(round(float(point[0]),2),round(float(point[1]),2))
        if not rounded or value!=rounded[-1]:rounded.append(value)
    if len(rounded)<=2:return rounded
    result=[rounded[0]]
    for index in range(1,len(rounded)-1):
        previous,current,following=result[-1],rounded[index],rounded[index+1]
        if not(previous[0]==current[0]==following[0] or previous[1]==current[1]==following[1]):result.append(current)
    result.append(rounded[-1]);return result

def get_connection_points(position):
    x,y=float(position["x"]),float(position["y"]);width,height=float(position["width"]),float(position["height"])
    return {"left":(x,y+height/2),"right":(x+width,y+height/2),"top":(x+width/2,y),"bottom":(x+width/2,y+height)}

def offset_point(point,side,distance):
    dx,dy={"left":(-distance,0),"right":(distance,0),"top":(0,-distance),"bottom":(0,distance)}[side]
    return point[0]+dx,point[1]+dy

def _shift_anchor(point,side,offset):
    return (point[0],point[1]+offset) if side in {"left","right"} else (point[0]+offset,point[1])

def _candidate(source,target,source_side,target_side,clearance,axis=None,source_offset=0,target_offset=0):
    source_anchor=_shift_anchor(get_connection_points(source)[source_side],source_side,source_offset)
    target_anchor=_shift_anchor(get_connection_points(target)[target_side],target_side,target_offset)
    source_exit=offset_point(source_anchor,source_side,clearance);target_entry=offset_point(target_anchor,target_side,clearance)
    horizontal=source_side in {"left","right"}
    if axis is None:axis=((source_exit[0]+target_entry[0])/2 if horizontal else (source_exit[1]+target_entry[1])/2)
    middle=[(axis,source_exit[1]),(axis,target_entry[1])] if horizontal else [(source_exit[0],axis),(target_entry[0],axis)]
    return simplify_path([source_anchor,source_exit,*middle,target_entry,target_anchor])

def calculate_path_score(points,obstacles,overlap_penalty=0):
    length=sum(abs(b[0]-a[0])+abs(b[1]-a[1]) for a,b in zip(points,points[1:]))
    collisions=sum(edge_intersects_shape(points,obstacle) for obstacle in obstacles)
    return collisions*1_000_000+overlap_penalty+length+max(0,len(points)-2)*80

def calculate_edge_waypoints(source_id,target_id,node_positions,clearance=30,*,corridor=None,kind="sequence",used_segments=None,source_offset=0,target_offset=0):
    source=node_positions[source_id];target=node_positions[target_id]
    obstacles=[position for node_id,position in sorted(node_positions.items())
        if node_id not in {source_id,target_id} and not _contains(position,source) and not _contains(position,target)]
    if corridor:
        side,value=corridor;candidates=[_candidate(source,target,side,side,clearance,value,source_offset,target_offset)]
    else:
        candidates=[_candidate(source,target,"right","left",clearance,source_offset=source_offset,target_offset=target_offset),
            _candidate(source,target,"bottom","top",clearance,source_offset=source_offset,target_offset=target_offset),
            _candidate(source,target,"top","bottom",clearance,source_offset=source_offset,target_offset=target_offset),
            _candidate(source,target,"left","right",clearance,source_offset=source_offset,target_offset=target_offset)]
        if obstacles:
            rectangles=[expand_rectangle(o) for o in obstacles]
            candidates.extend([_candidate(source,target,"top","top",clearance,min(o["top"] for o in rectangles)-clearance,source_offset,target_offset),
                _candidate(source,target,"bottom","bottom",clearance,max(o["bottom"] for o in rectangles)+clearance,source_offset,target_offset),
                _candidate(source,target,"right","right",clearance,max(o["right"] for o in rectangles)+clearance,source_offset,target_offset),
                _candidate(source,target,"left","left",clearance,min(o["left"] for o in rectangles)-clearance,source_offset,target_offset)])
    used_segments=used_segments or set()
    def score(path):
        segments={tuple(sorted((a,b))) for a,b in zip(path,path[1:])}
        return calculate_path_score(path,obstacles,5_000*len(segments&used_segments)),tuple(path)
    return min(candidates,key=score)

def calculate_routes(flows,messages,geometry,layout,clearance=30):
    """Route all DI edges in stable order and allocate distinct external corridors."""
    routes={};used=set();metadata=layout.get("metadata",{}) if isinstance(layout,Mapping) else {}
    back=metadata.get("back_edge_channels",{}) if isinstance(metadata,Mapping) else {}
    processes=layout.get("processes",{}) if isinstance(layout,Mapping) else {}
    bounds={pid:value.get("bounds",{}) for pid,value in processes.items()} if isinstance(processes,Mapping) else {}
    global_right=max((r["x"]+r["width"] for r in geometry.values()),default=0)
    ordered_flows=sorted(flows,key=lambda value:str(value.get("id","")))
    outgoing={};incoming={}
    for flow in ordered_flows:
        outgoing.setdefault(str(flow["source"]),[]).append(str(flow["id"]));incoming.setdefault(str(flow["target"]),[]).append(str(flow["id"]))
    def port_offset(ids,fid):return (ids.index(fid)-(len(ids)-1)/2)*8
    for flow in ordered_flows:
        fid=str(flow["id"]);channel=back.get(fid) if isinstance(back,Mapping) else None;corridor=None
        if isinstance(channel,Mapping):
            rect=bounds.get(channel.get("process_id"),{});index=int(channel.get("channel_index",0))
            corridor=("bottom",float(rect.get("y",0))+float(rect.get("height",0))+BACK_EDGE_MARGIN*(index+1))
        path=calculate_edge_waypoints(str(flow["source"]),str(flow["target"]),geometry,clearance,corridor=corridor,used_segments=used,
            source_offset=port_offset(outgoing[str(flow["source"])],fid),target_offset=port_offset(incoming[str(flow["target"])],fid))
        routes[fid]=path;used.update(tuple(sorted((a,b))) for a,b in zip(path,path[1:]))
    for index,message in enumerate(sorted(messages,key=lambda value:str(value.get("id","")))):
        fid=str(message["id"]);corridor=("right",global_right+BACK_EDGE_MARGIN*(index+1))
        path=calculate_edge_waypoints(str(message["source"]),str(message["target"]),geometry,clearance,corridor=corridor,kind="message",used_segments=used)
        routes[fid]=path;used.update(tuple(sorted((a,b))) for a,b in zip(path,path[1:]))
    return routes

def routing_metrics(routes,geometry,endpoints):
    collisions=0
    for fid,path in routes.items():
        source,target=endpoints.get(fid,(None,None))
        collisions+=sum(edge_intersects_shape(path,shape) for node_id,shape in geometry.items() if node_id not in {source,target})
    return {"edge_shape_collisions":collisions,"edge_count":len(routes)}

def create_edge_waypoints(edge,waypoints):
    for x,y in waypoints:ET.SubElement(edge,tag(DI_NS,"waypoint"),{"x":str(x),"y":str(y)})
