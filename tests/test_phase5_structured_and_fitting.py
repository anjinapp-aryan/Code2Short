"""Phase 5: structured output, schema-stated prompts, timeline fitting, and
the superseded-attempt distinction.

Every test here is credential-free and network-free. Each one exists
because a REAL model broke something a mock never could.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from code2shorts.ai.contracts import (
    ExplanationResponse,
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.ai.structured import (
    SchemaValidationError,
    extract_json,
    generate_structured,
    json_schema_instruction,
)
from code2shorts.core.models import ValidationResult
from code2shorts.llm.provider import LLMProvider
from code2shorts.narration.fitting import DEFAULT_PADDING_SECONDS, fit_plan_to_narration
from code2shorts.workflow.state import ValidationSummary


class _Canned(LLMProvider):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, prompt: str, system: str | None = None) -> str:
        self.calls += 1
        return self.reply


class _Tiny(BaseModel):
    a: int


# ---- extract_json ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a":1}', '{"a":1}'),
        ('```json\n{"a":1}\n```', '{"a":1}'),
        ('```\n{"a":1}\n```', '{"a":1}'),
        ('Here is the plan:\n{"a":1}', '{"a":1}'),
        ('Sure!\n```json\n{"a":1}\n```\nHope that helps.', '{"a":1}'),
        ("[1,2]", "[1,2]"),
    ],
)
def test_extract_json_recovers_the_body_real_models_actually_send(raw, expected) -> None:
    assert extract_json(raw) == expected


def test_extract_json_returns_the_original_when_nothing_is_json_shaped() -> None:
    """So the schema error reports what the model really said, rather than
    some mangled slice of it."""
    assert extract_json("I cannot help with that.") == "I cannot help with that."


def test_extraction_is_not_lenient_parsing() -> None:
    """Extraction only finds the JSON; the Pydantic model still decides.
    A fenced-but-wrong payload must still fail."""
    provider = _Canned('```json\n{"a":"not-an-int-and-also-wrong"}\n```')
    with pytest.raises(SchemaValidationError):
        generate_structured(provider, "p", _Tiny)


def test_extract_json_never_executes_anything() -> None:
    """It slices text; it must not eval, import, or interpret."""
    import ast
    import inspect

    from code2shorts.ai import structured

    module = ast.parse(inspect.getsource(structured))
    target = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "extract_json"
    )
    called = {
        node.func.id
        for node in ast.walk(target)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__", "literal_eval"}


def test_fenced_response_no_longer_burns_a_repair_attempt() -> None:
    """The repair loop and generate_structured must share ONE parse path.
    They did not, so a correct-but-fenced reply failed in the node while
    succeeding in the seam."""
    from code2shorts.ai.repair import generate_with_repair

    provider = _Canned('```json\n{"a":7}\n```')
    parsed, history = generate_with_repair(
        provider,
        build_prompt=lambda: "p",
        response_model=_Tiny,
        validate=lambda _: ValidationResult(stage="semantic", passed=True),
        max_repair_attempts=2,
    )
    assert parsed.a == 7
    assert provider.calls == 1, "a valid fenced reply must not trigger repair"


# ---- schema-stated prompts ------------------------------------------------


@pytest.mark.parametrize(
    "model", [ExplanationResponse, VisualizationPlanResponse, NarrationResponse]
)
def test_prompt_states_the_real_schema_derived_from_the_contract(model) -> None:
    instruction = json_schema_instruction(model)
    # It must carry the model's ACTUAL schema, not a hand-written example
    # that could silently rot when a field changes.
    schema = model.model_json_schema()
    assert json.dumps(schema, separators=(",", ":")) in instruction
    assert "JSON Schema" in instruction


def test_every_ai_prompt_states_its_response_schema() -> None:
    """The gap that broke the first real run: prompts never said what shape
    to reply in, and the model returned `steps` as an array of strings."""
    from code2shorts.core.models import ExecutionTrace
    from code2shorts.workflow.nodes import (
        build_explanation_prompt,
        build_narration_prompt,
        build_visualization_prompt,
    )

    trace = ExecutionTrace(
        algorithm_name="X", language="java", input="a", output="b", events=[],
        succeeded=True, exit_code=0,
    )
    explanation = ExplanationResponse(
        summary="s", learning_objectives=["o"], steps=[], referenced_trace_event_indices=[]
    )
    plan = VisualizationPlanResponse(lesson_title="T", steps=[])

    for prompt in (
        build_explanation_prompt(trace),
        build_visualization_prompt(trace, explanation),
        build_narration_prompt(plan),
    ):
        assert "JSON Schema" in prompt


# ---- timeline fitting -----------------------------------------------------


def _plan(durations: list[float]) -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="T",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.INTRO,
                trace_event_index=i,
                narration_text=f"n{i}",
                duration_seconds=d,
            )
            for i, d in enumerate(durations)
        ],
    )


def _narration(count: int) -> NarrationResponse:
    return NarrationResponse(
        segments=[
            NarrationSegment(order=i, text=f"n{i}", visualization_step_order=i)
            for i in range(count)
        ]
    )


def test_a_step_too_short_for_its_speech_is_widened_not_the_audio_cut() -> None:
    """The exact real failure: the model proposed 1.0s steps for 3.5s of
    narration and all 25 segments overflowed."""
    result = fit_plan_to_narration(
        _plan([1.0, 1.0]), _narration(2), {0: ("a.wav", 3.5), 1: ("b.wav", 2.0)}
    )
    assert [s.duration_seconds for s in result.plan.steps] == [
        pytest.approx(3.5 + DEFAULT_PADDING_SECONDS),
        pytest.approx(2.0 + DEFAULT_PADDING_SECONDS),
    ]
    assert len(result.adjustments) == 2


def test_fitting_only_widens_and_never_shrinks() -> None:
    """A step the model deliberately made long keeps its pacing — only the
    impossible part is corrected."""
    result = fit_plan_to_narration(_plan([10.0]), _narration(1), {0: ("a.wav", 2.0)})
    assert result.plan.steps[0].duration_seconds == 10.0
    assert not result.changed


def test_fitting_eliminates_overflow_end_to_end() -> None:
    from code2shorts.narration import align_narration, validate_alignment

    plan, narration = _plan([1.0, 1.0, 1.0]), _narration(3)
    audio = {0: ("a.wav", 3.1), 1: ("b.wav", 4.4), 2: ("c.wav", 2.7)}

    before = align_narration(narration, plan, audio_by_segment=audio)
    assert before.overflow_count == 3

    fitted = fit_plan_to_narration(plan, narration, audio).plan
    after = align_narration(narration, fitted, audio_by_segment=audio)
    assert after.overflow_count == 0
    assert validate_alignment(after, fitted).passed


def test_several_segments_sharing_a_step_are_summed_not_maxed() -> None:
    """They play end to end, so the step must hold their total."""
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=0, text="a", visualization_step_order=0),
            NarrationSegment(order=1, text="b", visualization_step_order=0),
        ]
    )
    result = fit_plan_to_narration(
        _plan([1.0]), narration, {0: ("a.wav", 2.0), 1: ("b.wav", 3.0)}
    )
    assert result.plan.steps[0].duration_seconds == pytest.approx(5.0 + DEFAULT_PADDING_SECONDS)


def test_fitting_does_not_mutate_the_validated_plan() -> None:
    plan = _plan([1.0])
    fit_plan_to_narration(plan, _narration(1), {0: ("a.wav", 5.0)})
    assert plan.steps[0].duration_seconds == 1.0


def test_a_step_with_no_narration_is_left_alone() -> None:
    result = fit_plan_to_narration(_plan([2.0]), NarrationResponse(segments=[]), {})
    assert result.plan.steps[0].duration_seconds == 2.0
    assert not result.changed


def test_fitting_never_truncates_or_stretches() -> None:
    """Phase 4.4's prohibitions still hold in the module that adjusts time."""
    import ast
    import inspect

    from code2shorts.narration import fitting

    tree = ast.parse(inspect.getsource(fitting))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "min", "shrinking a step could cut speech off"


# ---- superseded repair attempts ------------------------------------------


def test_a_repaired_failure_does_not_make_the_run_report_invalid() -> None:
    """A real run reported FAIL overall while every artifact had been built
    from a passing attempt."""
    summary = ValidationSummary()
    summary.record_history(
        [
            ValidationResult(stage="semantic", passed=False, errors=["step order wrong"]),
            ValidationResult(stage="semantic", passed=True),
        ]
    )
    assert summary.all_passed
    assert not summary.every_attempt_passed
    assert [r.superseded for r in summary.results] == [True, False]


def test_a_final_failure_still_fails() -> None:
    summary = ValidationSummary()
    summary.record_history(
        [
            ValidationResult(stage="semantic", passed=False, errors=["e1"]),
            ValidationResult(stage="semantic", passed=False, errors=["e2"]),
        ]
    )
    assert not summary.all_passed


def test_the_failed_attempt_is_kept_as_audit_evidence() -> None:
    """Repair history must never be deleted — it is how many attempts a
    provider needed."""
    summary = ValidationSummary()
    summary.record_history(
        [
            ValidationResult(stage="semantic", passed=False, errors=["the original complaint"]),
            ValidationResult(stage="semantic", passed=True),
        ]
    )
    assert any("the original complaint" in e for r in summary.results for e in r.errors)
