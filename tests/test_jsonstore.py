"""Row-level delta writes for vocabulary.json and snippets.json (spec §5.4).

The pane is how people work; the file stays valid, hand-editable and re-read
per dictation. Everything here follows from that stance.
"""

import json

import pytest

from cadent import jsonstore, snippets, vocabulary


def write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def vocab(tmp_path):
    return write(tmp_path / "vocabulary.json", {
        "_comment": "hand-written note",
        "_editing": "edit me freely",
        "terms": ["Cadent", {"term": "Kubernetes", "soundsLike": ["cooper netties"]}],
    })


@pytest.fixture
def snips(tmp_path):
    return write(tmp_path / "snippets.json", {
        "_comment": "hand-written note",
        "my sig": "Best,\nMe",
    })


# ---- round-tripping is by construction, not by remembering ----------------

def test_a_term_edit_preserves_comments_and_unknown_keys(vocab):
    write(vocab, {**read(vocab), "some_future_key": 1})
    jsonstore.upsert_term(vocab, "Cadent", ["local flow"])
    raw = read(vocab)
    assert raw["_comment"] == "hand-written note"
    assert raw["_editing"] == "edit me freely"
    assert raw["some_future_key"] == 1


def test_an_external_edit_to_another_entry_is_never_stomped(vocab):
    raw = read(vocab)
    raw["terms"].append("Postgres")
    write(vocab, raw)
    jsonstore.upsert_term(vocab, "Cadent", ["local flow"])
    assert "Postgres" in [t if isinstance(t, str) else t["term"]
                          for t in read(vocab)["terms"]]


def test_writes_are_atomic_and_leave_no_temp_behind(tmp_path, vocab):
    jsonstore.upsert_term(vocab, "Postgres")
    assert [p.name for p in tmp_path.iterdir()] == ["vocabulary.json"]


# ---- vocabulary rows -------------------------------------------------------

def test_a_new_term_appends_because_order_is_priority(vocab):
    """pack_hotwords() packs in file order and drops the overflow, so a new
    term must not displace an existing one."""
    jsonstore.upsert_term(vocab, "Postgres")
    terms = [t if isinstance(t, str) else t["term"] for t in read(vocab)["terms"]]
    assert terms == ["Cadent", "Kubernetes", "Postgres"]


def test_editing_a_term_keeps_its_position(vocab):
    jsonstore.upsert_term(vocab, "Cadent", ["local flow"])
    assert read(vocab)["terms"][0] == {"term": "Cadent",
                                       "soundsLike": ["local flow"]}


def test_a_term_with_no_aliases_stays_a_bare_string(vocab):
    """The simplest thing a hand-editor writes stays the simplest thing on
    disk."""
    jsonstore.upsert_term(vocab, "Kubernetes", [])
    assert read(vocab)["terms"][1] == "Kubernetes"


def test_renaming_a_term_keeps_its_position(vocab):
    jsonstore.upsert_term(vocab, "K8s", ["kates"], replacing="Kubernetes")
    terms = read(vocab)["terms"]
    assert terms[1] == {"term": "K8s", "soundsLike": ["kates"]}
    assert len(terms) == 2


def test_removing_a_term_leaves_the_rest_alone(vocab):
    jsonstore.remove_term(vocab, "Kubernetes")
    assert read(vocab)["terms"] == ["Cadent"]
    assert read(vocab)["_comment"] == "hand-written note"


def test_reordering_rewrites_order_and_only_order(vocab):
    jsonstore.reorder_terms(vocab, ["Kubernetes", "Cadent"])
    terms = [t if isinstance(t, str) else t["term"] for t in read(vocab)["terms"]]
    assert terms == ["Kubernetes", "Cadent"]
    assert read(vocab)["_comment"] == "hand-written note"


def test_reordering_keeps_rows_the_caller_did_not_mention(vocab):
    """A filtered view must never silently drop the rows it isn't showing."""
    jsonstore.upsert_term(vocab, "Postgres")
    jsonstore.reorder_terms(vocab, ["Kubernetes", "Cadent"])
    terms = [t if isinstance(t, str) else t["term"] for t in read(vocab)["terms"]]
    assert terms == ["Kubernetes", "Cadent", "Postgres"]


def test_a_written_term_is_live_on_the_next_dictation(vocab):
    """Writing the file *is* applying it — there is no engine to restart."""
    jsonstore.upsert_term(vocab, "Postgres", ["post gres"])
    terms, warning = vocabulary.load(vocab)
    assert warning is None
    assert vocabulary.Term("Postgres", ("post gres",)) in terms


# ---- nothing is refused or reverted under the user's hands ----------------

def test_a_duplicate_term_is_written_anyway(vocab):
    """Writing two colliding keys to JSON is lossless; the collapse only
    happens at load. The pane warns inline; it does not refuse."""
    jsonstore.upsert_term(vocab, "Postgres")
    raw = read(vocab)
    raw["terms"].append("Postgres")
    write(vocab, raw)
    jsonstore.upsert_term(vocab, "Postgres", ["post gres"])
    assert len(read(vocab)["terms"]) == 4


def test_an_empty_replacement_is_written_anyway(snips):
    jsonstore.upsert_snippet(snips, "my sig", "")
    assert read(snips)["my sig"] == ""


# ---- snippets --------------------------------------------------------------

def test_a_snippet_edit_preserves_comments(snips):
    jsonstore.upsert_snippet(snips, "my sig", "Regards,\nMe")
    assert read(snips)["_comment"] == "hand-written note"
    assert read(snips)["my sig"] == "Regards,\nMe"


def test_a_new_snippet_is_added(snips):
    jsonstore.upsert_snippet(snips, "my address", "1 Main St")
    assert read(snips)["my address"] == "1 Main St"


def test_renaming_a_trigger_keeps_its_position(snips):
    jsonstore.upsert_snippet(snips, "my address", "1 Main St")
    jsonstore.upsert_snippet(snips, "signature", "Best,\nMe", replacing="my sig")
    assert list(read(snips)) == ["_comment", "signature", "my address"]


def test_removing_a_snippet_leaves_the_rest_alone(snips):
    jsonstore.upsert_snippet(snips, "my address", "1 Main St")
    jsonstore.remove_snippet(snips, "my sig")
    assert list(read(snips)) == ["_comment", "my address"]


def test_a_multi_line_replacement_round_trips(snips):
    jsonstore.upsert_snippet(snips, "my sig", "Line one\nLine two\n\nLine four")
    table, warning = snippets.load(snips)
    assert warning is None
    assert table[snippets.normalize("my sig")] == "Line one\nLine two\n\nLine four"


# ---- an unreadable file is never written to -------------------------------

def test_an_unparseable_file_refuses_every_write(tmp_path):
    """Editing is disabled in the pane, so the UI can never overwrite a file
    it failed to read."""
    path = tmp_path / "vocabulary.json"
    path.write_text('{"terms": [', encoding="utf-8")
    for call in (lambda: jsonstore.upsert_term(path, "x"),
                 lambda: jsonstore.remove_term(path, "x"),
                 lambda: jsonstore.reorder_terms(path, ["x"]),
                 lambda: jsonstore.upsert_snippet(path, "x", "y"),
                 lambda: jsonstore.remove_snippet(path, "x")):
        with pytest.raises(jsonstore.UnreadableFile):
            call()
    assert path.read_text(encoding="utf-8") == '{"terms": ['


def test_a_json_file_that_is_not_an_object_is_unreadable(tmp_path):
    path = tmp_path / "snippets.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(jsonstore.UnreadableFile):
        jsonstore.upsert_snippet(path, "x", "y")


def test_a_missing_file_is_simply_empty(tmp_path):
    path = tmp_path / "vocabulary.json"
    jsonstore.upsert_term(path, "Cadent")
    assert read(path)["terms"] == ["Cadent"]
