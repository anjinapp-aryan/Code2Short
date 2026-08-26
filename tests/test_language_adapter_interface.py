import pytest

from code2shorts.core.models import SupportedLanguage
from code2shorts.langadapter.base import LanguageAdapter
from code2shorts.langadapter.java import JavaAdapter


def test_language_adapter_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        LanguageAdapter()  # type: ignore[abstract]


def test_language_adapter_has_no_generate_method() -> None:
    assert not hasattr(LanguageAdapter, "generate")


def test_java_adapter_declares_java() -> None:
    adapter = JavaAdapter()
    assert adapter.language == SupportedLanguage.JAVA


def test_java_adapter_has_all_four_stages() -> None:
    # compile/test/execute are real as of Phase 1, trace() as of Phase 2
    # (see tests/test_java_adapter_integration.py and
    # tests/test_java_trace_integration.py for the real, subprocess-backed
    # proof — this just confirms the interface shape).
    adapter = JavaAdapter()
    for stage in ("compile", "test", "execute", "trace"):
        assert callable(getattr(adapter, stage))
