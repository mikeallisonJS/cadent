# ASR noise robustness & STT-side knobs (research, 2026-08-18)

Ticket: #45, on the #43 map ("Wayfinder: noise reduction"). Two facts a placement decision waits
on: does a separate denoise pass in front of Whisper-family / Parakeet-TDT help or hurt WER in
noise, and what noise-relevant knobs do the two engines we already ship expose — and which of
those is the cheap first lever.

Primary sources only: the Whisper paper, faster-whisper 1.2.1 source (the version in the venv),
the Parakeet-TDT-0.6b-v2 model card, onnx-asr 0.12.0 source, and first-party / peer-reviewed
evaluations of enhancement-before-ASR. Nothing here was measured on our own noisy set — that is
the bake-off's job, and this doc says what the bake-off should and should not bother measuring.

## Verdict

**(1) A separate off-the-shelf denoise pass in front of either engine is more likely to raise WER
than lower it.** Both models were trained on large, naturally noisy corpora and are measured by
their own authors as robust down to roughly 0–10 dB SNR (§1). Every recent evaluation that put a
stand-alone enhancer in front of Whisper or Parakeet found WER *went up* — in the one study that
tested Parakeet-TDT directly, in every one of 40 configurations (§2). The mechanism is known and
old: it is the enhancer's *artifacts*, not residual noise, that hurt the recognizer (§2.3). The
remaining case for a front-end is the very low SNR regime (below ~0 dB) or an *observation-addition*
mix (enhanced + a slice of the raw signal), not full suppression.

**(2) The knobs already there** — faster-whisper: `vad_filter`/`vad_parameters`,
`no_speech_threshold`, `log_prob_threshold`, `compression_ratio_threshold`, the `temperature`
fallback ladder, `condition_on_previous_text`, `initial_prompt`/`hotwords`. onnx-asr/Parakeet:
essentially none at decode time — a Silero VAD wrapper (`with_vad`) with the same parameter family,
and token log-probs via `with_timestamps()`. Full table in §3.

**Cheap first lever: faster-whisper's decoding knobs, in `stt.py`, zero packaging cost** — in this
order: `condition_on_previous_text=False`, a shorter `temperature` ladder (or `0.0`), and
`vad_parameters` tuned for a push-to-talk utterance rather than long-form audio (§4). None of them
make the acoustic model hear better; they stop the *decoder* from turning noise into invented text,
which is the failure a dictation user actually sees. Parakeet has no equivalent lever, and its
authors' own SNR table says it needs one less (§1.2).

## 1. Both models were trained on noise and are measured as robust to it

### 1.1 Whisper

Trained on 680,000 hours of weakly supervised web audio
([Radford et al. 2022, abstract](https://arxiv.org/abs/2212.04356)). §3.7 of the paper,
"Robustness to Additive Noise", added white noise and pub noise (Audio Degradation Toolbox) to
LibriSpeech test-clean and compared with 14 LibriSpeech-trained models:

> "There are many models that outperform our zero-shot performance under low noise (40 dB SNR)
> ... but all models quickly degrade as the noise becomes more intensive, performing worse than the
> Whisper model under additive pub noise of SNR below 10 dB. This showcases Whisper's robustness to
> noise, especially under more natural distribution shifts like the pub noise."
> — [arXiv:2212.04356, §3.7 / Fig. 5](https://arxiv.org/pdf/2212.04356)

Same paper, §4.5, is where every faster-whisper default in §3 below comes from: beam 5, temperature
fallback 0→1.0 in steps of 0.2 "when either the average log probability over the generated tokens is
lower than −1 or the generated text has a gzip compression rate higher than 2.4", previous-text
conditioning "when the applied temperature is below 0.5", and "combining the no-speech probability
threshold of 0.6 and the average log-probability threshold of −1 makes the voice activity detection
of Whisper more reliable." The authors call these "a workaround for the noisy predictions of the
model" — they were tuned for 30 s-window *long-form* transcription, not a 3–20 s push-to-talk
utterance.

Caveat for us: `distil-small.en` is a distilled student, and the paper's robustness curve is for
the full models. Distil-Whisper's noise-robustness claims are the vendor's; we have not verified
them, and the vocab-biasing doc already records that distil models behave differently from the
parents in other ways (`docs/research/vocab-biasing.md` §2).

### 1.2 Parakeet-TDT-0.6b-v2

The model card ([nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2))
says it was trained on ~120,000 hours — 10k human-transcribed (LibriSpeech, Fisher, VCTK, Common
Voice, ...) plus 110k pseudo-labelled hours from YouTube-Commons, YODAS and Librilight — assembled
into the Granary set, which it describes as combining "noise robust data from various sources". Its
own **Noise Robustness** table (MUSAN music+noise added, averaged over the OpenASR sets):

| Condition | Avg WER | Relative change |
|---|---|---|
| Clean | 6.05% | — |
| SNR 10 dB | 6.95% | −14.75% |
| SNR 5 dB | 8.23% | −35.97% |
| SNR 0 dB | 11.88% | −96.28% |
| SNR −5 dB | 20.26% | −234.66% |
| Telephony (μ-law 8 kHz) | 6.32% | −4.10% |

So the vendor's own number for a bad-but-not-hopeless room (0 dB, speech as loud as the noise) is
roughly a doubling of WER from a low base — still under 12%. There is no NeMo/onnx-asr decode-time
knob that would move this; TDT decoding is greedy over duration-predicting transducer steps and the
model card documents no noise option.

## 2. Denoising in front of a robust recognizer: what the evaluations found

### 2.1 Directly on Whisper and Parakeet (2025)

Chondhekar et al., "When De-noising Hurts: A Systematic Study of Speech Enhancement Effects on
Modern Medical ASR Systems", [arXiv:2512.17562](https://arxiv.org/abs/2512.17562) (Dec 2025).
MetricGAN-plus-voicebank (SpeechBrain) in front of Whisper large-v3, **Parakeet-TDT-1.1B**, Gemini
Flash 2.0 and Parrotlet; 500 recordings; background / short / Gaussian noise at three levels each.

> "Speech enhancement methods are commonly believed to improve the performance of automatic speech
> recognition (ASR) in noisy environments. However, the effectiveness of these techniques cannot be
> taken for granted in the case of modern large-scale ASR models trained on diverse, noisy data."

Result: WER rose in **all 40 configurations**, by 1.1 to 46.6 absolute points. Numbers from their
tables ([HTML](https://arxiv.org/html/2512.17562)):

| Model | Condition | Noisy | Enhanced |
|---|---|---|---|
| Parakeet-TDT-1.1B | background noise, 10 dB | 10.86% | 16.79% |
| Parakeet-TDT-1.1B | background noise, 50 dB | 6.28% | 7.35% |
| Parakeet-TDT-1.1B | short noise, 10 dB | 10.05% | 13.29% |
| Whisper large-v3 | background noise, 10 dB | 8.82% | 25.83% |
| Whisper large-v3 | short noise, 10 dB | 8.00% | 17.68% |

Their explanation is the one the ticket anticipated: the models "possess sufficient internal noise
robustness", and enhancement may "remove acoustic features critical for ASR" and introduce
"processing artifacts" such as spectral smearing.

### 2.2 Directly on Whisper, all sizes (2026)

Islam et al., "When Denoising Hinders: Revisiting Zero-Shot ASR with SAM-Audio and Whisper",
[arXiv:2603.04710](https://arxiv.org/abs/2603.04710) (Mar 2026). SAM-Audio separation in front of
Whisper tiny→large-v3 on MS-SNSD noise (traffic, babble, appliances, typing, vacuum). Signal
metrics improved (PSNR 32.3→36.0 dB) while recognition got worse in "every evaluated model-dataset
configuration": Whisper base WER 10.53% → 21.66%, CER 4.48% → 12.50%. Their stated mechanism:
Whisper "implicitly learn[ed] to exploit noise-correlated cues, channel artifacts, and real-world
acoustic variability" that denoising strips, creating a train/inference mismatch — plus the
enhancer's own "spectral smoothing, temporal inconsistencies, or phase irregularities".

### 2.3 Why: artifacts, not residual noise (the older, peer-reviewed line)

Iwamoto et al., "How Bad Are Artifacts?: Analyzing the Impact of Speech Enhancement Errors on ASR",
Interspeech 2022, [DOI 10.21437/Interspeech.2022-318](https://www.isca-archive.org/interspeech_2022/iwamoto22_interspeech.html).
Decomposes the enhancer's error into a *noise* component and an *artifact* component and scales
each independently; identifies "the artifact component as the main cause of performance
degradation", and shows that "adding a scaled version of the observed signal to the enhanced
output" — observation addition — recovers performance by raising the signal-to-artifact ratio.
Extended in TASLP 2024 ("Rethinking Processing Distortions",
[DOI 10.1109/TASLP.2024.3426924](https://dl.acm.org/doi/10.1109/TASLP.2024.3426924)) and still the
active remedy in 2026: Li et al., "Training-Free Intelligibility-Guided Observation Addition for
Noisy ASR", [arXiv:2602.20967](https://arxiv.org/abs/2602.20967) — SE front-ends "often introduce
artifacts that harm recognition", so mix noisy and enhanced audio with weights taken from the ASR
backend rather than feed it the enhanced signal alone.

A related 2026 listening study (de Oliveira, Peer, Gerkmann, "Too Good to Be True",
[arXiv:2605.12107](https://arxiv.org/abs/2605.12107)) makes the same point from the other side:
modern ASR models "with large-scale noisy training and embedded language models" are so robust that
their WER is "uninformative to an acoustics-focused evaluation of enhancement" — i.e. the enhancer
cleans things the recognizer had already stopped caring about.

### 2.4 What this does and does not license

- It does **not** say noise reduction is worthless for dictation. The evaluations are at 10 dB and
  above, where both models are already near their clean WER; nobody in this set measured a fan at
  −5 dB, and the Parakeet card shows the model itself falling off a cliff there.
- It does say: a bake-off that measures *only* "denoiser on vs off → WER" at moderate SNR will most
  likely find "off wins", for both engines, and that result would be consistent with the literature
  rather than a bug in the bake-off.
- If a front-end is built anyway, the evidence favours **mild suppression or observation addition**
  (raw + enhanced mixed) over full suppression, and a **default-off toggle** — which is already the
  standing preference on #43.
- Denoising may still be worth it for reasons WER does not capture: the level meter, stored
  history audio, and Whisper's tendency to hallucinate on non-speech (which is what `vad_filter`
  already addresses, §3.1).

## 3. The knobs we already have

### 3.1 faster-whisper 1.2.1 (`WhisperModel.transcribe`)

What `stt.py` passes today: `language="en"`, `hotwords=<vocab>`, `vad_filter=True`, `beam_size=5`.
Everything else is default. Signature defaults read off the installed 1.2.1
(`faster_whisper/transcribe.py`; README at
[SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/README.md)):

| Knob | Default | We pass | What it does in noise |
|---|---|---|---|
| `vad_filter` | `False` | **`True`** | Runs Silero VAD, drops non-speech, feeds only speech chunks to the model. Whisper's known hallucination-on-silence failure is exactly what this exists for. |
| `vad_parameters` | see below | default | `threshold` 0.5, `neg_threshold` = threshold−0.15, `min_speech_duration_ms` 0, `min_silence_duration_ms`, `speech_pad_ms` 400. **Note:** the README says the default "only removes silence longer than 2 seconds", and the `VadOptions` dataclass default is indeed 2000 ms — but `transcribe()` builds its own `VadOptions(max_speech_duration_s=chunk_length, min_silence_duration_ms=160)` when `vad_parameters is None` (transcribe.py ~L397). So we are running with a **160 ms** silence split, not 2 s. In a noisy room Silero's 0.5 threshold can both miss quiet speech and admit loud noise; this is the one knob that touches the *acoustics* rather than the decoder. |
| `no_speech_threshold` | 0.6 | default | A window is *skipped* only if `no_speech_prob > 0.6` **and** `avg_logprob < log_prob_threshold` (paper §4.5). Not "skip if probably silent" — the logprob guard means it rarely fires on real speech. |
| `log_prob_threshold` | −1.0 | default | Below this the decode is a *fallback trigger*: re-decode at the next temperature. Also the guard for `no_speech_threshold`. |
| `compression_ratio_threshold` | 2.4 | default | gzip ratio above this = "too repetitive" → fallback. This is the repetition-loop detector. |
| `temperature` | `[0, .2, .4, .6, .8, 1.0]` | default | The fallback ladder. In noise, low avg-logprob is *normal*, so the ladder fires more, and each rung is a sampled decode with a higher chance of inventing words. `temperature=0` disables fallback entirely; a short ladder (`[0, 0.2, 0.4]`) is the middle road. |
| `condition_on_previous_text` | `True` | default | Feeds the previous window's text as prompt. faster-whisper's own docstring: "disabling may make the text inconsistent across windows, but the model becomes less prone to getting stuck in a failure loop, such as repetition looping"; the README's distil example sets it `False`. Only matters when an utterance exceeds one 30 s window (`Recorder.max_seconds` = 120, so it can). |
| `prompt_reset_on_temperature` | 0.5 | default | Resets that prompt when a fallback went above 0.5. Only relevant with the above `True`. |
| `initial_prompt` | `None` | not passed | Free text prepended as previous-text. Shares the prompt half of the context with `hotwords` (both capped at `max_length // 2 − 1`, `get_prompt()` ~L1542); a "style" prompt would compete with the vocab budget ticket 04 already spends. Not a noise lever. |
| `hotwords` | `None` | **vocab** | Same slot; already in use for biasing (vocab-biasing doc). |
| `hallucination_silence_threshold` | `None` | not passed | Only active with `word_timestamps=True`; skips silences longer than N s where a hallucination is suspected. Costs word-timestamp decoding; overlaps with what VAD already does. |
| `suppress_blank`, `suppress_tokens` | `True`, `[-1]` | default | Token suppression at start / non-speech tokens. Already sane. |
| `beam_size` | 5 | **5** | Paper §4.5: beam 5 reduces repetition looping vs greedy. Keep. |
| `chunk_length` | 30 | default | Window length; also the VAD `max_speech_duration_s`. |

There is no denoising, gain, or spectral-subtraction option anywhere in the API; the only
acoustic-side control is the Silero VAD.

### 3.2 onnx-asr 0.12.0 / Parakeet-TDT

What `stt.py` passes today: `recognize(audio, sample_rate=16_000)` on
`load_model("nemo-parakeet-tdt-0.6b-v2", quantization="int8", providers=[...])`. Read off the
installed package ([istupakov/onnx-asr](https://github.com/istupakov/onnx-asr), usage guide at
[istupakov.github.io/onnx-asr/usage](https://istupakov.github.io/onnx-asr/usage/)):

| Surface | Options | Relevance |
|---|---|---|
| `recognize(waveform, *, sample_rate, channel, **RecognizeOptions)` | `RecognizeOptions` = `language`, `target_language`, `pnc` — all documented "only for Whisper and Canary models" | **Nothing** for NeMo/Parakeet. |
| `.with_vad(vad, **VadOptions)` after `load_vad("silero" or "pyannote")` | `batch_size`, `threshold` (0.5), `neg_threshold` (threshold−0.15), `min_speech_duration_ms`, `max_speech_duration_s`, `min_silence_duration_ms`, `speech_pad_ms` | Same Silero family as faster-whisper's `vad_filter`. The docs warn "You will most likely need to adjust VAD parameters to get the correct results." Segments audio before recognition; not a denoiser. |
| `.with_timestamps()` | returns tokens, timestamps, log-probs (`TimestampedResult`) | A confidence signal we could read (e.g. flag a low-mean-logprob dictation), not a knob that changes output. |
| `load_model(...)` | `quantization`, `sess_options`, `providers`, `provider_options`, `cpu_preprocessing`, `asr_config` (`max_tokens_per_step` etc.), `preprocessor_config` | Runtime plumbing. `max_tokens_per_step` (default 10) bounds TDT symbols per frame — a runaway-repetition guard, not a noise control. |

There is no `initial_prompt`, no `hotwords`, no temperature, no no-speech threshold: Parakeet's
answer to noise is entirely the acoustic model, and by its own SNR table (§1.2) that answer holds
to about 0 dB. Note also that we do **not** currently run any VAD in front of Parakeet — the whole
push-to-talk buffer goes in — where faster-whisper trims it. That asymmetry is worth one line in the
bake-off (does leading/trailing room noise cost Parakeet anything?), but the model card's telephony
and SNR figures suggest not much.

## 4. The cheap first lever

Ranked by cost-to-try (all are edits to one call site in `stt.py`, no new dependency, no model,
no packaging change, no platform seam):

1. **`condition_on_previous_text=False`** on the faster-whisper call. Removes the one documented
   noise-triggered failure mode that a dictation user actually notices — a repetition loop that
   fills the rest of a long utterance with the same phrase — at the cost of cross-window
   consistency we barely use (most utterances fit in one window). faster-whisper's own distil
   example ships it off. Zero latency cost.
2. **Shorten the `temperature` ladder** (`[0.0, 0.2, 0.4]`, or `0.0` to disable fallback). Noise
   depresses avg-logprob, which is precisely the fallback trigger; each extra rung is a sampled
   decode more likely to invent text. Trade: fewer retries on a genuinely hard clip. This is the
   knob to A/B first in the bake-off because it is the only one that changes *what gets typed*
   on a moderately noisy single-window utterance.
3. **`vad_parameters`** tuned for push-to-talk: today we inherit `min_silence_duration_ms=160`
   (not the README's 2 s — §3.1) and Silero `threshold=0.5`. Raising `threshold` (0.6–0.7) rejects
   more room noise as non-speech; raising `min_silence_duration_ms` (500–1000) stops the VAD from
   shredding a hesitant sentence into many tiny clips that each start cold. This is the only lever
   on the *acoustic* side and the only one Parakeet could share (`with_vad`).
4. Leave `no_speech_threshold` / `log_prob_threshold` alone. Their skip logic is AND-ed and rarely
   fires on speech; loosening them mostly changes what gets *dropped*, which is not the noise
   complaint. `initial_prompt` is not a noise lever and competes with the hotwords budget.

What none of these do: make the model hear a whispered word under a fan. If the bake-off's noisy
set is dominated by *misheard* words rather than *invented* or *dropped* ones, the decoder knobs
will show nothing, and the question becomes the very-low-SNR front-end (mild suppression /
observation addition, default-off) that §2.4 leaves open — measured against the raw baseline, per
engine, because the literature says the raw baseline will be hard to beat.

## 5. Answers in one breath

- **Denoise before Whisper/Parakeet?** Evidence says no by default: both trained on noisy data,
  both robust to ~0–10 dB by their authors' own tables; every recent evaluation of an off-the-shelf
  enhancer in front of them raised WER (Whisper large-v3 8.8→25.8% at 10 dB; Parakeet-TDT-1.1B
  10.9→16.8%), and the cause is enhancer artifacts, not residual noise. Only very low SNR or an
  observation-addition mix is still on the table.
- **Existing knobs:** faster-whisper has a full decoder-side kit (VAD, temperature ladder,
  logprob / compression / no-speech thresholds, previous-text conditioning, prompt); we set only
  `vad_filter=True` and `beam_size=5`. onnx-asr/Parakeet has a Silero VAD wrapper and log-probs,
  nothing else.
- **Cheap first lever:** faster-whisper decoding parameters in `stt.py` —
  `condition_on_previous_text=False`, a short temperature ladder, then VAD threshold / silence
  tuning — before anything is bought in packaging weight.
