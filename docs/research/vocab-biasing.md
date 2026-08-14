# Vocabulary biasing in faster-whisper + post-correction landscape

Research for ticket #30 (M2 ticket 03 — vocabulary biasing research). Date: 2026-07-28.
Stack context: faster-whisper (CTranslate2), default model `distil-small.en`, CPU-only, Python 3.11 uv `.venv`, Windows.

## 1. Biasing mechanisms — what the code actually does

Source read directly: [`faster_whisper/transcribe.py` (SYSTRAN/faster-whisper master)](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py).

Both mechanisms work the same way underneath: extra tokens are placed in the decoder's *previous-context* slot, after the `<|startofprev|>` token and before the `<|startoftranscript|>` sequence. Whisper was trained so that text in this slot is treated as "the transcript so far", which softly biases the decoder toward re-using its spelling/vocabulary. Neither is a lattice/shallow-fusion boost — there is no per-word weight; it is pure LM conditioning.

### `initial_prompt`

- Docstring: "Optional text string or iterable of token ids to provide as a prompt for each window."
- Mechanics: encoded once as `" " + initial_prompt.strip()` and appended to `all_tokens` **before** the segment loop. It reaches the decoder only as part of `previous_tokens` (the sliding context). Consequences:
  - Window 1 always sees it.
  - Later windows see it only while it still fits in the rolling `max_length // 2 - 1` context window **and** `condition_on_previous_text=True`; with `condition_on_previous_text=False` (or a temperature fallback above `prompt_reset_on_temperature`) the context is reset and the initial_prompt is gone after window 1.

### `hotwords`

- Docstring: "Hotwords/hint phrases to the model. Has no effect if prefix is not None."
- Mechanics, `get_prompt()` verbatim:

  ```python
  if previous_tokens or (hotwords and not prefix):
      prompt.append(tokenizer.sot_prev)
      if hotwords and not prefix:
          hotwords_tokens = tokenizer.encode(" " + hotwords.strip())
          if len(hotwords_tokens) >= self.max_length // 2:
              hotwords_tokens = hotwords_tokens[: self.max_length // 2 - 1]
          prompt.extend(hotwords_tokens)
      if previous_tokens:
          prompt.extend(previous_tokens[-(self.max_length // 2 - 1) :])
  ```

- `get_prompt(..., hotwords=options.hotwords)` is called **once per window**, so hotwords are re-injected into *every* window (unlike initial_prompt), prepended ahead of the rolling previous-token context. They are silently ignored when `prefix` is set (prefix applies to window 0 only).

### Limits

- `self.max_length = 448` (Whisper's decoder max), so the hotwords/prompt budget is `448 // 2 - 1 = 223 tokens` (~150–170 English words). Both hotwords and carried-over context are truncated to that, and they *share* the window's prompt region — a huge hotword string starves the previous-text context.
- For Cadent this is comfortable: 10–100 short terms ≈ 30–250 tokens; near the top end, trim or prioritize.

### Failure modes (documented)

- **Prompt echo**: prompt text can leak verbatim (or paraphrased) into the transcript, most often on short audio or leading silence — [openai/whisper discussion #1150](https://github.com/openai/whisper/discussions/1150), [#1486](https://github.com/openai/whisper/discussions/1486). Mitigations: VAD-trim leading silence (Cadent already uses push-to-talk chunks), keep the prompt short, and a post-pass sanity check that inserted text isn't just the vocab list.
- **Bias too weak**: conditioning is soft; rare terms still lose to acoustically-likely common words. [OpenAI's Whisper prompting guide](https://cookbook.openai.com/examples/whisper_prompting_guide) notes prompts are ~224-token limited, reliability is limited, and glossary-style lists work but natural prose containing the terms tends to bias more strongly.
- **Bias too strong / hallucination**: with silence or noise, prompt content is the decoder's best guess and gets emitted (same echo issue as above).

### Recommendation for a 10–100-term user vocab

Use **`hotwords`** as the primary mechanism: it exists precisely for this (a term list, not a style prompt), it is re-injected every window so behavior does not depend on `condition_on_previous_text`, and it leaves `initial_prompt` free for style conditioning later. For Cadent's short single-window utterances the two are nearly equivalent, but hotwords is the semantically-correct, window-stable choice. Format: space/comma-separated terms, e.g. `"Cadent, faster-whisper, CTranslate2, ..."`.

## 2. Distil-model caveats

- The [distil-small.en model card](https://huggingface.co/distil-whisper/distil-small.en) documents **no prompting support at all** — no mention of `initial_prompt`, `<|startofprev|>`, or `condition_on_previous_text`. Distil-Whisper was trained/evaluated with the *chunked* long-form algorithm, not OpenAI's sequential previous-context algorithm.
- Community reports say prompting distil models is markedly weaker than with full Whisper: users "can't get distil models to generate expected tokens" from the same initial_prompt, and initial_prompt "has not been found to make much impact" ([huggingface/distil-whisper discussion #118](https://github.com/huggingface/distil-whisper/discussions/118), [issue #20](https://github.com/huggingface/distil-whisper/issues/20) — the latter never even got a maintainer answer). `condition_on_previous_text=True` is also reported to *degrade* distil output on long audio.
- **Implication for Cadent**: biasing on `distil-small.en` should be treated as best-effort, not the load-bearing mechanism. Post-correction must carry most of the vocabulary guarantee. Worth a small empirical check during implementation (hotwords on/off against a test list); if biasing proves useless on distil, the vocab feature still works via the post-pass, and switching the default model to a non-distil small.en remains a config-level escape hatch.

## 3. Post-correction landscape

### Approaches

| Approach | Catches | Misses / risks | Library |
|---|---|---|---|
| Exact / case-insensitive replace | casing fixes ("cadent" → "Cadent") | any phonetic misrecognition | stdlib |
| Edit-distance / token similarity | close misspellings ("kubernets" → "Kubernetes") | homophones spelled differently ("colonel"/"kernel") | rapidfuzz |
| Phonetic encoding (Soundex / Metaphone) | sound-alikes with different spelling | over-matches short words (Soundex is coarse: same code for many words) | jellyfish |
| Hybrid gated pipeline | both classes | complexity; needs thresholds | rapidfuzz + jellyfish |

### Prior art

- **A commercial Whisper dictation app** documents a 6-pass vocab pipeline: exact match → Levenshtein (edit-distance threshold) → Soundex phonetic encoding → bigram character-pair similarity → case-insensitive → word-boundary match, motivated by "ASR errors are phonetic" (e.g. "park it" for "Parakeet"). This is the closest published design to what ticket 04 needs.
- **Open-source dictation apps** in this space ship a built-in term list with casing/grammar fixup, or auto-learn terms from user corrections. None publishes threshold guidance.

> Source links for both were dropped when this repository was created: the URLs
> carried vendor names. The technique descriptions are unchanged, and the
> conclusions below rest on the reasoning, not on the citations.

### Library shortlist (all verified installable in a Windows cp311 uv venv)

- **rapidfuzz 3.14.5** (Apr 2026) — C++ backed, prebuilt Windows wheels ([PyPI](https://pypi.org/project/RapidFuzz/), [install docs](https://rapidfuzz.github.io/RapidFuzz/Installation.html)). Provides normalized Levenshtein/Jaro-Winkler ratios, `process.extractOne` over a term list, token-level scorers. The workhorse for the similarity gate.
- **jellyfish 1.2.1** — Rust-backed, `win_amd64` wheels for cp311/cp312, requires-python ≥3.9 ([PyPI JSON](https://pypi.org/pypi/jellyfish/json), [repo](https://github.com/jamesturk/jellyfish)). Soundex, Metaphone, NYSIIS, plus its own distance metrics. Use for the phonetic-equality gate.
- **Metaphone 0.6** (pure-Python Double Metaphone) — works, but last released **2016**, sdist-only ([PyPI JSON](https://pypi.org/pypi/Metaphone/json)). Only worth adding if single Metaphone proves too coarse; prefer jellyfish first (maintained, one dep covers phonetic + extra metrics).

**Recommendation**: `rapidfuzz + jellyfish`, both binary wheels, no compiler needed on Windows.

### Sketch of a safe hybrid pass (for ticket 04 to refine)

Per transcript word/n-gram (n up to the longest vocab term's word count), against each vocab term:

1. Exact (case-sensitive) match → leave untouched (already right).
2. Case-only mismatch → rewrite casing.
3. Rewrite only if **both** gates pass: `rapidfuzz` normalized similarity ≥ threshold (~0.8 for words ≥5 chars) **and** phonetic code match (Metaphone equality via jellyfish). Requiring both is the false-positive guard: string similarity alone rewrites neighbors ("test" → "Tess"), phonetics alone over-matches (Soundex maps thousands of words to the same code).
4. Skip candidates below a minimum length (≤3 chars) and dictionary-common words unless similarity is near-exact — short tokens are where false rewrites concentrate.

## 4. Double-apply / false-rewrite risk

- **Double-apply is structurally benign** if the post-pass starts with an exact-match check: when biasing already produced the correct term, pass 1 matches exactly and the pipeline stops for that token — it cannot be "corrected" into something else. The only ordering requirement is *exact match short-circuits before any fuzzy pass* (this is exactly why that pipeline puts exact first).
- **The real risk is false rewrites of ordinary words** that happen to resemble a vocab term. No formal published threshold standard exists for STT post-correction; practice (the layered design above, general fuzzy-matching guidance) converges on:
  - conjunctive gating (edit-distance **and** phonetic must agree) rather than any-single-signal;
  - length-scaled thresholds (short words need near-exact scores);
  - restricting candidates to word-boundary n-grams sized to the vocab term;
  - conservative default (~0.8+ normalized ratio) with per-term override possible in the vocab file schema.
- **Biasing↔post-correction interaction**: biasing raises the prior that the term is already spelled right (more exact-match short-circuits, fewer fuzzy rewrites) — they compose safely. One genuine interaction to watch: prompt echo can inject vocab terms into silence-adjacent transcripts; that is a biasing artifact, not a post-correction one, and is mitigated by VAD/push-to-talk framing, not thresholds.

## 5. Answers in one breath

1. `initial_prompt` = one-shot text prepended to the rolling previous-token context; `hotwords` = per-window injection after `<|startofprev|>`, ignored when `prefix` set; both are soft LM conditioning, budget ≈223 tokens each (shared prompt region), truncated silently.
2. Use `hotwords` for the vocab list; expect weak effect on `distil-small.en` (distil models were not trained for previous-context conditioning) — post-correction is the reliable layer.
3. Post-correction: rapidfuzz (similarity) + jellyfish (phonetics), exact-match-first pipeline with conjunctive similarity+phonetic gating, length-scaled ~0.8 threshold, word-boundary n-gram candidates. Double-apply is a non-issue with exact-first ordering; false rewrites are controlled by the conjunctive gate and short-word exclusions.
