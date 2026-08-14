"""Automated acceptance evidence for M2 ticket 10 (flow-mode E2E verification).

Mirrors scripts/verify_e2e.py (M1 ticket 17) and reuses its window plumbing.
Drives the real Pipeline — real STT, real llama.cpp Cleaner, real Injector,
real (temp) History — into a live Notepad:

    python scripts/verify_e2e_m2.py --flow-latency    # 3/10/30 s SAPI dictations, 10 s gate <= 3.5 s
    python scripts/verify_e2e_m2.py --fallbacks       # crash / timeout / diff-guard -> raw inserted
    python scripts/verify_e2e_m2.py --raw-regression  # rerun the M1 matrix + latency
    python scripts/verify_e2e_m2.py --all --out report.json

SAPI-synthesized utterances stand in for the microphone, exactly like the M1
latency proxy. Real-mic dictations, cleanup quality, vocab spelling, snippet
triggers, and the toggle surfaces stay on the ticket for the owner.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

# The M1 test string carries ✓ and 𝄞; a cp1252 console must not kill the run.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import verify_e2e as m1  # noqa: E402  (window plumbing + injector factory)

from cadent import cleanup as cleanup_mod  # noqa: E402
from cadent.cleanup import Cleaner  # noqa: E402
from cadent.config import Config  # noqa: E402
from cadent.config_store import ConfigStore  # noqa: E402
from cadent.history import History  # noqa: E402
from cadent.pipeline import Pipeline  # noqa: E402

# Filler-laden dictations at the ticket's three lengths (SAPI ~150 wpm).
UTTER_3S = "Um, can you send me the, uh, the latest draft?"
UTTER_10S = (
    "Um, please schedule the quarterly review for Thursday afternoon, and, uh, "
    "remind the design team that the updated mockups are due this week."
)
UTTER_30S = (
    "So, um, I wanted to follow up on the conversation we had yesterday about "
    "the onboarding flow. Uh, I think the first screen has too many, too many "
    "options, and, um, new users are getting stuck before they ever reach the "
    "dashboard. Let's, uh, let's cut the account questions down to just email "
    "and password, move the workspace setup into a, um, a separate step after "
    "sign in, and then measure whether the completion rate actually improves "
    "before we touch anything else."
)

FALLBACK_RAW = (
    "um so the quick brown fox uh jumps over the lazy dog and, and runs into "
    "the forest before anyone can catch it"
)


def speak_to_wav(text: str, path: Path) -> float:
    """Synthesize `text` to a 16 kHz mono WAV via SAPI; returns duration in s."""
    import win32com.client

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 18  # SAFT16kHz16BitMono
    stream.Open(str(path), 3)  # SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AllowAudioOutputFormatChangesOnNextSet = False
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def load_wav(path: Path):
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(path), sampling_rate=16000)
    return audio, len(audio) / 16000.0


def rss_mb() -> int:
    import psutil

    return round(psutil.Process().memory_info().rss / 1e6)


# ---- real components, assembled the way app.py assembles them ---------------

class _TimingEngine:
    """Pass-through STT wrapper so the report can split release->inserted."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self.last_s = 0.0

    def transcribe(self, audio, sample_rate, hotwords=None) -> str:
        t0 = time.perf_counter()
        text = self._engine.transcribe(audio, sample_rate, hotwords=hotwords)
        self.last_s = time.perf_counter() - t0
        return text

    def count_tokens(self, text: str) -> int:
        return self._engine.count_tokens(text)


class _TimingCleaner:
    def __init__(self, cleaner: Cleaner) -> None:
        self._cleaner = cleaner
        self.last_s = 0.0

    def clean(self, raw: str) -> str:
        t0 = time.perf_counter()
        text = self._cleaner.clean(raw)
        self.last_s = time.perf_counter() - t0
        return text


class _StubEngine:
    """Fixed transcript for the fallback checks: STT accuracy is verified
    elsewhere; these checks are about what happens after the transcript."""

    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio, sample_rate, hotwords=None) -> str:
        return self.text


class _CannedLLM:
    """llama.cpp stand-in that returns a fixed completion instantly."""

    def __init__(self, content: str) -> None:
        self.content = content

    def create_chat_completion(self, **_kwargs) -> dict:
        return {"choices": [{"message": {"content": self.content}}]}


class _CrashingLLM:
    def create_chat_completion(self, **_kwargs) -> dict:
        raise RuntimeError("simulated llama.cpp crash mid-generation")


def make_cleaner(cfg: Config) -> Cleaner:
    """The app's Cleaner, pinned to the CPU whatever config.json says.

    Pinned rather than configurable (#116): the M2 flow gate is a **CPU-only
    primary machine** number, and since cleanup started reaching for the GPU
    the default would quietly re-point this harness at hardware the charter
    doesn't measure — a green 3.5 s that isn't the claim it looks like. The
    STT engine beside it is pinned the same way and for the same reason.
    """
    if not Path(cfg.llm_model_path).exists():
        sys.exit(f"cleanup model not found: {cfg.llm_model_path}\n"
                 "download it first (app first-run, or scripts/bench_llm.py)")
    return Cleaner(cfg.llm_model_path, cfg.llm_max_tokens, runtime="cpu")


def make_pipeline(cfg: Config, engine, cleaner, db_path: Path) -> tuple[Pipeline, History]:
    history = History(db_path)
    pipeline = Pipeline(
        stt_supplier=lambda: engine,
        injector=m1.make_injector(cfg),
        history=history,
        history_enabled=True,
        cleaner=cleaner,
        cleanup_supplier=lambda: True,
    )
    return pipeline, history


def _last_row(history: History):
    rows = history.search(limit=1)
    return rows[0] if rows else None


# Win11 Notepad restores its previous session, so a fresh window can carry
# multiple tabs — each with its own RichEdit child. The paste lands in the
# active tab, which is not always the first child enumerated; track them all.

def _edit_children(top: int) -> list[int]:
    import ctypes
    from ctypes import wintypes

    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(64)
        m1.user32.GetClassNameW(hwnd, buf, 64)
        if "RichEdit" in buf.value or buf.value == "Edit":
            found.append(hwnd)
        return True

    m1.user32.EnumChildWindows(top, cb, 0)
    return found


def _find_edits(top: int, timeout_s: float = 6.0) -> list[int]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        edits = _edit_children(top)
        if edits:
            return edits
        time.sleep(0.3)
    return []


def _read_notepad(edits: list[int]) -> str:
    for e in edits:
        text = m1._read_window_text(e)
        if text:
            return text
    return ""


def _clear_notepad(edits: list[int]) -> None:
    for e in edits:
        m1._clear_window_text(e)


# Text that can only be ours: session-restore resurrects unsaved tabs from
# earlier (crashed/killed) harness runs, and those zombie windows steal the
# paste from the window we launched. Anything else is a user's document and
# must never be touched.
_RESIDUE_MARKERS = (
    "quick brown fox", "Cadent e2e check", "lf-diag", "lf-e2e",
    "schedule the quarterly review", "onboarding flow", "latest draft",
)


def _close_residue_notepads() -> list[int]:
    """Close Notepad windows that are empty or contain only our test strings.
    Returns hwnds of windows left alone (not provably ours)."""
    kept: list[int] = []
    for w in m1.windows_of({"notepad.exe"}):
        edits = _edit_children(w)
        texts = [m1._read_window_text(e) for e in edits]
        ours = all(not t or any(mark in t for mark in _RESIDUE_MARKERS)
                   for t in texts)
        if not ours:
            kept.append(w)
            continue
        for e in edits:
            m1._clear_window_text(e)  # nothing unsaved -> no save prompt
        m1.close_window(w, {"notepad.exe"})
    return kept


def _open_notepad(check: str) -> tuple[int | None, list[int], dict | None]:
    """Wait for user idle, close our zombie windows, launch a fresh Notepad and
    track ITS hwnd — a pre-existing user Notepad (personal unsaved notes) may
    stay open; we simply never inject unless our own window is foreground.
    Returns (hwnd, edits, error_result)."""
    if not m1.wait_user_idle():
        return None, [], {"check": check, "ok": False, "detail": "user never went idle"}
    _close_residue_notepads()
    before = set(m1.windows_of({"notepad.exe"}))
    subprocess.Popen(["notepad.exe"])
    hwnd = None
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        fresh = [w for w in m1.windows_of({"notepad.exe"}) if w not in before]
        if fresh and m1.focus_window(fresh[0]):
            hwnd = fresh[0]
            break
        time.sleep(0.5)
    if hwnd is None:
        return None, [], {"check": check, "ok": False,
                          "detail": "fresh notepad window never appeared/focused"}
    edits = _find_edits(hwnd)
    if not edits:
        return None, [], {"check": check, "ok": False, "detail": "no edit child found"}
    _clear_notepad(edits)
    return hwnd, edits, None


def _our_window_foreground(hwnd: int) -> bool:
    """Guard before every injection: the exact window we launched — process
    name alone is not enough when the user has their own Notepad open."""
    return m1.user32.GetForegroundWindow() == hwnd


# ---- fallback checks ---------------------------------------------------------

def _dictate_and_read(pipeline: Pipeline, history: History, edits: list[int],
                      audio) -> tuple[dict, str]:
    """Run one dictation through the real pipeline into Notepad; return the
    report facts and the WM_GETTEXT readback."""
    report = pipeline.process(audio)
    time.sleep(0.8)  # past the notepad override's 500 ms clipboard settle
    got = _read_notepad(edits)
    _clear_notepad(edits)
    row = _last_row(history)
    return {
        "outcome": report.outcome,
        "inserted_text": report.text,
        "read_back": got,
        "history_raw": row["raw_text"] if row else None,
        "history_cleaned": row["cleaned_text"] if row else None,
        "history_outcome": row["outcome"] if row else None,
    }, got


def test_fallbacks(cfg: Config) -> dict:
    """Charter safety contract, end-to-end: LLM crash, hard timeout, and
    diff-guard rejection each silently insert the raw transcript, with the
    raw row already in history. Plus a positive control (cleanup works)."""
    import numpy

    cleaner = make_cleaner(cfg)
    print("loading LLM (real llama.cpp)...", flush=True)
    cleaner.load()
    engine = _StubEngine(FALLBACK_RAW)
    audio = numpy.zeros(16000, dtype="float32")  # 1 s of silence

    hwnd, edits, err = _open_notepad("fallbacks")
    if err:
        return err

    with tempfile.TemporaryDirectory() as td:
        pipeline, history = make_pipeline(cfg, engine, cleaner, Path(td) / "h.db")
        scenarios = []
        # Fault injection leans on Cleaner privates (_llm, _gen_lock) — those
        # names are load-bearing here; update this harness if they move.
        real_llm = cleaner._llm

        def run(name: str, expect_raw: bool) -> dict:
            # The pipeline pastes into whatever is foreground (like the real
            # app) — never inject unless it is exactly the window we launched.
            if not _our_window_foreground(hwnd):
                r = {"scenario": name, "ok": False,
                     "detail": "our notepad window lost focus; aborted before injecting"}
                print(json.dumps(r, ensure_ascii=False), flush=True)
                return r
            facts, got = _dictate_and_read(pipeline, history, edits, audio)
            raw_ok = (facts["inserted_text"] == FALLBACK_RAW) == expect_raw
            ok = (facts["outcome"] == "inserted"
                  and got == facts["inserted_text"]
                  and facts["history_raw"] == FALLBACK_RAW
                  and facts["history_outcome"] == "inserted"
                  and raw_ok)
            r = {"scenario": name, "ok": ok, **facts}
            print(json.dumps(r, ensure_ascii=False), flush=True)
            return r

        # Positive control: real model, real cleanup -> cleaned text inserted,
        # raw and cleaned both in history.
        scenarios.append(run("control-cleanup-works", expect_raw=False))

        # 1. LLM dies mid-generation.
        cleaner._llm = _CrashingLLM()
        scenarios.append(run("llm-crash", expect_raw=True))
        cleaner._llm = real_llm

        # 2. Hard timeout: the real generation outlives a 50 ms deadline. The
        # worker thread is abandoned holding the single-flight lock, so an
        # immediately following dictation must also come out raw.
        real_timeout = cleanup_mod.cleanup_timeout
        cleanup_mod.cleanup_timeout = lambda words: 0.05
        try:
            scenarios.append(run("timeout", expect_raw=True))
        finally:
            cleanup_mod.cleanup_timeout = real_timeout
        # Let the abandoned generation drain so later checks start clean.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if cleaner._gen_lock.acquire(blocking=False):
                cleaner._gen_lock.release()
                break
            time.sleep(0.5)

        # 2b. Dictation while a generation is in flight: hold the single-flight
        # lock, exactly the state a timed-out worker leaves behind. (Timing the
        # real abandoned generation is racy — a warm model can finish before
        # the next dictation arrives.)
        cleaner._gen_lock.acquire()
        try:
            scenarios.append(run("dictation-during-in-flight-generation",
                                 expect_raw=True))
        finally:
            cleaner._gen_lock.release()

        # 3. Diff-guard rejection: model "answers" with a different utterance.
        cleaner._llm = _CannedLLM(
            "The weather in Paris is lovely this time of year, with mild "
            "evenings along the river and quiet cafes on every corner.")
        scenarios.append(run("diff-guard-rejection", expect_raw=True))
        cleaner._llm = real_llm
        history.conn.close()  # Windows: an open SQLite handle blocks temp cleanup

    m1.close_window(hwnd, {"notepad.exe"})
    cleaner.unload()
    return {"check": "fallbacks", "ok": all(s["ok"] for s in scenarios),
            "raw_transcript": FALLBACK_RAW, "scenarios": scenarios}


# ---- flow latency ------------------------------------------------------------

def test_flow_latency(cfg: Config) -> dict:
    """Release->inserted through the real flow pipeline (STT + cleanup +
    injection; excludes only the microphone) at ~3/10/30 s. Gate: the ~10 s
    dictation <= 3.5 s at the shipped default rung. STT and cleanup both run
    on CPU — the charter targets the CPU-only primary machine; see
    make_cleaner. The result records `llm_landed_on` so a reader a year from
    now doesn't have to take that on trust."""
    from cadent.stt import FasterWhisperEngine

    wavs = []
    for label, text in (("3s", UTTER_3S), ("10s", UTTER_10S), ("30s", UTTER_30S)):
        path = Path(tempfile.gettempdir()) / f"lf_m2_{label}.wav"
        if not path.exists():
            speak_to_wav(text, path)
        wavs.append((label, path))

    print(f"loading STT ({cfg.stt_model}, cpu int8)...", flush=True)
    engine = _TimingEngine(FasterWhisperEngine(cfg.stt_model, device="cpu"))
    cleaner_inner = make_cleaner(cfg)
    print("loading LLM (real llama.cpp)...", flush=True)
    t0 = time.perf_counter()
    cleaner_inner.load()
    llm_load_s = round(time.perf_counter() - t0, 2)
    cleaner = _TimingCleaner(cleaner_inner)

    # Warm both models: first calls pay one-time kernel/graph init.
    warm_audio, _ = load_wav(wavs[0][1])
    engine.transcribe(warm_audio, 16000)
    cleaner.clean("um so this is a, a warm up utterance for the model")
    flow_idle_rss = rss_mb()

    hwnd, edits, err = _open_notepad("flow-latency")
    if err:
        return err

    runs = []
    gate = None
    with tempfile.TemporaryDirectory() as td:
        pipeline, history = make_pipeline(cfg, engine, cleaner, Path(td) / "h.db")
        for label, path in wavs:
            audio, duration = load_wav(path)
            if not _our_window_foreground(hwnd):
                return {"check": "flow-latency", "ok": False, "runs": runs,
                        "detail": "our notepad window lost focus mid-run"}
            t0 = time.perf_counter()
            report = pipeline.process(audio)
            total = time.perf_counter() - t0
            time.sleep(0.8)
            got = _read_notepad(edits)
            _clear_notepad(edits)
            row = _last_row(history)
            run = {
                "utterance": label,
                "audio_s": round(duration, 2),
                "stt_s": round(engine.last_s, 2),
                "cleanup_s": round(cleaner.last_s, 2),
                "release_to_inserted_s": round(total, 2),
                "outcome": report.outcome,
                "landed": got == report.text,
                "raw": row["raw_text"] if row else None,
                "cleaned": row["cleaned_text"] if row else None,
            }
            if not run["landed"]:
                run["read_back"] = got
            if label == "10s":
                gate = total <= 3.5
                run["gate_3_5s"] = gate
            runs.append(run)
            print(json.dumps(run, ensure_ascii=False), flush=True)
        history.conn.close()  # Windows: an open SQLite handle blocks temp cleanup

    m1.close_window(hwnd, {"notepad.exe"})
    cleaner_inner.unload()
    ok = (gate is True) and all(r["landed"] and r["outcome"] == "inserted" for r in runs)
    return {"check": "flow-latency", "ok": ok, "model": cfg.stt_model,
            "llm": Path(cfg.llm_model_path).name, "llm_load_s": llm_load_s,
            # Where cleanup actually ran (#116) — "cpu" by construction, but
            # recorded rather than assumed: a 3.5 s gate met on a GPU is not
            # the gate the charter set, and the report is the only place a
            # later reader can check which one this was. Not `llm_runtime`:
            # that is the config field, whose values are `auto` and `cpu`,
            # and a rung of `gpu` in a key named for it would read as a
            # setting nobody can set (#155).
            "llm_landed_on": cleaner_inner.runtime,
            "flow_idle_rss_mb": flow_idle_rss, "runs": runs}


# ---- raw-mode regression -----------------------------------------------------

def test_raw_regression() -> dict:
    """M1 ticket-17 matrix + latency, unchanged harness: raw mode must still
    be byte-perfect with M1 latency."""
    import os

    out = Path(tempfile.gettempdir()) / "lf_m2_raw_regression.json"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "verify_e2e.py"),
         "--matrix", "--latency", "--out", str(out)],
        text=True, capture_output=True,
        env={**os.environ, "PYTHONUTF8": "1"})  # the ✓ in TEST_TEXT vs cp1252 pipes
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    # The ticket's criterion is "latency unchanged", not the M1 harness's own
    # <=2.0 s proxy gate (which the M1 evidence itself recorded as 2.46-2.94 s
    # under load on the primary machine). Regression = worse than that band.
    M1_RECORDED_MAX_S = 2.94
    real_failures = []
    for r in results:
        if r.get("ok"):
            continue
        if "not installed" in r.get("detail", ""):
            continue  # a missing app is a skip, not a regression (M1: "VS Code n/a")
        if r.get("app") == "discord":
            # M1 left Discord manual on purpose: the harness won't navigate a
            # real account to focus a composer. Owner check, not a regression.
            r["manual"] = "no composer focused; owner check (as in M1 ticket 17)"
            continue
        if (r.get("check") == "latency"
                and r.get("release_to_inserted_s", 99.0) <= M1_RECORDED_MAX_S):
            continue
        real_failures.append(r)
    return {"check": "raw-regression", "ok": bool(results) and not real_failures,
            "exit_code": proc.returncode, "results": results,
            "stderr_tail": proc.stderr[-500:] if proc.returncode else ""}


# ---- driver -------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--flow-latency", action="store_true")
    ap.add_argument("--fallbacks", action="store_true")
    ap.add_argument("--raw-regression", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", metavar="JSON_PATH")
    args = ap.parse_args()

    if sys.platform != "win32":
        sys.exit("this harness only runs on Windows")

    cfg = ConfigStore().config
    results: list[dict] = []

    if args.fallbacks or args.all:
        print("--- fallbacks", flush=True)
        results.append(test_fallbacks(cfg))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if args.flow_latency or args.all:
        print("--- flow-latency", flush=True)
        results.append(test_flow_latency(cfg))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if args.raw_regression or args.all:
        print("--- raw-regression", flush=True)
        results.append(test_raw_regression())
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)

    if not results:
        ap.error("pick --flow-latency/--fallbacks/--raw-regression/--all")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        print(f"report written: {args.out}")
    failed = [r for r in results if not r.get("ok")]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
