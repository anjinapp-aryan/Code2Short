"""GeminiLLMProvider unit tests — NO network, NO real API key. The
litellm-shaped completion call is injected as a fake, and provider-library
exceptions are simulated by name (GeminiLLMProvider classifies by
exception CLASS NAME, not by importing litellm's exception types).
"""

from __future__ import annotations

import pytest

from code2shorts.ai.providers.gemini import (
    GeminiLLMProvider,
    GeminiPermanentError,
    GeminiTransientError,
)


class Timeout(Exception):
    pass


class RateLimitError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = type("Message", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def test_successful_response() -> None:
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return _FakeResponse("hello")

    provider = GeminiLLMProvider(api_key="fake-key", completion_fn=fake_completion)
    result = provider.complete("prompt", system="sys")

    assert result == "hello"
    assert calls[0]["model"] == "gemini/gemini-1.5-flash"
    assert calls[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "prompt"},
    ]
    assert "api_key" not in str(calls[0].get("messages"))  # key never leaks into message content


def test_constructor_requires_api_key() -> None:
    with pytest.raises(GeminiPermanentError):
        GeminiLLMProvider(api_key=None, completion_fn=lambda **_: None)


def test_timeout_is_retried_then_succeeds() -> None:
    attempts = {"count": 0}

    def fake_completion(**kwargs):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise Timeout("simulated timeout")
        return _FakeResponse("recovered")

    provider = GeminiLLMProvider(
        api_key="fake-key", max_retries=2, backoff_seconds=0, completion_fn=fake_completion
    )
    result = provider.complete("prompt")

    assert result == "recovered"
    assert attempts["count"] == 2


def test_rate_limit_retried_with_backoff_then_succeeds() -> None:
    attempts = {"count": 0}

    def fake_completion(**kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RateLimitError("simulated rate limit")
        return _FakeResponse("ok")

    provider = GeminiLLMProvider(
        api_key="fake-key", max_retries=3, backoff_seconds=0, completion_fn=fake_completion
    )
    result = provider.complete("prompt")

    assert result == "ok"
    assert attempts["count"] == 3


def test_transient_failure_exhausts_retries_and_raises() -> None:
    def fake_completion(**kwargs):
        raise Timeout("always times out")

    provider = GeminiLLMProvider(
        api_key="fake-key", max_retries=2, backoff_seconds=0, completion_fn=fake_completion
    )
    with pytest.raises(GeminiTransientError):
        provider.complete("prompt")


def test_permanent_failure_is_never_retried() -> None:
    attempts = {"count": 0}

    def fake_completion(**kwargs):
        attempts["count"] += 1
        raise AuthenticationError("bad key")

    provider = GeminiLLMProvider(
        api_key="fake-key", max_retries=5, backoff_seconds=0, completion_fn=fake_completion
    )
    with pytest.raises(GeminiPermanentError):
        provider.complete("prompt")

    assert attempts["count"] == 1  # never retried


def test_empty_response_is_a_permanent_error() -> None:
    def fake_completion(**kwargs):
        return _FakeResponse("")

    provider = GeminiLLMProvider(api_key="fake-key", completion_fn=fake_completion)
    with pytest.raises(GeminiPermanentError):
        provider.complete("prompt")
