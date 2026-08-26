"""Java implementation of CodeGenerator.

Phase 0: interface skeleton only. Phase 1 (Reverse String golden example)
wires this up to a prompt template that asks the LLM for a Main.java (with
structured trace-event logging built in) matching the AlgorithmSpec.
"""

from __future__ import annotations

from code2shorts.codegen.base import CodeGenerator, GeneratedCode
from code2shorts.core.models import AlgorithmSpec, SupportedLanguage


class JavaCodeGenerator(CodeGenerator):
    @property
    def language(self) -> SupportedLanguage:
        return SupportedLanguage.JAVA

    def generate(self, algorithm_spec: AlgorithmSpec) -> GeneratedCode:
        raise NotImplementedError("Wired up in Phase 1 (Reverse String).")
