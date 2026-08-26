"""The seam that keeps `if language == "java"` out of the pipeline.

Everything above this interface (`LessonSpec`, `AlgorithmSpec`,
`ExecutionTrace`, `AnimationSpec`, the renderer, the composer) is
language-independent. Everything below it is one concrete adapter.

LanguageAdapter is deliberately narrow: it only does language/runtime
operations on code it is handed. It does not generate that code —
that's `code2shorts.codegen.CodeGenerator`'s job.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import ExecutionTrace, SupportedLanguage
from code2shorts.execution.sandbox import Workspace


class CompileResult(BaseModel):
    succeeded: bool
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False


class TestResult(BaseModel):
    succeeded: bool
    stdout: str = ""
    stderr: str = ""
    tests_run: int = 0
    tests_passed: int = 0
    duration_seconds: float = 0.0
    timed_out: bool = False


class ExecuteResult(BaseModel):
    succeeded: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    output: str = Field(default="", description="Program's captured result output")
    duration_seconds: float = 0.0
    timed_out: bool = False


class LanguageAdapter(ABC):
    """One implementation per target language (Java first, then Python/JS)."""

    @property
    @abstractmethod
    def language(self) -> SupportedLanguage: ...

    @abstractmethod
    def compile(self, code: GeneratedCode, workspace: Workspace) -> CompileResult:
        """Compile generated code inside the given isolated workspace."""

    @abstractmethod
    def test(self, code: GeneratedCode, workspace: Workspace) -> TestResult:
        """Run unit tests against the compiled code inside the workspace."""

    @abstractmethod
    def execute(
        self, code: GeneratedCode, workspace: Workspace, input_value: str
    ) -> ExecuteResult:
        """Run the algorithm on input_value and capture its raw output."""

    @abstractmethod
    def trace(
        self, code: GeneratedCode, workspace: Workspace, input_value: str
    ) -> ExecutionTrace:
        """Run the algorithm on input_value and capture a real ExecutionTrace.

        Implementations MUST derive every TraceEvent from actual program
        output (e.g. structured trace logging emitted by the generated
        code itself). Never synthesize events the program did not report.
        """
