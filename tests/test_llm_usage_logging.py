"""POST-8.3 — LLM token usage and latency are logged per response.

Phase 8.3's failover runs were diagnosable only from raw error bodies:
nothing recorded how many tokens a request used or how long it took, so a
Groq tokens-per-minute limit could not be anticipated. litellm already
returns `usage`; this only logs it beside the existing `chars=` line.
Numbers only - never the prompt, the response text or a key.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from code2shorts.ai.providers.gemini import GeminiLLMProvider
from code2shorts.ai.providers.openai_compatible import OpenAICompatibleProvider

SECRET_PROMPT = "THE-PROMPT-MUST-NOT-BE-LOGGED"
KEY = "test-not-a-real-key"


def _response(usage=True):
    message = SimpleNamespace(content="pong")
    usage_ns = SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=usage_ns if usage else None)


def _providers(usage=True):
    fn = lambda **_kwargs: _response(usage)  # noqa: E731
    return [
        OpenAICompatibleProvider(base_url="http://127.0.0.1:1/v1", model="m", api_key=KEY,
                                 completion_fn=fn),
        GeminiLLMProvider(api_key=KEY, model="g", completion_fn=fn),
    ]


@pytest.mark.parametrize("index", [0, 1], ids=["openai_compatible", "gemini"])
def test_usage_and_latency_are_logged(caplog, index) -> None:
    provider = _providers()[index]
    with caplog.at_level(logging.INFO):
        assert provider.complete(SECRET_PROMPT) == "pong"
    received = [r.getMessage() for r in caplog.records if "response received" in r.getMessage()]
    assert len(received) == 1
    line = received[0]
    assert "prompt_tokens=11" in line and "completion_tokens=3" in line
    assert "latency_ms=" in line
    text = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET_PROMPT not in text and KEY not in text and "pong" not in text


@pytest.mark.parametrize("index", [0, 1], ids=["openai_compatible", "gemini"])
def test_a_response_without_usage_still_succeeds(caplog, index) -> None:
    provider = _providers(usage=False)[index]
    with caplog.at_level(logging.INFO):
        assert provider.complete("x") == "pong"
    line = next(r.getMessage() for r in caplog.records if "response received" in r.getMessage())
    assert "prompt_tokens=None" in line
