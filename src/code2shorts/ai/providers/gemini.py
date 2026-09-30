"""GeminiLLMProvider: a real, production-oriented LLMProvider
implementation, built on `litellm` (already a project dependency —
Phase 0's own docstring for `llm/provider.py` always intended a
LiteLLM-backed provider, this is that provider). Implements the EXISTING
`LLMProvider` interface — nothing above this module (workflow nodes,
`ai/repair.py`, `ai/structured.py`) knows or cares that Gemini is involved.

Testable without credentials: `completion_fn` is injected (defaults to
`litellm.completion`), so unit tests supply a fake that raises simulated
litellm exceptions or returns canned responses — no network, no API key,
no `unittest.mock.patch` on library internals.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from code2shorts.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-1.5-flash"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 1.0


class GeminiProviderError(Exception):
    """Base class for every error this provider raises. Never includes the
    API key in its message — only the underlying library's own error text,
    which we don't control but also never augment with secrets."""


class GeminiTransientError(GeminiProviderError):
    """Timeout, rate limit, or another retryable condition. Callers
    (workflow nodes) may map this to FailureKind.TRANSIENT."""


class GeminiPermanentError(GeminiProviderError):
    """Bad config, auth failure, invalid request — retrying without
    fixing the underlying problem will fail identically. Callers should
    map this to FailureKind.PERMANENT, never auto-retry it."""


# HTTP status is checked BEFORE the class name because litellm collapses
# several statuses into a generic `APIError`, and that class had to be
# treated as transient to stay safe. Measured: an HTTP 403 arrived as
# `APIError` and was therefore classified transient, which in a failover
# chain means a forbidden key is retried at two more vendors instead of
# being reported. The status is unambiguous when present, so it wins.
PERMANENT_STATUS = frozenset({400, 401, 403, 404, 405, 409, 413, 422})
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _default_completion_fn(**kwargs: Any) -> Any:
    import litellm  # imported lazily so importing this module never requires litellm's transitive deps to be resolvable in odd environments

    return litellm.completion(**kwargs)


class GeminiLLMProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise GeminiPermanentError(
                "no Gemini API key configured (pass api_key= or set GEMINI_API_KEY)"
            )
        self._api_key = resolved_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._completion_fn = completion_fn or _default_completion_fn

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error: GeminiProviderError | None = None
        for attempt in range(1, self._max_retries + 2):  # +1 initial, +max_retries
            try:
                logger.info(
                    "gemini request model=%s attempt=%d prompt_chars=%d",
                    self._model,
                    attempt,
                    len(prompt),
                )
                response = self._completion_fn(
                    model=f"gemini/{self._model}",
                    messages=messages,
                    api_key=self._api_key,
                    timeout=self._timeout_seconds,
                )
                content = response.choices[0].message.content
                if not content:
                    raise GeminiPermanentError("Gemini returned an empty response")
                logger.info("gemini response received chars=%d", len(content))
                return content
            except GeminiProviderError:
                raise
            except Exception as error:  # noqa: BLE001 - classify by library exception type name
                classified = self._classify(error)
                if isinstance(classified, GeminiPermanentError):
                    raise classified from error
                last_error = classified
                logger.warning("gemini transient error attempt=%d: %s", attempt, classified)
                if attempt <= self._max_retries:
                    time.sleep(self._backoff_seconds * attempt)
                    continue
                raise classified from error

        # unreachable, but keeps control-flow analysis happy
        raise last_error or GeminiTransientError("exhausted retries with no captured error")

    @staticmethod
    def _classify(error: Exception) -> GeminiProviderError:
        """litellm normalizes provider errors into its own exception
        hierarchy (litellm.exceptions.*), but we classify by exception
        CLASS NAME rather than importing litellm's exception types
        directly — keeps this file working even against litellm versions
        that move those classes around, and keeps the transient/permanent
        split explicit and reviewable in one place.
        """
        status = getattr(error, "status_code", None)
        if isinstance(status, int):
            if status in PERMANENT_STATUS:
                return GeminiPermanentError(f"HTTP {status}: {error}")
            if status in TRANSIENT_STATUS:
                return GeminiTransientError(f"HTTP {status}: {error}")

        name = type(error).__name__
        transient_names = {"Timeout", "APIConnectionError", "RateLimitError", "ServiceUnavailableError"}
        permanent_names = {"AuthenticationError", "BadRequestError", "InvalidRequestError", "PermissionDeniedError", "NotFoundError"}
        if name in transient_names:
            return GeminiTransientError(f"{name}: {error}")
        if name in permanent_names:
            return GeminiPermanentError(f"{name}: {error}")
        # Unknown error shape — treat as transient (retry a bounded number
        # of times) rather than permanent, since permanent misclassifies
        # to "never retry" and a real outage would then never recover.
        return GeminiTransientError(f"{name}: {error}")
