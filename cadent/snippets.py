"""Whole-utterance snippets: dictate a trigger phrase, insert canned text verbatim.

snippets.json (%LOCALAPPDATA%/Cadent) maps trigger phrase -> replacement.
Re-read at the start of each dictation so edits apply without a restart.
A malformed file warns and acts empty — it must never block dictation.
Keys starting with "_" are comments (JSON has no native ones).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

EXAMPLE = {
    "_comment": (
        "Cadent snippets. When a whole dictation exactly matches a trigger "
        "phrase below (ignoring case and punctuation), the replacement text is "
        "inserted verbatim instead — no cleanup is applied. Keys starting with "
        "an underscore are ignored."
    ),
    "_editing": (
        "Edit this file freely; it is re-read at the start of every dictation. "
        "If it becomes invalid JSON, Cadent warns and ignores it."
    ),
    # No live sample entries (M4 §5.4). The seeded "my email signature"
    # snippet was live enough to inject placeholder contact details into a
    # real document the first time anyone said the phrase. The prose above is
    # the only thing that ever taught hand-editors the JSON shape, so it
    # stays; the Snippets pane's empty state teaches by example instead.
    # No migration: existing installs keep what they have.
}


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — both triggers and
    transcripts pass through this, so matching survives STT punctuation."""
    stripped = "".join(c for c in text if not unicodedata.category(c).startswith("P"))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def load(path: Path) -> tuple[dict[str, str], str | None]:
    """Read snippets.json into {normalized trigger: replacement}.

    Returns (snippets, warning). A missing file is simply empty; a malformed
    one returns empty plus a human-readable warning — never an exception.
    """
    try:
        if not path.exists():
            return {}, None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, f"{path.name} is not valid JSON — snippets are off for this dictation."
    if not isinstance(data, dict):
        return {}, f"{path.name} must be a JSON object of trigger → replacement pairs."

    table: dict[str, str] = {}
    bad: list[str] = []
    for trigger, replacement in data.items():
        if trigger.startswith("_"):
            continue
        if not isinstance(replacement, str):
            bad.append(trigger)
            continue
        key = normalize(trigger)
        if key:
            table[key] = replacement
    warning = None
    if bad:
        warning = (f"{path.name}: ignored non-text replacement for "
                   f"{', '.join(repr(t) for t in bad)}.")
    return table, warning


def match(table: dict[str, str], transcript: str) -> str | None:
    """Whole-utterance match only: the entire normalized transcript must equal
    a trigger. Partial matches are deliberately out of scope for M2."""
    return table.get(normalize(transcript))


def ensure_example(path: Path) -> None:
    """First-run: ship a commented example the user can edit (M1 pattern)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EXAMPLE, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
