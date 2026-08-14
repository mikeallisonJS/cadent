# The M1 Max numbers: speech rungs and one heavy cleanup tier (#137)

The measurements #132's contingent rules were waiting on, taken on the real
target machine. Headline: **Parakeet v2 earns the Apple-Silicon Recommended
chip** (varied-length median 0.30 s against the ~≤ 1 s bar), and **Metal full
offload is unambiguous for cleanup** (Qwen3-4B warm cleans in 0.15–1.3 s at
~55–61 tok/s, versus 0.33–3.3 s on the CPU).

## Rig

- Apple M1 Max, 32 GB, macOS 26.4.1, CPython 3.13.13, plugged in.
- `onnxruntime` 1.28.0 (CPU EP), `faster-whisper`/CTranslate2 CPU int8,
  `llama-cpp-python` 0.3.34 built from source with Metal (per #129 — the
  pinned cpu-index wheels are corrupt on macOS).
- Branch `task-137-bench-m1max`; harnesses `scripts/bench_macos_stt.py` and
  `scripts/bench_macos_llm.py` (siblings of `bench_parakeet.py` /
  `bench_llm.py` — same shipping code paths, new axes: a varied-length
  utterance *set*, and `n_gpu_layers`).

## Method notes, so the numbers can be trusted

- **Varied lengths, first pass only.** Ten distinct utterances (1.5 s to
  30.1 s of `say`-synthesized dictation at 16 kHz mono), each timed once
  after a warm-up clip outside the set. ONNX Runtime re-plans per novel
  input length, so repeating one clip flatters it; here the repeat-median
  column shows that on the CPU EP the effect is nil (repeat ≈ varied median
  for every engine — the DirectML plan-reuse cliff from #72 does not exist
  on this rung).
- **Shipping transcribe paths**: `cadent.stt.make_engine`, VAD on, beam 5
  for Whisper; int8 quantization both engines. Latency only — synthesized
  speech says nothing honest about WER (see `bench_parakeet.py --wer` for
  that, on real speech).
- **One config per subprocess, one bench at a time.** A first pass ran two
  benches concurrently and the contention was visible (distil-medium.en
  read 4.8 s median against a real 1.61 s); every number below is from a
  solo re-run with cached weights.
- With n=10, p95 interpolates between the 9th and 10th sample — read it as
  "what a 20–30 s dictation costs", not as tail jitter.

## Speech: insert latency on the varied set

| engine / model | landed on | load s | median s | p95 s | max s | repeat-median s | RSS MB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| parakeet-tdt-0.6b-v2 | CPU EP¹ | 2.92 | **0.30** | 0.70 | 0.83 | 0.30 | 1663 |
| tiny.en | cpu | 3.09 | 0.31 | 0.68 | 0.75 | 0.31 | 367 |
| distil-small.en | cpu | 1.29 | 0.78 | 1.80 | 2.45 | 0.79 | 968 |
| distil-medium.en | cpu | 1.05 | 1.61 | 2.64 | 3.35 | 1.62 | 1361 |
| distil-large-v3 | cpu | 1.48 | 2.79 | 4.52 | 5.77 | 2.76 | 1544 |

¹ The engine *reported* `directml`. The current provider ladder asks ONNX
Runtime for `DmlExecutionProvider` first, and ORT on macOS **warns and
silently falls back to the CPU EP while the session constructs fine** — so
the probe "succeeds", `landed_on` lies, and every downstream surface gated on
it (the GPU-pack offer, Settings labels) would lie with it. Verified by
inspecting `session.get_providers()`: all three sessions ran on
`CPUExecutionProvider`. The numbers are therefore genuine CPU-EP numbers,
and the darwin ladder #132 already decided (`("auto", "cpu")`, no DML rung)
is load-bearing, not cosmetic.

Latency scales gently with clip length for Parakeet (0.07 s at 1.5 s of
audio → 0.83 s at 30 s) and is dominated by a per-clip floor for Whisper
(distil-large-v3: 2.5 s for a 1.5 s clip, 5.8 s for a 30 s one — the floor
*is* the median). Parakeet's load 2.92 s includes the probe-then-discard
double construction.

### What this settles (the #132 contingent rules)

- **Parakeet v2 median 0.30 s ≤ ~1 s → it gets the Apple-Silicon Recommended
  chip at RAM ≥ 16 GB.** Even its p95 — a 30 s dictation — is under the bar.
- The Whisper rung ordering survives on macOS: every distil tier is 2.5–9×
  slower than Parakeet at the median, and distil-medium/large sit above the
  1 s line, so nothing below distil-small is a defensible darwin default.

## Cleanup: Qwen3-4B Q4_K_M, Metal vs CPU

`Cleaner.load()` settings (n_ctx 4096, physical-core threads, shipping
system prompt, length-scaled max_tokens); cold = first call after load, warm
= two repeats; the one-token row is #132's commit-proof generation.

| | load s | one-token proof s | short (8 tok) warm | medium (25 tok) warm | long (79 tok) warm | tok/s | RSS MB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Metal, full offload (`n_gpu_layers=-1`) | 0.37 | 0.09 | 0.15 | 0.42 | 1.3 | 55–61 | 3225 |
| CPU (`n_gpu_layers=0`) | 2.92 | 0.15 | 0.33 | 1.0 | 3.0–3.3 | 24–26 | 5716 |

- **`auto` = Metal full offload is confirmed as the darwin default**: ~2.4×
  the CPU throughput, faster to load, *lower* RSS (weights live in
  Metal-managed memory), and the one-token proof costs 0.09 s — cheap enough
  to run on every load.
- The **`MAY_BE_SLOW` drop holds**: the heavy tier's worst warm clean (a
  ~30 s dictation) is 1.3 s on the default path. Cold-start adds ~0.2 s on
  the first clean only.
- The CPU escape hatch is *usable*, not just survivable: 3.3 s worst-case on
  the long transcript. Both devices produced identical, correct cleanups.

## Numbers the implementation spec should carry

- darwin `suggest_model()`: Parakeet v2 chipped at RAM ≥ 16 GB (median
  0.30 s); distil-small.en is the sub-1-second Whisper fallback (0.78 s).
- Cleanup chip constants on darwin: 4B tier measured at 0.15/0.42/1.3 s
  warm (short/medium/long) on Metal — no "unmeasured" hedge needed.
- The darwin provider ladder must not contain a DML rung (it "succeeds"
  silently on the CPU) and `landed_on` must be derived from
  `session.get_providers()`, not from which ladder entry didn't throw.
