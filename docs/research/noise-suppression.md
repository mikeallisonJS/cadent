# Noise-suppression candidates for the dictation path (research, 2026-08-18)

Ticket: #44, part of the #43 "Wayfinder: noise reduction" map. Survey, not measurement: which
noise-suppression approaches could sit between `Recorder.stop()` (`cadent/audio.py`, a whole
16 kHz mono float32 utterance) and the STT engine, on **both** Windows and macOS arm64, and what
each would cost to ship. Same lens as `parakeet-runtime.md`: **packaging weight first, latency
second**, and read the wheel lists rather than the README.

Everything below was checked against PyPI's JSON API (wheel filenames and sizes), the upstream
repos and their licence files, on 2026-08-18. Latency figures are the authors' own published
numbers scaled to a 10 s clip — none were measured here; that is what the prototype ticket is
for.

## Verdict

**Shortlist to prototype, in order: GTCRN (pure ONNX Runtime, in-house STFT front end),
RNNoise via `pyrnnoise`, and spectral gating written in numpy (not the `noisereduce` package).**
Everything else is either not on both platforms as a wheel, drags PyTorch, or is
non-commercial-licensed.

Two things the survey turned up that shape the prototype more than any table cell:

1. **The cheapest good model is already ONNX and 0.5 MB.** GTCRN (MIT, 48 K parameters,
   33 MMAC/s, 16 kHz native) ships as `gtcrn_simple.onnx` from the sherpa-onnx release page and
   runs on the `onnxruntime` we already bundle. It needs no new wheel at all — only a
   256-hop STFT/iSTFT in numpy, which is thirty lines. sherpa-onnx wraps the same file if we
   would rather not write those lines, at the price of a second copy of ONNX Runtime.
2. **Denoising can make transcription worse.** Whisper and Parakeet were trained on noisy
   audio; a speech-enhancement front end that introduces artifacts (musical noise, over-
   suppression of quiet consonants) is a known way to *raise* WER on modern ASR
   ([Iwamoto et al. 2022](https://arxiv.org/abs/2201.06685) identify the artifact component as
   the main cause and show that mixing a little of the raw signal back in — "observation
   adding" — recovers it). Whatever we prototype must be gated on measured WER, not on how the
   waveform looks, and should ship with a wet/dry mix rather than a hard replace.

## 1. What we already ship, and what the input looks like

From `pyproject.toml` / `uv.lock`: `numpy` 2.4.x, `onnxruntime` 1.28 (macOS/Linux) or
`onnxruntime-directml` 1.24.4 (Windows), `sounddevice`, `av` (via faster-whisper — useful: it
carries FFmpeg's `swresample`, so we have a resampler without adding scipy), `huggingface-hub`.
**Not** shipped: scipy, torch, librosa, soundfile, matplotlib. Python `>=3.11`; the lock is
resolved for 3.12+.

`Recorder.stop()` returns the whole utterance as one float32 array in [-1, 1] at 16 kHz. That
means the denoiser runs **offline on a buffer, once per dictation** — the streaming/latency
properties these libraries advertise (10 ms look-ahead, per-frame state) are irrelevant to us;
what matters is total wall-clock on a ≤ 120 s clip and whether the model wants 16 kHz or 48 kHz.
Anything native to 48 kHz (RNNoise, DeepFilterNet) costs an up/down resample around it.

Silero VAD is already in the pipeline for the Whisper engine (`vad_filter=True` in
`cadent/stt.py`); Parakeet needs no gate (see `parakeet-runtime.md` §6). VAD trims silence; it
does not clean speech, so it is listed below for completeness but is not a noise-suppression
candidate on its own.

## 2. Comparison table

"Wheel" = binary wheel on PyPI for **both** `win_amd64` and `macosx_*_arm64` at a Python we
support. "Δ dependencies" = what a plain `pip install` would add beyond §1. Latency is the
upstream figure scaled to 10 s of audio on one CPU core, plus resampling where needed.

| Candidate | Wheels win32 + darwin-arm64 | Δ dependencies vs shipped | Model / payload | Licence | ~10 s clip, CPU | Offline | Verdict |
|---|---|---|---|---|---|---|---|
| **GTCRN, pure ORT** — `gtcrn_simple.onnx` from the sherpa-onnx `speech-enhancement-models` release, run on the ORT we ship | n/a — no new wheel; ORT already present on both | **none** (numpy STFT in-house) | 0.54 MB ONNX (48 K params, 33 MMAC/s), 16 kHz native | MIT (model + code) | ≈ 0.7 s streaming per-frame in Python (author RTF 0.07 on i5-12400 for the streaming graph; a whole-buffer graph, `gtcrn.onnx` 0.35 MB, should be well under that) | yes | **prototype #1** |
| GTCRN via **sherpa-onnx** (`OfflineSpeechDenoiser`) | yes: cp311–cp314 win_amd64 + macosx_11_0_arm64 | `sherpa-onnx` 2.3 MB + `sherpa-onnx-core` 16.5 MB (win) / 9.3 MB (mac arm64) — vendors its **own** ONNX Runtime beside ours | same 0.54 MB model | Apache-2.0 | same model; C++ frame loop, so faster than the Python one | yes | fallback if the in-house STFT front end is a pain |
| **RNNoise** via `pyrnnoise` (ctypes over a bundled `rnnoise.dll`/`.dylib`) | yes: `py3-none` wheels for win_amd64, macosx_15_0_universal2, manylinux x86_64/aarch64 | `audiolab` → `av` (have), `click`, `humanize`, `jinja2`, `requests`, `smart_open`, `soundfile`; plus `matplotlib`, `tqdm` — ~13 MB wheel of which 14.8 MB uncompressed is the DLL | 85 kB of weights inside the DLL; **48 kHz only**, 480-sample frames | Apache-2.0 (binding), BSD-3 (RNNoise) | ≈ 0.2 s (Xiph: "about 60x faster than real-time on an x86 CPU") + 16k→48k→16k resample | yes | **prototype #2**; the binding's dependency list is silly but every item is pure Python or already shipped except `matplotlib` |
| **Spectral gating** — `noisereduce` | pure Python `py3-none-any` | `scipy` (36.6 MB win / 20.4 MB mac arm64), `matplotlib` (9.3 MB), `joblib`, `tqdm` | none | MIT | tens of ms (numpy STFT) | yes | algorithm yes, package no — **prototype #3 as ~100 lines of numpy**, no scipy |
| **DeepFilterNet** (2/3) via the `deepfilternet` PyPI package | `deepfilterlib` wheels stop at **cp311** (win_amd64 + macosx_11_0_arm64); `numpy<2.0` pin; imports **torch** at module load | torch (hundreds of MB), loguru, sympy, requests, appdirs, packaging; and a numpy downgrade | DFN3 checkpoint 8 MB; **48 kHz** native | MIT / Apache-2.0 dual | RTF 0.04 (DFN2 paper, notebook Core-i5) → ≈ 0.4 s + resample | yes | rejected as packaged: torch, numpy<2, no 3.12 wheels |
| DeepFilterNet3 as **ONNX** — official `DeepFilterNet3_onnx.tar.gz` (three sub-graphs, ~8 MB) or the `deep-filter` CLI binaries (27–30 MB, tract-based) | ONNX: runs on our ORT; CLI: unsigned 2023 binaries for win x86_64 / darwin arm64 | ONNX: none, **but** the ERB feature front end, normalisation and deep-filter synthesis (`libDF`, Rust) must be re-implemented in numpy — community ports exist on Hugging Face but each has its own I/O contract; CLI: subprocess + Gatekeeper/notarisation on macOS | 8 MB | MIT / Apache-2.0 | as above | yes | parked — best quality of the set, highest integration cost; revisit if GTCRN under-delivers |
| **DPDFNet** (CEVA) — `dpdfnet` PyPI or raw ONNX from the sherpa release | `dpdfnet` is `py3-none-any` but requires `librosa` (→ scipy, numba, llvmlite) | librosa stack ≈ 70+ MB; or via sherpa-onnx as above | 8.3–13.9 MB ONNX, 16 kHz variants, 2.3–3.5 M params, 0.36–4.4 GMAC/s | Apache-2.0 | baseline 0.36 GMAC/s: likely ≈ 0.5–1 s; larger variants slower | yes | second-tier: 20× GTCRN's payload for a model we cannot compare without a WER bench |
| **DTLN** — `model_1.onnx` + `model_2.onnx` from the repo | n/a — raw ONNX on our ORT | none (numpy framing) | 1.5 + 2.5 MB, 16 kHz native, < 1 M params | MIT | per-frame Python loop, ~1250 frames × 2 sessions → ≈ 0.5 s | yes | viable but 2020-vintage and beaten by GTCRN on DNS3 in GTCRN's own tables; keep as a spare |
| **WebRTC noise suppression** (`webrtc-audio-processing`) | **no**: `webrtc-noise-gain` ships manylinux only; `webrtc-audio-processing` 0.1.3 ships linux-armv7 only; `webrtc-apm` 0.1.6 has one cp311 win wheel and a placeholder homepage | would mean building the C++ APM in our CI for two OSes | none (DSP) | BSD-3 (WebRTC) | RTF ≈ 0.01 | yes | rejected on packaging: no credible cross-platform wheel |
| **Silero denoise** (`silero-models` `denoise_models`: `sns`, `snf`, `lnf`) | TorchScript `.jit` only, downloaded at runtime | torch | ~n/a | **CC BY-NC-SA 4.0** (repo licence) | n/a | needs a download | rejected: non-commercial + torch |
| **Silero VAD** (`silero_vad.onnx`) | already bundled by faster-whisper; ORT everywhere | none | 2.3 MB (16 k op15 variant 1.3 MB) | MIT | < 1 ms per 30 ms chunk → ≈ 0.3 s | yes | not a denoiser; already used for Whisper; useful as the *gate* in front of any denoiser (only run it when speech probability says the clip is noisy/quiet) |
| `speexdsp-ns` (Speex preprocessor) | manylinux only | build for win/mac ourselves | none | BSD | trivial | yes | rejected on packaging |

Sources for the table: PyPI JSON for
[`pyrnnoise`](https://pypi.org/project/pyrnnoise/), [`audiolab`](https://pypi.org/project/audiolab/),
[`noisereduce`](https://pypi.org/project/noisereduce/), [`deepfilternet`](https://pypi.org/project/deepfilternet/),
[`deepfilterlib`](https://pypi.org/project/deepfilterlib/), [`sherpa-onnx`](https://pypi.org/project/sherpa-onnx/),
[`sherpa-onnx-core`](https://pypi.org/project/sherpa-onnx-core/), [`dpdfnet`](https://pypi.org/project/dpdfnet/),
[`webrtc-noise-gain`](https://pypi.org/project/webrtc-noise-gain/), [`webrtc-audio-processing`](https://pypi.org/project/webrtc-audio-processing/),
[`webrtc-apm`](https://pypi.org/project/webrtc-apm/), [`speexdsp-ns`](https://pypi.org/project/speexdsp-ns/),
[`silero-vad`](https://pypi.org/project/silero-vad/), [`onnxruntime`](https://pypi.org/project/onnxruntime/),
[`onnxruntime-directml`](https://pypi.org/project/onnxruntime-directml/), [`scipy`](https://pypi.org/project/scipy/),
[`matplotlib`](https://pypi.org/project/matplotlib/); repos and licences:
[xiph/rnnoise](https://github.com/xiph/rnnoise) (BSD-3; [demo page](https://jmvalin.ca/demo/rnnoise/) for the 85 kB / 60× figures),
[pengzhendong/pyrnnoise](https://github.com/pengzhendong/pyrnnoise) (Apache-2.0; `rnnoise.py` hard-codes `SAMPLE_RATE = 48000`),
[Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) (dual MIT/Apache-2.0; `models/` for the ONNX tarballs, releases for the binaries, `DeepFilterNet/pyproject.toml` for `numpy<2.0`, `df/enhance.py` for `import torch`; [DFN2 paper](https://arxiv.org/abs/2205.05474) for RTF 0.04),
[Xiaobin-Rong/gtcrn](https://github.com/Xiaobin-Rong/gtcrn) (MIT; README for 48.2 K / 33 MMAC/s / RTF 0.07),
[sherpa-onnx speech-enhancement-models release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speech-enhancement-models) (`gtcrn_simple.onnx` 0.54 MB, DPDFNet ONNX 8.8–14.6 MB) and its [`offline-speech-enhancement-gtcrn.py`](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-speech-enhancement-gtcrn.py) / [`offline-speech-denoiser-gtcrn-impl.h`](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/csrc/offline-speech-denoiser-gtcrn-impl.h) (STFT parameters come from the model's metadata; `hann_sqrt` window),
[ceva-ip/DPDFNet](https://github.com/ceva-ip/DPDFNet) (Apache-2.0; model-profile table),
[breizhn/DTLN](https://github.com/breizhn/DTLN) (MIT; `pretrained_model/` ONNX sizes),
[rhasspy/webrtc-noise-gain](https://github.com/rhasspy/webrtc-noise-gain),
[snakers4/silero-models](https://github.com/snakers4/silero-models) (`models.yml` `denoise_models`, `.jit` only; repo `LICENSE` is CC BY-NC-SA 4.0),
[snakers4/silero-vad](https://github.com/snakers4/silero-vad) (MIT; `src/silero_vad/data/` sizes; README "< 1 ms per 30+ ms chunk"),
[timsainb/noisereduce](https://github.com/timsainb/noisereduce).

## 3. Notes per candidate

### GTCRN

The find of the survey. 48.2 K parameters, 33 MMAC/s, trained on DNS3 / VCTK, MIT, 16 kHz
native — so no resample — and already exported to ONNX by the author and re-hosted by
sherpa-onnx, whose C++ shows exactly what the graph wants: an STFT of the mix (`n_fft`, `hop`,
window type read from the ONNX metadata; the window is `hann_sqrt`), fed as `[1, n_fft/2+1, T, 2]`
real/imag, with recurrent state tensors threaded between frames for the streaming
`gtcrn_simple.onnx`. That is a numpy STFT and a `for` loop over ~625 frames for a 10 s clip on
the ORT session we already have — the CPU EP; a graph this small gains nothing from DirectML and
should not be routed there.

Cost: zero new distributions, 0.5 MB of model (bundle it as a data file next to Silero's, or
fetch through `huggingface-hub` like the STT models). Risk: quality on a real dictation
microphone is unproven for us, and 48 K parameters is not going to remove a hairdryer. That is
precisely what the prototype should measure.

If the numpy front end fights back, `sherpa-onnx` runs the same file behind
`OfflineSpeechDenoiser`, at +19 MB on Windows / +11.6 MB on macOS and a second ONNX Runtime
living beside ours (the `parakeet-runtime.md` §1 objection — no DirectML — does not matter here,
because we would run this on CPU anyway).

### RNNoise / `pyrnnoise`

The classic. BSD-3 C library, 85 kB of weights, GRU-based, "about 60x faster than real-time on
an x86 CPU" per its author, and it returns a per-frame speech probability we could use as a
free VAD. `pyrnnoise` is the only binding with wheels for both our platforms; it is
`py3-none-<plat>` (a ctypes shim over a bundled `rnnoise.dll` / `librnnoise.dylib`), so it is
Python-version-agnostic and needs no compiler. Two costs:

- **48 kHz only.** The library operates on 480-sample frames at 48 kHz; the binding does not
  resample. We would go 16 k → 48 k → RNNoise → 16 k. `av`'s `AudioResampler` (already shipped)
  or a polyphase in numpy covers that; scipy is not needed.
- **The dependency list is careless**: `audiolab` (which wants `av`, `click`, `humanize`,
  `jinja2`, `requests`, `smart_open`, `soundfile`), `matplotlib`, `tqdm`. All pure Python or
  already ours except `matplotlib` (9 MB) and `soundfile` (1 MB). Options: install with
  `--no-deps` and vendor the ~120-line `rnnoise.py`, or just carry the weight; PyInstaller
  would need a hook to pick up the DLL either way.

Note also the wheel is 13 MB compressed for an 85 kB model — the bundled DLL is 14.8 MB
uncompressed, which looks like an unstripped build; worth a look during the prototype but not a
blocker.

### Spectral gating (`noisereduce`)

The algorithm — estimate a per-band noise floor from the STFT (stationary: from the whole clip;
non-stationary: a running estimate), gate bins below threshold, smooth the mask, iSTFT — is
exactly what a whole-utterance buffer makes easy, costs milliseconds, and has no model to
license or download. The **package** is the problem: hard dependencies on `scipy` (36 MB on
Windows) and, inexplicably, `matplotlib`. Since Cadent's audio is already a numpy array, the
right prototype is ~100 lines of numpy in `cadent/` (STFT via `np.fft.rfft` over a strided
frame view, mask, overlap-add), not a new dependency. It is also the candidate most likely to
produce the artifacts §Verdict warns about, so it doubles as the control arm of the WER bench.

### DeepFilterNet

Best perceptual quality of the set and the one the ticket named first, but every route into
Python is expensive: the PyPI package imports torch and pins `numpy<2.0` with `deepfilterlib`
wheels stopping at cp311 (our lock is on 3.12 / numpy 2.4); the ONNX export is three sub-graphs
that need `libDF`'s ERB feature extraction, normalisation and deep-filter synthesis re-done in
numpy (community ports on Hugging Face exist but disagree on the I/O contract); the `deep-filter`
CLI binaries are 27–30 MB, dated 2023, and unsigned on macOS. And it too is 48 kHz. Parked: if
GTCRN's quality is not enough, this is the next rung, and the numpy front-end port is the way
to take it (still zero new distributions).

### DPDFNet, DTLN

Both are ONNX, 16 kHz, permissive, runnable on our ORT. DPDFNet is 16–26× GTCRN's payload with
a `librosa`-shaped PyPI package we would not use; DTLN is older and, per GTCRN's own tables,
weaker. Neither is worth prototyping before GTCRN has been measured; both are cheap to swap in
if it disappoints, because the integration shape (raw ONNX + numpy framing) is identical.

### WebRTC NS, Speex, Silero denoise

WebRTC's suppressor is the one every meeting app ships and would be the fastest of the lot, but
there is no credible cross-platform Python wheel — the maintained binding (`webrtc-noise-gain`)
publishes manylinux only, and the alternatives are single-platform or abandoned. Building
`webrtc-audio-processing` for win_amd64 and darwin-arm64 in our CI is possible but is a new
native build to babysit for a DSP-era result. `speexdsp-ns` has the same wheel gap. Silero's
denoisers are TorchScript-only under CC BY-NC-SA — out on licence before dependencies.

## 4. What the prototype ticket should measure

- **WER, not SNR.** Reuse the LibriSpeech-validation harness from `parakeet-runtime.md` §4b with
  additive noise at a few SNRs (and a handful of real dictation clips from a laptop mic), for
  both engines, with each candidate in three modes: off, full, 50/50 wet-dry. If a candidate
  cannot beat "off" on Whisper *and* Parakeet it does not ship, whatever it sounds like.
- **Wall-clock on the CPU-only primary box** for a 10 s and a 60 s clip; the numbers above are
  desktop-CPU estimates and RNNoise/DFN pay for a resample on top.
- **Gate it.** Silero's speech probability (already computed for Whisper) or RNNoise's own VAD
  output can decide whether a clip is clean enough to skip the denoiser entirely, which is the
  cheapest way to avoid the artifact penalty on the common case.
- **Packaging check on both installers**: PyInstaller hook for `pyrnnoise`'s DLL, model data file
  for GTCRN, and confirm nothing new lands in the macOS bundle that needs signing.
