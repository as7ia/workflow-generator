"""Canonical v2 prompt builders."""
from __future__ import annotations
import json
from typing import Any

_V2_RULES = """Contrat obligatoire :
- schemaVersion vaut exactement "2.0".
- Utilise processes[], participants[], lanes[], nodes[], flows[], messageFlows[] et ambiguities[].
- participant.processRef est obligatoire.
- lane.processRef et lane.participantRef sont obligatoires.
- node.processRef est obligatoire; node.participantRef et node.laneRef sont explicites ou null.
- flow.source et flow.target sont obligatoires; flow.processRef est obligatoire.
- Utilise uniquement source et target, jamais sourceRef ni targetRef.
- Un sequence flow reste dans un seul process: ses deux nodes ont le même processRef que flow.processRef.
- Un messageFlow relie des nodes de process différents et ne porte ni condition ni isDefault.
- actor est une information métier et ne détermine jamais l'ownership.
- N'utilise jamais participant.nodeRefs, lane.nodeRefs ou des lanes imbriquées.
- Types de nodes autorisés: startEvent, endEvent, userTask, serviceTask, manualTask, scriptTask, businessRuleTask, sendTask, receiveTask, exclusiveGateway, parallelGateway, inclusiveGateway, eventBasedGateway, subProcess, intermediateCatchEvent, intermediateThrowEvent.
- Un timer est représenté par intermediateCatchEvent avec properties.timer; timerEvent est interdit.
- Les sorties d'une parallelGateway ne portent aucune condition.
- Ne produis aucune coordonnée, waypoint ou XML BPMN."""

def build_generation_prompt(description:str,schema:dict[str,Any])->str:
    return ("Transforme la description métier en workflow JSON canonique v2.\n\n"
        f"Description métier:\n{description}\n\nJSON Schema officiel:\n{json.dumps(schema,ensure_ascii=False,indent=2,sort_keys=True)}\n\n"
        f"{_V2_RULES}\nConserve strictement l'intention métier. Retourne uniquement un objet JSON valide, sans Markdown ni explication.")

def build_repair_prompt(workflow:dict[str,Any],errors:list[dict[str,Any]],schema:dict[str,Any])->str:
    lines=[f"- [{e.get('code','ERROR')}] {e.get('message','Erreur non décrite')}" for e in errors if isinstance(e,dict)] or ["- Validation échouée"]
    return ("Corrige le workflow JSON canonique v2 sans altérer son intention métier.\n\nErreurs:\n"
        +"\n".join(lines)+f"\n\nWorkflow:\n{json.dumps(workflow,ensure_ascii=False,indent=2,sort_keys=True)}"
        f"\n\nJSON Schema officiel:\n{json.dumps(schema,ensure_ascii=False,indent=2,sort_keys=True)}\n\n{_V2_RULES}\n"
        "Connecte une tâche métier isolée; ne la supprime que si elle est démontrablement inutile. Retourne uniquement un objet JSON valide.")
