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

from .prompts import build_generation_prompt


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
            generate_json = getattr(self.generator, "generate_json", None)
            if not callable(generate_json):
                raise TypeError(
                    "Le générateur doit exposer generate_json(prompt: str) -> dict."
                )
            raw = generate_json(prompt)
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
            normalized, safe_repair_report = self._apply_safe_repairs(
                normalized, []
            )
            result["normalized_workflow"] = normalized
            result["warnings"].extend(
                entry for entry in normalization_report if entry.get("severity") == "warning"
            )
            result["warnings"].extend(safe_repair_report)
        except Exception as exc:
            return self._fail(result, "normalization", exc)

        schema_errors = self._validate_schema(normalized, schema)
        semantic_errors = self._semantic_validator.validate(normalized)
        result["schema_errors"] = schema_errors
        result["semantic_errors"] = semantic_errors
        blocking = self._blocking_errors(schema_errors, semantic_errors)

        if blocking:
            try:
                current_workflow = copy.deepcopy(normalized)
                repairer = WorkflowRepairer(
                    max_attempts=self.max_repair_attempts
                )
                repaired_workflow, repair_report = repairer.repair(
                    workflow=current_workflow,
                    errors=blocking,
                    generator=self.generator,
                )
                repaired_workflow, repair_normalization_report = (
                    self._normalizer.normalize_with_report(repaired_workflow)
                )
                normalized = self._to_canonical_schema_flows(repaired_workflow)
                normalized, safe_repair_report = self._apply_safe_repairs(
                    normalized, blocking
                )
                result["normalized_workflow"] = normalized
                result["warnings"].extend(
                    entry
                    for entry in repair_normalization_report
                    if entry.get("severity") == "warning"
                )
                result["warnings"].extend(safe_repair_report)
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

    def _apply_safe_repairs(
        self,
        workflow: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Apply conservative deterministic fixes on a deep workflow copy."""
        del errors
        repaired = copy.deepcopy(workflow) if isinstance(workflow, Mapping) else {}
        report: list[dict[str, Any]] = []
        raw_nodes = repaired.get("nodes", [])
        nodes = raw_nodes if isinstance(raw_nodes, list) else []
        raw_messages = repaired.get("messageFlows", [])
        message_flows = raw_messages if isinstance(raw_messages, list) else []

        retained_nodes: list[Any] = []
        for index, node in enumerate(nodes):
            if not isinstance(node, Mapping):
                retained_nodes.append(node)
                continue
            node_type = str(node.get("type", "")).replace("_", "").lower()
            if node_type not in {"messageflow", "message"}:
                retained_nodes.append(node)
                continue
            source = node.get("source", node.get("sourceRef"))
            target = node.get("target", node.get("targetRef"))
            node_id = node.get("id")
            if source is not None and target is not None:
                moved = {
                    "id": node_id,
                    "source": source,
                    "target": target,
                    "condition": None,
                    "isDefault": False,
                    "type": "messageFlow",
                }
                if node.get("name"):
                    moved["name"] = node["name"]
                if isinstance(node.get("properties"), Mapping):
                    moved["properties"] = copy.deepcopy(dict(node["properties"]))
                message_flows.append(moved)
                report.append(
                    {
                        "code": "MISPLACED_MESSAGE_FLOW_MOVED",
                        "message": f"Le faux nœud {node_id} a été déplacé vers messageFlows.",
                        "node_id": str(node_id) if node_id is not None else None,
                        "severity": "warning",
                    }
                )
            else:
                report.append(
                    {
                        "code": "INVALID_MESSAGE_FLOW_NODE_REMOVED",
                        "message": f"Le faux nœud {node_id} a été supprimé car sa source ou sa cible est absente.",
                        "node_id": str(node_id) if node_id is not None else None,
                        "severity": "warning",
                    }
                )
        repaired["nodes"] = retained_nodes
        if message_flows or "messageFlows" in repaired:
            repaired["messageFlows"] = message_flows

        parallel_ids = {
            node.get("id")
            for node in retained_nodes
            if isinstance(node, Mapping) and node.get("type") == "parallelGateway"
        }
        raw_flows = repaired.get("flows", [])
        flows = raw_flows if isinstance(raw_flows, list) else []
        for index, flow in enumerate(flows):
            if not isinstance(flow, dict):
                continue
            source = flow.get("source", flow.get("sourceRef"))
            condition_keys = {
                "conditionExpression",
                "condition_expression",
                "expression",
            }
            had_condition = flow.get("condition") not in (None, "") or any(
                key in flow and flow.get(key) not in (None, "") for key in condition_keys
            )
            if source not in parallel_ids or not had_condition:
                continue
            flow["condition"] = None
            for key in condition_keys:
                flow.pop(key, None)
            report.append(
                {
                    "code": "PARALLEL_GATEWAY_CONDITION_REMOVED",
                    "message": "Condition supprimée d'un flux sortant d'une passerelle parallèle.",
                    "flow_id": str(flow.get("id")) if flow.get("id") is not None else None,
                    "severity": "warning",
                }
            )

        node_ids = {
            node.get("id")
            for node in retained_nodes
            if isinstance(node, Mapping) and node.get("id") is not None
        }
        valid_flows = []
        for flow in flows:
            if not isinstance(flow, Mapping):
                valid_flows.append(flow)
                continue
            source = flow.get("source", flow.get("sourceRef"))
            target = flow.get("target", flow.get("targetRef"))
            if source in node_ids and target in node_ids:
                valid_flows.append(flow)
                continue
            flow_id = str(flow.get("id")) if flow.get("id") is not None else None
            report.append(
                {
                    "code": "REMOVED_INVALID_FLOW",
                    "message": f"Le flux {flow_id} a été supprimé car sa source ou sa cible est absente ou inconnue.",
                    "flow_id": flow_id,
                    "severity": "warning",
                }
            )
        repaired["flows"] = valid_flows
        flows = valid_flows

        referenced: set[Any] = set()
        for collection in (flows, message_flows):
            for flow in collection:
                if isinstance(flow, Mapping):
                    referenced.update(
                        {
                            flow.get("source", flow.get("sourceRef")),
                            flow.get("target", flow.get("targetRef")),
                        }
                    )
        for participant in repaired.get("participants", []):
            if not isinstance(participant, Mapping):
                continue
            referenced.update(participant.get("nodeRefs", []))
            for lane in participant.get("lanes", []):
                if isinstance(lane, Mapping):
                    referenced.update(lane.get("nodeRefs", []))
        for lane in repaired.get("lanes", []):
            if isinstance(lane, Mapping):
                referenced.update(lane.get("nodeRefs", []))

        task_types = {
            "userTask",
            "serviceTask",
            "manualTask",
            "scriptTask",
            "businessRuleTask",
        }
        final_nodes = []
        for node in retained_nodes:
            if (
                isinstance(node, Mapping)
                and node.get("type") in task_types
                and node.get("id") not in referenced
            ):
                node_id = str(node.get("id"))
                report.append(
                    {
                        "code": "REMOVED_ISOLATED_NODE",
                        "message": f"Le nœud {node_id} a été supprimé car il était totalement isolé.",
                        "node_id": node_id,
                        "severity": "warning",
                    }
                )
                continue
            final_nodes.append(node)
        repaired["nodes"] = final_nodes
        return repaired, report

    @classmethod
    def _to_canonical_schema_flows(cls, workflow: dict[str, Any]) -> dict[str, Any]:
        """Convert every accepted legacy alias to canonical source/target."""
        result = copy.deepcopy(workflow)
        for collection in ("flows", "messageFlows"):
            raw_flows = result.get(collection, [])
            if not isinstance(raw_flows, list):
                continue
            for flow in raw_flows:
                if not isinstance(flow, dict):
                    continue
                if "source" not in flow:
                    for alias in ("sourceRef", "source_id", "from"):
                        if alias in flow:
                            flow["source"] = flow[alias]
                            break
                if "target" not in flow:
                    for alias in ("targetRef", "target_id", "to"):
                        if alias in flow:
                            flow["target"] = flow[alias]
                            break
                for alias in ("sourceRef", "source_id", "from"):
                    flow.pop(alias, None)
                for alias in ("targetRef", "target_id", "to"):
                    flow.pop(alias, None)
        return result

    @staticmethod
    def _fail(
        result: CompilationResult, stage: str, error: Exception | str
    ) -> CompilationResult:
        """Convert an exception into a notebook-safe structured failure."""
        result["success"] = False
        result["error"] = {"stage": stage, "message": str(error)}
        return result
