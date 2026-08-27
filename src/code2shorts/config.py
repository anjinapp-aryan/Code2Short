"""Centralized settings. No hardcoded API keys anywhere in this codebase.

Environment selection
---------------------
`CODE2SHORTS_ENV` decides which files may be read, and nothing else:

    local (default)  .env, then .env.local   — a developer's real keys
    test             no files at all         — deterministic, credential-free
    production       no files at all         — runtime injection only

Production reads **no dotenv file**. That is deliberate: dotenv paths are
resolved relative to the working directory, so a production process started
inside a developer checkout would otherwise silently pick up `.env.local`
and run on someone's personal key. Making production file-free removes the
possibility rather than documenting a rule nobody can enforce.

Precedence, in every environment (verified by test, not assumed):

    process environment  >  env files  >  field defaults
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_ENV_FILES = (".env", ".env.local")
"""`.env.local` is read AFTER `.env`, so it wins — it is the conventional
place for a developer's real keys, and it is gitignored. Both are optional."""

FILE_FREE_ENVIRONMENTS = ("production", "prod", "test", "ci")


def env_files_for(environment: str | None) -> tuple[str, ...]:
    """Which dotenv files this environment is allowed to read."""
    name = (environment or "local").strip().lower()
    return () if name in FILE_FREE_ENVIRONMENTS else LOCAL_ENV_FILES


def _readable_environment(environment: str | None) -> dict[str, str]:
    """Every name this process may legitimately read, process env last so
    it keeps winning. Used only to recover a value the alias resolution
    dropped; never to widen what production is allowed to read."""
    values: dict[str, str] = {}
    for name in env_files_for(environment):
        path = Path(name)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    values.update(os.environ)
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODE2SHORTS_",
        env_file=LOCAL_ENV_FILES,
        extra="ignore",
    )

    def __init__(self, **values):
        # Resolved per instantiation rather than at import, so a process (or
        # a test) that sets CODE2SHORTS_ENV is honoured without reimporting.
        if "_env_file" not in values:
            values["_env_file"] = env_files_for(os.environ.get("CODE2SHORTS_ENV"))
        super().__init__(**values)

    @model_validator(mode="after")
    def _recover_credentials_shadowed_by_blank_entries(self):
        """Fall through to an alternate variable name when the preferred one
        is blank.

        pydantic-settings resolves `AliasChoices` *inside* the dotenv
        source, so the first name that appears in the file wins even when
        its value is empty. A `.env.local` started from `.env.example`
        keeps lines like `CODE2SHORTS_LLM_API_KEY=`, which then consumed
        the slot and hid a real `OMNI_ROUTE_LLM_API_KEY` further down the
        same file — the key looked configured and failed at the wire.

        Dropping blank values from the source is not enough, because by
        then the alternative has already been discarded. So the remaining
        names are consulted directly here.
        """
        fallbacks = {
            "llm_api_key": ("OMNI_ROUTE_LLM_API_KEY", "OMNIROUTE_API_KEY", "LLM_API_KEY"),
            "gemini_api_key": ("GEMINI_API_KEY",),
            "xai_api_key": ("XAI_API_KEY",),
            "llm_base_url": ("OMNI_ROUTE_LLM_BASE_URL", "OMNIROUTE_BASE_URL", "LLM_BASE_URL"),
        }
        available = _readable_environment(self.environment)
        for field, names in fallbacks.items():
            if reveal(getattr(self, field)):
                continue
            for name in names:
                value = available.get(name)
                if value and value.strip():
                    annotation = type(self).model_fields[field].annotation
                    raw = value.strip()
                    object.__setattr__(
                        self,
                        field,
                        SecretStr(raw) if "SecretStr" in str(annotation) else raw,
                    )
                    break
        return self

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        """Drop blank assignments so they cannot shadow a real value.

        `.env.local` files are usually started by copying `.env.example`,
        which leaves lines like `CODE2SHORTS_LLM_API_KEY=` behind. Because
        that name is the FIRST choice in this field's alias list, the empty
        string won the race and a real `OMNI_ROUTE_LLM_API_KEY` further down
        the same file was never consulted — a key that is present but
        silently unread, which is worse than one that is missing.

        Treating "" as absent makes a blank line mean "not configured",
        which is what someone writing it intends.
        """

        def _without_blanks(source):
            def call():
                return {
                    key: value
                    for key, value in source().items()
                    if not (isinstance(value, str) and not value.strip())
                }

            return call

        return (
            init_settings,
            _without_blanks(env_settings),
            _without_blanks(dotenv_settings),
            file_secret_settings,
        )

    environment: str = Field(
        default="local",
        validation_alias=AliasChoices("CODE2SHORTS_ENV", "APP_ENV"),
        description="local | test | production. Only controls file loading.",
    )

    # A few settings accept the vendor's conventional unprefixed name as
    # well as the CODE2SHORTS_ one, because that is what people actually
    # have in a .env.local and what other tools set. The prefixed form is
    # listed first and therefore wins.
    llm_provider: str = Field(
        default="mock",
        validation_alias=AliasChoices("CODE2SHORTS_LLM_PROVIDER", "LLM_PROVIDER"),
    )
    llm_model: str = Field(
        default="llama3.1",
        validation_alias=AliasChoices(
            "CODE2SHORTS_LLM_MODEL", "LLM_MODEL", "OMNIROUTE_MODEL"
        ),
    )
    execution_timeout_seconds: float = 10.0
    build_timeout_seconds: float = 120.0
    java_home: str | None = None

    # Phase 2 trace limits. max_events/max_loop_iterations/max_call_depth are
    # enforced INSIDE the traced JVM (see Code2ShortsTrace.java) via
    # environment variables, not just after the fact — see
    # ARCHITECTURE_DECISIONS.md ADR-004. max_output_bytes is also enforced
    # in-JVM (a counting stdout wrapper), not in run_subprocess, since
    # run_subprocess's Popen.communicate() can't safely cap mid-stream
    # without a threaded rewrite — see SECURITY_SANDBOX.md.
    instrumenter_timeout_seconds: float = 30.0
    trace_max_events: int = 2000
    trace_max_loop_iterations: int = 1000
    trace_max_call_depth: int = 200
    trace_max_output_bytes: int = 1_000_000
    trace_max_trace_size_events: int = 2000

    # Phase 4: AI provider, repair, rendering, TTS. Credentials (e.g.
    # gemini_api_key) come from environment only — never given a default
    # value here, never logged. Feature flags let a deployment disable a
    # stage (e.g. rendering) without code changes.
    # Phase 5 multi-provider. `llm_provider` selects the implementation in
    # ai/providers/factory.py; nothing above that seam sees these values.
    # Defaults to "mock" so nothing silently requires credentials.
    llm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CODE2SHORTS_LLM_BASE_URL", "LLM_BASE_URL", "OMNIROUTE_BASE_URL"
        ),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        # `OMNI_ROUTE_LLM_API_KEY` is accepted alongside `OMNIROUTE_API_KEY`
        # because both spellings occur in real .env.local files, and a key
        # that is present but silently unread is worse than one that is
        # missing — it looks configured and fails at the wire.
        validation_alias=AliasChoices(
            "CODE2SHORTS_LLM_API_KEY",
            "LLM_API_KEY",
            "OMNIROUTE_API_KEY",
            "OMNI_ROUTE_LLM_API_KEY",
        ),
    )

    # Declared so production can state its intent and be validated for it.
    # NOTE: no automatic runtime failover is wired in — see
    # docs/PHASE_5_PROVIDER_CONFIGURATION.md. Silently switching provider
    # mid-run would change an artifact's recorded producer and hide a
    # failing primary, which is a bigger decision than a config key.
    llm_fallback_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CODE2SHORTS_LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_PROVIDER"
        ),
    )

    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CODE2SHORTS_GEMINI_API_KEY", "GEMINI_API_KEY"),
    )
    gemini_model: str = Field(
        default="gemini-1.5-flash",
        validation_alias=AliasChoices("CODE2SHORTS_GEMINI_MODEL", "GEMINI_MODEL"),
    )

    # xAI/Grok needs no provider class: api.x.ai speaks the OpenAI wire
    # format, so it is one more `base_url` (ADR-5.1). Its key is separate
    # from `llm_api_key` only so both can sit in one .env.local.
    xai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CODE2SHORTS_XAI_API_KEY", "XAI_API_KEY"),
    )
    xai_model: str = Field(
        default="grok-4.1-fast",
        validation_alias=AliasChoices("CODE2SHORTS_XAI_MODEL", "GROK_MODEL", "XAI_MODEL"),
    )
    ai_timeout_seconds: float = 30.0
    ai_max_retries: int = 2
    ai_max_repair_attempts: int = 2

    visualization_max_steps: int = 100
    narration_max_segments: int = 100
    rendering_timeout_seconds: float = 300.0
    video_resolution: str = "1080x1920"
    video_fps: int = 30
    output_directory: str = "output"

    tts_provider: str = "mock"

    feature_ai_explanation_enabled: bool = True
    feature_rendering_enabled: bool = True


def reveal(secret: SecretStr | str | None) -> str | None:
    """Unwrap a credential at the one moment it is actually needed.

    Credential fields are `SecretStr` so that printing a `Settings` object —
    a pytest assertion diff, a debug log, a traceback — renders
    `SecretStr('**********')` instead of the key. A real key was leaked into
    test output exactly that way before this existed.

    Call this only where the value goes on the wire.
    """
    if secret is None:
        return None
    return secret.get_secret_value() if isinstance(secret, SecretStr) else secret


CREDENTIAL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("xai-", "xai"),
    ("gsk_", "groq"),
    ("AIza", "google"),
    ("sk-ant-", "anthropic"),
    ("sk-", "openai"),
)


def classify_credential(secret: SecretStr | str | None) -> str:
    """Name the vendor a credential *looks* like, from its prefix alone.

    Purely advisory and deliberately not enforced by the factory: prefixes
    are vendor conventions, not guarantees, and hard-rejecting on one would
    break the day a vendor changes its format. Its job is to stop a
    confident-but-wrong diagnosis.

    It exists because a `gsk_` key — a **Groq** prefix — was configured as
    `XAI_API_KEY`. api.x.ai rejected it with "Incorrect API key provided",
    which reads like an expired key rather than the wrong vendor entirely.

    Returns a label only. Never the value.
    """
    value = reveal(secret)
    if not value:
        return "missing"
    for prefix, vendor in CREDENTIAL_PREFIXES:
        if value.startswith(prefix):
            return vendor
    return "unknown"


class ConfigurationError(Exception):
    """Configuration cannot start the application. Names the missing
    VARIABLE; never carries a value, so it is safe to log."""


# Which credential each provider requires, and the variable a human should
# actually set. `mock` is absent on purpose: it needs nothing.
PROVIDER_CREDENTIALS: dict[str, tuple[str, str]] = {
    "gemini": ("gemini_api_key", "GEMINI_API_KEY"),
    "xai": ("xai_api_key", "XAI_API_KEY"),
    "grok": ("xai_api_key", "XAI_API_KEY"),
}


def validate_configuration(settings: Settings | None = None) -> Settings:
    """Fail at startup, not at the first LLM call.

    Without this, a production deployment missing `GEMINI_API_KEY` starts
    happily, runs Maven, compiles, executes and traces the algorithm, and
    only then discovers it cannot reach a model — after minutes of work.

    Raises `ConfigurationError` naming the missing variable. The message
    never contains a credential.
    """
    settings = settings or Settings()
    provider = (settings.llm_provider or "mock").strip().lower()

    from code2shorts.ai.providers.factory import KNOWN_PROVIDERS

    if provider not in KNOWN_PROVIDERS and provider != "grok":
        raise ConfigurationError(
            f"unknown LLM_PROVIDER {provider!r}. Known: {', '.join(KNOWN_PROVIDERS)}"
        )

    def require(name: str, role: str) -> None:
        field, variable = PROVIDER_CREDENTIALS[name]
        if not reveal(getattr(settings, field)):
            raise ConfigurationError(f"{variable} is required when {role}")

    if provider in PROVIDER_CREDENTIALS:
        require(provider, f"LLM_PROVIDER={provider}")

    if provider in ("omniroute", "openai_compatible") and not settings.llm_base_url:
        # omniroute has a default URL; openai_compatible genuinely has none.
        if provider == "openai_compatible":
            raise ConfigurationError(
                "CODE2SHORTS_LLM_BASE_URL is required when "
                "LLM_PROVIDER=openai_compatible"
            )

    fallback = (settings.llm_fallback_provider or "").strip().lower()
    if fallback and fallback != "mock":
        if fallback in PROVIDER_CREDENTIALS:
            require(fallback, f"LLM_FALLBACK_PROVIDER={fallback}")
        elif fallback not in KNOWN_PROVIDERS:
            raise ConfigurationError(
                f"unknown LLM_FALLBACK_PROVIDER {fallback!r}. "
                f"Known: {', '.join(KNOWN_PROVIDERS)}"
            )

    return settings


def get_settings() -> Settings:
    return Settings()
