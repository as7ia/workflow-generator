import copy

from src.bpmn.routing import (calculate_edge_waypoints,calculate_routes,
    edge_intersects_shape,routing_metrics)


def rect(x,y,width=100,height=60):
    return {"x":float(x),"y":float(y),"width":float(width),"height":float(height)}


def orthogonal(path):
    return all(a[0]==b[0] or a[1]==b[1] for a,b in zip(path,path[1:]))


def layout(bounds=None,back=None):
    return {"processes":{"p":{"bounds":bounds or rect(20,20,700,300)}},
        "metadata":{"back_edge_channels":back or {}}}


def test_forward_edge_is_orthogonal():
    path=calculate_edge_waypoints("a","b",{"a":rect(0,0),"b":rect(300,0)})
    assert orthogonal(path)


def test_inter_lane_edge_is_orthogonal():
    path=calculate_edge_waypoints("a","b",{"a":rect(0,0),"b":rect(300,180)})
    assert orthogonal(path)


def test_route_avoids_foreign_shape():
    geometry={"a":rect(0,0),"obstacle":rect(180,0),"b":rect(400,0)}
    path=calculate_edge_waypoints("a","b",geometry)
    assert not edge_intersects_shape(path,geometry["obstacle"])


def test_split_routes_are_not_identical():
    geometry={"g":rect(0,100,50,50),"a":rect(300,0),"b":rect(300,200)}
    flows=[{"id":"f1","source":"g","target":"a"},{"id":"f2","source":"g","target":"b"}]
    routes=calculate_routes(flows,[],geometry,layout())
    assert routes["f1"]!=routes["f2"] and all(orthogonal(path) for path in routes.values())


def test_join_routes_are_not_identical():
    geometry={"a":rect(0,0),"b":rect(0,200),"g":rect(350,100,50,50)}
    flows=[{"id":"f1","source":"a","target":"g"},{"id":"f2","source":"b","target":"g"}]
    routes=calculate_routes(flows,[],geometry,layout())
    assert routes["f1"]!=routes["f2"]


def test_back_edge_uses_reserved_external_corridor():
    geometry={"a":rect(50,50),"b":rect(400,50)};bounds=rect(20,20,600,180)
    back={"loop":{"process_id":"p","channel_index":0}}
    routes=calculate_routes([{"id":"loop","source":"b","target":"a"}],[],geometry,layout(bounds,back))
    assert max(y for _,y in routes["loop"])>bounds["y"]+bounds["height"]


def test_two_back_edges_use_distinct_corridors():
    geometry={"a":rect(50,50),"b":rect(400,50),"c":rect(50,130),"d":rect(400,130)}
    back={"one":{"process_id":"p","channel_index":0},"two":{"process_id":"p","channel_index":1}}
    flows=[{"id":"one","source":"b","target":"a"},{"id":"two","source":"d","target":"c"}]
    routes=calculate_routes(flows,[],geometry,layout(rect(20,20,600,220),back))
    assert max(y for _,y in routes["one"])!=max(y for _,y in routes["two"])


def test_message_flows_use_distinct_external_corridors():
    geometry={"a":rect(50,50),"b":rect(50,350),"c":rect(200,50),"d":rect(200,350)}
    messages=[{"id":"m1","source":"a","target":"b"},{"id":"m2","source":"c","target":"d"}]
    routes=calculate_routes([],messages,geometry,layout())
    assert max(x for x,_ in routes["m1"])!=max(x for x,_ in routes["m2"])
    assert all(orthogonal(path) for path in routes.values())


def test_routing_metrics_report_no_foreign_shape_collision():
    geometry={"a":rect(0,0),"middle":rect(180,0),"b":rect(400,0)}
    routes=calculate_routes([{"id":"f","source":"a","target":"b"}],[],geometry,layout())
    assert routing_metrics(routes,geometry,{"f":("a","b")})["edge_shape_collisions"]==0


def test_routing_is_deterministic_and_does_not_mutate_inputs():
    geometry={"a":rect(0,0),"middle":rect(180,0),"b":rect(400,0)}
    flows=[{"id":"f","source":"a","target":"b"}];before=copy.deepcopy((geometry,flows))
    first=calculate_routes(flows,[],geometry,layout());second=calculate_routes(list(reversed(flows)),[],geometry,layout())
    assert first==second and (geometry,flows)==before
