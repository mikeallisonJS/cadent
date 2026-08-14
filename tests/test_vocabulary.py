"""Vocabulary seam tests (M2 ticket 09; decisions in ticket 04).

Covers the acceptance shape: correction fixtures, the false-rewrite corpus
that pins the thresholds, and robustness (malformed file, oversized list).
"""

import json

from cadent import vocabulary


def vocab_file(tmp_path, data):
    path = tmp_path / "vocabulary.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---- load ------------------------------------------------------------------

def test_load_bare_strings_and_sounds_like_objects(tmp_path):
    path = vocab_file(tmp_path, {"terms": [
        "Kubernetes",
        {"term": "Parakeet", "soundsLike": ["park it", "para keet"]},
    ]})
    terms, warning = vocabulary.load(path)
    assert warning is None
    assert [t.term for t in terms] == ["Kubernetes", "Parakeet"]
    assert terms[0].sounds_like == ()
    assert terms[1].sounds_like == ("park it", "para keet")


def test_load_missing_file_is_empty(tmp_path):
    terms, warning = vocabulary.load(tmp_path / "vocabulary.json")
    assert terms == [] and warning is None


def test_load_malformed_json_warns_and_acts_empty(tmp_path):
    path = tmp_path / "vocabulary.json"
    path.write_text("{broken", encoding="utf-8")
    terms, warning = vocabulary.load(path)
    assert terms == []
    assert warning and "vocabulary.json" in warning


def test_load_wrong_shape_warns_and_acts_empty(tmp_path):
    path = vocab_file(tmp_path, ["Kubernetes"])   # missing {"terms": ...} wrapper
    terms, warning = vocabulary.load(path)
    assert terms == []
    assert warning and "vocabulary.json" in warning


def test_load_skips_bad_entries_with_warning(tmp_path):
    path = vocab_file(tmp_path, {"terms": [
        "Kubernetes", 42, {"soundsLike": ["no term"]}, {"term": "Parakeet"},
    ]})
    terms, warning = vocabulary.load(path)
    assert [t.term for t in terms] == ["Kubernetes", "Parakeet"]
    assert warning and "vocabulary.json" in warning


# ---- hotwords packing ------------------------------------------------------

def T(term, *sounds_like):
    return vocabulary.Term(term, tuple(sounds_like))


def words_as_tokens(text):
    """Stand-in tokenizer for tests: one token per word."""
    return len(text.split())


def test_hotwords_pack_canonical_terms_only_in_file_order():
    packed, dropped = vocabulary.pack_hotwords(
        [T("Kubernetes"), T("Parakeet", "park it")], words_as_tokens)
    assert packed == "Kubernetes, Parakeet"   # never the soundsLike variants
    assert dropped == []


def test_hotwords_empty_vocabulary_packs_none():
    packed, dropped = vocabulary.pack_hotwords([], words_as_tokens)
    assert packed is None and dropped == []


def test_hotwords_truncation_drops_whole_terms_from_the_end():
    terms = [T("alpha beta"), T("gamma delta"), T("epsilon zeta")]
    packed, dropped = vocabulary.pack_hotwords(terms, words_as_tokens, budget=4)
    assert packed == "alpha beta, gamma delta"   # never truncated mid-term
    assert dropped == ["epsilon zeta"]


def test_hotwords_all_terms_over_budget_packs_none():
    packed, dropped = vocabulary.pack_hotwords([T("a b c")], words_as_tokens, budget=2)
    assert packed is None and dropped == ["a b c"]


# ---- correction gate -------------------------------------------------------
# Gate order per ticket 04: exact → soundsLike alias → case-fix → fuzzy
# (rapidfuzz similarity AND Metaphone equality, conjunctive).

def test_exact_match_left_untouched():
    text = "deploy it to Kubernetes now"
    assert vocabulary.correct(text, [T("Kubernetes")]) == text


def test_sounds_like_alias_replaced_on_normalized_text():
    got = vocabulary.correct("just Park it, please", [T("Parakeet", "park it")])
    assert got == "just Parakeet, please"


def test_case_only_mismatch_fixed():
    got = vocabulary.correct("deploy to kubernetes.", [T("Kubernetes")])
    assert got == "deploy to Kubernetes."


def test_multi_word_term_case_fixed_across_words():
    got = vocabulary.correct("we use github actions here", [T("GitHub Actions")])
    assert got == "we use GitHub Actions here"


def test_fuzzy_plus_phonetic_rewrite():
    got = vocabulary.correct("ask alison about it", [T("Allison")])
    assert got == "ask Allison about it"


def test_fuzzy_plus_phonetic_rewrite_on_multi_word_span():
    got = vocabulary.correct("open visuel studio please", [T("Visual Studio")])
    assert got == "open Visual Studio please"


def test_fuzzy_rewrite_keeps_surrounding_punctuation():
    got = vocabulary.correct("Tell kubernetis: scale up.", [T("Kubernetes")])
    assert got == "Tell Kubernetes: scale up."


def test_corrected_span_not_rescanned():
    # Single pass: once "para keet" -> "Parakeet", the rewritten span is
    # consumed — a second term must not fire inside it.
    got = vocabulary.correct("the para keet sings", [T("Parakeet", "para keet"),
                                                     T("Keeton", "keet")])
    assert got == "the Parakeet sings"


def test_empty_vocab_is_identity():
    assert vocabulary.correct("hello world", []) == "hello world"


# False-rewrite corpus: ordinary sentences salted with near-misses — zero
# rewrites. Pins the thresholds + conjunctive phonetic gate against regression.

FALSE_REWRITE_CORPUS = [
    ("we test the code daily", [T("Tess")]),          # phonetics differ
    ("the colonel arrived early", [T("kernel")]),     # famous homophone, spelled differently
    ("get the code from the repo", [T("Git")]),       # <=3-char span: never fuzzy
    ("rest now and ship later", [T("Rust")]),         # 4-char span under 0.90
    ("that costs a lot", [T("cost")]),                # inflection, phonetics differ
    ("a parade of options", [T("Parakeet")]),         # low similarity
]


def test_false_rewrite_corpus_untouched():
    for text, terms in FALSE_REWRITE_CORPUS:
        assert vocabulary.correct(text, terms) == text


def test_short_spans_still_get_case_fix():
    # <=3 chars blocks only the fuzzy gate — exact-class fixes still apply.
    assert vocabulary.correct("push to git now", [T("Git")]) == "push to Git now"


def test_thresholds_are_tunable():
    assert vocabulary.correct("ask alison", [T("Allison")],
                              threshold=0.95) == "ask alison"


def test_ensure_example_written_once_and_loads_clean(tmp_path):
    path = tmp_path / "vocabulary.json"
    vocabulary.ensure_example(path)
    assert path.exists()
    terms, warning = vocabulary.load(path)
    # The example must parse, and must ship no live terms: a seeded term
    # biases the speech model for someone who never asked for it (M4 §5.4).
    assert warning is None and terms == []
    marker = path.read_text(encoding="utf-8")
    vocabulary.ensure_example(path)    # second run must not clobber edits
    assert path.read_text(encoding="utf-8") == marker
