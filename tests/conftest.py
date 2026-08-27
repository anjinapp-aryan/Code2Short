"""Test isolation from developer credentials.

The audited problem: `Settings` reads `.env.local`, and pytest runs with the
repository as its working directory, so **the unit suite was loading a
developer's real API keys**. Consequences, all observed rather than
theoretical:

* `pytest` behaved differently on a machine with `.env.local` than on a
  clean checkout — the opposite of deterministic.
* `test_no_secret_is_logged_or_defaulted_in_config` asserted the key was
  `None`, passed on CI-like machines, and failed on a configured one — while
  printing the real key into the pytest output.
* Any test constructing a provider from `Settings()` could have made a live
  billed network call by accident.

So: every test that is NOT marked `integration` runs against configuration
defaults, with env files and credential variables removed. Integration
tests are left alone — reaching a real provider is their entire purpose.

This is isolation, not mocking: `Settings` is unchanged and still resolves
exactly as production does. Only the *inputs* visible to a unit test are
narrowed.
"""

from __future__ import annotations

import pytest

CREDENTIAL_ENV_VARS = (
    "GEMINI_API_KEY",
    "CODE2SHORTS_GEMINI_API_KEY",
    "XAI_API_KEY",
    "CODE2SHORTS_XAI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CODE2SHORTS_LLM_API_KEY",
    "LLM_PROVIDER",
    "CODE2SHORTS_LLM_PROVIDER",
    "CODE2SHORTS_LLM_BASE_URL",
    "LLM_FALLBACK_PROVIDER",
    "CODE2SHORTS_LLM_FALLBACK_PROVIDER",
)


@pytest.fixture(autouse=True)
def isolate_unit_tests_from_developer_credentials(request, monkeypatch):
    """Unit tests see configuration defaults and nothing else."""
    if request.node.get_closest_marker("integration"):
        return

    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    # The supported switch, not a private hook: CODE2SHORTS_ENV=test makes
    # Settings read no dotenv file at all. Production uses the same
    # mechanism, so this exercises the real code path rather than bypassing
    # it. monkeypatch restores the previous value afterwards.
    monkeypatch.setenv("CODE2SHORTS_ENV", "test")
