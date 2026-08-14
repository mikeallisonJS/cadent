# Standing up the Mac dev environment (task, 2026-08-10)

Ticket: #129, under the macOS map #124. The question is not "does it work" — it is **what
breaks, and where**, measured rather than predicted, so the decision tickets downstream argue
against a real machine instead of a guess.

Everything below was run on the target Mac. Where a claim is a *prediction* from an earlier
research ticket, it is marked as confirmed or corrected.

## Verdict

**The repo *constructs* on macOS today with one import guard's worth of work** — a narrower claim
than "runs", and the gap between the two is the substance of this document.
`python -m cadent` dies at exactly one line (`cadent/autostart.py:6`, `import winreg`), and
with that one module stubbed the whole application — config, history, recorder, injector, Qt,
tray, wizard — constructs without a further error. 776 of 785 tests pass with the same stub in
place; every failure is a platform artifact rather than logic.

The blockers are smaller than the map assumed. The *silent* failures are larger: the microphone
and the hotkey listener both come up reporting success and deliver nothing, because macOS answers
a missing TCC grant with silence rather than an error. Nothing is injected either — `inject.py`'s
non-Windows branch is a `print` that reports success. So the app can be *started* and cannot yet
dictate.

One thing is outright broken rather than merely absent: the `[tool.uv.sources]` pin on
`llama-cpp-python` serves **corrupt macOS wheels — every version, always the same file** (§2b).
Metal is still available, at the cost of a one-minute from-source build.

## 1. Machine facts

The numbers later tickets depend on:

| Fact | Value |
|---|---|
| Chip | Apple M1 Max (8 performance + 2 efficiency cores, 10 total) |
| RAM | 32 GB unified |
| macOS | 26.4.1 (build 25E253) |
| Python (venv) | CPython 3.13.13, from Homebrew `python@3.13` |
| System Python | 3.9.6 at `/usr/bin/python3` — below the project's `requires-python = ">=3.11"`, unusable |
| uv | 0.12.3 (aarch64-apple-darwin) |

`uv` was not installed. `brew install uv` failed on `/opt/homebrew` permissions; the Astral
standalone installer (`curl -LsSf https://astral.sh/uv/install.sh | sh`) put it at
`~/.local/bin/uv` without incident. **That directory is not on the default `PATH`** — every `uv`
invocation below was by absolute path.

## 2. What it takes to install

`uv sync --extra dev` fails outright:

```
error: Distribution `onnxruntime-directml==1.24.4` can't be installed because it doesn't have a
source distribution or wheel for the current platform
hint: You're on macOS (`macosx_26_0_arm64`), but `onnxruntime-directml` (v1.24.4) only has wheels
for the following platform: `win_amd64`
```

This is the `pyproject.toml` rework #126 predicted, confirmed with the exact error. All **three**
parts #126 named are load-bearing, and each was hit in turn:

1. **`onnxruntime-directml` needs a `sys_platform == 'win32'` marker** — the error above.
2. **The `[tool.uv] override-dependencies` line needs a win32 marker of its own.** It reads
   `"onnxruntime; sys_platform == 'cadent-never'"` — a deliberately never-true marker whose
   *purpose* is to delete faster-whisper's plain `onnxruntime` requirement, so that only the
   DirectML distribution provides the `onnxruntime` package. It should be doing that deletion on
   Windows only. Today it does it everywhere, including the platform that has nothing else to
   fall back on.
3. **The `[tool.uv.sources]` pin on `llama-cpp-python` needs one too** — and this is not a
   preference, it is broken. It has its own section: §2b.

Part 2 is the trap, because the failure it produces points somewhere else. Fix only part 1 and
the install *succeeds*, then raises `ModuleNotFoundError: No module named 'onnxruntime'` at import
time — from an environment whose lock file happily lists `onnx-asr`. The missing package is not
the one you marked.

The working local recipe, pending that rework, is in the Appendix; the two lines that matter are
an overrides file that drops `onnxruntime-directml`, then a separate `uv pip install onnxruntime`
to put back what part 2 wrongly deletes on this platform.

Everything else installed from mainstream wheels, first try, no build step: PySide6 6.11.1,
ctranslate2 4.8.1, onnxruntime 1.28.0, pynput 1.8.2, sounddevice 0.5.5, plus the pyobjc
frameworks pynput pulls in on darwin (core, cocoa, quartz, applicationservices, coretext).

### Runtime capabilities, as installed

- **onnxruntime 1.28.0** providers: `CoreMLExecutionProvider`, `AzureExecutionProvider`,
  `CPUExecutionProvider`. Confirms #126 — plain `onnxruntime` ships arm64 with CoreML included,
  no `onnxruntime-silicon`. (#125 separately concluded Parakeet must run on the **CPU** EP; the
  CoreML EP being *present* is not a reason to use it.)
- **ctranslate2 4.8.1**: `get_cuda_device_count() == 0`, supported CPU compute types
  `float32`, `int8`, `int8_float32`. CPU-only on arm64, as #126 said. Note the absence of
  `float16` — the runtime ladder cannot offer it here.
- **llama-cpp-python**: this one has its own section — see §2b. Short version: Metal works, the
  project's pinned index cannot deliver it, and the route that does costs a from-source build.

### 2b. llama-cpp-python: every macOS wheel on the pinned index is corrupt

The one dependency that does not have a clean answer, and the reason it does not is not the one
#126 anticipated.

**The pinned index serves macOS wheels, and they are broken.** `[tool.uv.sources]` pins
`llama-cpp-python` to the `llama-cpp-python-cpu` index. That index *does* publish a
`macosx_11_0_arm64` wheel — which will not extract:

```
× Failed to download `llama-cpp-python==0.3.32`
├─▶ Failed to extract archive: llama_cpp_python-0.3.32-py3-none-macosx_11_0_arm64.whl
╰─▶ ZIP file contains trailing contents after the end-of-central-directory record
```

Four checks, because a single tool's complaint about a ZIP is not evidence of much:

| Check | Result |
|---|---|
| `uv`, three times, incl. `--no-cache` | fails identically |
| Download complete? | yes — 17,274,817 bytes, exactly the server's `Content-Length` |
| `unzip -t` | `bad zipfile offset (local header sig)` from member #29 onward |
| CPython `zipfile.testzip()` | `Bad CRC-32 for file 'lib/libggml-base.0.15.3.dylib'` |
| `pip install` (more lenient than uv) | `zipfile.BadZipFile: Bad CRC-32 for 'lib/libggml-base.0.15.3.dylib'` |

So it is the artifact, not uv being strict, and not a truncated download.

**Bumping the pin does not help — it is every version, and always the same file.** Tested the
index's other macOS arm64 wheels:

| Version | `zipfile.testzip()` |
|---|---|
| 0.3.30 | BAD — `lib/libggml-base.0.dylib` |
| 0.3.32 | BAD — `lib/libggml-base.0.15.3.dylib` |
| 0.3.33 | BAD — `invalid block type` (won't even open) |
| 0.3.34 | BAD — `lib/libggml-base.0.16.0.dylib` |

Always `libggml-base`. This is a systematic upstream publish problem, not one bad upload.

**It is macOS-only.** The same index's `llama_cpp_python-0.3.34-py3-none-win_amd64.whl` passes
`testzip()` clean. Windows is unaffected and the pin is doing its job there — which is precisely
why the fix is a marker rather than a deletion.

**The working route is a from-source build, and Metal comes with it.** PyPI publishes **no macOS
wheel at all** for 0.3.34 — the only artifact is a 71 MB sdist. So `uv pip install
'llama-cpp-python>=0.3'` compiles llama.cpp: **~60 s wall, ~4 min 26 s CPU at 488% on the M1 Max**.
Metal is on by default in that build, and finds the hardware on import:

```
ggml_metal_device_init: GPU name:   MTL0 (Apple M1 Max)
ggml_metal_device_init: GPU family: MTLGPUFamilyApple7 (1007)
ggml_metal_device_init: has unified memory = true
ggml_metal_device_init: recommendedMaxWorkingSetSize = 26800.60 MB
```

`libggml-metal.dylib` ships alongside the CPU and BLAS backends and
`llama_supports_gpu_offload()` is `True`.

The only system prerequisite is the **Xcode Command Line Tools** (26.4.0.0 here) — `cmake` and
`ninja` are *not* on this machine's `PATH`, and the build works anyway because scikit-build-core
pulls them into its isolated build environment.

**This partly corrects #126**, in both directions. #126 pointed at "an official Metal wheel index";
there isn't a usable one — the CPU index's macOS artifacts are corrupt, and PyPI has no wheel to
prefer. But the conclusion it was reaching for holds: Metal is available, unconditionally, at the
cost of a one-minute compile. And 26.8 GB of addressable working set on a 32 GB unified-memory
machine makes the cleanup LLM's residency question a different conversation here than on a
discrete-VRAM Windows box.

Part 3 of the pyproject rework is therefore a bug fix, not a tidy-up: win32-mark the
`[tool.uv.sources]` pin and leave macOS on PyPI, accepting the build. Whether a one-minute compile
belongs in a personal-machine setup, or whether the `cleanup` extra should be optional on macOS,
is a call for the packaging ticket.

## 3. What breaks: import-time blockers

The ticket named three. Two are confirmed; the third — `winput.py` — is **wrong in an interesting
way**, and the way it is wrong is worth more than the other two put together.

### `cadent/autostart.py:6` — `import winreg` — confirmed, and it is the only real one

This single line is the entire reason `python -m cadent` does not start:

```
File "cadent/__main__.py", line 35, in main
  from .app import CadentApp, acquire_single_instance
File "cadent/app.py", line 21, in <module>
  from . import a11y, autostart, downloads, gpu_pack, hardware, icons, snippets, stt, vocabulary
File "cadent/autostart.py", line 6, in <module>
  import winreg
ModuleNotFoundError: No module named 'winreg'
```

Of the 32 modules in `cadent/`, **30 import cleanly on macOS**. The two that do not are
`autostart` and `app` — and `app` only because it imports `autostart`.

`autostart.py` is also the module with the least Windows-shaped *interface*: two functions,
`set_enabled(bool)` and `is_enabled()`, over what is conceptually "run at login". Everything
Windows about it is behind that pair — which is an observation for the seam ticket, not a
decision this ticket gets to make; nothing here surveyed `inject.py`, `hotkey.py`, or `a11y.py`
for comparison. The map's open "Autostart on macOS (LaunchAgent plist)" item lands here either
way.

### `cadent/winput.py` — `from ctypes import wintypes` — **the ticket's premise is wrong**

`ctypes.wintypes` **imports fine on macOS** under Python 3.13. `cadent.winput` is not an
import-time blocker and never appears in the failure list. In fairness to the module, its own
docstring says so — *"Import-safe on any platform (struct definitions only)"* — so the ticket's
premise was contradicted in the repo before this machine was ever switched on. What the docstring
does not say, and what needed the machine, is the next paragraph.

What it does instead is worse, and is why this correction matters. `wintypes` on a non-Windows
host silently resolves to the host's C types, and macOS is LP64 where Windows is LLP64:

| Type | Windows | This Mac |
|---|---|---|
| `WORD` | 2 | 2 |
| `DWORD` | 4 | **8** |
| `LONG` | 4 | **8** |

So the structs build, and they are the wrong size. `sizeof(INPUT)` is 56 bytes here against the
40 the Win32 ABI requires — which the suite catches:

```
tests/test_inject.py::test_input_struct_matches_win32_layout
  assert 56 == 40
```

That test is doing its job: it is the only thing standing between "imports fine" and a struct
that SendInput would reject. It should be marked Windows-only rather than deleted — the layout
guarantee it asserts is still load-bearing on the platform it was written for.

### `tests/test_autostart.py:7` — unguarded `import winreg` — confirmed

Two test modules fail *collection*, which aborts the whole run before anything executes:

- `tests/test_autostart.py:7` — `import winreg` at module scope.
- `tests/test_app_downloads.py:16` — `from cadent import app as app_mod`, which reaches
  `autostart` transitively. Not on the ticket's list; it falls out for free once `autostart` is
  guarded.

Twenty tests (7 + 13) sit behind those two imports. Section 4 runs them anyway, with the same
throwaway stub §5 uses, so their fate is measured rather than left as a hole.

## 4. The test suite

Two runs, because the honest number depends on what you stub.

**Excluding the two uncollectable modules** — what the repo gives you today, unmodified:

```
2 failed, 762 passed, 1 skipped in 132s
```

**With `winreg` stubbed** via a `sitecustomize.py` on `PYTHONPATH` (throwaway; nothing in the repo
changed), so the other 20 run too:

```
8 failed, 776 passed, 1 skipped, 4 errors in 133s
```

Nine distinct failures, in three groups, none of them a regression:

1. **`test_inject.py::test_input_struct_matches_win32_layout`** — the 56-vs-40 struct size in §3.
   Needs a Windows-only marker.
2. **`test_settings_ui.py::test_a_long_device_name_leaves_the_restart_hint_on_one_line`** — the
   hint wraps to 2 lines instead of 1. This is a **font metrics** failure, not a layout bug:
   the UI names `Segoe UI`, which does not exist here, so Qt substitutes and the text measures
   wider. See §6.
3. **Six of the seven `test_autostart.py` tests** — every one that actually reaches the stub and
   gets `OSError`, plus 4 teardown errors from the same cause. These are the tests that write to
   and read from a real `HKCU` key; there is nothing here for them to talk to. They are not
   portable and should not be made to pass off-Windows — they are the Windows half of whatever
   the seam ticket decides `autostart` becomes.

The good news is in the other column. **`test_app_downloads.py` passes 13 of 13** once the import
is unblocked, and `test_autostart.py::test_dev_registers_pythonw_module_entrypoint` and its
sibling exercise `run_command()`, which is pure path logic. So of the 20 tests the ticket
identified as blocked, **14 pass on macOS the moment the import guard exists** — they were never
Windows-specific, only import-shadowed by a module that is.

The skip is `tests/test_inject.py:151`, "needs the real Windows clipboard" — already correctly
guarded.

Qt tests need `QT_QPA_PLATFORM=offscreen`, and the offscreen plugin prints three harmless
complaints (`does not support propagateSizeHints()`, `raise()`, `setting window opacity`).

## 5. What happens past the blocker

With `winreg` stubbed in `sys.modules` (a throwaway probe, no repo change), the app goes
considerably further than "it imports":

```
PASS  import cadent.app (winreg stubbed)
PASS  acquire_single_instance() -> True
PASS  CadentApp() construction
```

Full construction: `ConfigStore`, `History` (SQLite), `Recorder`, `LevelMonitor`, `Injector`,
`Cleaner`, `Pipeline`, `QApplication`, tray, overlay, settings and wizard windows. No second
blocker hides behind the first.

`acquire_single_instance()` returns `True` because it short-circuits off-Windows — Cadent has
**no single-instance guard at all on macOS**. Two copies will run. That is the map's open
"single-instance mechanism" item, now with a concrete consequence.

### The silent failures

This is the part the map should carry forward, because neither of these announces itself.

**The microphone returns zeros.** A 1.5-second capture at 16 kHz:

```
1 input device(s): [(1, 'MacBook Pro Microphone', 1, 48000)]
24060 frames, peak=0.000000, all-zero=True
```

The stream opens, the callback fires at the right rate, the frame count is exactly right, and
every sample is zero. This confirms #126's TCC prediction **live on this machine**, and confirms
its conclusion that the recorder needs a zero-buffer heuristic: there is no exception to catch and
no error code to read. Without one, a user who has not granted the microphone gets a dictation
that records perfectly and transcribes to nothing.

**The hotkey listener does the same thing.** `pynput.keyboard.Listener` starts, reports
`running == True`, and receives no events:

```
This process is not trusted! Input event monitoring will not be possible until it is added to
accessibility clients.
pynput version: 1.8.2   backend: pynput.keyboard._darwin
listener.running after start: True
keys seen: 0
```

The warning goes to **stderr**, not to an exception — and Cadent logs to a file, so in a normal
run nobody sees it. `running` is `True` throughout. Any macOS onboarding has to *check* the
Accessibility grant rather than infer it from the listener coming up.

Two of #128's premises hold up, but note what kind of evidence each rests on — the distinction
this document is otherwise trying to keep. The resolver gives **pynput 1.8.2**, which is at or
above the version #128 requires for self-injected-event filtering; that the filtering *works* was
not exercised here, only that the version constraint is satisfiable. And the darwin backend does
reference `FlagsChanged` — the event the Ctrl+Cmd hold-and-release chord depends on — but that is
a **source read** of `pynput/keyboard/_darwin.py`, not a measurement. It could not be measured: the
listener that would have seen the event is the one receiving nothing without the TCC grant.

Both want re-checking once Accessibility is granted, which is the first thing the next ticket on
this machine should do.

**Injection already has a macOS path, and it is a stub.** `inject.py:103` — `if not WINDOWS`,
print `[inject] (non-Windows dev mode) would type: ...` and return `InjectionResult("inserted")`.
So an end-to-end dictation on this Mac would record, transcribe, and print. Note it reports
`"inserted"`: the dev stub lies to the pipeline about success, which is fine for a dev stub and
would be a real problem if it survived into the port.

## 6. Qt and fonts

`QSystemTrayIcon.isSystemTrayAvailable()` is `True` on the `cocoa` platform — the tray, which the
whole UI hangs off, is not in question.

Fonts are:

| Family | Present |
|---|---|
| Segoe UI | **no** |
| Consolas | **no** |
| SF Pro Text | **no** (the system font is not exposed under that family name) |
| Helvetica Neue | yes |
| Menlo | yes |

Qt says so out loud during the suite: `Populating font family aliases took 55 ms. Replace uses of
missing font family "Segoe UI"`. This is the cause of the §4 settings-UI failure, and it means the
per-platform surface work the map defers is not only about *copy* ("Start with Windows") — the
type stack needs a macOS answer too, and at least one existing layout assertion is already tight
enough to break on it.

## 7. What this leaves for later tickets

Settled here, measured:

- Machine facts (§1); the exact `pyproject.toml` failure and its **three**-part fix (§2); the real
  blocker list, with `winput.py` **corrected** off it (§3); a suite that is green modulo three
  understood platform artifacts (§4).
- #126's mic-TCC-yields-silence prediction: **confirmed live** (§5).
- Metal on llama-cpp-python: **available unconditionally**, but only via a ~60 s from-source
  build — PyPI has no macOS wheel, and every macOS wheel on the project's pinned index is corrupt
  (§2b). #126's open question, answered, with a bug attached.
- The 20 previously-unrunnable tests: **14 of them pass** the moment the import guard exists (§4).

Open, with the ground now under them:

- **Real M-series RTF for Parakeet and faster-whisper** (#125's open item) — not attempted; no
  model was downloaded. `float16` is unavailable in ctranslate2 on arm64, which constrains the
  ladder before any measurement is taken.
- **Whether pynput actually filters self-injected events, and whether `FlagsChanged` really
  carries the chord** (§5) — both currently rest on version numbers and source reads, because the
  listener sees nothing without the Accessibility grant. Grant it first, then re-run.
- **The zero-buffer heuristic** for the recorder (§5) — now a requirement, not a suggestion.
- **An Accessibility-grant check** distinct from "the listener started" (§5).
- **Single-instance on macOS** (§5) — currently absent, not merely different.
- **A macOS type stack** (§6) — and a decision on how UI tests assert layout across platforms.
- **What `autostart` becomes** — and where the seam goes. §3 has an observation, not a verdict.

Deliberately not done here, because the map is plan-only: **nothing in `cadent/`,
`pyproject.toml`, or `tests/` was changed.** Every fix this document names — the `winreg` guard,
the three markers, the Windows-only test markers — is small and obvious, and each one is a
decision about *where the seam goes* dressed up as a one-line edit. They belong to the seam
ticket.

## Appendix: the checklist

Ordered, and run start-to-finish on a clean shell. Steps 1–3 are the ones that bite: the system
Python is 3.9.6 and the project needs ≥3.11, `uv` lands somewhere that is not on `PATH`, and the
venv has to exist before anything is installed into it.

```sh
# 1. Toolchain — the system Python 3.9.6 cannot be used
xcode-select --install                              # needed to build llama-cpp-python (§2b)
brew install python@3.13
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs to ~/.local/bin, NOT on PATH
export PATH="$HOME/.local/bin:$PATH"

# 2. The repo
git clone https://github.com/mikeallisonJS/cadent.git
cd cadent

# 3. The venv, explicitly on 3.13
uv venv --python 3.13
source .venv/bin/activate

# 4. Install, working around the pyproject issues in §2
printf "onnxruntime-directml; sys_platform == 'cadent-never'\n" > /tmp/lf-mac-overrides.txt
uv pip install -e '.[dev]' --overrides /tmp/lf-mac-overrides.txt
uv pip install onnxruntime          # §2 part 2 deletes this on every platform
uv pip install 'llama-cpp-python>=0.3'   # from PyPI, NOT the pinned index (§2b).
                                         # Builds from source: ~60s wall on an M1 Max.

# 5. What breaks
python -m cadent                 # dies at autostart.py:6, ModuleNotFoundError: winreg

# 6. The suite as the repo stands
QT_QPA_PLATFORM=offscreen python -m pytest -q \
  --ignore=tests/test_app_downloads.py --ignore=tests/test_autostart.py

# 7. The suite including the 20 import-blocked tests, via a throwaway stub
mkdir -p /tmp/lfstub && cat > /tmp/lfstub/sitecustomize.py <<'PY'
import sys, types
m = types.ModuleType("winreg")
for n in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE", "KEY_SET_VALUE", "REG_SZ"):
    setattr(m, n, 0)
def _nope(*a, **k):
    raise OSError("stubbed winreg: no registry on macOS")
for n in ("CreateKeyEx", "OpenKey", "SetValueEx", "DeleteValue", "QueryValueEx"):
    setattr(m, n, _nope)
sys.modules["winreg"] = m
PY
QT_QPA_PLATFORM=offscreen PYTHONPATH=/tmp/lfstub python -m pytest -q
```

Steps 5–7 are the record; steps 1–4 are what a second machine needs.
