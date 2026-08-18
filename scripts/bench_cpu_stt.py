"""CPU-only speech bench on a varied-length utterance set (#28, Linux M6).

Sibling of bench_parakeet.py, with the axes the M1 Max bench (#137) added:
a *set* of distinct utterances from ~1.5 s to ~30 s, each timed once after a
warm-up clip, because ONNX Runtime re-plans per novel input length and a
repeated clip flatters it. Every row is forced to `cpu` — the question is
what Parakeet costs on an x86 CPU where no CUDA rung exists (AMD, Intel, old
NVIDIA drivers), against the Whisper CPU rows it would displace.

Usage:
    uv run python scripts/bench_cpu_stt.py --make-set bench_set     # synthesize
    uv run python scripts/bench_cpu_stt.py --set bench_set           # all rows
    # emulate a 4-core/8-thread laptop: pin to 8 logical CPUs, 4 ORT threads
    uv run python scripts/bench_cpu_stt.py --set bench_set --cores 8 --threads 4

`--cores N --threads T` pins the subprocess to logical CPUs 0..N-1 and sizes
the ORT pool to T, approximating a smaller machine. It is an approximation:
cache and memory bandwidth are still the big box's.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import wave
from pathlib import Path

CONFIGS = [
    ("parakeet", "parakeet-tdt-0.6b-v2", "cpu"),
    ("parakeet", "parakeet-tdt-0.6b-v3", "cpu"),
    ("faster-whisper", "tiny.en", "cpu"),
    ("faster-whisper", "distil-small.en", "cpu"),
    ("faster-whisper", "distil-medium.en", "cpu"),
]

# Ten utterances, short to long. Lengths are whatever the TTS makes of them;
# the point is that they are all *different* lengths.
UTTERANCES = [
    "Send it.",
    "Move the meeting to three.",
    "Can you pick up milk and eggs on the way home tonight?",
    "Please schedule the quarterly review for Thursday afternoon, and remind "
    "the design team that the updated mockups are due before the client "
    "presentation.",
    "I looked over the pull request this morning. The refactor is cleaner "
    "than the original, but the retry loop still swallows the timeout error, "
    "so the caller never learns the request failed.",
    "Hi Sam, thanks for the notes on the draft. I agree the introduction runs "
    "long, and I will cut the second paragraph. On the pricing section, I "
    "would rather keep the comparison table, since it is the one part "
    "reviewers keep quoting back to us. Let me know if that works.",
    "Here is what I want to do about the migration. First we snapshot the "
    "production database on Friday night. Then we run the schema change "
    "against the snapshot and time it. If it finishes under an hour we "
    "schedule the real run for the following weekend, otherwise we go back "
    "and split the alter table into smaller batches. Either way nobody "
    "touches the write path until the backfill has been verified.",
    "The main thing I took from the customer call is that they are not "
    "asking for more features. They are asking for the ones we have to stop "
    "surprising them. Three of the four issues they raised were the same "
    "shape: something changed under them without warning, a default moved, "
    "a shortcut got reassigned, an export format grew a column. So the "
    "proposal for next quarter is boring on purpose: a changelog people "
    "actually read, deprecation warnings before removals, and a settings "
    "page that shows the current values instead of hiding them.",
    "Okay, notes from the retro. What went well: the release went out on the "
    "day we said it would, the on call rotation held up, and the new "
    "dashboard caught the memory leak before any customer noticed. What did "
    "not go well: we spent most of the second week untangling a merge that "
    "should have been three small ones, the staging environment was broken "
    "for two days and nobody owned fixing it, and the design review happened "
    "after the code was written rather than before. Actions: smaller pull "
    "requests, one owner for staging, and design review moves to the start "
    "of the sprint. I will write these up properly and share them by "
    "tomorrow.",
    "This is the long one, so bear with me. I have been thinking about how "
    "we handle documentation, and I think we have the incentives backwards. "
    "Right now the person who knows a system best is the person least "
    "motivated to write it down, because they do not need it, and the person "
    "who needs it most is the person least able to write it, because they do "
    "not understand it yet. So the docs get written by neither. What I would "
    "like to try is pairing: when someone new joins a project, they write the "
    "guide as they learn, and the expert reviews it. The newcomer asks the "
    "questions a reader would ask, the expert supplies the answers, and the "
    "result is a document that actually starts where a reader starts. It "
    "costs the expert an hour of review instead of a day of writing, and it "
    "gives the newcomer a real first task with a real reader. We could try "
    "it on the next two onboardings and see whether anyone reads the result.",
]


def synth(text: str, path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        import win32com.client

        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 18  # SAFT16kHz16BitMono
        stream.Open(str(path), 3)
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.AllowAudioOutputFormatChangesOnNextSet = False
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
    elif system == "Darwin":
        subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text],
                       check=True)
    else:
        # espeak-ng ships on every target distro; robotic, but latency does
        # not care about the voice and this keeps the set reproducible.
        subprocess.run(["espeak-ng", "-w", str(path), "-s", "165", text], check=True)


def make_set(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i, text in enumerate(["Warm-up clip for the engine, thrown away."] + UTTERANCES):
        path = folder / f"{i:02d}.wav"
        synth(text, path)
        with wave.open(str(path), "rb") as w:
            print(f"{path.name}: {w.getnframes() / w.getframerate():.1f} s")


def load_set(folder: Path) -> list:
    from faster_whisper.audio import decode_audio

    return [decode_audio(str(p), sampling_rate=16000) for p in sorted(folder.glob("*.wav"))]


def pin(cores: int, threads: int | None) -> None:
    """Approximate a smaller machine: affinity to the first `cores` logical
    CPUs, and — because ORT sizes its default intra-op pool from the machine,
    not the affinity mask, so a pinned run oversubscribes and lies — an
    explicit ORT thread count, which is what ORT would default to on a real
    box with that many physical cores."""
    import psutil

    psutil.Process().cpu_affinity(list(range(cores)))
    os.environ["OMP_NUM_THREADS"] = str(threads or cores)
    if threads:
        import onnx_asr
        import onnxruntime as ort

        real = onnx_asr.load_model

        def patched(*a, **kw):
            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            return real(*a, sess_options=so, **kw)

        onnx_asr.load_model = patched


def bench(engine_name: str, model: str, device: str, folder: Path,
          cores: int | None, threads: int | None) -> dict:
    import psutil

    if cores:
        pin(cores, threads)
    from cadent.stt import make_engine

    clips = load_set(folder)
    warmup, clips = clips[0], clips[1:]
    proc = psutil.Process()
    rss_before = proc.memory_info().rss

    t0 = time.perf_counter()
    engine = make_engine(engine_name, model, device)
    load_s = time.perf_counter() - t0

    def run(clip) -> tuple[float, str]:
        t = time.perf_counter()
        text = engine.transcribe(clip, 16000)
        return time.perf_counter() - t, text

    run(warmup)
    first = [run(c) for c in clips]                       # varied, first pass
    times = sorted(t for t, _ in first)
    repeat = sorted(run(clips[3])[0] for _ in range(5))   # one clip, repeated
    rss_after = proc.memory_info().rss
    n = len(times)
    p95 = times[int(0.95 * (n - 1))]
    return {
        "engine": engine_name, "model": model, "device": device,
        "landed_on": getattr(engine, "device", "?"),
        "cores": cores or os.cpu_count(),
        "threads": threads or 0,
        "load_s": round(load_s, 2),
        "median_s": round(times[n // 2], 3),
        "p95_s": round(p95, 3),
        "max_s": round(times[-1], 3),
        "repeat_median_s": round(repeat[len(repeat) // 2], 3),
        "rss_mb": round((rss_after - rss_before) / 1e6),
        "per_clip": [(round(len(c) / 16000, 1), round(t, 3)) for c, (t, _) in zip(clips, first, strict=True)],
        "text": first[3][1],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-set", metavar="DIR")
    ap.add_argument("--set", metavar="DIR")
    ap.add_argument("--cores", type=int, help="pin to logical CPUs 0..N-1")
    ap.add_argument("--threads", type=int,
                    help="ORT intra-op threads for the pinned run (physical cores of the emulated box)")
    ap.add_argument("--engine")
    ap.add_argument("--model")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--only", help="substring filter on engine/model")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.make_set:
        make_set(Path(args.make_set))
        return
    if not args.set:
        ap.error("--make-set or --set is required")

    if args.engine:
        result = bench(args.engine, args.model, args.device, Path(args.set),
                       args.cores, args.threads)
        print(json.dumps(result) if args.json else result)
        return

    results = []
    failed: list[str] = []
    for engine_name, model, device in CONFIGS:
        if args.only and args.only not in f"{engine_name}/{model}":
            continue
        cmd = [sys.executable, __file__, "--engine", engine_name, "--model", model,
               "--device", device, "--set", args.set, "--json"]
        if args.cores:
            cmd += ["--cores", str(args.cores)]
        if args.threads:
            cmd += ["--threads", str(args.threads)]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            print(f"FAILED: {engine_name}/{model}/{device}\n{out.stderr[-2000:]}",
                  file=sys.stderr)
            failed.append(f"{engine_name}/{model}/{device}")
            continue
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
        print(f"done: {engine_name} {model} {device}", file=sys.stderr)

    cols = ["cores", "threads", "load_s", "median_s", "p95_s", "max_s", "repeat_median_s", "rss_mb"]
    header = f"{'engine/model':<40} {'on':<6} " + " ".join(f"{c:>15}" for c in cols)
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['engine'] + '/' + r['model']:<40} {r['landed_on']:<6} "
              + " ".join(f"{r[c]:>15}" for c in cols))
    for r in results:
        print(f"\n[{r['model']}] per clip (audio s, latency s): {r['per_clip']}")
        print(f"[{r['model']}] {r['text']}")
    if failed:
        # A partial table must not read as a finished benchmark.
        print(f"\n{len(failed)} row(s) FAILED: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
