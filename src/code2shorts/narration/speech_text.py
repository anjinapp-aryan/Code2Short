"""Narration text -> the words a narrator should actually say.

Measured problem, not a hypothetical one. Sent straight to a synthesiser,
real Code2Shorts narration comes out wrong, and wrong in the worst way —
confidently, with the meaning silently removed:

    chars[0]   -> "chars"          the index simply vanishes
    chars[6]   -> "chars"          indistinguishable from chars[0]
    a != b     -> "a b"            the comparison disappears
    left++     -> "left"           the increment disappears
    O(n)       -> "on"
    O(log n)   -> "oh-log n"

Both engines behave this way; it is a property of grapheme-to-phoneme, not
of Kokoro. A learner hearing "chars equals chars" for `chars[0] != chars[6]`
is being taught something false.

So the spoken form is derived separately from the written one. SUBTITLES
KEEP THE ORIGINAL TEXT — a reader wants to see `chars[0]`, while a
listener needs to hear "chars at index zero".

Everything here is a general rule about code notation. No algorithm, no
variable name and no fixture appears in this module, and a test enforces
that.
"""

from __future__ import annotations

import re

# Order matters: the more specific pattern must win. `O(log n)` has to be
# recognised before the generic `name(...)` call form, and `!=` before `=`.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # --- complexity notation ------------------------------------------
    # "constant", not "constant time": the notation is used for space as
    # well, and "O(1) space" must not be read as "constant time space".
    (re.compile(r"\bO\(\s*1\s*\)"), "constant"),
    (re.compile(r"\bO\(\s*log\s*n\s*\)", re.IGNORECASE), "order log n"),
    (re.compile(r"\bO\(\s*n\s*log\s*n\s*\)", re.IGNORECASE), "order n log n"),
    (re.compile(r"\bO\(\s*n\s*(?:\^|\*\*)\s*2\s*\)", re.IGNORECASE), "order n squared"),
    (re.compile(r"\bO\(\s*n²\s*\)", re.IGNORECASE), "order n squared"),
    (re.compile(r"\bO\(\s*n\s*\)", re.IGNORECASE), "order n"),
    # --- indexing ------------------------------------------------------
    # `chars[left]` -> "chars at index left"; the index is the whole point
    # of the expression and is exactly what the synthesiser was dropping.
    (re.compile(r"\b([A-Za-z_]\w*)\s*\[\s*([^\]]+?)\s*\]"), r"\1 at index \2"),
    # --- increment / decrement ----------------------------------------
    (re.compile(r"\b([A-Za-z_]\w*)\s*\+\+"), r"\1 increases by one"),
    (re.compile(r"\b([A-Za-z_]\w*)\s*--"), r"\1 decreases by one"),
    (re.compile(r"\+\+\s*\b([A-Za-z_]\w*)"), r"\1 increases by one"),
    (re.compile(r"--\s*\b([A-Za-z_]\w*)"), r"\1 decreases by one"),
    # --- comparison and assignment ------------------------------------
    (re.compile(r"\s*!=\s*"), " is not equal to "),
    (re.compile(r"\s*==\s*"), " equals "),
    (re.compile(r"\s*<=\s*"), " is less than or equal to "),
    (re.compile(r"\s*>=\s*"), " is greater than or equal to "),
    (re.compile(r"\s*&&\s*"), " and "),
    (re.compile(r"\s*\|\|\s*"), " or "),
    (re.compile(r"\s*<\s*"), " is less than "),
    (re.compile(r"\s*>\s*"), " is greater than "),
    # --- arithmetic that survives inside an index -----------------------
    (re.compile(r"\s+-\s+"), " minus "),
    (re.compile(r"\s+\+\s+"), " plus "),
    # --- leftovers ------------------------------------------------------
    # A bare method call reads better without its empty parentheses.
    (re.compile(r"\b([A-Za-z_]\w*)\(\s*\)"), r"\1"),
]

_COLLAPSE_SPACE = re.compile(r"\s{2,}")


def to_spoken(text: str) -> str:
    """Rewrite narration into the words a narrator should say.

    Deliberately conservative: it rewrites notation, never content. Prose
    that contains no code notation comes back unchanged, so the common
    case cannot be damaged by this layer.
    """
    spoken = text
    for pattern, replacement in _RULES:
        spoken = pattern.sub(replacement, spoken)
    # A trailing space before punctuation is what the operator rules leave
    # behind, and it becomes an audible hesitation.
    spoken = re.sub(r"\s+([,.;:!?])", r"\1", spoken)
    return _COLLAPSE_SPACE.sub(" ", spoken).strip()
