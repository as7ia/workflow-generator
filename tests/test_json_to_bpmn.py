"""Boundary test for canonical JSON to BPMN XML reference conversion."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class JsonToBpmnTests(unittest.TestCase):
    """Ensure sourceRef/targetRef exist only at the XML boundary."""

    def test_source_target_become_xml_sequence_flow_attributes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = {
            "id": "process",
            "name": "Process",
            "nodes": [
                {"id": "start", "type": "startEvent", "name": "Start"},
                {"id": "end", "type": "endEvent", "name": "End"},
            ],
            "flows": [{"id": "flow", "source": "start", "target": "end"}],
        }
        snapshot = json.loads(json.dumps(workflow))
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "workflow.json"
            output_path = Path(directory) / "workflow.bpmn"
            input_path.write_text(json.dumps(workflow), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(root / "src" / "json_to_bpmn.py"), str(input_path), str(output_path)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            tree = ET.parse(output_path)
        namespace = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
        sequence_flow = tree.find(".//bpmn:sequenceFlow", namespace)
        self.assertIsNotNone(sequence_flow)
        self.assertEqual("start", sequence_flow.get("sourceRef"))
        self.assertEqual("end", sequence_flow.get("targetRef"))
        self.assertEqual(snapshot, workflow)


if __name__ == "__main__":
    unittest.main()
