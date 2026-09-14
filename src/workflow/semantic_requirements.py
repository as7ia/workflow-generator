"""Canonical, extensible semantic requirements extracted from user text."""
from __future__ import annotations
import copy
import re
from dataclasses import asdict, dataclass, field
from collections.abc import Mapping
from typing import Any

_KEYS=("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")

@dataclass
class SemanticRequirements:
    participants:list[dict[str,Any]]=field(default_factory=list)
    lanes:list[dict[str,Any]]=field(default_factory=list)
    timers:list[dict[str,Any]]=field(default_factory=list)
    parallelisms:list[dict[str,Any]]=field(default_factory=list)
    decisions:list[dict[str,Any]]=field(default_factory=list)
    interactions:list[dict[str,Any]]=field(default_factory=list)
    activities:list[dict[str,Any]]=field(default_factory=list)
    ambiguities:list[dict[str,Any]]=field(default_factory=list)
    def to_dict(self):return asdict(self)

def normalize_semantic_requirements(raw:Any)->tuple[dict[str,Any],list[dict[str,Any]]]:
    """Return canonical requirements and non-fatal extraction issues."""
    issues=[];source=dict(raw) if isinstance(raw,Mapping) else {}
    if not isinstance(raw,Mapping):issues.append(_issue("INVALID_SEMANTIC_REQUIREMENTS","Requirements extraction did not return an object.","error"))
    result={}
    for key in _KEYS:
        values=source.get(key,[])
        if not isinstance(values,list):
            issues.append(_issue("INVALID_REQUIREMENT_COLLECTION",f"'{key}' must be a list.","error"));values=[]
        normalized=[]
        for value in values:
            if isinstance(value,str):item={"name":value.strip()}
            elif isinstance(value,Mapping):item=copy.deepcopy(dict(value))
            else:continue
            if key=="timers" and isinstance(item.get("duration"),str):item["duration"]=_duration(item["duration"])
            normalized.append(item)
        result[key]=normalized
    return SemanticRequirements(**result).to_dict(),issues

def _duration(value):
    match=re.fullmatch(r"\s*(\d+)\s*(day|days|hour|hours|minute|minutes)\s*",value,re.I)
    if not match:return value
    amount=int(match.group(1));unit=match.group(2).lower()
    return f"P{amount}D" if unit.startswith("day") else f"PT{amount}{'H' if unit.startswith('hour') else 'M'}"

def _issue(code,message,severity):return {"code":code,"message":message,"severity":severity}
