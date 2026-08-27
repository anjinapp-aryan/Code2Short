"""Phase 5: multi-provider LLM abstraction.

Every test here runs WITHOUT credentials and WITHOUT network. The
OpenAI-compatible provider is exercised against a real local HTTP stub that
speaks `/v1/chat/completions`, which is the same wire format OmniRoute,
Ollama, vLLM and OpenAI expose - so passing here means the provider works
against any of them.

Real-provider tests (Gemini, a live OmniRoute) live in
`test_llm_providers_integration.py` and are marked, never silently skipped
into a false pass.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from code2shorts.ai.providers import (
    KNOWN_PROVIDERS,
    MockLLMProvider,
    OpenAICompatiblePermanentError,
    OpenAICompatibleProvider,
    OpenAICompatibleTransientError,
    ProviderConfigurationError,
    build_llm_provider,
)
from code2shorts.config import Settings
from code2shorts.llm.provider import LLMProvider


# ---- a real, local OpenAI-compatible stub ---------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    reply = "stub reply"
    status = 200
    received: list[dict] = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append(
            {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
        )
        if type(self).status != 200:
            self.send_response(type(self).status)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"stub failure"}}')
            return
        payload = {
            "id": "stub",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": type(self).reply},
                 "finish_reason": "stop"}
            ],
            "model": body.get("model", "stub"),
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence the test log
        pass


@pytest.fixture
def stub_server():
    _StubHandler.received = []
    _StubHandler.reply = "stub reply"
    _StubHandler.status = 200
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}/v1", _StubHandler
    finally:
        server.shutdown()
        server.server_close()


# ---- the contract ---------------------------------------------------------


def test_every_provider_implements_the_one_shared_interface() -> None:
    """No duplicate provider abstraction was introduced."""
    from code2shorts.ai.providers.gemini import GeminiLLMProvider

    for cls in (MockLLMProvider, GeminiLLMProvider, OpenAICompatibleProvider):
        assert issubclass(cls, LLMProvider)
        assert hasattr(cls, "complete")


def test_provider_selection_lives_only_in_the_factory() -> None:
    """`if provider == ...` must not leak into workflow/domain code."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "code2shorts"
    factory = root / "ai" / "providers" / "factory.py"
    offenders = []
    for path in root.rglob("*.py"):
        if path == factory or "providers" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in ("omniroute", "OmniRoute", "api.x.ai", "x.ai"):
            # a comment mentioning it is fine; a code reference is not
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if needle.lower() in node.value.lower() and "http" in node.value.lower():
                        offenders.append(f"{path.name}: {node.value[:60]}")
    assert not offenders, f"provider-specific values leaked outside the seam: {offenders}"


# ---- OpenAI-compatible provider against a REAL local endpoint -------------


def test_openai_compatible_provider_talks_to_a_real_endpoint(stub_server) -> None:
    base_url, handler = stub_server
    handler.reply = "hello from the gateway"

    provider = OpenAICompatibleProvider(base_url=base_url, model="test-model", api_key="k")
    result = provider.complete("say hi", system="be brief")

    assert result == "hello from the gateway"
    assert len(handler.received) == 1
    request = handler.received[0]
    assert request["path"].endswith("/chat/completions")
    assert request["body"]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "say hi"},
    ]


def test_provider_reaches_any_base_url_which_is_why_omniroute_is_optional(stub_server) -> None:
    """The architectural claim, made testable: a different backend is a
    different base_url, not different code."""
    base_url, handler = stub_server
    handler.reply = "ok"
    for model in ("auto", "gemini-2.5-flash", "claude-sonnet-4.5", "qwen-max"):
        assert OpenAICompatibleProvider(base_url=base_url, model=model).complete("x") == "ok"
    assert len(handler.received) == 4
    # litellm strips its "openai/" routing prefix before the wire, so the
    # gateway receives the bare model name - which is what we want.
    assert {r["body"]["model"] for r in handler.received} == {
        "auto", "gemini-2.5-flash", "claude-sonnet-4.5", "qwen-max"
    }


def test_server_error_is_classified_transient_and_retried(stub_server) -> None:
    base_url, handler = stub_server
    handler.status = 503

    provider = OpenAICompatibleProvider(
        base_url=base_url, max_retries=2, backoff_seconds=0, timeout_seconds=5
    )
    with pytest.raises(OpenAICompatibleTransientError):
        provider.complete("x")
    # Exactly 3: our own attempts and no more. Without num_retries=0 /
    # max_retries=0 this was NINE, because litellm and the underlying
    # OpenAI client each retry on top of us - retry amplification that
    # would triple load on a rate-limited free tier.
    assert len(handler.received) == 3, (
        f"expected 3 upstream requests, got {len(handler.received)} - "
        "client-library retries are amplifying ours"
    )


def test_bad_request_is_permanent_and_never_retried(stub_server) -> None:
    base_url, handler = stub_server
    handler.status = 400

    provider = OpenAICompatibleProvider(
        base_url=base_url, max_retries=5, backoff_seconds=0, timeout_seconds=5
    )
    with pytest.raises(OpenAICompatiblePermanentError):
        provider.complete("x")
    assert len(handler.received) == 1, "a permanent failure must not be retried"


def test_empty_response_is_permanent(stub_server) -> None:
    base_url, handler = stub_server
    handler.reply = ""
    provider = OpenAICompatibleProvider(base_url=base_url, backoff_seconds=0)
    with pytest.raises(OpenAICompatiblePermanentError):
        provider.complete("x")


def test_missing_base_url_fails_loudly() -> None:
    import os

    saved = os.environ.pop("CODE2SHORTS_LLM_BASE_URL", None)
    try:
        with pytest.raises(OpenAICompatiblePermanentError, match="base_url"):
            OpenAICompatibleProvider(base_url=None)
    finally:
        if saved:
            os.environ["CODE2SHORTS_LLM_BASE_URL"] = saved


# ---- factory --------------------------------------------------------------


def test_factory_defaults_to_mock_so_nothing_silently_needs_credentials() -> None:
    provider = build_llm_provider(Settings(llm_provider="mock"))
    assert isinstance(provider, MockLLMProvider)


def test_factory_builds_openai_compatible_for_omniroute() -> None:
    provider = build_llm_provider(Settings(llm_provider="omniroute"))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.describe["base_url"] == "http://localhost:20128/v1"


def test_factory_gemini_without_a_key_fails_loudly_not_silently() -> None:
    with pytest.raises(ProviderConfigurationError, match="requires a key"):
        build_llm_provider(Settings(llm_provider="gemini", gemini_api_key=None))


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ProviderConfigurationError, match="unknown"):
        build_llm_provider(Settings(llm_provider="definitely-not-a-provider"))


def test_gemini_remains_supported() -> None:
    """Phase 5 must not remove or bypass the direct Gemini path."""
    from code2shorts.ai.providers.gemini import GeminiLLMProvider

    provider = build_llm_provider(
        Settings(llm_provider="gemini", gemini_api_key="fake-key-for-construction")
    )
    assert isinstance(provider, GeminiLLMProvider)
    assert "gemini" in KNOWN_PROVIDERS


# ---- secrets --------------------------------------------------------------


def test_api_key_never_appears_in_provenance() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://x/v1", model="m", api_key="super-secret-key"
    )
    assert "super-secret-key" not in json.dumps(provider.describe)


def test_api_key_never_appears_in_error_messages(stub_server) -> None:
    base_url, handler = stub_server
    handler.status = 400
    provider = OpenAICompatibleProvider(
        base_url=base_url, api_key="super-secret-key", backoff_seconds=0
    )
    try:
        provider.complete("x")
    except OpenAICompatiblePermanentError as error:
        assert "super-secret-key" not in str(error)
    else:
        pytest.fail("expected a permanent error")


def test_no_provider_module_logs_the_key() -> None:
    import inspect

    from code2shorts.ai.providers import gemini, openai_compatible

    for module in (gemini, openai_compatible):
        for line in inspect.getsource(module).splitlines():
            if "logger." in line:
                assert "api_key" not in line and "_api_key" not in line, module.__name__
