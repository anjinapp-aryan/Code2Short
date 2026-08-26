"""Loads pre-written Java source fixtures into GeneratedCode.

These fixtures stand in for what a CodeGenerator will eventually produce.
Phase 1 does not use an LLM, so the "generated" code here is hand-written
and deterministic, covering both a correct Reverse String implementation
and several intentionally broken variants for failure-path tests.
"""

from __future__ import annotations

from pathlib import Path

from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import SupportedLanguage

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "java" / "reverse_string"
ENTRY_POINT = "src/main/java/com/code2shorts/reversestring/Main.java"

TRACE_SAMPLES_ROOT = Path(__file__).parent / "fixtures" / "java" / "trace_samples"
TRACE_SAMPLE_ENTRY_POINT = "src/main/java/com/code2shorts/tracesamples/Main.java"


def load_java_fixture(variant: str) -> GeneratedCode:
    fixture_dir = FIXTURES_ROOT / variant
    source_files = {
        path.relative_to(fixture_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in fixture_dir.rglob("*.java")
    }
    return GeneratedCode(
        language=SupportedLanguage.JAVA,
        source_files=source_files,
        entry_point=ENTRY_POINT,
    )


ALGORITHMS_ROOT = Path(__file__).parent / "fixtures" / "java" / "algorithms"
ALGORITHM_ENTRY_POINT = "src/main/java/com/code2shorts/algorithms/Main.java"

# The Phase 4.2 generalization set. ONE generic visualization implementation
# must handle all of these with no algorithm-specific renderer branches.
ALGORITHM_VARIANTS = ("palindrome", "two_sum", "move_zeroes", "remove_duplicates")


def load_algorithm_fixture(variant: str) -> GeneratedCode:
    fixture_dir = ALGORITHMS_ROOT / variant
    source_files = {
        path.relative_to(fixture_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in fixture_dir.rglob("*.java")
    }
    return GeneratedCode(
        language=SupportedLanguage.JAVA,
        source_files=source_files,
        entry_point=ALGORITHM_ENTRY_POINT,
    )


def load_trace_sample(variant: str) -> GeneratedCode:
    fixture_dir = TRACE_SAMPLES_ROOT / variant
    source_files = {
        path.relative_to(fixture_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in fixture_dir.rglob("*.java")
    }
    return GeneratedCode(
        language=SupportedLanguage.JAVA,
        source_files=source_files,
        entry_point=TRACE_SAMPLE_ENTRY_POINT,
    )
