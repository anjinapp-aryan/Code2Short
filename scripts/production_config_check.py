"""Production configuration preflight.

    python scripts/production_config_check.py

Run this as the first step of a deployment, or in a container healthcheck.
It answers one question: *can this process actually reach the model it is
configured for?* — before Maven, the JVM, Manim or FFmpeg burn minutes on
work that cannot finish.

It never prints a credential. Presence is reported as `set` / `MISSING`.
Exit code 0 = usable, 1 = misconfigured.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code2shorts.config import (  # noqa: E402
    ConfigurationError,
    classify_credential,
    Settings,
    env_files_for,
    reveal,
    validate_configuration,
)


def main() -> int:
    settings = Settings()
    environment = settings.environment
    files = env_files_for(environment)

    print(f"environment      : {environment}")
    print(f"dotenv files read: {list(files) if files else '(none — runtime injection only)'}")
    print(f"provider         : {settings.llm_provider}")
    print(f"fallback         : {settings.llm_fallback_provider or '(none)'}")

    # Report presence AND the vendor the credential looks like. A key of the
    # wrong vendor produces an auth error that reads like an expired key, so
    # naming the mismatch here saves a long misdiagnosis. Advisory only —
    # prefixes are conventions, and the API is the real authority.
    expected_vendor = {"gemini_api_key": "google", "xai_api_key": "xai"}
    for label, field in (
        ("GEMINI_API_KEY", "gemini_api_key"),
        ("XAI_API_KEY", "xai_api_key"),
        ("LLM/OMNIROUTE_API_KEY", "llm_api_key"),
    ):
        value = getattr(settings, field)
        if not reveal(value):
            print(f"  {label:<22} MISSING")
            continue
        vendor = classify_credential(value)
        wanted = expected_vendor.get(field)
        note = ""
        if wanted and vendor not in (wanted, "unknown"):
            note = f"  <-- WARNING: looks like a {vendor} key, expected {wanted}"
        print(f"  {label:<22} set (looks like: {vendor}){note}")

    if environment in ("production", "prod") and files:
        print("\nFAIL: production must not read dotenv files", file=sys.stderr)
        return 1

    try:
        validate_configuration(settings)
    except ConfigurationError as error:
        # Safe to print and to log: names the variable, never the value.
        print(f"\nFAIL: {error}", file=sys.stderr)
        return 1

    print("\nOK: configuration is usable for this provider.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
