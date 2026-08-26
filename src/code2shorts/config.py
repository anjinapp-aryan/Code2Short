"""Centralized settings. No hardcoded API keys anywhere in this codebase."""

from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # `.env.local` is read AFTER `.env`, so it wins — it is the conventional
    # place for a developer's real keys, and it is gitignored. Both files
    # are optional; nothing here requires either to exist.
    model_config = SettingsConfigDict(
        env_prefix="CODE2SHORTS_",
        env_file=(".env", ".env.local"),
        extra="ignore",
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
        validation_alias=AliasChoices("CODE2SHORTS_LLM_MODEL", "LLM_MODEL"),
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
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None

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


def get_settings() -> Settings:
    return Settings()
