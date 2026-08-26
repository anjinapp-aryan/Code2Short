"""MockLLMProvider: deterministic stand-in implementing the EXISTING
LLMProvider interface (code2shorts.llm.provider) — not a new/parallel
interface. Sufficient for Phase 3; real providers (Gemini/OpenAI/Claude)
are future adapters behind the same seam, added only when needed.
"""

from __future__ import annotations

from collections.abc import Callable

from code2shorts.llm.provider import LLMProvider


class MockLLMProvider(LLMProvider):
    def __init__(self, canned_response: str | Callable[[str], str] | None = None) -> None:
        self._canned = canned_response
        self.calls: list[str] = []

    def complete(self, prompt: str, system: str | None = None) -> str:
        self.calls.append(prompt)
        if callable(self._canned):
            return self._canned(prompt)
        if self._canned is not None:
            return self._canned
        raise NotImplementedError(
            "MockLLMProvider requires a canned_response for deterministic tests"
        )
