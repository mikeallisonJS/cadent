"""Throwaway denoiser bake-off for the noise-reduction effort (#43, #48).

Runs each shortlisted approach from docs/research/noise-suppression.md over the
noisy evaluation set built by scripts/noise_eval.py, writing a processed copy
of the set per candidate, then scores every copy through the same harness
(`noise_eval.py run --audio-dir`) on both STT engines and prints a WER-delta
table against the raw baseline. Also times each candidate on CPU.

Candidates (all whole-utterance, 16 kHz float32 in/out, CPU only):

    gtcrn-full GTCRN whole-buffer graph (same weights, exported from the author's
               checkpoint; one ORT call per utterance).
    gtcrn      GTCRN streaming graph (gtcrn_simple.onnx, 0.5 MB) on the ORT we ship,
               numpy STFT front end (512/256, sqrt-Hann), state threaded per frame.
    rnnoise    Xiph RNNoise via the rnnoise.dll/.dylib bundled in the pyrnnoise wheel,
               driven with ctypes directly (its Python shim breaks on av 18);
               16 k -> 48 k -> denoise -> 16 k with a numpy windowed-sinc resampler.
    specgate   stationary spectral gating in ~60 lines of numpy (the control arm).

Each candidate also gets a `<name>-mix50` variant: 50/50 wet/dry
("observation adding", Iwamoto et al. 2022) to see whether the artifact
penalty is real.

Usage (repo root):

    uv run python scripts/noise_bakeoff.py process   [--cand gtcrn rnnoise specgate]
    uv run python scripts/noise_bakeoff.py score     [--engine faster-whisper parakeet]
    uv run python scripts/noise_bakeoff.py report

Assets it expects in .cache/bakeoff/:
    gtcrn_simple.onnx  from https://github.com/k2-fsa/sherpa-onnx/releases/tag/speech-enhancement-models
    rnnoise.dll | librnnoise.dylib   copied out of the pyrnnoise wheel
"""

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from noise_eval import SR, read_wav, write_wav  # noqa: E402

CACHE = REPO / ".cache" / "bakeoff"
EVAL = REPO / "evalset"
OUT = EVAL / "bakeoff"

# ---- shared DSP ---------------------------------------------------------------


def stft(x: np.ndarray, n_fft: int, hop: int, window: np.ndarray) -> np.ndarray:
    """Centre-padded framed rFFT -> [frames, n_fft//2+1] complex64."""
    pad = n_fft // 2
    xp = np.pad(x, (pad, pad), mode="reflect" if len(x) > pad else "constant")
    n_frames = 1 + (len(xp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = xp[idx] * window[None, :]
    return np.fft.rfft(frames, axis=1).astype(np.complex64)


def istft(spec: np.ndarray, n_fft: int, hop: int, window: np.ndarray, length: int) -> np.ndarray:
    frames = np.fft.irfft(spec, n=n_fft, axis=1).astype(np.float32) * window[None, :]
    n_frames = spec.shape[0]
    total = n_fft + hop * (n_frames - 1)
    y = np.zeros(total, np.float32)
    wsum = np.zeros(total, np.float32)
    w2 = (window * window).astype(np.float32)
    for i in range(n_frames):
        s = i * hop
        y[s : s + n_fft] += frames[i]
        wsum[s : s + n_fft] += w2
    y = y / np.maximum(wsum, 1e-8)
    pad = n_fft // 2
    return y[pad : pad + length]


def resample_int(x: np.ndarray, up: int, down: int, taps: int = 96) -> np.ndarray:
    """Windowed-sinc polyphase-ish rational resampler (up/down small ints)."""
    if up == down:
        return x
    # upsample by zero-stuffing, lowpass at min(1/up, 1/down) * Nyquist, decimate
    y = np.zeros(len(x) * up, np.float32)
    y[::up] = x * up
    cutoff = 0.5 / max(up, down)  # cycles/sample at the upsampled rate
    n = np.arange(-taps, taps + 1)
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(len(n))
    h = (h / h.sum()).astype(np.float32)
    y = np.convolve(y, h, mode="same")
    return y[::down].astype(np.float32)


# ---- candidates ----------------------------------------------------------------


class GTCRN:
    name = "gtcrn"

    def __init__(self) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(CACHE / "gtcrn_simple.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        meta = self.sess.get_modelmeta().custom_metadata_map
        self.n_fft = int(meta["n_fft"])
        self.hop = int(meta["hop_length"])
        assert meta["window_type"] == "hann_sqrt"
        self.window = np.sqrt(np.hanning(self.n_fft + 1)[:-1]).astype(np.float32)
        self.shapes = {k: tuple(int(v) for v in meta[f"{k}_shape"].split(","))
                       for k in ("conv_cache", "tra_cache", "inter_cache")}

    def __call__(self, x: np.ndarray) -> np.ndarray:
        spec = stft(x, self.n_fft, self.hop, self.window)  # [T, 257]
        caches = {k: np.zeros(s, np.float32) for k, s in self.shapes.items()}
        out = np.empty_like(spec)
        for t in range(spec.shape[0]):
            mix = np.stack([spec[t].real, spec[t].imag], axis=-1)[None, :, None, :].astype(np.float32)
            enh, caches["conv_cache"], caches["tra_cache"], caches["inter_cache"] = self.sess.run(
                None, {"mix": mix, **caches})
            out[t] = enh[0, :, 0, 0] + 1j * enh[0, :, 0, 1]
        return istft(out, self.n_fft, self.hop, self.window, len(x))


class GTCRNFull(GTCRN):
    """Same weights, whole-buffer graph (`gtcrn_full.onnx`, exported once from the
    author's checkpoint with torch.onnx.export, dynamic T axis): one ORT call per
    utterance instead of one per 16 ms frame."""

    name = "gtcrn-full"

    def __init__(self) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(str(CACHE / "gtcrn_full.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        self.n_fft, self.hop = 512, 256
        self.window = np.sqrt(np.hanning(self.n_fft + 1)[:-1]).astype(np.float32)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        spec = stft(x, self.n_fft, self.hop, self.window)  # [T, 257]
        mix = np.ascontiguousarray(
            np.stack([spec.real, spec.imag], -1).transpose(1, 0, 2)[None]).astype(np.float32)
        enh = self.sess.run(None, {"mix": mix})[0]  # [1, 257, T, 2]
        out = (enh[0, :, :, 0] + 1j * enh[0, :, :, 1]).T
        return istft(out, self.n_fft, self.hop, self.window, len(x))


class RNNoise:
    name = "rnnoise"
    RATE = 48_000

    def __init__(self) -> None:
        lib_name = "librnnoise.dylib" if platform.system() == "Darwin" else "rnnoise.dll"
        self.lib = ctypes.CDLL(str(CACHE / lib_name))
        self.lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_create.restype = ctypes.c_void_p
        self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_process_frame.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
        self.lib.rnnoise_process_frame.restype = ctypes.c_float
        self.lib.rnnoise_get_frame_size.restype = ctypes.c_int
        self.frame = self.lib.rnnoise_get_frame_size()

    def __call__(self, x: np.ndarray) -> np.ndarray:
        up = resample_int(x, 3, 1)
        n = int(np.ceil(len(up) / self.frame)) * self.frame
        buf = np.zeros(n, np.float32)
        buf[: len(up)] = up * 32767.0
        st = self.lib.rnnoise_create(None)
        try:
            for s in range(0, n, self.frame):
                ptr = buf[s : s + self.frame].ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                self.lib.rnnoise_process_frame(st, ptr, ptr)
        finally:
            self.lib.rnnoise_destroy(st)
        y = resample_int(buf[: len(up)] / 32767.0, 1, 3)
        return y[: len(x)]


class SpecGate:
    """Stationary spectral gating: per-bin noise floor from the quietest frames,
    soft mask, temporal+frequency smoothing, iSTFT."""

    name = "specgate"
    n_fft, hop = 512, 128

    def __init__(self) -> None:
        self.window = np.hanning(self.n_fft + 1)[:-1].astype(np.float32)

    def __call__(self, x: np.ndarray, floor_db: float = -20.0, over: float = 1.5) -> np.ndarray:
        spec = stft(x, self.n_fft, self.hop, self.window)
        mag = np.abs(spec)
        energy = mag.mean(axis=1)
        quiet = mag[energy <= np.percentile(energy, 15)]  # noise-only frames
        if len(quiet) < 4:
            quiet = mag
        noise = quiet.mean(axis=0) + over * quiet.std(axis=0)  # per-bin threshold
        mask = (mag > noise[None, :]).astype(np.float32)
        # smooth: 3-bin frequency, 5-frame time box filters
        k_f = np.ones(3, np.float32) / 3
        k_t = np.ones(5, np.float32) / 5
        mask = np.apply_along_axis(lambda m: np.convolve(m, k_f, "same"), 1, mask)
        mask = np.apply_along_axis(lambda m: np.convolve(m, k_t, "same"), 0, mask)
        gain = 10 ** (floor_db / 20)
        mask = gain + (1 - gain) * mask
        return istft(spec * mask, self.n_fft, self.hop, self.window, len(x))


CANDS = {c.name: c for c in (GTCRN, GTCRNFull, RNNoise, SpecGate)}


# ---- commands ----------------------------------------------------------------


def cmd_process(args: argparse.Namespace) -> None:
    manifest = json.loads((EVAL / "mixed" / "manifest.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    timings = json.loads((OUT / "timings.json").read_text()) if (OUT / "timings.json").exists() else {}
    for name in args.cand:
        den = CANDS[name]()
        secs, audio_secs = 0.0, 0.0
        for m in manifest:
            x = read_wav(EVAL / "mixed" / m["file"])
            t0 = time.perf_counter()
            y = den(x)
            secs += time.perf_counter() - t0
            audio_secs += len(x) / SR
            y = np.clip(y, -1, 1).astype(np.float32)
            write_wav(EVAL / f"mixed-{name}" / m["file"], y)
            write_wav(EVAL / f"mixed-{name}-mix50" / m["file"], 0.5 * y + 0.5 * x)
        for d in (name, f"{name}-mix50"):
            (EVAL / f"mixed-{d}" / "manifest.json").write_text(json.dumps(manifest, indent=1))
        # a 10 s and 60 s timing on synthetic input, single run each
        t10 = _time_one(den, 10)
        t60 = _time_one(den, 60)
        timings[name] = {"rtf": secs / audio_secs, "secs_10s": t10, "secs_60s": t60,
                         "cpu": platform.processor(), "os": platform.system()}
        print(f"{name}: RTF {secs / audio_secs:.3f}  10 s clip {t10:.2f} s  60 s clip {t60:.2f} s")
    (OUT / "timings.json").write_text(json.dumps(timings, indent=1))


def _time_one(den, seconds: float) -> float:
    rng = np.random.default_rng(0)
    x = (0.1 * rng.standard_normal(int(seconds * SR))).astype(np.float32)
    t0 = time.perf_counter()
    den(x)
    return time.perf_counter() - t0


def cmd_score(args: argparse.Namespace) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dirs = ["mixed"] + [f"mixed-{c}{s}" for c in args.cand for s in ("", "-mix50")]
    for engine in args.engine:
        model = {"faster-whisper": "distil-small.en", "parakeet": "parakeet-tdt-0.6b-v2"}[engine]
        for d in dirs:
            out = OUT / f"{d}__{engine}.json"
            if out.exists() and not args.redo:
                print(f"skip {out.name}")
                continue
            cmd = [sys.executable, str(REPO / "scripts" / "noise_eval.py"), "run",
                   "--set", str(EVAL / "mixed"), "--audio-dir", str(EVAL / d),
                   "--engine", engine, "--model", model, "--device", args.device,
                   "--json", str(out)]
            print(">", " ".join(cmd[1:]))
            subprocess.run(cmd, check=True)


def cmd_snr(args: argparse.Namespace) -> None:
    """Objective check independent of the STT engines: SNR vs the clean reference,
    before and after each candidate, per (noise, snr) condition."""
    manifest = json.loads((EVAL / "mixed" / "manifest.json").read_text(encoding="utf-8"))
    dirs = ["mixed"] + [f"mixed-{c}" for c in args.cand]
    acc: dict[tuple, dict[str, list]] = {}
    for m in manifest:
        if m["noise"] == "clean":
            continue
        clean = read_wav(EVAL / "clean" / f"{m['clip']}.wav")
        for d in dirs:
            y = read_wav(EVAL / d / m["file"])
            if "rnnoise" in d:  # RNNoise's frame windowing delays its output by 320 samples (20 ms) at 16 kHz
                y = y[320:]
            n = min(len(clean), len(y))
            err = np.mean((y[:n] - clean[:n]) ** 2)
            snr = 10 * np.log10(np.mean(clean[:n] ** 2) / max(err, 1e-12))
            acc.setdefault((m["noise"], m["snr"]), {}).setdefault(d, []).append(snr)
    lines = ["| condition | raw SNR | " + " | ".join(d.removeprefix("mixed-") + " ΔSNR" for d in dirs[1:]) + " |",
             "|---|---|" + "---|" * (len(dirs) - 1)]
    for (noise, snr), row in sorted(acc.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        raw = np.mean(row["mixed"])
        lines.append(f"| {noise} {snr:g} dB | {raw:.1f} | " +
                     " | ".join(f"{np.mean(row[d]) - raw:+.1f}" for d in dirs[1:]) + " |")
    text = chr(10).join(lines)
    print(text)
    (OUT / "snr.md").write_text(text, encoding="utf-8")


def cmd_report(args: argparse.Namespace) -> None:
    files = sorted(OUT.glob("*__*.json"))
    by_engine: dict[str, dict[str, dict]] = {}
    for f in files:
        d, engine = f.stem.split("__")
        by_engine.setdefault(engine, {})[d] = json.loads(f.read_text())
    lines = []
    for engine, runs in by_engine.items():
        base = runs.get("mixed")
        if not base:
            continue
        conds = [(c["noise"], c["snr"]) for c in base["conditions"]]
        cands = [d for d in runs if d != "mixed"]
        lines.append(f"\n### {engine} ({base['model']}) - WER %, delta vs raw in parentheses\n")
        lines.append("| condition | raw | " + " | ".join(c.removeprefix("mixed-") for c in cands) + " |")
        lines.append("|---|---|" + "---|" * len(cands))
        tot = {d: [0, 0] for d in runs}
        for i, (noise, snr) in enumerate(conds):
            row = [f"{noise}" + (f" {snr:g} dB" if snr is not None else "")]
            b = base["conditions"][i]
            row.append(f"{100 * b['wer']:.1f}")
            tot["mixed"][0] += b["edits"]; tot["mixed"][1] += b["words"]
            for d in cands:
                c = runs[d]["conditions"][i]
                tot[d][0] += c["edits"]; tot[d][1] += c["words"]
                delta = 100 * (c["wer"] - b["wer"])
                row.append(f"{100 * c['wer']:.1f} ({delta:+.1f})")
            lines.append("| " + " | ".join(row) + " |")
        row = ["**all**", f"{100 * tot['mixed'][0] / tot['mixed'][1]:.1f}"]
        for d in cands:
            w = 100 * tot[d][0] / tot[d][1]
            row.append(f"{w:.1f} ({w - 100 * tot['mixed'][0] / tot['mixed'][1]:+.1f})")
        lines.append("| " + " | ".join(row) + " |")
    if (OUT / "timings.json").exists():
        t = json.loads((OUT / "timings.json").read_text())
        lines.append("\n### CPU cost (single thread where the runtime allows)\n")
        lines.append("| candidate | RTF on the set | 10 s clip | 60 s clip | machine |")
        lines.append("|---|---|---|---|---|")
        for n, v in t.items():
            lines.append(f"| {n} | {v['rtf']:.3f} | {v['secs_10s']:.2f} s | {v['secs_60s']:.2f} s | {v['os']} {v['cpu']} |")
    text = "\n".join(lines)
    print(text)
    (OUT / "report.md").write_text(text, encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process")
    p.add_argument("--cand", nargs="+", default=list(CANDS), choices=list(CANDS))
    p.set_defaults(fn=cmd_process)
    p = sub.add_parser("score")
    p.add_argument("--cand", nargs="+", default=list(CANDS), choices=list(CANDS))
    p.add_argument("--engine", nargs="+", default=["faster-whisper", "parakeet"])
    p.add_argument("--device", default="cpu")
    p.add_argument("--redo", action="store_true")
    p.set_defaults(fn=cmd_score)
    p = sub.add_parser("snr")
    p.add_argument("--cand", nargs="+", default=list(CANDS), choices=list(CANDS))
    p.set_defaults(fn=cmd_snr)
    p = sub.add_parser("report")
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
