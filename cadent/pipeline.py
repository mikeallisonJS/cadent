"""Dictation pipeline: transcribe → persist → snippets → vocab → cleanup →
inject → report.

The M1 primary test seam. Everything is constructor-injected: the STT engine
(via a supplier, since it lazy-loads), the injector, the history store, and
the cleaner. The raw transcript is persisted before anything can touch it; a
whole-utterance snippet match inserts its replacement verbatim and skips both
correction and cleanup; otherwise vocabulary correction runs either way, and
with cleanup on the cleaner then gets a chance — under the charter safety
contract, any cleanup failure silently inserts what it had.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import snippets, vocabulary

log = logging.getLogger(__name__)


@dataclass
class DictationReport:
    text: str = ""
    outcome: str = "empty"    # empty|not-ready|inserted|fallback|notify-only|failed
    on_clipboard: bool = False
    detail: str = ""
    snippet_expanded: bool = False
    stt_crashed: bool = False  # engine-fatal: the app should rebuild the engine (#38)
    app_name: str = ""         # focused app at injection time
    typing_failed: bool = False   # auto-learn signal (#45), from InjectionResult
    notices: list[str] = field(default_factory=list)   # non-fatal warnings to surface
    # Vocabulary terms that didn't fit the model's hotwords budget (M4 §5.4).
    # Surfaced by the Vocabulary pane only when it actually bit — exact counts
    # from the model's own tokenizer, which an in-pane estimate can't produce.
    dropped_hotwords: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, stt_supplier: Callable[[], object | None],
                 injector, history, sample_rate: int = 16_000,
                 history_enabled: bool = True,
                 snippets_path: Path | None = None,
                 cleaner=None,
                 cleanup_supplier: Callable[[], bool] | None = None,
                 vocab_path: Path | None = None,
                 vocab_threshold: float = 0.85,
                 vocab_threshold_short: float = 0.90,
                 on_cleanup_start: Callable[[], None] | None = None,
                 mic_zero_hint: str | None = None) -> None:
        self.stt_supplier = stt_supplier   # returns an SttEngine, or None while loading
        self.injector = injector
        self.history = history
        self.sample_rate = sample_rate
        self.history_enabled = history_enabled
        self.snippets_path = snippets_path  # None = snippets disabled (tests)
        self.cleaner = cleaner              # None = cleanup disabled (tests)
        # Is cleanup on? Asked at dictation time, not at construction.
        self.cleanup_supplier = cleanup_supplier
        self.vocab_path = vocab_path        # None = vocabulary disabled (tests)
        self.vocab_threshold = vocab_threshold
        self.vocab_threshold_short = vocab_threshold_short
        # The STT->cleanup boundary, so the overlay can split "processing"
        # into Transcribing and Cleaning up (M4 §4.3). Cleaned-up dictation's
        # <=3.5 s vs raw's <=2 s is entirely the LLM; naming the second phase
        # explains the wait rather than leaving an undifferentiated longer
        # spinner.
        self.on_cleanup_start = on_cleanup_start
        # `Capabilities.mic_permission_hint`: what an all-zero capture means
        # here, or None where silence is just silence (a muted mic). On
        # darwin a missing Microphone TCC grant raises no exception — it
        # records zeros — so transcribing them would fail silently forever.
        self.mic_zero_hint = mic_zero_hint

    def process(self, audio) -> DictationReport:
        if audio is None or len(audio) == 0:
            return DictationReport()
        if self.mic_zero_hint and not np.asarray(audio).any():
            return DictationReport(outcome="failed", detail=self.mic_zero_hint)

        engine = self.stt_supplier()
        if engine is None:
            return DictationReport(outcome="not-ready",
                                   detail="speech model is still loading")

        # Vocabulary: re-read per dictation, biased into the STT via hotwords.
        notices: list[str] = []
        terms: list[vocabulary.Term] = []
        hotwords = None
        dropped: list[str] = []
        if self.vocab_path is not None:
            terms, vocab_warning = vocabulary.load(self.vocab_path)
            if vocab_warning:
                log.warning(vocab_warning)
                notices.append(vocab_warning)
            # An engine with no hotwords equivalent (Parakeet, #72) is sent
            # none, and reports none dropped: nothing was ever going to be
            # sent, so calling the terms "dropped" would turn an over-budget
            # warning into a permanent one that means nothing.
            if getattr(engine, "supports_hotwords", True):
                # Fakes without a tokenizer get a rough chars/4 BPE estimate.
                count = getattr(engine, "count_tokens", None) or (lambda s: len(s) // 4 + 1)
                hotwords, dropped = vocabulary.pack_hotwords(terms, count)
                if dropped:
                    log.warning("vocabulary over the hotwords budget; dropped: %s",
                                ", ".join(dropped))   # deliberate: log only, no toast

        # A transcribe-time crash can leave the engine wedged (a CUDA failure
        # does — #38); flag it engine-fatal so the app rebuilds the engine
        # rather than feeding it further dictations.
        try:
            raw = engine.transcribe(audio, self.sample_rate, hotwords=hotwords)
        except Exception as exc:
            log.exception("STT engine crashed during transcription")
            return DictationReport(outcome="failed", stt_crashed=True, detail=str(exc))
        if not raw:
            return DictationReport()

        duration = len(audio) / self.sample_rate
        app_name = self.injector.focused_app_name()
        cleanup = (self.cleanup_supplier()
                   if self.cleanup_supplier is not None else False)

        # Transcript safety: persist the raw transcript BEFORE anything else.
        row_id = None
        if self.history_enabled:
            # The stored mode values stay "flow"/"raw": they are user data in
            # a shipped database, and a migration that risks it to rename a
            # string nobody reads is a bad trade (#113).
            row_id = self.history.add(raw, None, duration, app_name,
                                      "flow" if cleanup else "raw")

        # Snippets: re-read per dictation; matched on the *uncorrected*
        # transcript (correction could rewrite a trigger word); a match
        # inserts the replacement verbatim, skipping correction and cleanup.
        text, expanded = raw, False
        if self.snippets_path is not None:
            table, warning = snippets.load(self.snippets_path)
            if warning:
                log.warning(warning)
                notices.append(warning)
            replacement = snippets.match(table, raw)
            if replacement is not None:
                text, expanded = replacement, True

        # Vocab post-correction, both modes: `raw` in history stays the
        # uncorrected transcript; the correction shows up via `cleaned`.
        if not expanded and terms:
            text = vocabulary.correct(text, terms, self.vocab_threshold,
                                      self.vocab_threshold_short)

        # Cleanup: the LLM pass, fed the corrected text. Cleaner.clean
        # already falls back to its input on its own failure paths (not
        # loaded, timeout, diff-guard rejection); the extra net here keeps a
        # crashing cleaner from ever costing a dictation.
        if cleanup and not expanded and self.cleaner is not None:
            try:
                if self.on_cleanup_start is not None:
                    self.on_cleanup_start()
                text = self.cleaner.clean(text)
            except Exception:
                log.exception("cleanup crashed; inserting uncleaned text")

        if text != raw and row_id is not None:
            # Recorded before insertion (same write-before-inject rule as raw):
            # on failure, `cleaned` is what went to the clipboard fallback, and
            # `outcome` says whether it landed.
            self.history.set_cleaned(row_id, text)

        try:
            result = self.injector.insert(text, app_name)
        except Exception as exc:
            result = self.injector.last_resort(text)
            result.detail = result.detail or str(exc)

        if row_id is not None:
            self.history.set_outcome(row_id, result.outcome)

        return DictationReport(text=text, outcome=result.outcome,
                               on_clipboard=result.on_clipboard, detail=result.detail,
                               snippet_expanded=expanded, notices=notices,
                               app_name=app_name,
                               typing_failed=result.typing_failed,
                               dropped_hotwords=dropped)
