"""Unit tests for generated workflow normalization."""

from __future__ import annotations

import copy
import unittest

from src.workflow import WorkflowNormalizer


class WorkflowNormalizerTests(unittest.TestCase):
    """Cover aliases, references, timers, collaboration data and immutability."""

    def setUp(self) -> None:
        self.normalizer = WorkflowNormalizer()

    @staticmethod
    def workflow(**overrides) -> dict:
        value = {
            "process": {"id": "Demo", "name": "Demo", "description": "", "executable": False},
            "participants": [],
            "nodes": [],
            "flows": [],
            "ambiguities": [],
        }
        value.update(overrides)
        return value

    def test_source_and_target_aliases(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(
                nodes=[{"id": "A", "type": "start"}, {"id": "B", "type": "end"}],
                flows=[{"id": "Flow 1", "source": "A", "target": "B"}],
            )
        )
        self.assertEqual("a", result["flows"][0]["source"])
        self.assertEqual("b", result["flows"][0]["target"])
        self.assertNotIn("sourceRef", result["flows"][0])

    def test_all_requested_node_type_aliases(self) -> None:
        aliases = {
            "start": "startEvent", "start_event": "startEvent", "begin": "startEvent",
            "end": "endEvent", "end_event": "endEvent", "finish": "endEvent",
            "task": "userTask", "user_task": "userTask", "humanTask": "userTask",
            "service_task": "serviceTask", "automaticTask": "serviceTask",
            "exclusive_gateway": "exclusiveGateway", "decision": "exclusiveGateway", "xor": "exclusiveGateway",
            "parallel_gateway": "parallelGateway", "parallel": "parallelGateway", "and": "parallelGateway",
            "inclusive_gateway": "inclusiveGateway", "inclusive": "inclusiveGateway", "or": "inclusiveGateway",
            "subprocess": "subProcess", "sub_process": "subProcess",
        }
        nodes = [{"id": f"node {index}", "type": alias} for index, alias in enumerate(aliases)]
        result = self.normalizer.normalize(self.workflow(nodes=nodes))
        self.assertEqual(list(aliases.values()), [item["type"] for item in result["nodes"]])

    def test_identifiers_and_references_are_updated(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(
                participants=[{"id": "Team Manager!", "name": "Manager", "type": "human"}],
                nodes=[{"id": "Task Manager Approval!", "type": "task", "actor": "Team Manager!"}],
                flows=[{"id": "Go!", "from": "Task Manager Approval!", "to": "Task Manager Approval!"}],
            )
        )
        self.assertEqual("task_manager_approval", result["nodes"][0]["id"])
        self.assertEqual("team_manager", result["nodes"][0]["actor"])
        self.assertEqual("task_manager_approval", result["flows"][0]["source"])

    def test_duplicate_identifiers_become_unique(self) -> None:
        result, report = self.normalizer.normalize_with_report(
            self.workflow(nodes=[{"id": "Same", "type": "task"}, {"id": "Same", "type": "task"}])
        )
        self.assertEqual(["same", "same_2"], [item["id"] for item in result["nodes"]])
        self.assertIn("DUPLICATE_ID_RENAMED", {entry["code"] for entry in report})

    def test_timer_minutes_hours_and_days(self) -> None:
        nodes = [
            {"id": "m", "type": "timerEvent", "properties": {"value": "5 minutes"}},
            {"id": "h", "type": "timerEvent", "duration": "2 hours"},
            {"id": "d", "type": "timerEvent", "timerDuration": "3 days"},
        ]
        result = self.normalizer.normalize(self.workflow(nodes=nodes))
        self.assertEqual(["PT5M", "PT2H", "P3D"], [node["properties"]["value"] for node in result["nodes"]])

    def test_pools_are_normalized_as_participants(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(pools=[{"id": "Customer Pool", "name": "Customer", "type": "externalOrganization"}])
        )
        self.assertEqual("customer_pool", result["participants"][0]["id"])
        self.assertNotIn("pools", result)

    def test_lane_nodes_become_node_refs(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(
                nodes=[{"id": "Review Task", "type": "task"}],
                lanes=[{"id": "Review Lane", "nodes": ["Review Task"]}],
            )
        )
        self.assertEqual(["review_task"], result["lanes"][0]["nodeRefs"])
        self.assertNotIn("nodes", result["lanes"][0])

    def test_participant_members_become_normalized_node_refs(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(
                pools=[{"id": "Operations", "name": "Operations", "members": ["Review Task"]}],
                nodes=[{"id": "Review Task", "type": "task"}],
            )
        )
        self.assertEqual(["review_task"], result["participants"][0]["nodeRefs"])

    def test_message_flow_between_two_pools(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(
                pools=[{"id": "Buyer Pool", "name": "Buyer"}, {"id": "Seller Pool", "name": "Seller"}],
                messages=[{"id": "Request Message", "from": "Buyer Pool", "to": "Seller Pool"}],
            )
        )
        message = result["messageFlows"][0]
        self.assertEqual("buyer_pool", message["source"])
        self.assertEqual("seller_pool", message["target"])
        self.assertEqual("messageFlow", message["type"])

    def test_original_object_is_not_modified(self) -> None:
        original = self.workflow(
            nodes=[{"id": "My Task", "type": "task"}],
            flows=[{"id": "Loop", "source": "My Task", "target": "My Task"}],
        )
        snapshot = copy.deepcopy(original)
        self.normalizer.normalize(original)
        self.assertEqual(snapshot, original)

    def test_report_has_stable_shape_and_unknown_field_warning(self) -> None:
        _, report = self.normalizer.normalize_with_report(
            self.workflow(nodes=[{"id": "Start Here", "type": "begin", "unexpected": 42}])
        )
        self.assertTrue(report)
        self.assertTrue(all(set(entry) == {"code", "message", "path", "severity"} for entry in report))
        self.assertTrue(any(entry["code"] == "UNKNOWN_FIELD_REMOVED" for entry in report))

    def test_unparsed_timer_is_preserved_with_warning(self) -> None:
        result, report = self.normalizer.normalize_with_report(
            self.workflow(nodes=[{"id": "wait", "type": "timerEvent", "value": "next business day"}])
        )
        self.assertEqual("next business day", result["nodes"][0]["properties"]["value"])
        self.assertTrue(any(entry["code"] == "UNPARSED_TIMER" and entry["severity"] == "warning" for entry in report))

    def test_misplaced_message_flow_node_is_moved(self) -> None:
        result, report = self.normalizer.normalize_with_report(
            self.workflow(
                nodes=[
                    {"id": "task_client", "type": "serviceTask"},
                    {"id": "task_company", "type": "serviceTask"},
                    {"id": "message_1", "name": "Request", "type": "messageFlow", "source": "task_client", "target": "task_company"},
                ]
            )
        )
        self.assertNotIn("message_1", {item["id"] for item in result["nodes"]})
        self.assertEqual("task_client", result["messageFlows"][0]["source"])
        self.assertEqual("task_company", result["messageFlows"][0]["target"])
        self.assertEqual("Request", result["messageFlows"][0]["name"])
        self.assertIn("MISPLACED_MESSAGE_FLOW_MOVED", {entry["code"] for entry in report})

    def test_invalid_misplaced_message_flow_is_removed_with_warning(self) -> None:
        result, report = self.normalizer.normalize_with_report(
            self.workflow(nodes=[{"id": "message_1", "type": "message_flow"}])
        )
        self.assertEqual([], result["nodes"])
        self.assertNotIn("messageFlows", result)
        self.assertIn("INVALID_MESSAGE_FLOW_NODE_REMOVED", {entry["code"] for entry in report})

    def test_parallel_gateway_conditions_are_removed(self) -> None:
        result, report = self.normalizer.normalize_with_report(
            self.workflow(
                nodes=[{"id": "parallel", "type": "parallelGateway"}, {"id": "task", "type": "serviceTask"}],
                flows=[{"id": "f", "source": "parallel", "target": "task", "conditionExpression": "${x}"}],
            )
        )
        self.assertIsNone(result["flows"][0]["condition"])
        self.assertIn("PARALLEL_GATEWAY_CONDITION_REMOVED", {entry["code"] for entry in report})

    def test_exclusive_gateway_condition_is_preserved(self) -> None:
        result = self.normalizer.normalize(
            self.workflow(
                nodes=[{"id": "choice", "type": "exclusiveGateway"}, {"id": "task", "type": "serviceTask"}],
                flows=[{"id": "f", "source": "choice", "target": "task", "condition": "${x}"}],
            )
        )
        self.assertEqual("${x}", result["flows"][0]["condition"])

    def test_safe_repair_removes_only_unreferenced_task(self) -> None:
        repaired, report = self.normalizer.apply_safe_repairs(
            {"nodes": [{"id": "orphan", "type": "serviceTask"}], "flows": []}
        )
        self.assertEqual([], repaired["nodes"])
        self.assertEqual("REMOVED_ISOLATED_NODE", report[0]["code"])
        self.assertEqual("orphan", report[0]["node_id"])

    def test_safe_repair_keeps_task_referenced_by_message_flow(self) -> None:
        repaired, _ = self.normalizer.apply_safe_repairs(
            {
                "nodes": [{"id": "task", "type": "serviceTask"}],
                "flows": [],
                "messageFlows": [{"id": "m", "source": "task", "target": "pool"}],
            }
        )
        self.assertEqual("task", repaired["nodes"][0]["id"])

    def test_safe_repair_keeps_isolated_start_event(self) -> None:
        repaired, report = self.normalizer.apply_safe_repairs(
            {"nodes": [{"id": "start", "type": "startEvent"}], "flows": []}
        )
        self.assertEqual("start", repaired["nodes"][0]["id"])
        self.assertEqual([], report)


if __name__ == "__main__":
    unittest.main()
