"""Command-line adapter for deterministic JSON-to-BPMN conversion."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bpmn import BPMNGenerator, BPMNLayoutEngine
from workflow import WorkflowGraphAnalyzer


def convert_json_to_bpmn(json_path: str | Path, output_path: str | Path) -> str:
    """Convert one normalized workflow file and return the generated XML."""
    source = Path(json_path)
    destination = Path(output_path)
    workflow: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        raise TypeError("Le workflow JSON doit être un objet.")

    analysis = WorkflowGraphAnalyzer().analyze(workflow)
    layout = BPMNLayoutEngine().layout(workflow, analysis)
    xml = BPMNGenerator().generate(workflow, layout)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(xml, encoding="utf-8")
    return xml


def main(argv: list[str] | None = None) -> int:
    """Run the legacy two-path conversion command."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print("Usage : python src/json_to_bpmn.py workflow.json workflow.bpmn")
        return 1
    try:
        convert_json_to_bpmn(arguments[0], arguments[1])
    except Exception as exc:
        print(f"Erreur : {exc}")
        return 1
    print(f"BPMN généré : {Path(arguments[1]).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
