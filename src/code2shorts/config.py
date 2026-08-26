"""Centralized settings. No hardcoded API keys anywhere in this codebase."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODE2SHORTS_", env_file=".env", extra="ignore"
    )

    llm_provider: str = "mock"
    llm_model: str = "llama3.1"
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
    llm_api_key: str | None = None

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"
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


def get_settings() -> Settings:
    return Settings()
