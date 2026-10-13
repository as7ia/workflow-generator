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
def normalize_semantic_requirements(raw,description=None):
    issues=[];source=dict(raw) if isinstance(raw,Mapping) else {}
    if not isinstance(raw,Mapping):issues.append(_issue("INVALID_SEMANTIC_REQUIREMENTS","Requirements must be an object.","error"))
    collections={k:_items(source.get(k,[]),k,issues) for k in KEYS}
    if isinstance(description,str):
        collections["timers"]=[item for item in collections["timers"] if _has_temporal_evidence(description)]
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
        if not isinstance(description,str) or _has_interaction_evidence(description,normalized["source"],normalized["target"]):
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
    activities=_dedupe([{"name":x.get("name","")} for x in collections["activities"]
                        if _norm(x.get("name")) and _norm(x.get("name")) not in timer_activities
                        and (not isinstance(description,str) or not _is_passive_end_state_activity(description,x.get("name")))],
                       lambda x:_norm(x.get("name")))
    result={"participants":participants,"lanes":lanes,"timers":_dedupe(timers,lambda x:(x.get("kind"),x.get("value"),_norm(x.get("activity")))),
            "parallelisms":parallelisms,"decisions":_dedupe(decisions,lambda x:(_norm(x["name"]),tuple(_norm(v) for v in x["outcomes"]))),
            "interactions":interactions,"activities":activities,"ambiguities":_dedupe(ambiguities,lambda x:_norm(x.get("message") or x.get("name") or x.get("requirement")))}
    return SemanticRequirements(**result).to_dict(),issues
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
    match=re.fullmatch(r"\s*(\d+)\s*(jour|jours|day|days|heure|heures|hour|hours|minute|minutes)\s*",str(value or ""),re.I)
    if not match:return str(value or "")
    amount=int(match.group(1));unit=match.group(2).lower()
    return f"P{amount}D" if unit.startswith(("j","d")) else f"PT{amount}{'H' if unit.startswith(('h','heure')) else 'M'}"
def _evidence_tokens(value):
    text=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower()
    aliases={"entreprise":"company","enterprise":"company","company":"company","customer":"client","client":"client"}
    return [aliases.get(token,token) for token in re.findall(r"[a-z0-9]+",text)]
def _actor_position(tokens,actor):
    wanted=_evidence_tokens(actor)
    if not wanted:return None
    width=len(wanted)
    return next((index for index in range(len(tokens)-width+1) if tokens[index:index+width]==wanted),None)
def _has_interaction_evidence(description,source,target):
    communication={"send","sends","sent","envoyer","envoie","envoient","transmit","transmits","transmettre",
                   "reply","replies","respond","responds","repond","repondre","inform","informs","informe","informer",
                   "notify","notifies","notified","notifie","notifier","communicate","communicates","share","shares"}
    for sentence in re.split(r"(?<=[.!?;])\s+|[\r\n]+",description):
        tokens=_evidence_tokens(sentence);source_pos=_actor_position(tokens,source);target_pos=_actor_position(tokens,target)
        if source_pos is None or target_pos is None or source_pos>=target_pos:continue
        if any(token in communication for token in tokens[source_pos+1:target_pos]):return True
    return False
def _has_temporal_evidence(description):
    text=unicodedata.normalize("NFKD",description).encode("ascii","ignore").decode().lower()
    number=r"(?:\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)"
    unit=r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|secondes?|heures?|jours?|semaines?|mois|ans?|annees?)"
    if re.search(rf"\b{number}\s+{unit}\b",text):return True
    if re.search(r"\b(?:every|each|tous?\s+les?|chaque|until|jusqu(?:a|')|deadline|echeance)\b",text):return True
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\b",text):return True
    return bool(re.search(r"\bP(?=\d|T)\d*(?:Y|M|W|D)(?:T\d+(?:H|M|S))?\b",description,re.I))
def _role_stem(token):
    for suffix in ("ing","ied","ed","es","s","ation","ment","er","ee","e"):
        if len(token)>len(suffix)+3 and token.endswith(suffix):return token[:-len(suffix)]
    return token
def _is_passive_end_state_activity(description,activity):
    ignored={"the","a","an","le","la","les","de","du","des"}
    activity_terms={_role_stem(token) for token in _evidence_tokens(activity) if token not in ignored}
    if not activity_terms:return False
    passive=re.compile(r"\b(?:is|are|was|were|be|been|est|sont|ete)\s+([a-z]+(?:ed|en)|[a-z]+(?:e|ee|es|ees))\b")
    for sentence in re.split(r"(?<=[.!?;])\s+|[\r\n]+",description):
        tokens=_evidence_tokens(sentence)
        if not passive.search(" ".join(tokens)):continue
        sentence_terms={_role_stem(token) for token in tokens if token not in ignored|{"is","are","was","were","be","been","est","sont","ete"}}
        if activity_terms<=sentence_terms:return True
    return False
def _valid_timer_value(kind,value):
    text=str(value or "").strip()
    if kind=="duration":return bool(re.fullmatch(r"P(?=.+)(?:\d+(?:\.\d+)?[YMWD])*(?:T(?:\d+(?:\.\d+)?[HMS])+)?",text))
    if kind=="date":return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?",text))
    return bool(re.fullmatch(r"R\d*/P.+",text))
def _issue(code,message,severity):return {"code":code,"message":message,"severity":severity}
