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
        "Distingue les sequence flows internes d'un participant des messageFlows entre participants ou pools.\n"
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
    return (
        "Le workflow JSON suivant est invalide. Corrige uniquement sa représentation JSON.\n\n"
        f"Erreurs détectées :\n{'\n'.join(error_lines)}\n\n"
        f"Workflow :\n{json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        f"JSON Schema officiel :\n{json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)}\n\n"
        "Conserve l'intention métier, les branches utiles, participants, pools, lanes, sequence flows et messageFlows.\n"
        "Corrige les références, événements, passerelles, conditions et timers nécessaires sans renommer les identifiants valides.\n"
        "Utilise source et target comme propriétés canoniques.\n"
        "Retourne uniquement un objet JSON valide, sans Markdown, commentaire ou explication."
    )
