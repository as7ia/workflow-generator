"""End-to-end natural-language to analyzed BPMN workflow compilation."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from ..bpmn import BPMNGenerator, BPMNLayoutEngine
    from ..workflow import (
        SemanticValidator,
        CanonicalWorkflowPreparer,
        WorkflowGraphAnalyzer,
        WorkflowNormalizer,
        WorkflowRepairer,
    )
    from ..workflow.repair import _StandardSchemaValidator
except ImportError:  # pragma: no cover - supports PYTHONPATH=src notebooks.
    from bpmn import BPMNGenerator, BPMNLayoutEngine
    from workflow import (
        SemanticValidator,
        CanonicalWorkflowPreparer,
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
        self._bpmn_generator = BPMNGenerator()

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

        preparer = CanonicalWorkflowPreparer(schema)
        try:
            prepared = preparer.prepare(dict(raw))
            normalized = prepared["workflow"]
            result["normalized_workflow"] = normalized
            result["warnings"].extend(
                entry for entry in prepared["normalization_report"] if entry.get("severity") == "warning"
            )
            result["warnings"].extend(prepared["safe_repair_report"])
        except Exception as exc:
            return self._fail(result, "normalization", exc)

        schema_errors = prepared["schema_errors"]
        semantic_errors = prepared["semantic_errors"]
        result["schema_errors"] = schema_errors
        result["semantic_errors"] = semantic_errors
        blocking = self._blocking_errors(schema_errors, semantic_errors)

        if blocking:
            try:
                current_workflow = copy.deepcopy(normalized)
                repairer = WorkflowRepairer(
                    max_attempts=self.max_repair_attempts, schema=schema
                )
                repaired_workflow, repair_report = repairer.repair(
                    workflow=current_workflow,
                    errors=blocking,
                    generator=self.generator,
                )
                prepared = preparer.prepare(repaired_workflow)
                normalized = prepared["workflow"]
                result["normalized_workflow"] = normalized
                result["warnings"].extend(
                    entry
                    for entry in prepared["normalization_report"]
                    if entry.get("severity") == "warning"
                )
                result["warnings"].extend(prepared["safe_repair_report"])
                result["repair_report"] = repair_report
                result["schema_errors"] = prepared["schema_errors"]
                result["semantic_errors"] = prepared["semantic_errors"]
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

        try:
            bpmn_xml = self._bpmn_generator.generate(normalized, result["layout"])
            if not isinstance(bpmn_xml, str) or not bpmn_xml.strip():
                raise ValueError("Le générateur BPMN a retourné un document vide.")
            result["bpmn_xml"] = bpmn_xml
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
        """Apply canonical v2 safe repairs through the shared preparation policy."""
        return CanonicalWorkflowPreparer.apply_safe_repairs(workflow)
        """Legacy implementation retained below temporarily unreachable."""
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

        ownership = self._explicit_node_participants(repaired, node_ids)
        used_message_ids = {
            str(flow.get("id"))
            for flow in message_flows
            if isinstance(flow, Mapping) and flow.get("id") is not None
        }
        next_message_index = 1
        internal_flows: list[Any] = []
        for flow in flows:
            if not isinstance(flow, Mapping):
                internal_flows.append(flow)
                continue
            source = flow.get("source", flow.get("sourceRef"))
            target = flow.get("target", flow.get("targetRef"))
            source_owner = ownership.get(source)
            target_owner = ownership.get(target)
            if source_owner is None or target_owner is None or source_owner == target_owner:
                internal_flows.append(flow)
                continue

            original_id = str(flow.get("id")) if flow.get("id") is not None else ""
            message_id = original_id
            if not message_id or message_id in used_message_ids:
                while f"message_flow_{next_message_index}" in used_message_ids:
                    next_message_index += 1
                message_id = f"message_flow_{next_message_index}"
                next_message_index += 1
            used_message_ids.add(message_id)
            converted: dict[str, Any] = {
                "id": message_id,
                "source": source,
                "target": target,
                "type": "messageFlow",
            }
            if flow.get("name"):
                converted["name"] = flow["name"]
            if isinstance(flow.get("properties"), Mapping):
                converted["properties"] = copy.deepcopy(dict(flow["properties"]))
            message_flows.append(converted)
            report.append(
                {
                    "code": "SEQUENCE_FLOW_CONVERTED_TO_MESSAGE_FLOW",
                    "message": f"Le flux {original_id or message_id} traversait deux participants et a été converti en messageFlow.",
                    "flow_id": message_id,
                    "severity": "warning",
                }
            )

        repaired["flows"] = internal_flows
        flows = internal_flows
        for flow in message_flows:
            if not isinstance(flow, Mapping):
                continue
            source = flow.get("source", flow.get("sourceRef"))
            target = flow.get("target", flow.get("targetRef"))
            source_owner = ownership.get(source)
            target_owner = ownership.get(target)
            if source_owner is None or source_owner != target_owner:
                continue
            flow_id = str(flow.get("id")) if flow.get("id") is not None else None
            report.append(
                {
                    "code": "MESSAGE_FLOW_WITHIN_SAME_PARTICIPANT",
                    "message": f"Le message flow {flow_id} relie deux nœuds du même participant.",
                    "flow_id": flow_id,
                    "severity": "warning",
                }
            )
        if message_flows or "messageFlows" in repaired:
            repaired["messageFlows"] = message_flows

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

    @staticmethod
    def _explicit_node_participants(
        workflow: Mapping[str, Any], node_ids: set[Any]
    ) -> dict[Any, str]:
        """Build explicit node-to-pool ownership from nodeRefs and lanes."""
        ownership: dict[Any, str] = {}
        participants = workflow.get("participants", [])
        if not isinstance(participants, list):
            return ownership
        for participant in participants:
            if not isinstance(participant, Mapping) or not isinstance(participant.get("id"), str):
                continue
            participant_id = participant["id"]
            references = participant.get("nodeRefs", [])
            if isinstance(references, list):
                for node_id in references:
                    if node_id in node_ids:
                        ownership[node_id] = participant_id
            lanes = participant.get("lanes", [])
            if not isinstance(lanes, list):
                continue
            for lane in lanes:
                if not isinstance(lane, Mapping):
                    continue
                lane_references = lane.get("nodeRefs", [])
                if isinstance(lane_references, list):
                    for node_id in lane_references:
                        if node_id in node_ids:
                            ownership[node_id] = participant_id
        return ownership

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
