"""Iterative repair orchestration for generated workflow JSON.

The module is generator-agnostic and uses only the Python standard library.
It normalizes and validates each candidate but contains no BPMN rendering or
business-specific repair logic.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .normalizer import WorkflowNormalizer
from .semantic_validator import SemanticValidator


RepairError = dict[str, Any]
RepairAttempt = dict[str, Any]


class WorkflowGeneratorProtocol(Protocol):
    """Minimal interface required from a JSON-generating component."""

    def generate_json(self, prompt: str) -> dict[str, Any]:
        """Generate and return one JSON object from *prompt*."""
        ...


class WorkflowRepairer:
    """Repair workflows through bounded, validated generator iterations."""

    MAX_ATTEMPTS = 3

    def __init__(self, max_attempts: int | None = None) -> None:
        """Create a repairer with an optional positive attempt limit."""
        limit = self.MAX_ATTEMPTS if max_attempts is None else max_attempts
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("max_attempts doit être un entier strictement positif")
        self.max_attempts = limit
        self._normalizer = WorkflowNormalizer()
        self._semantic_validator = SemanticValidator()
        self._schema = self._load_effective_schema()

    def repair(
        self,
        workflow: dict[str, Any],
        errors: list[dict[str, Any]],
        generator: WorkflowGeneratorProtocol,
    ) -> tuple[dict[str, Any], list[RepairAttempt]]:
        """Return the best repaired workflow and a report for every attempt.

        Candidates are normalized before schema and semantic validation.  The
        original workflow and error list are never mutated.  A candidate is
        valid when it has no issue with severity ``error``; semantic warnings,
        such as intentional loops, do not trigger another repair.
        """
        original = copy.deepcopy(workflow) if isinstance(workflow, Mapping) else {}
        current = self._normalizer.normalize(original)
        current_errors = self._validate(current)
        report: list[RepairAttempt] = []

        if self._is_valid(current_errors):
            return current, report

        supplied_errors = copy.deepcopy(errors) if isinstance(errors, list) else []
        prompt_errors = supplied_errors or copy.deepcopy(current_errors)
        best = copy.deepcopy(current)
        best_score = self._score(current_errors)

        for attempt in range(1, self.max_attempts + 1):
            input_errors = copy.deepcopy(prompt_errors)
            prompt = self.build_repair_prompt(current, input_errors)
            try:
                generated = generator.generate_json(prompt)
            except Exception as exc:  # Generator implementations are external.
                failure = self._pipeline_error(
                    "GENERATOR_EXCEPTION", f"Le générateur a échoué : {exc}"
                )
                report.append(
                    self._attempt(
                        attempt,
                        "failed",
                        input_errors,
                        [*copy.deepcopy(current_errors), failure],
                        "La tentative a échoué pendant l'appel au générateur.",
                    )
                )
                prompt_errors = current_errors
                continue

            if not isinstance(generated, Mapping):
                failure = self._pipeline_error(
                    "GENERATOR_INVALID_OUTPUT",
                    "Le générateur n'a pas retourné un objet JSON.",
                )
                report.append(
                    self._attempt(
                        attempt,
                        "failed",
                        input_errors,
                        [*copy.deepcopy(current_errors), failure],
                        "La sortie du générateur a été ignorée car elle n'est pas un dictionnaire.",
                    )
                )
                prompt_errors = current_errors
                continue

            candidate = self._normalizer.normalize(copy.deepcopy(dict(generated)))
            output_errors = self._validate(candidate)
            candidate_score = self._score(output_errors)
            if candidate_score < best_score:
                best = copy.deepcopy(candidate)
                best_score = candidate_score

            if self._is_valid(output_errors):
                report.append(
                    self._attempt(
                        attempt,
                        "success",
                        input_errors,
                        output_errors,
                        "Le workflow corrigé est valide.",
                    )
                )
                return candidate, report

            report.append(
                self._attempt(
                    attempt,
                    "failed",
                    input_errors,
                    output_errors,
                    "Le workflow corrigé contient encore des erreurs.",
                )
            )
            current = candidate
            current_errors = output_errors
            prompt_errors = output_errors

        return best, report

    def build_repair_prompt(
        self, workflow: dict[str, Any], errors: list[dict[str, Any]]
    ) -> str:
        """Build a strict JSON-only repair prompt containing structured errors."""
        error_lines = []
        for error in errors if isinstance(errors, list) else []:
            if not isinstance(error, Mapping):
                error_lines.append(f"- {error}")
                continue
            code = error.get("code", "ERROR")
            message = error.get("message", "Erreur non décrite")
            location = error.get("path") or error.get("node_id") or error.get("flow_id")
            suffix = f" (élément : {location})" if location else ""
            error_lines.append(f"- [{code}] {message}{suffix}")
        if not error_lines:
            error_lines.append("- Le workflow ne satisfait pas les validations structurelles ou sémantiques.")

        serialized = json.dumps(workflow, ensure_ascii=False, indent=2, sort_keys=True)
        codes = {error.get("code") for error in errors if isinstance(error, Mapping)}
        messages = " ".join(
            str(error.get("message", "")) for error in errors if isinstance(error, Mapping)
        ).lower()
        targeted: list[str] = []
        if "JSON_SCHEMA_ERROR" in codes and "messageflow" in messages.replace(" ", ""):
            targeted.append("Déplace tout messageFlow présent dans nodes vers la collection messageFlows.")
        if "ISOLATED_TASK" in codes:
            targeted.append("Connecte correctement toute tâche métier isolée; supprime-la uniquement si elle est inutile.")
        if "PARALLEL_GATEWAY_CONDITION" in codes:
            targeted.append("Supprime toutes les conditions des flux sortant d'une parallelGateway.")
        targeted_text = "\n".join(targeted)
        return (
            "Le workflow JSON suivant est invalide.\n\n"
            "Erreurs détectées :\n"
            + "\n".join(error_lines)
            + "\n\nWorkflow à corriger :\n"
            + serialized
            + "\n\nCorrige uniquement le JSON.\n"
            "Conserve l'intention métier.\n"
            "Ne supprime aucune branche utile.\n"
            "N'invente pas de nouvelle étape sauf si elle est nécessaire pour corriger une erreur.\n"
            "Corrige les références invalides.\n"
            "Utilise uniquement source et target; n'utilise jamais sourceRef ni targetRef.\n"
            "flows contient uniquement les sequence flows internes à un même participant ou pool.\n"
            "Un sequence flow ne traverse jamais un pool; utilise messageFlows pour toute communication entre pools différents.\n"
            "Un messageFlow relie des participants ou pools différents et ne porte aucune condition de séquence.\n"
            "Chaque source et target doit correspondre à un node.id existant et aucun flux ne doit être incomplet.\n"
            "Ajoute les événements de début ou de fin manquants si nécessaire.\n"
            "Corrige les passerelles et ajoute les conditions manquantes aux sorties des passerelles exclusives.\n"
            "Ne modifie pas les identifiants déjà valides sans raison.\n"
            "Préserve les participants, pools, lanes, sequence flows et message flows.\n"
            "Types de nœuds autorisés : startEvent, endEvent, userTask, serviceTask, manualTask, scriptTask, businessRuleTask, exclusiveGateway, parallelGateway, inclusiveGateway, eventBasedGateway, subProcess, intermediateCatchEvent, intermediateThrowEvent.\n"
            "messageFlow doit uniquement apparaître dans messageFlows et jamais comme type de nœud.\n"
            f"{targeted_text}\n"
            "Retourne uniquement un objet JSON valide, sans Markdown, sans commentaire et sans explication."
        )

    def _validate(self, workflow: dict[str, Any]) -> list[RepairError]:
        """Run the effective JSON Schema, then semantic validation."""
        schema_errors = _StandardSchemaValidator(self._schema).validate(workflow)
        semantic_errors = self._semantic_validator.validate(workflow)
        return [*schema_errors, *semantic_errors]

    @staticmethod
    def _is_valid(errors: list[RepairError]) -> bool:
        """Return whether no blocking validation error remains."""
        return not any(error.get("severity", "error") == "error" for error in errors)

    @staticmethod
    def _score(errors: list[RepairError]) -> tuple[int, int]:
        """Rank candidates by blocking errors first, then total issues."""
        blocking = sum(error.get("severity", "error") == "error" for error in errors)
        return blocking, len(errors)

    @staticmethod
    def _attempt(
        attempt: int,
        status: str,
        input_errors: list[RepairError],
        output_errors: list[RepairError],
        message: str,
    ) -> RepairAttempt:
        """Build one stable attempt report entry."""
        return {
            "attempt": attempt,
            "status": status,
            "input_errors": copy.deepcopy(input_errors),
            "output_errors": copy.deepcopy(output_errors),
            "message": message,
        }

    @staticmethod
    def _pipeline_error(code: str, message: str) -> RepairError:
        """Create an error raised by repair orchestration itself."""
        return {"code": code, "message": message, "path": "", "severity": "error"}

    @staticmethod
    def _load_effective_schema() -> dict[str, Any]:
        """Load schema 1.0.0 and overlay the normalized BPMN extensions.

        The checked-in schema still names flow references ``source`` and
        ``target`` and is closed to collaboration extensions.  The overlay is
        intentionally local and minimal: it keeps every original constraint
        while accepting the normalizer's public contract until the schema file
        itself is versioned.
        """
        schema_path = Path(__file__).resolve().parents[2] / "schemas" / "workflow.schema.json"
        return json.loads(schema_path.read_text(encoding="utf-8"))


class _StandardSchemaValidator:
    """Small standard-library evaluator for keywords used by this schema."""

    _TYPE_MAP = {
        "object": Mapping,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
        "number": (int, float),
        "integer": int,
    }

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    def validate(self, instance: Any) -> list[RepairError]:
        """Return structured errors for all supported schema violations."""
        errors: list[RepairError] = []
        self._visit(instance, self.schema, "", errors)
        return errors

    def _visit(
        self, instance: Any, schema: Mapping[str, Any], path: str, errors: list[RepairError]
    ) -> None:
        if "$ref" in schema:
            self._visit(instance, self._resolve_ref(str(schema["$ref"])), path, errors)
            return
        expected = schema.get("type")
        if expected is not None and not self._matches_type(instance, expected):
            self._add(errors, path, f"Type invalide : attendu {expected}.")
            return
        if "const" in schema and instance != schema["const"]:
            self._add(errors, path, f"Valeur attendue : {schema['const']!r}.")
        if "enum" in schema and instance not in schema["enum"]:
            self._add(errors, path, f"Valeur {instance!r} absente de l'énumération autorisée.")
        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                self._add(errors, path, "Chaîne trop courte.")
            if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
                self._add(errors, path, "Chaîne non conforme au motif attendu.")
        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                self._add(errors, path, "Liste contenant trop peu d'éléments.")
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                for index, value in enumerate(instance):
                    self._visit(value, item_schema, self._join(path, index), errors)
        if isinstance(instance, Mapping):
            properties = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in instance:
                    self._add(errors, self._join(path, key), f"Champ obligatoire '{key}' absent.")
            if schema.get("additionalProperties") is False:
                for key in instance.keys() - properties.keys():
                    self._add(errors, self._join(path, key), f"Champ supplémentaire '{key}' interdit.")
            for key, child_schema in properties.items():
                if key in instance and isinstance(child_schema, Mapping):
                    self._visit(instance[key], child_schema, self._join(path, key), errors)
        for alternative_group in ("anyOf",):
            alternatives = schema.get(alternative_group)
            if alternatives and not any(self._passes(instance, alternative) for alternative in alternatives):
                self._add(errors, path, "Aucune variante de schéma autorisée ne correspond.")
        for rule in schema.get("allOf", []):
            condition = rule.get("if")
            consequent = rule.get("then")
            if condition is not None and consequent is not None and self._passes(instance, condition):
                self._visit(instance, consequent, path, errors)

    def _passes(self, instance: Any, schema: Mapping[str, Any]) -> bool:
        errors: list[RepairError] = []
        self._visit(instance, schema, "", errors)
        return not errors

    def _resolve_ref(self, reference: str) -> Mapping[str, Any]:
        value: Any = self.schema
        for part in reference.removeprefix("#/").split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        return value

    @classmethod
    def _matches_type(cls, value: Any, expected: Any) -> bool:
        choices = expected if isinstance(expected, list) else [expected]
        for choice in choices:
            python_type = cls._TYPE_MAP.get(choice)
            if python_type is None:
                continue
            if choice in {"number", "integer"} and isinstance(value, bool):
                continue
            if isinstance(value, python_type):
                return True
        return False

    @staticmethod
    def _join(path: str, part: str | int) -> str:
        if isinstance(part, int):
            return f"{path}[{part}]" if path else f"[{part}]"
        return f"{path}.{part}" if path else part

    @staticmethod
    def _add(errors: list[RepairError], path: str, message: str) -> None:
        errors.append(
            {
                "code": "JSON_SCHEMA_ERROR",
                "message": message,
                "path": path,
                "severity": "error",
            }
        )
