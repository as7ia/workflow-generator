"""Lazy loading utilities for Qwen models backed by Transformers."""

from __future__ import annotations

from typing import Any


DEFAULT_QWEN_MODEL = "Qwen/Qwen3-4B"


def load_qwen_model(
    model_name: str = DEFAULT_QWEN_MODEL,
    load_in_4bit: bool = False,
) -> tuple[Any, Any]:
    """Load and return a Qwen tokenizer/model pair.

    Imports are intentionally local so the deterministic workflow and BPMN
    layers remain usable without Transformers.  ``device_map='auto'`` lets
    Accelerate select a GPU when available and otherwise keeps a CPU setup.
    Four-bit mode requires the optional ``bitsandbytes`` package.
    """
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - depends on runtime setup.
        raise RuntimeError(
            "Transformers n'est pas installé. Exécutez "
            "'pip install -r requirements.txt'."
        ) from exc

    model_options: dict[str, Any] = {"device_map": "auto", "torch_dtype": "auto"}
    if load_in_4bit:
        try:
            from transformers import BitsAndBytesConfig
        except ImportError as exc:  # pragma: no cover - version dependent.
            raise RuntimeError(
                "La quantification 4 bits nécessite bitsandbytes et une version "
                "compatible de Transformers."
            ) from exc
        model_options["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name, **model_options)
    return tokenizer, model
