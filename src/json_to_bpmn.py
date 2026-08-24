"""Command-line adapter for deterministic JSON-to-BPMN conversion."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bpmn import BPMNGenerator, BPMNLayoutEngine
from workflow import SemanticValidator, WorkflowGraphAnalyzer, WorkflowNormalizer


def convert_json_to_bpmn(json_path: str | Path, output_path: str | Path) -> str:
    """Convert one normalized workflow file and return the generated XML."""
    source = Path(json_path)
    destination = Path(output_path)
    workflow: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        raise TypeError("Le workflow JSON doit être un objet.")

    canonical = WorkflowNormalizer().normalize(workflow)
    issues = SemanticValidator().validate(canonical)
    blocking = [issue for issue in issues if issue.get("severity", "error") == "error"]
    if blocking:
        details = "; ".join(
            f"[{issue.get('code', 'ERROR')}] {issue.get('message', 'Erreur de validation')}"
            for issue in blocking
        )
        raise ValueError(f"Workflow invalide après normalisation v2 : {details}")
    analysis = WorkflowGraphAnalyzer().analyze(canonical)
    layout = BPMNLayoutEngine().layout(canonical, analysis)
    xml = BPMNGenerator().generate(canonical, layout)
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
