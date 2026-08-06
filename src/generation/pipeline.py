"""End-to-end natural-language to analyzed BPMN workflow compilation."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from ..bpmn import BPMNLayoutEngine
    from ..workflow import (
        SemanticValidator,
        WorkflowGraphAnalyzer,
        WorkflowNormalizer,
        WorkflowRepairer,
    )
    from ..workflow.repair import _StandardSchemaValidator
except ImportError:  # pragma: no cover - supports PYTHONPATH=src notebooks.
    from bpmn import BPMNLayoutEngine
    from workflow import (
        SemanticValidator,
        WorkflowGraphAnalyzer,
        WorkflowNormalizer,
        WorkflowRepairer,
    )
    from workflow.repair import _StandardSchemaValidator

from .prompts import build_generation_prompt, build_repair_prompt


CompilationResult = dict[str, Any]


class WorkflowCompiler:
    """Coordinate generation, normalization, repair, analysis and layout."""

    def __init__(
        self,
        generator: Any,
        schema_path: str | Path,
        max_repair_attempts: int = 3,
    ) -> None:
        self.generator = generator
        self.schema_path = Path(schema_path)
        self.max_repair_attempts = max_repair_attempts
        self._normalizer = WorkflowNormalizer()
        self._semantic_validator = SemanticValidator()
        self._analyzer = WorkflowGraphAnalyzer()
        self._layout_engine = BPMNLayoutEngine()

    def compile(self, description: str) -> CompilationResult:
        """Compile one natural-language description without leaking exceptions."""
        result = self._empty_result(description)
        try:
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise TypeError("Le JSON Schema doit être un objet.")
        except Exception as exc:
            return self._fail(result, "schema_validation", exc)

        try:
            prompt = build_generation_prompt(description, schema)
            raw = self.generator.generate_json(prompt)
            if not isinstance(raw, Mapping):
                raise TypeError("Le générateur doit retourner un dictionnaire JSON.")
            result["raw_workflow"] = copy.deepcopy(dict(raw))
        except Exception as exc:
            return self._fail(result, "generation", exc)

        try:
            normalized, normalization_report = self._normalizer.normalize_with_report(
                dict(raw)
            )
            normalized = self._to_canonical_schema_flows(normalized)
            result["normalized_workflow"] = normalized
            result["warnings"].extend(
                entry for entry in normalization_report if entry.get("severity") == "warning"
            )
        except Exception as exc:
            return self._fail(result, "normalization", exc)

        schema_errors = self._validate_schema(normalized, schema)
        semantic_errors = self._semantic_validator.validate(normalized)
        result["schema_errors"] = schema_errors
        result["semantic_errors"] = semantic_errors
        blocking = self._blocking_errors(schema_errors, semantic_errors)

        if blocking:
            try:
                repair_input = self._to_normalizer_flow_refs(normalized)
                repairer = WorkflowRepairer(
                    max_attempts=self.max_repair_attempts,
                    prompt_builder=lambda candidate, candidate_errors: build_repair_prompt(
                        self._to_canonical_schema_flows(candidate),
                        candidate_errors,
                        schema,
                    ),
                )
                repaired, repair_report = repairer.repair(
                    repair_input, blocking, self.generator
                )
                normalized = self._to_canonical_schema_flows(repaired)
                result["normalized_workflow"] = normalized
                result["repair_report"] = repair_report
                result["schema_errors"] = self._validate_schema(normalized, schema)
                result["semantic_errors"] = self._semantic_validator.validate(normalized)
                blocking = self._blocking_errors(
                    result["schema_errors"], result["semantic_errors"]
                )
            except Exception as exc:
                return self._fail(result, "repair", exc)
            if blocking:
                result["error"] = {
                    "stage": "repair",
                    "message": "Le meilleur workflow obtenu reste invalide après les tentatives de réparation.",
                }
                return result

        try:
            result["analysis"] = self._analyzer.analyze(normalized)
        except Exception as exc:
            return self._fail(result, "analysis", exc)

        try:
            result["layout"] = self._layout_engine.layout(
                normalized, result["analysis"]
            )
        except Exception as exc:
            return self._fail(result, "layout", exc)

        result["warnings"].extend(
            issue
            for issue in result["semantic_errors"]
            if issue.get("severity") == "warning"
        )
        result["warnings"].extend(result["analysis"].get("warnings", []))
        result["warnings"].extend(
            result["layout"].get("metadata", {}).get("warnings", [])
        )

        bpmn_builder = getattr(self.generator, "generate_bpmn_xml", None)
        if callable(bpmn_builder):
            try:
                result["bpmn_xml"] = bpmn_builder(normalized, result["layout"])
            except Exception as exc:
                return self._fail(result, "bpmn", exc)

        result["success"] = True
        return result

    def compile_and_save(self, description: str, output_dir: str) -> CompilationResult:
        """Compile and persist every available artifact in *output_dir*."""
        result = self.compile(description)
        try:
            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "raw_workflow.json": result["raw_workflow"],
                "normalized_workflow.json": result["normalized_workflow"],
                "analysis.json": result["analysis"],
                "layout.json": result["layout"],
                "repair_report.json": result["repair_report"],
            }
            for filename, value in artifacts.items():
                (destination / filename).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            if isinstance(result.get("bpmn_xml"), str):
                (destination / "workflow.bpmn").write_text(
                    result["bpmn_xml"], encoding="utf-8"
                )
            report_copy = copy.deepcopy(result)
            report_copy["bpmn_xml"] = None if result.get("bpmn_xml") else result.get("bpmn_xml")
            (destination / "compilation_report.json").write_text(
                json.dumps(report_copy, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            return self._fail(result, "bpmn", f"Échec de sauvegarde : {exc}")
        return result

    @staticmethod
    def _empty_result(description: str) -> CompilationResult:
        """Build the stable public result structure."""
        return {
            "success": False,
            "description": description,
            "raw_workflow": {},
            "normalized_workflow": {},
            "schema_errors": [],
            "semantic_errors": [],
            "repair_report": [],
            "analysis": {},
            "layout": {},
            "bpmn_xml": None,
            "warnings": [],
            "error": None,
        }

    @staticmethod
    def _validate_schema(
        workflow: dict[str, Any], schema: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Convert jsonschema errors to the pipeline's structured format."""
        internal_errors = _StandardSchemaValidator(schema).validate(workflow)
        errors = []
        for error in internal_errors:
            errors.append(
                {
                    "code": "JSON_SCHEMA_ERROR",
                    "message": error.get("message", "Erreur JSON Schema."),
                    "path": error.get("path", ""),
                    "node_id": None,
                    "flow_id": None,
                    "severity": "error",
                }
            )
        return errors

    @staticmethod
    def _blocking_errors(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(error)
            for group in groups
            for error in group
            if error.get("severity", "error") == "error"
        ]

    @classmethod
    def _to_canonical_schema_flows(cls, workflow: dict[str, Any]) -> dict[str, Any]:
        """Convert normalized references to schema-canonical source/target."""
        result = copy.deepcopy(workflow)
        for collection in ("flows", "messageFlows"):
            raw_flows = result.get(collection, [])
            if not isinstance(raw_flows, list):
                continue
            for flow in raw_flows:
                if not isinstance(flow, dict):
                    continue
                if "sourceRef" in flow:
                    flow["source"] = flow.pop("sourceRef")
                if "targetRef" in flow:
                    flow["target"] = flow.pop("targetRef")
        return result

    @classmethod
    def _to_normalizer_flow_refs(cls, workflow: dict[str, Any]) -> dict[str, Any]:
        """Convert canonical references to the repairer's normalized contract."""
        result = copy.deepcopy(workflow)
        for collection in ("flows", "messageFlows"):
            raw_flows = result.get(collection, [])
            if not isinstance(raw_flows, list):
                continue
            for flow in raw_flows:
                if not isinstance(flow, dict):
                    continue
                if "source" in flow:
                    flow["sourceRef"] = flow.pop("source")
                if "target" in flow:
                    flow["targetRef"] = flow.pop("target")
        return result

    @staticmethod
    def _fail(
        result: CompilationResult, stage: str, error: Exception | str
    ) -> CompilationResult:
        """Convert an exception into a notebook-safe structured failure."""
        result["success"] = False
        result["error"] = {"stage": stage, "message": str(error)}
        return result
