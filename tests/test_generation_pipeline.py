"""Integration tests for the natural-language workflow compiler."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.generation import WorkflowCompiler, extract_json_object
from src.generation.prompts import build_generation_prompt, build_repair_prompt
from src.workflow import WorkflowNormalizer


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "workflow.schema.json"


def valid_workflow(**overrides) -> dict:
    """Build a schema-valid linear workflow using canonical flow references."""
    value = {
        "schemaVersion": "1.0.0",
        "process": {"id": "process", "name": "Process", "description": "", "executable": False},
        "participants": [],
        "nodes": [
            {"id": "start", "type": "startEvent", "name": "Start", "actor": None, "properties": {}},
            {"id": "work", "type": "serviceTask", "name": "Work", "actor": "system", "properties": {}},
            {"id": "end", "type": "endEvent", "name": "End", "actor": None, "properties": {}},
        ],
        "flows": [
            {"id": "f1", "source": "start", "target": "work", "condition": None, "isDefault": False},
            {"id": "f2", "source": "work", "target": "end", "condition": None, "isDefault": False},
        ],
        "ambiguities": [],
    }
    value.update(overrides)
    return value


def invalid_without_end() -> dict:
    value = valid_workflow()
    value["nodes"] = value["nodes"][:-1]
    value["flows"] = value["flows"][:-1]
    return value


class FakeGenerator:
    """Return queued responses and retain every prompt."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, prompt: str):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class WorkflowCompilerTests(unittest.TestCase):
    """Cover successful, repaired, failed and collaboration compilations."""

    def compile_with(self, *responses, description: str = "Traiter une demande") -> dict:
        return WorkflowCompiler(FakeGenerator(*responses), SCHEMA_PATH).compile(description)

    def test_valid_generation_succeeds_first_time(self) -> None:
        generator = FakeGenerator(valid_workflow())
        result = WorkflowCompiler(generator, SCHEMA_PATH).compile("Processus linéaire")
        self.assertTrue(result["success"])
        self.assertEqual(1, len(generator.prompts))
        self.assertEqual([], result["repair_report"])

    def test_invalid_json_generation_returns_failure(self) -> None:
        result = self.compile_with(ValueError("JSON incomplet"))
        self.assertFalse(result["success"])
        self.assertEqual("generation", result["error"]["stage"])

    def test_workflow_needing_repair_starts_repair_loop(self) -> None:
        result = self.compile_with(invalid_without_end(), valid_workflow())
        self.assertEqual(1, len(result["repair_report"]))
        self.assertIn("MISSING_END_EVENT", {e["code"] for e in result["repair_report"][0]["input_errors"]})

    def test_successful_repair_returns_success(self) -> None:
        result = self.compile_with(invalid_without_end(), valid_workflow())
        self.assertTrue(result["success"])
        self.assertEqual("success", result["repair_report"][-1]["status"])

    def test_pipeline_never_passes_prompt_builder_to_repairer(self) -> None:
        constructor_arguments: list[dict] = []

        class StrictRepairer:
            def __init__(self, max_attempts=None) -> None:
                constructor_arguments.append({"max_attempts": max_attempts})

            def repair(self, workflow, errors, generator):
                return valid_workflow(), [
                    {
                        "attempt": 1,
                        "status": "success",
                        "input_errors": copy.deepcopy(errors),
                        "output_errors": [],
                        "message": "Réparé par le faux strict.",
                    }
                ]

        with patch("src.generation.pipeline.WorkflowRepairer", StrictRepairer):
            result = WorkflowCompiler(
                FakeGenerator(invalid_without_end()), SCHEMA_PATH
            ).compile("Régression constructeur")

        self.assertTrue(result["success"], result)
        self.assertEqual([{"max_attempts": 3}], constructor_arguments)

    def test_failed_repair_stops_after_three_attempts(self) -> None:
        invalid = invalid_without_end()
        result = self.compile_with(invalid, invalid, invalid, invalid)
        self.assertFalse(result["success"])
        self.assertEqual("repair", result["error"]["stage"])
        self.assertEqual(3, len(result["repair_report"]))

    def test_generator_exception_is_structured(self) -> None:
        result = self.compile_with(RuntimeError("model offline"))
        self.assertEqual({"stage": "generation", "message": "model offline"}, result["error"])

    def test_schema_errors_use_requested_shape(self) -> None:
        invalid = valid_workflow(schemaVersion="9.0")
        result = self.compile_with(invalid, invalid, invalid, invalid)
        self.assertTrue(result["schema_errors"])
        expected = {"code", "message", "path", "node_id", "flow_id", "severity"}
        self.assertTrue(all(set(error) == expected for error in result["schema_errors"]))
        self.assertTrue(all(error["code"] == "JSON_SCHEMA_ERROR" for error in result["schema_errors"]))

    def test_pipeline_never_validates_raw_legacy_flow_keys(self) -> None:
        raw = valid_workflow()
        for item in raw["flows"]:
            item["sourceRef"] = item.pop("source")
            item["targetRef"] = item.pop("target")
        compiler = WorkflowCompiler(FakeGenerator(raw), SCHEMA_PATH)
        observed: list[dict] = []
        real_validate = compiler._semantic_validator.validate

        def recording_validate(candidate):
            observed.append(copy.deepcopy(candidate))
            return real_validate(candidate)

        compiler._semantic_validator.validate = recording_validate
        result = compiler.compile("Entrée legacy")
        self.assertTrue(result["success"], result)
        self.assertTrue(observed)
        for candidate in observed:
            for item in candidate["flows"]:
                self.assertIn("source", item)
                self.assertIn("target", item)
                self.assertNotIn("sourceRef", item)
                self.assertNotIn("targetRef", item)

    def test_semantic_errors_are_collected(self) -> None:
        invalid = invalid_without_end()
        result = self.compile_with(invalid, invalid, invalid, invalid)
        self.assertIn("MISSING_END_EVENT", {error["code"] for error in result["semantic_errors"]})

    def test_graph_analysis_is_returned(self) -> None:
        result = self.compile_with(valid_workflow())
        self.assertEqual(["start", "work", "end"], result["analysis"]["topological_order"])

    def test_layout_is_returned(self) -> None:
        result = self.compile_with(valid_workflow())
        self.assertEqual("horizontal", result["layout"]["metadata"]["orientation"])
        self.assertLess(result["layout"]["nodes"]["start"]["x"], result["layout"]["nodes"]["end"]["x"])

    def test_compile_and_save_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = WorkflowCompiler(FakeGenerator(valid_workflow()), SCHEMA_PATH).compile_and_save("Sauvegarder", directory)
            self.assertTrue(result["success"])
            expected = {
                "raw_workflow.json", "normalized_workflow.json", "analysis.json",
                "layout.json", "repair_report.json", "compilation_report.json",
            }
            self.assertEqual(expected, {path.name for path in Path(directory).iterdir()})
            saved = json.loads((Path(directory) / "compilation_report.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["success"])

    def test_description_is_preserved(self) -> None:
        description = "Une description métier libre"
        self.assertEqual(description, self.compile_with(valid_workflow(), description=description)["description"])

    def test_success_true_and_error_null(self) -> None:
        result = self.compile_with(valid_workflow())
        self.assertTrue(result["success"])
        self.assertIsNone(result["error"])

    def test_failure_keeps_complete_result_shape(self) -> None:
        result = self.compile_with(ValueError("bad output"))
        expected = {
            "success", "description", "raw_workflow", "normalized_workflow",
            "schema_errors", "semantic_errors", "repair_report", "analysis",
            "layout", "bpmn_xml", "warnings", "error",
        }
        self.assertEqual(expected, set(result))

    def test_pools_and_lanes_compile(self) -> None:
        raw = valid_workflow(
            participants=[{
                "id": "operations", "name": "Operations", "type": "human",
                "lanes": [{"id": "processing", "name": "Processing", "nodeRefs": ["start", "work", "end"]}],
            }]
        )
        result = self.compile_with(raw)
        self.assertTrue(result["success"], result)
        self.assertIn("processing", result["layout"]["lanes"])

    def test_message_flow_between_pools_compiles(self) -> None:
        raw = valid_workflow(
            participants=[
                {"id": "p1", "name": "P1", "type": "human", "nodeRefs": ["start", "work"]},
                {"id": "p2", "name": "P2", "type": "system", "nodeRefs": ["end"]},
            ],
            messageFlows=[{"id": "message", "source": "work", "target": "end", "type": "messageFlow"}],
        )
        result = self.compile_with(raw)
        self.assertTrue(result["success"], result)
        self.assertEqual("message", result["layout"]["metadata"]["message_flow_connections"][0]["flow_id"])

    def test_iso_timer_compiles(self) -> None:
        raw = valid_workflow()
        raw["nodes"][1] = {"id": "work", "type": "timerEvent", "name": "Wait", "actor": None, "properties": {"timerType": "duration", "value": "PT5M"}}
        result = self.compile_with(raw)
        self.assertTrue(result["success"], result)
        self.assertEqual("PT5M", result["normalized_workflow"]["nodes"][1]["properties"]["value"])

    def test_loop_compiles_with_warning_and_channel(self) -> None:
        raw = valid_workflow()
        raw["flows"].append({"id": "back", "source": "work", "target": "work", "condition": None, "isDefault": False})
        result = self.compile_with(raw)
        self.assertTrue(result["success"], result)
        self.assertIn("back", result["layout"]["metadata"]["back_edge_channels"])

    def test_parallel_branches_compile(self) -> None:
        raw = valid_workflow(
            nodes=[
                {"id": "start", "type": "startEvent", "name": "Start", "actor": None, "properties": {}},
                {"id": "split", "type": "parallelGateway", "name": "Split", "actor": None, "properties": {"gatewayDirection": "diverging"}},
                {"id": "a", "type": "serviceTask", "name": "A", "actor": "system", "properties": {}},
                {"id": "b", "type": "serviceTask", "name": "B", "actor": "system", "properties": {}},
                {"id": "join", "type": "parallelGateway", "name": "Join", "actor": None, "properties": {"gatewayDirection": "converging"}},
                {"id": "end", "type": "endEvent", "name": "End", "actor": None, "properties": {}},
            ],
            flows=[
                {"id": "1", "source": "start", "target": "split", "condition": None, "isDefault": False},
                {"id": "2", "source": "split", "target": "a", "condition": None, "isDefault": False},
                {"id": "3", "source": "split", "target": "b", "condition": None, "isDefault": False},
                {"id": "4", "source": "a", "target": "join", "condition": None, "isDefault": False},
                {"id": "5", "source": "b", "target": "join", "condition": None, "isDefault": False},
                {"id": "6", "source": "join", "target": "end", "condition": None, "isDefault": False},
            ],
        )
        result = self.compile_with(raw)
        self.assertTrue(result["success"], result)
        self.assertNotEqual(result["layout"]["nodes"]["a"]["y"], result["layout"]["nodes"]["b"]["y"])

    def test_pipeline_fixes_misplaced_message_parallel_conditions_and_orphan(self) -> None:
        raw = valid_workflow(
            nodes=[
                {"id": "start", "type": "startEvent", "name": "Start", "actor": None, "properties": {}},
                {"id": "split", "type": "parallelGateway", "name": "Split", "actor": None, "properties": {"gatewayDirection": "diverging"}},
                {"id": "a", "type": "serviceTask", "name": "A", "actor": "system", "properties": {}},
                {"id": "b", "type": "serviceTask", "name": "B", "actor": "system", "properties": {}},
                {"id": "join", "type": "parallelGateway", "name": "Join", "actor": None, "properties": {"gatewayDirection": "converging"}},
                {"id": "end", "type": "endEvent", "name": "End", "actor": None, "properties": {}},
                {"id": "orphan", "type": "serviceTask", "name": "Unused", "actor": "system", "properties": {}},
                {"id": "message", "type": "messageFlow", "name": "Communication", "source": "a", "target": "b"},
            ],
            flows=[
                {"id": "1", "source": "start", "target": "split", "condition": None, "isDefault": False},
                {"id": "2", "source": "split", "target": "a", "condition": "${left}", "isDefault": False},
                {"id": "3", "source": "split", "target": "b", "conditionExpression": "${right}", "isDefault": False},
                {"id": "4", "source": "a", "target": "join", "condition": None, "isDefault": False},
                {"id": "5", "source": "b", "target": "join", "condition": None, "isDefault": False},
                {"id": "6", "source": "join", "target": "end", "condition": None, "isDefault": False},
            ],
        )
        generator = FakeGenerator(raw)
        result = WorkflowCompiler(generator, SCHEMA_PATH).compile("Processus générique")
        self.assertTrue(result["success"], result)
        self.assertEqual(1, len(generator.prompts))
        self.assertNotIn("orphan", {node["id"] for node in result["normalized_workflow"]["nodes"]})
        self.assertIn("message", {item["id"] for item in result["normalized_workflow"]["messageFlows"]})
        self.assertTrue(all(flow["condition"] is None for flow in result["normalized_workflow"]["flows"] if flow["source"] == "split"))
        self.assertIn("REMOVED_ISOLATED_NODE", {warning["code"] for warning in result["warnings"]})

    def test_compiler_owns_safe_repairs_without_normalizer_method(self) -> None:
        compiler = WorkflowCompiler(FakeGenerator(), SCHEMA_PATH)
        self.assertFalse(hasattr(WorkflowNormalizer(), "apply_safe_repairs"))
        self.assertTrue(callable(compiler._apply_safe_repairs))
        original = {
            "nodes": [
                {"id": "parallel", "type": "parallelGateway"},
                {"id": "connected", "type": "serviceTask"},
                {"id": "orphan", "type": "serviceTask"},
                {"id": "message", "type": "messageFlow", "source": "connected", "target": "pool"},
            ],
            "flows": [{"id": "f", "source": "parallel", "target": "connected", "condition": "${x}"}],
        }
        snapshot = copy.deepcopy(original)
        repaired, report = compiler._apply_safe_repairs(original)
        self.assertEqual(snapshot, original)
        self.assertNotIn("orphan", {node["id"] for node in repaired["nodes"]})
        self.assertIn("message", {flow["id"] for flow in repaired["messageFlows"]})
        self.assertIsNone(repaired["flows"][0]["condition"])
        self.assertEqual(
            {
                "MISPLACED_MESSAGE_FLOW_MOVED",
                "PARALLEL_GATEWAY_CONDITION_REMOVED",
                "REMOVED_ISOLATED_NODE",
            },
            {entry["code"] for entry in report},
        )

    def test_safe_repairs_keep_message_linked_task_and_start_event(self) -> None:
        compiler = WorkflowCompiler(FakeGenerator(), SCHEMA_PATH)
        repaired, report = compiler._apply_safe_repairs(
            {
                "nodes": [
                    {"id": "start", "type": "startEvent"},
                    {"id": "task", "type": "serviceTask"},
                ],
                "flows": [],
                "messageFlows": [{"id": "m", "source": "task", "target": "pool"}],
            }
        )
        self.assertEqual({"start", "task"}, {node["id"] for node in repaired["nodes"]})
        self.assertNotIn("REMOVED_ISOLATED_NODE", {entry["code"] for entry in report})

    def test_safe_repairs_remove_only_unresolvable_sequence_flows(self) -> None:
        compiler = WorkflowCompiler(FakeGenerator(), SCHEMA_PATH)
        repaired, report = compiler._apply_safe_repairs(
            {
                "nodes": [
                    {"id": "a", "type": "startEvent"},
                    {"id": "b", "type": "endEvent"},
                ],
                "flows": [
                    {"id": "valid", "source": "a", "target": "b"},
                    {"id": "missing", "source": "a"},
                    {"id": "unknown", "source": "a", "target": "ghost"},
                ],
            }
        )
        self.assertEqual(["valid"], [flow["id"] for flow in repaired["flows"]])
        self.assertEqual(2, sum(entry["code"] == "REMOVED_INVALID_FLOW" for entry in report))

    def test_corrections_contain_no_refund_specific_rule(self) -> None:
        sources = [
            Path(__file__).resolve().parents[1] / "src" / "workflow" / "normalizer.py",
            Path(__file__).resolve().parents[1] / "src" / "generation" / "pipeline.py",
            Path(__file__).resolve().parents[1] / "src" / "generation" / "prompts.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in sources)
        self.assertNotIn("remboursement", combined)

    def test_prompt_builders_include_schema_and_canonical_keys(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        generation = build_generation_prompt("Description", schema)
        repair = build_repair_prompt({}, [{"code": "X", "message": "Erreur"}], schema)
        for prompt in (generation, repair):
            self.assertIn("source et target", prompt)
            self.assertIn("JSON Schema", prompt)
            self.assertIn("uniquement un objet JSON", prompt)

    def test_extract_json_object_handles_markdown_and_incomplete_json(self) -> None:
        self.assertEqual({"ok": True}, extract_json_object("```json\n{\"ok\": true}\n```"))
        with self.assertRaisesRegex(ValueError, "incomplet"):
            extract_json_object("```json\n{\"ok\": true")


if __name__ == "__main__":
    unittest.main()
