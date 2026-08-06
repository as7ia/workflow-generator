"""Unit tests for bounded workflow repair orchestration."""

from __future__ import annotations

import copy
import unittest

from src.workflow import WorkflowRepairer


def valid_workflow() -> dict:
    """Return a small valid workflow independent of repository examples."""
    return {
        "schemaVersion": "1.0.0",
        "process": {"id": "process", "name": "Process", "description": "", "executable": False},
        "participants": [],
        "nodes": [
            {"id": "start", "type": "startEvent", "name": "Start", "actor": None, "properties": {}},
            {"id": "work", "type": "serviceTask", "name": "Work", "actor": "system", "properties": {}},
            {"id": "end", "type": "endEvent", "name": "End", "actor": None, "properties": {}},
        ],
        "flows": [
            {"id": "f1", "sourceRef": "start", "targetRef": "work", "condition": None, "isDefault": False},
            {"id": "f2", "sourceRef": "work", "targetRef": "end", "condition": None, "isDefault": False},
        ],
        "ambiguities": [],
    }


def invalid_workflow() -> dict:
    """Return a normalized-looking workflow with no end event."""
    workflow = valid_workflow()
    workflow["nodes"] = workflow["nodes"][:-1]
    workflow["flows"] = workflow["flows"][:-1]
    return workflow


class FakeGenerator:
    """Return queued values or raise queued exceptions."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, prompt: str):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class WorkflowRepairerTests(unittest.TestCase):
    """Exercise success, retry, failure isolation, prompts and reports."""

    def setUp(self) -> None:
        self.repairer = WorkflowRepairer()
        self.end_error = [{"code": "MISSING_END_EVENT", "message": "Fin absente", "node_id": None, "flow_id": None, "severity": "error"}]

    def test_workflow_repaired_on_first_attempt(self) -> None:
        generator = FakeGenerator(valid_workflow())
        repaired, report = self.repairer.repair(invalid_workflow(), self.end_error, generator)
        self.assertEqual("endEvent", repaired["nodes"][-1]["type"])
        self.assertEqual(1, len(report))
        self.assertEqual("success", report[0]["status"])

    def test_workflow_repaired_on_second_attempt(self) -> None:
        generator = FakeGenerator(invalid_workflow(), valid_workflow())
        _, report = self.repairer.repair(invalid_workflow(), self.end_error, generator)
        self.assertEqual(["failed", "success"], [attempt["status"] for attempt in report])
        self.assertEqual(2, len(generator.prompts))

    def test_stops_after_maximum_attempts(self) -> None:
        generator = FakeGenerator(invalid_workflow(), invalid_workflow(), invalid_workflow())
        repaired, report = self.repairer.repair(invalid_workflow(), self.end_error, generator)
        self.assertEqual(3, len(report))
        self.assertEqual(3, len(generator.prompts))
        self.assertIsInstance(repaired, dict)

    def test_generator_exception_does_not_crash_pipeline(self) -> None:
        generator = FakeGenerator(RuntimeError("offline"), RuntimeError("offline"), RuntimeError("offline"))
        _, report = self.repairer.repair(invalid_workflow(), self.end_error, generator)
        self.assertEqual(3, len(report))
        self.assertTrue(all(any(error["code"] == "GENERATOR_EXCEPTION" for error in item["output_errors"]) for item in report))

    def test_non_dictionary_generator_output_is_rejected(self) -> None:
        generator = FakeGenerator("not json", [], None)
        _, report = self.repairer.repair(invalid_workflow(), self.end_error, generator)
        self.assertEqual(3, len(report))
        self.assertTrue(all(any(error["code"] == "GENERATOR_INVALID_OUTPUT" for error in item["output_errors"]) for item in report))

    def test_original_workflow_is_preserved(self) -> None:
        original = invalid_workflow()
        snapshot = copy.deepcopy(original)
        self.repairer.repair(original, self.end_error, FakeGenerator(valid_workflow()))
        self.assertEqual(snapshot, original)

    def test_prompt_contains_json_only_rules_and_collaboration_terms(self) -> None:
        prompt = self.repairer.build_repair_prompt(invalid_workflow(), self.end_error)
        self.assertIn("Retourne uniquement un objet JSON valide", prompt)
        self.assertIn("sans Markdown", prompt)
        self.assertIn("Conserve l'intention métier", prompt)
        self.assertIn("pools, lanes, sequence flows et message flows", prompt)
        self.assertIn("MISSING_END_EVENT", prompt)

    def test_report_contains_every_attempt_and_required_fields(self) -> None:
        generator = FakeGenerator(invalid_workflow(), invalid_workflow(), valid_workflow())
        _, report = self.repairer.repair(invalid_workflow(), self.end_error, generator)
        self.assertEqual([1, 2, 3], [item["attempt"] for item in report])
        expected = {"attempt", "status", "input_errors", "output_errors", "message"}
        self.assertTrue(all(set(item) == expected for item in report))

    def test_initially_valid_workflow_stops_without_generator(self) -> None:
        generator = FakeGenerator()
        repaired, report = self.repairer.repair(valid_workflow(), [], generator)
        self.assertEqual([], report)
        self.assertEqual([], generator.prompts)
        self.assertEqual(valid_workflow(), repaired)

    def test_gateway_error_is_included_in_prompt(self) -> None:
        errors = [{"code": "GATEWAY_SPLIT_TOO_FEW_OUTPUTS", "message": "La passerelle gateway_decision possède une seule sortie.", "node_id": "gateway_decision", "severity": "error"}]
        prompt = self.repairer.build_repair_prompt(invalid_workflow(), errors)
        self.assertIn("gateway_decision possède une seule sortie", prompt)
        self.assertIn("Corrige les passerelles", prompt)
        self.assertIn("conditions manquantes", prompt)

    def test_invalid_reference_error_is_included_in_prompt(self) -> None:
        errors = [{"code": "INVALID_TARGET_REF", "message": "flow_4 pointe vers un nœud inexistant.", "flow_id": "flow_4", "severity": "error"}]
        prompt = self.repairer.build_repair_prompt(invalid_workflow(), errors)
        self.assertIn("flow_4 pointe vers un nœud inexistant", prompt)
        self.assertIn("Corrige les références invalides", prompt)


if __name__ == "__main__":
    unittest.main()
