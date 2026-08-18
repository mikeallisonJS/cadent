# Denoiser bake-off on the noisy evaluation set (prototype, 2026-08-18)

Ticket: #48, on the #43 map ("Wayfinder: noise reduction"). The shortlist from
`noise-suppression.md` (#44) run through the WER harness from #46 (`scripts/noise_eval.py`,
PR #50), both STT engines, plus CPU cost and install weight. Harness for this bake-off:
`scripts/noise_bakeoff.py` on the `prototype/noise-bakeoff` branch. Everything below is
measured, on this machine, on the **synthetic** set (SAPI voice, synthetic noise beds) — the
own-voice clips (`noise_eval.py record`) had not been recorded when this ran; see §5.

## Verdict

**No shortlisted denoiser earns a place in front of either engine.** At full strength every
candidate makes WER *worse* on both engines — GTCRN by +2.6 to +3.6 points overall on Parakeet
and +4.8 to +5.8 on Whisper (+20 to +44 points on 0 dB babble); RNNoise by +10 (Parakeet) and
+13.7 (Whisper) overall, with Whisper hallucinating to 146 % WER on 0 dB babble; numpy spectral
gating −0.1 / +1.1. That is exactly the artifact penalty #45 predicted from the literature, now
reproduced on our own set: the SNR-vs-clean table (§2) shows every candidate *does* raise SNR by
2–5 dB at low SNR, and both recognizers still transcribe the noisy original better than the
cleaner-looking output.

The **50/50 wet/dry mixes** ("observation adding") remove most of the damage but do not turn it
into a gain: GTCRN mix50 −0.1 / +0.6, spectral-gate mix50 −0.7 / **0.0** (Parakeet / Whisper),
RNNoise mix50 +0.5 / +2.1. The only consistent wins are on 0 dB café/babble (−1 to −6 points),
where the baseline is already an unusable transcript (20–54 % WER), and they are paid for by
+0.5–1 point losses at 5–10 dB, which is where a real user actually dictates.

**Recommendation for the spec:** ship the STT-side decoding knobs from #45 unconditionally (as
#47 already decided) and do **not** put a denoiser behind the toggle on this evidence — none of
the three approaches beats "off" on both engines, which was the shipping bar set in #44 §4.
Two things could still change that, both cheap to check: (a) own-voice recordings through a
real mic (§5) — synthetic TTS is unnaturally clean and may be hiding the regime where a
front-end helps; (b) a noise-gated mix (denoise only when the clip is estimated below ~5 dB),
which would keep the 0 dB gains without the 5–10 dB losses. If either pans out, the arm to
ship is the cheapest one — numpy spectral gating at 50 % wet/dry (zero dependencies, zero
download, 0.02 s per 10 s). GTCRN is the neural fallback (0.42 MB, zero new packages, 0.12 s
per 10 s with the whole-buffer graph) but on this set it loses to doing nothing; RNNoise is out
on every axis (slowest, most damaging, broken binding).

## 1. WER by condition

WER % per (noise, SNR); delta vs the raw mix in parentheses; `all` pools every condition
including clean. 15 clips × (4 beds × 4 SNRs + clean) = 255 utterances, 3 536 reference words
per engine per candidate. Both engines run through `cadent.stt.make_engine` exactly as the app
does (`vad_filter=True` on Whisper).

### Parakeet (parakeet-tdt-0.6b-v2, DirectML)

| condition | raw | gtcrn-full-mix50 | gtcrn-full | gtcrn-mix50 | gtcrn | rnnoise-mix50 | rnnoise | specgate-mix50 | specgate |
|---|---|---|---|---|---|---|---|---|---|
| clean | 2.4 | 1.0 (-1.4) | 2.4 (+0.0) | 2.4 (+0.0) | 2.4 (+0.0) | 1.0 (-1.4) | 1.0 (-1.4) | 2.4 (+0.0) | 1.0 (-1.4) |
| cafe 20 dB | 1.0 | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) |
| cafe 10 dB | 1.0 | 1.4 (+0.5) | 2.4 (+1.4) | 1.0 (+0.0) | 3.9 (+2.9) | 1.0 (+0.0) | 1.4 (+0.5) | 1.9 (+1.0) | 1.9 (+1.0) |
| cafe 5 dB | 2.9 | 2.9 (+0.0) | 3.4 (+0.5) | 2.9 (+0.0) | 2.4 (-0.5) | 2.9 (+0.0) | 11.1 (+8.2) | 2.9 (+0.0) | 2.9 (+0.0) |
| cafe 0 dB | 21.6 | 20.2 (-1.4) | 33.7 (+12.0) | 17.3 (-4.3) | 31.2 (+9.6) | 26.0 (+4.3) | 68.3 (+46.6) | 15.9 (-5.8) | 19.7 (-1.9) |
| chatter 20 dB | 1.0 | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 2.9 (+1.9) | 1.0 (+0.0) | 1.0 (+0.0) |
| chatter 10 dB | 1.9 | 1.0 (-1.0) | 1.9 (+0.0) | 1.9 (+0.0) | 1.9 (+0.0) | 1.9 (+0.0) | 6.7 (+4.8) | 1.9 (+0.0) | 1.9 (+0.0) |
| chatter 5 dB | 2.9 | 3.9 (+1.0) | 11.5 (+8.7) | 4.3 (+1.4) | 8.2 (+5.3) | 2.9 (+0.0) | 48.1 (+45.2) | 2.9 (+0.0) | 2.9 (+0.0) |
| chatter 0 dB | 48.1 | 48.1 (+0.0) | 77.9 (+29.8) | 49.5 (+1.4) | 68.3 (+20.2) | 51.0 (+2.9) | 93.8 (+45.7) | 42.8 (-5.3) | 49.0 (+1.0) |
| fan 20 dB | 1.0 | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) |
| fan 10 dB | 1.0 | 1.9 (+1.0) | 2.4 (+1.4) | 1.0 (+0.0) | 1.9 (+1.0) | 2.4 (+1.4) | 2.9 (+1.9) | 1.0 (+0.0) | 1.0 (+0.0) |
| fan 5 dB | 2.4 | 2.4 (+0.0) | 3.4 (+1.0) | 2.9 (+0.5) | 2.4 (+0.0) | 2.4 (+0.0) | 2.9 (+0.5) | 2.9 (+0.5) | 2.9 (+0.5) |
| fan 0 dB | 5.3 | 3.9 (-1.4) | 10.6 (+5.3) | 4.3 (-1.0) | 8.6 (+3.4) | 6.7 (+1.4) | 20.2 (+14.9) | 3.9 (-1.4) | 4.3 (-1.0) |
| keyboard 20 dB | 1.0 | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 2.4 (+1.4) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) |
| keyboard 10 dB | 1.0 | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) |
| keyboard 5 dB | 1.0 | 1.0 (+0.0) | 1.4 (+0.5) | 1.0 (+0.0) | 1.9 (+1.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) |
| keyboard 0 dB | 1.0 | 1.0 (+0.0) | 1.0 (+0.0) | 1.0 (+0.0) | 1.4 (+0.5) | 1.4 (+0.5) | 1.4 (+0.5) | 1.0 (+0.0) | 1.0 (+0.0) |
| **all** | 5.7 | 5.5 (-0.2) | 9.2 (+3.6) | 5.5 (-0.1) | 8.3 (+2.6) | 6.2 (+0.5) | 15.6 (+10.0) | 5.0 (-0.7) | 5.5 (-0.1) |

### faster-whisper (distil-small.en, CPU)

| condition | raw | gtcrn-full-mix50 | gtcrn-full | gtcrn-mix50 | gtcrn | rnnoise-mix50 | rnnoise | specgate-mix50 | specgate |
|---|---|---|---|---|---|---|---|---|---|
| clean | 0.5 | 0.5 (+0.0) | 1.4 (+1.0) | 1.4 (+1.0) | 0.5 (+0.0) | 1.0 (+0.5) | 0.5 (+0.0) | 1.4 (+1.0) | 0.5 (+0.0) |
| cafe 20 dB | 1.4 | 1.4 (+0.0) | 1.0 (-0.5) | 1.4 (+0.0) | 0.5 (-1.0) | 1.9 (+0.5) | 1.4 (+0.0) | 1.4 (+0.0) | 0.5 (-1.0) |
| cafe 10 dB | 1.4 | 1.9 (+0.5) | 3.9 (+2.4) | 1.4 (+0.0) | 3.4 (+1.9) | 4.3 (+2.9) | 2.9 (+1.4) | 1.4 (+0.0) | 3.4 (+1.9) |
| cafe 5 dB | 3.9 | 5.3 (+1.4) | 8.2 (+4.3) | 3.9 (+0.0) | 11.1 (+7.2) | 5.3 (+1.4) | 13.9 (+10.1) | 3.9 (+0.0) | 7.7 (+3.8) |
| cafe 0 dB | 20.2 | 24.0 (+3.9) | 41.8 (+21.6) | 23.6 (+3.4) | 41.3 (+21.2) | 30.3 (+10.1) | 75.5 (+55.3) | 19.2 (-1.0) | 23.6 (+3.4) |
| chatter 20 dB | 2.4 | 2.4 (+0.0) | 1.4 (-1.0) | 2.4 (+0.0) | 2.4 (+0.0) | 2.4 (+0.0) | 3.4 (+1.0) | 2.4 (+0.0) | 2.4 (+0.0) |
| chatter 10 dB | 2.9 | 3.4 (+0.5) | 3.9 (+1.0) | 2.9 (+0.0) | 3.4 (+0.5) | 2.9 (+0.0) | 5.8 (+2.9) | 3.4 (+0.5) | 2.4 (-0.5) |
| chatter 5 dB | 8.6 | 9.1 (+0.5) | 13.5 (+4.8) | 8.2 (-0.5) | 13.0 (+4.3) | 8.2 (-0.5) | 53.4 (+44.7) | 7.7 (-1.0) | 7.7 (-1.0) |
| chatter 0 dB | 54.3 | 53.8 (-0.5) | 98.6 (+44.2) | 60.6 (+6.2) | 89.4 (+35.1) | 66.3 (+12.0) | 146.2 (+91.8) | 51.9 (-2.4) | 62.5 (+8.2) |
| fan 20 dB | 1.4 | 1.4 (+0.0) | 0.5 (-1.0) | 1.4 (+0.0) | 1.4 (+0.0) | 1.9 (+0.5) | 0.5 (-1.0) | 1.4 (+0.0) | 0.5 (-1.0) |
| fan 10 dB | 1.4 | 2.4 (+1.0) | 3.4 (+1.9) | 1.9 (+0.5) | 3.9 (+2.4) | 1.9 (+0.5) | 2.9 (+1.4) | 2.4 (+1.0) | 1.9 (+0.5) |
| fan 5 dB | 3.9 | 3.4 (-0.5) | 5.8 (+1.9) | 3.9 (+0.0) | 1.9 (-1.9) | 5.3 (+1.4) | 8.2 (+4.3) | 3.9 (+0.0) | 5.8 (+1.9) |
| fan 0 dB | 7.2 | 12.0 (+4.8) | 23.6 (+16.4) | 8.2 (+1.0) | 19.7 (+12.5) | 12.0 (+4.8) | 30.3 (+23.1) | 10.6 (+3.4) | 9.6 (+2.4) |
| keyboard 20 dB | 0.5 | 0.5 (+0.0) | 0.5 (+0.0) | 0.5 (+0.0) | 0.5 (+0.0) | 1.0 (+0.5) | 0.5 (+0.0) | 0.5 (+0.0) | 0.5 (+0.0) |
| keyboard 10 dB | 1.0 | 0.5 (-0.5) | 2.4 (+1.4) | 1.0 (+0.0) | 1.4 (+0.5) | 1.4 (+0.5) | 0.5 (-0.5) | 0.5 (-0.5) | 1.4 (+0.5) |
| keyboard 5 dB | 1.9 | 1.0 (-1.0) | 1.4 (-0.5) | 1.0 (-1.0) | 1.4 (-0.5) | 2.4 (+0.5) | 1.0 (-1.0) | 1.9 (+0.0) | 1.9 (+0.0) |
| keyboard 0 dB | 3.4 | 3.4 (+0.0) | 3.4 (+0.0) | 2.4 (-1.0) | 2.9 (-0.5) | 3.4 (+0.0) | 2.9 (-0.5) | 2.4 (-1.0) | 2.4 (-1.0) |
| **all** | 6.8 | 7.4 (+0.6) | 12.6 (+5.8) | 7.4 (+0.6) | 11.7 (+4.8) | 8.9 (+2.1) | 20.6 (+13.7) | 6.8 (+0.0) | 7.9 (+1.1) |

## 2. Objective check: SNR vs the clean reference

Independent of the recognizers — does each candidate actually remove noise? Mean SNR of the
raw mix against the clean clip, and each candidate's change to it (RNNoise output is
realigned by its 20 ms frame delay first).

| condition | raw SNR | gtcrn ΔSNR | gtcrn-full ΔSNR | rnnoise ΔSNR | specgate ΔSNR |
|---|---|---|---|---|---|
| cafe 20 dB | 17.8 | +2.0 | +1.4 | -6.6 | +1.9 |
| cafe 10 dB | 8.0 | +2.7 | +3.2 | +1.1 | +2.9 |
| cafe 5 dB | 3.3 | +2.1 | +4.0 | +3.4 | +3.3 |
| cafe 0 dB | -0.6 | +2.9 | +4.3 | +4.3 | +3.8 |
| chatter 20 dB | 17.8 | +0.8 | +1.0 | -7.3 | +1.7 |
| chatter 10 dB | 8.1 | +1.5 | +2.1 | -0.5 | +1.9 |
| chatter 5 dB | 3.7 | +1.3 | +2.2 | +0.1 | +2.0 |
| chatter 0 dB | -0.4 | +2.5 | +2.6 | +1.9 | +2.2 |
| fan 20 dB | 17.8 | +2.0 | +1.3 | -6.6 | +1.4 |
| fan 10 dB | 7.9 | +3.1 | +3.4 | +1.3 | +3.0 |
| fan 5 dB | 3.2 | +2.5 | +4.2 | +3.9 | +3.8 |
| fan 0 dB | -1.0 | +3.5 | +5.2 | +5.4 | +4.6 |
| keyboard 20 dB | 17.8 | +3.3 | +3.0 | -6.1 | -0.1 |
| keyboard 10 dB | 8.1 | +3.2 | +3.0 | +1.1 | +0.1 |
| keyboard 5 dB | 4.1 | +0.5 | +0.7 | +0.6 | +0.0 |
| keyboard 0 dB | 1.8 | +0.2 | +0.4 | +0.4 | +0.0 |

So the denoisers work as denoisers (a few dB, most on stationary fan/café at low SNR, almost
nothing on keyboard transients or babble), and RNNoise's own processing costs ~11 dB of
fidelity even on near-clean input — the WER damage is not because they fail to remove noise;
it is what they do to the speech while removing it.

## 3. CPU cost

Wall-clock for the whole-utterance pass, single ORT thread, i9-13900KS (a fast desktop — a
laptop will be 2–4× slower). Budget from #47: **≤ 0.3 s per 10 s of audio**.

| candidate | RTF on the set | 10 s clip | 60 s clip | within budget |
|---|---|---|---|---|
| gtcrn (streaming graph, one ORT call per 16 ms frame) | 0.073 | 0.70 s | 4.5 s | **no** — Python per-frame loop overhead, ~1.1 ms per call |
| gtcrn-full (whole-buffer graph, one ORT call) | 0.012 | 0.12 s | 1.07 s | yes |
| rnnoise (ctypes over the wheel's DLL, + 16→48→16 k resample) | 0.086 | 0.88 s | 5.1 s | **no** — the bundled DLL runs 0.86 s per 10 s on real signal (0.05 s on silence); the resampler is 0.02 s |
| specgate (numpy) | 0.002 | 0.02 s | 0.13 s | yes |

macOS arm64 was **not** measured here (no Mac in this session). The script is
platform-neutral (`librnnoise.dylib` from the same wheel; ONNX and numpy identical) — run
`uv run python scripts/noise_bakeoff.py process` on the M1 Max to fill that in.

Notes on the two GTCRN graphs: `gtcrn_simple.onnx` (sherpa-onnx release, 0.54 MB) is the
author's *streaming* export; my per-frame loop reproduces the author's reference `enh.wav`
to 71 dB SNR, so the numpy front end (512/256, √Hann, centre-padded) is right.
`gtcrn_full.onnx` (0.42 MB) is a one-off `torch.onnx.export` of the same DNS3 checkpoint with
a dynamic time axis — exact against the torch model to 1e-5, and 6× cheaper because it is one
call. The two graphs are *not* numerically identical (the streaming conversion is causal;
offline is not: 24 dB apart) and, interestingly, the offline graph hurts WER *more* (+3.6 vs
+2.6) while removing *more* noise (§2) — the same artifact-vs-noise trade-off in miniature.

## 4. Install weight and packaging

| candidate | new distributions | payload | licence | packaging notes |
|---|---|---|---|---|
| gtcrn / gtcrn-full | none — runs on the `onnxruntime` we ship, CPU EP | 0.42–0.54 MB ONNX | MIT | bundle as a data file next to Silero's or fetch via `huggingface-hub`; the whole-buffer export is ours to host |
| rnnoise | `pyrnnoise` 13 MB wheel — **but its Python shim does not import against the `av` 18 we ship** (`audiolab` → `from av.option import OptionType` fails), so it would mean vendoring the DLL/dylib (14.8 MB, unstripped) and our own ~40-line ctypes wrapper, plus a PyInstaller hook and 48 kHz resampling | 85 kB of weights inside a 14.8 MB DLL | BSD-3 / Apache-2.0 | dead on arrival: slowest, most damaging, and the binding is broken |
| specgate | none | none | n/a | ~60 lines of numpy already written in the prototype |

## 5. What this does *not* settle

- **Own voice.** The set is a SAPI voice with synthetic beds. Synthetic TTS is unnaturally
  clean and evenly voiced, which flatters the recognizers and gives the denoisers little
  real-mic noise (room, AGC hiss, plosives) to work on. Re-record with `noise_eval.py record`,
  re-mix, re-run `noise_bakeoff.py process && score && report` — about 90 min wall-clock on
  this box. If own-voice results show the mixes helping at 5–10 dB (not just at 0 dB) the
  spectral-gate-mix50 arm becomes worth shipping behind the toggle; if they match this table,
  the toggle has nothing worth guarding and #49 should ask whether the feature is only the STT
  knobs.
- **Real noise beds.** Fan/keyboard/babble/café here are synthesised. A real laptop-fan and
  a real open-plan recording could move the stationary-noise rows.
- **The 50/50 ratio** was the only mix tried; 30/70 or an SNR-gated switch (only denoise
  when a cheap noise estimate says < 5 dB) were not.
- **macOS timings** (§3).
