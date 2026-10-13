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

from .preparation import CanonicalWorkflowPreparer
from .coverage_validator import CoverageValidator


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

    def __init__(self, max_attempts: int | None = None, schema: dict[str, Any] | None = None) -> None:
        """Create a repairer with an optional non-negative attempt limit."""
        limit = self.MAX_ATTEMPTS if max_attempts is None else max_attempts
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("max_attempts doit Ãªtre un entier positif ou nul")
        self.max_attempts = limit
        self._schema = copy.deepcopy(schema) if isinstance(schema, dict) else self._load_effective_schema()
        self._preparer = CanonicalWorkflowPreparer(self._schema)

    def repair(
        self,
        workflow: dict[str, Any],
        errors: list[dict[str, Any]],
        generator: WorkflowGeneratorProtocol,
        description: str | None = None,
        requirements: dict[str, Any] | None = None,
        repair_kind: str = "structural",
        workflow_is_prepared: bool = False,
    ) -> tuple[dict[str, Any], list[RepairAttempt]]:
        """Return the best repaired workflow and a report for every attempt.

        Candidates are normalized before schema and semantic validation.  The
        original workflow and error list are never mutated.  A candidate is
        valid when it has no issue with severity ``error``; semantic warnings,
        such as intentional loops, do not trigger another repair.
        """
        original = copy.deepcopy(workflow) if isinstance(workflow, Mapping) else {}
        prepared = (self._preparer.validate_prepared(original) if workflow_is_prepared
                    else self._preparer.prepare_and_validate(original))
        current = prepared["workflow"]
        current_structural = prepared["errors"]
        current_coverage = CoverageValidator().validate(requirements or {},current)
        current_errors = [*current_structural,*current_coverage]
        report: list[RepairAttempt] = []

        if self._is_valid(current_errors):
            return current, report

        supplied_errors = copy.deepcopy(errors) if isinstance(errors, list) else []
        prompt_errors = self._prompt_errors(supplied_errors or current_errors)
        best = copy.deepcopy(current)
        best_score = self._score(current_errors)

        for attempt in range(1, self.max_attempts + 1):
            input_errors = copy.deepcopy(prompt_errors)
            prompt = self.build_repair_prompt(
                current, input_errors, description, requirements, repair_kind
            )
            try:
                generated = generator.generate_json(prompt)
            except Exception as exc:  # Generator implementations are external.
                failure = self._pipeline_error(
                    "GENERATOR_EXCEPTION", f"Le gÃ©nÃ©rateur a Ã©chouÃ© : {exc}"
                )
                report.append(
                    self._attempt(
                        attempt,
                        "failed",
                        input_errors,
                        [*copy.deepcopy(current_errors), failure],
                        "La tentative a Ã©chouÃ© pendant l'appel au gÃ©nÃ©rateur.",
                    )
                )
                prompt_errors = self._prompt_errors(current_errors)
                if self._is_resource_exhaustion(exc):
                    break
                continue

            if not isinstance(generated, Mapping):
                failure = self._pipeline_error(
                    "GENERATOR_INVALID_OUTPUT",
                    "Le gÃ©nÃ©rateur n'a pas retournÃ© un objet JSON.",
                )
                report.append(
                    self._attempt(
                        attempt,
                        "failed",
                        input_errors,
                        [*copy.deepcopy(current_errors), failure],
                        "La sortie du gÃ©nÃ©rateur a Ã©tÃ© ignorÃ©e car elle n'est pas un dictionnaire.",
                    )
                )
                prompt_errors = self._prompt_errors(current_errors)
                continue

            prepared = self._preparer.prepare_and_validate(copy.deepcopy(dict(generated)))
            candidate = prepared["workflow"]
            candidate_structural=prepared["errors"]
            candidate_coverage=CoverageValidator().validate(requirements or {},candidate)
            output_errors = [*candidate_structural,*candidate_coverage]
            candidate_score = self._score(output_errors)
            current_score = self._score(current_errors)
            structural_diagnostic=(self.structural_preservation_report(current,candidate,input_errors)
                                   if repair_kind=="structural" else None)
            accepted_candidate = (self._coverage_candidate_acceptable(current,candidate,current_structural,candidate_structural,current_coverage,candidate_coverage)
                                  if repair_kind=="coverage" else
                                  candidate_score<current_score and not structural_diagnostic["destructive_candidate"]
                                  and not self._introduces_grave_structural_errors(current_structural,candidate_structural))
            if accepted_candidate and candidate_score < best_score:
                best = copy.deepcopy(candidate)
                best_score = candidate_score

            coverage_diagnostic=self._coverage_diagnostic(current_structural,candidate_structural,current_coverage,candidate_coverage,accepted_candidate,candidate) if repair_kind=="coverage" else None
            if accepted_candidate and self._is_valid(output_errors):
                report.append(
                    self._attempt(
                        attempt,
                        "success",
                        input_errors,
                        output_errors,
                        "Le workflow corrigÃ© est valide.",
                        True,
                        self._error_count(current_errors),
                        coverage_diagnostic,
                        structural_diagnostic,
                    )
                )
                return candidate, report

            report.append(
                self._attempt(
                    attempt,
                    "failed",
                    input_errors,
                    output_errors,
                    "Le workflow corrigÃ© contient encore des erreurs.",
                    accepted_candidate,
                    self._error_count(current_errors),
                    coverage_diagnostic,
                    structural_diagnostic,
                )
            )
            if accepted_candidate:
                current = candidate
                current_structural=candidate_structural
                current_coverage=candidate_coverage
                current_errors = output_errors
            prompt_errors = self._prompt_errors(current_errors)

        return best, report

    def build_repair_prompt(
        self, workflow: dict[str, Any], errors: list[dict[str, Any]], description: str | None = None,
        requirements: dict[str, Any] | None = None,
        repair_kind: str = "structural",
    ) -> str:
        """Build a strict JSON-only repair prompt containing structured errors."""
        try:
            from ..generation.prompts import build_coverage_repair_prompt, build_repair_prompt
        except ImportError:  # pragma: no cover - PYTHONPATH=src entry point.
            from generation.prompts import build_coverage_repair_prompt, build_repair_prompt
        if repair_kind == "coverage":
            return build_coverage_repair_prompt(workflow, errors, requirements or {})
        return build_repair_prompt(workflow, self._prompt_errors(errors), self._schema)

    @staticmethod
    def _is_valid(errors: list[RepairError]) -> bool:
        """Return whether no blocking validation error remains."""
        return not any(error.get("severity", "error") == "error" for error in errors)

    @staticmethod
    def _score(errors: list[RepairError]) -> tuple[int, ...]:
        """Rank candidates using only generic blocking-error categories."""
        blocking=[error for error in errors if error.get("severity","error")=="error"]
        codes=[str(error.get("code","")) for error in blocking]
        count=lambda code:sum(value==code for value in codes)
        schema=sum(value.startswith("JSON_SCHEMA") for value in codes)
        unknown=sum(value.startswith("UNKNOWN_") for value in codes)
        tracked={"UNREACHABLE_NODE","ISOLATED_TASK","MISSING_INCOMING_FLOW",
                 "MISSING_OUTGOING_FLOW","NO_PATH_TO_END"}
        other=sum(value not in tracked and not value.startswith(("JSON_SCHEMA","UNKNOWN_")) for value in codes)
        return (len(blocking),count("UNREACHABLE_NODE"),count("ISOLATED_TASK"),
                count("MISSING_INCOMING_FLOW"),count("MISSING_OUTGOING_FLOW"),
                count("NO_PATH_TO_END"),schema,unknown,other)

    @staticmethod
    def _prompt_errors(errors: list[RepairError]) -> list[RepairError]:
        """Keep only unique blocking diagnostics for a compact Qwen prompt."""
        result=[];seen=set()
        for error in errors if isinstance(errors,list) else []:
            if not isinstance(error,Mapping) or error.get("severity","error")!="error":continue
            compact={key:copy.deepcopy(error[key]) for key in
                     ("code","message","path","process_id","participant_id","lane_id","node_id","flow_id","severity")
                     if key in error}
            normalized_message=" ".join(str(compact.get("message","")).lower().split())
            key=(compact.get("code"),compact.get("node_id"),compact.get("flow_id"),
                 compact.get("process_id"),normalized_message)
            if key in seen:continue
            seen.add(key);result.append(compact)
        return result

    @staticmethod
    def _is_resource_exhaustion(error: Exception) -> bool:
        """Identify CUDA OOM and compiler-wide generation-budget exhaustion."""
        if getattr(error, "generation_budget_exhausted", False):
            return True
        name = type(error).__name__.lower()
        message = str(error).lower()
        return ("outofmemory" in name or "out of memory" in message) and "cuda" in message

    @staticmethod
    def _attempt(
        attempt: int,
        status: str,
        input_errors: list[RepairError],
        output_errors: list[RepairError],
        message: str,
        accepted_candidate: bool = False,
        errors_before: int | None = None,
        coverage_repair: dict[str, Any] | None = None,
        structural_repair: dict[str, Any] | None = None,
    ) -> RepairAttempt:
        """Build one stable attempt report entry."""
        return {
            "attempt": attempt,
            "status": status,
            "input_errors": copy.deepcopy(input_errors),
            "output_errors": copy.deepcopy(output_errors),
            "message": message,
            "accepted_candidate": accepted_candidate,
            "errors_before": errors_before if errors_before is not None else WorkflowRepairer._error_count(input_errors),
            "errors_after": WorkflowRepairer._error_count(output_errors),
            **({"coverage_repair": copy.deepcopy(coverage_repair)} if coverage_repair is not None else {}),
            **(copy.deepcopy(structural_repair) if structural_repair is not None else {}),
        }

    @staticmethod
    def _error_count(errors: list[RepairError]) -> int:
        return sum(error.get("severity","error")=="error" for error in errors)

    @classmethod
    def structural_preservation_report(cls,original,candidate,errors=None):
        keys=("processes","participants","lanes","nodes","flows","messageFlows")
        def ids(workflow,key):
            value=workflow.get(key,[]) if isinstance(workflow,Mapping) else []
            return {item.get("id") for item in value if isinstance(item,Mapping) and item.get("id")}
        original_ids={key:ids(original,key) for key in keys};candidate_ids={key:ids(candidate,key) for key in keys}
        removed={key:sorted(original_ids[key]-candidate_ids[key]) for key in keys}
        counts=lambda workflow:{key:len(workflow.get(key,[])) if isinstance(workflow,Mapping) and isinstance(workflow.get(key),list) else 0 for key in keys}
        original_counts=counts(original);candidate_counts=counts(candidate)
        reason=None
        if original_counts["nodes"]>0 and candidate_counts["nodes"]==0:reason="Candidate removed all business nodes"
        elif original_counts["processes"]>0 and candidate_counts["processes"]==0:reason="Candidate removed all processes"
        elif original_counts["participants"]>0 and candidate_counts["participants"]==0:reason="Candidate removed all participants"
        elif original_counts["messageFlows"]>0 and candidate_counts["messageFlows"]==0:reason="Candidate removed all message flows"
        elif any(removed[key] for key in ("processes","participants","lanes","nodes","messageFlows")):
            key=next(key for key in ("processes","participants","lanes","nodes","messageFlows") if removed[key])
            reason=f"Candidate removed existing {key} IDs"
        elif original_counts["flows"]>0 and candidate_counts["flows"]==0:
            reason="Candidate removed all sequence flows"
        return {"original_counts":original_counts,"candidate_counts":candidate_counts,
                "removed_process_ids":removed["processes"],"removed_participant_ids":removed["participants"],
                "removed_lane_ids":removed["lanes"],"removed_node_ids":removed["nodes"],
                "removed_flow_ids":removed["flows"],"removed_message_flow_ids":removed["messageFlows"],
                "destructive_candidate":reason is not None,"rejection_reason":reason}

    @staticmethod
    def _introduces_grave_structural_errors(before,after):
        strict={"JSON_SCHEMA_ERROR","UNKNOWN_NODE_PROCESS","UNKNOWN_NODE_PARTICIPANT","UNKNOWN_NODE_LANE",
                "UNKNOWN_FLOW_PROCESS","UNKNOWN_MESSAGE_FLOW_SOURCE","UNKNOWN_MESSAGE_FLOW_TARGET"}
        contextual={"PROCESS_MISSING_END_EVENT","NO_PATH_TO_END"}
        for code in strict|contextual:
            previous=sum(item.get("code")==code and item.get("severity","error")=="error" for item in before)
            current=sum(item.get("code")==code and item.get("severity","error")=="error" for item in after)
            if (code in strict and current>previous) or (code in contextual and previous==0 and current>0):return True
        return False

    @classmethod
    def _coverage_candidate_acceptable(cls,current,candidate,current_structural,candidate_structural,current_coverage,candidate_coverage):
        if cls.is_catastrophic_regression(current,candidate,candidate_structural):return False
        for key in ("nodes","participants","processes","lanes"):
            before=current.get(key,[]) if isinstance(current.get(key,[]),list) else []
            after=candidate.get(key,[]) if isinstance(candidate.get(key,[]),list) else []
            if before and not after:return False
            if len(after)<len(before):return False
        if not cls._preserves_existing_ids(current,candidate):return False
        if cls._error_count(candidate_structural)>cls._error_count(current_structural):return False
        protected_unknown={"UNKNOWN_NODE_PARTICIPANT","UNKNOWN_NODE_LANE","UNKNOWN_NODE_PROCESS","UNKNOWN_FLOW_PROCESS"}
        for code in protected_unknown:
            before=sum(x.get("code")==code for x in current_structural if x.get("severity","error")=="error")
            after=sum(x.get("code")==code for x in candidate_structural if x.get("severity","error")=="error")
            if after>before:return False
        before_unknown=sum(str(x.get("code","")).startswith("UNKNOWN_") for x in current_structural)
        after_unknown=sum(str(x.get("code","")).startswith("UNKNOWN_") for x in candidate_structural)
        return after_unknown<=before_unknown and cls._error_count(candidate_coverage)<cls._error_count(current_coverage)

    @staticmethod
    def _preserves_existing_ids(current,candidate):
        """Coverage repair is additive: existing structural identities must survive."""
        for key in ("processes","participants","lanes","nodes","flows","messageFlows"):
            before=current.get(key,[]) if isinstance(current,Mapping) else []
            after=candidate.get(key,[]) if isinstance(candidate,Mapping) else []
            before_ids={item.get("id") for item in before if isinstance(item,Mapping) and item.get("id")}
            after_ids={item.get("id") for item in after if isinstance(item,Mapping) and item.get("id")}
            if not before_ids<=after_ids:return False
        return True

    @classmethod
    def is_catastrophic_regression(cls,before,candidate,candidate_errors):
        def count(workflow,key):
            value=workflow.get(key,[]) if isinstance(workflow,Mapping) else []
            return len(value) if isinstance(value,list) else 0
        if count(before,"nodes") and not count(candidate,"nodes"):return True
        if count(before,"participants") and not count(candidate,"participants"):return True
        if count(before,"processes")>1 and count(candidate,"processes")<=1:return True
        codes=[str(error.get("code","")) for error in candidate_errors if error.get("severity","error")=="error"]
        if "JSON_SCHEMA_ERROR" in codes:return True
        unknown={"UNKNOWN_NODE_PROCESS","UNKNOWN_MESSAGE_FLOW_SOURCE","UNKNOWN_MESSAGE_FLOW_TARGET"}
        return sum(code in unknown for code in codes)>=3

    @classmethod
    def _coverage_diagnostic(cls,before_structure,after_structure,before_coverage,after_coverage,accepted,candidate):
        return {"errors_before":cls._error_count(before_coverage),"errors_after":cls._error_count(after_coverage),
                "structure_errors_before":cls._error_count(before_structure),"structure_errors_after":cls._error_count(after_structure),
                "accepted_candidate":accepted,
                "candidate_counts":{key:len(candidate.get(key,[])) if isinstance(candidate.get(key),list) else 0
                                    for key in ("processes","participants","lanes","nodes","flows","messageFlows")}}

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
            self._add(errors, path, f"Valeur {instance!r} absente de l'Ã©numÃ©ration autorisÃ©e.")
        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                self._add(errors, path, "ChaÃ®ne trop courte.")
            if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
                self._add(errors, path, "ChaÃ®ne non conforme au motif attendu.")
        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                self._add(errors, path, "Liste contenant trop peu d'Ã©lÃ©ments.")
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
                    self._add(errors, self._join(path, key), f"Champ supplÃ©mentaire '{key}' interdit.")
            for key, child_schema in properties.items():
                if key in instance and isinstance(child_schema, Mapping):
                    self._visit(instance[key], child_schema, self._join(path, key), errors)
        for alternative_group in ("anyOf",):
            alternatives = schema.get(alternative_group)
            if alternatives and not any(self._passes(instance, alternative) for alternative in alternatives):
                self._add(errors, path, "Aucune variante de schÃ©ma autorisÃ©e ne correspond.")
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
