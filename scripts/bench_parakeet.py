"""Benchmark the Parakeet engine against the faster-whisper default (#72).

Sibling of bench_stt.py rather than an extension of it: that one is a
ticket-03 artefact that measures faster-whisper on the CPU at int8, and its
answer should stay the answer it gave. This one measures *engines* — through
`cadent.stt.make_engine`, so what is benchmarked is what ships, GPU probe
and fallback included — and it measures accuracy as well as latency.

Usage:
    # latency, on a synthesized utterance
    python scripts/bench_parakeet.py --make-wav bench.wav
    python scripts/bench_parakeet.py --wav bench.wav

    # word error rate, on real recorded speech (needs pyarrow):
    uv run --with pyarrow python scripts/bench_parakeet.py --wer

Each configuration runs in its own subprocess so a model's resident memory is
its own and a wedged CUDA runtime can't take the whole run down with it.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import time
import wave

# (engine, model, device). `auto` is what the app actually starts on, so it is
# what gets measured; the CPU rows are there to price the fallback.
CONFIGS = [
    ("faster-whisper", "distil-small.en", "auto"),   # today's default
    ("faster-whisper", "distil-small.en", "cpu"),    # and its CPU-only floor
    ("faster-whisper", "distil-large-v3", "auto"),   # what a GPU box is told to pick
    ("parakeet", "parakeet-tdt-0.6b-v2", "auto"),    # English-only checkpoint
    ("parakeet", "parakeet-tdt-0.6b-v3", "auto"),
    ("parakeet", "parakeet-tdt-0.6b-v3", "cpu"),     # prices the failed-probe fallback
]

UTTERANCE = (
    "Please schedule the quarterly review for Thursday afternoon, and remind the "
    "design team that the updated mockups are due before the client presentation."
)

# 73 utterances of real read speech, LibriSpeech validation-clean. Small
# enough to fetch in seconds and honest in a way synthesized speech is not —
# a TTS voice is far easier than a person at a microphone, so a WER measured
# on `--wav` alone would flatter every engine equally and rank none of them.
WER_DATASET = "hf-internal-testing/librispeech_asr_dummy"
WER_FILE = "clean/validation-00000-of-00001.parquet"


# ---- scoring ---------------------------------------------------------------

def normalize(text: str) -> list[str]:
    """Case and punctuation off, so Parakeet's native punctuation is neither
    rewarded nor punished against LibriSpeech's bare uppercase references."""
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def edits(reference: list[str], hypothesis: list[str]) -> int:
    """Levenshtein distance over words — the S+D+I of the WER numerator."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        current = [i]
        for j, hyp in enumerate(hypothesis, 1):
            current.append(min(previous[j] + 1,          # deletion
                               current[j - 1] + 1,       # insertion
                               previous[j - 1] + (ref != hyp)))
        previous = current
    return previous[-1]


def enable_gpu_pack() -> None:
    """What __main__._enable_cuda_support_pack does at startup.

    Without it the faster-whisper `auto` row silently measures the CPU on a
    machine that has the pack installed — ctranslate2 finds cuBLAS through the
    process PATH, and a bench that skips this is comparing the wrong things.
    """
    import os

    from cadent.config import CUDA_DIR

    if CUDA_DIR.is_dir():
        os.environ["PATH"] = str(CUDA_DIR) + os.pathsep + os.environ.get("PATH", "")


# ---- audio -----------------------------------------------------------------

def make_wav(path: str) -> None:
    import win32com.client

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 18  # SAFT16kHz16BitMono
    stream.Open(path, 3)     # SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AllowAudioOutputFormatChangesOnNextSet = False
    voice.AudioOutputStream = stream
    voice.Speak(UTTERANCE)
    stream.Close()
    with wave.open(path, "rb") as w:
        print(f"Wrote {path}: {w.getnframes() / w.getframerate():.1f} s")


def load_wav(path: str):
    from faster_whisper.audio import decode_audio

    audio = decode_audio(path, sampling_rate=16000)
    return audio, len(audio) / 16000.0


def load_wer_samples() -> list[tuple]:
    """(audio, reference) pairs, decoded to 16 kHz mono float32."""
    import pyarrow.parquet as pq
    from faster_whisper.audio import decode_audio
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(WER_DATASET, WER_FILE, repo_type="dataset")
    table = pq.read_table(path).to_pylist()
    return [(decode_audio(io.BytesIO(r["audio"]["bytes"]), sampling_rate=16000),
             r["text"]) for r in table]


# ---- the runs --------------------------------------------------------------

def bench_latency(engine_name: str, model: str, device: str, wav_path: str) -> dict:
    import psutil

    from cadent.stt import make_engine

    enable_gpu_pack()
    audio, duration = load_wav(wav_path)
    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    t0 = time.perf_counter()
    engine = make_engine(engine_name, model, device)
    load_s = time.perf_counter() - t0

    def run(clip) -> tuple[float, str]:
        t = time.perf_counter()
        text = engine.transcribe(clip, 16000)
        return time.perf_counter() - t, text

    cold_s, text = run(audio)
    warm = sorted(run(audio)[0] for _ in range(5))

    # The number that actually predicts dictation. Repeating one clip lets
    # ONNX Runtime reuse a plan it will never get to reuse in real use, where
    # every utterance is a different length — on DirectML that is the
    # difference between 0.16 s and 0.49 s (#72). Whichever engine is under
    # test, this is the honest column.
    varied = sorted(run(audio[:int(16000 * (3 + 0.41 * i))])[0] for i in range(8))
    rss_after = proc.memory_info().rss

    return {
        "engine": engine_name, "model": model, "device": device,
        # Where it *landed*, which is the number that means anything: `auto`
        # reports cpu when the GPU probe failed.
        "landed_on": getattr(engine, "device", "?"),
        "audio_s": round(duration, 2),
        "load_s": round(load_s, 2),
        "cold_s": round(cold_s, 3),
        "warm_median_s": round(warm[len(warm) // 2], 3),
        "varied_median_s": round(varied[len(varied) // 2], 3),
        "varied_max_s": round(varied[-1], 3),
        "rss_mb": round((rss_after - rss_before) / 1e6),
        "text": text,
    }


def bench_wer(engine_name: str, model: str, device: str, limit: int) -> dict:
    from cadent.stt import make_engine

    enable_gpu_pack()
    samples = load_wer_samples()[:limit]
    engine = make_engine(engine_name, model, device)
    total_edits = total_words = 0
    total_audio = elapsed = 0.0
    for audio, reference in samples:
        t = time.perf_counter()
        hypothesis = engine.transcribe(audio, 16000)
        elapsed += time.perf_counter() - t
        total_audio += len(audio) / 16000.0
        ref = normalize(reference)
        total_edits += edits(ref, normalize(hypothesis))
        total_words += len(ref)
    return {
        "engine": engine_name, "model": model, "device": device,
        "landed_on": getattr(engine, "device", "?"),
        "utterances": len(samples),
        "wer_pct": round(100 * total_edits / total_words, 2),
        "audio_s": round(total_audio, 1),
        "transcribe_s": round(elapsed, 1),
        "realtime_factor": round(total_audio / elapsed, 1) if elapsed else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-wav", metavar="PATH")
    ap.add_argument("--wav", metavar="PATH")
    ap.add_argument("--wer", action="store_true",
                    help=f"score against {WER_DATASET} instead of timing a wav")
    ap.add_argument("--limit", type=int, default=73, help="utterances to score")
    ap.add_argument("--engine")
    ap.add_argument("--model")
    ap.add_argument("--device")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.make_wav:
        make_wav(args.make_wav)
        return
    if not args.wav and not args.wer:
        ap.error("one of --make-wav, --wav or --wer is required")

    if args.engine:                      # one config, in this process
        result = (bench_wer(args.engine, args.model, args.device, args.limit)
                  if args.wer else
                  bench_latency(args.engine, args.model, args.device, args.wav))
        print(json.dumps(result) if args.json else result)
        return

    results = []
    for engine_name, model, device in CONFIGS:
        cmd = [sys.executable, __file__, "--engine", engine_name,
               "--model", model, "--device", device, "--json"]
        cmd += ["--wer", "--limit", str(args.limit)] if args.wer else ["--wav", args.wav]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            print(f"FAILED: {engine_name}/{model}/{device}\n{out.stderr[-2000:]}",
                  file=sys.stderr)
            continue
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
        print(f"done: {engine_name} {model} {device}", file=sys.stderr)

    cols = (["utterances", "wer_pct", "audio_s", "transcribe_s", "realtime_factor"]
            if args.wer else
            ["audio_s", "load_s", "cold_s", "warm_median_s", "varied_median_s",
             "varied_max_s", "rss_mb"])
    header = f"{'engine/model/device':<46} {'on':<9} " + " ".join(f"{c:>14}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in results:
        name = f"{r['engine']}/{r['model']}/{r['device']}"
        print(f"{name:<46} {r['landed_on']:<9} "
              + " ".join(f"{r[c]:>14}" for c in cols))
    if not args.wer:
        for r in results:
            print(f"\n[{r['engine']} {r['device']}] {r['text']}")


if __name__ == "__main__":
    main()
