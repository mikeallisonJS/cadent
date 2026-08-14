"""Benchmark faster-whisper CPU latency across candidate models (ticket 03).

Usage:
    python scripts/bench_stt.py --make-wav bench.wav   # synthesize ~10 s utterance via SAPI
    python scripts/bench_stt.py --wav bench.wav        # run all models, print table
    python scripts/bench_stt.py --wav bench.wav --model small.en --json  # one model (subprocess mode)

Each model is measured in its own subprocess so RAM numbers are not polluted
by previously loaded models. Settings mirror cadent/stt.py: CPU, int8,
beam_size=5, vad_filter=True, language="en".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import wave

MODELS = ["tiny.en", "base.en", "small.en", "distil-small.en"]

UTTERANCE = (
    "Please schedule the quarterly review for Thursday afternoon, and remind the "
    "design team that the updated mockups are due before the client presentation."
)


def make_wav(path: str) -> None:
    import win32com.client

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 18  # SAFT16kHz16BitMono
    stream.Open(path, 3)  # SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AllowAudioOutputFormatChangesOnNextSet = False  # keep 16 kHz, don't revert to voice default
    voice.AudioOutputStream = stream
    voice.Speak(UTTERANCE)
    stream.Close()
    with wave.open(path, "rb") as w:
        dur = w.getnframes() / w.getframerate()
        rate = w.getframerate()
    print(f"Wrote {path}: {dur:.1f} s at {rate} Hz (resampled to 16 kHz at load)")


def load_wav(path: str):
    from faster_whisper.audio import decode_audio  # resamples to 16 kHz mono

    audio = decode_audio(path, sampling_rate=16000)
    return audio, len(audio) / 16000.0


def bench_one(model_name: str, wav_path: str) -> dict:
    import psutil
    from faster_whisper import WhisperModel

    audio, duration = load_wav(wav_path)
    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    t0 = time.perf_counter()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    load_s = time.perf_counter() - t0

    def run() -> tuple[float, str]:
        t = time.perf_counter()
        segments, _ = model.transcribe(
            audio, language="en", vad_filter=True, beam_size=5
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return time.perf_counter() - t, text

    cold_s, text = run()
    warm = [run()[0] for _ in range(3)]
    rss_after = proc.memory_info().rss

    return {
        "model": model_name,
        "audio_s": round(duration, 2),
        "load_s": round(load_s, 2),
        "cold_s": round(cold_s, 2),
        "warm_min_s": round(min(warm), 2),
        "warm_max_s": round(max(warm), 2),
        "rss_mb": round((rss_after - rss_before) / 1e6),
        "rss_total_mb": round(rss_after / 1e6),
        "text": text,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-wav", metavar="PATH")
    ap.add_argument("--wav", metavar="PATH")
    ap.add_argument("--model", choices=MODELS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.make_wav:
        make_wav(args.make_wav)
        return
    if not args.wav:
        ap.error("--wav is required unless --make-wav is given")

    if args.model:
        result = bench_one(args.model, args.wav)
        print(json.dumps(result) if args.json else result)
        return

    results = []
    for m in MODELS:
        out = subprocess.run(
            [sys.executable, __file__, "--wav", args.wav, "--model", m, "--json"],
            capture_output=True, text=True, check=True,
        )
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
        print(f"done: {m}", file=sys.stderr)

    cols = ["audio_s", "load_s", "cold_s", "warm_min_s", "warm_max_s", "rss_mb", "rss_total_mb"]
    hdr = f"{'model':<16} " + " ".join(f"{c:>12}" for c in cols)
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['model']:<16} " + " ".join(f"{r[c]:>12}" for c in cols))
    for r in results:
        print(f"\n[{r['model']}] {r['text']}")


if __name__ == "__main__":
    main()
