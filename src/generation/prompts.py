"""Canonical v2 prompt builders."""
from __future__ import annotations
import json
from typing import Any

_FIDELITY_RULES="""The output must be derived ONLY from the user description below.
- Do not invent participants that are not stated or strongly implied.
- Do not invent durations, business activities, interactions, decisions or parallel work.
- Do not reuse entities from examples or previous requests.
- Preserve the language and terminology of the user when possible.
- If information is missing, use ambiguities instead of inventing facts."""

def build_semantic_requirements_prompt(description:str)->str:
    return ("SEMANTIC_REQUIREMENTS_EXTRACTION\n"+_FIDELITY_RULES+"\n"
        "Extract only business requirements, not a BPMN workflow.\n"
        "Return JSON with exactly these arrays: participants, lanes, timers, parallelisms, decisions, interactions, activities, ambiguities.\n"
        "Participants are external actors or organizations explicitly present. Lanes are explicit internal roles and require name plus participant.\n"
        "Timers use kind, ISO 8601 value and optional activity, and exist only for explicit delays.\n"
        "Interactions use source and target participant names and may preserve sourceLane/targetLane.\n"
        "One parallelism contains a branches array and requires explicit simultaneity.\n"
        "A decision requires a genuine conditional choice and at least two outcomes; actions or states are not decisions.\n"
        "Do not duplicate a timer activity in activities. Deduplicate all requirements.\n"
        f"<USER_DESCRIPTION>\n{description}\n</USER_DESCRIPTION>\nReturn JSON only.")

_V2_RULES='''Contrat obligatoire :
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
- Ne produis aucune coordonnée, waypoint ou XML BPMN.

IMPORTANT BPMN COLLABORATION RULES:
1. Each participant/pool MUST reference its own process.
2. Two participants MUST NOT share the same processRef.
3. Every node processRef MUST match the processRef of its participantRef.
4. A sequenceFlow MUST connect nodes from the same process.
5. Communication between different participants/processes MUST use messageFlows.
6. Timer durations MUST use ISO 8601 (format example: 3 days = P3D). Convert only a duration explicitly present in the user description; never add the example duration.

Abstract structure example only: participant_alpha.processRef = process_alpha and
participant_beta.processRef = process_beta. Nodes owned by participant_alpha use
process_alpha; nodes owned by participant_beta use process_beta. Communication
between those nodes belongs in messageFlows, never flows.'''

_REPAIR_RULES='''Contraintes canoniques essentielles :
- schemaVersion vaut exactement "2.0".
- Conserve processes, participants, lanes, nodes, flows, messageFlows et ambiguities.
- participant.processRef, lane.processRef, lane.participantRef, node.processRef et flow.processRef sont cohérents.
- Utilise source et target, jamais sourceRef ni targetRef.
- Un flow reste dans un seul process; une communication entre process utilise messageFlows.
- N'utilise jamais participant.nodeRefs, lane.nodeRefs ou des lanes imbriquées.
- Types autorisés: startEvent, endEvent, userTask, serviceTask, manualTask, scriptTask, businessRuleTask, sendTask, receiveTask, exclusiveGateway, parallelGateway, inclusiveGateway, eventBasedGateway, subProcess, intermediateCatchEvent, intermediateThrowEvent.
- Un timer est un intermediateCatchEvent avec properties.timer et une valeur ISO 8601.
- Une parallelGateway ne porte aucune condition sortante.'''

def build_generation_prompt(description:str,schema:dict[str,Any],requirements:dict[str,Any]|None=None)->str:
    semantic_context=(f"\n\n<VALIDATED_SEMANTIC_REQUIREMENTS>\n{json.dumps(requirements,ensure_ascii=False,indent=2,sort_keys=True)}\n</VALIDATED_SEMANTIC_REQUIREMENTS>" if requirements is not None else "")
    return ("Transforme la description métier en workflow JSON canonique v2.\n"+_FIDELITY_RULES+"\n\n"
        f"<USER_DESCRIPTION>\n{description}\n</USER_DESCRIPTION>{semantic_context}\n\n"
        f"JSON Schema officiel:\n{json.dumps(schema,ensure_ascii=False,indent=2,sort_keys=True)}\n\n"
        f"{_V2_RULES}\nConserve strictement l'intention métier. Retourne uniquement un objet JSON valide, sans Markdown ni explication.")

def build_repair_prompt(workflow:dict[str,Any],errors:list[dict[str,Any]],schema:dict[str,Any],description:str|None=None,requirements:dict[str,Any]|None=None)->str:
    del schema
    lines=[f"- [{e.get('code','ERROR')}] {e.get('message','Erreur non décrite')}" for e in errors if isinstance(e,dict)] or ["- Validation échouée"]
    context=(f"{_FIDELITY_RULES}\n<USER_DESCRIPTION>\n{description}\n</USER_DESCRIPTION>\n\nExigences sémantiques validées:\n{json.dumps(requirements,ensure_ascii=False,indent=2)}\n\n" if description is not None and requirements is not None else "")
    return ("Corrige uniquement les informations manquantes du workflow JSON canonique v2 sans le reconstruire inutilement.\n\n"+context+"Erreurs:\n"
        +"\n".join(lines)+f"\n\nWorkflow:\n{json.dumps(workflow,ensure_ascii=False,separators=(',',':'),sort_keys=True)}"
        f"\n\n{_REPAIR_RULES}\n"
        "Pour MULTIPLE_PARTICIPANTS_FOR_PROCESS: Create one distinct process per participant and update participant.processRef, node.processRef, lane.processRef and flow.processRef consistently.\n"
        "Pour MESSAGE_FLOW_WITHIN_SAME_PROCESS, sépare d'abord les processes des participants si nécessaire avant de modifier le messageFlow.\n"
        "Pour INVALID_TIMER_DURATION, utilise obligatoirement une durée ISO 8601 déjà supportée par la description.\n"
        "Connecte une tâche métier isolée; ne la supprime que si elle est démontrablement inutile. Retourne uniquement un objet JSON valide.")

def build_coverage_repair_prompt(workflow:dict[str,Any],errors:list[dict[str,Any]],requirements:dict[str,Any])->str:
    """Build the deliberately small prompt used for semantic coverage only."""
    blocking=[]
    for error in errors:
        if not isinstance(error,dict) or error.get("severity","error")!="error":continue
        blocking.append({key:error[key] for key in ("code","message","requirement") if key in error})
    return ("Corrige uniquement ces exigences manquantes dans le workflow existant. Ne régénère pas le workflow et préserve tous ses processus, participants, nœuds et flux existants.\n\n"
        f"Exigences sémantiques nécessaires:\n{json.dumps(requirements,ensure_ascii=False,separators=(',',':'),sort_keys=True)}\n\n"
        f"Erreurs de couverture bloquantes:\n{json.dumps(blocking,ensure_ascii=False,separators=(',',':'),sort_keys=True)}\n\n"
        f"Workflow actuel:\n{json.dumps(workflow,ensure_ascii=False,separators=(',',':'),sort_keys=True)}\n\n"
        "Retourne uniquement l'objet JSON complet corrigé, sans Markdown ni explication.")
