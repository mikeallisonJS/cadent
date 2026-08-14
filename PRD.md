# Cadent — Product Requirements Document

**Version:** 0.1 (Draft) · **Date:** 2026-07-23 · **Owner:** Mike Allison
**One-liner:** A fully-local, privacy-first voice dictation app — hold a hotkey, speak, and polished text appears in whatever app you're using. Push-to-talk transcription with AI cleanup, and zero cloud dependency.

> **Superseded on one point.** This document specifies v1, which was Windows-only
> by design; every "Windows" below should be read as the v1 scope, not as today's.
> The macOS port shipped in M5 — see [`docs/specs/m5-macos-port-spec.md`](docs/specs/m5-macos-port-spec.md)
> and ADRs [0001](docs/adr/0001-macos-pastes-by-default.md)–[0005](docs/adr/0005-platform-seam.md)
> for what Windows and macOS each do now. Linux is planned but has no platform
> adapter yet.

---

## 1. Problem & Motivation

Hotkey-driven dictation dramatically speeds up writing, but the tools that do it well send audio (or transcripts) to the cloud, require subscriptions, and mostly target macOS. Power users on Windows want the same "speak anywhere, get clean text" experience with everything — audio, transcription, and AI cleanup — running on their own machine.

## 2. Goals

1. **Speak anywhere:** Hold (or toggle) a global hotkey in any Windows app, speak, release, and the transcribed text is inserted at the cursor.
2. **Text that reads like you typed it:** A local LLM pass removes filler words ("um", "uh", "like"), fixes punctuation/capitalization, and applies light formatting.
3. **Your vocabulary:** User-defined names, jargon, and text snippets are respected in output.
4. **Nothing leaves the machine:** No network calls in the dictation path. Model downloads are the only network activity, and they are explicit.
5. **Fast enough to trust:** End of speech → text inserted in under ~2s for a 10-second utterance on a mid-range machine (raw mode faster; cleanup mode may add ~1s).

### Non-goals (v1)

- macOS/Linux support (architecture should not preclude it, but no effort spent).
- Meeting transcription / long-form recording.
- Voice commands ("delete that", "new paragraph" as spoken commands).
- Real-time streaming word-by-word insertion (v1 inserts on release; streaming is a v2 candidate).
- Cloud LLM fallback.

## 3. Target User

- Primary: the developer himself and similar technical Windows power users — comfortable installing an app, tolerant of a settings file, allergic to subscriptions and cloud audio.
- Secondary: privacy-sensitive professionals (legal, medical, journalists) on Windows.

## 4. Core User Stories

1. As a user, I hold `Ctrl+Win` (configurable), speak a sentence into any focused text field (browser, IDE, Slack, Word), release, and clean text appears at my cursor within ~2 seconds.
2. As a user, I see a small unobtrusive overlay near the bottom of the screen while recording (mic level animation), so I know it's listening, and a brief "processing" state after release.
3. As a user, I can turn **cleanup** on or off — raw transcript versus LLM-polished — from the tray menu or a secondary hotkey.
4. As a user, I can add custom vocabulary ("Kubernetes", "Allison", "minibeast") so they're transcribed and spelled correctly.
5. As a user, I can define snippets — saying "my email sig" expands to a stored block of text.
6. As a user, I can open a History window, search past dictations, and copy any entry (with both raw and cleaned versions kept).
7. As a user, I can pick my microphone, hotkey, STT model size, and LLM on/off in Settings.
8. As a user, I can trust that the app made zero network requests while dictating (verifiable; a "fully local" indicator).

## 5. Functional Requirements

### 5.1 Capture & Hotkey
- Global push-to-talk hotkey (default `Ctrl+Win`), registered system-wide; hold-to-talk and toggle modes.
- Audio captured at 16 kHz mono from the selected input device; starts <100 ms after keydown.
- Max utterance length guard (default 120 s) with graceful cutoff.

### 5.2 Transcription (STT)
- Engine: **faster-whisper** (CTranslate2) as default; model size configurable (`base.en` → `large-v3-turbo`). GPU (CUDA) used when available, CPU int8 otherwise.
- Pluggable engine interface so **NVIDIA Parakeet TDT** (ONNX) can be added later for faster CPU inference.
- Custom vocabulary is fed to the engine via initial-prompt/hotword biasing, plus post-pass fuzzy correction.

### 5.3 AI Cleanup
- Local LLM via **llama.cpp** (llama-cpp-python), default model: a ~3–4B instruct model in GGUF (e.g. Qwen3-4B-Instruct or Llama-3.2-3B-Instruct), quantized Q4.
- Cleanup prompt: remove fillers/false starts, fix punctuation/casing, preserve meaning and wording style, never add content. Per-app tone presets (v1.1: e.g. "Slack casual" vs "email professional").
- If the LLM is disabled or fails, fall back to raw transcript — dictation must never be blocked by cleanup.

### 5.4 Text Insertion
- Primary: Win32 `SendInput` Unicode synthesis into the focused window.
- Fallback: clipboard-paste injection (save clipboard → set text → `Ctrl+V` → restore clipboard) for apps that reject synthetic input.
- Per-app override list (some apps need the fallback by default).

### 5.5 Vocabulary & Snippets
- `vocabulary.json`: list of terms + optional "sounds like" variants; applied to STT biasing and post-correction.
- `snippets.json`: trigger phrase → replacement text; matched against the final transcript.

### 5.6 History
- SQLite store: timestamp, raw text, cleaned text, duration, app name, mode. Search + copy UI; retention setting (default: keep forever, user-purgeable). Stored under `%APPDATA%/Cadent`.

### 5.7 App Shell
- System tray icon: mode toggle, settings, history, pause, quit. Runs at login (optional). Overlay: frameless, always-on-top, click-through pill showing recording/processing state.

## 6. Non-functional Requirements

- **Latency targets** (10 s utterance, mid-range CPU, `small.en`): raw ≤2 s; with cleanup ≤3.5 s. GPU roughly halves this.
- **Privacy:** no telemetry; no network in dictation path; models downloaded explicitly on first run with clear disclosure.
- **Footprint:** idle RAM ≤1.5 GB with models loaded (models are lazy-loaded / unload-after-idle option).
- **Reliability:** hotkey works across elevated and non-elevated windows where OS permits; crash in cleanup or injection never loses the transcript (always in history + clipboard as last resort).

## 7. Success Metrics (personal-product scale)

- Daily use survives week 2 (the real test).
- ≥95% of dictations inserted without manual retry.
- Word error rate on personal vocabulary items near zero after adding them.
- End-to-end latency targets met on the primary machine.

## 8. Milestones

| Milestone | Scope |
|---|---|
| **M0 – Spike (weekend)** | Hotkey → record → faster-whisper → SendInput. No UI. Prove the loop + latency. |
| **M1 – MVP** | Overlay, tray, config file, clipboard fallback, history logging, raw mode solid. |
| **M2 – Cleanup** | llama.cpp cleanup, its toggle, vocabulary biasing + post-correction, snippets. |
| **M3 – Polish** | Settings UI, history search UI, per-app overrides, installer (PyInstaller + Inno Setup), autostart. |
| **v2 candidates** | Streaming insertion, Parakeet engine, voice commands, tone presets per app, macOS/Linux. |

## 9. Key Risks & Mitigations

1. **Synthetic input blocked by some apps (games, admin windows, some Electron apps)** → clipboard fallback + per-app overrides; document known offenders.
2. **Cleanup LLM rewrites meaning or hallucinates** → conservative prompt, low temperature, diff-guard (reject cleanup if too divergent from raw), raw always preserved in history.
3. **Latency disappoints on CPU-only machines** → model-size auto-suggestion on first run; Parakeet ONNX engine as the CPU escape hatch.
4. **Python packaging pain (PyInstaller + CUDA + Qt)** → keep native deps minimal, pin versions, CI build early (M1, not M3).
5. **Hotkey conflicts** → configurable, conflict detection at registration.

## 10. Tech Stack (decision)

**Python 3.11+** application. Rationale: the entire local-AI ecosystem (faster-whisper, llama-cpp-python, sounddevice, ONNX) is Python-native — no FFI layer to maintain; it is also the stack AI coding agents build most reliably, which matches the "AI-built, efficiency-first" development process. The overlay/tray/settings UI uses **PySide6** (Qt), which handles frameless always-on-top click-through overlays well. Packaging via **PyInstaller** + Inno Setup.

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| STT | faster-whisper (CTranslate2), pluggable engine API |
| Cleanup LLM | llama-cpp-python + GGUF 3–4B instruct |
| Audio capture | sounddevice (WASAPI) |
| Hotkeys | Win32 RegisterHotKey / low-level keyboard hook (pynput) |
| Injection | pywin32 SendInput + clipboard fallback |
| UI (overlay/tray/settings/history) | PySide6 |
| Storage | SQLite + JSON config |
| Packaging | PyInstaller + Inno Setup |

Considered and rejected for v1: **Tauri (Rust+TS)** — nicer distributable, but every AI component crosses an FFI boundary and iteration slows; **C#/WPF** — great Windows integration but weaker local-AI library story. The module boundaries (engine interfaces) keep a future port possible.
