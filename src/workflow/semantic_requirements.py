"""Canonical semantic requirements extracted from user text."""
from __future__ import annotations
import copy,re,unicodedata
from dataclasses import asdict,dataclass,field
from collections.abc import Mapping
from typing import Any
KEYS=("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")
@dataclass
class SemanticRequirements:
    participants:list[dict[str,Any]]=field(default_factory=list);lanes:list[dict[str,Any]]=field(default_factory=list)
    timers:list[dict[str,Any]]=field(default_factory=list);parallelisms:list[dict[str,Any]]=field(default_factory=list)
    decisions:list[dict[str,Any]]=field(default_factory=list);interactions:list[dict[str,Any]]=field(default_factory=list)
    activities:list[dict[str,Any]]=field(default_factory=list);ambiguities:list[dict[str,Any]]=field(default_factory=list)
    def to_dict(self):return asdict(self)
def normalize_semantic_requirements(raw):
    issues=[];source=dict(raw) if isinstance(raw,Mapping) else {}
    if not isinstance(raw,Mapping):issues.append(_issue("INVALID_SEMANTIC_REQUIREMENTS","Requirements must be an object.","error"))
    collections={k:_items(source.get(k,[]),k,issues) for k in KEYS}
    participants=_dedupe(collections["participants"],lambda x:_norm(x.get("name")))
    participant_names=[x.get("name","") for x in participants]
    lanes=[]
    for item in collections["lanes"]:
        if not item.get("participant"):continue
        lanes.append({"name":item.get("name",""),"participant":item["participant"]})
    lanes=_dedupe(lanes,lambda x:(_norm(x["participant"]),_norm(x["name"])))
    lane_lookup={_norm(x["name"]):x for x in lanes}
    interactions=[]
    for item in collections["interactions"]:
        normalized={}
        for side in ("source","target"):
            actor,lane=_split_actor(item.get(side),participant_names,lane_lookup)
            normalized[side]=actor
            if lane:normalized[side+"Lane"]=lane
            elif item.get(side+"Lane"):normalized[side+"Lane"]=item[side+"Lane"]
        interactions.append(normalized)
    interactions=_dedupe(interactions,lambda x:tuple(_norm(x.get(k)) for k in ("source","target","sourceLane","targetLane")))
    timers=[]
    for item in collections["timers"]:
        value=item.get("value") or item.get("duration") or item.get("name")
        timers.append({k:v for k,v in {"kind":item.get("kind","duration"),"value":_duration(value),"activity":item.get("activity")}.items() if v})
    parallelisms=[];grouped={}
    for item in collections["parallelisms"]:
        branches=item.get("branches") or ([{"activity":item["activity"]}] if item.get("activity") else [])
        key=_norm(item.get("name") or item.get("group") or "default")
        grouped.setdefault(key,[]).extend(branches)
    for branches in grouped.values():parallelisms.append({"branches":_dedupe(branches,lambda x:_norm(x.get("activity")))})
    decisions=[{"name":x.get("name",""),"outcomes":x.get("outcomes",[])} for x in collections["decisions"] if isinstance(x.get("outcomes"),list) and len(x["outcomes"])>=2]
    timer_activities={_norm(x.get("activity")) for x in timers if x.get("activity")}
    activities=_dedupe([x for x in collections["activities"] if _norm(x.get("name")) not in timer_activities],lambda x:_norm(x.get("name")))
    result={"participants":participants,"lanes":lanes,"timers":_dedupe(timers,lambda x:(x.get("kind"),x.get("value"),_norm(x.get("activity")))),
            "parallelisms":parallelisms,"decisions":_dedupe(decisions,lambda x:(_norm(x["name"]),tuple(_norm(v) for v in x["outcomes"]))),
            "interactions":interactions,"activities":activities,"ambiguities":_dedupe(collections["ambiguities"],lambda x:_norm(x.get("message") or x.get("name")))}
    return SemanticRequirements(**result).to_dict(),issues
def _items(value,key,issues):
    if not isinstance(value,list):issues.append(_issue("INVALID_REQUIREMENT_COLLECTION",f"'{key}' must be a list.","error"));return []
    return [{"name":x.strip()} if isinstance(x,str) else copy.deepcopy(dict(x)) for x in value if isinstance(x,(str,Mapping))]
def _split_actor(value,participants,lanes):
    text=str(value or "").strip();match=re.match(r"^(.*?)\s*\((.*?)\)\s*$",text)
    if match:return match.group(1).strip(),match.group(2).strip().removeprefix("Service ").strip()
    for participant in participants:
        if _norm(text)==_norm(participant):return participant,None
    return text,None
def _dedupe(items,key):
    result=[];seen=set()
    for item in items:
        marker=key(item)
        if marker and marker not in seen:seen.add(marker);result.append(item)
    return result
def _norm(value):return " ".join(re.findall(r"[a-z0-9]+",unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower()))
def _duration(value):
    match=re.fullmatch(r"\s*(\d+)\s*(jour|jours|day|days|heure|heures|hour|hours|minute|minutes)\s*",str(value or ""),re.I)
    if not match:return str(value or "")
    amount=int(match.group(1));unit=match.group(2).lower()
    return f"P{amount}D" if unit.startswith(("j","d")) else f"PT{amount}{'H' if unit.startswith(('h','heure')) else 'M'}"
def _issue(code,message,severity):return {"code":code,"message":message,"severity":severity}
