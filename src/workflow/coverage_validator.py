"""Semantic fidelity validation between requirements and canonical workflows."""
from __future__ import annotations
import re
import unicodedata
from collections.abc import Mapping

class CoverageValidator:
    CODES={"participants":"MISSING_PARTICIPANT","lanes":"MISSING_LANE","timers":"MISSING_TIMER",
           "parallelisms":"MISSING_PARALLELISM","decisions":"MISSING_DECISION","interactions":"MISSING_INTERACTION","activities":"MISSING_ACTIVITY"}
    def validate(self,requirements,workflow):
        if not isinstance(requirements,Mapping) or not isinstance(workflow,Mapping):return []
        participants=workflow.get("participants",[]);lanes=workflow.get("lanes",[]);nodes=workflow.get("nodes",[])
        flows=workflow.get("flows",[]);messages=workflow.get("messageFlows",[])
        participant_by_id={p.get("id"):p for p in participants if isinstance(p,Mapping)}
        node_by_id={n.get("id"):n for n in nodes if isinstance(n,Mapping)}
        indexes={
            "participants":[p for p in participants if isinstance(p,Mapping)],
            "lanes":[l for l in lanes if isinstance(l,Mapping)],
            "activities":[n for n in nodes if isinstance(n,Mapping)],
        }
        issues=[]
        for kind in ("participants","lanes","activities"):
            for req in requirements.get(kind,[]):
                if not any(self._matches(req,item) for item in indexes[kind]):issues.append(self._missing(kind,req))
        timers=[n for n in nodes if isinstance(n,Mapping) and isinstance(n.get("properties"),Mapping) and isinstance(n["properties"].get("timer"),Mapping)]
        for req in requirements.get("timers",[]):
            if not any(t["properties"]["timer"].get("value")==req.get("duration") and self._optional_context(req,t) for t in timers):issues.append(self._missing("timers",req))
        outgoing={}
        for flow in flows:
            if isinstance(flow,Mapping):outgoing.setdefault(flow.get("source"),[]).append(flow.get("target"))
        parallels=[n for n in nodes if isinstance(n,Mapping) and n.get("type")=="parallelGateway" and len(outgoing.get(n.get("id"),[]))>=2]
        decisions=[n for n in nodes if isinstance(n,Mapping) and n.get("type") in {"exclusiveGateway","inclusiveGateway","eventBasedGateway"} and len(outgoing.get(n.get("id"),[]))>=2]
        for req in requirements.get("parallelisms",[]):
            if not parallels:issues.append(self._missing("parallelisms",req))
        for req in requirements.get("decisions",[]):
            if not decisions:issues.append(self._missing("decisions",req))
        for req in requirements.get("interactions",[]):
            found=False
            for message in messages:
                if not isinstance(message,Mapping):continue
                source=node_by_id.get(message.get("source"),{});target=node_by_id.get(message.get("target"),{})
                sp=participant_by_id.get(source.get("participantRef"),{});tp=participant_by_id.get(target.get("participantRef"),{})
                if self._text_match(req.get("source"),sp) and self._text_match(req.get("target"),tp):found=True;break
            if not found:issues.append(self._missing("interactions",req))
        for ambiguity in requirements.get("ambiguities",[]):
            issues.append({"code":"SEMANTIC_AMBIGUITY","message":"Semantic requirement is ambiguous.","severity":"warning","requirement":ambiguity,"related_node_ids":[]})
        return issues
    def _missing(self,kind,req):return {"code":self.CODES[kind],"message":f"Missing semantic coverage for {kind}.","severity":"error","requirement":req,"related_node_ids":[]}
    def _matches(self,req,item):return self._text_match(req.get("name") if isinstance(req,Mapping) else req,item)
    def _optional_context(self,req,node):
        context=req.get("activity") or req.get("name")
        return not context or self._text_match(context,node)
    def _text_match(self,expected,item):
        wanted=self._tokens(expected);actual=self._tokens(item.get("name"),item.get("id")) if isinstance(item,Mapping) else self._tokens(item)
        return bool(wanted) and (wanted<=actual or actual<=wanted or len(wanted&actual)/len(wanted)>=0.6)
    @staticmethod
    def _tokens(*values):
        text=unicodedata.normalize("NFKD"," ".join(str(v or "") for v in values)).encode("ascii","ignore").decode().lower()
        return set(re.findall(r"[a-z0-9]+",text))
