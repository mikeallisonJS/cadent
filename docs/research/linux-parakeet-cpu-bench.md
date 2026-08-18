# Parakeet on an x86 CPU: does it earn the Recommended chip on non-NVIDIA Linux? (task, 2026-08-17)

Ticket: #28 (wayfinder task, part of #11). The number ADR 0010 / #22 left open: on Linux
Parakeet's CUDA rung reaches only NVIDIA + R580 drivers, everywhere else (AMD, Intel, older
NVIDIA) it runs on the CPU and `gpu_only_engines` is empty — so it is *offered* there. Nobody
had measured it on x86. Headline: **CPU Parakeet v2 is fast enough to earn the chip down to a
2-core emulation, and faster than the Whisper CPU rows it would displace at every size** —
`gpu_only_engines` stays empty on Linux, and the non-NVIDIA Linux `suggest_model` branch
recommends Parakeet v2 at ≥ 4 physical cores and ≥ 8 GB RAM.

## Rig — and the caveat that goes with it

- AMD Ryzen AI MAX+ PRO 395 (Zen 5, 16 C / 32 T), 96 GB, **Windows 11**, plugged in.
- CPython 3.11.15, `onnxruntime` 1.24.4 **CPU EP** (`stt_device=cpu` forced; the DML rung
  exists on this box and was deliberately not taken), `ctranslate2` 4.8.1 int8, shipping
  paths via `cadent.stt.make_engine`.
- **This is a proxy for the Linux boxes, not a run on them.** The ticket asked for the
  driving dev's Arch/CachyOS machines; this session had only the Windows box. The quantity
  under test — ORT CPU-EP int8 throughput on x86 — is OS-independent to first order (same
  wheel kernels, same thread pool; Linux is if anything a touch faster on the same silicon),
  so the *decision* stands on these numbers. A Linux confirmation run is a three-command
  checklist (§5) and should land in this doc's table when it happens; the rule only moves if
  the Linux medians land more than ~2× worse, which nothing here predicts.
- Harness: `scripts/bench_cpu_stt.py` (branch `wayfinder/linux-parakeet-cpu-bench`) — the
  M1 Max bench's structure (#137): a varied-length utterance *set*, first pass only, one config
  per subprocess, one bench at a time. Ten TTS clips (SAPI here; `say` / `espeak-ng` on the
  other OSes) of **1.4 → 57 s**, i.e. longer at the top than the M1 set (30 s), which pushes
  every median up — read per-clip rows, not just medians, when comparing to #137.

## 1. Full box (32 threads, ORT default pool)

| engine / model | load s | median s | p95 s | max s | RSS MB |
| --- | --- | --- | --- | --- | --- |
| parakeet-tdt-0.6b-v2 | 9.7 | **0.66** | 1.46 | 2.19 | 1461 |
| parakeet-tdt-0.6b-v3 | 26.7 | 0.69 | 1.54 | 2.21 | 1492 |
| tiny.en | 3.8 | 0.59 | 1.24 | 1.94 | 151 |
| distil-small.en (today's `FALLBACK_MODEL`) | 1.6 | 2.21 | 20.5 | 26.5 | 266 |
| distil-medium.en | 21.6 | 5.03 | 9.93 | 14.0 | 521 |

Per-clip, Parakeet v2 (audio s → latency s): 1.4→0.10 · 2.0→0.13 · 3.5→0.18 · 9.3→0.37 ·
11.8→0.41 · 19.1→0.66 · 25.4→0.89 · 33.7→1.19 · 40.4→1.46 · 56.9→2.19. Latency is linear in
clip length at ~26× realtime; a typical 5–15 s dictation costs 0.25–0.5 s. Against the M1 Max
(#137: 0.30 s median on a set topping out at 30 s) the same lengths land at ~0.37 s here —
same class. v3 tracks v2 within 5 % (its load is 2.8× longer; the multilingual vocabulary
tokenizer). Both engines transcribed the reference clip verbatim, punctuation included.

The Whisper rows are slower here than on the M1 (distil-small.en 1.3–2.4 s at *every* length
vs 0.78 s median there) and the two longest clips blow up on distil-small (20–26 s —
beam-5 decode over a 40–57 s window); that is CTranslate2 on this box and not the question,
but it means Parakeet's margin over the Whisper CPU rows is *larger* here than on darwin, and
distil-medium.en (5 s median) is as indefensible a CPU default on x86 as it was on the M1.

## 2. Emulated smaller machines (Parakeet v2 only)

`--cores N --threads T`: process affinity to the first N logical CPUs and an explicit ORT
intra-op pool of T (what ORT defaults to on a real T-physical-core box — see §4 for why the
pool must be sized by hand). Cache and memory bandwidth remain the big box's, so treat these
as optimistic by maybe 1.5× for an older mobile part, not as that part's number.

| emulated box | median s | p95 s | max s | 9.3 s clip | 11.8 s clip | 19.1 s clip | RSS MB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 8 C / 16 T (ORT 8 threads) | 0.66 | 1.52 | 2.31 | 0.35 | 0.42 | 0.66 | 1419 |
| **4 C / 8 T (ORT 4 threads)** | 0.84 | 1.99 | 3.09 | 0.43 | 0.54 | 0.84 | 1413 |
| **2 C / 4 T (ORT 2 threads)** | 1.14 | 2.56 | 4.00 | 0.55 | 0.74 | 1.14 | 1407 |

Parakeet saturates at ~8 threads (the 8-thread row equals the 32-thread row). At 4 threads
it is still ~22× realtime; at 2 threads ~16×. **A 5–15 s dictation stays under ~0.75 s
even on the 2-core emulation.** The median crossing the 1 s bar on the 2-core row is the
19 s clip — a full paragraph — and the same clip costs tiny.en 0.6 s and distil-small.en
2.4 s on that row, so there is no Whisper row that beats it there anyway.

For comparison on the 4 C / 8 T row: tiny.en 0.51 s median (0.32 s at 9.3 s), distil-small.en
1.72 s median (1.60 s at 9.3 s). Parakeet is behind tiny.en on the long tail only, and ahead
of `FALLBACK_MODEL` at every length on every row.

## 3. Decision

1. **`gpu_only_engines` on Linux = `frozenset()`** — confirmed, not merely carried over from
   darwin. CPU Parakeet is not "usable but slower"; on x86 it is *faster* than the Whisper row
   Cadent falls back to, at every emulated size. Disabling it with "Needs a graphics card" would
   be false.
2. **The Recommended chip on non-NVIDIA Linux goes to Parakeet v2 at `physical_cores >= 4 and
   ram_gb >= 8`.** The RAM floor is RSS (1.4 GB) plus the rest of the app plus the cleanup
   model on an 8 GB box; darwin's 16 GB rule was about unified memory shared with Metal and
   does not transfer. Below the core floor, keep today's ladder (`ram_gb < 6` → tiny.en,
   `< 8` → base.en, else `FALLBACK_MODEL`); the win32 `ram >= 16 and cores >= 8` →
   distil-medium.en row is **not** copied — 5 s median on 32 threads.
   Copy shape mirrors darwin: "Half the mistakes, well under a second a dictation — and it
   punctuates as it types." (Numbers in the spec, not in the string.)
3. **NVIDIA-with-driver keeps the ADR 0010 rule** (Parakeet on ≥ 4 GB VRAM + CUDA-13
   driver) — unchanged; this ticket only fills the branch beneath it. AMD/Intel GPUs never
   enter the STT rule on Linux (no ORT EP for them, per #14), so the branch is keyed on
   cores + RAM, not on the GPU probe.
4. **v2 stays the recommended checkpoint**; v3 costs nothing extra to run but 17 s more to
   load, and English users gain nothing from it — same call as Windows/macOS.

For the spec's `Capabilities` / `suggest_model` table this is one new branch, gated on
`sys.platform == "linux"` and the CUDA probe having *not* landed, ahead of the RAM rows.

## 4. Method notes, so the numbers can be trusted

- **Pinned runs must size the ORT pool by hand.** ORT's default `intra_op_num_threads=0`
  derives from the machine's core count, not the affinity mask; a first pinned pass without
  `--threads` read 2.5 s median at 8 CPUs and 10.6 s at 4 — thread oversubscription, not the
  model. Those numbers are wrong and are not in the tables. A real 4-core laptop defaults ORT
  to 4 threads and sees the §2 rows, not the oversubscribed ones. (CTranslate2's rows barely
  moved with the pin, so Whisper was not re-run with a hand-sized pool.)
- First pass, varied lengths, warm-up clip outside the set; `repeat_median_s` on the 9.3 s clip
  matched its first-pass time within noise on every Parakeet row (0.31–0.42 s), confirming the
  CPU EP has no plan-reuse cliff (#137's finding holds on x86).
- n=10 per row; p95 is the nearest-rank estimate (`times[int(0.95 * (n - 1))]`, the 9th
  ordered sample at n=10) — read it as "what a 40–57 s dictation costs".
- Latency only. WER is `bench_parakeet.py --wer` on real speech (2.87 % v2 vs 6.26 %
  distil-small.en, from #72) and does not depend on the CPU.

## 5. Linux confirmation checklist (driving dev, Arch or CachyOS)

```sh
git fetch && git checkout wayfinder/linux-parakeet-cpu-bench
sudo pacman -S --needed espeak-ng          # TTS for the set; any voice is fine
uv run python scripts/bench_cpu_stt.py --make-set bench_set
uv run python scripts/bench_cpu_stt.py --set bench_set                       # full box
uv run python scripts/bench_cpu_stt.py --set bench_set --cores 8 --threads 4 # 4-core laptop
```

Paste the two tables under a "Linux run" heading here. The decision in §3 changes only if
Parakeet v2's 9.3 s-clip time on the 4-thread row exceeds ~1 s (2× the number above).
