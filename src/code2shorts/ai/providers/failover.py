"""FailoverLLMProvider: try configured providers in a fixed order.

This implements what ADR-5.10 deferred, under the conditions that ADR set:
failover is **opt-in** (`LLM_PROVIDER=failover`, never a side effect of
declaring a fallback), the order is deterministic, the chain is bounded,
and the provider that actually answered is recorded so artifact lineage
still names its real producer.

It is itself an `LLMProvider`, so nothing above the seam changes:
`generate_structured` and `ai/repair.py` receive one object with one
`complete()` method and never learn how many endpoints sit behind it.

    complete()
        |
        +-- OmniRoute   transient failure -> next
        |
        +-- OpenRouter  transient failure -> next
        |
        +-- Gemini      answers            -> done

The classification this walks on is NOT new. Each provider already sorts
its own errors into `*TransientError` (timeout, 429, 5xx, connection
reset) and `*PermanentError` (401, 403, 400, unknown model), and those
classifications are already covered by tests. This module adds the walk,
not the taxonomy.
"""

from __future__ import annotations

import logging

from code2shorts.ai.providers.gemini import GeminiPermanentError, GeminiTransientError
from code2shorts.ai.providers.openai_compatible import (
    OpenAICompatiblePermanentError,
    OpenAICompatibleTransientError,
)
from code2shorts.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    GeminiTransientError,
    OpenAICompatibleTransientError,
)
"""Failures that mean "this provider, right now" - so another provider is
worth trying. Every one is already classified at the provider that raised
it; this module never inspects an HTTP status itself."""

PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    GeminiPermanentError,
    OpenAICompatiblePermanentError,
)
"""Failures that mean "this request, anywhere" - a bad key, a bad model,
a malformed request. Walking the chain on these would turn one clear
"your key is wrong" into three vague outages and hide the real cause, so
they stop the chain immediately."""


class NoProviderConfiguredError(Exception):
    """Not one provider in the chain has a credential. Names variables,
    never values."""


class AllProvidersFailedError(Exception):
    """Every configured provider failed transiently.

    Carries each provider's own error so the caller sees which endpoints
    were tried and why each one declined - a chain that reported only its
    last failure would hide a rate-limited primary behind a timed-out
    tertiary.
    """

    def __init__(self, failures: list[tuple[str, Exception]]) -> None:
        self.failures = failures
        detail = "; ".join(f"{name}: {error}" for name, error in failures)
        super().__init__(f"all {len(failures)} configured LLM providers failed: {detail}")


class FailoverLLMProvider(LLMProvider):
    """An ordered chain of providers, tried once each.

    Bounded by construction: the chain length is the attempt count. There
    is no loop back to an earlier provider, no recursion, and no retry of
    the chain itself. Within a provider, its own bounded backoff still
    applies - `build_failover_provider` sets that to a single attempt by
    default so three providers cost three requests rather than nine.
    """

    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        if not providers:
            raise NoProviderConfiguredError(
                "no LLM provider is configured. Set at least one of "
                "CODE2SHORTS_LLM_API_KEY (OmniRoute), "
                "CODE2SHORTS_OPENROUTER_API_KEY or CODE2SHORTS_GEMINI_API_KEY."
            )
        self._providers = list(providers)
        self._last_provider_name: str | None = None
        self._last_fallback_from: str | None = None

    @property
    def provider_names(self) -> list[str]:
        """The order, as it will actually be walked."""
        return [name for name, _ in self._providers]

    @property
    def provider_name(self) -> str:
        """The provider that ANSWERED, once one has.

        Before the first call there is no answer to attribute, so the
        chain names itself rather than guessing at its primary - recording
        a producer that has not produced anything is exactly the lineage
        defect ADR-5.10 was protecting against.
        """
        return self._last_provider_name or "failover"

    def is_configured(self) -> bool:
        return any(provider.is_configured() for _, provider in self._providers)

    @property
    def describe(self) -> dict[str, str]:
        """Provenance for artifact metadata. No key, and the REAL producer.

        `fallback_from` is present only when the primary was not the one
        that answered, so a fallback can never look like a clean primary
        run in the recorded lineage.
        """
        described: dict[str, str] = {
            "provider": self.provider_name,
            "chain": ",".join(self.provider_names),
        }
        if self._last_fallback_from:
            described["fallback_from"] = self._last_fallback_from
        return described

    def complete(self, prompt: str, system: str | None = None) -> str:
        failures: list[tuple[str, Exception]] = []
        previous: str | None = None

        for index, (name, provider) in enumerate(self._providers):
            # Prompt length, not the prompt. The content of a request is
            # never logged - see the Phase 5 security tests.
            logger.info(
                "llm request started provider=%s prompt_chars=%d", name, len(prompt)
            )
            try:
                content = provider.complete(prompt, system=system)
            except PERMANENT_ERRORS:
                # A bad key is not an outage. Report the real problem
                # rather than turning it into three of them.
                logger.error(
                    "llm provider failed provider=%s failureType=PERMANENT "
                    "fallback=none",
                    name,
                )
                self._last_provider_name = name
                self._last_fallback_from = previous
                raise
            except TRANSIENT_ERRORS as error:
                remaining = self._providers[index + 1 :]
                logger.warning(
                    "llm provider failed provider=%s failureType=TRANSIENT "
                    "fallback=%s",
                    name,
                    remaining[0][0] if remaining else "none",
                )
                failures.append((name, error))
                previous = name
                continue

            self._last_provider_name = name
            self._last_fallback_from = previous
            if previous:
                logger.info(
                    "llm provider succeeded provider=%s fallbackFrom=%s", name, previous
                )
            else:
                logger.info("llm provider succeeded provider=%s", name)
            return content

        raise AllProvidersFailedError(failures)
