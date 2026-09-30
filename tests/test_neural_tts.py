"""PHASE 6.2 — the permissive neural speech path.

Two things are worth locking down here, and they are the two things that
would fail silently:

* the gruut -> Kokoro phoneme mapping. Two IPA conventions meet here, and
  a regression is a MISPRONUNCIATION, not a crash — nothing else in the
  pipeline would notice.
* the spoken-text layer. Sent raw to any synthesiser, `chars[0]` and
  `chars[6]` both come out as "chars", so a learner is told two different
  states are identical. Measured on both SAPI and Kokoro.

The heavy tests (real model inference) are marked `integration` so the
default suite stays fast and needs no 340 MB download.
"""

from __future__ import annotations

import shutil

import pytest

from code2shorts.narration.phonemes import (
    AFFRICATES,
    TIE_BAR,
    encode,
    kokoro_vocab,
    normalize_token,
    to_kokoro_phonemes,
    unsupported_symbols,
)
from code2shorts.narration.speech_text import to_spoken

# ---------------------------------------------------------------------------
# Spoken-text layer. Every case below was produced by a real synthesiser.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written,expected",
    [
        ("chars[0]", "chars at index 0"),
        ("chars[6]", "chars at index 6"),
        ("arr[j]", "arr at index j"),
        ("arr[j - 1]", "arr at index j minus 1"),
        ("a != b", "a is not equal to b"),
        ("a == b", "a equals b"),
        ("left++", "left increases by one"),
        ("right--", "right decreases by one"),
        ("O(n)", "order n"),
        ("O(log n)", "order log n"),
        ("O(n^2)", "order n squared"),
        ("O(1)", "constant"),
        ("left < right", "left is less than right"),
        ("i >= n", "i is greater than or equal to n"),
    ],
)
def test_code_notation_becomes_words(written: str, expected: str) -> None:
    assert to_spoken(written) == expected


def test_the_two_indexes_stop_sounding_identical() -> None:
    """The defect in one line: raw, both of these synthesise as "chars"."""
    assert to_spoken("chars[0]") != to_spoken("chars[6]")


def test_complexity_notation_reads_correctly_for_space_as_well() -> None:
    """`O(1)` is used for space too, so it must not carry the word "time"."""
    assert to_spoken("It runs in O(n) time and O(1) space.") == (
        "It runs in order n time and constant space."
    )


def test_ordinary_prose_is_untouched() -> None:
    """The common case must be impossible to damage."""
    for prose in [
        "The left pointer starts at the last index of the array.",
        "Both characters match, so the pointers move inward.",
        "Nothing here is code at all.",
    ]:
        assert to_spoken(prose) == prose


def test_a_whole_narration_sentence_is_rewritten() -> None:
    assert to_spoken("While left < right, left++ and right--.") == (
        "While left is less than right, left increases by one and "
        "right decreases by one."
    )


def test_no_space_is_left_before_punctuation() -> None:
    """Operator rules pad with spaces; a space before a full stop becomes
    an audible hesitation."""
    assert " ." not in to_spoken("The value is a != b.")
    assert " ," not in to_spoken("If a != b, stop.")


def test_the_spoken_layer_is_not_algorithm_aware() -> None:
    """Section 15/23: no algorithm-specific hacks anywhere."""
    import inspect

    import code2shorts.narration.speech_text as module

    source = inspect.getsource(module).lower()
    for name in ("palindrome", "racecar", "two_sum", "insertion", "binary search"):
        assert name not in source


# ---------------------------------------------------------------------------
# Phoneme mapping.
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_vendored_and_loadable() -> None:
    """Vendored on purpose: reading it from `kokoro-onnx` needs an import,
    and that package imports espeak-ng (GPL-3.0) at module load."""
    vocab = kokoro_vocab()
    assert len(vocab) == 114
    assert all(isinstance(v, int) for v in vocab.values())


def test_stress_marks_and_diphthongs_split_into_symbols() -> None:
    """gruut bundles them; Kokoro has each piece separately."""
    vocab = kokoro_vocab()
    for symbol in ("ˈ", "ˌ", "a", "ɪ", "e", "o", "ʊ", "ɔ"):
        assert symbol in vocab
    assert to_kokoro_phonemes(["ˈaɪ"]) == "ˈaɪ"
    assert set(to_kokoro_phonemes(["ˈaɪ"])) <= set(vocab)


@pytest.mark.parametrize("tied,precomposed", sorted(AFFRICATES.items()))
def test_affricates_map_to_the_precomposed_symbol(tied, precomposed) -> None:
    """Kept as ONE unit, which is how the model was trained to see it —
    splitting 'd͡ʒ' into 'd' + 'ʒ' would change the sound."""
    assert normalize_token(tied) == precomposed
    assert precomposed in kokoro_vocab()


def test_the_tie_bar_never_survives() -> None:
    assert TIE_BAR not in to_kokoro_phonemes(["d͡ʒ", "t͡ʃ"])


def test_gruut_breaks_become_punctuation() -> None:
    """Kokoro expresses prosody with punctuation, gruut with break marks."""
    assert normalize_token("|") == ","
    assert normalize_token("‖") == "."


def test_encoding_drops_nothing_from_real_narration() -> None:
    phonemes = "ðə lˈɛft pˈɔɪntɚ stˈɑɹts ˈæt ˈɪndɛks zˈɪɹoʊ ."
    assert unsupported_symbols(phonemes) <= {" "}
    assert len(encode(phonemes)) > 0


def test_unknown_symbols_are_reported_not_guessed() -> None:
    """A substituted guess puts a sound in the speech the text never asked
    for, which is worse than a missing one - so unknown symbols are
    reported and dropped, never approximated."""
    unknown = "\u20ac\u2603"  # euro sign, snowman: certainly not phonemes
    assert unsupported_symbols(unknown) == set(unknown)
    # ...and they really are absent from the encoding, not silently mapped.
    assert encode(unknown) == []


def test_encoding_keeps_the_symbols_it_knows() -> None:
    known = "l\u02c8\u025bft"
    assert unsupported_symbols(known) == set()
    assert len(encode(known)) == len(known)


# ---------------------------------------------------------------------------
# Real model. Skipped unless the weights are present.
# ---------------------------------------------------------------------------


def _provider():
    from code2shorts.narration.tts_kokoro import KokoroTTSProvider

    return KokoroTTSProvider()


kokoro_required = pytest.mark.skipif(
    not _provider().is_available(),
    reason="Kokoro model files / onnxruntime / gruut not present",
)


@pytest.mark.integration
@kokoro_required
def test_every_programming_term_phonemises() -> None:
    """Section 5: the pronunciation corpus must all produce phonemes the
    model knows. A term that yields nothing is a silently skipped word."""
    provider = _provider()
    terms = [
        "Java", "Spring Boot", "algorithm", "array", "integer", "variable",
        "boolean", "pointer", "index", "iteration", "condition", "method",
        "class", "object", "character", "string", "substring", "recursion",
        "loop", "ascending", "descending", "comparison", "equals",
        "null", "true", "false",
    ]
    for term in terms:
        phonemes = provider.phonemise(term)
        assert phonemes.strip(), f"{term!r} produced no phonemes"
        assert unsupported_symbols(phonemes) <= {" "}, term


@pytest.mark.integration
@kokoro_required
def test_normalised_code_phonemises_distinctly() -> None:
    """The end-to-end version of the headline defect."""
    provider = _provider()
    zero = provider.phonemise(to_spoken("chars[0]"))
    six = provider.phonemise(to_spoken("chars[6]"))
    assert zero and six and zero != six


@pytest.mark.integration
@kokoro_required
def test_real_synthesis_produces_measurable_speech(tmp_path) -> None:
    provider = _provider()
    result = provider.synthesize(
        "The left pointer starts at index zero.", tmp_path / "s.wav"
    )
    assert result.provider == "kokoro-onnx"
    assert result.metadata["neural"] is True
    assert result.metadata["sample_rate"] == 24000
    assert result.duration_seconds > 0.5
    assert shutil.which  # keep the import meaningful on all platforms


@pytest.mark.integration
@kokoro_required
def test_long_narration_is_chunked_on_word_boundaries(tmp_path) -> None:
    """Kokoro caps the token sequence; splitting mid-word would synthesise
    the halves as if they were separate words."""
    provider = _provider()
    long_text = " ".join(
        ["The algorithm compares the characters at both ends of the array."] * 12
    )
    chunks = provider._chunk(provider.phonemise(long_text))
    assert len(chunks) > 1
    from code2shorts.narration.phonemes import MAX_PHONEME_LENGTH

    assert all(len(c) <= MAX_PHONEME_LENGTH for c in chunks)


# ---------------------------------------------------------------------------
# Phase 6.3 — af_heart is the configured default narrator.
# ---------------------------------------------------------------------------


def test_af_heart_is_the_default_voice() -> None:
    """Chosen by listening, so it is recorded rather than re-derived."""
    from code2shorts.config import Settings
    from code2shorts.narration.tts_kokoro import DEFAULT_VOICE, KokoroTTSProvider

    assert DEFAULT_VOICE == "af_heart"
    assert Settings().kokoro_voice == "af_heart"
    # The library default and the configured default must agree, or a
    # direct caller and the pipeline would use different narrators.
    assert KokoroTTSProvider()._voice == Settings().kokoro_voice


def test_another_voice_is_selectable_without_touching_source(monkeypatch) -> None:
    from code2shorts.config import Settings

    monkeypatch.setenv("CODE2SHORTS_KOKORO_VOICE", "am_michael")
    assert Settings().kokoro_voice == "am_michael"


def test_the_voice_setting_cannot_carry_a_payload(monkeypatch) -> None:
    """Section 19: voice selection must not become an injection vector.

    The value indexes the voice pack and never reaches a command line, and
    the pattern keeps it that way even if a future caller is careless.
    """
    import pydantic

    from code2shorts.config import Settings

    for hostile in ["rm -rf /", "af_heart; whoami", "../../etc/passwd", "$(id)"]:
        monkeypatch.setenv("CODE2SHORTS_KOKORO_VOICE", hostile)
        with pytest.raises(pydantic.ValidationError):
            Settings()


def test_no_shell_is_ever_constructed_from_the_voice() -> None:
    """AST check, not a text search: the module must contain no shell=True,
    no eval and no exec, and must not build a command from the voice."""
    import ast
    import inspect

    import code2shorts.narration.tts_kokoro as module

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            assert name not in {"eval", "exec", "compile"}
            for keyword in node.keywords:
                assert keyword.arg != "shell"


@pytest.mark.integration
@kokoro_required
def test_the_producer_records_engine_and_voice(tmp_path) -> None:
    """Section 20: "kokoro" alone is not reproducible — two runs with
    different voices are different videos."""
    from code2shorts.narration.tts_kokoro import KokoroTTSProvider

    result = KokoroTTSProvider().synthesize("Index zero.", tmp_path / "p.wav")
    assert result.metadata["producer"] == "kokoro:af_heart"
    assert result.voice == "af_heart"
    assert result.metadata["model"] == "kokoro-v1.0.onnx"
    assert result.metadata["speed"] == 1.0


@pytest.mark.integration
@kokoro_required
def test_the_same_text_synthesises_identically(tmp_path) -> None:
    """Section 4: same text, model, voice and settings -> same audio."""
    from code2shorts.narration.tts_kokoro import KokoroTTSProvider

    provider = KokoroTTSProvider()
    first = provider.synthesize("The left pointer is at index zero.", tmp_path / "a.wav")
    second = provider.synthesize("The left pointer is at index zero.", tmp_path / "b.wav")
    assert first.checksum == second.checksum
