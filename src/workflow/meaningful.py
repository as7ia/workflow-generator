"""Generic content guard for generated and repaired workflows."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

def is_meaningful_workflow(workflow:Any,previous:Mapping[str,Any]|None=None)->bool:
    """Return whether a workflow contains a real process and at least one node."""
    if not isinstance(workflow,Mapping):return False
    processes=workflow.get("processes");nodes=workflow.get("nodes")
    if not isinstance(processes,list) and isinstance(workflow.get("process"),Mapping):
        processes=[workflow["process"]]
    if not isinstance(processes,list) or not processes:return False
    if not isinstance(nodes,list) or not nodes:return False
    if not any(isinstance(node,Mapping) and str(node.get("type") or "").strip() for node in nodes):return False
    if isinstance(previous,Mapping):
        previous_nodes=previous.get("nodes")
        if isinstance(previous_nodes,list) and previous_nodes and not nodes:return False
    return True
