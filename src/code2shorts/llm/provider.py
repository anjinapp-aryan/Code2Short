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

    # `complete` is the contract. The two below are CONCRETE rather than
    # abstract on purpose: `FailoverLLMProvider` needs to name a provider
    # and ask whether it can run at all, and making either abstract would
    # break every implementation that predates them — including any a
    # deployment has written against this interface. The defaults are
    # correct for a provider that is constructed only when its credential
    # is present, which is how the factory builds all of them.

    @property
    def provider_name(self) -> str:
        """Short, stable, credential-free identity for logs and lineage."""
        return type(self).__name__

    def is_configured(self) -> bool:
        """Whether this provider can be used at all.

        A provider reaches construction only after the factory has found
        its credential, so a constructed provider is configured. An
        implementation that can be built without one — a chain, or an
        endpoint whose key arrives later — overrides this.
        """
        return True
