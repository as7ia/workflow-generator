"""Representation-only normalization into the canonical workflow model v2."""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any


NormalizationEntry = dict[str, str]


class WorkflowNormalizer:
    """Normalize v2 payloads and migrate supported v1 representations."""

    _NODE_TYPES = {
        "start": "startEvent", "start_event": "startEvent", "startevent": "startEvent", "begin": "startEvent",
        "end": "endEvent", "end_event": "endEvent", "endevent": "endEvent", "finish": "endEvent",
        "task": "userTask", "user_task": "userTask", "usertask": "userTask", "human_task": "userTask", "humantask": "userTask",
        "service_task": "serviceTask", "servicetask": "serviceTask", "automatic_task": "serviceTask", "automatictask": "serviceTask",
        "manual_task": "manualTask", "manualtask": "manualTask",
        "script_task": "scriptTask", "scripttask": "scriptTask",
        "business_rule_task": "businessRuleTask", "businessruletask": "businessRuleTask",
        "send_task": "sendTask", "sendtask": "sendTask",
        "receive_task": "receiveTask", "receivetask": "receiveTask",
        "exclusive_gateway": "exclusiveGateway", "exclusivegateway": "exclusiveGateway", "decision": "exclusiveGateway", "xor": "exclusiveGateway",
        "parallel_gateway": "parallelGateway", "parallelgateway": "parallelGateway", "parallel": "parallelGateway", "and": "parallelGateway",
        "inclusive_gateway": "inclusiveGateway", "inclusivegateway": "inclusiveGateway", "inclusive": "inclusiveGateway", "or": "inclusiveGateway",
        "event_based_gateway": "eventBasedGateway", "eventbasedgateway": "eventBasedGateway",
        "subprocess": "subProcess", "sub_process": "subProcess",
        "intermediate_catch_event": "intermediateCatchEvent", "intermediatecatchevent": "intermediateCatchEvent",
        "intermediate_throw_event": "intermediateThrowEvent", "intermediatethrowevent": "intermediateThrowEvent",
        "timer_event": "intermediateCatchEvent", "timerevent": "intermediateCatchEvent",
    }
    _FLOW_ALIASES = {
        "source": ("source", "sourceRef", "source_id", "from"),
        "target": ("target", "targetRef", "target_id", "to"),
    }
    _TIMER_PATTERN = re.compile(
        r"^\s*(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|week|weeks)\s*$",
        re.IGNORECASE,
    )

    def normalize(self, workflow: dict[str, Any]) -> dict[str, Any]:
        normalized, _ = self.normalize_with_report(workflow)
        return normalized

    def normalize_with_report(
        self, workflow: dict[str, Any]
    ) -> tuple[dict[str, Any], list[NormalizationEntry]]:
        report: list[NormalizationEntry] = []
        if not isinstance(workflow, Mapping):
            return {}, [self._entry("INVALID_WORKFLOW", "Le workflow doit être un objet.", "", "warning")]

        source = copy.deepcopy(dict(workflow))
        raw_version = source.get("schemaVersion")
        is_v2 = isinstance(source.get("processes"), list) or str(raw_version or "").startswith("2")
        supported_v1 = raw_version in (None, "", "1.0", "1.0.0") or str(raw_version).startswith("1.")
        supported_v2 = str(raw_version or "").startswith("2")
        output_version = "2.0" if supported_v2 or supported_v1 else raw_version
        if not is_v2 and not supported_v1:
            self._report(report, "UNSUPPORTED_SCHEMA_VERSION", f"Version de schéma non supportée : {raw_version}.", "schemaVersion", "warning")
        used_ids: set[str] = set()

        processes, process_ids = self._normalize_processes(source, used_ids, report)
        default_process = processes[0]["id"] if processes else None
        participants, participant_ids, participant_members, nested_lanes = self._normalize_participants(
            source, processes, process_ids, default_process, is_v2, used_ids, report
        )
        process_ids = {item["id"]: item["id"] for item in processes} | process_ids

        raw_lanes = source.get("lanes", source.get("swimlanes", []))
        lanes, lane_ids, lane_members = self._normalize_lanes(
            [*(raw_lanes if isinstance(raw_lanes, list) else []), *nested_lanes],
            process_ids,
            participant_ids,
            {item["id"]: item["processRef"] for item in participants},
            default_process,
            used_ids,
            report,
        )

        legacy_participant_for_node = self._membership_map(participant_members)
        legacy_lane_for_node = self._membership_map(lane_members)
        nodes, node_ids = self._normalize_nodes(
            source.get("nodes"),
            process_ids,
            participant_ids,
            lane_ids,
            {item["id"]: item for item in participants},
            {item["id"]: item for item in lanes},
            legacy_participant_for_node,
            legacy_lane_for_node,
            default_process,
            is_v2,
            used_ids,
            report,
        )
        node_processes = {item["id"]: item.get("processRef") for item in nodes}

        flows = self._normalize_flows(
            source.get("flows"), "flows", node_ids, process_ids, node_processes,
            default_process, is_v2, used_ids, report, message=False
        )
        raw_messages = source.get("messageFlows", source.get("message_flows", source.get("messages", [])))
        message_flows = self._normalize_flows(
            raw_messages, "messageFlows", node_ids, process_ids, node_processes,
            default_process, is_v2, used_ids, report, message=True
        )
        ambiguities = self._normalize_ambiguities(source.get("ambiguities"), node_ids, used_ids, report)

        allowed = {
            "schemaVersion", "process", "processes", "participants", "pools", "lanes", "swimlanes",
            "nodes", "flows", "messageFlows", "message_flows", "messages", "ambiguities",
        }
        self._unknown(source, allowed, "", report)
        normalized = {
            "schemaVersion": output_version,
            "processes": processes,
            "participants": participants,
            "lanes": lanes,
            "nodes": nodes,
            "flows": flows,
            "messageFlows": message_flows,
            "ambiguities": ambiguities,
        }
        self._assert_no_collection_loss(source,normalized)
        return normalized, report

    @staticmethod
    def _assert_no_collection_loss(source,normalized):
        """Reject complete disappearance of populated structural collections."""
        aliases={"processes":("processes",),"participants":("participants","pools"),
                 "nodes":("nodes",),"flows":("flows",)}
        for target,keys in aliases.items():
            raw=next((source.get(key) for key in keys if isinstance(source.get(key),list)),None)
            if isinstance(raw,list) and raw and not normalized.get(target):
                raise ValueError(f"La normalisation a supprimé tous les éléments de '{target}'.")

    def _normalize_processes(self, source, used_ids, report):
        raw = source.get("processes")
        if not isinstance(raw, list):
            raw = [source.get("process", {})]
            self._report(report, "V1_PROCESS_MIGRATED", "Clé process convertie en processes.", "processes")
        result, references = [], {}
        for index, item in self._items(raw, "processes", report):
            old_id = item.get("id")
            identifier = self._assign_id(old_id, "process", used_ids, report, f"processes[{index}].id")
            self._remember(references, old_id, identifier)
            result.append({
                "id": identifier,
                "name": item.get("name") or self._name(identifier),
                "description": str(item.get("description", "")),
                "executable": bool(item.get("executable", False)),
                "properties": self._properties(item),
            })
            self._unknown(item, {"id", "name", "description", "executable", "properties", "participantId"}, f"processes[{index}]", report)
        has_structure=any(isinstance(source.get(key),list) and source.get(key)
                          for key in ("nodes","flows","participants","pools","lanes"))
        if not result and has_structure:
            identifier = self._assign_id(None, "process", used_ids, report, "processes[0].id")
            result.append({"id": identifier, "name": self._name(identifier), "description": "", "executable": False, "properties": {}})
        return result, references

    def _normalize_participants(self, source, processes, process_ids, default_process, is_v2, used_ids, report):
        raw = source.get("participants")
        if not raw and isinstance(source.get("pools"), list):
            raw = source["pools"]
            self._report(report, "KEY_NORMALIZED", "Clé pools convertie en participants.", "participants")
        result, references, memberships, nested_lanes = [], {}, [], []
        items = self._items(raw, "participants", report)
        poolish = [bool(self._first(item, ("nodeRefs", "nodeIds", "nodes", "members"))[0] or item.get("lanes")) for _, item in items]
        pool_count = sum(poolish)
        pool_index = 0
        for position, (index, item) in enumerate(items):
            old_id = item.get("id")
            identifier = self._assign_id(old_id, "participant", used_ids, report, f"participants[{index}].id")
            self._remember(references, old_id, identifier)
            explicit_process = item.get("processRef")
            if explicit_process is not None:
                process_ref = self._resolve(explicit_process, process_ids)
            elif not is_v2 and pool_count > 1 and poolish[position]:
                if pool_index == 0:
                    process_ref = default_process
                else:
                    template = processes[0]
                    process_ref = self._assign_id(f"{default_process}_{identifier}", "process", used_ids, report, f"processes[{len(processes)}].id")
                    processes.append({**copy.deepcopy(template), "id": process_ref, "name": item.get("name") or self._name(process_ref)})
                pool_index += 1
            else:
                process_ref = default_process
            result.append({"id": identifier, "name": item.get("name") or self._name(identifier), "processRef": process_ref, "properties": self._properties(item)})
            refs, _ = self._first(item, ("nodeRefs", "nodeIds", "nodes", "members"))
            memberships.append((identifier, refs if isinstance(refs, list) else []))
            lanes = item.get("lanes")
            if isinstance(lanes, list):
                for lane in lanes:
                    if isinstance(lane, Mapping):
                        nested_lanes.append({**dict(lane), "processRef": process_ref, "participantRef": identifier})
            self._unknown(item, {"id", "name", "processRef", "properties", "type", "nodeRefs", "nodeIds", "nodes", "members", "lanes"}, f"participants[{index}]", report)
        return result, references, memberships, nested_lanes

    def _normalize_lanes(self, raw, process_ids, participant_ids, participant_processes, default_process, used_ids, report):
        result, references, memberships = [], {}, []
        for index, item in self._items(raw, "lanes", report):
            old_id = item.get("id")
            identifier = self._assign_id(old_id, "lane", used_ids, report, f"lanes[{index}].id")
            self._remember(references, old_id, identifier)
            participant_ref = self._optional_ref(item.get("participantRef"), participant_ids)
            explicit_process = item.get("processRef")
            process_ref = self._resolve(explicit_process, process_ids) if explicit_process is not None else participant_processes.get(participant_ref, default_process)
            result.append({
                "id": identifier, "name": item.get("name") or self._name(identifier),
                "processRef": process_ref, "participantRef": participant_ref,
                "properties": self._properties(item),
            })
            refs, _ = self._first(item, ("nodeRefs", "nodeIds", "nodes", "members"))
            memberships.append((identifier, refs if isinstance(refs, list) else []))
            self._unknown(item, {"id", "name", "processRef", "participantRef", "properties", "nodeRefs", "nodeIds", "nodes", "members"}, f"lanes[{index}]", report)
        return result, references, memberships

    def _normalize_nodes(self, raw, process_ids, participant_ids, lane_ids, participants, lanes, legacy_participants, legacy_lanes, default_process, is_v2, used_ids, report):
        prepared, references = [], {}
        for index, item in self._items(raw, "nodes", report):
            old_id = item.get("id")
            node_type, legacy_timer = self._node_type(item.get("type"), report, f"nodes[{index}].type")
            identifier = self._assign_id(old_id, self._prefix(node_type), used_ids, report, f"nodes[{index}].id")
            self._remember(references, old_id, identifier)
            prepared.append((index, item, identifier, node_type, legacy_timer))
        result = []
        for index, item, identifier, node_type, legacy_timer in prepared:
            participant_raw = item.get("participantRef", item.get("poolRef"))
            lane_raw = item.get("laneRef")
            participant_ref = self._optional_ref(participant_raw, participant_ids)
            lane_ref = self._optional_ref(lane_raw, lane_ids)
            if participant_raw is None and not is_v2:
                participant_ref = self._legacy_owner(item.get("id"), legacy_participants, participant_ids)
            if lane_raw is None and not is_v2:
                lane_ref = self._legacy_owner(item.get("id"), legacy_lanes, lane_ids)
            process_raw = item.get("processRef")
            if process_raw is not None:
                process_ref = self._resolve(process_raw, process_ids)
            elif not is_v2:
                process_ref = (lanes.get(lane_ref, {}).get("processRef") or participants.get(participant_ref, {}).get("processRef") or default_process)
            else:
                process_ref = None
            properties = self._properties(item)
            if legacy_timer:
                value = self._timer_value(item, properties)
                properties["timer"] = {"kind": "duration", "value": self._duration_to_iso(value)} if value is not None else {"kind": "duration", "value": None}
            result.append({
                "id": identifier, "type": node_type, "name": item.get("name") or self._name(identifier),
                "processRef": process_ref, "participantRef": participant_ref, "laneRef": lane_ref,
                "actor": item.get("actor"), "properties": properties,
            })
            self._unknown(item, {"id", "type", "name", "processRef", "participantRef", "poolRef", "laneRef", "actor", "properties", "duration", "timerDuration", "value"}, f"nodes[{index}]", report)
        return result, references

    def _normalize_flows(self, raw, collection, node_ids, process_ids, node_processes, default_process, is_v2, used_ids, report, message):
        result = []
        for index, item in self._items(raw, collection, report):
            identifier = self._assign_id(item.get("id"), "message_flow" if message else "flow", used_ids, report, f"{collection}[{index}].id")
            source, source_key = self._first(item, self._FLOW_ALIASES["source"])
            target, target_key = self._first(item, self._FLOW_ALIASES["target"])
            source = self._resolve(source, node_ids)
            target = self._resolve(target, node_ids)
            flow = {"id": identifier, "source": source, "target": target}
            if item.get("name") is not None:
                flow["name"] = str(item["name"])
            if message:
                flow["properties"] = self._properties(item)
            else:
                explicit = item.get("processRef")
                if explicit is not None:
                    process_ref = self._resolve(explicit, process_ids)
                elif not is_v2:
                    process_ref = node_processes.get(source) if node_processes.get(source) == node_processes.get(target) else default_process
                else:
                    process_ref = None
                flow.update({
                    "processRef": process_ref,
                    "condition": item.get("condition", item.get("conditionExpression")),
                    "isDefault": bool(item.get("isDefault", False)),
                    "properties": self._properties(item),
                })
            if source_key and source_key != "source":
                self._report(report, "KEY_NORMALIZED", f"Clé {source_key} convertie en source.", f"{collection}[{index}].source")
            if target_key and target_key != "target":
                self._report(report, "KEY_NORMALIZED", f"Clé {target_key} convertie en target.", f"{collection}[{index}].target")
            self._unknown(item, {"id", "name", "source", "sourceRef", "source_id", "from", "target", "targetRef", "target_id", "to", "processRef", "condition", "conditionExpression", "isDefault", "properties", "type", "flowType"}, f"{collection}[{index}]", report)
            result.append(flow)
        return result

    def _normalize_ambiguities(self, raw, node_ids, used_ids, report):
        result = []
        for index, item in self._items(raw, "ambiguities", report):
            identifier = self._assign_id(item.get("id"), "ambiguity", used_ids, report, f"ambiguities[{index}].id")
            related = item.get("relatedNode")
            result.append({
                "id": identifier, "message": str(item.get("message") or "Ambiguïté non décrite"),
                "relatedNode": self._resolve(related, node_ids) if related is not None else None,
                "severity": item.get("severity", "warning"),
            })
        return result

    @staticmethod
    def _membership_map(memberships):
        result = {}
        for owner, refs in memberships:
            for ref in refs:
                result.setdefault(str(ref), owner)
                result.setdefault(WorkflowNormalizer._sanitize_id(ref), owner)
        return result

    @staticmethod
    def _legacy_owner(node_id, membership, owner_ids):
        raw, sanitized = str(node_id), WorkflowNormalizer._sanitize_id(node_id)
        owner = membership.get(raw, membership.get(sanitized))
        return WorkflowNormalizer._resolve(owner, owner_ids) if owner is not None else None

    def _node_type(self, value, report, path):
        if not isinstance(value, str):
            return value, False
        key = re.sub(r"[\s-]+", "_", value.strip()).lower()
        legacy_timer = key in {"timer_event", "timerevent"}
        normalized = self._NODE_TYPES.get(key, self._NODE_TYPES.get(key.replace("_", ""), value))
        if normalized != value:
            self._report(report, "NODE_TYPE_NORMALIZED", f"Type {value} converti en {normalized}.", path)
        return normalized, legacy_timer

    @staticmethod
    def _properties(item):
        value = item.get("properties", {})
        return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}

    @staticmethod
    def _timer_value(item, properties):
        timer = properties.get("timer")
        if isinstance(timer, Mapping) and timer.get("value") is not None:
            return str(timer["value"])
        for key in ("value", "timerDuration", "duration"):
            if item.get(key) is not None:
                return str(item[key])
            if properties.get(key) is not None:
                return str(properties[key])
        return None

    @classmethod
    def _duration_to_iso(cls, value):
        if value is None:
            return None
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

    def _items(self, raw, path, report):
        if raw is None:
            return []
        if not isinstance(raw, list):
            self._report(report, "INVALID_COLLECTION", "La valeur doit être une liste.", path, "warning")
            return []
        result = []
        for index, item in enumerate(raw):
            if isinstance(item, Mapping):
                result.append((index, dict(item)))
            else:
                self._report(report, "INVALID_ELEMENT", "Élément non objet ignoré.", f"{path}[{index}]", "warning")
        return result

    def _assign_id(self, value, prefix, used_ids, report, path):
        original = value
        base = self._sanitize_id(value) if value is not None else ""
        if not base:
            base = prefix
            self._report(report, "ID_GENERATED", f"Identifiant {base} créé.", path)
        candidate, suffix = base, 2
        while candidate in used_ids:
            candidate, suffix = f"{base}_{suffix}", suffix + 1
        used_ids.add(candidate)
        if original is not None and str(original) != candidate:
            code = "DUPLICATE_ID_RENAMED" if candidate != base else "ID_NORMALIZED"
            self._report(report, code, f"Identifiant {original} converti en {candidate}.", path)
        return candidate

    @staticmethod
    def _sanitize_id(value):
        text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"\s+", "_", text.strip().lower())
        text = re.sub(r"[^a-z0-9_-]", "", text)
        text = re.sub(r"_+", "_", text).strip("_-")
        return f"id_{text}" if text and not re.match(r"^[a-z_]", text) else text

    @staticmethod
    def _remember(mapping, old, new):
        if old is not None:
            mapping.setdefault(str(old), new)
            mapping.setdefault(WorkflowNormalizer._sanitize_id(old), new)

    @staticmethod
    def _resolve(value, *mappings):
        if value is None:
            return None
        raw, sanitized = str(value), WorkflowNormalizer._sanitize_id(value)
        for mapping in mappings:
            if raw in mapping:
                return mapping[raw]
            if sanitized in mapping:
                return mapping[sanitized]
        return sanitized or value

    @classmethod
    def _optional_ref(cls, value, mapping):
        return None if value in (None, "") else cls._resolve(value, mapping)

    @staticmethod
    def _first(item, keys):
        for key in keys:
            if key in item:
                return item[key], key
        return None, None

    @staticmethod
    def _name(identifier):
        return identifier.replace("_", " ").strip().capitalize() or "Element"

    @staticmethod
    def _prefix(node_type):
        return {
            "startEvent": "start_event", "endEvent": "end_event", "userTask": "user_task",
            "serviceTask": "service_task", "manualTask": "manual_task", "scriptTask": "script_task",
            "businessRuleTask": "business_rule_task", "sendTask": "send_task", "receiveTask": "receive_task",
            "exclusiveGateway": "exclusive_gateway", "parallelGateway": "parallel_gateway",
            "inclusiveGateway": "inclusive_gateway", "eventBasedGateway": "event_based_gateway",
            "subProcess": "sub_process", "intermediateCatchEvent": "intermediate_catch_event",
            "intermediateThrowEvent": "intermediate_throw_event",
        }.get(node_type, "node")

    def _unknown(self, item, allowed, path, report):
        for key in item.keys() - allowed:
            field = f"{path}.{key}" if path else key
            self._report(report, "UNKNOWN_FIELD_REMOVED", f"Champ non reconnu {key} retiré.", field, "warning")

    @staticmethod
    def _report(report, code, message, path, severity="info"):
        report.append(WorkflowNormalizer._entry(code, message, path, severity))

    @staticmethod
    def _entry(code, message, path, severity="info"):
        return {"code": code, "message": message, "path": path, "severity": severity}
