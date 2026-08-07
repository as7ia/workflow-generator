"""Executable natural-language to BPMN workflow compiler."""

from __future__ import annotations

import argparse
from pathlib import Path

from generation import QwenWorkflowGenerator, WorkflowCompiler
from generation.model_loader import DEFAULT_QWEN_MODEL, load_qwen_model


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Compile une description métier en un fichier BPMN 2.0."
    )
    parser.add_argument("--description", help="Description métier à compiler.")
    parser.add_argument("--model", default=DEFAULT_QWEN_MODEL, help="Modèle Qwen Transformers.")
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=3,
        help="Nombre maximal de tentatives de réparation JSON.",
    )
    parser.add_argument("--output", default="output/run", help="Répertoire de sortie.")
    parser.add_argument(
        "--load-in-4bit",
        action="store_true",
        help="Charge le modèle en 4 bits avec bitsandbytes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Load Qwen, compile the description and save all artifacts."""
    arguments = build_parser().parse_args(argv)
    description = arguments.description
    if not description:
        description = input("Décrivez votre processus :\n").strip()
    if not description:
        print("Erreur : la description métier est vide.")
        return 2
    if arguments.max_repair_attempts < 0:
        print("Erreur : --max-repair-attempts doit être positif ou nul.")
        return 2

    project_root = Path(__file__).resolve().parent.parent
    schema_path = project_root / "schemas" / "workflow.schema.json"
    try:
        tokenizer, model = load_qwen_model(arguments.model, arguments.load_in_4bit)
        generator = QwenWorkflowGenerator(tokenizer, model)
        compiler = WorkflowCompiler(
            generator,
            schema_path,
            max_repair_attempts=arguments.max_repair_attempts,
        )
        output_dir = Path(arguments.output)
        result = compiler.compile_and_save(description, str(output_dir))
    except Exception as exc:
        print(f"Échec du chargement ou de la compilation : {exc}")
        return 1

    if not result["success"]:
        error = result.get("error") or {}
        print(f"Compilation échouée à l'étape {error.get('stage', 'inconnue')} : {error.get('message', '')}")
        return 1
    print(f"BPMN généré : {(output_dir / 'workflow.bpmn').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
