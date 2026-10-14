"""Semantic fidelity validation between requirements and canonical workflows."""
from __future__ import annotations
import re,unicodedata
from collections.abc import Mapping

_SEMANTIC_STOPWORDS={"le","la","les","un","une","de","du","des","au","aux","et","pour","sur","dans","par","the","to","of","and","for","service","department","dept"}
_SEMANTIC_ALIASES={
    "entreprise":"company","enterprise":"company","company":"company",
    "client":"client","customer":"client",
    "commercial":"sales","sales":"sales","sale":"sales",
    "financier":"finance","financiere":"finance","finance":"finance","financial":"finance",
    "demande":"request","request":"request","requests":"request",
    "reception":"receive","recevoir":"receive","recoit":"receive","recu":"receive",
    "receive":"receive","receives":"receive","received":"receive","receiving":"receive",
    "verification":"verify","verifier":"verify","verify":"verify","verifies":"verify",
    "verified":"verify","checking":"verify","check":"verify","checked":"verify",
    "validate":"verify","validates":"verify","validated":"verify","validation":"verify",
    "acceptation":"accept","accept":"accept","accepts":"accept","accepted":"accept",
    "approval":"accept","approved":"accept","approve":"accept",
    "rejet":"reject","rejeter":"reject","reject":"reject","rejects":"reject","rejection":"reject",
    "rejected":"reject","refus":"reject","refusal":"reject","refuse":"reject",
    "envoyer":"send","envoi":"send","send":"send","sends":"send","sent":"send",
    "transmettre":"send","transmission":"send","forward":"send","forwards":"send","forwarded":"send",
    "informer":"inform","informe":"inform","inform":"inform","informs":"inform","notify":"inform","notifies":"inform","notified":"inform",
    "approuver":"accept","approuve":"accept",
    "demander":"request","demande":"request","ask":"request","asks":"request",
    "attendre":"wait","attente":"wait","wait":"wait","waits":"wait","waiting":"wait",
    "reponse":"response","response":"response",
    "payer":"pay","paie":"pay","paiement":"pay","pay":"pay","pays":"pay","payment":"pay",
    "cloture":"close","cloturer":"close","close":"close","closed":"close","closing":"close",
}

def normalize_semantic_tokens(text: str) -> set[str]:
    """Return the significant generic FR/EN concepts contained in a label."""
    normalized=unicodedata.normalize("NFKD",str(text or "")).encode("ascii","ignore").decode().lower()
    result=set()
    for raw in re.findall(r"[a-z0-9]+",normalized):
        if raw in _SEMANTIC_STOPWORDS:continue
        token=_SEMANTIC_ALIASES.get(raw,raw)
        if token not in _SEMANTIC_ALIASES.values() and len(token)>4 and token.endswith("s"):
            token=token[:-1]
        result.add(_SEMANTIC_ALIASES.get(token,token))
    return result

def normalize_semantic_label(text):
    """Return a stable lightweight FR/EN normalization for a semantic label."""
    return " ".join(sorted(normalize_semantic_tokens(text)))

def semantic_label_match(a,b):
    """Match labels when one side contains all significant concepts of the other."""
    left=normalize_semantic_tokens(str(a or ""));right=normalize_semantic_tokens(str(b or ""))
    return bool(left and right) and (left==right or left<right or right<left)

_ACTIVITY_ACTIONS={"send","receive","verify","accept","reject","inform","wait","pay","close"}
_ACTIVITY_TYPES={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask","subProcess","intermediateCatchEvent"}

def semantic_activity_match(requirement,node):
    """Match a BPM activity by canonical action first, then business object."""
    if not isinstance(node,Mapping) or node.get("type") not in _ACTIVITY_TYPES:return False
    expected=normalize_semantic_tokens(requirement.get("name") if isinstance(requirement,Mapping) else requirement)
    actual=normalize_semantic_tokens(node.get("name"))
    if semantic_label_match(requirement.get("name") if isinstance(requirement,Mapping) else requirement,node.get("name")):return True
    expected_actions=expected&_ACTIVITY_ACTIONS;actual_actions=actual&_ACTIVITY_ACTIONS
    if node.get("type")=="sendTask":actual_actions.add("send")
    if node.get("type")=="receiveTask":actual_actions.add("receive")
    timer=isinstance(node.get("properties"),Mapping) and isinstance(node["properties"].get("timer"),Mapping)
    if timer:actual_actions.add("wait")
    if not expected_actions or not actual_actions or expected_actions.isdisjoint(actual_actions):return False
    if timer and "wait" in expected_actions:return True
    expected_objects=expected-_ACTIVITY_ACTIONS;actual_objects=actual-_ACTIVITY_ACTIONS
    return not expected_objects or not actual_objects or bool(expected_objects&actual_objects) or expected_objects<actual_objects or actual_objects<expected_objects

class CoverageValidator:
    CODES={"participants":"MISSING_PARTICIPANT","lanes":"MISSING_LANE","timers":"MISSING_TIMER","parallelisms":"MISSING_PARALLELISM","decisions":"MISSING_DECISION","interactions":"MISSING_INTERACTION","activities":"MISSING_ACTIVITY"}
    _STOPWORDS=_SEMANTIC_STOPWORDS

    def validate(self,requirements,workflow):
        issues,_=self.validate_with_report(requirements,workflow)
        return issues

    def validate_with_report(self,requirements,workflow):
        if not isinstance(requirements,Mapping) or not isinstance(workflow,Mapping):return [],[]
        participants=self._maps(workflow.get("participants"));lanes=self._maps(workflow.get("lanes"));nodes=self._maps(workflow.get("nodes"));flows=self._maps(workflow.get("flows"));messages=self._maps(workflow.get("messageFlows"))
        participant_by_id={p.get("id"):p for p in participants};lane_by_id={x.get("id"):x for x in lanes};node_by_id={n.get("id"):n for n in nodes};outgoing={}
        for flow in flows:outgoing.setdefault(flow.get("source"),[]).append(flow)
        issues=[];report=[]
        for req in self._valid(requirements,"participants",("name",)):
            if not any(self._name_match(req["name"],p) for p in participants):issues.append(self._missing("participants",req,self._participant_candidates(req,participants,nodes)))
        for req in self._valid(requirements,"lanes",("name",)):
            participant_ids={p.get("id") for p in participants if not req.get("participant") or self._name_match(req["participant"],p)}
            if not any(x.get("participantRef") in participant_ids and self._name_match(req["name"],x) for x in lanes):
                issues.append(self._missing("lanes",req,[n.get("id") for n in nodes if n.get("participantRef") in participant_ids]))
        timer_nodes=[n for n in nodes if n.get("type")=="intermediateCatchEvent" and isinstance(n.get("properties"),Mapping) and isinstance(n["properties"].get("timer"),Mapping)]
        timer_requirements=[]
        for raw_req in self._requirements(requirements,"timers"):
            req=self._canonical_timer_requirement(raw_req)
            if not req:continue
            timer_requirements.append(req)
            if not self._matching_timers(req,timer_nodes):issues.append(self._missing("timers",req,self._rank(req,timer_nodes,self._timer_score)))
        timer_activities=[req.get("activity") for req in timer_requirements if req.get("activity")]
        activity_types={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask","subProcess"}
        activity_nodes=[n for n in nodes if n.get("type") in activity_types]
        for req in self._valid(requirements,"activities",("name",)):
            candidates=[n for n in [*activity_nodes,*timer_nodes] if semantic_activity_match(req,n)]
            covered=bool(candidates) or any(self._text_match(req["name"],name) for name in timer_activities)
            report.append(self._match_report("activity",req,activity_nodes,candidates))
            if not covered:issues.append(self._missing("activities",req,self._rank(req,activity_nodes,self._name_score)))
        parallel_gateways=[n for n in nodes if n.get("type")=="parallelGateway" and len(outgoing.get(n.get("id"),[]))>=2]
        for req in self._requirements(requirements,"parallelisms"):
            branches=self._branch_names(req)
            if len(branches)<2:continue
            if not any(self._parallel_covers(g,branches,outgoing,node_by_id) for g in parallel_gateways):issues.append(self._missing("parallelisms",req,[g.get("id") for g in parallel_gateways]))
        decision_gateways=[n for n in nodes if n.get("type") in {"exclusiveGateway","inclusiveGateway"} and len(outgoing.get(n.get("id"),[]))>=2]
        for req in self._requirements(requirements,"decisions"):
            outcomes=req.get("outcomes") if isinstance(req,Mapping) else None
            valid_outcomes=isinstance(outcomes,list) and len([x for x in outcomes if str(x).strip()])>=2
            covered=(any(self._decision_covers(g,req,outgoing,node_by_id) for g in decision_gateways) if valid_outcomes
                     else any(self._name_match(req.get("name"),g) for g in decision_gateways))
            if not covered:issues.append(self._missing("decisions",req,self._rank(req,decision_gateways,self._name_score)))
            report.append(self._match_report("decision",req,decision_gateways,[g for g in decision_gateways if self._decision_covers(g,req,outgoing,node_by_id)]))
        for req in self._valid(requirements,"interactions",("source","target")):
            found=False
            for message in messages:
                source=node_by_id.get(message.get("source"));target=node_by_id.get(message.get("target"))
                if not source or not target:continue
                sp=participant_by_id.get(source.get("participantRef"));tp=participant_by_id.get(target.get("participantRef"))
                if not sp or not tp or not self._name_match(req["source"],sp) or not self._name_match(req["target"],tp):continue
                if not self._lane_matches(req.get("sourceLane"),source,sp,lanes,lane_by_id):continue
                if not self._lane_matches(req.get("targetLane"),target,tp,lanes,lane_by_id):continue
                found=True;break
            if not found:issues.append(self._missing("interactions",req,self._interaction_candidates(req,messages,node_by_id,participant_by_id)))
            report.append({"type":"interaction","requirement":dict(req),"candidates":[m.get("id") for m in messages],
                           "selected":message.get("id") if found else None,"score":1 if found else 0,"covered":found,
                           "reason":"matching messageFlow with owned endpoints" if found else "no matching owned messageFlow"})
        for ambiguity in self._requirements(requirements,"ambiguities"):
            issues.append({"code":"SEMANTIC_AMBIGUITY","message":"Semantic requirement is ambiguous.","severity":"warning","requirement":ambiguity,"related_node_ids":[]})
        return self._dedupe_issues(issues),report

    def _match_report(self,kind,req,candidates,matches):
        return {"type":kind,"requirement":dict(req),"candidates":[n.get("id") for n in candidates],
                "selected":matches[0].get("id") if len(matches)==1 else None,"score":1 if matches else 0,
                "covered":bool(matches),"reason":"semantic action/object match" if matches else "no compatible semantic match"}

    @staticmethod
    def _maps(value):return [x for x in value if isinstance(x,Mapping)] if isinstance(value,list) else []
    def _requirements(self,requirements,kind):return self._maps(requirements.get(kind,[]))
    def _valid(self,requirements,kind,fields):return [r for r in self._requirements(requirements,kind) if all(str(r.get(f) or "").strip() for f in fields)]
    def _missing(self,kind,req,related=()):return {"code":self.CODES[kind],"message":f"Missing semantic coverage for {kind}.","severity":"error","requirement":req,"related_node_ids":list(dict.fromkeys(x for x in related if x is not None))}
    def _name_match(self,expected,item):return self._text_match(expected,item.get("name")) if isinstance(item,Mapping) else self._text_match(expected,item)
    def _text_match(self,expected,actual):
        return semantic_label_match(expected,actual)
    def _similarity(self,a,b):
        left=self._tokens(a);right=self._tokens(b)
        return len(left&right)/len(left|right) if left and right else 0.0
    def _name_score(self,req,node):return self._similarity(req.get("name"),node.get("name"))
    def _matching_timers(self,req,nodes):
        return [n for n in nodes if n["properties"]["timer"].get("kind")==req.get("kind") and n["properties"]["timer"].get("value")==req.get("value") and (not req.get("activity") or self._name_match(req["activity"],n))]
    @staticmethod
    def _canonical_timer_requirement(req):
        if not isinstance(req,Mapping):return None
        value=req.get("value") or req.get("duration");kind=req.get("kind","duration")
        return {**dict(req),"kind":kind,"value":value} if kind in {"duration","date","cycle"} and value else None
    def _timer_score(self,req,node):
        timer=node.get("properties",{}).get("timer",{});score=float(timer.get("kind")==req.get("kind"))+float(timer.get("value")==req.get("value"))
        return score+(self._similarity(req.get("activity"),node.get("name")) if req.get("activity") else 0)
    @staticmethod
    def _branch_names(req):
        raw=req.get("branches",req.get("activities",[])) if isinstance(req,Mapping) else []
        if not isinstance(raw,list):return []
        return [value for item in raw for value in [item.get("activity") if isinstance(item,Mapping) else item] if str(value or "").strip()]
    def _parallel_covers(self,gateway,branches,outgoing,node_by_id):
        starts=[f.get("target") for f in outgoing.get(gateway.get("id"),[])];reachable=[self._reachable(x,outgoing,node_by_id) for x in starts]
        exclusive=[node_ids-set().union(*(other for j,other in enumerate(reachable) if j!=i)) for i,node_ids in enumerate(reachable)]
        choices=[{i for i,node_ids in enumerate(exclusive) if any(self._name_match(branch,node_by_id.get(n,{})) for n in node_ids)} for branch in branches]
        return all(choices) and self._distinct(choices)
    @staticmethod
    def _reachable(start,outgoing,node_by_id):
        visited=set();queue=[start]
        while queue:
            current=queue.pop(0)
            if current in visited or current not in node_by_id:continue
            visited.add(current);queue.extend(f.get("target") for f in outgoing.get(current,[]))
        return visited
    def _decision_covers(self,gateway,req,outgoing,node_by_id):
        if not self._name_match(req.get("name"),gateway):return False
        branches=outgoing.get(gateway.get("id"),[]);choices=[]
        for outcome in req.get("outcomes",[]):
            outcome=outcome.get("name") if isinstance(outcome,Mapping) else outcome
            matches={i for i,flow in enumerate(branches) if any(self._text_match(outcome,label) for label in (flow.get("condition"),flow.get("name"),node_by_id.get(flow.get("target"),{}).get("name")) if label)}
            if not matches:return False
            choices.append(matches)
        return self._distinct(choices)
    @staticmethod
    def _distinct(choices,index=0,used=None):
        used=set() if used is None else used
        return index==len(choices) or any(CoverageValidator._distinct(choices,index+1,used|{x}) for x in choices[index] if x not in used)
    def _lane_matches(self,expected,node,participant,lanes,lane_by_id):
        if not expected:return True
        lane=lane_by_id.get(node.get("laneRef"))
        if lane:return lane.get("participantRef")==participant.get("id") and self._name_match(expected,lane)
        compatible=[x for x in lanes if x.get("participantRef")==participant.get("id") and self._name_match(expected,x)]
        return len(compatible)==1
    def _interaction_candidates(self,req,messages,node_by_id,participant_by_id):
        ranked=[]
        for message in messages:
            source=node_by_id.get(message.get("source"),{});target=node_by_id.get(message.get("target"),{});sp=participant_by_id.get(source.get("participantRef"),{});tp=participant_by_id.get(target.get("participantRef"),{})
            score=self._similarity(req.get("source"),sp.get("name"))+self._similarity(req.get("target"),tp.get("name"))
            if score:ranked.append((score,source.get("id"),target.get("id")))
        if not ranked:return []
        best=max(x[0] for x in ranked);return [value for score,*values in ranked if score==best for value in values]
    def _participant_candidates(self,req,participants,nodes):
        scores=[(self._similarity(req.get("name"),p.get("name")),p.get("id")) for p in participants];best=max((x[0] for x in scores),default=0);ids={identifier for score,identifier in scores if score==best and score>0}
        return [n.get("id") for n in nodes if n.get("participantRef") in ids]
    @staticmethod
    def _rank(req,nodes,scorer):
        ranked=[(scorer(req,n),n.get("id")) for n in nodes];best=max((x[0] for x in ranked),default=0)
        return [identifier for score,identifier in ranked if score==best and score>0]
    @staticmethod
    def _dedupe_issues(issues):
        result=[];seen=set()
        for issue in issues:
            req=issue.get("requirement",{});marker=(issue.get("code"),repr(sorted(req.items())) if isinstance(req,Mapping) else str(req))
            if marker not in seen:seen.add(marker);result.append(issue)
        return result
    @classmethod
    def _tokens(cls,*values):
        return normalize_semantic_tokens(" ".join(str(v or "") for v in values))
