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
from code2shorts.media.audio_forensics import (  # noqa: E402
    measure_audio,
    validate_audio_experience,
)
from code2shorts.core.video_profile import VERTICAL_HD  # noqa: E402
from code2shorts.media.composer import (  # noqa: E402
    AUDIO_SAMPLE_RATE,
    assemble_narration_track,
)
from code2shorts.media.validation import validate_final_video, validate_timeline  # noqa: E402
from code2shorts.narration.audio import (  # noqa: E402
    BREATH_SECONDS,
    KEEP_EDGE_SILENCE_SECONDS,
    TARGET_LUFS,
    normalize_track,
)
from code2shorts.visualization.timing import (  # noqa: E402
    FINAL_FADE_OUT_SECONDS,
    STEP_FADE_IN_SECONDS,
    STEP_FADE_OUT_SECONDS,
    TITLE_FADE_IN_SECONDS,
    TITLE_HOLD_SECONDS,
)
from code2shorts.narration import (  # noqa: E402
    KokoroTTSProvider,
    to_spoken,
    SapiTTSProvider,
    SyntheticTTSProvider,
    align_narration,
    fit_plan_to_narration,
    validate_alignment,
    validate_srt,
    validate_each_moment_is_narrated_once,
    validate_narration_describes_its_moment,
    validate_teaching_synchronization,
)
from code2shorts.narration.subtitles import write_srt  # noqa: E402
from code2shorts.visualization import ManimVideoRenderer, RenderContext  # noqa: E402
from code2shorts.workflow import (  # noqa: E402
    Code2ShortsState,
    CompileNode,
    EducationalPlanNode,
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
    # Phase 6.1 section 12: the same source with an input that MISMATCHES,
    # so the early `return false` path is exercised and its narration can
    # be checked against a visible mismatch rather than a visible match.
    "palindrome_negative": ("algorithm", "palindrome", "RACE", "false"),
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

    def education(prompt: str) -> str:
        """A deterministic lesson built FROM the prompt's real indices, so
        it cites only events that actually happened. Covers exactly the
        concepts this trace's shape requires — no duration reasoning."""
        from code2shorts.ai.contracts import (
            ClaimKind,
            EducationalMoment,
            EducationalPlanResponse,
            LearningConcept,
        )

        idx = indices(prompt) or [0]
        # The prompt names the required concepts; parse them back rather
        # than hardcoding, so this mock tracks the rubric automatically.
        required = [
            concept
            for concept in LearningConcept
            if f"{concept.value}," in prompt or f"{concept.value}\n" in prompt
        ] or [LearningConcept.CORE_CONCEPT]

        moments = []
        for i, concept in enumerate(required):
            moments.append(
                EducationalMoment(
                    id=f"m{i}",
                    concept=concept,
                    claim_kind=ClaimKind.EXPLANATION,
                    explanation=f"{concept.value.replace('_', ' ')} for "
                                f"{algo.replace('_', ' ')}",
                    narration=f"Here we cover {concept.value.replace('_', ' ')}.",
                    evidence_event_indices=[idx[min(i, len(idx) - 1)]],
                    prerequisite_ids=[f"m{i - 1}"] if i else [],
                )
            )
        return EducationalPlanResponse(
            lesson_title=algo.replace("_", " ").title(),
            problem_statement=f"how {algo.replace('_', ' ')} works",
            moments=moments,
            time_complexity="O(n)",
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

    return explanation, education, plan, narration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", default="reverse_string", choices=sorted(ALGORITHMS))
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    # Free-tier models are slow: a full-trace explanation prompt routinely
    # exceeds the 30s default, so the golden path allows a longer budget.
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--tts",
        default="kokoro",
        choices=["kokoro", "sapi", "auto"],
        help="speech engine; 'auto' prefers kokoro and falls back to sapi",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="voice name for the chosen engine (default: the engine's own)",
    )
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
        explanation_fn, education_fn, plan_fn, narration_fn = _mock_responses(args.algorithm)
        explain_provider = build_llm_provider(settings, canned_response=explanation_fn)
        education_provider = build_llm_provider(settings, canned_response=education_fn)
        plan_provider = build_llm_provider(settings, canned_response=plan_fn)
        narration_provider = build_llm_provider(settings, canned_response=narration_fn)
        print(f"LLM provider: mock (deterministic, credential-free)")
    else:
        shared = build_llm_provider(settings, **overrides)
        explain_provider = education_provider = plan_provider = narration_provider = shared
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
            # Phase 5.3: plan the LESSON before planning the visuals.
            EducationalPlanNode(education_provider, provider_name=args.provider,
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
    if "educational_plan" in ids:
        from code2shorts.ai.contracts import EducationalPlanResponse
        from code2shorts.ai.education import evaluate_learning_completeness

        lesson = EducationalPlanResponse.model_validate(
            store.get(ids["educational_plan"]).content
        )
        completeness = evaluate_learning_completeness(lesson, trace)
        kinds: dict[str, int] = {}
        grounded = 0
        for moment in lesson.moments:
            kinds[moment.claim_kind.value] = kinds.get(moment.claim_kind.value, 0) + 1
            if moment.evidence_event_indices:
                grounded += 1
        print(f"lesson: {lesson.lesson_title!r} — {len(lesson.moments)} moments, "
              f"{grounded} carrying trace evidence, claims={kinds}")
        print(f"  shapes   : {[s.value for s in completeness.shapes]}")
        print(f"  covered  : {[c.value for c in completeness.covered]}")
        print(f"  missing  : {[c.value for c in completeness.missing] or 'none'}")
        print(f"  narration words: {completeness.estimated_narration_words} "
              f"(informational only — never a pass/fail criterion, ADR-5.11)")
        print(f"  completeness: {'PASS' if completeness.passed else 'FAIL'}")

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
    # Provider selection is EXPLICIT and recorded, never silent. A video
    # whose voice changes halfway through is worse than one that is merely
    # synthetic, so a requested engine that cannot run fails loudly unless
    # the caller asked for automatic fallback.
    tts = _build_tts(args)
    print(
        f"tts provider     : {type(tts).__name__} "
        f"voice={getattr(tts, '_voice', None)}"
    )
    started = time.perf_counter()
    audio = {}
    for segment in narration.segments:
        # Speak the spoken form; subtitles keep `segment.text` exactly.
        # Sent raw, "chars[0]" and "chars[6]" both synthesise as "chars"
        # and the learner is told two different states are identical.
        r = tts.synthesize(to_spoken(segment.text), out / f"seg_{segment.order}")
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
    # Semantic synchronization: matching total durations proves nothing
    # about teaching. This asserts each spoken segment occupies exactly
    # the visual moment it describes.
    sync = validate_teaching_synchronization(alignment, plan, trace)
    print(f"teaching sync    : {'PASS' if sync.passed else 'FAIL ' + str(sync.errors[:3])}")

    content = validate_narration_describes_its_moment(alignment, plan, trace)
    distinct = validate_each_moment_is_narrated_once(alignment, plan, trace)
    print(f"narration content: {'PASS' if content.passed else 'WARN ' + str(content.errors[:2])}")
    print(f"moment distinct : {'PASS' if distinct.passed else 'FAIL ' + str(distinct.errors[:2])}")

    srt_path = write_srt(alignment, out / "subtitles.srt")
    print(f"subtitles: valid={validate_srt(srt_path.read_text(encoding='utf-8')).passed}")

    # --- real render ------------------------------------------------------
    started = time.perf_counter()
    render = ManimVideoRenderer(
        fps=30, resolution=VERTICAL_HD.resolution, timeout_seconds=900.0
    ).render(
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
    combined = assemble_narration_track(alignment, out / "narration.wav")
    # Loudness-normalise the assembled track, not the individual segments:
    # normalising each one separately would flatten the natural level
    # differences between sentences and is what makes narration sound
    # machine-levelled.
    normalize_track(combined, sample_rate=AUDIO_SAMPLE_RATE)
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

    # Independent forensics: ask FFmpeg what is really in the file rather
    # than asking the pipeline whether it agrees with itself. The 22.1s
    # silent tail passed every internal check.
    forensics = measure_audio(final)
    audio_experience = validate_audio_experience(
        forensics,
        video_duration_seconds=probe_video(final).duration_seconds,
        # Every budget below is DERIVED from the pipeline's own timing
        # constants, so it tracks the design instead of being tuned until
        # the file passes.
        #
        # Tail: the last segment gets the same breath as every other, then
        # the renderer's closing fade.
        allowed_silent_tail_seconds=(
            KEEP_EDGE_SILENCE_SECONDS + BREATH_SECONDS + FINAL_FADE_OUT_SECONDS
        ),
        # Between segments: two trimmed edges, the breath, and the
        # cross-fade during which the picture is already changing.
        max_internal_gap_seconds=(
            2 * KEEP_EDGE_SILENCE_SECONDS
            + BREATH_SECONDS
            + STEP_FADE_OUT_SECONDS
            + STEP_FADE_IN_SECONDS
            + 0.15
        ),
        # The title card, which is silent because there is nothing to say
        # until the first step is drawn.
        allowed_lead_in_seconds=(
            TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS + STEP_FADE_IN_SECONDS + 0.1
        ),
        # Silence the pipeline deliberately left: from the end of each
        # segment's speech to the start of the next one's. That covers the
        # breath, the cross-fade, and any step the plan declared longer
        # than its narration needed - which the fitter keeps on purpose,
        # because pacing is content and is never trimmed to save time.
        designed_silence_intervals=_designed_silences(alignment),
        target_lufs=TARGET_LUFS,
        expected_sample_rate=AUDIO_SAMPLE_RATE,
    )
    print(
        f"audio forensics  : {forensics.integrated_lufs} LUFS, "
        f"peak {forensics.true_peak_dbtp} dBTP, "
        f"{forensics.sample_rate} Hz x{forensics.channels}, "
        f"silent tail {forensics.silent_tail_seconds:.2f}s"
    )
    print(
        "audio experience : "
        + ("PASS" if audio_experience.passed else "FAIL")
    )
    for error in audio_experience.errors:
        print(f"    - {error}")
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
    # Phase 6 section 25: the reviewer must be able to re-open and audit
    # this run later, so the inputs to every visual claim are persisted
    # next to the MP4 rather than left in the workflow's memory.
    _persist(out, trace=trace, plan=plan, alignment=alignment,
             report=report, timeline=timeline, sync=sync, content=content,
             distinct=distinct, forensics=forensics,
             audio_experience=audio_experience)

    print(f"\nfinal artifact: {final.resolve()}")
    return 0 if (report.passed and timeline.passed and audio_experience.passed) else 1





def _build_tts(args):
    """Pick the speech engine explicitly.

    Phase 6.2. Kokoro is a neural, offline, permissively licensed voice;
    SAPI remains a working fallback. Selection never happens silently
    mid-run: `--tts kokoro` fails outright if the model is missing, and
    only `--tts auto` is allowed to substitute.
    """
    if args.tts in ("kokoro", "auto"):
        # The voice comes from configuration, never from a literal here:
        # CODE2SHORTS_KOKORO_VOICE overrides it without touching source.
        voice = args.voice or Settings().kokoro_voice
        kokoro = KokoroTTSProvider(voice=voice)
        if kokoro.is_available():
            return kokoro
        if args.tts == "kokoro":
            raise SystemExit(
                "Kokoro was requested but is unavailable (model files or "
                "onnxruntime/gruut missing). Re-run with --tts auto to fall "
                "back to SAPI, or install the model."
            )
        print("  (kokoro unavailable, falling back to SAPI)")

    if SapiTTSProvider.is_available():
        return SapiTTSProvider(voice=args.voice or "Microsoft Zira Desktop")
    return SyntheticTTSProvider()


def _designed_silences(alignment) -> list[tuple[float, float]]:
    """Intervals where the pipeline intended no speech.

    Each runs from where a segment's measured audio ends to where the next
    segment's audio begins. Anything the file goes quiet for outside these
    is silence nobody asked for, which is the only kind worth reporting.
    """
    segments = sorted(alignment.segments, key=lambda s: s.start_seconds)
    intervals: list[tuple[float, float]] = []
    for current, following in zip(segments, segments[1:]):
        speech_ends = current.start_seconds + (current.audio_duration_seconds or 0.0)
        if following.start_seconds > speech_ends:
            intervals.append((speech_ends, following.start_seconds))
    return intervals


def _persist(out: Path, **artifacts) -> None:
    """Write the run's evidence beside the video, as JSON where possible.

    Deliberately best-effort per artifact: a model object that will not
    serialise must not cost the reviewer the rest of the evidence, and it
    must never fail a run whose video is already validated.
    """
    import json

    for name, value in artifacts.items():
        path = out / f"{name}.json"
        try:
            if hasattr(value, "model_dump_json"):
                path.write_text(value.model_dump_json(indent=2), encoding="utf-8")
            else:
                path.write_text(
                    json.dumps(value, indent=2, default=str), encoding="utf-8"
                )
        except (TypeError, ValueError, OSError) as error:
            print(f"  (could not persist {name}: {error})")


if __name__ == "__main__":
    raise SystemExit(main())
