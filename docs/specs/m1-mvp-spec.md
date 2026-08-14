# Spec: Cadent M1 MVP — solid raw-mode dictation

> Historical snapshot of the M1 spec as implemented (M1 complete 2026-07-28; effort map: #9). Live specs and issues are GitHub Issues — see `docs/agents/issue-tracker.md`.

Status: ready-for-agent
Source map: #9 · Source PRD: [../../PRD.md](../../PRD.md)

## Problem Statement

Windows power users who want dictation have to send their voice to the cloud, pay subscriptions, and accept tools built primarily for macOS. There is no trustworthy way on Windows to hold a key, speak into any focused app, and get the exact transcript inserted at the cursor — with the audio never leaving the machine. Even for the app's own developer, nothing dictation-shaped can be used daily until the core loop (hotkey → record → transcribe → insert) is reliable enough to survive week two of real use.

## Solution

Cadent M1 delivers the raw-mode dictation loop as a daily-usable Windows tray app. The user holds `Ctrl+Win` (or toggles it), speaks, releases, and the exact transcript appears at their cursor in under ~2 seconds for a 10-second utterance. A small always-on-top pill shows recording and processing states. Everything runs locally: faster-whisper on CPU, no network in the dictation path. Every dictation is logged to a local SQLite history, and a transcript is never lost — even when insertion into the focused app fails, it survives in history and on the clipboard. Apps that reject synthetic input are handled by a per-app override list driving a clipboard-paste fallback. AI cleanup (Flow mode) is deliberately absent; the raw loop must be solid first.

## User Stories

1. As a Windows power user, I want to hold `Ctrl+Win`, speak, and release in any focused text field, so that the transcript appears at my cursor without touching the mouse.
2. As a user, I want recording to start within 100 ms of pressing the hotkey, so that the beginning of my sentence is never clipped.
3. As a user, I want a transcript of a 10-second utterance inserted within ~2 seconds of release, so that dictation feels faster than typing.
4. As a user, I want a toggle mode (press to start, press again to stop) as an alternative to hold-to-talk, so that long dictations don't require holding keys down.
5. As a user, I want accidental sub-200 ms taps of the hotkey to be discarded, so that brushing the chord doesn't insert stray text.
6. As a user, I want pressing any other key mid-hold (e.g. `Ctrl+Win+Arrow`) to cancel the dictation, so that OS shortcuts still work and never produce accidental insertions.
7. As a user, I want the Start menu to never pop open when I release the Win key after dictating, so that dictation doesn't disrupt my workflow.
8. As a user, I want a small unobtrusive pill near the bottom of my screen with a mic-level animation while recording, so that I know the app is listening.
9. As a user, I want the pill to show a distinct processing state after release, so that I know the transcript is on its way.
10. As a user, I want the pill to show a brief error state when something fails, so that I'm not left wondering where my text went.
11. As a user, I want the pill to be click-through and never steal focus, so that my target app keeps the cursor.
12. As a user, I want the exact transcript inserted — no cleanup, no rewording — so that what I said is what I get (raw mode).
13. As a user, I want Unicode text (accents, emoji, non-Latin scripts) to insert correctly, so that dictated names and symbols aren't mangled.
14. As a user, I want apps that reject synthetic keystrokes to receive the text via clipboard-paste instead, so that dictation works in terminals and stubborn apps.
15. As a user, I want my clipboard contents restored after a clipboard-paste insertion, so that dictation doesn't destroy what I had copied.
16. As a user, I want dictated text kept out of the Windows clipboard history (Win+V) during fallback, so that transient dictations don't pile up in cloud-synced history.
17. As a user, I want a per-app override list in my config, so that I can force clipboard mode (or a custom paste chord) for specific programs.
18. As a user, I want to be notified instead of silently failing when the focused window is elevated (admin), so that I understand why insertion didn't happen.
19. As a user, I want every dictation stored with timestamp, text, duration, and target app in a local SQLite database, so that no transcript is ever lost.
20. As a user, I want the history row written before insertion is attempted, so that even a crash during injection can't lose my words.
21. As a user, I want a failed insertion to leave the transcript on my clipboard as a last resort, so that I can paste it manually.
22. As a user, I want a History window listing recent dictations with copy buttons, so that I can retrieve any past transcript.
23. As a user, I want to purge my history, so that I control what's retained on my machine.
24. As a user, I want a system tray icon with pause/resume, history, and quit, so that the app is controllable without a main window.
25. As a user, I want pause to fully disarm the hotkey and release the microphone, so that I can guarantee the app isn't listening.
26. As a user, I want only one Cadent instance to run at a time, so that hotkeys and audio devices don't conflict.
27. As a user, I want to configure my hotkey, hotkey mode, microphone, and STT model size in a config file, so that I can tune the app without a settings UI.
28. As a user, I want an invalid or missing config to fall back to sane defaults without crashing, so that a typo can't brick the app.
29. As a user, I want the STT model preloaded in the background at startup, so that my first dictation isn't slow.
30. As a user, I want the STT model downloaded only once, explicitly, with clear disclosure on first run, so that I know exactly when the app touches the network.
31. As a user, I want zero network calls during dictation itself, so that I can trust the privacy claim.
32. As a user, I want utterances capped at a configurable maximum (default 120 s) with a graceful cutoff — the captured audio is still transcribed — so that a stuck hotkey can't record forever.
33. As a user, I want the app to keep working (with degraded transcription quality at most) when my configured microphone disappears, by falling back to the system default device, so that unplugging a headset doesn't kill dictation.
34. As a privacy-sensitive professional, I want all state (config, history, models) under `%APPDATA%/Cadent`, so that I know where my data lives and can delete it.
35. As the developer, I want dictation events logged locally at a debug level, so that failures during daily use are diagnosable.

## Implementation Decisions

Decisions locked by wayfinder research (full citations in the map's research notes):

- **Hotkey capture**: a low-level keyboard hook (`WH_KEYBOARD_LL` via pynput with `win32_event_filter`), because `RegisterHotKey` cannot register a modifiers-only chord and provides no release edge. The hook callback only sets flags/posts to a queue (callbacks exceeding 1000 ms get the hook silently removed). Chord detection is a state machine over left/right Ctrl and Win VKs: idempotent on auto-repeat, chord-down when both groups are down, chord-up on first keyup, cancel on any non-chord keypress mid-hold, and ignore events flagged `LLKHF_INJECTED` (our own synthetic input). Chord events pass through unswallowed; a dummy VK `0xFF` down/up injected on chord release suppresses the Start menu (MenuMaskKey technique). Elevated windows are a documented v1 limitation.
- **Hold vs toggle**: one state machine. Hold: start audio immediately on chord-down, discard the utterance if released within ~200 ms. Toggle: chord-down flip-flop with full-release re-arm, bounded by the max-utterance guard.
- **Injection**: default is `SendInput` with `KEYEVENTF_UNICODE`, the whole utterance batched in one call (two INPUT structs per UTF-16 code unit; surrogate pairs work as consecutive units). Before injecting, wait until the hotkey modifiers are physically released (`GetAsyncKeyState`) — a held Ctrl corrupts Unicode injection. Pre-flight the foreground window's integrity level: elevated targets get notify-only, since UIPI makes blocked injection indistinguishable from success and blocks synthetic Ctrl+V equally.
- **Clipboard fallback**: triggered by per-app override match or `SendInput` reporting fewer events sent than submitted — never by guesswork. Mechanics: `OpenClipboard` retry loop, set `CF_UNICODETEXT` plus the `ExcludeClipboardContentFromMonitorProcessing` format, send the paste chord, wait a per-app settle delay (~150 ms default), restore only text formats and only if `GetClipboardSequenceNumber` is unchanged. On total failure: leave the transcript on the clipboard without the exclusion format and surface an error.
- **Per-app override entries express**: process-name match, strategy (`sendinput` | `clipboard` | `clipboard-no-restore` | `notify-only`), paste chord (terminals want `Ctrl+Shift+V`), chunking size/delay, settle delay, restore-clipboard flag. Shipped defaults: terminals → clipboard with `Ctrl+Shift+V`; known raw-input apps → notify-only.

Decisions made in this spec (superseding the naive scaffold where they conflict):

- **Transcript-safety ordering**: history write happens before the injection attempt, and the row records the insertion outcome (inserted / fallback / failed). The existing history schema gains an outcome column; `cleaned_text` stays as a nullable column reserved for M2.
- **Pipeline orchestration**: the on-release pipeline is its own module boundary — record → transcribe → persist → inject → report — depending on the STT engine protocol, an injector interface, and the history store, all constructor-injected. The existing `SttEngine` Protocol shape extends to the injector so fakes slot in.
- **STT**: faster-whisper on CPU int8 (primary machine has no NVIDIA GPU); `small.en` remains the placeholder default until the benchmark ticket picks the final default; model size is config. First run downloads the model explicitly with a visible disclosure (tray notification naming the size and source); dictation is disabled until the model is present.
- **Config**: JSON at `%APPDATA%/Cadent/config.json` (existing dataclass approach), extended with hotkey mode, per-app override list (replacing the bare `clipboard_fallback_apps` string list), and paused-state persistence. Unknown keys ignored, missing keys defaulted; changes apply on restart (no hot-reload in M1).
- **Tray**: pause/resume (disarms hook and closes the audio stream), History…, Quit. The Flow-mode toggle from the scaffold is removed until M2. Single-instance enforced via a named mutex.
- **Overlay**: existing frameless click-through pill with recording (RMS level bar) and processing states, plus a new error state (brief red flash with a short message) that auto-hides. Positioned bottom-center of the active screen.
- **Failure surfacing**: overlay error state for pipeline failures; Windows toast (tray message) for notify-only insertions and clipboard-last-resort events, containing no transcript text beyond a preview.
- **No network in the dictation path**: the pipeline makes no network calls; model download is the only network activity and happens outside dictation.

## Testing Decisions

- Good tests exercise external behavior at the seams — what goes in and what comes out — never implementation details (no asserting on internal call order, private state, or Win32 API invocation shapes).
- **Primary seam — pipeline orchestrator**: constructor-injected fake STT engine, fake injector, fake recorder, real (temp-file) history store. Tests cover: raw transcript flows to injector untouched; history row written before injection and updated with outcome; injector failure still leaves the transcript in history and reports the clipboard last-resort; empty audio produces no insertion and no history row; per-app override selects the right strategy.
- **Hotkey chord state machine**: driven as pure logic with synthetic key events (no OS hook): chord-down/up edges, auto-repeat idempotence, sub-200 ms discard, non-chord-key cancellation, toggle flip-flop and re-arm, injected-event filtering.
- **Existing pure-Python seams stay**: config load/save/defaults (extended for new fields), history schema/search/purge (extended for outcome column), following the prior art in `tests/test_config.py` and `tests/test_history.py` (pytest, temp dirs, no mocking frameworks).
- **Win32 edges (SendInput, clipboard, the live hook, overlay rendering) are not unit-tested** — they are validated by the manual hotkey + injection spike ticket on the primary machine.

## Out of Scope

- Flow mode / LLM cleanup, vocabulary biasing, snippets (M2) — the `cleanup.py` / `vocabulary.py` scaffolds stay untouched or dormant.
- Settings UI, history search UI polish, installer, autostart (M3).
- Packaging / CI build (explicitly ruled out of this effort by the owner).
- Streaming word-by-word insertion, Parakeet engine, voice commands, tone presets, macOS/Linux (v2).
- Injection verification via UI Automation read-back (possible later opt-in, not v1).
- Elevated-window injection (documented limitation; signed `uiAccess` binary is an M3 investigation).

## Further Notes

- Open wayfinder tickets that refine (but don't block) this spec: the STT CPU benchmark finalizes the default model size; the overlay prototype finalizes visuals; the config/history/tray/failure-UX grilling tickets may adjust details. Implementation can start from this spec's defaults; resolved tickets amend it.
- Latency budget to verify during implementation: keydown→capture <100 ms; release→inserted ≤2 s for a 10 s utterance on CPU int8. If `small.en` misses the budget, the benchmark ticket's numbers pick the fallback default.
- The scaffold's naive `hotkey.py` and `inject.py` do not implement the researched decisions above; they are starting points to be reworked, not preserved behavior.
