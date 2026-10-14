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
    if isinstance(description,str):
        grounded,issues,_=ground_semantic_requirements(raw,description)
        return grounded,issues
    return _normalize_semantic_requirements(raw,description)
def _normalize_semantic_requirements(raw,description=None):
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
        outcomes=[]
        if isinstance(item.get("outcomes"),list):
            for value in item["outcomes"]:
                if isinstance(value,Mapping):
                    outcome=copy.deepcopy(dict(value))
                    if _norm(outcome.get("name")):outcomes.append(outcome)
                elif str(value).strip():outcomes.append(str(value).strip())
            outcomes=_dedupe(outcomes,lambda value:_norm(value.get("name")) if isinstance(value,Mapping) else _norm(value))
        if not _norm(item.get("name")) or len(outcomes)<2:
            ambiguities.append({"message":"Decision requires a name and at least two outcomes.","requirement":item});continue
        decisions.append({"name":item["name"],"outcomes":outcomes})
    timer_activities={_norm(x.get("activity")) for x in timers if x.get("activity")}
    activities=_dedupe([{"name":x.get("name","")} for x in collections["activities"]
                        if _norm(x.get("name")) and _norm(x.get("name")) not in timer_activities
                        and (not isinstance(description,str) or not _activity_is_passive_only(description,x.get("name")))],
                       lambda x:_norm(x.get("name")))
    activities=_prefer_complete_activities(activities)
    result={"participants":participants,"lanes":lanes,"timers":_dedupe(timers,lambda x:(x.get("kind"),x.get("value"),_norm(x.get("activity")))),
            "parallelisms":parallelisms,"decisions":_dedupe(decisions,lambda x:(_norm(x["name"]),tuple(_norm(v.get("name")) if isinstance(v,Mapping) else _norm(v) for v in x["outcomes"]))),
            "interactions":interactions,"activities":activities,"ambiguities":_dedupe(ambiguities,lambda x:_norm(x.get("message") or x.get("name") or x.get("requirement")))}
    return SemanticRequirements(**result).to_dict(),issues
def ground_semantic_requirements(raw,description):
    """Ground Qwen candidates individually and supplement only explicit text evidence."""
    source=copy.deepcopy(dict(raw)) if isinstance(raw,Mapping) else {}
    report=[];candidate={key:_items(source.get(key,[]),key,[]) for key in KEYS}
    for item in candidate["decisions"]:
        if not item.get("name") and item.get("condition"):item["name"]=item["condition"]
        if not item.get("outcomes"):
            outcomes=[item.get("trueOutcome"),item.get("falseOutcome")]
            if all(str(value or "").strip() for value in outcomes):item["outcomes"]=outcomes
    for kind in ("participants","timers","interactions","activities","decisions"):
        retained=[]
        for item in candidate[kind]:
            status,evidence,reason=_grounding_status(kind,item,description)
            report.append({"type":kind,"requirement":copy.deepcopy(item),"status":status,"evidence":evidence,"reason":reason})
            if status!="UNSUPPORTED":retained.append(item)
        candidate[kind]=retained
    explicit=_extract_explicit_requirements(description,candidate.get("participants",[]))
    for kind,items in explicit.items():
        existing={_requirement_marker(kind,item) for item in candidate[kind]}
        for item,evidence in items:
            marker=_requirement_marker(kind,item)
            if marker in existing or (kind=="activities" and any(_same_activity_candidate(current.get("name"),item.get("name")) for current in candidate[kind])):continue
            candidate[kind].append(item);existing.add(marker)
            report.append({"type":kind,"requirement":copy.deepcopy(item),"status":"SUPPORTED",
                           "evidence":evidence,"reason":"Explicit linguistic evidence extracted from the description."})
    normalized,issues=_normalize_semantic_requirements(candidate,description)
    return normalized,issues,report

def _grounding_status(kind,item,description):
    sentences=_sentences(description)
    if kind=="timers":
        evidence=next((sentence for sentence in sentences if _has_temporal_evidence(sentence)),"")
        return (("SUPPORTED",evidence,"Explicit temporal expression found.") if evidence else
                ("UNSUPPORTED","","No temporal expression supports this timer."))
    if kind=="interactions":
        evidence=_interaction_evidence(description,item.get("source"),item.get("target"))
        return (("SUPPORTED",evidence,"Explicit directed communication found.") if evidence else
                ("UNSUPPORTED","","No explicit directed communication supports this interaction."))
    if kind=="participants":
        evidence=next((sentence for sentence in sentences if _actor_position(_evidence_tokens(sentence),item.get("name")) is not None),"")
        return (("SUPPORTED",evidence,"Actor explicitly mentioned.") if evidence else
                ("AMBIGUOUS","","Participant label may be a paraphrase not recoverable deterministically."))
    if kind=="activities":
        matching=[sentence for sentence in sentences if _activity_evidence_sentence(item.get("name"),sentence)]
        if not matching:return "UNSUPPORTED","","No clause expresses this activity."
        if _activity_is_passive_only(description,item.get("name")):return "UNSUPPORTED",matching[0],"Only a passive end state was found."
        return "SUPPORTED",matching[0],"Explicit action clause found."
    if kind=="decisions":
        conditional=[sentence for sentence in sentences if _is_conditional(sentence)]
        evidence=next((sentence for sentence in conditional if _semantic_overlap(item.get("name") or item.get("condition"),sentence)),conditional[0] if len(conditional)==1 else "")
        return (("SUPPORTED",evidence,"Explicit conditional branch found.") if evidence else
                ("UNSUPPORTED","","No explicit conditional clause supports this decision."))
    return "AMBIGUOUS","","No deterministic grounding rule for this requirement type."

def _extract_explicit_requirements(description,participants):
    result={kind:[] for kind in ("participants","timers","interactions","activities","decisions")}
    actors=[item.get("name") for item in participants if isinstance(item,Mapping) and str(item.get("name") or "").strip()]
    if not actors:
        actors=list(dict.fromkeys(re.findall(r"(?<![A-Za-z])([A-Z])(?=\s+(?:sends?|receives?|checks?|verifies?|approves?|rejects?|requests?|informs?|notifies?|provides?|submits?|forwards?))",description)))
        result["participants"].extend(({"name":actor},actor) for actor in actors)
    for sentence in _sentences(description):
        tokens=_evidence_tokens(sentence)
        for actor in actors:
            position=_actor_position(tokens,actor)
            if position is None or position>1 or (position==1 and tokens[0] not in {"the","a","an","le","la","les","l","un","une","otherwise","else","sinon"}):continue
            actor_width=len(_evidence_tokens(actor));phrase=tokens[position+actor_width:]
            if not phrase or phrase[0] in {"is","are","was","were","est","sont"}:continue
            chunks=_split_action_chunks(phrase,actors)
            result["activities"].extend(({"name":" ".join(chunk)},sentence) for chunk in chunks if chunk)
        for source in actors:
            for target in actors:
                if source==target:continue
                evidence=_interaction_evidence(sentence,source,target)
                if evidence:result["interactions"].append(({"source":source,"target":target},evidence))
    for match in re.finditer(r"\b(?:if|si)\s+([^,.;]+)[,]\s*([^.;]+)[.;]\s*(?:otherwise|else|sinon)\s*[, :]?\s*([^.;]+)",description,re.I):
        condition,true_branch,false_branch=(value.strip() for value in match.groups())
        result["decisions"].append(({"name":condition,"outcomes":[true_branch,false_branch]},match.group(0)))
    duration=_explicit_duration(description)
    if duration:result["timers"].append(({"kind":"duration","value":duration[0]},duration[1]))
    return result

def _sentences(description):return [value.strip() for value in re.split(r"(?<=[.!?;])\s+|,\s*|[\r\n]+",description) if value.strip()]
def _semantic_match(left,right):
    from .coverage_validator import semantic_label_match
    return semantic_label_match(left,right)
def _semantic_overlap(left,right):
    a=set(_evidence_tokens(left));b=set(_evidence_tokens(right));return bool(a&b)
def _activity_evidence_sentence(activity,sentence):
    if _semantic_match(activity,sentence):return True
    from .coverage_validator import normalize_semantic_tokens
    common=normalize_semantic_tokens(activity)&normalize_semantic_tokens(sentence)
    pronouns={"it","this","that","them","he","she","elle","il","le","la","les","lui","cela","ceci"}
    return bool(common and pronouns&set(_evidence_tokens(sentence)))
def _same_activity_candidate(left,right):
    from .coverage_validator import normalize_semantic_tokens
    left_tokens=normalize_semantic_tokens(left);right_tokens=normalize_semantic_tokens(right)
    if left_tokens==right_tokens:return True
    pronouns={"it","this","that","them","he","she","elle","il","le","la","les","lui","cela","ceci"}
    return bool(left_tokens&right_tokens and pronouns&set(_evidence_tokens(right)))
def _is_conditional(sentence):return bool(re.search(r"\b(?:if|else|otherwise|si|sinon|selon|depending\s+on|en\s+fonction\s+de)\b",sentence,re.I))
def _split_action_chunks(tokens,actors):
    boundaries={"and","then","et","puis","ensuite"};chunks=[];current=[]
    actor_tokens={token for actor in actors for token in _evidence_tokens(actor) if len(token)>1}
    for token in tokens:
        if token in boundaries:
            if current:chunks.append(current);current=[]
            continue
        if token in {"to","from","de","vers"} or token in actor_tokens:break
        if token in {"a","an","the","un","une","le","la","les"}:continue
        current.append(token)
    if current:chunks.append(current)
    return chunks
def _requirement_marker(kind,item):
    if kind=="interactions":return (_norm(item.get("source")),_norm(item.get("target")))
    if kind=="timers":return (item.get("kind"),item.get("value") or item.get("duration"))
    if kind=="decisions":return (_norm(item.get("name") or item.get("condition")),tuple(_norm(value.get("name")) if isinstance(value,Mapping) else _norm(value) for value in item.get("outcomes",[]) if value))
    return _norm(item.get("name"))
def _explicit_duration(description):
    text=unicodedata.normalize("NFKD",description).encode("ascii","ignore").decode().lower()
    words={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
           "un":1,"une":1,"deux":2,"trois":3,"quatre":4,"cinq":5,"six":6,"sept":7,"huit":8,"neuf":9,"dix":10}
    match=re.search(r"\b(\d+|"+"|".join(words)+r")\s+(seconds?|minutes?|hours?|days?|weeks?|secondes?|heures?|jours?|semaines?)\b",text)
    if not match:return None
    amount=int(match.group(1)) if match.group(1).isdigit() else words[match.group(1)];unit=match.group(2)
    value=(f"P{amount*7}D" if unit.startswith(("week","semaine")) else f"P{amount}D" if unit.startswith(("day","jour"))
           else f"PT{amount}H" if unit.startswith(("hour","heure")) else f"PT{amount}M" if unit.startswith("minute") else f"PT{amount}S")
    return value,match.group(0)
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
def _prefer_complete_activities(activities):
    """Drop only lexical fragments subsumed by a more complete activity."""
    tokenized=[]
    for item in activities:
        from .coverage_validator import activity_semantic_components
        parts=activity_semantic_components(item.get("name"));tokenized.append((item,parts))
    repetition={"again","repeat","repeated","nouveau","recheck"}
    return [item for item,parts in tokenized if not any(
        parts["tokens"] and parts["tokens"]<other_parts["tokens"]
        and (parts["actions"]==other_parts["actions"] or len(parts["tokens"])==1)
        and not ((other_parts["tokens"]-parts["tokens"])&repetition)
        for other,other_parts in tokenized if other is not item)]
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
    return bool(_interaction_evidence(description,source,target))
def _interaction_evidence(description,source,target):
    outgoing={"send","sends","sent","envoyer","envoie","envoient","transmit","transmits","transmettre","forward","forwards","forwarded",
              "reply","replies","respond","responds","repond","repondre","inform","informs","informe","informer","notify","notifies","notified",
              "notifie","notifier","communicate","communicates","share","shares","provide","provides","provided","submit","submits","submitted",
              "request","requests","requested","demande","demander"}
    receiving={"receive","receives","received","recevoir","recoit","recu"}
    for sentence in _sentences(description):
        tokens=_evidence_tokens(sentence);source_pos=_actor_position(tokens,source);target_pos=_actor_position(tokens,target)
        if source_pos is None or target_pos is None:continue
        if source_pos<target_pos and any(token in outgoing for token in tokens[source_pos+1:target_pos]):return sentence
        if target_pos<source_pos and any(token in receiving for token in tokens[target_pos+1:source_pos]) and "from" in tokens[target_pos+1:source_pos+1]:return sentence
    return ""
def _has_temporal_evidence(description):
    text=unicodedata.normalize("NFKD",description).encode("ascii","ignore").decode().lower()
    number=r"(?:\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)"
    unit=r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|secondes?|heures?|jours?|semaines?|mois|ans?|annees?)"
    if re.search(rf"\b{number}\s+{unit}\b",text):return True
    if re.search(r"\b(?:every|each|tous?\s+les?|chaque|until|jusqu(?:a|')|deadline|echeance)\b",text):return True
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\b",text):return True
    return bool(re.search(r"\bP(?=\d|T)\d*(?:Y|M|W|D)(?:T\d+(?:H|M|S))?\b",description,re.I))
def _activity_is_passive_only(description,activity):
    from .coverage_validator import semantic_label_match
    passive=re.compile(r"\b(?:is|are|was|were|est|sont|ete)\s+[a-z]+(?:ed|en|e|ee|es|ees)\b")
    matching=[]
    for sentence in re.split(r"(?<=[.!?;])\s+|[\r\n]+",description):
        normalized=" ".join(_evidence_tokens(sentence))
        if semantic_label_match(activity,normalized):matching.append(bool(passive.search(normalized)))
    return bool(matching) and all(matching)
def _valid_timer_value(kind,value):
    text=str(value or "").strip()
    if kind=="duration":return bool(re.fullmatch(r"P(?=.+)(?:\d+(?:\.\d+)?[YMWD])*(?:T(?:\d+(?:\.\d+)?[HMS])+)?",text))
    if kind=="date":return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?",text))
    return bool(re.fullmatch(r"R\d*/P.+",text))
def _issue(code,message,severity):return {"code":code,"message":message,"severity":severity}
