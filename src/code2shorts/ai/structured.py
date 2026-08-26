"""generate_structured: the ONE seam that turns an LLMProvider's raw text
into a validated Pydantic model. Deliberately reuses
`code2shorts.llm.provider.LLMProvider` (Phase 0.1's provider abstraction)
rather than inventing a second, parallel "AIProvider" interface — see
ARCHITECTURE_DECISIONS.md "framework-independent AI contracts".
"""

from __future__ import annotations

import json
import re

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from code2shorts.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)


class SchemaValidationError(Exception):
    """The provider's raw output could not be parsed into the requested
    response model. Distinct from SemanticValidationError (ai/validation.py)
    — this is "the JSON shape is wrong", not "the JSON is well-formed but
    the claims inside it are false".
    """


def extract_json(raw: str) -> str:
    """Recover the JSON body from a real model's reply.

    Purely lexical and non-executing: it slices the text, never interprets
    it. Real models routinely wrap JSON in a ```json fence or add a
    sentence of preamble, and `model_validate_json` rejects both - which
    would burn a repair attempt on a response that was actually correct.

    This is NOT lenient parsing: whatever is extracted still has to satisfy
    the Pydantic model in full. If nothing JSON-shaped is found the original
    text is returned so the schema error reports what the model really said.
    """
    text = raw.strip()

    fence = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    if text.startswith(("{", "[")):
        return text

    # fall back to the outermost brace/bracket span
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start : end + 1]
    return raw


def generate_structured(
    provider: LLMProvider,
    prompt: str,
    response_model: type[T],
    system: str | None = None,
) -> T:
    raw = provider.complete(prompt, system=system)
    try:
        return response_model.model_validate_json(extract_json(raw))
    except ValidationError as error:
        raise SchemaValidationError(str(error)) from error


def json_schema_instruction(response_model: type[BaseModel]) -> str:
    """The response-format instruction appended to every AI prompt.

    Generated FROM the Pydantic model, so it can never drift from the
    contract the response is validated against - a hand-written example
    would silently rot the first time a field changed.

    This exists because real models need to be told the shape. Phase 4's
    mock provider always emitted valid JSON, so prompts that never stated
    the schema looked fine; the first live model returned `steps` as an
    array of strings instead of objects and burned every repair attempt.
    Stating the schema is the fix - loosening validation is not.
    """
    schema = json.dumps(response_model.model_json_schema(), indent=None, separators=(",", ":"))
    return (
        "\nRespond with a single JSON object and nothing else - no prose, "
        "no markdown fence, no explanation before or after.\n"
        "It MUST validate against this JSON Schema. Every field marked "
        "required must be present, and objects must be objects (not strings):\n"
        f"{schema}"
    )
