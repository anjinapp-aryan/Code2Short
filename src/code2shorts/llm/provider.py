"""Clean LLM provider abstraction. No provider-specific code outside this seam.

Concrete implementations (LiteLLM-backed, routing to Ollama/Claude/Gemini/
OpenAI per `config.Settings`) are wired up in Phase 1. Nothing above this
interface — `CodeGenerator`, lesson planning — should import a provider SDK
directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, system: str | None = None) -> str:
        """Return the model's full text completion for `prompt`."""
