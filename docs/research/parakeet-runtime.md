# Parakeet runtime & packaging (research, 2026-08-07)

Ticket: #72 step 0 — "decide the runtime before writing code". The same shape as #40's
faster-whisper stack scan: pick on **packaging weight first, latency second**, and PE-verify the
transitive DLL deps rather than trusting a wheel's declared requirements.

Measured on the 4090 box (#49), Windows 11, Python 3.12, RTX 4090 / driver 596.36. Per the
project's standing caveat, latency here is *indicative* — the charter's gates bind on the CPU-only
primary machine. The packaging numbers below are machine-independent facts.

## Verdict

**`onnx-asr` on ONNX Runtime, DirectML execution provider, falling back to the CPU provider.**
Not NeMo, not the CUDA provider.

DirectML costs about ninety times less to ship than the CUDA provider — +9 MB on the installer
against a ~1.9 GB second pack tier — and was no slower on the hardware available to compare them
on. The ticket says to pick on packaging weight first, and packaging weight is not close.

The latency comparison is the weaker of the two claims and should be read as "not a reason to pay
the 1.9 GB" rather than "DirectML is faster": §4 measured DirectML at 3x the CUDA provider, but
both were measured by repeating one clip, which §4b shows flatters DirectML specifically.

## 1. The candidates

| Runtime | Verdict | Why |
|---|---|---|
| **NeMo toolkit** | rejected without measurement | Pulls full PyTorch + CUDA wheels. Disqualifying against the PyInstaller/Inno footprint (#40, #47, #48), exactly as the ticket predicted. |
| **ORT CUDA EP** | rejected | Needs a ~1.9 GB GPU pack (§3) and is slower here than DirectML (§4). |
| **ORT DirectML EP** | **chosen** | +22 MB in the installer, no pack at all, fastest measured. |
| **ORT CPU EP** | chosen as the fallback | Free — already shipped — and quick enough to be a real answer rather than a token one (§4). |
| **sherpa-onnx** | rejected | The CPU wheel is a tidy 2.2 MB with ORT vendored inside, but there is no DirectML build; its GPU wheels carry the same CUDA+cuDNN bill as the ORT CUDA EP, from an index that is not PyPI. |

`onnx-asr` 0.12.0 itself is close to free: `numpy` is its only hard requirement, ONNX Runtime is an
extra, and its `[hub]` extra is `huggingface-hub`, which faster-whisper already pulls in.

## 2. The load-bearing find: the CUDA provider needs cuDNN, and hides it

`onnxruntime_providers_cuda.dll`'s **import table** names only cuBLAS:

```
onnxruntime-gpu 1.28.0 (CUDA 13)   import  cublas64_13.dll, cublasLt64_13.dll
onnxruntime-gpu 1.22.x (CUDA 12)   import  cublas64_12.dll, cublasLt64_12.dll,
                                           cudart64_12.dll, cudnn64_9.dll, cufft64_11.dll
```

At 1.28 the rest moved out of the import table and into `LoadLibrary`-by-name at first use —
`cudart64_13.dll`, `cudnn64_9.dll`, `cufft64_12.dll`, `nvrtc64_1*.dll` are all present as plain
strings in the binary. A scan that stopped at the import table would have concluded cuDNN was
unnecessary.

It is not. With cuBLAS alone on `PATH`, session creation succeeds and the **first encode** fails:

```
NOT_IMPLEMENTED : Non-zero status code returned while running Conv node ...
cuDNN is unavailable or disabled for CUDA Execution Provider:
LoadLibrary failed for cudnn64_9.dll with error 2
```

That is #38's failure mode verbatim, one library down the stack — which is why `ParakeetEngine`
commits to a provider only after a real encode, not after a successful construction.

The 1.28 wheel also moved to CUDA **13** (`cublas64_13`), while ctranslate2 wants cuBLAS 12. Taking
the CUDA path at a current ORT would therefore have meant shipping two cuBLAS major versions side
by side, or pinning ORT back to 1.22 to share the existing pack.

## 3. Packaging weight

Payloads, measured (extracted, not wheel-compressed):

| Component | Size | Where it would have to live |
|---|---|---|
| `cudnn_engines_precompiled64_9.dll` | 547 MB | new pack tier |
| `cudnn_adv64_9.dll` | 269 MB | new pack tier |
| `cudnn_ops64_9.dll` | 106 MB | new pack tier |
| `cudnn_graph64_9.dll` | 100 MB | new pack tier |
| `cudnn_heuristic64_9.dll` | 59 MB | new pack tier |
| rest of cuDNN 9 + cudart + cuFFT | ~40 MB | new pack tier |
| **cuDNN 9 total** | **~1.1 GB** | |
| existing GPU pack (`cublas64_12` + `cublasLt64_12`) | 771 MB | already installed by `gpu_pack.py` |
| `onnxruntime_providers_cuda.dll` | 265 MB | installer |

So the CUDA answer to "does the pack grow, gain a second tier, or go download-only" is: **a second
tier of roughly 1.9 GB**, plus a quarter-gigabyte in the installer itself — against a ~550 MB
download that is the entire GPU story today.

DirectML, for the same job:

| Component | Size | Note |
|---|---|---|
| `DirectML.dll` | 18.5 MB | ships inside the wheel |
| `onnxruntime.dll` (DML build) | 21.1 MB | **replaces** the 17.8 MB CPU build already bundled |
| `onnx-asr` | ~0.1 MB | pure Python |

`DirectML.dll`'s only non-system import is `d3d12.dll`, which is part of Windows. Nothing is
downloaded, nothing is delay-loaded from outside the app directory, and there is no second pack
tier.

Measured on the real build rather than predicted — `dist/Cadent` before and after, plus LZMA
over the changed files, which is what Inno compresses with:

| | onedir | LZMA'd |
|---|---|---|
| removed: CPU `onnxruntime` | −36.2 MB | −7.4 MB |
| added: DirectML `onnxruntime` | +66.1 MB | +16.0 MB |
| added: `onnx_asr` preprocessor assets | +6.9 MB | +0.6 MB |
| **net** | **+36.9 MB** (355.7 → 392.6 MB) | **≈ +9.2 MB** |

So the installer goes from ~92 MB to ~101 MB, about +10%. (Inno Setup is not installed on the
4090 box, so the compressed figure is LZMA over the changed payload rather than a built
`Cadent-Setup.exe`; the onedir figure is a straight measurement of two builds.)

`onnx_asr` ships mel front ends for GigaAM, Kaldi and Whisper alongside the NeMo one Parakeet
uses; the hook collects the lot because filtering `collect_data_files` by filename is the kind of
thing that breaks silently on an upgrade. 6.3 MB of the 6.9 MB is unused — 0.5 MB compressed,
which is not worth the fragility.

The one wrinkle is that `onnxruntime` and `onnxruntime-directml` are two distributions installing
the *same* `onnxruntime` package, and faster-whisper requires the former for Silero VAD. Leaving
that to resolution order would mean whichever landed last wins, so `pyproject.toml` carries a
`[tool.uv] override-dependencies` entry with a never-true marker that drops faster-whisper's
requirement. Exactly one distribution then provides the module, and the DML build serves the CPU
provider Silero needs just as well.

## 4. Latency

`istupakov/parakeet-tdt-0.6b-v3-onnx`, int8, 9.31 s SAPI utterance, 10 warm runs after a cold one:

| Provider | Cold | Warm median | Warm range | RSS | Load |
|---|---|---|---|---|---|
| **DirectML** | 0.85 s | **0.133 s** | 0.112–0.239 s | 534 MB | 4.8 s |
| CUDA | 0.60 s | 0.406 s | 0.346–0.449 s | 2127 MB | 2.4 s |
| CPU | 2.81 s | 0.737 s | 0.713–0.862 s | 961 MB | 2.3 s |

All three produced identical, correctly punctuated and capitalised text with no prompting.

### The probe is not free, and the bill is hidden

The #38 rule says commit to an accelerator only after a real encode. Doing that on the session
you then keep costs **3.5x steady-state latency, permanently**:

| First inference the session ever ran | Steady-state on a 9.3 s utterance |
|---|---|
| the 9.3 s utterance itself | 0.119 s |
| 0.1 s of silence | 0.44 s |
| 0.1 s of white noise | 0.43 s |
| 2 s / 10 s / 30 s of silence | 0.42 / 0.42 / 0.48 s |
| 0.1 s of silence, on a session then discarded | 0.154 s |

ONNX Runtime derives a session's execution plan from its first inference, and a probe-shaped one
sticks. Nothing about the probe's *content* or *length* rescues it — noise, tone, real speech and
thirty seconds of silence all land in the same place; only never having probed that particular
session does. (Note the last row: a 0.1 s call made *after* a session is warm is harmless. It is
specifically the first one that decides.)

So `ParakeetEngine` probes a session and throws it away, then builds the one it keeps. The cost is
a second session construction — load goes from ~5 s to ~12 s, once, on the background thread that
already preloads STT. That is the right trade: load happens once and is already asynchronous,
where the latency it buys back is paid on every dictation forever.

Two more things worth naming:

- **CUDA lost on this measurement**, probably because on an int8 graph the CUDA EP dequantises to
  fp32 where DirectML runs the quantised kernels; it also cost 4x the resident memory. Treat it as
  weak evidence: it is int8-specific, it may invert for fp32 weights, and §4b shows this style of
  measurement favours DirectML. It is not worth 1.9 GB to find out which.
- **CPU is a real fallback.** 0.737 s for 9.3 s of audio contradicts the ticket's premise that
  "Parakeet has no CPU story worth having", and is why a failed GPU probe keeps the user's chosen
  engine on the CPU provider rather than silently defecting to Whisper. The caveat stands that this
  is a fast desktop CPU; the primary box will be slower.

## 4b. What it looks like through `cadent.stt` — and why the numbers above flatter it

Everything in §4 repeats one clip. Real dictation never does, and on DirectML that is the whole
story: **every novel input length costs a fresh execution plan, about 0.4 s of it.** Measured
through `make_engine`, on 12 clips of 12 different lengths:

| | wall clock | realtime factor |
|---|---|---|
| the same 9.3 s clip, 12 times | 1.92 s | 58x |
| 12 clips of distinct lengths | 5.73 s | 12.6x |
| the same 12, padded up to 5 s buckets | 5.87 s | 17.9x |

Padding buys back the plan and pays for it in compute — the wall clock is unchanged, so it is not
worth doing. The honest per-utterance figure is the varied one, and `scripts/bench_parakeet.py`
reports `varied_median_s` for exactly this reason.

**Latency and accuracy together**, on the 4090 box. Latency is the median of eight clips of
distinct lengths (3.0–5.9 s); WER is over the 73 utterances of LibriSpeech validation-clean, case
and punctuation normalised away:

| engine / model / runtime | landed on | per utterance | WER | load |
|---|---|---|---|---|
| faster-whisper `distil-small.en` (today's default) | cuda | 0.065 s | 6.26% | 1.7 s |
| faster-whisper `distil-small.en` | cpu | 0.975 s | 6.52% | 1.5 s |
| faster-whisper `distil-large-v3` | cuda | 0.102 s | 5.30% | 2.8 s |
| **parakeet `-v2`** | **directml** | **0.407 s** | **2.87%** | 11.5 s |
| parakeet `-v3` | directml | 0.463 s | 4.35% | 11.0 s |
| parakeet `-v3` | cpu | 0.519 s | 4.87% | 7.8 s |

Reading it:

- **Parakeet roughly halves the error rate.** 2.87% against the current default's 6.26% — and it
  still beats `distil-large-v3`, the model the wizard recommends to a 6 GB GPU today, by two
  points while being no slower than half a second.
- **It is not the fastest option**, and the earlier claim that DirectML beat CUDA outright does
  not survive varying input lengths. It costs ~6x the CPU time of `distil-small.en`-on-CUDA. Both
  sit far inside the ≤2 s dictation budget, so this buys accuracy with latency nobody will feel.
- **The comparison that matters most is against CPU Whisper**, because the cuBLAS pack is a 550 MB
  opt-in most users will never take. There Parakeet wins on both axes at once: 0.41 s against
  0.98 s, and 2.87% against 6.52%.
- **v2 beats v3 on English by 1.5 points**, and is marginally faster. v3's twenty-four other
  languages are not something an app that calls `language="en"` can spend, so **v2 is the
  default** and v3 is there for anyone who wants the rest.
- Load time is the price of the throwaway probe: ~11 s against Whisper's ~2 s, once, on the
  background thread that already preloads STT.

## 5. Weights

Both checkpoints are offered, from `istupakov`'s ONNX exports, int8. **v2 is the default** on the
§4b measurement — 2.87% WER against v3's 4.35% on English:

| Model | Encoder | Decoder+joint | Total download | Languages |
|---|---|---|---|---|
| `parakeet-tdt-0.6b-v2` | 652 MB | 9 MB | ~661 MB | English |
| `parakeet-tdt-0.6b-v3` | 652 MB | 18 MB | ~670 MB | 25 European incl. English |

The fp32 exports are ~2.5 GB for a quality difference that does not survive a dictation microphone;
int8 is the only tier shipped.

**License correction:** the ticket says Apache-2.0. Both `nvidia/parakeet-tdt-0.6b-v2` and `-v3`,
and the ONNX re-exports, are **CC-BY-4.0** — commercial use is fine, attribution is required. That
is a real obligation the Whisper models do not carry, and the About surface should credit NVIDIA if
a Parakeet model is in use.

## 6. Consequences for the rest of the app

- **The GPU gate changes meaning.** `gpu_pack.nvidia_driver_present()` asks "is there an NVIDIA
  driver". DirectML asks "is there a Direct3D 12 device", which is true on AMD and Intel GPUs too.
  Hence `hardware.dx12_gpu_present()`, in the same ctypes style as the `nvcuda.dll` probe.
- **Vocabulary biasing does not exist here.** `hotwords=` is a faster-whisper parameter with no
  Parakeet equivalent, and inventing one is out of scope. The engine declares
  `supports_hotwords = False`, the pipeline skips packing entirely for it — which also stops it
  reporting terms as "dropped" when nothing was ever going to be sent — and vocabulary falls back
  to the layer-2 post-correction pass in `vocabulary.py`. The Settings UI says so out loud.
- **VAD is neither bundled nor needed.** `vad_filter=True` is faster-whisper's Silero. Parakeet's
  TDT decoder emits nothing for silence — a 1 s zero buffer returns `""` — so the Parakeet path
  runs no gate at all. `scripts/build.py` keeps datas-ing in the Silero asset for the Whisper path.
