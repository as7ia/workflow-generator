"""Thin adapter around an already-loaded Transformers Qwen model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first complete valid JSON object from arbitrary model text.

    Braces inside JSON strings are ignored. An incomplete object is reported
    distinctly from output containing no JSON object at all.
    """
    if not isinstance(text, str):
        raise TypeError("La sortie du modèle doit être une chaîne de caractères.")
    saw_opening = False
    for start, character in enumerate(text):
        if character != "{":
            continue
        saw_opening = True
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        value = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
    if saw_opening:
        raise ValueError("La sortie contient un objet JSON incomplet ou invalide.")
    raise ValueError("Aucun objet JSON n'a été trouvé dans la sortie du modèle.")


class QwenWorkflowGenerator:
    """Generate JSON with a caller-provided tokenizer and Transformers model."""

    def __init__(
        self,
        tokenizer: Any,
        model: Any,
        max_new_tokens: int = 4096,
        enable_thinking: bool = False,
    ) -> None:
        self.tokenizer = tokenizer
        self.model = model
        self.max_new_tokens = max_new_tokens
        self.enable_thinking = enable_thinking
        self.last_generation_metadata: dict[str, Any] = {
            "raw_text_length": 0, "json_extracted": False,
            "parse_success": False, "cause": None,
        }

    def generate_json(self, prompt: str) -> dict[str, Any]:
        """Run Qwen and return the JSON object extracted from its response."""
        strict_prompt = (
            f"{prompt}\n\nRetourne uniquement un objet JSON valide, sans Markdown ni explication."
        )
        messages = [{"role": "user", "content": strict_prompt}]
        try:
            rendered = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=self.enable_thinking,
            )
        except TypeError:
            rendered = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        inputs = generation_inputs = outputs = generated_ids = input_ids = None
        try:
            inputs = self.tokenizer(rendered, return_tensors="pt")
            device = getattr(self.model, "device", None)
            if device is not None and hasattr(inputs, "to"):
                inputs = inputs.to(device)
            generation_inputs = dict(inputs) if isinstance(inputs, Mapping) else inputs
            if not isinstance(generation_inputs, dict):
                raise TypeError("Le tokenizer doit retourner un mapping compatible avec model.generate().")
            outputs = self.model.generate(
                **generation_inputs,
                max_new_tokens=self.max_new_tokens,
            )
            input_ids = generation_inputs.get("input_ids")
            prompt_length = self._sequence_length(input_ids)
            generated_ids = outputs[0][prompt_length:] if prompt_length is not None else outputs[0]
            decoded = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
            self.last_generation_metadata = {
                "raw_text_length": len(decoded), "json_extracted": False,
                "parse_success": False, "cause": None,
            }
            try:
                parsed = extract_json_object(decoded)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.last_generation_metadata["cause"] = str(exc)
                raise
            self.last_generation_metadata.update(json_extracted=True,parse_success=True)
            return parsed
        finally:
            # Release references between complete generation attempts. Cleanup is
            # best-effort and must never hide the original generation/OOM error.
            inputs = generation_inputs = outputs = generated_ids = input_ids = None
            self._release_cuda_cache()

    @staticmethod
    def _release_cuda_cache() -> None:
        """Release unused CUDA allocations between generation calls, if active."""
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @staticmethod
    def _sequence_length(input_ids: Any) -> int | None:
        """Read tokenized prompt length without importing tensor libraries."""
        shape = getattr(input_ids, "shape", None)
        if shape is not None and len(shape) >= 2:
            return int(shape[-1])
        try:
            return len(input_ids[0])
        except (TypeError, IndexError, KeyError):
            return None
