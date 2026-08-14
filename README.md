# Cadent

[![PyPI](https://img.shields.io/pypi/v/cadent)](https://pypi.org/project/cadent/)
[![Python](https://img.shields.io/pypi/pyversions/cadent)](https://pypi.org/project/cadent/)

Fully-local push-to-talk AI dictation for Windows, with zero cloud. Hold **Ctrl+Win**, speak, release: clean text appears at your cursor in any app.

See **PRD.md** for the full product spec.

## How it works

```
hotkey (hold) ─▶ mic capture (16 kHz) ─▶ faster-whisper STT (vocab-biased)
    ─▶ vocabulary correction + snippet expansion
    ─▶ [cleanup on] local LLM cleanup (llama.cpp, hallucination-guarded)
    ─▶ SendInput / clipboard-paste into focused app ─▶ SQLite history
```

Everything runs on-device. The only network activity is the explicit first-run model download.

## Setup (Windows, Python 3.11+)

Cadent ships as a Windows installer — see [Releases](https://github.com/mikeallisonJS/cadent/releases). To run from source:

```powershell
git clone https://github.com/mikeallisonJS/cadent && cd cadent
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[cleanup,dev]"      # drop ",cleanup" to skip the LLM for now
python -m cadent
```

> The `cadent` name is registered on PyPI, but the published release is a
> placeholder — `pip install cadent` does **not** get you a working app yet.
> Use a source checkout until a real release is published.

First run downloads the faster-whisper model (default `small.en`) and creates config at `%APPDATA%\Cadent\config.json`.

**Cleanup:** turn on `Clean up transcripts before inserting them` in Settings ▸ Speech & cleanup and pick one of four models — Cadent downloads the one you pick, once. Off by default, and any cleanup failure silently inserts the raw transcript.

**GPU:** if you have an NVIDIA card, faster-whisper uses CUDA automatically when available. Cleanup ships on the Vulkan build of `llama-cpp-python`, so it uses any GPU — AMD, Intel or NVIDIA — with no extra install, and falls back to the processor when there isn't one. Pin it either way under Settings ▸ Speech & cleanup ▸ Show advanced.

## Project layout

| File | Responsibility |
|---|---|
| `cadent/app.py` | Orchestrator: tray, pipeline, thread marshaling |
| `cadent/hotkey.py` | Global push-to-talk hotkey (hold/toggle) |
| `cadent/audio.py` | Mic capture + level metering |
| `cadent/stt.py` | Pluggable STT engines (faster-whisper default, Parakeet on GPU) |
| `cadent/cleanup.py` | Local LLM transcript cleanup with divergence guard |
| `cadent/vocabulary.py` | Custom terms, sounds-like correction, snippets |
| `cadent/inject.py` | SendInput Unicode injection + clipboard fallback |
| `cadent/overlay.py` | Recording/processing overlay pill (click-through) |
| `cadent/history.py` / `history_ui.py` | SQLite history + search window |
| `cadent/config.py` | Settings under `%APPDATA%\Cadent` |

## Config quick reference (`%APPDATA%\Cadent\config.json`)

- `hotkey`: e.g. `"<ctrl>+<cmd>"` (Win key = `<cmd>`), `hotkey_mode`: `"hold"` or `"toggle"`
- `stt_engine`: `faster-whisper` (default, runs anywhere) | `parakeet` (needs a GPU).
  Derived from the model you pick in Settings and written alongside it
- `stt_model`: for `faster-whisper`, `tiny.en` | `base.en` | `distil-small.en` |
  `small.en` | `distil-medium.en` | `medium.en` | `distil-large-v3` | `large-v3`;
  for `parakeet`, `parakeet-tdt-0.6b-v2` | `parakeet-tdt-0.6b-v3`.
  Settings lists the six worth choosing between; the rest stay loadable
- `stt_device`: `auto` | `cpu`, plus `cuda` (both engines) and `directml` (`parakeet` only)
- `cleanup_mode`: is cleanup on? `cleanup_hotkey` taps it on and off
- `llm_model_path`: path to a `.gguf` file — one of the four Cadent can
  download, or your own
- `vocabulary.json`: `[{"term": "minibeast", "sounds_like": ["mini beast"]}]`
- `snippets.json`: `{"my email sig": "Best,\nMike"}`

## Development

```powershell
pytest          # unit tests (vocab, config, history — no audio/GPU needed)
ruff check .    # lint
```

Build order (matches PRD milestones): get the M0 loop working end-to-end first (`hotkey → record → transcribe → inject`), then overlay/tray/history, then cleanup.
