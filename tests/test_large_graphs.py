from pathlib import Path

from src.workflow import SemanticValidator, WorkflowGraphAnalyzer


def large_workflow(count, process_id="p", cycle=False):
    nodes=[]
    for index in range(count):
        kind="startEvent" if index==0 else "endEvent" if index==count-1 and not cycle else "serviceTask"
        nodes.append({"id":f"n{index}","type":kind,"name":f"Node {index}","processRef":process_id,
                      "participantRef":None,"laneRef":None,"actor":None,"properties":{}})
    flows=[{"id":f"f{index}","source":f"n{index}","target":f"n{index+1}","processRef":process_id,
            "condition":None,"isDefault":False,"properties":{}} for index in range(count-1)]
    if cycle:
        nodes[0]["type"]="serviceTask"
        flows.append({"id":"cycle","source":f"n{count-1}","target":"n0","processRef":process_id,
                      "condition":None,"isDefault":False,"properties":{}})
    return {"schemaVersion":"2.0","processes":[{"id":process_id,"name":process_id,"description":"","executable":False,"properties":{}}],
            "participants":[],"lanes":[],"nodes":nodes,"flows":flows,"messageFlows":[],"ambiguities":[]}


def test_analyzer_handles_chain_of_3000_nodes_without_recursion():
    analysis=WorkflowGraphAnalyzer().analyze(large_workflow(3000))["processes"]["p"]
    assert len(analysis["topological_order"])==3000
    assert analysis["topological_order"][0]=="n0" and analysis["topological_order"][-1]=="n2999"


def test_analyzer_and_validator_handle_large_cycle_without_recursion():
    workflow=large_workflow(1500,cycle=True)
    analysis=WorkflowGraphAnalyzer().analyze(workflow)["processes"]["p"]
    assert len(analysis["strongly_connected_components"])==1
    assert len(analysis["strongly_connected_components"][0])==1500
    assert any(issue["code"]=="CYCLE_DETECTED" for issue in SemanticValidator().validate(workflow))


def test_iterative_scc_handles_multiple_disconnected_components():
    ids=["a","b","c","d","e","f"]
    adjacency={"a":["b"],"b":["a"],"c":["d"],"d":["c"],"e":["f"],"f":[]}
    assert WorkflowGraphAnalyzer._scc(ids,adjacency)==[["a","b"],["c","d"],["e"],["f"]]


def test_large_multi_process_collaboration_and_many_messages():
    workflow={"schemaVersion":"2.0","processes":[],"participants":[],"lanes":[],"nodes":[],"flows":[],"messageFlows":[],"ambiguities":[]}
    for process_index in range(4):
        process=f"p{process_index}";participant=f"participant_{process_index}"
        workflow["processes"].append({"id":process,"name":process,"description":"","executable":False,"properties":{}})
        workflow["participants"].append({"id":participant,"name":participant,"processRef":process,"properties":{}})
        for index in range(500):
            kind="startEvent" if index==0 else "endEvent" if index==499 else "serviceTask"
            workflow["nodes"].append({"id":f"{process}_n{index}","type":kind,"name":str(index),"processRef":process,
                                      "participantRef":participant,"laneRef":None,"actor":None,"properties":{}})
        workflow["flows"].extend({"id":f"{process}_f{index}","source":f"{process}_n{index}","target":f"{process}_n{index+1}",
                                  "processRef":process,"condition":None,"isDefault":False,"properties":{}} for index in range(499))
    workflow["messageFlows"]=[{"id":f"m{index}","source":f"p0_n{index%500}","target":f"p1_n{index%500}","properties":{}} for index in range(1000)]
    analysis=WorkflowGraphAnalyzer().analyze(workflow)
    assert all(len(value["node_ids"])==500 for value in analysis["processes"].values())
    assert len(analysis["collaboration"]["message_flows"])==1000


def test_unreachable_legacy_markers_are_absent_from_active_modules():
    root=Path(__file__).parents[1]
    pipeline=(root/"src/generation/pipeline.py").read_text(encoding="utf-8")
    repair=(root/"src/workflow/repair.py").read_text(encoding="utf-8")
    assert "Legacy implementation retained below temporarily unreachable" not in pipeline
    assert "Previous prompt implementation retained below temporarily unreachable" not in repair
    assert "def _explicit_node_participants" not in pipeline
    assert "def _to_canonical_schema_flows" not in pipeline
