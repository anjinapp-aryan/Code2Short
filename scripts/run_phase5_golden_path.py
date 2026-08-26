"""Phase 5 golden path: a REAL LLM drives the pipeline end to end.

    python scripts/run_phase5_golden_path.py --algorithm reverse_string
    python scripts/run_phase5_golden_path.py --provider omniroute --algorithm move_zeroes

Provider is chosen by configuration, never by code:

    --provider mock        deterministic, credential-free (default)
    --provider omniroute   local gateway at CODE2SHORTS_LLM_BASE_URL
    --provider gemini      needs CODE2SHORTS_GEMINI_API_KEY

Everything after the LLM is unchanged Phase 0-4.5.1 machinery: the LLM only
ever proposes, validation decides, and the trusted renderer executes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from code2shorts.ai.contracts import (  # noqa: E402
    ExplanationResponse,
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.ai.providers import build_llm_provider  # noqa: E402
from code2shorts.artifacts import InMemoryArtifactStore  # noqa: E402
from code2shorts.config import Settings  # noqa: E402
from code2shorts.core.models import SupportedLanguage, TraceEventType  # noqa: E402
from code2shorts.media import MediaComposer, probe_audio, probe_video  # noqa: E402
from code2shorts.media.validation import validate_final_video, validate_timeline  # noqa: E402
from code2shorts.narration import (  # noqa: E402
    SapiTTSProvider,
    SyntheticTTSProvider,
    align_narration,
    fit_plan_to_narration,
    validate_alignment,
    validate_srt,
)
from code2shorts.narration.subtitles import write_srt  # noqa: E402
from code2shorts.visualization import ManimVideoRenderer, RenderContext  # noqa: E402
from code2shorts.workflow import (  # noqa: E402
    Code2ShortsState,
    CompileNode,
    ExplainNode,
    NarrationNode,
    TraceNode,
    VisualizationPlanNode,
    Workflow,
    WorkflowEvent,
    WorkflowMetadata,
    WorkflowRequest,
    WorkflowRunner,
)
from tests.java_fixtures import load_algorithm_fixture, load_java_fixture  # noqa: E402

ALGORITHMS = {
    "reverse_string": ("fixture", "correct", "HELLO", "OLLEH"),
    "palindrome": ("algorithm", "palindrome", "RACECAR", "true"),
    "two_sum": ("algorithm", "two_sum", "9", "0,1"),
    "move_zeroes": ("algorithm", "move_zeroes", "", "1,3,12,0,0"),
    "remove_duplicates": ("algorithm", "remove_duplicates", "", "3"),
}
STEP_SECONDS = 4.0


def _load(algo: str):
    kind, variant, arg, expected = ALGORITHMS[algo]
    code = load_java_fixture(variant) if kind == "fixture" else load_algorithm_fixture(variant)
    return code, arg, expected


def _mock_responses(algo: str):
    """Deterministic stand-ins so the golden path runs credential-free.
    Built FROM the prompt's real trace indices, so they can never reference
    an event that did not happen."""

    def indices(prompt: str) -> list[int]:
        return [
            int(line.split("]")[0].strip(" ["))
            for line in prompt.splitlines()
            if line.strip().startswith("[")
        ]

    def explanation(prompt: str) -> str:
        idx = indices(prompt)
        return ExplanationResponse(
            summary=f"{algo.replace('_', ' ').title()} explained from the real execution trace.",
            learning_objectives=["read the trace", "follow the pointers"],
            steps=[
                {"order": 0, "description": "the algorithm begins",
                 "referenced_trace_event_indices": idx[:1]}
            ],
            referenced_trace_event_indices=idx[:1],
        ).model_dump_json()

    captured: dict = {}

    def plan(prompt: str) -> str:
        idx = indices(prompt)
        chosen = sorted({idx[0], *idx[1:3], idx[-1]})[:4]
        actions = [VisualAction.INTRO, VisualAction.ARRAY_ACCESS,
                   VisualAction.SWAP, VisualAction.COMPLETION]
        response = VisualizationPlanResponse(
            lesson_title=algo.replace("_", " ").title(),
            steps=[
                VisualizationStepPlan(
                    order=i, visual_action=actions[min(i, 3)], trace_event_index=e,
                    narration_text=f"Step {i + 1} of {algo.replace('_', ' ')}.",
                    duration_seconds=STEP_SECONDS,
                )
                for i, e in enumerate(chosen)
            ],
        )
        captured["plan"] = response
        return response.model_dump_json()

    def narration(prompt: str) -> str:
        return NarrationResponse(
            segments=[
                NarrationSegment(order=s.order, text=s.narration_text,
                                 visualization_step_order=s.order)
                for s in captured["plan"].steps
            ]
        ).model_dump_json()

    return explanation, plan, narration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", default="reverse_string", choices=sorted(ALGORITHMS))
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    # Free-tier models are slow: a full-trace explanation prompt routinely
    # exceeds the 30s default, so the golden path allows a longer budget.
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output-dir", type=Path, default=Path("output/phase5"))
    args = parser.parse_args()

    for tool in ("mvn", "java", "manim", "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"ERROR: {tool!r} not on PATH", file=sys.stderr)
            return 2

    out = args.output_dir / args.algorithm
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    settings = Settings(llm_provider=args.provider, ai_timeout_seconds=args.timeout)
    overrides = {k: v for k, v in
                 (("model", args.model), ("base_url", args.base_url)) if v}

    timings: dict[str, float] = {}
    events: list[WorkflowEvent] = []
    code, arg, expected = _load(args.algorithm)

    # --- build the provider(s). One seam; no provider-specific branching. --
    if args.provider == "mock":
        explanation_fn, plan_fn, narration_fn = _mock_responses(args.algorithm)
        explain_provider = build_llm_provider(settings, canned_response=explanation_fn)
        plan_provider = build_llm_provider(settings, canned_response=plan_fn)
        narration_provider = build_llm_provider(settings, canned_response=narration_fn)
        print(f"LLM provider: mock (deterministic, credential-free)")
    else:
        shared = build_llm_provider(settings, **overrides)
        explain_provider = plan_provider = narration_provider = shared
        describe = getattr(shared, "describe", {"provider": args.provider})
        print(f"LLM provider: {describe}")

    store = InMemoryArtifactStore()
    workflow = Workflow(
        "phase5",
        [
            CompileNode(),
            TraceNode(),
            ExplainNode(explain_provider, provider_name=args.provider,
                        model=args.model or "default", max_repair_attempts=2),
            VisualizationPlanNode(plan_provider, provider_name=args.provider,
                                  model=args.model or "default", max_repair_attempts=2),
            NarrationNode(narration_provider, provider_name=args.provider,
                          model=args.model or "default", max_repair_attempts=2),
        ],
    )
    state = Code2ShortsState(
        request=WorkflowRequest(
            topic=args.algorithm, language=SupportedLanguage.JAVA,
            source_files=code.source_files, entry_point=code.entry_point, input_value=arg,
        ),
        metadata=WorkflowMetadata(workflow_id="phase5", execution_id=uuid.uuid4().hex),
    )

    started = time.perf_counter()
    result = WorkflowRunner(store, event_sink=events.append).run(workflow, state)
    timings["workflow_llm"] = time.perf_counter() - started

    if not result.succeeded:
        print(f"WORKFLOW FAILED at node {result.failed_node!r}", file=sys.stderr)
        for error in result.state.errors:
            print(f"  {error.node_name}: {error.kind.value}: {error.message}", file=sys.stderr)
        return 1

    ids = result.state.artifact_ids
    print(f"artifacts: {sorted(ids)}")
    print(f"validation: {'PASS' if result.state.validation.all_passed else 'FAIL'}")
    for check in result.state.validation.results:
        mark = "PASS" if check.passed else ("FAIL(superseded by repair)" if check.superseded else "FAIL")
        print(f"  {check.stage:<24} {mark} {check.errors if check.errors else ''}")

    trace = __import__("code2shorts.core.models", fromlist=["ExecutionTrace"]).ExecutionTrace
    trace = trace.model_validate(store.get(ids["trace"]).content)
    plan = VisualizationPlanResponse.model_validate(store.get(ids["visualization_plan"]).content)
    narration = NarrationResponse.model_validate(store.get(ids["narration"]).content)
    print(f"trace: {len(trace.events)} events, {arg!r} -> {trace.output!r} (expected {expected!r})")
    if trace.output != expected:
        print("ERROR: execution output mismatch", file=sys.stderr)
        return 1

    # --- LLM provenance recorded on the artifact --------------------------
    plan_artifact = store.get(ids["visualization_plan"])
    print(f"plan producer: {plan_artifact.producer}")

    # --- speech, subtitles ------------------------------------------------
    tts = SapiTTSProvider() if SapiTTSProvider.is_available() else SyntheticTTSProvider()
    started = time.perf_counter()
    audio = {}
    for segment in narration.segments:
        r = tts.synthesize(segment.text, out / f"seg_{segment.order}")
        audio[segment.order] = (r.audio_path, r.duration_seconds)
    timings["tts"] = time.perf_counter() - started

    # Trusted code widens visual steps to hold the measured speech BEFORE
    # rendering. Widen-only: nothing is truncated, no -shortest, no video
    # stretching. A real model proposed 1.0s steps for 3.5s of narration.
    fit = fit_plan_to_narration(plan, narration, audio)
    if fit.changed:
        print(f"timeline fit: widened {len(fit.adjustments)} step(s) to hold narration; "
              f"e.g. step {fit.adjustments[0].step_order} "
              f"{fit.adjustments[0].proposed_seconds}s -> {fit.adjustments[0].fitted_seconds}s")
    plan = fit.plan

    alignment = align_narration(narration, plan, audio_by_segment=audio)
    check = validate_alignment(alignment, plan)
    print(f"alignment: {len(alignment.segments)} segments, overflow={alignment.overflow_count} "
          f"{'OK' if check.passed else 'WARN ' + str(check.errors)}")
    srt_path = write_srt(alignment, out / "subtitles.srt")
    print(f"subtitles: valid={validate_srt(srt_path.read_text(encoding='utf-8')).passed}")

    # --- real render ------------------------------------------------------
    started = time.perf_counter()
    render = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=900.0).render(
        plan, out / "render",
        RenderContext(trace=trace, source_files=dict(code.source_files),
                      entry_point=code.entry_point),
    )
    timings["manim"] = time.perf_counter() - started
    video = probe_video(Path(render.output_path))
    print(f"video: {video.width}x{video.height} {video.codec} {video.duration_seconds:.2f}s "
          f"{video.frame_count} frames")

    # --- compose + validate ----------------------------------------------
    started = time.perf_counter()
    combined = _concat_audio(alignment, out / "narration.wav")
    final = out / "final.mp4"
    MediaComposer(timeout_seconds=300.0).compose(
        Path(render.output_path), combined, final, video.duration_seconds)
    timings["ffmpeg"] = time.perf_counter() - started

    final_video = probe_video(final)
    final_audio = probe_audio(final)
    report = validate_final_video(final, expected_duration_seconds=video.duration_seconds)
    timeline = validate_timeline(alignment, final_video.duration_seconds)
    print(f"final: {final_video.width}x{final_video.height} {final_video.codec} "
          f"{final_video.duration_seconds:.2f}s | audio {final_audio.codec} "
          f"{final_audio.sample_rate}Hz {final_audio.duration_seconds:.2f}s")
    print(f"media validation : {'PASS' if report.passed else 'FAIL ' + str(report.errors)}")
    print(f"timeline validate: {'PASS' if timeline.passed else 'FAIL ' + str(timeline.errors)}")

    # Per-node timing derived from the workflow event stream, which already
    # carries timestamps — no new instrumentation needed.
    starts: dict[str, object] = {}
    for event in events:
        if event.type.value == "node_started":
            starts[event.node_name] = event.timestamp
        elif event.type.value == "node_completed" and event.node_name in starts:
            timings[f"node:{event.node_name}"] = (
                event.timestamp - starts[event.node_name]
            ).total_seconds()

    repairs = sum(1 for r in result.state.validation.results if r.superseded)
    print(f"repair attempts consumed: {repairs}")
    print("\ntimings:", {k: round(v, 2) for k, v in timings.items()})
    print(f"workflow events: {len(events)}")
    print(f"\nfinal artifact: {final.resolve()}")
    return 0 if (report.passed and timeline.passed) else 1


def _concat_audio(alignment, output_path: Path) -> Path:
    from code2shorts.execution.sandbox import run_subprocess

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path(s.audio_path).resolve() for s in alignment.segments]
    command = ["ffmpeg", "-y"]
    for path in paths:
        command += ["-i", str(path)]
    filters = [
        f"[{i}:a]adelay={int(s.start_seconds*1000)}|{int(s.start_seconds*1000)}[a{i}]"
        for i, s in enumerate(alignment.segments)
    ]
    filters.append("".join(f"[a{i}]" for i in range(len(paths)))
                   + f"amix=inputs={len(paths)}:normalize=0[out]")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]",
                "-t", f"{alignment.total_duration_seconds:.3f}", str(output_path)]
    result = run_subprocess(command, cwd=output_path.parent, timeout_seconds=300.0)
    if result.timed_out or result.returncode != 0:
        raise RuntimeError(f"audio concat failed: {result.stderr[-1000:]}")
    return output_path


if __name__ == "__main__":
    raise SystemExit(main())
