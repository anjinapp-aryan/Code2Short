"""build_llm_provider: the ONE place configuration chooses a provider.

The architectural point: `if provider == "gemini"` exists here and nowhere
else. Workflow nodes, validation, rendering and the domain models all take
an `LLMProvider` and never learn which one they got. That is what keeps
OmniRoute optional infrastructure rather than a dependency - swapping
backends is a config change, not a code change.

    LLM_PROVIDER=mock        -> MockLLMProvider          (default; no creds)
    LLM_PROVIDER=gemini      -> GeminiLLMProvider        (needs GEMINI_API_KEY)
    LLM_PROVIDER=omniroute   -> OpenAICompatibleProvider (local gateway)
    LLM_PROVIDER=openrouter  -> OpenAICompatibleProvider (openrouter.ai)
    LLM_PROVIDER=openai_compatible -> OpenAICompatibleProvider (any /v1)
    LLM_PROVIDER=failover    -> FailoverLLMProvider      (an ordered chain)

Adding OpenRouter cost one base URL and one key rather than a class,
because it is another OpenAI-compatible endpoint - the concrete payoff of
ADR-5.1, the same way xAI was added. A separate `OpenRouterLLMProvider`
would be a duplicate of `OpenAICompatibleProvider` with a different
constant in it.
"""

from __future__ import annotations

import logging

from code2shorts.config import reveal
from code2shorts.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

# OmniRoute's documented default. Only a default - nothing here depends on
# OmniRoute being the thing listening.
OMNIROUTE_DEFAULT_BASE_URL = "http://localhost:20128/v1"

# xAI/Grok speaks the OpenAI wire format, so supporting it costs one
# constant and no new class — the concrete payoff of ADR-5.1.
XAI_DEFAULT_BASE_URL = "https://api.x.ai/v1"

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Groq is OpenAI-compatible too: one more constant, no new class (ADR-5.1).
GROQ_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

# NVIDIA NIM (build.nvidia.com) likewise.
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

KNOWN_PROVIDERS = (
    "mock", "gemini", "omniroute", "openrouter", "openai_compatible", "xai",
    "groq", "nvidia", "failover",
)

DEFAULT_PROVIDER_ORDER = ("omniroute", "openrouter", "gemini")
"""The failover order. Deterministic: never shuffled, never load
balanced, never reordered by observed latency. OmniRoute is primary
because it is the configured gateway; the two cloud providers behind it
are fallbacks, in that order."""


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

    if name == "failover":
        return build_failover_provider(settings, **overrides)

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

    if name in (
        "omniroute", "openai_compatible", "xai", "openrouter", "groq", "nvidia"
    ):
        from code2shorts.ai.providers.openai_compatible import OpenAICompatibleProvider

        defaults = {
            "omniroute": OMNIROUTE_DEFAULT_BASE_URL,
            "xai": XAI_DEFAULT_BASE_URL,
            "openrouter": OPENROUTER_DEFAULT_BASE_URL,
            "groq": GROQ_DEFAULT_BASE_URL,
            "nvidia": NVIDIA_DEFAULT_BASE_URL,
        }
        # OpenRouter has its own default URL, so a CODE2SHORTS_LLM_BASE_URL
        # set for OmniRoute must not be inherited by it - in a failover
        # chain both are built from the same Settings, and inheriting would
        # silently point the fallback at the primary that just failed.
        # Groq likewise.
        configured_url = (
            None
            if name in ("openrouter", "groq", "nvidia")
            else settings.llm_base_url
        )
        base_url = (
            overrides.get("base_url") or configured_url or defaults.get(name)
        )
        if not base_url:
            raise ProviderConfigurationError(
                "LLM_PROVIDER=openai_compatible requires CODE2SHORTS_LLM_BASE_URL "
                "(e.g. http://localhost:11434/v1 for Ollama)."
            )

        if name == "openrouter":
            api_key = overrides.get("api_key") or reveal(settings.openrouter_api_key)
            if not api_key:
                raise ProviderConfigurationError(
                    "LLM_PROVIDER=openrouter requires a key: set "
                    "CODE2SHORTS_OPENROUTER_API_KEY (or OPENROUTER_API_KEY)."
                )
            model = overrides.get("model") or settings.openrouter_model
        elif name == "groq":
            api_key = overrides.get("api_key") or reveal(settings.groq_api_key)
            if not api_key:
                raise ProviderConfigurationError(
                    "LLM_PROVIDER=groq requires a key: set GROQ_API_KEY "
                    "(or CODE2SHORTS_GROQ_API_KEY)."
                )
            model = overrides.get("model") or settings.groq_model
        elif name == "nvidia":
            api_key = overrides.get("api_key") or reveal(settings.nvidia_api_key)
            if not api_key:
                raise ProviderConfigurationError(
                    "LLM_PROVIDER=nvidia requires a key: set NVIDIA_API_KEY "
                    "(or CODE2SHORTS_NVIDIA_API_KEY)."
                )
            model = overrides.get("model") or settings.nvidia_model
        elif name == "xai":
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


# Attempts per provider INSIDE the chain. One: the chain is the retry, so
# leaving each provider's own 3-attempt loop in place would turn a
# three-provider chain into nine upstream requests against exactly the
# rate-limited free tiers this exists to route around. The providers keep
# their backoff for direct (non-chained) use; only the chain pins it.
FAILOVER_ATTEMPTS_PER_PROVIDER = 0


def build_failover_provider(settings=None, **overrides) -> LLMProvider:
    """Build the ordered chain, skipping providers with no credential.

    Unconfigured is not an error here - it is the normal case. A developer
    with only a Gemini key gets a one-provider chain rather than a startup
    failure, and the chain still refuses to exist if NOTHING is configured,
    because that is a real misconfiguration rather than a thinner chain.

    `ProviderConfigurationError` is what "unconfigured" looks like from the
    builders above, so it is caught per provider rather than probed for
    separately - one definition of "configured", not two that can drift.
    """
    from code2shorts.ai.providers.failover import FailoverLLMProvider

    from code2shorts.config import get_settings

    settings = settings or get_settings()
    order = _provider_order(settings, overrides.get("provider_order"))

    chain: list[tuple[str, LLMProvider]] = []
    skipped: list[str] = []
    for member in order:
        if not is_provider_configured(settings, member):
            skipped.append(member)
            continue
        try:
            chain.append(
                (
                    member,
                    build_llm_provider(
                        settings,
                        provider=member,
                        max_retries=overrides.get(
                            "max_retries", FAILOVER_ATTEMPTS_PER_PROVIDER
                        ),
                        **{
                            key: value
                            for key, value in overrides.items()
                            if key in ("timeout_seconds", "canned_response")
                        },
                    ),
                )
            )
        except ProviderConfigurationError:
            # No credential. Names only, never values - this is logged.
            skipped.append(member)

    if skipped:
        logger.info("llm failover skipping unconfigured providers=%s", ",".join(skipped))
    if not chain:
        raise ProviderConfigurationError(
            "LLM_PROVIDER=failover but no provider in the chain is configured "
            f"({', '.join(order)}). Set at least one of "
            "CODE2SHORTS_LLM_API_KEY, CODE2SHORTS_OPENROUTER_API_KEY or "
            "CODE2SHORTS_GEMINI_API_KEY."
        )
    logger.info("llm failover chain=%s", ",".join(name for name, _ in chain))
    return FailoverLLMProvider(chain)


# What "configured" means, per provider, for CHAIN MEMBERSHIP.
#
# This cannot be inferred from whether the provider constructs, which was
# the first attempt and was wrong: `OpenAICompatibleProvider` has a default
# OmniRoute URL and sends a `not-needed` placeholder key, so it builds
# happily with nothing configured at all. In a chain that meant OmniRoute
# was always a member, an unconfigured deployment never reported "nothing
# is configured", and every run began with a doomed request to localhost.
#
# So membership asks for an explicit signal that a human meant to use this
# provider. Names of settings only - never values, so this is safe to log.
CHAIN_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "omniroute": ("llm_api_key", "llm_base_url"),
    "openai_compatible": ("llm_base_url",),
    "openrouter": ("openrouter_api_key",),
    "gemini": ("gemini_api_key",),
    "xai": ("xai_api_key",),
    "groq": ("groq_api_key",),
    "nvidia": ("nvidia_api_key",),
}


def is_provider_configured(settings, name: str) -> bool:
    """Whether `name` has enough configuration to be worth attempting.

    `mock` needs nothing, so it is always available - which is what makes a
    credential-free test chain possible.
    """
    name = name.strip().lower()
    if name == "mock":
        return True
    required = CHAIN_CREDENTIALS.get(name)
    if required is None:
        return False
    return any(reveal(getattr(settings, field, None)) for field in required)


def _provider_order(settings, override: str | list[str] | None) -> list[str]:
    """The chain's order, validated. Deterministic by construction: it is
    read once from configuration and never sorted, shuffled or reordered."""
    raw = override if override is not None else settings.llm_provider_order
    names = raw if isinstance(raw, list) else [
        part.strip().lower() for part in (raw or "").split(",")
    ]
    order = [name for name in names if name]
    if not order:
        order = list(DEFAULT_PROVIDER_ORDER)
    for name in order:
        if name == "failover":
            raise ProviderConfigurationError(
                "a failover chain cannot contain 'failover' - that would "
                "nest chains and lose the bound on attempts"
            )
        if name not in KNOWN_PROVIDERS:
            raise ProviderConfigurationError(
                f"unknown provider {name!r} in LLM_PROVIDER_ORDER. "
                f"Known: {', '.join(KNOWN_PROVIDERS)}"
            )
    return order
