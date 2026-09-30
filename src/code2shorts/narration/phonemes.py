"""gruut IPA -> Kokoro phoneme-id encoding.

Why this module exists at all: Kokoro-82M was trained on espeak/misaki
IPA, and every packaged G2P that produces exactly that output is GPL —
`phonemizer`, `phonemizer-fork` and espeak-ng itself. Code2Shorts is MIT
and publicly distributed, so none of them may enter the dependency tree.
`gruut` is MIT and produces IPA of its own; this module reconciles the two
alphabets so the permissive path can drive the Apache-2.0 model.

The difference between them turns out to be purely notational, which is
why the reconciliation is a lookup and a split rather than a model:

    gruut token   Kokoro symbols   why
    ------------  ---------------  -------------------------------------
    'ˈaɪ'         'ˈ' 'a' 'ɪ'      gruut bundles the stress mark and the
                                   diphthong into one token; Kokoro has
                                   each piece as its own symbol
    'd͡ʒ'          'ʤ'              gruut ties the affricate; Kokoro has a
                                   precomposed symbol for it
    '|' / '‖'     ',' / '.'        phrase and sentence breaks; Kokoro
                                   expresses prosody with punctuation

Nothing here is language-specific beyond IPA, and nothing is specific to
any algorithm or narration text.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

MAX_PHONEME_LENGTH = 510
"""Kokoro's positional limit. Longer input must be split by the caller;
truncating would cut a sentence mid-word."""

TIE_BAR = "͡"
"""U+0361 COMBINING DOUBLE INVERTED BREVE — the affricate tie in 'd͡ʒ'."""

AFFRICATES = {
    "d͡ʒ": "ʤ",   # d͡ʒ -> ʤ
    "t͡ʃ": "ʧ",   # t͡ʃ -> ʧ
    "d͡ʑ": "ʣ",   # d͡ʑ -> ʣ
    "t͡ɕ": "ʨ",   # t͡ɕ -> ʨ
    "t͡ʂ": "ʦ",   # t͡s -> ʦ
}
"""Precomposed symbols Kokoro already has. Mapping to these rather than
splitting into two consonants keeps the affricate a single unit, which is
how the model was trained to see it."""

BREAKS = {
    "|": ",",      # gruut minor (phrase) break
    "‖": ".",  # gruut major (sentence) break
    "‖": ".",
}


VOCAB_PATH = Path(__file__).parent / "data" / "kokoro_vocab.json"
"""The model's own symbol table, vendored as data.

Kokoro's ONNX graph takes token IDs, so the mapping from IPA symbol to ID
is part of the model's interface — it is Apache-2.0 data, not code, and
114 entries of it. Vendoring means Code2Shorts needs no TTS wrapper
package at all: the only runtime dependencies are `onnxruntime` (MIT) and
`gruut` (MIT).

It also avoids a trap. The obvious alternative, reading the table out of
the `kokoro-onnx` package, cannot be done without importing it, and that
package imports espeak-ng at module load — pulling GPL code in through
the back door of a licence check that had just been passed."""


@lru_cache(maxsize=1)
def kokoro_vocab() -> dict[str, int]:
    """Symbol -> token id, as the model was exported with."""
    with VOCAB_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_token(token: str) -> str:
    """One gruut token -> the Kokoro symbols that spell it.

    Order matters: affricates are matched before the tie bar is stripped,
    so 'd͡ʒ' becomes the single symbol 'ʤ' rather than the pair 'd' 'ʒ'.
    """
    if token in BREAKS:
        return BREAKS[token]
    for tied, precomposed in AFFRICATES.items():
        if tied in token:
            token = token.replace(tied, precomposed)
    return token.replace(TIE_BAR, "")


def to_kokoro_phonemes(word_phonemes: list[str]) -> str:
    """gruut's per-word phoneme list -> a Kokoro phoneme string."""
    return "".join(normalize_token(token) for token in word_phonemes)


def encode(phonemes: str) -> list[int]:
    """Phoneme string -> model token ids, unknown symbols dropped.

    Dropping is deliberate and is reported by `unsupported_symbols`:
    substituting a guess would put a sound in the speech that the text
    never asked for, which is worse than a missing one.
    """
    vocab = kokoro_vocab()
    return [vocab[symbol] for symbol in phonemes if symbol in vocab]


def unsupported_symbols(phonemes: str) -> set[str]:
    """Symbols `encode` would silently drop. Tests assert this is empty
    for ordinary English narration; a non-empty result on real text means
    the mapping table above needs another entry, not a wider tolerance."""
    vocab = kokoro_vocab()
    return {symbol for symbol in phonemes if symbol not in vocab}
