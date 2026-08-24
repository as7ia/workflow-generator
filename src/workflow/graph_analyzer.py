"""Deterministic analysis of canonical workflow model v2."""
from __future__ import annotations
from collections import deque
from collections.abc import Mapping
from typing import Any

class WorkflowGraphAnalyzer:
    _GATEWAY_TYPES={"exclusiveGateway","parallelGateway","inclusiveGateway","eventBasedGateway"}

    def analyze(self, workflow: dict[str,Any])->dict[str,Any]:
        src=workflow if isinstance(workflow,Mapping) else {}; warnings=[]
        processes=self._index(src.get("processes"),"process",warnings)
        participants=self._index(src.get("participants"),"participant",warnings)
        lanes=self._index(src.get("lanes"),"lane",warnings)
        nodes=self._index(src.get("nodes"),"node",warnings)
        flows=self._index(src.get("flows"),"flow",warnings)
        messages=self._index(src.get("messageFlows"),"message flow",warnings)
        node_buckets={pid:{} for pid in processes}
        for nid,node in nodes.items():
            pid=node.get("processRef")
            if pid not in processes:
                warnings.append(self._warning("UNKNOWN_NODE_PROCESS_DURING_ANALYSIS",f"Node '{nid}' references an unknown process.",node_id=nid,process_id=pid))
            else: node_buckets[pid][nid]=node
        flow_buckets={pid:[] for pid in processes}
        for fid,flow in flows.items():
            source,target=flow.get("source"),flow.get("target")
            sn,tn=nodes.get(source),nodes.get(target); pid=flow.get("processRef")
            if sn is None or tn is None:
                warnings.append(self._warning("UNKNOWN_FLOW_ENDPOINT_DURING_ANALYSIS",f"Flow '{fid}' has an unknown endpoint.",flow_id=fid,process_id=pid)); continue
            if pid not in processes or sn.get("processRef")!=pid or tn.get("processRef")!=pid:
                warnings.append(self._warning("FLOW_PROCESS_MISMATCH_DURING_ANALYSIS",f"Flow '{fid}' does not stay inside its declared process.",flow_id=fid,process_id=pid)); continue
            flow_buckets[pid].append((fid,source,target))
        analyses={pid:self._process(pid,node_buckets[pid],flow_buckets[pid]) for pid in sorted(processes)}
        collaboration=self._collaboration(participants,lanes,nodes,messages,warnings)
        return {"processes":analyses,"collaboration":collaboration,"warnings":sorted(warnings,key=self._warning_key)}

    def _process(self,pid,nodes,edges):
        ids=sorted(nodes); adjsets={n:set() for n in ids}; revsets={n:set() for n in ids}
        for _,source,target in sorted(edges): adjsets[source].add(target); revsets[target].add(source)
        adj={n:sorted(adjsets[n]) for n in ids}; rev={n:sorted(revsets[n]) for n in ids}
        indeg={n:len(rev[n]) for n in ids}; outdeg={n:len(adj[n]) for n in ids}; types={n:nodes[n].get("type") for n in ids}
        scc=self._scc(ids,adj); component_of,condensed=self._condense(scc,adj); clevels,corder=self._levels(condensed)
        levels={n:clevels[component_of[n]] for n in ids}
        gateways=[n for n in ids if types[n] in self._GATEWAY_TYPES]
        splits=[n for n in gateways if outdeg[n]>1]; joins=[n for n in gateways if indeg[n]>1]
        return {"node_ids":ids,"start_nodes":[n for n in ids if types[n]=="startEvent"],"end_nodes":[n for n in ids if types[n]=="endEvent"],
          "nodes_without_predecessors":[n for n in ids if indeg[n]==0],"nodes_without_successors":[n for n in ids if outdeg[n]==0],
          "adjacency":adj,"reverse_adjacency":rev,"in_degree":indeg,"out_degree":outdeg,"levels":levels,
          "components":self._components(ids,adj,rev),"strongly_connected_components":scc,
          "condensed_components":{str(i):v for i,v in enumerate(scc)},"condensed_adjacency":{str(i):[str(x) for x in condensed[i]] for i in sorted(condensed)},
          "cycles":[c for c in scc if len(c)>1 or (c and c[0] in adj[c[0]])],"split_gateways":splits,"join_gateways":joins,
          "branches":self._branches(pid,splits,types,adj,rev,levels),"back_edges":self._back_edges(pid,ids,adj,edges),
          "topological_order":[n for c in corder for n in scc[c]]}

    def _collaboration(self,participants,lanes,nodes,messages,warnings):
        np={}; npa={}; nl={}
        for nid,node in nodes.items():
            if isinstance(node.get("processRef"),str): np[nid]=node["processRef"]
            if isinstance(node.get("participantRef"),str): npa[nid]=node["participantRef"]
            if isinstance(node.get("laneRef"),str): nl[nid]=node["laneRef"]
        pr={k:{"processRef":v.get("processRef"),"name":v.get("name")} for k,v in participants.items()}
        lr={k:{"processRef":v.get("processRef"),"participantRef":v.get("participantRef"),"name":v.get("name")} for k,v in lanes.items()}
        mr=[]
        for mid,msg in messages.items():
            copy=dict(msg); sn=nodes.get(msg.get("source")); tn=nodes.get(msg.get("target"))
            if sn is None or tn is None: warnings.append(self._warning("UNKNOWN_MESSAGE_FLOW_ENDPOINT_DURING_ANALYSIS",f"Message flow '{mid}' has an unknown endpoint.",flow_id=mid))
            copy.update({"source_process":sn.get("processRef") if sn else None,"target_process":tn.get("processRef") if tn else None,
                         "source_participant":sn.get("participantRef") if sn else None,"target_participant":tn.get("participantRef") if tn else None}); mr.append(copy)
        return {"participants":pr,"lanes":lr,"message_flows":mr,"node_to_process":dict(sorted(np.items())),"node_to_participant":dict(sorted(npa.items())),"node_to_lane":dict(sorted(nl.items()))}

    @staticmethod
    def _index(raw,label,warnings):
        result={}; raw=[] if raw is None else raw
        if not isinstance(raw,list): warnings.append(WorkflowGraphAnalyzer._warning("INVALID_COLLECTION_DURING_ANALYSIS",f"The {label} collection is not a list.")); return result
        for i,item in enumerate(raw):
            iid=item.get("id") if isinstance(item,Mapping) else None
            if not isinstance(iid,str) or not iid: warnings.append(WorkflowGraphAnalyzer._warning("INVALID_ITEM_DURING_ANALYSIS",f"Invalid {label} at index {i}."))
            elif iid in result: warnings.append(WorkflowGraphAnalyzer._warning("DUPLICATE_ID_DURING_ANALYSIS",f"Duplicate {label} id '{iid}'."))
            else: result[iid]=dict(item)
        return {k:result[k] for k in sorted(result)}

    @staticmethod
    def _components(ids,adj,rev):
        unseen=set(ids); result=[]
        while unseen:
            todo=[min(unseen)]; found=set()
            while todo:
                n=todo.pop()
                if n in found: continue
                found.add(n); todo.extend(reversed(sorted(set(adj[n])|set(rev[n]))))
            unseen-=found; result.append(sorted(found))
        return result

    @staticmethod
    def _scc(ids,adj):
        counter=0; indexes={}; lows={}; stack=[]; active=set(); result=[]
        def visit(n):
            nonlocal counter
            indexes[n]=lows[n]=counter; counter+=1; stack.append(n); active.add(n)
            for target in adj[n]:
                if target not in indexes: visit(target); lows[n]=min(lows[n],lows[target])
                elif target in active: lows[n]=min(lows[n],indexes[target])
            if lows[n]==indexes[n]:
                comp=[]
                while True:
                    member=stack.pop(); active.remove(member); comp.append(member)
                    if member==n: break
                result.append(sorted(comp))
        for n in ids:
            if n not in indexes: visit(n)
        return sorted(result,key=lambda c:c[0])

    @staticmethod
    def _condense(scc,adj):
        owner={n:i for i,c in enumerate(scc) for n in c}; dag={i:set() for i in range(len(scc))}
        for source,targets in adj.items():
            for target in targets:
                if owner[source]!=owner[target]: dag[owner[source]].add(owner[target])
        return owner,{i:sorted(v) for i,v in dag.items()}

    @staticmethod
    def _levels(adj):
        degree={n:0 for n in adj}
        for targets in adj.values():
            for target in targets: degree[target]+=1
        ready=sorted(n for n,d in degree.items() if d==0); levels={n:0 for n in adj}; order=[]
        while ready:
            n=ready.pop(0); order.append(n)
            for target in adj[n]:
                levels[target]=max(levels[target],levels[n]+1); degree[target]-=1
                if degree[target]==0: ready.append(target); ready.sort()
        return levels,order

    @staticmethod
    def _back_edges(pid,ids,adj,edges):
        state={}; pairs=set()
        def visit(n):
            state[n]=1
            for target in adj[n]:
                if state.get(target,0)==0: visit(target)
                elif state.get(target)==1: pairs.add((n,target))
            state[n]=2
        for n in ids:
            if state.get(n,0)==0: visit(n)
        return [{"flow_id":fid,"source":s,"target":t,"process_id":pid} for fid,s,t in sorted(edges) if (s,t) in pairs]

    def _branches(self,pid,splits,types,adj,rev,levels):
        result=[]; splitset=set(splits)
        for split in splits:
            starts=adj[split]; distances={s:self._distances(s,adj,{split}) for s in starts}
            common=set.intersection(*(set(v) for v in distances.values())) if distances else set()
            candidates=[n for n in common if len(rev[n])>1 and n!=split]
            join=min(candidates,key=lambda n:(max(distances[s][n] for s in starts),sum(distances[s][n] for s in starts),n),default=None)
            branch_nodes={s:sorted(self._collect(s,join,adj,{split}),key=lambda n:(levels[n],n)) for s in starts}
            nested=sorted((set().union(*map(set,branch_nodes.values())) if branch_nodes else set())&(splitset-{split}))
            result.append({"split_gateway":split,"process_id":pid,"branch_starts":starts,"join_node":join,"branches_end_separately":join is None,"branch_nodes":branch_nodes,"nested_splits":nested,"gateway_type":types[split]})
        return result

    @staticmethod
    def _distances(start,adj,blocked):
        distances={start:0}; todo=deque([start])
        while todo:
            n=todo.popleft()
            for target in adj[n]:
                if target not in blocked and target not in distances: distances[target]=distances[n]+1; todo.append(target)
        return distances
    @staticmethod
    def _collect(start,stop,adj,blocked):
        found=set(); todo=[start]
        while todo:
            n=todo.pop()
            if n in found or n in blocked or n==stop: continue
            found.add(n); todo.extend(reversed(adj[n]))
        return found

    @staticmethod
    def _warning(code,message,node_id=None,flow_id=None,process_id=None):
        return {"code":code,"message":message,"node_id":node_id,"flow_id":flow_id,"severity":"warning","process_id":process_id}
    @staticmethod
    def _warning_key(w): return tuple(str(w.get(k) or "") for k in ("code","process_id","node_id","flow_id","message"))
