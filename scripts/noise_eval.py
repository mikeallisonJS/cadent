"""Noisy evaluation set + WER harness for the noise-reduction effort (#43, #46).

Builds a small, reproducible set of speech clips mixed with representative
noise at fixed SNRs, runs a clip set through a configured STT engine exactly
as `cadent.stt` would, and reports word error rate per condition. This is the
evidence base the denoiser bake-off (#48) judges candidates on: WER on a
noisy set, not feel.

Usage (from the repo root, `uv run python scripts/noise_eval.py ...`):

    # 1. Clean clips. Either record your own voice (preferred) ...
    noise_eval.py record --out evalset/clean [--device N]
    #    ... or synthesize stand-ins with the Windows SAPI voice.
    noise_eval.py synth-speech --out evalset/clean

    # 2. Noise beds. Drop real recordings (fan.wav, keyboard.wav, chatter.wav,
    #    cafe.wav ...) into evalset/noise, or generate synthetic stand-ins.
    noise_eval.py synth-noise --out evalset/noise

    # 3. Mix at a few SNRs (default 20/10/5/0 dB) -> evalset/mixed/<clip>__<noise>__snr<dB>.wav + manifest.json
    noise_eval.py mix --clean evalset/clean --noise evalset/noise --out evalset/mixed --snr 20 10 5 0

    # 4. Score. Same engine construction as the app (cadent.stt.make_engine).
    noise_eval.py run --set evalset/mixed --engine faster-whisper --model distil-small.en
    noise_eval.py run --set evalset/mixed --engine parakeet --model parakeet-tdt-0.6b-v2 --json out.json

    # Score a *processed* set (a denoiser candidate writes its output next to
    # the manifest, same file names) with --audio-dir:
    noise_eval.py run --set evalset/mixed --audio-dir evalset/mixed-gtcrn --engine faster-whisper

Layout:
    clean/   NNN.wav + NNN.txt   16 kHz mono, one utterance + its reference transcript
    noise/   <name>.wav          any length; looped/trimmed to fit each clip
    mixed/   manifest.json       [{file, ref, clip, noise, snr}] - noise "clean" is the unmixed control

`evalset/` is git-ignored: audio is regenerable from this script and, for
own-voice clips, personal. Commit nothing under it.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SR = 16_000

# Dictation-shaped sentences: no numbers at all (Whisper writes "nine" as
# "9" and scoring does no number normalisation), a few proper nouns, some
# punctuation the engines will or won't emit. Fifteen keeps a full run of both engines under a few minutes on CPU.
SENTENCES = [
    "Please schedule the quarterly review for Thursday afternoon.",
    "Remind the design team that the updated mockups are due before the client presentation.",
    "I think we should move the deployment to next week and let the tests settle first.",
    "Can you send me the latest draft of the onboarding guide when you get a chance?",
    "The kitchen sink is leaking again, so I called the plumber for tomorrow morning.",
    "Let's grab lunch at the new Thai place near the office on Friday.",
    "Add milk, eggs, spinach, and a loaf of rye bread to the shopping list.",
    "The meeting ran long because nobody had read the agenda beforehand.",
    "I'll be working from home on Monday and in the office the rest of the week.",
    "Our flight lands in Denver a little after dinner, so we should be at the hotel before midnight.",
    "The new hire starts next Tuesday; make sure her laptop is imaged before then.",
    "Could you double check the invoice totals against the purchase order?",
    "Turn left at the second light, and the pharmacy is on the corner next to the bank.",
    "The dog needs his heartworm medication on the first of every month.",
    "Thanks for the feedback; I'll fold it into the next revision of the spec.",
]

# Unrelated text for the synthetic babble bed.
BABBLE = [
    "Did you see the game last night, it went into overtime again.",
    "I need to pick up the dry cleaning before they close at six.",
    "She said the train was delayed by almost forty minutes this morning.",
    "We're thinking of repainting the living room some kind of pale green.",
    "Honestly the coffee here has gone downhill since they changed suppliers.",
    "My brother is visiting next weekend so we might drive up to the lake.",
]


# ---- wav io -----------------------------------------------------------------

def read_wav(path: Path) -> np.ndarray:
    """16 kHz mono float32 in [-1, 1]. Anything not already 16-bit/32-bit PCM at
    16 kHz goes through faster-whisper's decoder (which the app itself relies on)."""
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        from faster_whisper.audio import decode_audio
        return decode_audio(str(path), sampling_rate=SR)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != SR:
        from faster_whisper.audio import decode_audio
        return decode_audio(str(path), sampling_rate=SR)
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---- clean speech -------------------------------------------------------------

def cmd_record(args: argparse.Namespace) -> None:
    """Own-voice clips: prints each sentence, Enter starts, Enter stops."""
    import sounddevice as sd

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Recording {len(SENTENCES)} sentences at {SR} Hz into {out}. "
          "Speak in a quiet room; noise gets added later.")
    for i, sentence in enumerate(SENTENCES, 1):
        stem = out / f"{i:03d}"
        if stem.with_suffix(".wav").exists() and not args.redo:
            print(f"[{i:02d}] exists, skipping (use --redo to re-record)")
            continue
        chunks: list[np.ndarray] = []

        def on_audio(indata, frames, time_info, status, _sink=chunks) -> None:
            _sink.append(indata.copy())

        input(f"\n[{i:02d}] {sentence}\n      Enter to start recording ...")
        stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                device=args.device, callback=on_audio)
        with stream:
            input("      recording - Enter to stop ...")
        audio = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, np.float32)
        write_wav(stem.with_suffix(".wav"), audio)
        stem.with_suffix(".txt").write_text(sentence + "\n", encoding="utf-8")
        print(f"      saved {len(audio) / SR:.1f} s")


def _sapi_say(text: str, path: Path) -> None:
    import win32com.client  # Windows only; see bench_stt.py

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 18  # SAFT16kHz16BitMono
    stream.Open(str(path), 3)
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AllowAudioOutputFormatChangesOnNextSet = False
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()


def cmd_synth_speech(args: argparse.Namespace) -> None:
    """Stand-in clean clips from the Windows SAPI voice - lets the harness run
    end to end before anyone has recorded a word. TTS is far cleaner and more
    regular than a real microphone, so treat its numbers as a floor."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, sentence in enumerate(SENTENCES, 1):
        stem = out / f"{i:03d}"
        _sapi_say(sentence, stem.with_suffix(".wav"))
        stem.with_suffix(".txt").write_text(sentence + "\n", encoding="utf-8")
    print(f"wrote {len(SENTENCES)} SAPI clips to {out}")


# ---- noise beds ---------------------------------------------------------------

def _lowpass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """One-pole IIR lowpass, vectorised via scipy-free recurrence."""
    a = math.exp(-2 * math.pi * cutoff_hz / SR)
    y = np.empty_like(x)
    acc = 0.0
    b = 1 - a
    for i in range(len(x)):
        acc = a * acc + b * x[i]
        y[i] = acc
    return y


def _fan(rng: np.random.Generator, seconds: float) -> np.ndarray:
    """HVAC/fan: brownish broadband rumble with a faint blade-rate hum."""
    n = int(seconds * SR)
    x = _lowpass(rng.standard_normal(n).astype(np.float32), 400.0)
    t = np.arange(n) / SR
    x += 0.15 * np.sin(2 * math.pi * 120 * t).astype(np.float32) * x.std()
    return x / (np.abs(x).max() + 1e-9)


def _keyboard(rng: np.random.Generator, seconds: float) -> np.ndarray:
    """Mechanical keyboard: sparse clicks (~9/s) - a bright transient plus a
    lower thock, each a few ms long."""
    n = int(seconds * SR)
    x = np.zeros(n, np.float32)
    t = 0.0
    while t < seconds:
        t += rng.exponential(1 / 9)
        i = int(t * SR)
        if i >= n:
            break
        length = int(rng.uniform(0.004, 0.012) * SR)
        env = np.exp(-np.arange(length) / (length / 4)).astype(np.float32)
        click = rng.standard_normal(length).astype(np.float32) * env
        thock = np.sin(2 * math.pi * rng.uniform(150, 300)
                       * np.arange(length) / SR).astype(np.float32) * env
        seg = (click + 0.6 * thock) * rng.uniform(0.4, 1.0)
        x[i:i + length] += seg[: n - i]
    return x / (np.abs(x).max() + 1e-9)


def _babble(rng: np.random.Generator, seconds: float, tmp: Path) -> np.ndarray | None:
    """Background chatter: several TTS voices talking over each other,
    lowpassed as if across the room. Windows only (SAPI)."""
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return None
    n = int(seconds * SR)
    x = np.zeros(n, np.float32)
    tmp.mkdir(parents=True, exist_ok=True)
    for k, text in enumerate(BABBLE):
        p = tmp / f"babble{k}.wav"
        _sapi_say(" ".join([text] * 3), p)
        v = read_wav(p)
        v = np.tile(v, int(math.ceil(n / len(v))) + 1)
        off = int(rng.uniform(0, len(v) - n))
        x += v[off:off + n] * rng.uniform(0.5, 1.0)
    x = _lowpass(x, 2500.0)
    return x / (np.abs(x).max() + 1e-9)


def cmd_synth_noise(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    secs = args.seconds
    beds = {"fan": _fan(rng, secs), "keyboard": _keyboard(rng, secs)}
    babble = _babble(rng, secs, out / "_tmp")
    if babble is not None:
        beds["chatter"] = babble
        beds["cafe"] = 0.7 * babble + 0.5 * beds["fan"] + 0.3 * beds["keyboard"]
    else:
        print("no SAPI on this platform: skipping chatter/cafe - supply real recordings")
    for name, x in beds.items():
        write_wav(out / f"{name}.wav", 0.9 * x / (np.abs(x).max() + 1e-9))
    for p in (out / "_tmp").glob("*"):
        p.unlink()
    if (out / "_tmp").exists():
        (out / "_tmp").rmdir()
    print(f"wrote {sorted(beds)} ({secs:.0f} s each) to {out}")


# ---- mixing -------------------------------------------------------------------

def _speech_power(x: np.ndarray) -> float:
    """Mean power over active frames (top 60% by energy), so leading and
    trailing silence doesn't deflate the reference level."""
    frame = int(0.02 * SR)
    m = len(x) // frame
    frames = x[: m * frame].reshape(m, frame)
    e = (frames ** 2).mean(axis=1)
    keep = e >= np.quantile(e, 0.4)
    return float(e[keep].mean()) if keep.any() else float((x ** 2).mean() + 1e-12)


def mix_at_snr(clean: np.ndarray, noise: np.ndarray, snr_db: float,
               rng: np.random.Generator) -> np.ndarray:
    n = len(clean)
    if len(noise) < n:
        noise = np.tile(noise, int(math.ceil(n / len(noise))) + 1)
    off = int(rng.integers(0, len(noise) - n + 1))
    seg = noise[off:off + n]
    p_s = _speech_power(clean)
    p_n = float((seg ** 2).mean()) + 1e-12
    gain = math.sqrt(p_s / (p_n * 10 ** (snr_db / 10)))
    y = clean + gain * seg
    peak = np.abs(y).max()
    return y / peak * 0.99 if peak > 0.99 else y


def cmd_mix(args: argparse.Namespace) -> None:
    clean_dir, noise_dir, out = Path(args.clean), Path(args.noise), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    clips = sorted(clean_dir.glob("*.wav"))
    noises = {p.stem: read_wav(p) for p in sorted(noise_dir.glob("*.wav"))}
    if not clips or not noises:
        sys.exit(f"need clips in {clean_dir} and noise beds in {noise_dir}")
    manifest = []
    for clip in clips:
        ref = clip.with_suffix(".txt").read_text(encoding="utf-8").strip()
        x = read_wav(clip)
        name = f"{clip.stem}__clean.wav"
        write_wav(out / name, x)
        manifest.append({"file": name, "ref": ref, "clip": clip.stem,
                         "noise": "clean", "snr": None})
        for nname, bed in noises.items():
            for snr in args.snr:
                name = f"{clip.stem}__{nname}__snr{int(snr)}.wav"
                write_wav(out / name, mix_at_snr(x, bed, snr, rng))
                manifest.append({"file": name, "ref": ref, "clip": clip.stem,
                                 "noise": nname, "snr": snr})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"wrote {len(manifest)} files + manifest.json to {out} "
          f"({len(clips)} clips x {len(noises)} noises x {len(args.snr)} SNRs + clean)")


# ---- scoring ------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s']")


def normalize(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    text = _PUNCT.sub("", text)
    return text.split()


def word_edits(ref: list[str], hyp: list[str]) -> int:
    """Levenshtein distance over word tokens."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def cmd_run(args: argparse.Namespace) -> None:
    import time

    from cadent.stt import make_engine

    set_dir = Path(args.set)
    audio_dir = Path(args.audio_dir) if args.audio_dir else set_dir
    manifest = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8"))
    if args.noise:
        manifest = [m for m in manifest if m["noise"] in args.noise or m["noise"] == "clean"]

    t0 = time.perf_counter()
    engine = make_engine(args.engine, args.model, args.device)
    print(f"# {args.engine} {args.model} on {engine.device} "
          f"(loaded in {time.perf_counter() - t0:.1f} s), {len(manifest)} clips, "
          f"audio from {audio_dir}")

    rows = []
    for m in manifest:
        audio = read_wav(audio_dir / m["file"])
        t = time.perf_counter()
        hyp = engine.transcribe(audio, SR)
        secs = time.perf_counter() - t
        ref_w, hyp_w = normalize(m["ref"]), normalize(hyp)
        rows.append({**m, "hyp": hyp, "edits": word_edits(ref_w, hyp_w),
                     "words": len(ref_w), "secs": round(secs, 3)})
        if args.verbose:
            print(f"{m['file']:40s} {rows[-1]['edits']:2d}/{rows[-1]['words']:2d}  {hyp}")

    # Aggregate per condition (noise, snr): corpus WER = sum edits / sum words.
    conds: dict[tuple, dict] = {}
    for r in rows:
        key = (r["noise"], r["snr"])
        c = conds.setdefault(key, {"edits": 0, "words": 0, "secs": 0.0, "n": 0})
        c["edits"] += r["edits"]
        c["words"] += r["words"]
        c["secs"] += r["secs"]
        c["n"] += 1

    def order(k):
        return (k[0] != "clean", k[0], -(k[1] if k[1] is not None else 999))

    print(f"\n{'condition':<20s} {'WER':>7s} {'edits/words':>12s} {'s/clip':>7s}")
    table = []
    for key in sorted(conds, key=order):
        c = conds[key]
        label = key[0] if key[1] is None else f"{key[0]} @ {key[1]:g} dB"
        wer = c["edits"] / max(c["words"], 1)
        table.append({"noise": key[0], "snr": key[1], "wer": round(wer, 4),
                      "edits": c["edits"], "words": c["words"],
                      "secs_per_clip": round(c["secs"] / c["n"], 3)})
        print(f"{label:<20s} {wer * 100:6.1f}% {c['edits']:5d}/{c['words']:<6d} "
              f"{c['secs'] / c['n']:7.2f}")
    total_e = sum(r["edits"] for r in rows)
    total_w = sum(r["words"] for r in rows)
    print(f"{'all':<20s} {total_e / max(total_w, 1) * 100:6.1f}% {total_e:5d}/{total_w:<6d}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "engine": args.engine, "model": args.model, "device": engine.device,
            "set": str(set_dir), "audio_dir": str(audio_dir),
            "conditions": table, "clips": rows}, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")


# ---- cli ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="record own-voice clean clips")
    p.add_argument("--out", default="evalset/clean")
    p.add_argument("--device", type=int)
    p.add_argument("--redo", action="store_true")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("synth-speech", help="SAPI stand-in clean clips (Windows)")
    p.add_argument("--out", default="evalset/clean")
    p.set_defaults(fn=cmd_synth_speech)

    p = sub.add_parser("synth-noise", help="synthetic fan/keyboard/chatter/cafe beds")
    p.add_argument("--out", default="evalset/noise")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_synth_noise)

    p = sub.add_parser("mix", help="mix clean x noise x SNR into a scored set")
    p.add_argument("--clean", default="evalset/clean")
    p.add_argument("--noise", default="evalset/noise")
    p.add_argument("--out", default="evalset/mixed")
    p.add_argument("--snr", type=float, nargs="+", default=[20, 10, 5, 0])
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_mix)

    p = sub.add_parser("run", help="transcribe a set and report WER per condition")
    p.add_argument("--set", default="evalset/mixed")
    p.add_argument("--audio-dir", help="score these files instead (same names as the manifest)")
    p.add_argument("--engine", default="faster-whisper", choices=["faster-whisper", "parakeet"])
    p.add_argument("--model", default="distil-small.en")
    p.add_argument("--device", default="auto")
    p.add_argument("--noise", nargs="*", help="only these noise beds (clean is always kept)")
    p.add_argument("--json")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(fn=cmd_run)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
