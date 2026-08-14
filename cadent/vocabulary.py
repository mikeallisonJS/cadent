"""Custom vocabulary: STT biasing plus post-correction (M2 ticket 09).

vocabulary.json (%LOCALAPPDATA%/Cadent) holds {"terms": [...]}, each entry
a bare string or {"term": ..., "soundsLike": [...]}. Re-read at the start of
each dictation so edits apply without a restart; a malformed file warns and
acts empty — it must never block dictation. Keys starting with "_" at the top
level are comments.

Two layers (ticket 04): `hotwords` biasing into faster-whisper is best-effort;
the correction gate over the transcript carries the guarantee.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .snippets import normalize

EXAMPLE = {
    "_comment": (
        "Cadent vocabulary. Terms listed here are fed to the speech model "
        "as a hint and fixed up in the transcript when it comes out close but "
        "wrong (casing, fuzzy misspellings). Use soundsLike for consistent "
        "mis-hearings: when a dictation contains that exact phrase, it is "
        "replaced by the term. Keys starting with an underscore are ignored."
    ),
    "_editing": (
        "Edit this file freely; it is re-read at the start of every dictation. "
        "If it becomes invalid JSON, Cadent warns and ignores it."
    ),
    # No live sample terms (M4 §5.4): a seeded term biases the speech model
    # and rewrites transcripts for someone who never asked for it. The prose
    # above is what teaches the JSON shape; the Vocabulary pane's empty state
    # teaches by example.
    "terms": [],
}


@dataclass(frozen=True)
class Term:
    term: str
    sounds_like: tuple[str, ...] = ()


def load(path: Path) -> tuple[list[Term], str | None]:
    """Read vocabulary.json into an ordered term list.

    Returns (terms, warning). A missing file is simply empty; a malformed one
    returns empty plus a human-readable warning — never an exception.
    """
    try:
        if not path.exists():
            return [], None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], f"{path.name} is not valid JSON — vocabulary is off for this dictation."
    if not isinstance(data, dict) or not isinstance(data.get("terms"), list):
        return [], (f'{path.name} must be an object with a "terms" list — '
                    "vocabulary is off for this dictation.")

    terms: list[Term] = []
    bad: list[str] = []
    for entry in data["terms"]:
        if isinstance(entry, str) and entry.strip():
            terms.append(Term(entry.strip()))
        elif (isinstance(entry, dict) and isinstance(entry.get("term"), str)
              and entry["term"].strip()):
            aliases = tuple(a for a in entry.get("soundsLike", ())
                            if isinstance(a, str) and a.strip())
            terms.append(Term(entry["term"].strip(), aliases))
        else:
            bad.append(repr(entry))
    warning = None
    if bad:
        warning = f"{path.name}: ignored malformed entries: {', '.join(bad)}."
    return terms, warning


# faster-whisper spends at most max_length//2 - 1 = 223 tokens of the
# per-window prompt on hotwords; beyond that it truncates mid-term itself,
# so we truncate first, on whole terms (ticket 04).
HOTWORDS_TOKEN_BUDGET = 223


def pack_hotwords(terms: list[Term], count_tokens: Callable[[str], int],
                  budget: int = HOTWORDS_TOKEN_BUDGET) -> tuple[str | None, list[str]]:
    """Pack canonical terms (never soundsLike — biasing toward known
    misrecognitions would be harmful) comma-joined in file order.

    Returns (packed, dropped): the longest prefix of the list that fits the
    token budget, and the whole terms dropped from the end to make it fit.
    """
    kept: list[str] = []
    dropped: list[str] = []
    for t in terms:
        if dropped or count_tokens(", ".join(kept + [t.term])) > budget:
            dropped.append(t.term)
        else:
            kept.append(t.term)
    return (", ".join(kept) if kept else None), dropped


@dataclass(frozen=True)
class _Word:
    """A whitespace token's letter core, with surrounding punctuation left in
    place in the text (so rewriting 'kubernetis:' keeps the colon)."""
    start: int   # core start/end offsets into the original text
    end: int
    core: str


def _words(text: str) -> list[_Word]:
    words = []
    for m in re.finditer(r"\S+", text):
        start, end = m.start(), m.end()
        while start < end and unicodedata.category(text[start]).startswith("P"):
            start += 1
        while end > start and unicodedata.category(text[end - 1]).startswith("P"):
            end -= 1
        if end > start:
            words.append(_Word(start, end, text[start:end]))
    return words


def correct(text: str, terms: list[Term],
            threshold: float = 0.85, threshold_short: float = 0.90) -> str:
    """Fix vocabulary terms the STT got nearly right. Single left-to-right
    pass over word-boundary spans, longest span first (multi-word terms beat
    their fragments); a matched span is consumed and never re-scanned.

    Per-span gates, in order (ticket 04): exact match (leave untouched —
    double-apply is structurally impossible), soundsLike alias, case-only
    fix, then fuzzy — rapidfuzz similarity AND Metaphone equality, both
    required, and spans of <=3 characters are never fuzzy-rewritten. A false
    rewrite costs more than a missed correction.
    """
    if not terms:
        return text
    from jellyfish import metaphone  # lazy imports: keep app startup lean
    from rapidfuzz import fuzz

    prepared = [(t, {normalize(a) for a in t.sounds_like}) for t in terms]
    max_n = max(max((len(t.term.split()) for t in terms), default=1),
                max((len(a.split()) for t in terms for a in t.sounds_like),
                    default=1))

    def match_span(span: str) -> str | None:
        """None = no gate fired; otherwise the text the span should become
        (possibly identical, for an exact match that consumes the span)."""
        for t, _aliases in prepared:
            if span == t.term:
                return span
        norm = normalize(span)
        for t, aliases in prepared:
            if norm in aliases:
                return t.term
        for t, _aliases in prepared:
            if span.casefold() == t.term.casefold():
                return t.term
        if len(span) <= 3:
            return None
        thr = threshold_short if len(span) == 4 else threshold
        for t, _aliases in prepared:
            if (fuzz.ratio(span.casefold(), t.term.casefold()) / 100 >= thr
                    and metaphone(span) == metaphone(t.term)):
                return t.term
        return None

    words = _words(text)
    out: list[str] = []
    emitted = 0   # offset into text up to which output is already built
    i = 0
    while i < len(words):
        for n in range(min(max_n, len(words) - i), 0, -1):
            span_words = words[i:i + n]
            replacement = match_span(" ".join(w.core for w in span_words))
            if replacement is not None:
                start, end = span_words[0].start, span_words[-1].end
                out.append(text[emitted:start])
                out.append(replacement)
                emitted = end
                i += n
                break
        else:
            i += 1
    out.append(text[emitted:])
    return "".join(out)


def ensure_example(path: Path) -> None:
    """First-run: ship a commented example the user can edit (M1 pattern)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EXAMPLE, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
