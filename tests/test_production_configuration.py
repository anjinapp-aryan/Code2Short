"""Production-safe configuration: separation, precedence, and secret leakage.

Every test is credential-free and network-free. Where a credential is
needed, a canary string stands in — and the point of most tests here is
that the canary must NOT appear somewhere.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from code2shorts.ai.providers import build_llm_provider
from code2shorts.config import (
    ConfigurationError,
    Settings,
    env_files_for,
    reveal,
    validate_configuration,
)

REPO = Path(__file__).resolve().parents[1]
CANARY = "AQ.canary-value-that-must-never-be-rendered"


def _settings(**overrides) -> Settings:
    """Settings built from explicit values only — no files, no process env."""
    overrides.setdefault("environment", "test")
    return Settings(_env_file=(), **overrides)


# ---- file separation ------------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("local", (".env", ".env.local")),
        (None, (".env", ".env.local")),
        ("production", ()),
        ("prod", ()),
        ("test", ()),
        ("ci", ()),
    ],
)
def test_only_local_environments_may_read_dotenv_files(environment, expected) -> None:
    assert env_files_for(environment) == expected


def test_production_can_never_inherit_env_local() -> None:
    """The whole point of the environment switch.

    dotenv paths resolve against the working directory, so a production
    process started inside a developer checkout would otherwise pick up
    `.env.local` and run on a personal key.
    """
    assert ".env.local" not in env_files_for("production")


def test_the_committed_templates_are_the_only_env_files_in_git() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=60
    ).stdout.split()
    env_files = {name for name in tracked if Path(name).name.startswith(".env")}
    assert env_files <= {".env.example", ".env.production.example"}, (
        f"a non-template env file is tracked by git: {env_files}"
    )


@pytest.mark.parametrize("template", [".env.example", ".env.production.example"])
def test_committed_templates_contain_placeholders_only(template) -> None:
    """A template that acquired a real key would be published on the next push."""
    import re

    path = REPO / template
    assert path.exists(), f"{template} is part of the configuration contract"
    text = path.read_text(encoding="utf-8")

    real_key_shapes = [
        r"AIza[0-9A-Za-z_-]{30,}",   # Google
        r"sk-[A-Za-z0-9]{20,}",      # OpenAI-style
        r"gsk_[A-Za-z0-9]{20,}",     # Groq
        r"xai-[A-Za-z0-9]{20,}",     # xAI
        r"AQ\.[A-Za-z0-9_-]{20,}",   # observed Google variant
    ]
    for pattern in real_key_shapes:
        assert not re.search(pattern, text), f"{template} contains real key material"

    # Every credential variable must be empty or an obvious placeholder.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if "API_KEY" not in name.upper():
            continue
        value = value.strip()
        assert value == "" or value.startswith("<"), (
            f"{template}: {name.strip()} has a non-placeholder value"
        )


@pytest.mark.parametrize(
    "candidate", [".env", ".env.local", ".env.production", ".env.staging.local"]
)
def test_every_secret_bearing_env_file_is_ignored(candidate) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", candidate], cwd=REPO, timeout=60
    )
    assert result.returncode == 0, f"{candidate} is NOT gitignored"


@pytest.mark.parametrize("template", [".env.example", ".env.production.example"])
def test_the_templates_stay_visible_to_git(template) -> None:
    """A blanket `.env.*` rule must not hide the contract itself."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", template], cwd=REPO, timeout=60
    )
    assert result.returncode != 0, f"{template} must remain committable"


# ---- precedence -----------------------------------------------------------


def test_process_environment_beats_env_files() -> None:
    """Verified against the real loader in a real subprocess, because this
    is the property production depends on."""
    probe = (
        "import sys; sys.path.insert(0, r'{src}');"
        "from code2shorts.config import Settings, reveal;"
        "s = Settings();"
        "print(s.llm_provider, reveal(s.gemini_api_key))"
    ).format(src=REPO / "src")

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO,
        env={
            "PATH": "",
            "SYSTEMROOT": "C:\\Windows",
            "CODE2SHORTS_ENV": "production",
            "LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "injected-wins",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "gemini injected-wins" in result.stdout, result.stderr[-400:]


def test_defaults_apply_when_nothing_is_configured() -> None:
    settings = _settings()
    assert settings.llm_provider == "mock", "a fresh checkout must need no credentials"
    assert settings.gemini_api_key is None


# ---- startup validation ---------------------------------------------------


def test_mock_requires_no_credential() -> None:
    assert validate_configuration(_settings(llm_provider="mock")) is not None


@pytest.mark.parametrize(
    ("provider", "variable"),
    [("gemini", "GEMINI_API_KEY"), ("xai", "XAI_API_KEY"), ("grok", "XAI_API_KEY")],
)
def test_a_provider_without_its_credential_fails_at_startup(provider, variable) -> None:
    with pytest.raises(ConfigurationError) as error:
        validate_configuration(_settings(llm_provider=provider))
    assert variable in str(error.value)
    assert f"LLM_PROVIDER={provider}" in str(error.value)


def test_a_configured_provider_starts() -> None:
    validate_configuration(_settings(llm_provider="gemini", gemini_api_key=CANARY))
    validate_configuration(_settings(llm_provider="xai", xai_api_key=CANARY))


def test_openai_compatible_requires_a_base_url() -> None:
    with pytest.raises(ConfigurationError, match="LLM_BASE_URL"):
        validate_configuration(_settings(llm_provider="openai_compatible"))


def test_omniroute_needs_no_base_url_because_it_has_a_default() -> None:
    validate_configuration(_settings(llm_provider="omniroute"))


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="unknown"):
        validate_configuration(_settings(llm_provider="totally-made-up"))


def test_a_fallback_that_cannot_run_is_rejected_at_startup() -> None:
    """Declaring a fallback you have no credential for is a configuration
    error, not a surprise at 3am."""
    with pytest.raises(ConfigurationError, match="LLM_FALLBACK_PROVIDER=gemini"):
        validate_configuration(
            _settings(llm_provider="mock", llm_fallback_provider="gemini")
        )

    validate_configuration(
        _settings(
            llm_provider="mock", llm_fallback_provider="gemini", gemini_api_key=CANARY
        )
    )


def test_an_unknown_fallback_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="unknown LLM_FALLBACK_PROVIDER"):
        validate_configuration(
            _settings(llm_provider="mock", llm_fallback_provider="nonsense")
        )


# ---- secret leakage -------------------------------------------------------


def _configured() -> Settings:
    return _settings(
        llm_provider="gemini",
        gemini_api_key=CANARY,
        xai_api_key=CANARY,
        llm_api_key=CANARY,
    )


def test_no_rendering_of_settings_exposes_a_key() -> None:
    settings = _configured()
    for rendering in (
        repr(settings),
        str(settings),
        str(settings.model_dump()),
        settings.model_dump_json(),
    ):
        assert CANARY not in rendering


def test_a_validation_error_never_carries_the_secret() -> None:
    with pytest.raises(ConfigurationError) as error:
        validate_configuration(_settings(llm_provider="xai", gemini_api_key=CANARY))
    assert CANARY not in str(error.value)
    assert "XAI_API_KEY is required" in str(error.value)


def test_provider_provenance_excludes_the_key() -> None:
    """`describe` is stored on artifacts, so it is the highest-risk sink."""
    from code2shorts.ai.providers import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        base_url="http://x/v1", model="m", api_key=CANARY
    )
    assert CANARY not in json.dumps(provider.describe)


def test_a_key_never_reaches_an_artifact() -> None:
    from code2shorts.artifacts.models import Artifact, ArtifactType

    artifact = Artifact.create(
        type=ArtifactType.VISUALIZATION_PLAN,
        producer="gemini:gemini-3.6-flash",
        content={"lesson_title": "T", "steps": []},
    )
    assert CANARY not in json.dumps(artifact.model_dump(mode="json"))


def test_a_key_never_reaches_workflow_state() -> None:
    from code2shorts.core.models import SupportedLanguage
    from code2shorts.workflow import Code2ShortsState, WorkflowMetadata, WorkflowRequest

    state = Code2ShortsState(
        request=WorkflowRequest(
            topic="reverse_string",
            language=SupportedLanguage.JAVA,
            source_files={"A.java": "class A {}"},
            entry_point="A",
        ),
        metadata=WorkflowMetadata(workflow_id="w", execution_id="e"),
    )
    assert CANARY not in state.model_dump_json()


def test_no_prompt_can_carry_a_credential() -> None:
    """A prompt goes to a third party. AST-checked so it cannot regress."""
    import ast
    import inspect

    from code2shorts.workflow import nodes

    module = ast.parse(inspect.getsource(nodes))
    builders = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("build_")
        and node.name.endswith("_prompt")
    ]
    assert builders, "prompt builders not found — has the module been renamed?"
    for builder in builders:
        for node in ast.walk(builder):
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "environ", "getenv", "api_key", "gemini_api_key",
                    "xai_api_key", "llm_api_key",
                }, f"{builder.name} can reach a credential"


def test_no_module_logs_a_credential() -> None:
    import ast
    from pathlib import Path as _Path

    root = _Path(REPO) / "src" / "code2shorts"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id not in ("logger", "logging", "print"):
                continue
            for argument in ast.walk(node):
                if isinstance(argument, ast.Attribute) and "api_key" in argument.attr:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"a credential may be logged at {offenders}"


def test_reveal_is_the_only_unwrapping_path_and_it_works() -> None:
    settings = _configured()
    assert reveal(settings.gemini_api_key) == CANARY
    assert reveal(None) is None
    assert reveal("already-a-string") == "already-a-string"


# ---- credential vendor classification (Phase 5.1) -------------------------

FAKE_GEMINI_SECRET = "test-gemini-secret"
FAKE_XAI_SECRET = "test-xai-secret"


@pytest.mark.parametrize(
    ("value", "vendor"),
    [
        ("xai-0123456789abcdef", "xai"),
        ("gsk_0123456789abcdef", "groq"),
        ("AIzaSyA0123456789abcdef", "google"),
        ("sk-ant-0123456789", "anthropic"),
        ("sk-0123456789abcdef", "openai"),
        (FAKE_XAI_SECRET, "unknown"),
        (None, "missing"),
        ("", "missing"),
    ],
)
def test_a_credential_is_classified_by_its_vendor_prefix(value, vendor) -> None:
    from code2shorts.config import classify_credential

    assert classify_credential(value) == vendor


def test_a_groq_key_is_never_reported_as_an_xai_credential() -> None:
    """The exact confusion that cost a gate: a `gsk_` key configured as
    XAI_API_KEY. api.x.ai answers "Incorrect API key provided", which reads
    like an expired key rather than the wrong vendor entirely."""
    from code2shorts.config import classify_credential

    assert classify_credential("gsk_looks_like_a_key_but_is_groq") == "groq"
    assert classify_credential("gsk_looks_like_a_key_but_is_groq") != "xai"


def test_classification_never_returns_the_credential_itself() -> None:
    from code2shorts.config import classify_credential

    for value in ("xai-super-secret-value", "gsk_super-secret-value"):
        assert value not in classify_credential(value)


def test_classification_is_advisory_and_does_not_block_the_factory() -> None:
    """Prefixes are vendor conventions, not guarantees. Hard-rejecting on
    one would break the day a vendor changes its format, so the factory
    still builds the provider and lets the API be the authority."""
    from code2shorts.ai.providers import OpenAICompatibleProvider

    provider = build_llm_provider(
        _settings(llm_provider="xai", xai_api_key="gsk_wrong_vendor_but_still_built")
    )
    assert isinstance(provider, OpenAICompatibleProvider)


# ---- provider architecture regression locks -------------------------------


def test_xai_reuses_the_shared_openai_compatible_provider() -> None:
    from code2shorts.ai.providers import OpenAICompatibleProvider

    provider = build_llm_provider(
        _settings(llm_provider="xai", xai_api_key=FAKE_XAI_SECRET)
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.describe["base_url"] == "https://api.x.ai/v1"


def test_no_per_vendor_provider_class_was_ever_created() -> None:
    """ADR-5.1: a new OpenAI-compatible backend costs a URL, not a class."""
    import code2shorts.ai.providers as providers

    for forbidden in (
        "GrokProvider", "XAIProvider", "OmniRouteProvider", "OpenRouterProvider",
        "OpenRouterLLMProvider", "OmniRouteLLMProvider",
    ):
        assert not hasattr(providers, forbidden), f"{forbidden} must not exist"

    from pathlib import Path as _Path

    files = {p.name for p in (_Path(REPO) / "src/code2shorts/ai/providers").glob("*.py")}
    # `failover.py` is the ONE addition, and it is not a vendor: it holds
    # no base URL, no key and no wire format - it walks a list of providers
    # built by the factory. Phase 6.5.4 added OpenRouter to the chain and
    # it still cost a URL and a key rather than a module, which is the
    # property this exact-match assertion exists to keep.
    assert files == {"__init__.py", "factory.py", "failover.py", "gemini.py",
                     "mock.py", "openai_compatible.py"}, (
        f"unexpected provider module: {files}"
    )


@pytest.mark.parametrize(
    ("provider", "expected_base_url"),
    [
        ("xai", "https://api.x.ai/v1"),
        ("omniroute", "http://localhost:20128/v1"),
    ],
)
def test_each_backend_is_only_a_base_url(provider, expected_base_url) -> None:
    built = build_llm_provider(
        _settings(llm_provider=provider, xai_api_key=FAKE_XAI_SECRET)
    )
    assert built.describe["base_url"] == expected_base_url


# ---- provider identity: no silent switching -------------------------------


def test_configuring_a_fallback_does_not_change_which_provider_is_built() -> None:
    """Fallback declares intent. It must NEVER quietly become the provider
    that actually runs, because the artifact would record a producer that
    did not produce it."""
    from code2shorts.ai.providers.gemini import GeminiLLMProvider
    from code2shorts.ai.providers.mock import MockLLMProvider

    built = build_llm_provider(
        _settings(
            llm_provider="mock",
            llm_fallback_provider="gemini",
            gemini_api_key=FAKE_GEMINI_SECRET,
        )
    )
    assert isinstance(built, MockLLMProvider)
    assert not isinstance(built, GeminiLLMProvider)


def test_a_failing_provider_raises_rather_than_switching() -> None:
    """No automatic runtime failover (ADR-5.8). A provider that cannot
    answer must surface the failure, not hand the work to another vendor."""
    from code2shorts.ai.providers import (
        OpenAICompatibleError,
        OpenAICompatibleProvider,
    )

    def always_fails(**_kwargs):
        raise RuntimeError("upstream is down")

    provider = OpenAICompatibleProvider(
        base_url="https://api.x.ai/v1",
        model="grok-4.1-fast",
        api_key=FAKE_XAI_SECRET,
        max_retries=0,
        backoff_seconds=0,
        completion_fn=always_fails,
    )
    with pytest.raises(OpenAICompatibleError):
        provider.complete("x")
    # identity is unchanged by the failure
    assert provider.describe["base_url"] == "https://api.x.ai/v1"


def test_no_module_implements_automatic_provider_failover() -> None:
    """A structural lock on the deferred decision: nothing may call the
    factory a second time to substitute a provider mid-run."""
    import ast
    from pathlib import Path as _Path

    root = _Path(REPO) / "src" / "code2shorts"
    factory = root / "ai" / "providers" / "factory.py"
    # The web app's pipeline wiring is a COMPOSITION ROOT: it builds the
    # provider once, at the moment a pipeline is assembled, exactly as the
    # golden-path scripts do outside src/. That is not the thing this test
    # forbids. What it forbids is a SECOND construction used to substitute
    # a provider after one has failed, which is checked below.
    composition_roots = {root / "webapp" / "pipeline.py"}
    callers: list[str] = []
    rooted: list[ast.Call] = []
    for path in root.rglob("*.py"):
        if path == factory:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name != "build_llm_provider":
                    continue
                if path in composition_roots:
                    rooted.append(node)
                    continue
                callers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not callers, (
        "provider construction outside the factory seam — a failover path "
        f"may have been added without an ADR: {callers}"
    )

    # A composition root builds ONCE, and never from inside an exception
    # handler or a loop — either would be a substitution path wearing a
    # composition root's clothes.
    assert len(rooted) == 1, f"a composition root built the provider {len(rooted)} times"
    for path in composition_roots:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ExceptHandler, ast.While, ast.For)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and (
                    getattr(inner.func, "id", None) == "build_llm_provider"
                ):
                    raise AssertionError(
                        f"{path.name}:{inner.lineno} builds a provider inside a "
                        "retry/handler — that is substitution, not composition"
                    )


def test_the_producer_recorded_on_an_artifact_names_the_real_provider() -> None:
    """Artifact lineage must never claim a provider that did not run."""
    from code2shorts.artifacts.models import Artifact, ArtifactType

    artifact = Artifact.create(
        type=ArtifactType.VISUALIZATION_PLAN,
        producer="gemini:gemini-3.6-flash",
        content={"lesson_title": "T", "steps": []},
    )
    assert artifact.producer == "gemini:gemini-3.6-flash"
    assert "mock" not in artifact.producer


# ---- blank entries must not shadow a real credential ----------------------


def test_a_blank_entry_does_not_hide_a_real_key_further_down_the_file(tmp_path) -> None:
    """Regression, found with a real key.

    pydantic-settings resolves AliasChoices INSIDE the dotenv source, so
    the first alias present in the file wins even when its value is empty.
    A `.env.local` started from `.env.example` keeps
    `CODE2SHORTS_LLM_API_KEY=`, which consumed the slot and hid a real
    `OMNI_ROUTE_LLM_API_KEY` further down the same file — the key looked
    configured and failed at the wire.
    """
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "CODE2SHORTS_LLM_API_KEY=\n"
        "LLM_PROVIDER=omniroute\n"
        f"OMNI_ROUTE_LLM_API_KEY={FAKE_XAI_SECRET}\n",
        encoding="utf-8",
    )

    probe = (
        "import sys; sys.path.insert(0, r'{src}');"
        "from code2shorts.config import Settings, reveal;"
        "print('KEY=', reveal(Settings().llm_api_key))"
    ).format(src=REPO / "src")

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,  # so this .env.local is the one that loads
        env={"PATH": "", "SYSTEMROOT": "C:\\Windows"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert f"KEY= {FAKE_XAI_SECRET}" in result.stdout, (
        f"blank entry shadowed the real key. stdout={result.stdout!r} "
        f"stderr={result.stderr[-300:]!r}"
    )


def test_a_blank_entry_is_treated_as_absent_not_as_empty_string() -> None:
    settings = _settings()
    assert settings.llm_api_key is None
    assert reveal(settings.llm_api_key) is None


def test_recovery_never_lets_production_read_a_file(tmp_path) -> None:
    """The fallback consults env files — it must respect the environment
    switch, or it would reintroduce the leak ADR-5.8 closed."""
    (tmp_path / ".env.local").write_text(
        f"OMNI_ROUTE_LLM_API_KEY={FAKE_XAI_SECRET}\n", encoding="utf-8"
    )
    probe = (
        "import sys; sys.path.insert(0, r'{src}');"
        "from code2shorts.config import Settings, reveal;"
        "print('KEY=', reveal(Settings().llm_api_key))"
    ).format(src=REPO / "src")

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env={"PATH": "", "SYSTEMROOT": "C:\\Windows", "CODE2SHORTS_ENV": "production"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "KEY= None" in result.stdout, (
        "production recovered a credential from a dotenv file — ADR-5.8 broken"
    )
