"""Defensive normalization of generated workflow JSON.

The normalizer repairs representation differences only.  It deliberately does
not validate graph semantics, generate BPMN, or infer missing business facts.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any


NormalizationEntry = dict[str, str]


class WorkflowNormalizer:
    """Normalize loosely generated workflow dictionaries into a stable form."""

    _NODE_TYPES = {
        "start": "startEvent",
        "start_event": "startEvent",
        "startevent": "startEvent",
        "begin": "startEvent",
        "end": "endEvent",
        "end_event": "endEvent",
        "endevent": "endEvent",
        "finish": "endEvent",
        "task": "userTask",
        "user_task": "userTask",
        "usertask": "userTask",
        "human_task": "userTask",
        "humantask": "userTask",
        "service_task": "serviceTask",
        "servicetask": "serviceTask",
        "automatic_task": "serviceTask",
        "automatictask": "serviceTask",
        "exclusive_gateway": "exclusiveGateway",
        "exclusivegateway": "exclusiveGateway",
        "decision": "exclusiveGateway",
        "xor": "exclusiveGateway",
        "parallel_gateway": "parallelGateway",
        "parallelgateway": "parallelGateway",
        "parallel": "parallelGateway",
        "and": "parallelGateway",
        "inclusive_gateway": "inclusiveGateway",
        "inclusivegateway": "inclusiveGateway",
        "inclusive": "inclusiveGateway",
        "or": "inclusiveGateway",
        "subprocess": "subProcess",
        "sub_process": "subProcess",
        "timer_event": "timerEvent",
        "timerevent": "timerEvent",
    }
    _NULL_ACTOR_TYPES = {
        "startEvent",
        "endEvent",
        "timerEvent",
        "exclusiveGateway",
        "parallelGateway",
        "inclusiveGateway",
    }
    _TOP_LEVEL_KEYS = {
        "schemaVersion",
        "process",
        "participants",
        "nodes",
        "flows",
        "ambiguities",
        # Forward-compatible collaboration extensions requested by the API.
        "lanes",
        "messageFlows",
    }
    _ALIASES = {
        "participants": ("participants", "pools"),
        "lanes": ("lanes", "lane", "swimlanes"),
        "messageFlows": ("messageFlows", "message_flows", "messages", "communications"),
    }
    _FLOW_REF_ALIASES = {
        "source": ("source", "sourceRef", "source_id", "from"),
        "target": ("target", "targetRef", "target_id", "to"),
    }
    _ALLOWED_FIELDS = {
        "process": {"id", "name", "description", "executable"},
        "participant": {"id", "name", "type", "lanes", "nodeRefs", "properties"},
        "lane": {"id", "name", "nodeRefs", "properties"},
        "node": {"id", "type", "name", "actor", "properties", "lanes", "nodeRefs"},
        "flow": {"id", "name", "source", "target", "condition", "isDefault", "type", "properties"},
        "ambiguity": {"id", "message", "relatedNode", "severity"},
    }
    _SCHEMA_1_EXTENSION_KEYS = {"lanes", "messageFlows", "sourceRef", "targetRef"}
    _TIMER_PATTERN = re.compile(
        r"^\s*(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|week|weeks)\s*$",
        re.IGNORECASE,
    )

    def normalize(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """Return a normalized deep copy of *workflow* without its report."""
        normalized, _ = self.normalize_with_report(workflow)
        return normalized

    def normalize_with_report(
        self, workflow: dict[str, Any]
    ) -> tuple[dict[str, Any], list[NormalizationEntry]]:
        """Return a normalized workflow and a detailed modification report."""
        report: list[NormalizationEntry] = []
        if not isinstance(workflow, Mapping):
            return {}, [self._entry("INVALID_WORKFLOW", "Le workflow doit être un objet.", "", "warning")]

        source = copy.deepcopy(dict(workflow))
        result: dict[str, Any] = {}
        used_ids: set[str] = set()

        result["schemaVersion"] = source.get("schemaVersion", "1.0.0")
        if "schemaVersion" not in source:
            self._report(report, "FIELD_DEFAULTED", "Version de schéma ajoutée.", "schemaVersion")

        result["process"] = self._normalize_process(source.get("process"), used_ids, report)

        raw_participants, participant_alias = self._aliased_value(source, "participants", [])
        if participant_alias and participant_alias != "participants":
            self._report_alias(report, participant_alias, "participants")
        participants, participant_ids = self._normalize_participants(
            raw_participants, used_ids, report
        )
        result["participants"] = participants

        raw_nodes, misplaced_messages = self._extract_misplaced_message_flows(
            source.get("nodes"), report
        )
        nodes, node_ids = self._normalize_nodes(raw_nodes, used_ids, report)
        self._normalize_node_actors(nodes, participant_ids, report)
        self._finalize_participant_lanes(participants, used_ids, node_ids, report)
        result["nodes"] = nodes

        result["flows"] = self._normalize_flows(
            source.get("flows"), "flows", used_ids, node_ids, participant_ids, report
        )

        raw_lanes, lane_alias = self._aliased_value(source, "lanes", None)
        if raw_lanes is not None:
            if lane_alias != "lanes":
                self._report_alias(report, str(lane_alias), "lanes")
            result["lanes"] = self._normalize_lanes(raw_lanes, used_ids, node_ids, report, "lanes")
            self._report_schema_extension(report, "lanes")

        raw_messages, message_alias = self._aliased_value(source, "messageFlows", None)
        if misplaced_messages:
            existing_messages = raw_messages if isinstance(raw_messages, list) else []
            raw_messages = [*existing_messages, *misplaced_messages]
            message_alias = message_alias or "messageFlows"
        if raw_messages is not None:
            if message_alias != "messageFlows":
                self._report_alias(report, str(message_alias), "messageFlows")
            result["messageFlows"] = self._normalize_flows(
                raw_messages,
                "messageFlows",
                used_ids,
                node_ids,
                participant_ids,
                report,
                default_type="messageFlow",
            )
            self._report_schema_extension(report, "messageFlows")

        self._remove_parallel_gateway_conditions(result["nodes"], result["flows"], report)

        result["ambiguities"] = self._normalize_ambiguities(
            source.get("ambiguities"), used_ids, node_ids, report
        )
        self._report_unknown_fields(source, self._source_top_level_keys(), "", report)
        return result, report

    def apply_safe_repairs(
        self, workflow: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return a copy with only context-proven isolated tasks removed."""
        result = copy.deepcopy(workflow) if isinstance(workflow, Mapping) else {}
        report: list[dict[str, Any]] = []
        self._remove_safe_isolated_tasks(result, report)
        return result, report

    def _extract_misplaced_message_flows(
        self, raw_nodes: Any, report: list[NormalizationEntry]
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Move communication elements incorrectly emitted inside ``nodes``."""
        if not isinstance(raw_nodes, list):
            return raw_nodes, []
        nodes: list[Any] = []
        messages: list[dict[str, Any]] = []
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, Mapping):
                nodes.append(raw_node)
                continue
            raw_type = raw_node.get("type")
            normalized_type = (
                re.sub(r"[\s-]+", "_", raw_type.strip()).lower()
                if isinstance(raw_type, str)
                else ""
            )
            if normalized_type not in {"messageflow", "message_flow", "message"}:
                nodes.append(raw_node)
                continue
            source, _ = self._first_value(raw_node, self._FLOW_REF_ALIASES["source"])
            target, _ = self._first_value(raw_node, self._FLOW_REF_ALIASES["target"])
            if source is not None and target is not None:
                messages.append(dict(raw_node))
                self._report(
                    report,
                    "MISPLACED_MESSAGE_FLOW_MOVED",
                    "Élément de communication déplacé de nodes vers messageFlows.",
                    f"nodes[{index}]",
                    "warning",
                )
            else:
                self._report(
                    report,
                    "INVALID_MESSAGE_FLOW_NODE_REMOVED",
                    "Faux nœud messageFlow supprimé car sa source ou sa cible est absente.",
                    f"nodes[{index}]",
                    "warning",
                )
        return nodes, messages

    def _remove_parallel_gateway_conditions(
        self,
        nodes: list[dict[str, Any]],
        flows: list[dict[str, Any]],
        report: list[NormalizationEntry],
    ) -> None:
        """Remove conditions from outgoing parallel-gateway sequence flows."""
        parallel_ids = {
            node["id"] for node in nodes if node.get("type") == "parallelGateway"
        }
        for index, flow in enumerate(flows):
            if flow.get("source") not in parallel_ids or flow.get("condition") in (None, ""):
                continue
            flow["condition"] = None
            self._report(
                report,
                "PARALLEL_GATEWAY_CONDITION_REMOVED",
                "Condition supprimée d'un flux sortant d'une passerelle parallèle.",
                f"flows[{index}].condition",
            )

    def _remove_safe_isolated_tasks(
        self, workflow: dict[str, Any], report: list[dict[str, Any]]
    ) -> None:
        """Remove only unreferenced isolated tasks, never events or business-linked tasks."""
        task_types = {"userTask", "serviceTask", "manualTask", "scriptTask", "businessRuleTask"}
        referenced: set[Any] = set()
        for collection in ("flows", "messageFlows"):
            for flow in workflow.get(collection, []):
                if isinstance(flow, Mapping):
                    referenced.update({flow.get("source"), flow.get("target")})
        for participant in workflow.get("participants", []):
            if not isinstance(participant, Mapping):
                continue
            referenced.update(participant.get("nodeRefs", []))
            for lane in participant.get("lanes", []):
                if isinstance(lane, Mapping):
                    referenced.update(lane.get("nodeRefs", []))
        for lane in workflow.get("lanes", []):
            if isinstance(lane, Mapping):
                referenced.update(lane.get("nodeRefs", []))

        retained = []
        for index, node in enumerate(workflow.get("nodes", [])):
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
            retained.append(node)
        workflow["nodes"] = retained

    def _normalize_process(
        self, raw: Any, used_ids: set[str], report: list[NormalizationEntry]
    ) -> dict[str, Any]:
        """Normalize the single process descriptor."""
        item = dict(raw) if isinstance(raw, Mapping) else {}
        if not isinstance(raw, Mapping):
            self._report(report, "FIELD_DEFAULTED", "Objet process créé.", "process")
        process_id = self._assign_id(item.get("id"), "process", used_ids, report, "process.id")
        result = {
            "id": process_id,
            "name": item.get("name") or self._name_from_id(process_id),
            "description": item.get("description", ""),
            "executable": item.get("executable", False),
        }
        self._report_unknown_fields(item, self._ALLOWED_FIELDS["process"], "process", report)
        return result

    def _normalize_participants(
        self, raw: Any, used_ids: set[str], report: list[NormalizationEntry]
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Normalize participants/pools and return their reference mapping."""
        result: list[dict[str, Any]] = []
        references: dict[str, str] = {}
        for index, item in self._mapping_items(raw, "participants", report):
            path = f"participants[{index}]"
            old_id = item.get("id")
            new_id = self._assign_id(old_id, "participant", used_ids, report, f"{path}.id")
            self._remember_reference(references, old_id, new_id)
            participant = {
                "id": new_id,
                "name": item.get("name") or self._name_from_id(new_id),
                "type": item.get("type", "human"),
            }
            if "type" not in item:
                self._report(report, "FIELD_DEFAULTED", "Type de participant générique ajouté.", f"{path}.type")
            properties = item.get("properties")
            if isinstance(properties, Mapping):
                participant["properties"] = copy.deepcopy(dict(properties))
            node_refs, node_refs_key = self._first_value(
                item, ("nodeRefs", "nodeIds", "nodes", "members")
            )
            if node_refs_key is not None:
                participant["_raw_node_refs"] = node_refs
                participant["_node_refs_alias"] = node_refs_key
            lane_raw, lane_alias = self._aliased_value(item, "lanes", None)
            if lane_raw is not None:
                if lane_alias != "lanes":
                    self._report_alias(report, f"{path}.{lane_alias}", f"{path}.lanes")
                participant["_raw_lanes"] = lane_raw
            result.append(participant)
            self._report_unknown_fields(item, self._participant_source_keys(), path, report)

        # Lane references depend on the complete node map and are finalized in
        # a second pass after nodes have been normalized.
        return result, references

    def _finalize_participant_lanes(
        self,
        participants: list[dict[str, Any]],
        used_ids: set[str],
        node_ids: dict[str, str],
        report: list[NormalizationEntry],
    ) -> None:
        """Normalize lanes nested in participants once node IDs are known."""
        for index, participant in enumerate(participants):
            raw_refs = participant.pop("_raw_node_refs", None)
            refs_alias = participant.pop("_node_refs_alias", None)
            if raw_refs is not None:
                refs_path = f"participants[{index}].nodeRefs"
                participant["nodeRefs"] = (
                    [self._resolve_reference(ref, node_ids) for ref in raw_refs]
                    if isinstance(raw_refs, list)
                    else []
                )
                if refs_alias != "nodeRefs":
                    self._report_alias(
                        report,
                        f"participants[{index}].{refs_alias}",
                        refs_path,
                    )
                self._report_schema_extension(report, refs_path)
            raw_lanes = participant.pop("_raw_lanes", None)
            if raw_lanes is None:
                continue
            path = f"participants[{index}].lanes"
            participant["lanes"] = self._normalize_lanes(
                raw_lanes, used_ids, node_ids, report, path
            )
            self._report_schema_extension(report, path)

    def _normalize_nodes(
        self, raw: Any, used_ids: set[str], report: list[NormalizationEntry]
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Normalize flow nodes and build old-to-new node references."""
        result: list[dict[str, Any]] = []
        references: dict[str, str] = {}
        for index, item in self._mapping_items(raw, "nodes", report):
            path = f"nodes[{index}]"
            old_id = item.get("id")
            node_type = self._normalize_node_type(item.get("type"), report, f"{path}.type")
            prefix = self._id_prefix(node_type)
            new_id = self._assign_id(old_id, prefix, used_ids, report, f"{path}.id")
            self._remember_reference(references, old_id, new_id)
            properties = copy.deepcopy(dict(item.get("properties", {}))) if isinstance(item.get("properties", {}), Mapping) else {}
            node = {
                "id": new_id,
                "type": node_type,
                "name": item.get("name") or self._name_from_id(new_id),
                "actor": item.get("actor"),
                "properties": properties,
            }
            if node_type == "serviceTask" and not node["actor"]:
                node["actor"] = "system"
                self._report(report, "ACTOR_DEFAULTED", "Acteur système ajouté à la tâche automatique.", f"{path}.actor")
            elif node_type in self._NULL_ACTOR_TYPES:
                node["actor"] = None
            self._normalize_timer(node, item, report, path)
            result.append(node)
            self._report_unknown_fields(item, self._ALLOWED_FIELDS["node"] | {"duration", "timerDuration", "value"}, path, report)
        return result, references

    def _normalize_node_actors(
        self,
        nodes: list[dict[str, Any]],
        participant_ids: dict[str, str],
        report: list[NormalizationEntry],
    ) -> None:
        """Update actor references without inventing a business participant."""
        for index, node in enumerate(nodes):
            actor = node.get("actor")
            if actor in participant_ids:
                normalized = participant_ids[str(actor)]
                if actor != normalized:
                    node["actor"] = normalized
                    self._report(report, "REFERENCE_UPDATED", "Référence d'acteur normalisée.", f"nodes[{index}].actor")

    def _normalize_flows(
        self,
        raw: Any,
        collection: str,
        used_ids: set[str],
        node_ids: dict[str, str],
        participant_ids: dict[str, str],
        report: list[NormalizationEntry],
        default_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize sequence or message flow objects."""
        result: list[dict[str, Any]] = []
        for index, item in self._mapping_items(raw, collection, report):
            path = f"{collection}[{index}]"
            flow_id = self._assign_id(item.get("id"), "flow", used_ids, report, f"{path}.id")
            source, source_key = self._first_value(item, self._FLOW_REF_ALIASES["source"])
            target, target_key = self._first_value(item, self._FLOW_REF_ALIASES["target"])
            normalized_source = self._resolve_reference(source, node_ids, participant_ids)
            normalized_target = self._resolve_reference(target, node_ids, participant_ids)
            condition = item.get("condition", item.get("conditionExpression"))
            flow: dict[str, Any] = {
                "id": flow_id,
                "source": normalized_source,
                "target": normalized_target,
                "condition": condition,
                "isDefault": bool(item.get("isDefault", False)),
            }
            flow_type = item.get("type", item.get("flowType", default_type))
            if flow_type is not None:
                flow["type"] = flow_type
            if item.get("name"):
                flow["name"] = item["name"]
            if isinstance(item.get("properties"), Mapping):
                flow["properties"] = copy.deepcopy(dict(item["properties"]))
            if source_key != "source":
                self._report_alias(report, f"{path}.{source_key}", f"{path}.source")
            if target_key != "target":
                self._report_alias(report, f"{path}.{target_key}", f"{path}.target")
            if "conditionExpression" in item and "condition" not in item:
                self._report_alias(report, f"{path}.conditionExpression", f"{path}.condition")
            if source != normalized_source:
                self._report(report, "REFERENCE_UPDATED", "Référence source normalisée.", f"{path}.source")
            if target != normalized_target:
                self._report(report, "REFERENCE_UPDATED", "Référence cible normalisée.", f"{path}.target")
            result.append(flow)
            self._report_unknown_fields(item, self._flow_source_keys(), path, report)
        return result

    def _normalize_lanes(
        self,
        raw: Any,
        used_ids: set[str],
        node_ids: dict[str, str],
        report: list[NormalizationEntry],
        base_path: str,
    ) -> list[dict[str, Any]]:
        """Normalize lane membership without applying layout semantics."""
        result: list[dict[str, Any]] = []
        for index, item in self._mapping_items(raw, base_path, report):
            path = f"{base_path}[{index}]"
            lane_id = self._assign_id(item.get("id"), "lane", used_ids, report, f"{path}.id")
            refs, refs_key = self._first_value(item, ("nodeRefs", "nodeIds", "nodes", "members"))
            normalized_refs = [self._resolve_reference(ref, node_ids) for ref in refs] if isinstance(refs, list) else []
            lane: dict[str, Any] = {
                "id": lane_id,
                "name": item.get("name") or self._name_from_id(lane_id),
                "nodeRefs": normalized_refs,
            }
            if isinstance(item.get("properties"), Mapping):
                lane["properties"] = copy.deepcopy(dict(item["properties"]))
            if refs_key != "nodeRefs":
                self._report_alias(report, f"{path}.{refs_key}", f"{path}.nodeRefs")
            result.append(lane)
            self._report_unknown_fields(item, self._lane_source_keys(), path, report)
        return result

    def _normalize_ambiguities(
        self, raw: Any, used_ids: set[str], node_ids: dict[str, str], report: list[NormalizationEntry]
    ) -> list[dict[str, Any]]:
        """Normalize ambiguity identifiers and related-node references."""
        result: list[dict[str, Any]] = []
        for index, item in self._mapping_items(raw, "ambiguities", report):
            path = f"ambiguities[{index}]"
            ambiguity = {key: copy.deepcopy(value) for key, value in item.items() if key in self._ALLOWED_FIELDS["ambiguity"]}
            ambiguity["id"] = self._assign_id(item.get("id"), "ambiguity", used_ids, report, f"{path}.id")
            if item.get("relatedNode") is not None:
                ambiguity["relatedNode"] = self._resolve_reference(item["relatedNode"], node_ids)
            result.append(ambiguity)
            self._report_unknown_fields(item, self._ALLOWED_FIELDS["ambiguity"], path, report)
        return result

    def _normalize_timer(
        self, node: dict[str, Any], raw_node: dict[str, Any], report: list[NormalizationEntry], path: str
    ) -> None:
        """Convert simple timer durations to ISO 8601 when possible."""
        if node.get("type") != "timerEvent":
            return
        properties = node["properties"]
        value = properties.get("value")
        source_path = f"{path}.properties.value"
        if value is None:
            for key in ("timerDuration", "duration", "value"):
                if key in raw_node:
                    value = raw_node[key]
                    source_path = f"{path}.{key}"
                    break
        if not isinstance(value, str):
            return
        iso_value = self._duration_to_iso(value)
        properties["value"] = iso_value
        if iso_value != value:
            self._report(report, "TIMER_NORMALIZED", f"Durée '{value}' convertie en '{iso_value}'.", source_path)
        elif not self._is_iso_duration(value):
            self._report(report, "UNPARSED_TIMER", f"Durée de timer non interprétée : '{value}'.", source_path, "warning")

    def _normalize_node_type(self, value: Any, report: list[NormalizationEntry], path: str) -> Any:
        """Map common generated type spellings to canonical BPMN names."""
        if not isinstance(value, str):
            return value
        key = re.sub(r"[\s-]+", "_", value.strip()).lower()
        normalized = self._NODE_TYPES.get(key, self._NODE_TYPES.get(key.replace("_", ""), value))
        if normalized != value:
            self._report(report, "NODE_TYPE_NORMALIZED", f"Type '{value}' converti en '{normalized}'.", path)
        return normalized

    def _assign_id(
        self,
        value: Any,
        prefix: str,
        used_ids: set[str],
        report: list[NormalizationEntry],
        path: str,
    ) -> str:
        """Create, sanitize and globally uniquify an identifier."""
        original = value
        base = self._sanitize_id(value) if value is not None else ""
        if not base:
            base = prefix
            self._report(report, "ID_GENERATED", f"Identifiant '{base}' créé.", path)
        candidate = base
        suffix = 2
        while candidate in used_ids:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_ids.add(candidate)
        if original is not None and str(original) != candidate:
            code = "DUPLICATE_ID_RENAMED" if candidate != base else "ID_NORMALIZED"
            self._report(report, code, f"Identifiant '{original}' converti en '{candidate}'.", path)
        return candidate

    @staticmethod
    def _sanitize_id(value: Any) -> str:
        """Return a lowercase ASCII identifier accepted by the current schema."""
        text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"\s+", "_", text.strip().lower())
        text = re.sub(r"[^a-z0-9_-]", "", text)
        text = re.sub(r"_+", "_", text).strip("_-")
        if text and not re.match(r"^[a-z_]", text):
            text = f"id_{text}"
        return text

    @staticmethod
    def _name_from_id(identifier: str) -> str:
        """Derive a readable neutral name from an identifier."""
        return identifier.replace("_", " ").strip().capitalize() or "Element"

    @staticmethod
    def _id_prefix(node_type: Any) -> str:
        """Choose a neutral generated-ID prefix from an element type."""
        prefixes = {
            "startEvent": "start_event",
            "endEvent": "end_event",
            "serviceTask": "service_task",
            "userTask": "user_task",
            "exclusiveGateway": "exclusive_gateway",
            "parallelGateway": "parallel_gateway",
            "inclusiveGateway": "inclusive_gateway",
            "subProcess": "sub_process",
            "timerEvent": "timer_event",
        }
        return prefixes.get(node_type, "node")

    @staticmethod
    def _remember_reference(references: dict[str, str], old_id: Any, new_id: str) -> None:
        """Map an original ID to its first occurrence, resolving ambiguity safely."""
        if old_id is not None:
            references.setdefault(str(old_id), new_id)
            references.setdefault(WorkflowNormalizer._sanitize_id(old_id), new_id)

    @staticmethod
    def _resolve_reference(value: Any, *reference_maps: dict[str, str]) -> Any:
        """Resolve a reference through ordered element namespaces."""
        if value is None:
            return None
        raw = str(value)
        sanitized = WorkflowNormalizer._sanitize_id(value)
        for references in reference_maps:
            if raw in references:
                return references[raw]
            if sanitized in references:
                return references[sanitized]
        return sanitized or value

    @classmethod
    def _duration_to_iso(cls, value: str) -> str:
        """Convert supported natural-language durations to ISO 8601."""
        match = cls._TIMER_PATTERN.match(value)
        if not match:
            return value
        amount, unit = int(match.group(1)), match.group(2).lower()
        if unit.startswith("min"):
            return f"PT{amount}M"
        if unit.startswith("hour") or unit in {"hr", "hrs"}:
            return f"PT{amount}H"
        if unit.startswith("day"):
            return f"P{amount}D"
        return f"P{amount * 7}D"

    @staticmethod
    def _is_iso_duration(value: str) -> bool:
        """Recognize the common ISO 8601 duration forms without fully parsing them."""
        return bool(re.fullmatch(r"P(?=\d|T\d)(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?", value, re.IGNORECASE))

    def _mapping_items(
        self, raw: Any, path: str, report: list[NormalizationEntry]
    ) -> list[tuple[int, dict[str, Any]]]:
        """Return mapping items from a list and report unusable values."""
        if raw is None:
            return []
        if not isinstance(raw, list):
            self._report(report, "INVALID_COLLECTION", "La valeur doit être une liste.", path, "warning")
            return []
        result: list[tuple[int, dict[str, Any]]] = []
        for index, item in enumerate(raw):
            if isinstance(item, Mapping):
                result.append((index, dict(item)))
            else:
                self._report(report, "INVALID_ELEMENT", "Élément non objet ignoré.", f"{path}[{index}]", "warning")
        return result

    @staticmethod
    def _first_value(item: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[Any, str | None]:
        """Return the first present alias and its key."""
        for key in keys:
            if key in item:
                return item[key], key
        return None, None

    def _aliased_value(
        self, item: Mapping[str, Any], canonical: str, default: Any
    ) -> tuple[Any, str | None]:
        """Read a canonical field or a useful supported alias.

        Generated payloads sometimes contain an empty canonical collection and
        a populated alias.  In that case the populated value carries more
        information and is selected without merging or duplicating elements.
        """
        aliases = self._ALIASES[canonical]
        if canonical in item and item[canonical] not in (None, [], {}):
            return item[canonical], canonical
        for key in aliases:
            if key != canonical and key in item and item[key] not in (None, [], {}):
                return item[key], key
        value, key = self._first_value(item, aliases)
        return (default, None) if key is None else (value, key)

    def _report_unknown_fields(
        self,
        item: Mapping[str, Any],
        allowed: set[str],
        path: str,
        report: list[NormalizationEntry],
    ) -> None:
        """Report every field omitted because the target contract disallows it."""
        for key in item.keys() - allowed:
            field_path = f"{path}.{key}" if path else key
            self._report(report, "UNKNOWN_FIELD_REMOVED", f"Champ non reconnu '{key}' retiré de la sortie normalisée.", field_path, "warning")

    def _report_schema_extension(self, report: list[NormalizationEntry], path: str) -> None:
        """Explain useful fields retained beyond the closed schema 1.0.0."""
        self._report(report, "SCHEMA_EXTENSION_PRESERVED", "Extension BPMN utile conservée malgré le schéma 1.0.0 fermé.", path, "warning")

    @classmethod
    def _source_top_level_keys(cls) -> set[str]:
        return cls._TOP_LEVEL_KEYS | {alias for aliases in cls._ALIASES.values() for alias in aliases}

    @classmethod
    def _participant_source_keys(cls) -> set[str]:
        return (
            cls._ALLOWED_FIELDS["participant"]
            | set(cls._ALIASES["lanes"])
            | {"nodeIds", "nodes", "members"}
        )

    @staticmethod
    def _lane_source_keys() -> set[str]:
        return {"id", "name", "nodeRefs", "nodeIds", "nodes", "members", "properties"}

    @classmethod
    def _flow_source_keys(cls) -> set[str]:
        return cls._ALLOWED_FIELDS["flow"] | {key for keys in cls._FLOW_REF_ALIASES.values() for key in keys} | {"conditionExpression", "flowType"}

    @staticmethod
    def _report_alias(report: list[NormalizationEntry], source: str, target: str) -> None:
        WorkflowNormalizer._report(report, "KEY_NORMALIZED", f"Clé '{source}' convertie en '{target}'.", target)

    @staticmethod
    def _report(
        report: list[NormalizationEntry], code: str, message: str, path: str, severity: str = "info"
    ) -> None:
        report.append(WorkflowNormalizer._entry(code, message, path, severity))

    @staticmethod
    def _entry(code: str, message: str, path: str, severity: str = "info") -> NormalizationEntry:
        """Build a stable normalization-report entry."""
        return {"code": code, "message": message, "path": path, "severity": severity}
