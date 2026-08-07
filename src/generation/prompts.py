"""Prompt builders for workflow generation and repair."""

from __future__ import annotations

import json
from typing import Any


def build_generation_prompt(description: str, schema: dict[str, Any]) -> str:
    """Build a schema-grounded JSON-only workflow generation prompt."""
    return (
        "Transforme la description métier ci-dessous en un workflow JSON conforme au JSON Schema fourni.\n\n"
        f"Description métier :\n{description}\n\n"
        f"JSON Schema officiel :\n{json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        "Retourne uniquement un objet JSON valide. N'utilise ni Markdown, ni bloc de code, ni explication.\n"
        "Utilise source et target comme propriétés canoniques des flux.\n"
        "Ne produis jamais sourceRef ou targetRef dans le résultat final.\n"
        "Distingue les sequence flows internes d'un participant des messageFlows entre participants ou pools.\n"
        "flows ne contient que des sequence flows dont source et target appartiennent au même participant ou pool.\n"
        "Un sequence flow ne traverse jamais la frontière d'un pool.\n"
        "Lorsqu'une étape communique avec un autre participant, place cette communication dans messageFlows.\n"
        "Un messageFlow relie deux participants ou pools différents.\n"
        "messageFlow n'est jamais un type de node et doit uniquement apparaître dans messageFlows.\n"
        "Aucun nœud isolé n'est autorisé : toutes les tâches doivent être reliées par des sequence flows ou des message flows appropriés.\n"
        "Les flux sortant d'une parallelGateway ne doivent porter aucune condition.\n"
        "Types de nœuds autorisés : startEvent, endEvent, userTask, serviceTask, manualTask, scriptTask, businessRuleTask, exclusiveGateway, parallelGateway, inclusiveGateway, eventBasedGateway, subProcess, intermediateCatchEvent, intermediateThrowEvent.\n"
        "Gère les participants, pools, lanes et leurs nodeRefs lorsque la description les exige.\n"
        "Utilise correctement les passerelles exclusiveGateway, parallelGateway et inclusiveGateway.\n"
        "Exprime les durées de timer au format ISO 8601.\n"
        "Conserve strictement l'intention métier et n'invente aucune étape inutile.\n"
        "Ne génère aucune coordonnée, aucun waypoint et aucun XML BPMN."
    )


def build_repair_prompt(
    workflow: dict[str, Any], errors: list[dict[str, Any]], schema: dict[str, Any]
) -> str:
    """Build a schema-grounded JSON-only prompt for workflow correction."""
    error_lines = [
        f"- [{error.get('code', 'ERROR')}] {error.get('message', 'Erreur non décrite')}"
        + (f" (chemin : {error.get('path')})" if error.get("path") else "")
        for error in errors
        if isinstance(error, dict)
    ] or ["- Le workflow ne satisfait pas les validations."]
    codes = {error.get("code") for error in errors if isinstance(error, dict)}
    messages = " ".join(str(error.get("message", "")) for error in errors if isinstance(error, dict)).lower()
    targeted = []
    if "JSON_SCHEMA_ERROR" in codes and "messageflow" in messages.replace(" ", ""):
        targeted.append("Déplace l'élément messageFlow de nodes vers messageFlows.")
    if "ISOLATED_TASK" in codes:
        targeted.append("Connecte la tâche isolée si elle est métier; sinon supprime-la si elle est réellement inutile.")
    if "PARALLEL_GATEWAY_CONDITION" in codes:
        targeted.append("Supprime toutes les conditions des sorties de la parallelGateway.")
    targeted_text = "\n".join(targeted)
    return (
        "Le workflow JSON suivant est invalide. Corrige uniquement sa représentation JSON.\n\n"
        f"Erreurs détectées :\n{'\n'.join(error_lines)}\n\n"
        f"Workflow :\n{json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        f"JSON Schema officiel :\n{json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        "Conserve l'intention métier, les branches utiles, participants, pools, lanes, sequence flows et messageFlows.\n"
        "Corrige les références, événements, passerelles, conditions et timers nécessaires sans renommer les identifiants valides.\n"
        "Utilise source et target comme propriétés canoniques.\n"
        "Ne retourne jamais sourceRef ou targetRef.\n"
        "Types de nœuds autorisés : startEvent, endEvent, userTask, serviceTask, manualTask, scriptTask, businessRuleTask, exclusiveGateway, parallelGateway, inclusiveGateway, eventBasedGateway, subProcess, intermediateCatchEvent, intermediateThrowEvent.\n"
        "messageFlow doit uniquement apparaître dans messageFlows.\n"
        "flows contient uniquement les enchaînements internes à un même participant ou pool.\n"
        "Un sequence flow ne traverse jamais un pool; convertis toute communication inter-pools en messageFlow.\n"
        "Un messageFlow doit relier des participants ou pools différents.\n"
        f"{targeted_text}\n"
        "Retourne uniquement un objet JSON valide, sans Markdown, commentaire ou explication."
    )
