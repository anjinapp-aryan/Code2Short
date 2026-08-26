"""OpenAICompatibleProvider: one provider for every OpenAI-compatible endpoint.

This is the whole of Code2Shorts' multi-provider story, and it is
deliberately small. OmniRoute, Ollama, LM Studio, vLLM, OpenRouter, Together
and OpenAI itself all speak the same `/v1/chat/completions` wire format, so
a single provider parameterised by `base_url` reaches all of them. There is
no OmniRouteProvider class, no per-vendor subclass, and no
`if provider == ...` anywhere above this seam.

Built on `litellm`, already a project dependency since Phase 0 and already
used by `GeminiLLMProvider` - no new dependency (see
docs/PHASE_5_REUSE_AUDIT.md).

Testable without credentials or network: `completion_fn` is injected, the
same pattern `GeminiLLMProvider` established.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from code2shorts.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 1.0


class OpenAICompatibleError(Exception):
    """Base error. Never carries the API key - only the upstream library's
    own message, which we do not augment with credentials."""


class OpenAICompatibleTransientError(OpenAICompatibleError):
    """Timeout, rate limit, 5xx, connection reset. Retryable by the
    PROVIDER. Distinct from a workflow repair, which handles invalid
    content rather than a failed call - see ADR "provider retry vs
    workflow repair"."""


class OpenAICompatiblePermanentError(OpenAICompatibleError):
    """Auth failure, bad request, missing model. Retrying without changing
    the configuration fails identically, so it never is retried."""


def _default_completion_fn(**kwargs: Any) -> Any:
    import litellm  # imported lazily so importing this module stays cheap

    return litellm.completion(**kwargs)


class OpenAICompatibleProvider(LLMProvider):
    """Talks to any OpenAI-compatible `/v1` endpoint.

    `base_url` is the only thing that distinguishes one backend from
    another - e.g. `http://localhost:20128/v1` for a local OmniRoute
    gateway, `http://localhost:11434/v1` for Ollama. Code2Shorts holds no
    knowledge of what is behind it, which is exactly why OmniRoute can be
    optional infrastructure rather than a dependency.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "auto",
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        resolved_url = base_url or os.environ.get("CODE2SHORTS_LLM_BASE_URL")
        if not resolved_url:
            raise OpenAICompatiblePermanentError(
                "no base_url configured (pass base_url= or set "
                "CODE2SHORTS_LLM_BASE_URL, e.g. http://localhost:20128/v1)"
            )
        self._base_url = resolved_url
        self._model = model
        # Many local gateways need no key at all; send a harmless placeholder
        # so the client library does not refuse to build the request.
        self._api_key = api_key or os.environ.get("CODE2SHORTS_LLM_API_KEY") or "not-needed"
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._completion_fn = completion_fn or _default_completion_fn

    @property
    def describe(self) -> dict[str, str]:
        """Provenance for artifact metadata. Deliberately excludes the key."""
        return {"provider": "openai_compatible", "base_url": self._base_url, "model": self._model}

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(1, self._max_retries + 2):
            try:
                logger.info(
                    "llm request base_url=%s model=%s attempt=%d prompt_chars=%d",
                    self._base_url,
                    self._model,
                    attempt,
                    len(prompt),
                )
                response = self._completion_fn(
                    model=f"openai/{self._model}",
                    messages=messages,
                    base_url=self._base_url,
                    api_key=self._api_key,
                    timeout=self._timeout_seconds,
                    # Disable the client library's OWN retry loops so this
                    # class is the single retry authority.
                    #
                    # Measured: without these, `max_retries=2` produced NINE
                    # upstream HTTP requests - litellm retries 3x and the
                    # underlying OpenAI client retries again, multiplying
                    # with our 3 attempts. Against a rate-limited free tier
                    # (the OmniRoute use case) that silently triples load
                    # and can burn quota or trigger a ban, while the
                    # configured number says 3.
                    num_retries=0,
                    max_retries=0,
                )
                content = response.choices[0].message.content
                if not content:
                    raise OpenAICompatiblePermanentError("endpoint returned an empty response")
                logger.info("llm response received chars=%d", len(content))
                return content
            except OpenAICompatibleError:
                raise
            except Exception as error:  # noqa: BLE001 - classify by exception class name
                classified = self._classify(error)
                if isinstance(classified, OpenAICompatiblePermanentError):
                    raise classified from error
                logger.warning("llm transient error attempt=%d: %s", attempt, classified)
                if attempt <= self._max_retries:
                    time.sleep(self._backoff_seconds * attempt)
                    continue
                raise classified from error

        raise OpenAICompatibleTransientError("exhausted retries")  # pragma: no cover

    @staticmethod
    def _classify(error: Exception) -> OpenAICompatibleError:
        """Classify by exception CLASS NAME rather than importing litellm's
        exception hierarchy, so this keeps working across litellm versions
        that reorganise those classes. Same approach as GeminiLLMProvider.
        """
        name = type(error).__name__
        transient = {
            "Timeout", "APIConnectionError", "RateLimitError",
            "ServiceUnavailableError", "InternalServerError", "APIError",
        }
        permanent = {
            "AuthenticationError", "BadRequestError", "InvalidRequestError",
            "PermissionDeniedError", "NotFoundError", "ContentPolicyViolationError",
        }
        if name in transient:
            return OpenAICompatibleTransientError(f"{name}: {error}")
        if name in permanent:
            return OpenAICompatiblePermanentError(f"{name}: {error}")
        # Unknown shapes are treated as transient: misclassifying as
        # permanent would make a real outage unrecoverable.
        return OpenAICompatibleTransientError(f"{name}: {error}")
