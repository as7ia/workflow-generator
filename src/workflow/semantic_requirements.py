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
    ambiguities=list(collections["ambiguities"])
    participants=_dedupe([{"name":x.get("name","")} for x in collections["participants"] if _norm(x.get("name"))],lambda x:_norm(x.get("name")))
    participant_names=[x.get("name","") for x in participants]
    lanes=[]
    for item in collections["lanes"]:
        if not _norm(item.get("name")) or not _norm(item.get("participant")):
            ambiguities.append({"message":"Incomplete lane requirement.","requirement":item});continue
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
        if not _norm(normalized.get("source")) or not _norm(normalized.get("target")):
            ambiguities.append({"message":"Incomplete interaction requirement.","requirement":item});continue
        interactions.append(normalized)
    interactions=_dedupe(interactions,lambda x:tuple(_norm(x.get(k)) for k in ("source","target","sourceLane","targetLane")))
    timers=[]
    for item in collections["timers"]:
        value=item.get("value") or item.get("duration") or item.get("name")
        kind=str(item.get("kind","duration")).lower();canonical_value=_duration(value) if kind=="duration" else str(value or "").strip()
        if kind not in {"duration","date","cycle"} or not _valid_timer_value(kind,canonical_value):
            ambiguities.append({"message":"Malformed timer requirement.","requirement":item});continue
        timers.append({k:v for k,v in {"kind":kind,"value":canonical_value,"activity":item.get("activity")}.items() if v})
    parallelisms=[];grouped={}
    for item in collections["parallelisms"]:
        branches=item.get("branches") or ([{"activity":item["activity"]}] if item.get("activity") else [])
        key=_norm(item.get("name") or item.get("group") or "default")
        grouped.setdefault(key,[]).extend(branches)
    for branches in grouped.values():
        canonical_branches=_dedupe(branches,lambda x:_norm(x.get("activity")))
        if len(canonical_branches)>=2:parallelisms.append({"branches":canonical_branches})
        else:ambiguities.append({"message":"Parallelism requires at least two branches.","requirement":{"branches":branches}})
    decisions=[]
    for item in collections["decisions"]:
        outcomes=_dedupe([str(value).strip() for value in item.get("outcomes",[]) if str(value).strip()],_norm) if isinstance(item.get("outcomes"),list) else []
        if not _norm(item.get("name")) or len(outcomes)<2:
            ambiguities.append({"message":"Decision requires a name and at least two outcomes.","requirement":item});continue
        decisions.append({"name":item["name"],"outcomes":outcomes})
    timer_activities={_norm(x.get("activity")) for x in timers if x.get("activity")}
    activities=_dedupe([{"name":x.get("name","")} for x in collections["activities"] if _norm(x.get("name")) and _norm(x.get("name")) not in timer_activities],lambda x:_norm(x.get("name")))
    result={"participants":participants,"lanes":lanes,"timers":_dedupe(timers,lambda x:(x.get("kind"),x.get("value"),_norm(x.get("activity")))),
            "parallelisms":parallelisms,"decisions":_dedupe(decisions,lambda x:(_norm(x["name"]),tuple(_norm(v) for v in x["outcomes"]))),
            "interactions":interactions,"activities":activities,"ambiguities":_dedupe(ambiguities,lambda x:_norm(x.get("message") or x.get("name") or x.get("requirement")))}
    return SemanticRequirements(**result).to_dict(),issues

def validate_requirements_against_description(requirements,description):
    """Remove only requirements with no defensible support in the description."""
    result=copy.deepcopy(dict(requirements)) if isinstance(requirements,Mapping) else SemanticRequirements().to_dict()
    text=str(description or "");description_tokens=_content_tokens(text);issues=[]
    ambiguities=list(result.get("ambiguities",[]))
    explicit_durations=set(_description_durations(text))

    def reject(kind,item,reason):
        ambiguities.append({"message":reason,"requirement":copy.deepcopy(item)})
        issues.append(_issue("UNSUPPORTED_SEMANTIC_REQUIREMENT",reason,"warning"))

    declared_lane_names={_norm(item.get("name")) for item in result.get("lanes",[]) if isinstance(item,Mapping)}
    supported_participants=[]
    for item in result.get("participants",[]):
        if _norm(item.get("name")) in declared_lane_names:
            reject("participants",item,"Internal lane role must not be duplicated as a participant.")
        elif _lexically_supported(item.get("name"),description_tokens):supported_participants.append(item)
        else:reject("participants",item,"Participant absent from the user description.")
    result["participants"]=supported_participants
    participant_names={_norm(item.get("name")) for item in supported_participants}

    supported_lanes=[]
    for item in result.get("lanes",[]):
        if (_lexically_supported(item.get("name"),description_tokens) and
                _norm(item.get("participant")) in participant_names):supported_lanes.append(item)
        else:reject("lanes",item,"Lane or owning participant absent from the user description.")
    result["lanes"]=supported_lanes

    supported_timers=[]
    for item in result.get("timers",[]):
        value=str(item.get("value") or "")
        supported=(item.get("kind")=="duration" and value in explicit_durations) or (item.get("kind")!="duration" and value and value.lower() in text.lower())
        if supported:supported_timers.append(item)
        else:reject("timers",item,"Timer value absent from the user description.")
    result["timers"]=supported_timers

    supported_activities=[]
    for item in result.get("activities",[]):
        if _lexically_supported(item.get("name"),description_tokens):supported_activities.append(item)
        else:reject("activities",item,"Activity absent from the user description.")
    result["activities"]=supported_activities

    supported_interactions=[]
    for item in result.get("interactions",[]):
        endpoints=(item.get("source"),item.get("target"))
        if all(_norm(value) in participant_names or _lexically_supported(value,description_tokens) for value in endpoints):supported_interactions.append(item)
        else:reject("interactions",item,"Interaction endpoint absent from the user description.")
    result["interactions"]=supported_interactions

    supported_parallelisms=[]
    for item in result.get("parallelisms",[]):
        branches=item.get("branches",[]) if isinstance(item,Mapping) else []
        if len(branches)>=2 and all(_lexically_supported(branch.get("activity"),description_tokens) for branch in branches if isinstance(branch,Mapping)):supported_parallelisms.append(item)
        else:reject("parallelisms",item,"Parallel branches are not supported by the user description.")
    result["parallelisms"]=supported_parallelisms

    supported_decisions=[]
    for item in result.get("decisions",[]):
        terms=[item.get("name"),*(item.get("outcomes",[]) if isinstance(item.get("outcomes"),list) else [])]
        if any(_lexically_supported(term,description_tokens) for term in terms):supported_decisions.append(item)
        else:reject("decisions",item,"Decision absent from the user description.")
    result["decisions"]=supported_decisions
    result["ambiguities"]=_dedupe(ambiguities,lambda x:_norm(x.get("message") or x.get("requirement")))
    return SemanticRequirements(**{key:list(result.get(key,[])) for key in KEYS}).to_dict(),issues
def _items(value,key,issues):
    if not isinstance(value,list):issues.append(_issue("INVALID_REQUIREMENT_COLLECTION",f"'{key}' must be a list.","warning"));return []
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
def _norm(value):
    tokens=re.findall(r"[a-z0-9]+",unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower())
    return " ".join(token[:-1] if len(token)>4 and token.endswith(("s","r")) else token for token in tokens)
def _duration(value):
    match=re.fullmatch(r"\s*(\d+|one|two|three|four|five|six|seven|eight|nine|ten|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s*(jour|jours|day|days|heure|heures|hour|hours|minute|minutes)\s*",str(value or ""),re.I)
    if not match:return str(value or "")
    numbers={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
        "un":1,"une":1,"deux":2,"trois":3,"quatre":4,"cinq":5,"sept":7,"huit":8,"neuf":9,"dix":10}
    raw_amount=match.group(1).lower();amount=int(raw_amount) if raw_amount.isdigit() else numbers[raw_amount];unit=match.group(2).lower()
    return f"P{amount}D" if unit.startswith(("j","d")) else f"PT{amount}{'H' if unit.startswith(('h','heure')) else 'M'}"
def _description_durations(value):
    text=str(value or "");result=set(re.findall(r"\bP(?=\w*\d)(?:\d+(?:\.\d+)?[YMWD])*(?:T(?:\d+(?:\.\d+)?[HMS])+)?\b",text,re.I))
    amount=r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)"
    for match in re.finditer(rf"\b{amount}\s*(?:jour|jours|day|days|heure|heures|hour|hours|minute|minutes)\b",text,re.I):
        result.add(_duration(match.group(0)))
    return {item.upper() for item in result if item}
def _content_tokens(value):
    generic={"actor","acteur","organization","organisation","participant","role","service","internal","interne","task","activity","activite","the","and","et","une","des","dans","with","avec","performs","effectue"}
    return {token for token in _norm(value).split() if len(token)>=2 and token not in generic}
def _lexically_supported(value,description_tokens):
    tokens=_content_tokens(value)
    return bool(tokens and tokens&description_tokens)
def _valid_timer_value(kind,value):
    text=str(value or "").strip()
    if kind=="duration":return bool(re.fullmatch(r"P(?=.+)(?:\d+(?:\.\d+)?[YMWD])*(?:T(?:\d+(?:\.\d+)?[HMS])+)?",text))
    if kind=="date":return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?",text))
    return bool(re.fullmatch(r"R\d*/P.+",text))
def _issue(code,message,severity):return {"code":code,"message":message,"severity":severity}
