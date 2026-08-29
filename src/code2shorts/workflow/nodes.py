"""Real WorkflowNode implementations wiring Phase 3 to the existing,
UNMODIFIED Phase 1/2 JavaAdapter pipeline. Nothing here changes
JavaAdapter/JavaWorkspace/Code2ShortsTrace behavior — nodes only call the
public compile()/trace() methods already proven in Phase 1/2's own tests.

State is mutated in place (plain Pydantic object mutation) rather than
copy-on-write: this means validation/error bookkeeping recorded before a
node raises is never lost, since the WorkflowRunner's `state` variable and
the node's `state` parameter are the same object.
"""

from __future__ import annotations

from pathlib import Path

from code2shorts.ai.contracts import (
    AIRequestMetadata,
    EducationalPlanResponse,
    ExplanationResponse,
    NarrationResponse,
    VisualizationPlanResponse,
)
from code2shorts.ai.repair import SemanticValidationError, generate_with_repair
from code2shorts.ai.structured import json_schema_instruction, SchemaValidationError
from code2shorts.ai.validation import validate_explanation_against_trace
from code2shorts.artifacts.models import Artifact, ArtifactType
from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import ExecutionTrace, TraceStatus, ValidationResult
from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from code2shorts.llm.provider import LLMProvider
from code2shorts.media.composer import MediaComposer, MediaCompositionFailure
from code2shorts.narration.tts import TTSFailure, TTSProvider
from code2shorts.narration.validation import validate_narration
from code2shorts.visualization.renderer import RenderContext, RenderingFailure, VideoRenderer
from code2shorts.visualization.validation import validate_visualization_plan
from code2shorts.workflow.context import WorkflowContext
from code2shorts.workflow.errors import FailureKind, NodeExecutionError
from code2shorts.workflow.events import WorkflowEvent, WorkflowEventType
from code2shorts.workflow.node import NodeResult, WorkflowNode
from code2shorts.workflow.state import Code2ShortsState


def _generated_code(state: Code2ShortsState) -> GeneratedCode:
    return GeneratedCode(
        language=state.request.language,
        source_files=state.request.source_files,
        entry_point=state.request.entry_point,
    )


class CompileNode(WorkflowNode):
    """Validate Input + Compile stages. A compile failure is a
    DETERMINISTIC failure of the user's source — never auto-retried.
    """

    name = "compile"

    def __init__(self, adapter: JavaAdapter | None = None) -> None:
        self._adapter = adapter or JavaAdapter()

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        source_artifact = Artifact.create(
            type=ArtifactType.SOURCE,
            producer=self.name,
            content={
                "source_files": state.request.source_files,
                "entry_point": state.request.entry_point,
                "language": state.request.language.value,
            },
        )
        context.artifact_store.save(source_artifact)
        state.artifact_ids["source"] = source_artifact.id

        code = _generated_code(state)
        with Workspace() as workspace:
            result = self._adapter.compile(code, workspace)

        if not result.succeeded:
            raise NodeExecutionError(
                FailureKind.DETERMINISTIC,
                "Java compilation failed",
                detail={"stderr": result.stderr[-2000:], "source_artifact_id": source_artifact.id},
            )

        # duration_seconds excluded from checksummed content — same
        # reasoning as TraceNode below (wall-clock timing, not behavior).
        compilation_artifact = Artifact.create(
            type=ArtifactType.COMPILATION,
            producer=self.name,
            content=result.model_dump(exclude={"duration_seconds"}),
            input_artifact_ids=[source_artifact.id],
            metadata={"duration_seconds": result.duration_seconds},
        )
        context.artifact_store.save(compilation_artifact)
        state.artifact_ids["compilation"] = compilation_artifact.id

        return NodeResult(
            state=state, artifact_ids_created=[source_artifact.id, compilation_artifact.id]
        )


class TraceNode(WorkflowNode):
    """Execute + Trace stages (Phase 2's JavaAdapter.trace() already
    subsumes "did it run" — see ARCHITECTURE_DECISIONS.md for why Phase 3
    doesn't add a separate ExecuteNode alongside it).

    A trace whose status isn't COMPLETED is a DETERMINISTIC failure of the
    traced program (or, for TIMEOUT/TRACE_LIMIT_REACHED, a deterministic
    property of that program+input) — the trace artifact is still saved
    (partial traces are real, useful data per Phase 2's principle), but the
    node fails so the workflow doesn't proceed to explain a broken trace.
    """

    name = "trace"

    def __init__(self, adapter: JavaAdapter | None = None) -> None:
        self._adapter = adapter or JavaAdapter()

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        code = _generated_code(state)
        with Workspace() as workspace:
            trace = self._adapter.trace(code, workspace, state.request.input_value)

        # duration_seconds is explicitly excluded from Phase 2's own
        # determinism guarantee (ARCHITECTURE_DECISIONS.md, tests/trace_helpers.py
        # assert_traces_equivalent) — including it in checksummed `content`
        # would make two runs of the identical, deterministic program
        # produce different checksums purely from wall-clock jitter. It
        # still isn't lost: it moves to `metadata`, which isn't checksummed.
        trace_content = trace.model_dump(mode="json", exclude={"duration_seconds"})
        trace_artifact = Artifact.create(
            type=ArtifactType.TRACE,
            producer=self.name,
            content=trace_content,
            # compilation is the direct parent (source -> compilation ->
            # trace -> ...), matching the linear lineage chain the rest of
            # this pipeline follows. Not source directly too — even though
            # trace() internally re-consumes the original source (Phase 2's
            # compile+execute-together design) — full ancestry is still
            # reachable by walking one more hop (trace -> compilation ->
            # source); duplicating the edge would give `source` two
            # children instead of a clean chain.
            input_artifact_ids=[
                artifact_id
                for key in ("compilation",)
                if (artifact_id := state.artifact_ids.get(key)) is not None
            ],
            metadata={"duration_seconds": trace.duration_seconds},
        )
        context.artifact_store.save(trace_artifact)
        state.artifact_ids["trace"] = trace_artifact.id

        if trace.status != TraceStatus.COMPLETED:
            raise NodeExecutionError(
                FailureKind.DETERMINISTIC,
                f"trace did not complete: status={trace.status.value}",
                detail={"trace_artifact_id": trace_artifact.id, "status": trace.status.value},
            )

        return NodeResult(state=state, artifact_ids_created=[trace_artifact.id])


DEFAULT_EXPLANATION_PROMPT_VERSION = "v2"


def build_explanation_prompt(trace: ExecutionTrace) -> str:
    lines = [
        f"Explain the execution of {trace.algorithm_name} on input {trace.input!r}, "
        f"which produced output {trace.output!r}.",
        "Reference trace events ONLY by their step_index. Do not invent events.",
        "Trace events:",
    ]
    for event in trace.events:
        lines.append(f"  [{event.step_index}] {event.event_type}: {event.description}")
    return "\n".join(lines) + json_schema_instruction(ExplanationResponse)


class ExplainNode(WorkflowNode):
    """AI explanation stage. Depends only on `LLMProvider` (Phase 0.1's
    framework-neutral seam) — never on LangGraph/LangChain/any agent
    framework. Every response is schema-validated (generate_structured)
    and semantically validated against the real trace
    (validate_explanation_against_trace) before it's trusted enough to
    become an artifact.
    """

    name = "explain"

    def __init__(
        self,
        provider: LLMProvider,
        provider_name: str = "mock",
        model: str = "mock",
        prompt_version: str = DEFAULT_EXPLANATION_PROMPT_VERSION,
        max_repair_attempts: int = 1,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._prompt_version = prompt_version
        self._max_repair_attempts = max_repair_attempts

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        trace_artifact_id = state.artifact_ids.get("trace")
        if trace_artifact_id is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT, "no trace artifact in state; run TraceNode first"
            )
        trace_artifact = context.artifact_store.get(trace_artifact_id)
        if trace_artifact is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT, f"trace artifact {trace_artifact_id} not found in store"
            )
        trace = ExecutionTrace.model_validate(trace_artifact.content)

        metadata = AIRequestMetadata(
            provider=self._provider_name,
            model=self._model,
            prompt_version=self._prompt_version,
            input_artifact_ids=[trace_artifact_id],
        )

        self._emit_ai_event(context, WorkflowEventType.AI_CALL_STARTED)
        try:
            response, history = generate_with_repair(
                self._provider,
                build_prompt=lambda: build_explanation_prompt(trace),
                response_model=ExplanationResponse,
                validate=lambda parsed: _with_subject(
                    validate_explanation_against_trace(parsed, trace), trace_artifact_id
                ),
                max_repair_attempts=self._max_repair_attempts,
            )
        except SchemaValidationError as error:
            state.validation.record(
                _validation_result_from_error("schema", trace_artifact_id, str(error))
            )
            raise NodeExecutionError(
                FailureKind.VALIDATION, f"explanation schema validation failed: {error}"
            ) from error
        except SemanticValidationError as error:
            state.validation.record(
                ValidationResult(
                    stage="semantic",
                    passed=False,
                    errors=error.errors,
                    subject_artifact_id=trace_artifact_id,
                )
            )
            raise NodeExecutionError(
                FailureKind.VALIDATION,
                "explanation failed semantic validation against the trace after repair attempts",
                detail={"errors": error.errors},
            ) from error
        finally:
            self._emit_ai_event(context, WorkflowEventType.AI_CALL_COMPLETED)

        state.validation.record_history(history)

        explanation_artifact = Artifact.create(
            type=ArtifactType.EXPLANATION,
            producer=f"{self._provider_name}:{self._model}",
            content=response.model_dump(),
            input_artifact_ids=[trace_artifact_id],
            metadata=metadata.model_dump(),
        )
        context.artifact_store.save(explanation_artifact)
        state.artifact_ids["explanation"] = explanation_artifact.id

        return NodeResult(state=state, artifact_ids_created=[explanation_artifact.id])

    def _emit_ai_event(self, context: WorkflowContext, event_type: WorkflowEventType) -> None:
        context.emit(
            WorkflowEvent(
                type=event_type,
                workflow_id=context.workflow_id,
                execution_id=context.execution_id,
                node_name=self.name,
                data={"provider": self._provider_name, "model": self._model},
            )
        )


def _validation_result_from_error(
    stage: str, subject_artifact_id: str, error: str
) -> ValidationResult:
    return ValidationResult(
        stage=stage, passed=False, errors=[error], subject_artifact_id=subject_artifact_id
    )


def _with_subject(result: ValidationResult, subject_artifact_id: str) -> ValidationResult:
    result.subject_artifact_id = subject_artifact_id
    return result


# ---------------------------------------------------------------------------
# Phase 4: visualization plan, narration, rendering, media composition.
# Same shape as ExplainNode above — AI proposes (generate_with_repair),
# validation decides (visualization/validation.py, narration/validation.py),
# trusted code executes (VideoRenderer, MediaComposer — never AI-authored
# code). See ARCHITECTURE_DECISIONS.md "Phase 4" for the full reasoning.
# ---------------------------------------------------------------------------

DEFAULT_EDUCATIONAL_PROMPT_VERSION = "v1"


def build_educational_prompt(
    trace: ExecutionTrace,
    explanation: ExplanationResponse,
    source_files: dict[str, str] | None = None,
) -> str:
    """Ask the model to plan a LESSON, not a video.

    The contract is stated explicitly rather than implied — the Phase 5
    bug was a prompt that assumed the model knew the response shape.
    """
    from code2shorts.ai.education import required_concepts

    required = required_concepts(trace)

    lines = [
        f"You are an educational planner for the algorithm "
        f"{trace.algorithm_name!r}, which was executed on input "
        f"{trace.input!r} and produced output {trace.output!r}.",
        "",
        "YOUR ROLE",
        "You decide what a learner needs to understand and in what order.",
        "You are NOT the source of execution truth. The ExecutionTrace below",
        "is authoritative and complete: it is what really happened inside a",
        "JVM. Do not invent array values, pointer positions, loop counts,",
        "branch results, variable values or final results.",
        "",
        "CLAIM KINDS - this distinction is mandatory",
        "  observed    : asserts runtime state, e.g. 'fast = 3'.",
        "                REQUIRES evidence_event_indices citing the real",
        "                trace events that show it. Every value you state",
        "                must appear in those events.",
        "  explanation : explains WHY the algorithm does something. Grounded",
        "                in the source and algorithm semantics, not in one",
        "                event. Evidence is optional.",
        "  commentary  : motivation, complexity, analogy. No evidence needed.",
        "",
        "Mark a moment 'observed' ONLY if you are citing what the trace shows.",
        "If you want to explain why a pointer moves, that is 'explanation'.",
        "",
        "TEACH THESE CONCEPTS - all of them must appear as a moment's concept:",
        "  " + ", ".join(concept.value for concept in required),
        "",
        "LENGTH",
        "There is NO duration limit and no step budget. Do not drop a",
        "conceptual transition to make the lesson shorter. A longer lesson",
        "that teaches correctly is better than a short one that misleads.",
        "Repetitive events may be summarised into one moment, but keep the",
        "boundaries that teach: the first loop check teaches 'we continue",
        "while...', the last teaches 'we stop because...'.",
        "",
        "ORDERING",
        "prerequisite_ids must refer to moments that appear EARLIER.",
        "",
        f"Summary of the execution: {explanation.summary}",
        "",
        "EXECUTION TRACE (authoritative):",
    ]
    for event in trace.events:
        variable = f" [{event.variable_name}]" if event.variable_name else ""
        line = f" line {event.line_number}" if event.line_number else ""
        lines.append(
            f"  [{event.step_index}] {event.event_type}{variable}{line}: "
            f"{event.description}"
        )

    if source_files:
        lines.append("")
        lines.append("SOURCE (for explaining WHY; line numbers are 1-based):")
        for path, text in source_files.items():
            lines.append(f"  --- {path} ---")
            for number, code in enumerate(text.splitlines(), start=1):
                lines.append(f"  {number:>3} | {code}")

    return "\n".join(lines) + json_schema_instruction(EducationalPlanResponse)


class EducationalPlanNode(WorkflowNode):
    """Plans the LESSON between explanation and visualization.

    Its output is validated twice, and both checks are about
    understanding rather than length:

      * `validate_educational_plan` - every execution-dependent claim is
        supported by real trace evidence.
      * `validate_learning_completeness` - the required concepts for this
        algorithm's shape are actually taught.

    Neither can fail a plan for being long. See ADR-5.11.
    """

    name = "educational_plan"

    def __init__(
        self,
        provider: LLMProvider,
        provider_name: str = "mock",
        model: str = "mock",
        prompt_version: str = DEFAULT_EDUCATIONAL_PROMPT_VERSION,
        max_repair_attempts: int = 1,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._prompt_version = prompt_version
        self._max_repair_attempts = max_repair_attempts

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        from code2shorts.ai.education import validate_learning_completeness
        from code2shorts.ai.grounding import validate_educational_plan

        trace_artifact_id = state.artifact_ids.get("trace")
        explanation_artifact_id = state.artifact_ids.get("explanation")
        if trace_artifact_id is None or explanation_artifact_id is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT,
                "educational planning needs both trace and explanation artifacts",
            )
        trace = ExecutionTrace.model_validate(
            context.artifact_store.get(trace_artifact_id).content
        )
        explanation = ExplanationResponse.model_validate(
            context.artifact_store.get(explanation_artifact_id).content
        )
        source_files = dict(state.request.source_files)

        metadata = AIRequestMetadata(
            provider=self._provider_name,
            model=self._model,
            prompt_version=self._prompt_version,
            input_artifact_ids=[trace_artifact_id, explanation_artifact_id],
        )

        def validate(parsed: EducationalPlanResponse) -> ValidationResult:
            grounding = validate_educational_plan(parsed, trace, source_files)
            completeness = validate_learning_completeness(parsed, trace)
            errors = list(grounding.errors) + list(completeness.errors)
            return ValidationResult(
                stage="semantic",
                passed=not errors,
                errors=errors,
                subject_artifact_id=trace_artifact_id,
            )

        self._emit_ai_event(context, WorkflowEventType.AI_CALL_STARTED)
        try:
            response, history = generate_with_repair(
                self._provider,
                build_prompt=lambda: build_educational_prompt(
                    trace, explanation, source_files
                ),
                response_model=EducationalPlanResponse,
                validate=validate,
                max_repair_attempts=self._max_repair_attempts,
            )
        except SchemaValidationError as error:
            state.validation.record(
                _validation_result_from_error("schema", trace_artifact_id, str(error))
            )
            raise NodeExecutionError(
                FailureKind.VALIDATION,
                f"educational plan schema validation failed: {error}",
            ) from error
        except SemanticValidationError as error:
            state.validation.record(
                ValidationResult(
                    stage="semantic",
                    passed=False,
                    errors=error.errors,
                    subject_artifact_id=trace_artifact_id,
                )
            )
            raise NodeExecutionError(
                FailureKind.VALIDATION,
                "educational plan failed grounding/completeness validation "
                "after repair attempts",
                detail={"errors": error.errors},
            ) from error
        finally:
            self._emit_ai_event(context, WorkflowEventType.AI_CALL_COMPLETED)

        state.validation.record_history(history)

        artifact = Artifact.create(
            type=ArtifactType.EDUCATIONAL_PLAN,
            producer=f"{self._provider_name}:{self._model}",
            content=response.model_dump(),
            input_artifact_ids=[trace_artifact_id, explanation_artifact_id],
            metadata=metadata.model_dump(),
        )
        context.artifact_store.save(artifact)
        state.artifact_ids["educational_plan"] = artifact.id

        return NodeResult(state=state, artifact_ids_created=[artifact.id])

    def _emit_ai_event(self, context: WorkflowContext, event_type: WorkflowEventType) -> None:
        context.emit(
            WorkflowEvent(
                type=event_type,
                workflow_id=context.workflow_id,
                execution_id=context.execution_id,
                node_name=self.name,
                data={"provider": self._provider_name, "model": self._model},
            )
        )


DEFAULT_VISUALIZATION_PROMPT_VERSION = "v3"   # v3: per-event state, per-step narration


def build_visualization_prompt(
    trace: ExecutionTrace,
    explanation: ExplanationResponse,
    education: EducationalPlanResponse | None = None,
) -> str:
    from code2shorts.ai.contracts import VisualAction

    from code2shorts.visualization.state import reconstruct_frames

    lines = [
        f"Create a visualization plan for {trace.algorithm_name}, grounded ONLY "
        "in the trace events below.",
        "Every step's trace_event_index MUST be a real step_index from the "
        "trace, and steps must reference events in non-decreasing order.",
        "visual_action must be exactly one of: " + ", ".join(a.value for a in VisualAction),
        # Phase 6.1. Each step's narration is spoken while THAT step's state
        # is on screen, so a sentence copied from a lesson moment into
        # several steps ends up telling the learner about values they
        # cannot see yet. The state after each event is listed below
        # precisely so each step can be described in its own terms.
        "Each step's narration_text is spoken WHILE that step's state is on "
        "screen. Describe that state, or the action about to happen in "
        "words ('the pointers move inward'). NEVER state a variable's "
        "later value: if the step shows left=0, its narration must not say "
        "left is 1 or 'left becomes 1'. Give each step its own sentence - "
        "do not repeat one sentence across steps whose state differs.",
        f"Explanation summary: {explanation.summary}",
        "Trace events, with the state visible AFTER each one:",
    ]
    frames = {frame.step_index: frame for frame in reconstruct_frames(trace)}
    for event in trace.events:
        frame = frames.get(event.step_index)
        state = ""
        if frame is not None and frame.scalars:
            state = "  | state: " + ", ".join(
                f"{name}={value}" for name, value in sorted(frame.scalars.items())
            )
        lines.append(
            f"  [{event.step_index}] {event.event_type}: {event.description}{state}"
        )

    if education is not None:
        # The lesson decides WHAT matters; this prompt only decides how to
        # show it. Every moment must survive into the visuals — dropping one
        # to shorten the video is prohibited (ADR-5.11).
        lines.append("")
        lines.append(
            "EDUCATIONAL PLAN — each moment below must be visible in the "
            "output. Do not omit one to shorten the video; there is no "
            "duration limit."
        )
        for moment in education.moments:
            evidence = ", ".join(str(i) for i in moment.evidence_event_indices)
            lines.append(
                f"  ({moment.id}) {moment.concept.value} "
                f"[{moment.claim_kind.value}] events[{evidence}]: "
                f"{moment.explanation}"
            )

    return "\n".join(lines) + json_schema_instruction(VisualizationPlanResponse)


class VisualizationPlanNode(WorkflowNode):
    """AI proposes a VisualizationPlanResponse; validate_visualization_plan
    decides whether it's trustworthy. Bounded repair on failure — never an
    unbounded loop, never an artifact saved from output that failed
    validation.
    """

    name = "visualization_plan"

    def __init__(
        self,
        provider: LLMProvider,
        provider_name: str = "mock",
        model: str = "mock",
        prompt_version: str = DEFAULT_VISUALIZATION_PROMPT_VERSION,
        max_repair_attempts: int = 1,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._prompt_version = prompt_version
        self._max_repair_attempts = max_repair_attempts

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        trace_artifact_id = state.artifact_ids.get("trace")
        explanation_artifact_id = state.artifact_ids.get("explanation")
        if trace_artifact_id is None or explanation_artifact_id is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT,
                "visualization planning requires trace + explanation artifacts; run those nodes first",
            )
        trace_artifact = context.artifact_store.get(trace_artifact_id)
        explanation_artifact = context.artifact_store.get(explanation_artifact_id)
        if trace_artifact is None or explanation_artifact is None:
            raise NodeExecutionError(FailureKind.PERMANENT, "referenced artifact missing from store")

        trace = ExecutionTrace.model_validate(trace_artifact.content)
        explanation = ExplanationResponse.model_validate(explanation_artifact.content)

        # Optional so every existing caller and test keeps working: when
        # EducationalPlanNode has run, its lesson steers the visuals; when
        # it has not, this behaves exactly as it did in Phase 5.
        education = None
        education_artifact_id = state.artifact_ids.get("educational_plan")
        if education_artifact_id is not None:
            education_artifact = context.artifact_store.get(education_artifact_id)
            if education_artifact is not None:
                education = EducationalPlanResponse.model_validate(
                    education_artifact.content
                )

        self._emit_event(context, WorkflowEventType.AI_CALL_STARTED)
        try:
            plan, history = generate_with_repair(
                self._provider,
                build_prompt=lambda: build_visualization_prompt(
                    trace, explanation, education
                ),
                response_model=VisualizationPlanResponse,
                validate=lambda parsed: _with_subject(
                    validate_visualization_plan(parsed, trace), trace_artifact_id
                ),
                max_repair_attempts=self._max_repair_attempts,
            )
        except SchemaValidationError as error:
            raise NodeExecutionError(
                FailureKind.VALIDATION, f"visualization plan schema validation failed: {error}"
            ) from error
        except SemanticValidationError as error:
            raise NodeExecutionError(
                FailureKind.VALIDATION,
                "visualization plan failed semantic validation after repair attempts",
                detail={"errors": error.errors},
            ) from error
        finally:
            self._emit_event(context, WorkflowEventType.AI_CALL_COMPLETED)

        state.validation.record_history(history)

        plan_artifact = Artifact.create(
            type=ArtifactType.VISUALIZATION_PLAN,
            producer=f"{self._provider_name}:{self._model}",
            content=plan.model_dump(),
            input_artifact_ids=[trace_artifact_id, explanation_artifact_id],
        )
        context.artifact_store.save(plan_artifact)
        state.artifact_ids["visualization_plan"] = plan_artifact.id

        return NodeResult(state=state, artifact_ids_created=[plan_artifact.id])

    def _emit_event(self, context: WorkflowContext, event_type: WorkflowEventType) -> None:
        context.emit(
            WorkflowEvent(
                type=event_type,
                workflow_id=context.workflow_id,
                execution_id=context.execution_id,
                node_name=self.name,
            )
        )


DEFAULT_NARRATION_PROMPT_VERSION = "v2"


def build_narration_prompt(plan: VisualizationPlanResponse) -> str:
    lines = [
        f"Write narration for the video '{plan.lesson_title}'. Produce one segment "
        "per visualization step, each with visualization_step_order set to that "
        "step's order. Do not invent steps beyond what's listed.",
        "Visualization steps:",
    ]
    for step in plan.steps:
        lines.append(f"  [{step.order}] {step.visual_action.value}: {step.narration_text}")
    return "\n".join(lines) + json_schema_instruction(NarrationResponse)


class NarrationNode(WorkflowNode):
    """AI proposes NarrationResponse; validate_narration decides. Narration
    is grounded through the (already-validated) visualization plan, not
    directly re-derived from the trace — see narration/validation.py.
    """

    name = "narration"

    def __init__(
        self,
        provider: LLMProvider,
        provider_name: str = "mock",
        model: str = "mock",
        prompt_version: str = DEFAULT_NARRATION_PROMPT_VERSION,
        max_repair_attempts: int = 1,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._prompt_version = prompt_version
        self._max_repair_attempts = max_repair_attempts

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        plan_artifact_id = state.artifact_ids.get("visualization_plan")
        if plan_artifact_id is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT, "narration requires a visualization plan; run that node first"
            )
        plan_artifact = context.artifact_store.get(plan_artifact_id)
        if plan_artifact is None:
            raise NodeExecutionError(FailureKind.PERMANENT, "visualization plan artifact missing from store")
        plan = VisualizationPlanResponse.model_validate(plan_artifact.content)

        self._emit_event(context, WorkflowEventType.AI_CALL_STARTED)
        try:
            narration, history = generate_with_repair(
                self._provider,
                build_prompt=lambda: build_narration_prompt(plan),
                response_model=NarrationResponse,
                validate=lambda parsed: _with_subject(
                    validate_narration(parsed, plan), plan_artifact_id
                ),
                max_repair_attempts=self._max_repair_attempts,
            )
        except SchemaValidationError as error:
            raise NodeExecutionError(
                FailureKind.VALIDATION, f"narration schema validation failed: {error}"
            ) from error
        except SemanticValidationError as error:
            raise NodeExecutionError(
                FailureKind.VALIDATION,
                "narration failed semantic validation after repair attempts",
                detail={"errors": error.errors},
            ) from error
        finally:
            self._emit_event(context, WorkflowEventType.AI_CALL_COMPLETED)

        state.validation.record_history(history)

        narration_artifact = Artifact.create(
            type=ArtifactType.NARRATION,
            producer=f"{self._provider_name}:{self._model}",
            content=narration.model_dump(),
            input_artifact_ids=[plan_artifact_id],
        )
        context.artifact_store.save(narration_artifact)
        state.artifact_ids["narration"] = narration_artifact.id

        return NodeResult(state=state, artifact_ids_created=[narration_artifact.id])

    def _emit_event(self, context: WorkflowContext, event_type: WorkflowEventType) -> None:
        context.emit(
            WorkflowEvent(
                type=event_type,
                workflow_id=context.workflow_id,
                execution_id=context.execution_id,
                node_name=self.name,
            )
        )


class RenderVideoNode(WorkflowNode):
    """Trusted renderer stage. `renderer` interprets the ALREADY-VALIDATED
    VisualizationPlanResponse using code we wrote — never AI-authored
    Manim/Python source. See visualization/renderer.py.
    """

    name = "render_video"

    def __init__(self, renderer: VideoRenderer, output_dir: Path) -> None:
        self._renderer = renderer
        self._output_dir = output_dir

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        plan_artifact_id = state.artifact_ids.get("visualization_plan")
        if plan_artifact_id is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT, "rendering requires a visualization plan; run that node first"
            )
        plan_artifact = context.artifact_store.get(plan_artifact_id)
        if plan_artifact is None:
            raise NodeExecutionError(FailureKind.PERMANENT, "visualization plan artifact missing from store")
        plan = VisualizationPlanResponse.model_validate(plan_artifact.content)

        context.emit(
            WorkflowEvent(
                type=WorkflowEventType.RENDERING_STARTED,
                workflow_id=context.workflow_id,
                execution_id=context.execution_id,
                node_name=self.name,
            )
        )
        # Hand the renderer the canonical trace and the real source so it
        # can draw actual algorithm state (array tiles, pointers, the
        # executing line) instead of captions. Both are already-validated
        # facts — the renderer only reads them, and the trace remains owned
        # by the trace layer.
        render_context = RenderContext(
            trace=self._load_trace(state, context),
            source_files=dict(state.request.source_files),
            entry_point=state.request.entry_point,
            lesson_title=plan.lesson_title,
        )
        try:
            render_result = self._renderer.render(
                plan,
                self._output_dir / state.metadata.execution_id / "render",
                render_context,
            )
        except RenderingFailure as error:
            # A rendering failure at this point is an environment/tooling
            # problem (the plan was already validated) — safe to retry.
            raise NodeExecutionError(FailureKind.TRANSIENT, f"rendering failed: {error}") from error
        finally:
            context.emit(
                WorkflowEvent(
                    type=WorkflowEventType.RENDERING_COMPLETED,
                    workflow_id=context.workflow_id,
                    execution_id=context.execution_id,
                    node_name=self.name,
                )
            )

        # content is metadata about the video (paths/checksum/numbers),
        # never the video bytes themselves — see ARCHITECTURE_DECISIONS.md
        # "artifact lineage" for why raw media never flows through state.
        video_artifact = Artifact.create(
            type=ArtifactType.RENDERED_VIDEO,
            producer=self.name,
            content=render_result.model_dump(),
            input_artifact_ids=[plan_artifact_id],
        )
        context.artifact_store.save(video_artifact)
        state.artifact_ids["rendered_video"] = video_artifact.id

        return NodeResult(state=state, artifact_ids_created=[video_artifact.id])

    @staticmethod
    def _load_trace(
        state: Code2ShortsState, context: WorkflowContext
    ) -> ExecutionTrace | None:
        trace_artifact_id = state.artifact_ids.get("trace")
        if trace_artifact_id is None:
            return None
        trace_artifact = context.artifact_store.get(trace_artifact_id)
        if trace_artifact is None:
            return None
        return ExecutionTrace.model_validate(trace_artifact.content)

    @staticmethod
    def _entry_source(state: Code2ShortsState) -> str | None:
        """The entry-point file's source, for the code panel. Trimmed to a
        readable window so a long file cannot overflow a 9:16 frame."""
        source = state.request.source_files.get(state.request.entry_point)
        if source is None:
            return None
        lines = source.splitlines()
        return "\n".join(lines[:22])


class ComposeMediaNode(WorkflowNode):
    """Trusted media composition: narration text -> TTS audio (TTSProvider)
    + rendered video -> final video (MediaComposer, fixed-argument FFmpeg
    invocation). No AI-authored command ever reaches either.
    """

    name = "compose_media"

    def __init__(
        self, tts_provider: TTSProvider, composer: MediaComposer, output_dir: Path
    ) -> None:
        self._tts_provider = tts_provider
        self._composer = composer
        self._output_dir = output_dir

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        video_artifact_id = state.artifact_ids.get("rendered_video")
        narration_artifact_id = state.artifact_ids.get("narration")
        if video_artifact_id is None or narration_artifact_id is None:
            raise NodeExecutionError(
                FailureKind.PERMANENT,
                "media composition requires rendered_video + narration artifacts",
            )
        video_artifact = context.artifact_store.get(video_artifact_id)
        narration_artifact = context.artifact_store.get(narration_artifact_id)
        if video_artifact is None or narration_artifact is None:
            raise NodeExecutionError(FailureKind.PERMANENT, "referenced artifact missing from store")

        narration = NarrationResponse.model_validate(narration_artifact.content)
        full_text = " ".join(
            segment.text for segment in sorted(narration.segments, key=lambda s: s.order)
        )

        work_dir = self._output_dir / state.metadata.execution_id / "compose"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            tts_result = self._tts_provider.synthesize(full_text, work_dir / "narration.audio")
        except TTSFailure as error:
            raise NodeExecutionError(FailureKind.TRANSIENT, f"TTS synthesis failed: {error}") from error

        audio_artifact = Artifact.create(
            type=ArtifactType.AUDIO,
            producer=self.name,
            content=tts_result.model_dump(),
            input_artifact_ids=[narration_artifact_id],
        )
        context.artifact_store.save(audio_artifact)
        state.artifact_ids["audio"] = audio_artifact.id

        video_path = Path(video_artifact.content["output_path"])
        video_duration = video_artifact.content["duration_seconds"]
        final_path = work_dir / "final.mp4"
        try:
            compose_result = self._composer.compose(
                video_path, Path(tts_result.audio_path), final_path, video_duration
            )
        except MediaCompositionFailure as error:
            raise NodeExecutionError(
                FailureKind.TRANSIENT, f"media composition failed: {error}"
            ) from error

        final_artifact = Artifact.create(
            type=ArtifactType.FINAL_VIDEO,
            producer=self.name,
            content=compose_result.model_dump(),
            input_artifact_ids=[video_artifact_id, audio_artifact.id],
        )
        context.artifact_store.save(final_artifact)
        state.artifact_ids["final_video"] = final_artifact.id

        return NodeResult(
            state=state, artifact_ids_created=[audio_artifact.id, final_artifact.id]
        )


class FinalValidationNode(WorkflowNode):
    """Domain-level trust gate at the very end of the pipeline: the final
    video artifact must exist, must have a real checksum, and every
    validation result recorded anywhere earlier in the run must have
    passed. This is the last point where a broken pipeline can be caught
    before the workflow reports success.
    """

    name = "final_validation"

    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        final_video_id = state.artifact_ids.get("final_video")
        errors: list[str] = []

        final_artifact = context.artifact_store.get(final_video_id) if final_video_id else None
        if final_artifact is None:
            errors.append("no final_video artifact found")
        elif not final_artifact.content.get("checksum"):
            errors.append("final_video artifact has no checksum")

        failed_results = [r for r in state.validation.results if not r.passed]
        if failed_results:
            errors.append(f"{len(failed_results)} earlier validation result(s) did not pass")

        result = ValidationResult(
            stage="domain", passed=not errors, errors=errors, subject_artifact_id=final_video_id
        )
        state.validation.record(result)

        if errors:
            raise NodeExecutionError(
                FailureKind.VALIDATION, "final validation failed", detail={"errors": errors}
            )

        return NodeResult(state=state, artifact_ids_created=[])
