"""LLMProvider -> CodeGenerator -> GeneratedCode.

CodeGenerator owns producing source code from an AlgorithmSpec via an
LLMProvider. It knows nothing about compiling, testing, executing, or
tracing that code — those are LanguageAdapter's job. One CodeGenerator
implementation per language, same as LanguageAdapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from code2shorts.core.models import AlgorithmSpec, SupportedLanguage
from code2shorts.llm.provider import LLMProvider


class GeneratedCode(BaseModel):
    """Source produced by a CodeGenerator, not yet trusted."""

    language: SupportedLanguage
    source_files: dict[str, str] = Field(
        description="Relative file path within the workspace -> file contents"
    )
    entry_point: str = Field(description="File path containing the main/test entry")


class CodeGenerator(ABC):
    """One implementation per target language (Java first)."""

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm_provider = llm_provider

    @property
    @abstractmethod
    def language(self) -> SupportedLanguage: ...

    @abstractmethod
    def generate(self, algorithm_spec: AlgorithmSpec) -> GeneratedCode:
        """Ask the LLM for an implementation matching algorithm_spec."""
