"""PHASE 6.5.4 — automatic provider failover, OmniRoute → OpenRouter → Gemini.

Every test runs WITHOUT credentials and WITHOUT network access. The chain
is exercised against three REAL local HTTP stubs speaking
`/v1/chat/completions` and returning real status codes, so 429/503/401/400
travel the same path they would in production: HTTP status → litellm
exception → the provider's own `_classify` → the chain's decision. Faking
the classification would have tested this file's own opinion of what a 429
is rather than the system's.

What this file does NOT re-test: the transient/permanent taxonomy itself.
That predates this phase and is covered by `test_llm_providers.py`. The
assertions here are about the WALK - order, when it continues, when it
stops, and what it reports.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from code2shorts.ai.providers import (
    AllProvidersFailedError,
    DEFAULT_PROVIDER_ORDER,
    FailoverLLMProvider,
    OpenAICompatiblePermanentError,
    OpenAICompatibleProvider,
    ProviderConfigurationError,
    build_llm_provider,
)
from code2shorts.config import Settings
from code2shorts.llm.provider import LLMProvider

FAKE_KEY = "test-not-a-real-key"


# ---- three real endpoints, one per provider in the chain ------------------


def _make_handler():
    """A fresh handler class per endpoint, so status and call log are
    per-provider rather than shared class state."""

    class _Handler(BaseHTTPRequestHandler):
        status = 200
        reply = "ok"
        received: list[dict] = []

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            type(self).received.append({"body": body})
            if type(self).status != 200:
                self.send_response(type(self).status)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"stub failure"}}')
                return
            payload = {
                "id": "stub",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": type(self).reply},
                        "finish_reason": "stop",
                    }
                ],
                "model": body.get("model", "stub"),
            }
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    return _Handler


class _Endpoint:
    def __init__(self, name: str):
        self.name = name
        self.handler = _make_handler()
        self.handler.received = []
        self.handler.reply = f"answer from {name}"
        self._server = HTTPServer(("127.0.0.1", 0), self.handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def responds(self, status: int) -> None:
        self.handler.status = status

    @property
    def calls(self) -> int:
        return len(self.handler.received)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def provider(self) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(
            base_url=self.base_url,
            model="stub-model",
            api_key=FAKE_KEY,
            # One attempt per provider: the CHAIN is the retry. Three
            # providers each retrying three times would be nine upstream
            # requests against the rate-limited tiers this exists to route
            # around, which is the retry storm the phase forbids.
            max_retries=0,
            timeout_seconds=5.0,
        )


@pytest.fixture
def chain():
    """OmniRoute → OpenRouter → Gemini, each a real endpoint."""
    endpoints = {name: _Endpoint(name) for name in DEFAULT_PROVIDER_ORDER}
    provider = FailoverLLMProvider(
        [(name, endpoint.provider()) for name, endpoint in endpoints.items()]
    )
    try:
        yield provider, endpoints
    finally:
        for endpoint in endpoints.values():
            endpoint.close()


# ---- TEST 1 — primary success ---------------------------------------------


def test_the_primary_answers_and_no_fallback_is_attempted(chain) -> None:
    provider, endpoints = chain

    assert provider.complete("prompt") == "answer from omniroute"

    assert endpoints["omniroute"].calls == 1
    assert endpoints["openrouter"].calls == 0, "a healthy primary must not fan out"
    assert endpoints["gemini"].calls == 0
    assert provider.provider_name == "omniroute"
    assert "fallback_from" not in provider.describe


# ---- TESTS 2 & 3 — transient failures fall through -------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_transient_failure_falls_through_to_the_next_provider(chain, status) -> None:
    """Every status the phase names as fallback-eligible, end to end."""
    provider, endpoints = chain
    endpoints["omniroute"].responds(status)

    assert provider.complete("prompt") == "answer from openrouter"

    assert endpoints["omniroute"].calls == 1, "the chain is the retry, not the provider"
    assert endpoints["openrouter"].calls == 1
    assert endpoints["gemini"].calls == 0
    assert provider.provider_name == "openrouter"
    assert provider.describe["fallback_from"] == "omniroute"


def test_a_network_failure_falls_through(chain) -> None:
    """A provider whose endpoint is not listening at all - the connection
    error case, which never reaches an HTTP status."""
    provider, endpoints = chain
    endpoints["omniroute"].close()

    assert provider.complete("prompt") == "answer from openrouter"
    assert endpoints["openrouter"].calls == 1


# ---- TEST 4 — two failures, third answers ----------------------------------


def test_the_whole_chain_is_walked_in_order(chain) -> None:
    provider, endpoints = chain
    endpoints["omniroute"].responds(429)
    endpoints["openrouter"].responds(503)

    assert provider.complete("prompt") == "answer from gemini"

    assert endpoints["omniroute"].calls == 1
    assert endpoints["openrouter"].calls == 1
    assert endpoints["gemini"].calls == 1
    assert provider.provider_name == "gemini"
    assert provider.describe["fallback_from"] == "openrouter"


# ---- TEST 5 — everything fails ---------------------------------------------


def test_when_every_provider_fails_the_failure_names_all_of_them(chain) -> None:
    """No infinite retry, and no failure hidden behind another: a
    rate-limited primary must still be visible when the last provider
    happened to time out."""
    provider, endpoints = chain
    for endpoint in endpoints.values():
        endpoint.responds(503)

    with pytest.raises(AllProvidersFailedError) as raised:
        provider.complete("prompt")

    assert [name for name, _ in raised.value.failures] == list(DEFAULT_PROVIDER_ORDER)
    for name in DEFAULT_PROVIDER_ORDER:
        assert name in str(raised.value)
        assert endpoints[name].calls == 1, f"{name} was attempted more than once"


# ---- TESTS 6 & 7 — permanent failures STOP the chain -----------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_permanent_failure_stops_the_chain_immediately(chain, status) -> None:
    """A wrong key is not an outage. Falling through would turn one clear
    "your credential is invalid" into three vague failures and leave the
    real cause unreported.
    """
    provider, endpoints = chain
    endpoints["omniroute"].responds(status)

    with pytest.raises(OpenAICompatiblePermanentError):
        provider.complete("prompt")

    assert endpoints["omniroute"].calls == 1
    assert endpoints["openrouter"].calls == 0, "a bad key must not be retried elsewhere"
    assert endpoints["gemini"].calls == 0


# ---- TEST 8 — unconfigured providers are skipped ---------------------------


def _settings(**values) -> Settings:
    return Settings(_env_file=None, environment="test", **values)


def test_an_unconfigured_provider_is_skipped_not_fatal() -> None:
    """OmniRoute and Gemini configured, OpenRouter not: the chain is two
    long and nothing fails at startup."""
    built = build_llm_provider(
        _settings(
            llm_provider="failover",
            llm_base_url="http://127.0.0.1:1/v1",
            llm_api_key=FAKE_KEY,
            gemini_api_key=FAKE_KEY,
        )
    )
    assert isinstance(built, FailoverLLMProvider)
    assert built.provider_names == ["omniroute", "gemini"]


def test_a_single_configured_provider_still_makes_a_chain() -> None:
    built = build_llm_provider(
        _settings(llm_provider="failover", gemini_api_key=FAKE_KEY)
    )
    assert built.provider_names == ["gemini"]


def test_no_configured_provider_fails_immediately_and_clearly() -> None:
    """A chain of nothing is a misconfiguration, not a thinner chain."""
    with pytest.raises(ProviderConfigurationError) as raised:
        build_llm_provider(_settings(llm_provider="failover", llm_base_url=None))
    message = str(raised.value)
    assert "OPENROUTER_API_KEY" in message and "GEMINI_API_KEY" in message
    assert FAKE_KEY not in message


# ---- TEST 9 — the order is deterministic -----------------------------------


def test_the_default_order_is_omniroute_then_openrouter_then_gemini() -> None:
    assert DEFAULT_PROVIDER_ORDER == ("omniroute", "openrouter", "gemini")
    assert _settings().llm_provider_order == "omniroute,openrouter,gemini"


def test_the_order_is_the_same_on_every_build() -> None:
    """No load balancing, no latency-based reordering, no shuffling."""
    settings = _settings(
        llm_provider="failover",
        llm_base_url="http://127.0.0.1:1/v1",
        llm_api_key=FAKE_KEY,
        openrouter_api_key=FAKE_KEY,
        gemini_api_key=FAKE_KEY,
    )
    orders = {tuple(build_llm_provider(settings).provider_names) for _ in range(5)}
    assert orders == {("omniroute", "openrouter", "gemini")}


def test_a_chain_cannot_contain_itself() -> None:
    """Nesting chains would lose the bound on the number of attempts."""
    with pytest.raises(ProviderConfigurationError):
        build_llm_provider(
            _settings(
                llm_provider="failover",
                llm_provider_order="omniroute,failover",
                llm_api_key=FAKE_KEY,
            )
        )


def test_an_unknown_provider_in_the_order_is_rejected() -> None:
    with pytest.raises(ProviderConfigurationError) as raised:
        build_llm_provider(
            _settings(llm_provider="failover", llm_provider_order="omniroute,nonsense")
        )
    assert "nonsense" in str(raised.value)


# ---- TEST 10 — one response contract for all three -------------------------


def test_every_provider_in_the_chain_returns_the_same_contract(chain) -> None:
    """Provider-specific response shapes must not reach higher-level code:
    whichever endpoint answers, the caller gets `str`."""
    provider, endpoints = chain
    seen = []
    for failing in ([], ["omniroute"], ["omniroute", "openrouter"]):
        for name, endpoint in endpoints.items():
            endpoint.responds(503 if name in failing else 200)
        result = provider.complete("prompt")
        assert isinstance(result, str) and result
        seen.append(provider.provider_name)
    assert seen == ["omniroute", "openrouter", "gemini"]


def test_the_chain_is_itself_an_llm_provider(chain) -> None:
    """Nothing above the seam changes: callers still receive one
    `LLMProvider` with one `complete`."""
    provider, _endpoints = chain
    assert isinstance(provider, LLMProvider)
    assert provider.is_configured()


# ---- lineage and secrecy ---------------------------------------------------


def test_the_recorded_producer_is_the_provider_that_actually_answered(chain) -> None:
    """ADR-5.10's condition for ever allowing failover: an artifact must
    never record a producer that did not produce it."""
    provider, endpoints = chain
    assert provider.provider_name == "failover", "nothing has answered yet"

    endpoints["omniroute"].responds(429)
    provider.complete("prompt")

    assert provider.describe["provider"] == "openrouter"
    assert provider.describe["fallback_from"] == "omniroute"
    assert provider.describe["chain"] == "omniroute,openrouter,gemini"


def test_no_key_reaches_provenance_or_errors(chain) -> None:
    provider, endpoints = chain
    assert FAKE_KEY not in json_dumps(provider.describe)

    for endpoint in endpoints.values():
        endpoint.responds(503)
    with pytest.raises(AllProvidersFailedError) as raised:
        provider.complete("prompt")
    assert FAKE_KEY not in str(raised.value)


def json_dumps(value) -> str:
    return json.dumps(value)


def test_the_failover_module_never_logs_a_key_or_a_prompt() -> None:
    """AST sweep, not a text search: a module is allowed to name the thing
    it forbids in a comment."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src" / "code2shorts" / "ai" / "providers" / "failover.py"
    ).read_text(encoding="utf-8")

    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)):
            continue
        if target.value.id != "logger":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Name) and argument.id in ("prompt", "system"):
                offenders.append(ast.dump(argument))
            if isinstance(argument, ast.Attribute) and "key" in argument.attr.lower():
                offenders.append(argument.attr)
    assert not offenders, f"failover logs sensitive values: {offenders}"


# ---- Groq: one more OpenAI-compatible base_url -----------------------------


def test_groq_is_built_against_groq_with_its_own_key_and_model() -> None:
    from code2shorts.ai.providers.factory import GROQ_DEFAULT_BASE_URL

    built = build_llm_provider(
        _settings(
            llm_provider="groq",
            groq_api_key=FAKE_KEY,
            groq_model="openai/gpt-oss-120b",
            # An OmniRoute URL must not be inherited: in a chain that would
            # point the Groq fallback at the gateway that just failed.
            llm_base_url="http://127.0.0.1:1/v1",
        )
    )
    assert GROQ_DEFAULT_BASE_URL == "https://api.groq.com/openai/v1"
    assert built.describe["base_url"] == GROQ_DEFAULT_BASE_URL
    assert built.describe["model"] == "openai/gpt-oss-120b"
    assert built._api_key == FAKE_KEY


def test_groq_without_a_key_fails_loudly_rather_than_reaching_the_wire() -> None:
    with pytest.raises(ProviderConfigurationError) as raised:
        build_llm_provider(_settings(llm_provider="groq", groq_api_key=None))
    assert "GROQ_API_KEY" in str(raised.value)


def test_groq_joins_a_chain_only_when_its_key_is_set() -> None:
    order = "gemini,groq"
    with_key = build_llm_provider(
        _settings(
            llm_provider="failover",
            llm_provider_order=order,
            gemini_api_key=FAKE_KEY,
            groq_api_key=FAKE_KEY,
        )
    )
    without_key = build_llm_provider(
        _settings(llm_provider="failover", llm_provider_order=order, gemini_api_key=FAKE_KEY)
    )
    assert with_key.provider_names == ["gemini", "groq"]
    assert without_key.provider_names == ["gemini"]


def test_a_gemini_quota_429_falls_through_to_groq() -> None:
    """The case this exists for: Gemini's free-tier 429 is transient, so
    the next provider answers instead of the job failing."""
    from code2shorts.ai.providers.gemini import GeminiTransientError

    class QuotaExhausted(LLMProvider):
        provider_name = "gemini"

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt, system=None):
            raise GeminiTransientError("429 RESOURCE_EXHAUSTED")

    class Answers(LLMProvider):
        provider_name = "groq"

        def is_configured(self) -> bool:
            return True

        def complete(self, prompt, system=None):
            return "pong"

    built = FailoverLLMProvider([("gemini", QuotaExhausted()), ("groq", Answers())])
    assert built.complete("ping") == "pong"
    assert built.describe == {
        "provider": "groq", "chain": "gemini,groq", "fallback_from": "gemini",
    }


# ---- NVIDIA NIM: another OpenAI-compatible base_url ------------------------


def test_nvidia_is_built_against_nvidia_with_its_own_key_and_model() -> None:
    from code2shorts.ai.providers.factory import NVIDIA_DEFAULT_BASE_URL

    built = build_llm_provider(
        _settings(
            llm_provider="nvidia",
            nvidia_api_key=FAKE_KEY,
            nvidia_model="z-ai/glm-5.3",
            llm_base_url="http://127.0.0.1:1/v1",
        )
    )
    assert NVIDIA_DEFAULT_BASE_URL == "https://integrate.api.nvidia.com/v1"
    assert built.describe["base_url"] == NVIDIA_DEFAULT_BASE_URL
    assert built.describe["model"] == "z-ai/glm-5.3"
    assert built._api_key == FAKE_KEY


def test_nvidia_without_a_key_fails_loudly() -> None:
    with pytest.raises(ProviderConfigurationError) as raised:
        build_llm_provider(_settings(llm_provider="nvidia", nvidia_api_key=None))
    assert "NVIDIA_API_KEY" in str(raised.value)


def test_a_three_provider_chain_keeps_its_configured_order() -> None:
    built = build_llm_provider(
        _settings(
            llm_provider="failover",
            llm_provider_order="gemini,groq,nvidia",
            gemini_api_key=FAKE_KEY,
            groq_api_key=FAKE_KEY,
            nvidia_api_key=FAKE_KEY,
        )
    )
    assert built.provider_names == ["gemini", "groq", "nvidia"]
