import pytest

from code2shorts.codegen.base import CodeGenerator
from code2shorts.codegen.java import JavaCodeGenerator
from code2shorts.core.models import SupportedLanguage
from code2shorts.llm.provider import LLMProvider


class _FakeLLMProvider(LLMProvider):
    def complete(self, prompt: str, system: str | None = None) -> str:
        return ""


def test_code_generator_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        CodeGenerator(llm_provider=_FakeLLMProvider())  # type: ignore[abstract]


def test_java_code_generator_declares_java() -> None:
    generator = JavaCodeGenerator(llm_provider=_FakeLLMProvider())
    assert generator.language == SupportedLanguage.JAVA


def test_java_code_generator_not_yet_implemented() -> None:
    generator = JavaCodeGenerator(llm_provider=_FakeLLMProvider())
    with pytest.raises(NotImplementedError):
        generator.generate(algorithm_spec=None)  # type: ignore[arg-type]
