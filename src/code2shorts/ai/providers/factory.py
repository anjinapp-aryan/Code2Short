"""build_llm_provider: the ONE place configuration chooses a provider.

The architectural point: `if provider == "gemini"` exists here and nowhere
else. Workflow nodes, validation, rendering and the domain models all take
an `LLMProvider` and never learn which one they got. That is what keeps
OmniRoute optional infrastructure rather than a dependency - swapping
backends is a config change, not a code change.

    LLM_PROVIDER=mock        -> MockLLMProvider          (default; no creds)
    LLM_PROVIDER=gemini      -> GeminiLLMProvider        (needs GEMINI_API_KEY)
    LLM_PROVIDER=omniroute   -> OpenAICompatibleProvider (local gateway)
    LLM_PROVIDER=openai_compatible -> OpenAICompatibleProvider (any /v1)
"""

from __future__ import annotations

from code2shorts.config import reveal
from code2shorts.llm.provider import LLMProvider

# OmniRoute's documented default. Only a default - nothing here depends on
# OmniRoute being the thing listening.
OMNIROUTE_DEFAULT_BASE_URL = "http://localhost:20128/v1"

# xAI/Grok speaks the OpenAI wire format, so supporting it costs one
# constant and no new class — the concrete payoff of ADR-5.1.
XAI_DEFAULT_BASE_URL = "https://api.x.ai/v1"

KNOWN_PROVIDERS = ("mock", "gemini", "omniroute", "openai_compatible", "xai")


class ProviderConfigurationError(Exception):
    """Configuration cannot produce a usable provider. Never contains a key."""


def build_llm_provider(settings=None, **overrides) -> LLMProvider:
    """Construct the configured provider.

    Defaults to the mock provider so that importing and running the
    pipeline never silently requires credentials - a missing key must be an
    explicit, reported failure, never an accidental network call.
    """
    from code2shorts.config import get_settings

    settings = settings or get_settings()
    name = (overrides.get("provider") or settings.llm_provider or "mock").strip().lower()

    if name == "mock":
        from code2shorts.ai.providers.mock import MockLLMProvider

        return MockLLMProvider(canned_response=overrides.get("canned_response"))

    if name == "gemini":
        from code2shorts.ai.providers.gemini import GeminiLLMProvider

        api_key = overrides.get("api_key") or reveal(settings.gemini_api_key)
        if not api_key:
            raise ProviderConfigurationError(
                "LLM_PROVIDER=gemini requires a key: set CODE2SHORTS_GEMINI_API_KEY "
                "or GEMINI_API_KEY. Unit tests should use LLM_PROVIDER=mock."
            )
        return GeminiLLMProvider(
            api_key=api_key,
            model=overrides.get("model") or settings.gemini_model,
            timeout_seconds=overrides.get("timeout_seconds", settings.ai_timeout_seconds),
            max_retries=overrides.get("max_retries", settings.ai_max_retries),
        )

    if name in ("omniroute", "openai_compatible", "xai"):
        from code2shorts.ai.providers.openai_compatible import OpenAICompatibleProvider

        defaults = {
            "omniroute": OMNIROUTE_DEFAULT_BASE_URL,
            "xai": XAI_DEFAULT_BASE_URL,
        }
        base_url = (
            overrides.get("base_url") or settings.llm_base_url or defaults.get(name)
        )
        if not base_url:
            raise ProviderConfigurationError(
                "LLM_PROVIDER=openai_compatible requires CODE2SHORTS_LLM_BASE_URL "
                "(e.g. http://localhost:11434/v1 for Ollama)."
            )

        if name == "xai":
            # A remote paid endpoint, unlike a keyless local gateway: a
            # missing key must fail loudly here rather than reach the wire
            # as the "not-needed" placeholder and come back as a 401.
            api_key = (
                overrides.get("api_key")
                or reveal(settings.xai_api_key)
                or reveal(settings.llm_api_key)
            )
            if not api_key:
                raise ProviderConfigurationError(
                    "LLM_PROVIDER=xai requires a key: set XAI_API_KEY "
                    "(or CODE2SHORTS_XAI_API_KEY)."
                )
            model = overrides.get("model") or settings.xai_model
        else:
            api_key = overrides.get("api_key") or reveal(settings.llm_api_key)
            model = overrides.get("model") or settings.llm_model

        return OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=overrides.get("timeout_seconds", settings.ai_timeout_seconds),
            max_retries=overrides.get("max_retries", settings.ai_max_retries),
        )

    raise ProviderConfigurationError(
        f"unknown LLM_PROVIDER {name!r}. Known: {', '.join(KNOWN_PROVIDERS)}"
    )
