# Cadent

[![PyPI](https://img.shields.io/pypi/v/cadent)](https://pypi.org/project/cadent/)
[![Python](https://img.shields.io/pypi/pyversions/cadent)](https://pypi.org/project/cadent/)

Fully-local push-to-talk AI dictation for Windows and macOS, with zero cloud. Hold **Ctrl+Win** (**Ctrl+Cmd** on a Mac), speak, release: clean text appears at your cursor in any app.

See **PRD.md** for the full product spec.

## How it works

```
hotkey (hold) ─▶ mic capture (16 kHz) ─▶ faster-whisper STT (vocab-biased)
    ─▶ vocabulary correction + snippet expansion
    ─▶ [cleanup on] local LLM cleanup (llama.cpp, hallucination-guarded)
    ─▶ SendInput / clipboard-paste into focused app ─▶ SQLite history
```

Everything runs on-device. The only network activity is the explicit first-run model download.

## Setup (Python 3.11+)

Windows and macOS (Apple Silicon) are both supported and both run in CI.
**Linux is coming**: the platform seam it needs is in place, but there is no
adapter behind it yet — a Linux checkout falls through to the inert fallback,
where the hotkey hears nothing and injection prints instead of typing. Runnable
for development, not yet for dictation.

### Windows

Cadent ships as a Windows installer — see [Releases](https://github.com/mikeallisonJS/cadent/releases). To run from source:

```powershell
git clone https://github.com/mikeallisonJS/cadent && cd cadent
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[cleanup,dev]"      # drop ",cleanup" to skip the LLM for now
python -m cadent
```

### macOS

No installer and no `.app` bundle — a Mac runs it from a source checkout. The
Xcode Command Line Tools are the one system prerequisite: macOS has no
`llama-cpp-python` wheel, so cleanup builds from source (~60 s, Metal on by
default).

```sh
xcode-select --install               # only needed for the cleanup extra
git clone https://github.com/mikeallisonJS/cadent && cd cadent
uv sync --extra cleanup --extra dev  # drop "--extra cleanup" to skip the LLM for now
uv run python -m cadent
```

Grant Cadent **Accessibility** (System Settings ▸ Privacy & Security ▸
Accessibility) — it is the one permission the app cannot work without, since
both the hotkey tap and text injection go through it. The first-run wizard
deep-links there and re-checks on its own, and Settings shows a banner until
the grant lands.

> The `cadent` name is registered on PyPI, but the published release is a
> placeholder — `pip install cadent` does **not** get you a working app yet.
> Use a source checkout until a real release is published.

First run downloads the faster-whisper model (default `small.en`) and creates config at `%LOCALAPPDATA%\Cadent\config.json` (`~/Library/Application Support/Cadent/config.json` on macOS).

**Cleanup:** turn on `Clean up transcripts before inserting them` in Settings ▸ Speech & cleanup and pick one of four models — Cadent downloads the one you pick, once. Off by default, and any cleanup failure silently inserts the raw transcript.

**GPU:** on Windows, faster-whisper uses CUDA automatically when an NVIDIA card is available, and cleanup ships on the Vulkan build of `llama-cpp-python`, so it uses any GPU — AMD, Intel or NVIDIA — with no extra install. On macOS, speech runs on the CPU (ctranslate2 ships no Metal backend on arm64, and Parakeet's CoreML provider crashes) while cleanup runs on Metal. Either way the accelerator is proven by real work and drops to the processor when it isn't there; pin it under Settings ▸ Speech & cleanup ▸ Show advanced.

## Project layout

| File | Responsibility |
|---|---|
| `cadent/app.py` | Orchestrator: tray, pipeline, thread marshaling |
| `cadent/hotkey.py` | Global push-to-talk hotkey (hold/toggle) |
| `cadent/audio.py` | Mic capture + level metering |
| `cadent/stt.py` | Pluggable STT engines (faster-whisper default, Parakeet second) |
| `cadent/cleanup.py` | Local LLM transcript cleanup with divergence guard |
| `cadent/vocabulary.py` | Custom terms, sounds-like correction, snippets |
| `cadent/inject.py` | Unicode typing + clipboard-paste injection (order is a platform fact) |
| `cadent/platform/` | The per-OS seam: `win32.py`, `darwin.py`, and the `current()` factory |
| `cadent/overlay.py` | Recording/processing overlay pill (click-through) |
| `cadent/history.py` / `history_ui.py` | SQLite history + search window |
| `cadent/config.py` | Settings under the per-OS data dir |

## Config quick reference (`config.json` in the data dir above)

- `hotkey`: e.g. `"<ctrl>+<cmd>"` (`<cmd>` is the Win key on Windows, Cmd on macOS), `hotkey_mode`: `"hold"` or `"toggle"`
- `stt_engine`: `faster-whisper` (default, runs anywhere) | `parakeet` (needs a
  GPU on Windows; on macOS it runs on the CPU like everything else).
  Derived from the model you pick in Settings and written alongside it
- `stt_model`: for `faster-whisper`, `tiny.en` | `base.en` | `distil-small.en` |
  `small.en` | `distil-medium.en` | `medium.en` | `distil-large-v3` | `large-v3`;
  for `parakeet`, `parakeet-tdt-0.6b-v2` | `parakeet-tdt-0.6b-v3`.
  Settings lists the six worth choosing between; the rest stay loadable
- `stt_device`: `auto` | `cpu`, plus — on Windows — `cuda` (both engines) and
  `directml` (`parakeet` only); macOS has the two portable rungs only, and a
  GPU value carried over from a Windows config sanitizes back to `auto`
- `cleanup_mode`: is cleanup on? `cleanup_hotkey` taps it on and off
- `llm_model_path`: path to a `.gguf` file — one of the four Cadent can
  download, or your own
- `vocabulary.json`: `[{"term": "minibeast", "sounds_like": ["mini beast"]}]`
- `snippets.json`: `{"my email sig": "Best,\nMike"}`

## Development

```sh
pytest          # unit tests (vocab, config, history — no audio/GPU needed)
ruff check .    # lint
```

The suite runs on both OSes on every push and PR (`windows-latest` and
`macos-14`), which is what keeps an OS-specific import from escaping a platform
adapter. Adapter-internal tests skip themselves on the OS they don't belong to.

Build order (matches PRD milestones): get the M0 loop working end-to-end first (`hotkey → record → transcribe → inject`), then overlay/tray/history, then cleanup.
