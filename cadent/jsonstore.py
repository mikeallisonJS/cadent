"""Row-level delta writes for vocabulary.json and snippets.json (spec §5.4).

Same mechanics as `config_store` — read current disk contents, apply only this
row's delta, write atomically — and the **opposite apply contract**, which is
stated in each file's own `_editing` note:

    vocabulary.json / snippets.json   re-read at the start of every dictation,
                                      so writing the file *is* applying it
    config.json                       read once at startup, so changes apply
                                      the next time Cadent starts

Because the delta is applied against whatever is on disk right now, `_comment`,
`_editing`, unknown keys and key order round-trip **by construction** rather
than by the serializer remembering to preserve them. An external edit to a
different entry is never stomped.

Nothing here refuses an edit. Duplicate terms, triggers that normalize
identically, pure-punctuation triggers and empty replacements are all written:
writing two colliding keys to JSON is lossless, and the collapse only happens
at load. Warning the user is the pane's job; refusing under their hands is
nobody's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class UnreadableFile(Exception):
    """The file on disk is not parseable, so nothing may be written to it.

    The pane disables editing and shows the loader's message plus [Open file]
    and [Reload], so the UI can never overwrite a file it failed to read.
    """


def read_raw(path: Path) -> dict:
    """The file as a raw dict, preserving comments and unknown keys."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise UnreadableFile(str(exc)) from exc
    if not isinstance(data, dict):
        raise UnreadableFile(f"{path.name} is not a JSON object")
    return data


def write_raw(path: Path, raw: dict) -> None:
    """Temp file + os.replace, so a crash mid-write can't truncate the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# ---- vocabulary.json -------------------------------------------------------
#
# Entries are bare strings or {"term": ..., "soundsLike": [...]}. Row order is
# biasing priority: pack_hotwords() packs in file order and drops the overflow,
# so a write must never reorder rows it wasn't asked to.

def _entry_term(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict) and isinstance(entry.get("term"), str):
        return entry["term"].strip() or None
    return None


def _make_entry(term: str, sounds_like: tuple[str, ...] | list[str]) -> object:
    aliases = [a.strip() for a in sounds_like if a and a.strip()]
    return {"term": term, "soundsLike": aliases} if aliases else term


def upsert_term(path: Path, term: str, sounds_like: tuple[str, ...] | list[str] = (),
                replacing: str | None = None) -> None:
    """Add or edit one term. New rows append — order is priority, so a new
    term must not displace an existing one."""
    raw = read_raw(path)
    terms = list(raw.get("terms") or [])
    target = replacing if replacing is not None else term
    for i, entry in enumerate(terms):
        if _entry_term(entry) == target:
            terms[i] = _make_entry(term, sounds_like)
            break
    else:
        terms.append(_make_entry(term, sounds_like))
    raw["terms"] = terms
    write_raw(path, raw)


def remove_term(path: Path, term: str) -> None:
    raw = read_raw(path)
    raw["terms"] = [e for e in (raw.get("terms") or []) if _entry_term(e) != term]
    write_raw(path, raw)


def reorder_terms(path: Path, order: list[str]) -> None:
    """Rewrite row order — and only row order.

    The drag handle is the only affordance that does this, and it says so,
    because order is the hint budget's priority. A header click sorts the
    *view* only, so tidying can never silently demote terms out of the budget.
    """
    raw = read_raw(path)
    existing = list(raw.get("terms") or [])
    by_term = {_entry_term(e): e for e in existing}
    reordered = [by_term[t] for t in order if t in by_term]
    reordered += [e for e in existing if _entry_term(e) not in set(order)]
    raw["terms"] = reordered
    write_raw(path, raw)


# ---- snippets.json ---------------------------------------------------------
#
# A flat trigger -> replacement mapping, where keys starting with "_" are
# comments. Order is inert here, so an edit keeps the key in place.

def upsert_snippet(path: Path, trigger: str, replacement: str,
                   replacing: str | None = None) -> None:
    raw = read_raw(path)
    if replacing is not None and replacing != trigger and replacing in raw:
        # Rebuild in place so renaming a trigger keeps its position rather
        # than moving the row to the end under the user's cursor.
        raw = {(trigger if k == replacing else k): (replacement if k == replacing else v)
               for k, v in raw.items()}
    else:
        raw[trigger] = replacement
    write_raw(path, raw)


def remove_snippet(path: Path, trigger: str) -> None:
    raw = read_raw(path)
    raw.pop(trigger, None)
    write_raw(path, raw)
