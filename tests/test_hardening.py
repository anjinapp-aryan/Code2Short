"""Phase 4.5: resource/timeout hardening, cleanup, observability, and
security regressions for every vulnerability found in earlier phases.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from code2shorts.execution.sandbox import Workspace, run_subprocess

# ---- every external process must be invoked the same trusted way --------

TRUSTED_CALLERS = [
    ("langadapter.java.maven", "run_maven"),
    ("langadapter.java.executor", "JavaExecutor"),
    ("visualization.manim_renderer", "ManimVideoRenderer"),
    ("media.composer", "MediaComposer"),
    ("narration.tts", "SyntheticTTSProvider"),
    ("narration.tts", "SapiTTSProvider"),
    ("langadapter.java.trace.instrumenter", "JavaSourceInstrumenter"),
]


def _source_of(module_suffix: str) -> str:
    import importlib

    module = importlib.import_module(f"code2shorts.{module_suffix}")
    return inspect.getsource(module)


@pytest.mark.parametrize(("module_suffix", "symbol"), TRUSTED_CALLERS)
def test_external_tools_use_fixed_argument_lists_with_timeouts(
    module_suffix: str, symbol: str
) -> None:
    # Parse the AST rather than grepping text: these modules legitimately
    # DOCUMENT the constructs they forbid ("no shell=True", "-shortest was
    # the bug"), and a naive substring scan flags its own comments.
    source = _source_of(module_suffix)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    value = getattr(keyword.value, "value", None)
                    assert value is not True, f"{symbol} uses shell=True"
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            assert name != "system", f"{symbol} calls os.system"
    assert "timeout_seconds" in source, f"{symbol} must bound its subprocess"


def test_no_exec_or_eval_anywhere_in_the_package() -> None:
    """Whole-package AST sweep - the guarantee the security model rests on.

    Uses the AST, not text search: several modules deliberately mention
    these constructs in prose to explain why they are avoided, and a
    substring scan would flag its own documentation.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "code2shorts"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None)
                attr = getattr(func, "attr", None)
                if name in ("exec", "eval"):
                    offenders.append(f"{path.name}: {name}() call")
                if attr == "system":
                    offenders.append(f"{path.name}: os.system() call")
                for keyword in node.keywords:
                    if keyword.arg == "shell" and getattr(keyword.value, "value", None) is True:
                        offenders.append(f"{path.name}: shell=True")
    assert not offenders, f"banned construct found: {offenders}"


def test_run_subprocess_never_inherits_stdin() -> None:
    source = inspect.getsource(run_subprocess)
    assert "stdin=subprocess.DEVNULL" in source
    assert "shell" not in source.replace("shell=False", "")


def test_run_subprocess_kills_the_whole_process_tree_on_timeout() -> None:
    """Regression: Phase 1 found a real orphaned java.exe when only the
    direct child was killed."""
    source = inspect.getsource(run_subprocess)
    assert "_kill_process_tree" in source
    from code2shorts.execution import sandbox

    tree_source = inspect.getsource(sandbox._kill_process_tree)
    assert "taskkill" in tree_source and "/T" in tree_source  # Windows
    assert "killpg" in tree_source  # POSIX


# ---- cleanup -------------------------------------------------------------


def test_workspace_removes_all_contents_including_nested_dirs() -> None:
    workspace = Workspace()
    path = workspace.path
    workspace.write_file("a/b/c/deep.txt", "x")
    assert (path / "a" / "b" / "c" / "deep.txt").exists()
    workspace.cleanup()
    assert not path.exists()


def test_each_workspace_is_isolated_from_every_other() -> None:
    first, second = Workspace(), Workspace()
    try:
        assert first.path != second.path
        first.write_file("only_in_first.txt", "x")
        assert not (second.path / "only_in_first.txt").exists()
    finally:
        first.cleanup()
        second.cleanup()


def test_output_directory_is_gitignored() -> None:
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "/output/" in gitignore, "generated media must never be committed"


# ---- observability -------------------------------------------------------


def test_workflow_events_carry_correlation_ids() -> None:
    from code2shorts.workflow import WorkflowEvent, WorkflowEventType

    event = WorkflowEvent(
        type=WorkflowEventType.NODE_STARTED,
        workflow_id="wf-1",
        execution_id="exec-1",
        node_name="trace",
        artifact_id="art-1",
    )
    assert event.workflow_id and event.execution_id
    assert event.node_name == "trace"
    assert event.artifact_id == "art-1"
    assert event.timestamp is not None


def test_event_vocabulary_covers_the_pipeline_lifecycle() -> None:
    from code2shorts.workflow import WorkflowEventType

    values = {e.value for e in WorkflowEventType}
    for required in (
        "workflow_started", "workflow_completed", "workflow_failed",
        "node_started", "node_completed", "node_failed",
        "artifact_created", "validation_failed",
        "rendering_started", "rendering_completed",
    ):
        assert required in values


def test_failures_are_structured_and_identify_the_stage() -> None:
    from code2shorts.workflow import FailureKind, WorkflowError

    error = WorkflowError(
        node_name="render_video",
        kind=FailureKind.TRANSIENT,
        message="manim failed",
        detail={"stderr": "..."},
        attempt=2,
    )
    assert error.node_name == "render_video"
    assert error.kind is FailureKind.TRANSIENT
    assert error.attempt == 2


def test_no_secret_is_logged_or_defaulted_in_config() -> None:
    from code2shorts.config import Settings

    settings = Settings()
    assert settings.gemini_api_key is None, "an API key must never have a default"
    source = inspect.getsource(Settings)
    assert "sk-" not in source and "AIza" not in source


def test_gemini_provider_never_logs_the_key() -> None:
    source = _source_of("ai.providers.gemini")
    # the key is used, but never formatted into a log line
    assert "logger.info" in source
    for line in source.splitlines():
        if "logger." in line:
            assert "api_key" not in line and "_api_key" not in line


# ---- security regressions (previously discovered, must stay fixed) ------


def test_regression_srt_cue_injection_stays_fixed() -> None:
    """Phase 4.4: a payload shaped like SRT timing forged a second cue."""
    import srt

    from code2shorts.narration.subtitles import sanitize_subtitle_text

    payload = "safe\n\n2\n00:00:10,000 --> 00:00:20,000\nFORGED"
    cleaned = sanitize_subtitle_text(payload)
    assert "\n" not in cleaned
    document = srt.compose(
        [
            srt.Subtitle(
                index=1,
                start=__import__("datetime").timedelta(0),
                end=__import__("datetime").timedelta(seconds=2),
                content=cleaned,
            )
        ]
    )
    assert len(list(srt.parse(document))) == 1


def test_regression_manim_partial_movie_files_are_excluded() -> None:
    """Phase 4.1: the renderer picked the alphabetically-first .mp4, which
    avoided Manim's fragments only by luck."""
    source = _source_of("visualization.manim_renderer")
    assert "partial_movie_files" in source


def test_regression_composer_never_uses_bare_shortest() -> None:
    """Phase 4.1: bare -shortest truncated an 18s render to 5.2s."""
    # Behavioural check: build a real command and inspect it, rather than
    # grepping source that legitimately describes the old bug in a comment.
    from code2shorts.execution.sandbox import ProcessResult
    from code2shorts.media import MediaComposer

    captured: dict = {}

    def fake_run(command, cwd, timeout_seconds):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"x")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        MediaComposer(run_subprocess_fn=fake_run).compose(
            Path(tmp) / "v.mp4", Path(tmp) / "a.m4a", Path(tmp) / "f.mp4", 18.0
        )

    command = captured["command"]
    assert "-shortest" not in command, "bare -shortest truncates video to audio"
    assert "apad" in command, "audio must be padded instead"
    assert "-t" in command and command[command.index("-t") + 1] == "18.000"


def test_regression_paths_are_resolved_absolute_before_subprocess() -> None:
    """Phase 4.1: relative paths were resolved twice against subprocess cwd."""
    for module in ("visualization.manim_renderer", "media.composer", "narration.tts"):
        assert ".resolve()" in _source_of(module), module


def test_regression_visual_action_vocabulary_stays_closed() -> None:
    """Phase 4.2: a closed enum is what stops AI supplying renderer commands."""
    from pydantic import ValidationError

    from code2shorts.ai.contracts import VisualizationStepPlan

    with pytest.raises(ValidationError):
        VisualizationStepPlan(
            order=0,
            visual_action="rm -rf /",
            trace_event_index=0,
            narration_text="x",
            duration_seconds=1.0,
        )


def test_regression_trace_repr_renders_array_contents_not_identity_hash() -> None:
    """Phase 4.2: String.valueOf(int[]) gave "[I@7ad041f3"."""
    runtime = (
        Path(__file__).resolve().parents[1]
        / "src" / "code2shorts" / "langadapter" / "java" / "resources" / "Code2ShortsTrace.java"
    ).read_text(encoding="utf-8")
    assert "Arrays.toString" in runtime
    assert "public static String repr(int[] value)" in runtime
