"""Snippets: whole-utterance triggers, normalized matching, tolerant loading."""

import json

from cadent import snippets


def write(tmp_path, data):
    path = tmp_path / "snippets.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---- normalize -----------------------------------------------------------

def test_normalize_lowercases_and_strips_punctuation():
    assert snippets.normalize("My Email Sig.") == "my email sig"
    assert snippets.normalize("  Hello,   world!  ") == "hello world"
    assert snippets.normalize("what's up?") == "whats up"


def test_normalize_handles_unicode_punctuation():
    assert snippets.normalize("don’t — stop") == "dont stop"


# ---- load ----------------------------------------------------------------

def test_load_missing_file_is_empty_without_warning(tmp_path):
    loaded, warning = snippets.load(tmp_path / "snippets.json")
    assert loaded == {}
    assert warning is None


def test_load_normalizes_triggers(tmp_path):
    path = write(tmp_path, {"My Email Sig.": "Best,\nMike"})
    loaded, warning = snippets.load(path)
    assert loaded == {"my email sig": "Best,\nMike"}
    assert warning is None


def test_load_skips_underscore_comment_keys(tmp_path):
    path = write(tmp_path, {"_comment": "how this file works", "hi": "Hello!"})
    loaded, _ = snippets.load(path)
    assert loaded == {"hi": "Hello!"}


def test_load_malformed_json_warns_and_is_empty(tmp_path):
    path = tmp_path / "snippets.json"
    path.write_text("{not json", encoding="utf-8")
    loaded, warning = snippets.load(path)
    assert loaded == {}
    assert warning and "snippets.json" in warning


def test_load_non_object_top_level_warns_and_is_empty(tmp_path):
    path = tmp_path / "snippets.json"
    path.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    loaded, warning = snippets.load(path)
    assert loaded == {}
    assert warning


def test_load_non_string_replacement_skipped_with_warning(tmp_path):
    path = write(tmp_path, {"good": "text", "bad": 42})
    loaded, warning = snippets.load(path)
    assert loaded == {"good": "text"}
    assert warning and "bad" in warning


# ---- match ---------------------------------------------------------------

def test_match_is_whole_utterance_and_punctuation_insensitive():
    table = {"my email sig": "Best,\nMike"}
    assert snippets.match(table, "My email sig.") == "Best,\nMike"
    assert snippets.match(table, "my EMAIL sig!") == "Best,\nMike"


def test_match_rejects_partial_and_containing_utterances():
    table = {"my email sig": "Best,\nMike"}
    assert snippets.match(table, "insert my email sig here") is None
    assert snippets.match(table, "my email") is None


# ---- first-run example ---------------------------------------------------

def test_ensure_example_creates_valid_commented_file(tmp_path):
    path = tmp_path / "snippets.json"
    snippets.ensure_example(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert any(k.startswith("_") for k in data)          # explains itself
    loaded, warning = snippets.load(path)
    assert warning is None


def test_the_seeded_file_ships_no_live_snippets(tmp_path):
    """The old "my email signature" sample was live enough to inject
    placeholder contact details the first time anyone said the phrase. The
    pane's empty state teaches by example instead (M4 §5.4)."""
    path = tmp_path / "snippets.json"
    snippets.ensure_example(path)
    table, _warning = snippets.load(path)
    assert table == {}


def test_ensure_example_never_overwrites(tmp_path):
    path = write(tmp_path, {"mine": "kept"})
    snippets.ensure_example(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"mine": "kept"}
