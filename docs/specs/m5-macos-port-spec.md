# Spec: Cadent M5 — macOS port (Apple Silicon)

Status: ready-for-agent
Source map: [#124](https://github.com/mikeallisonJS/cadent/issues/124) · ADRs: [`docs/adr/0001`](../adr/0001-macos-pastes-by-default.md)–[`0005`](../adr/0005-platform-seam.md) · Bench: [`docs/research/macos-bench-m1max.md`](../research/macos-bench-m1max.md) · Dev-env survey: [`docs/research/macos-dev-environment.md`](../research/macos-dev-environment.md)

> Every section is the settlement of a closed wayfinder ticket on map #124. The ticket and its ADR are the reasoning; this spec is the instruction. Section headings carry their sources. The four research findings docs not in this repo (Parakeet runtime #125, dependency matrix #126, text injection #127, hotkey capture #128) are summarized in their tickets' resolution comments.

---

## Scope

Cadent runs fully on an Apple Silicon Mac: hotkey hold-to-dictate, paste-based injection, Parakeet/Whisper speech on CPU, Metal-accelerated cleanup, settings/wizard/tray adapted per platform — from source in a venv on personal machines. **Not in scope**: Linux, codesigning/notarization/installers, a frozen `.app` build (revisit only if venv-run proves annoying). Target hardware truth: M1 Max / 32 GB / macOS 26.4.1 / CPython 3.13.13; all numbers below were measured there.

Prerequisite already landed: PR [#136](https://github.com/mikeallisonJS/cadent/pull/136) makes `uv sync` resolve on macOS (three win32 markers; plain `onnxruntime>=1.28` off-Windows). macOS cleanup builds `llama-cpp-python` from source (~60 s, needs Xcode CLT) because every macOS wheel on the pinned cpu-index is corrupt.

---

# 1. The platform seam (#133 · ADR 0005)

## 1.1 Package shape

A new `cadent/platform/` package:

- `base.py` — one `Protocol` per seam plus a frozen `Capabilities` dataclass (§1.3).
- `win32.py`, `darwin.py` — flat, one module per OS; split into subpackages only if one bloats.
- `__init__.py` — a single `current()` factory selecting on `sys.platform`, the only place that decision is made.

**Move-and-repoint, no shims.** Existing per-OS code moves into `win32.py` and callers re-point; legacy import paths (`cadent.winput`, the top of `autostart.py`, the mutex in `app.py`) do not survive as re-exports. Wiring is dependency injection at exactly one constructor: `CadentApp.__init__` grows an optional `platform=` parameter defaulting to `current()`.

## 1.2 The eight seams

Policy stays portable; platforms supply primitives and facts.

| Seam | Contract (win32 today → darwin fill) |
| --- | --- |
| **KeyboardOutput** | `winput`'s send-unicode / chord / mask primitives → CGEvent (unicode typing in 20-UTF-16-unit chunks, chord posting for Cmd-V) |
| **Clipboard** | `inject.py`'s private clipboard helpers → NSPasteboard, with `changeCount` as the `GetClipboardSequenceNumber` analogue |
| **FocusedApp** | app identity (§5), foreground-window rect (absorbing `overlay.py`'s duplicate probe), and the blocked/secure-input preflights (§2) |
| **HotkeyTap** | pynput listener wiring + injected-event suppression; `chord.py` stays pure (§3) |
| **HardwareProbe** | cuda/dx12/processor/driver probes; `suggest_model()` stays portable (§4.3) |
| **Autostart** | Run key → LaunchAgent plist (§6.1) |
| **SingleInstance** | named mutex (inlined at `app.py`) → `fcntl.flock` (§6.2) |
| **DesktopEnv** | open-in-shell, text scale, high contrast, animations (§6.3) |

The injection ladder (rung order, fall-through, auto-learn) lives once in `inject.py`; the per-OS default rung order is data: `("paste", "type")` on darwin. The per-OS keycode table is plain data in the OS module, carried by `Capabilities`; `chord.parse_combo` takes the table as a parameter defaulting to the current platform's, so either OS's chord logic tests anywhere.

## 1.3 The Capabilities table — full field enumeration

`Capabilities` is a frozen dataclass of platform *facts*. UI, settings, and wizard code read **only** this table, never the behavioral adapters. The accumulated fields and their per-platform values:

| Field | win32 | darwin | Source |
| --- | --- | --- | --- |
| `keycode_table` | VK-code dict (today in `chord.py`) | Carbon/CGEvent keycode dict (new) | #131 |
| `default_injection_strategy` | `"type"` | `"paste"` | ADR 0001 |
| `paste_chord` | Ctrl+V | Cmd+V | #130 |
| `default_overrides` / reasons | shipped seed rules + `DEFAULT_OVERRIDE_REASONS` | empty dict (Restore-defaults naturally inert) | ADR 0001/0004 |
| `auto_learn_overrides` | `True` | `False` (paste has no detectable failure) | ADR 0001 |
| `stt_runtimes` | per-engine, from `config.STT_RUNTIMES` today | `("auto", "cpu")` for both engines | ADR 0003 |
| `gpu_only_engines` | `{"parakeet"}` | empty — no disabled model rows, no "Needs a graphics card" | ADR 0003 |
| `show_runtime_combo` | `True` | `False` (one choice offered twice) | #132 |
| `gpu_pack_available` | `True` (wizard page + tray items) | `False` (Metal ships in the build; nothing to download) | ADR 0003 |
| `permission_preflight` | none | Accessibility: wizard step + Settings banner, checked via `AXIsProcessTrusted` | ADR 0002 |
| `autostart_label` | `"Start with Windows"` | `"Start at login"` | #139 |
| `app_identity_placeholder` | `app.exe` | `com.example.app` (bundle id) | ADR 0004 |
| `app_picker` | free-text field | running-apps picker + free text (§5.2) | #135 |

(Exact field names are the implementer's to bikeshed; the *set* is fixed. If a surface needs a platform fact not listed here, it goes in this table, not in an `if sys.platform` branch.)

## 1.4 Import rule and CI

**OS-specific imports (`winreg`, `ctypes.windll`, pyobjc/`AppKit`/`Quartz`) may appear only inside per-OS platform modules, which only the factory imports.** Enforced by an import-walk test that imports every non-adapter module and asserts success — run on both OSes by a **new CI test workflow** (`windows-latest` + `macos-14`), which today does not exist at all.

Testing strategy: app-logic tests use fakes at the seams; adapter internals are `pytest.mark.skipif`-platform-gated and run on the real machines. Do not deep-mock pyobjc/ctypes — that mostly tests the mocks.

Known survey facts to honor while moving code (#129): `winput.py` is *not* an import blocker off-Windows but silently builds LP64-sized structs (`sizeof(INPUT)` 56 vs 40) — moving it behind the seam removes the trap; `autostart.py`'s top-level `winreg` is the one real import blocker; 14 of the 20 import-blocked tests were never Windows-specific and should pass everywhere once the seam lands.

---

# 2. Text injection on darwin (#130 · ADR 0001)

**macOS pastes by default; Windows types by default.** The darwin ladder is `("paste", "type")` with *no automatic fall-through to type* — Cmd-V is paste essentially everywhere (terminals included), and CGEvent unicode typing truncates at 20 UTF-16 units per event so it's a paced multi-event sequence, never atomic. "Type it" stays selectable as an explicit per-app override.

- **Paste rung**: write to NSPasteboard, post Cmd-V via KeyboardOutput, restore the prior pasteboard contents guarded by `changeCount` (only restore if nobody else wrote in between) — the same shape as the Windows clipboard rung.
- **Type rung**: CGEvent unicode typing, chunked at 20 UTF-16 units per event.
- **Preflight gates** (replacing the Windows UIPI probe, both on FocusedApp):
  - *Accessibility*: persistent surface — wizard step + Settings banner (§7.1). Missing grant answers with silence, never an exception, so preflight is the only honest check.
  - *Secure event input*: pre-flight-detectable via `IsSecureEventInputEnabled`; the culprit PID is readable. Per-insert **notify-only**, naming the culprit app — do not block or retry.
- **Enum rename**: config value `"sendinput"` becomes `"type"` with a permanent read alias (old configs keep loading; writes emit `"type"`).
- **Auto-learn is Windows-only**: `typing_failed` is always false on macOS (pasteboard writes can't meaningfully fail); no learned override is ever written on darwin.

---

# 3. Hotkey capture and the key model (#131 · ADR 0002)

- **pynput ≥ 1.8.2, listen-only.** Modifier-only Ctrl+Cmd hold-and-release arrives via `kCGEventFlagsChanged`, which pynput's darwin listener handles. Tap-timeout deafness (pynput doesn't re-enable a timed-out tap) is **accepted for v1**; the named plan-B, if it bites, is a raw pyobjc event tap — not a different permission model.
- **Defaults unchanged**: the combo string `<ctrl>+<cmd>` reads as Ctrl+Cmd hold on darwin; the cleanup tap reads as Ctrl+Shift+Option. Option+Space is disqualified (listen-only = no suppression → typed spaces). Fn-hold is an aspiration gated behind a feasibility spike, only if ever wanted.
- **The combo string is the key model.** `chord.py` ports unchanged except `parse_combo(combo, table=...)` takes the per-OS keycode table (Carbon ints and VK ints never mix in one table); `ChordStateMachine` is untouched.
- **Self-injection**: filtered by pynput's injected-event flag (`kCGEventSourceUnixProcessID != 0`), the LLKHF_INJECTED parity. The Windows mask-key trick (`MASK_MENU`) becomes a no-op on darwin.
- **One permission**: Accessibility only (it supersedes Input Monitoring for event taps). `AXIsProcessTrusted` is the app's sole hotkey health check — the listener lies (`running == True` while deaf); never probe the listener.

---

# 4. Speech & cleanup runtimes (#132 · ADR 0003, numbers from #137)

## 4.1 Speech: one rung

- `STT_RUNTIMES` on darwin is `("auto", "cpu")` for **both** engines; `auto` stays the stored default; stray `cuda`/`directml` values sanitize back to `auto`. The Settings runtime combo hides (`show_runtime_combo=False`).
- **No DML rung in the darwin provider ladder** — this is load-bearing, not cosmetic: ORT on macOS accepts a `DmlExecutionProvider` request, warns, and silently constructs the session on CPU, so the probe "succeeds" and lies.
- **`landed_on` must be derived from `session.get_providers()`**, never from which ladder entry didn't throw. This fix applies on *both* platforms (spec-bound finding of #137).
- Parakeet keeps **probe-then-discard** even on CPU (the first-inference pathology is ORT-level; CoreML EP crashes on Parakeet — microsoft/onnxruntime#26355 — hence no CoreML rung either).
- `GPU_ONLY_ENGINES` (today `settings.py:74`) is empty on darwin: Parakeet stays enabled in the picker.

## 4.2 Cleanup: Metal under the existing `llm_device`

Nothing macOS-specific is introduced. `llm_device` (#116, the Vulkan ladder, merged in PR #138) carries over: `auto` = full GPU offload (`n_gpu_layers=-1`, Metal here) committed only after a real one-token generation (measured cost: 0.09 s); `cpu` remains the config-only escape hatch. Nothing replaces the GPU support pack — Metal ships inside the build; the pack's wizard page and tray items are Windows-only surfaces.

## 4.3 The measured constants (M1 Max, 32 GB — `docs/research/macos-bench-m1max.md`)

darwin `suggest_model()` branch:

- **Parakeet v2 gets the Apple-Silicon Recommended chip at RAM ≥ 16 GB** — varied-length median insert 0.30 s (p95 0.70 s) on the CPU EP, well under the ~1 s bar.
- **distil-small.en is the sub-second Whisper fallback** (median 0.78 s) below the RAM bar; distil-medium/large sit above the 1 s line and are not defensible darwin defaults (1.61 s / 2.79 s median).

darwin cleanup chips: keep the **RAM-headroom test** (unified memory makes it more honest — the GPU can't page its way out of a too-big model), **drop `MAY_BE_SLOW`** (`models.py:36`) whose physical-core gate mispredicts once Metal does the work. The 4B tier is measured, not hedged: Qwen3-4B Q4_K_M warm-cleans in **0.15 / 0.42 / 1.3 s** (short/medium/long) at ~55–61 tok/s on Metal full offload; CPU escape hatch 0.33–3.3 s at ~24–26 tok/s.

Vocabulary: **rung** = ladder step (a runtime), **tier** = model size. Don't conflate them in code or copy.

---

# 5. App identity and overrides (#135 · ADR 0004)

## 5.1 Identity

`AppOverride.process` (and History's `app_name`) holds the **bundle identifier** on macOS (`com.apple.Terminal`), executable name on Windows — same key, per-platform interpretation. Fallback ladder: bundle id → executable basename from `executableURL` (bundle-less processes) → shared `"unknown"` sentinel. Matching stays case-insensitive everywhere; overrides, history rows, and pane suggestions stay join-compatible.

Detection: `NSWorkspace.frontmostApplication()` — **no TCC grant needed**, app-level granularity = override granularity. Not the AX API, not `CGWindowListCopyWindowInfo` (Screen Recording TCC).

## 5.2 The pane bridges to humans

- The add affordance on darwin becomes a **picker of running regular-activation-policy apps**, rendered "Display Name — bundle.id", **storing the id**; free text still accepted.
- Override and history table rows render the **display name** when a live lookup resolves, the raw identity otherwise.
- Darwin ships an **empty default override list** — Restore defaults and `DEFAULT_OVERRIDE_REASONS` stay platform-neutral and naturally inert. The known-suspect list (§8) is verification work, never seed rows: untested seeds are superstition.

---

# 6. Darwin fills for the small seams (#139)

## 6.1 Autostart — LaunchAgent plist

`~/Library/LaunchAgents/com.mikeallisonjs.cadent.plist`. `set_enabled(True)` writes it, `set_enabled(False)` deletes it, `is_enabled()` is file existence — 1:1 with the Run-key adapter. `run_command()` = `[sys.executable, "-m", "cadent"]` (the venv interpreter; no `pythonw` analogue needed). `RunAtLoad=true`, **no `KeepAlive`** (a crash-relaunch loop fighting single-instance is worse than staying down). Checkbox copy via `Capabilities.autostart_label`.

## 6.2 SingleInstance — `fcntl.flock`

Lock file `user_data_dir("Cadent")/cadent.lock` (platformdirs → `~/Library/Application Support/Cadent`). Kernel releases on process death — no stale-lock handling; hold the fd on the adapter instance for the process lifetime. Second instance exits quietly, unchanged. Replaces the named mutex inlined at `app.py:54`.

## 6.3 DesktopEnv — four fills

- *Open-in-shell*: `open <path>` (parity with `os.startfile` — associated app, not a Finder reveal).
- *Text scale*: return `1.0`, an honest no-op (macOS exposes no readable global; Qt's devicePixelRatio already handles display scaling).
- *High contrast*: Qt's `contrastPreference()` (already the first probe) is the sole darwin detector; darwin fallback returns `False`.
- *Animations*: `NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion` via pyobjc; `animations_enabled()` = not-reduce-motion, default `True` on any exception. The meter-keeps-animating policy stays portable in overlay code.

`pyobjc` is already present transitively via pynput — **declare it explicitly** in pyproject (darwin marker).

---

# 7. Platform-conditional UI surface (#130/#132/#135, driven by Capabilities)

All of these read `Capabilities` fields (§1.3), never `sys.platform`:

1. **Wizard**: a darwin-only Accessibility step (grant + `AXIsProcessTrusted` check); the GPU-pack page renders only when `gpu_pack_available`.
2. **Settings banner**: persistent "Cadent needs Accessibility" banner on darwin while `AXIsProcessTrusted()` is false.
3. **Speech pane**: runtime combo hidden on darwin; no disabled model rows, "Needs a graphics card" never renders.
4. **Tray**: GPU-pack items Windows-only.
5. **Overrides pane**: running-apps picker + display-name rendering per §5.2; identity placeholder per platform.
6. **General pane**: autostart checkbox copy from `autostart_label`.
7. **Recorder**: a missing mic TCC grant yields **silent all-zero frames**, no exception (#126/#129) — the recorder needs a zero-buffer heuristic surfacing "check Microphone permission" instead of transcribing silence.

---

# 8. Real-Mac verification pass (#131/#135 handoff lists)

Manual/HITL, on the M1 Max, after the port lands:

1. **Listen-only delivery under Accessibility-only**: docs imply but don't promise a listen-only tap delivers with Accessibility granted and Input Monitoring not. If it fails: add Input Monitoring as a second wizard line — do not redesign the single-check model.
2. **Injected-flag filtering during an actual paste**: hold the hotkey, insert, confirm the posted Cmd-V doesn't re-enter the chord machine.
3. **The known suspects** — Microsoft Remote Desktop, Citrix, Parallels, VMware Fusion, UTM: verify paste-by-default behaves in each *before* authoring any override rows; overrides earned by evidence get hand-written reasons like the Windows seeds.
4. **TCC posture**: during development the grant belongs to the *responsible process* (Terminal/IDE), not the venv python — a granted dev machine proves nothing about other launch paths.
5. Fn feasibility spike: only if Fn promotion is ever wanted; explicitly not v1.

---

## Ticket index

| # | Ticket | Spec sections |
| --- | --- | --- |
| T1 | Platform seam: package, Capabilities, win32 move-and-repoint | §1 |
| T2 | CI test workflow (windows-latest + macos-14) + import-walk test | §1.4 |
| T3 | darwin injection: KeyboardOutput, Clipboard, FocusedApp | §2, §5.1 |
| T4 | darwin hotkeys: HotkeyTap + macOS keycode table | §3 |
| T5 | darwin runtimes: STT ladder, `landed_on`, suggest_model, cleanup chips | §4 |
| T6 | darwin small seams: Autostart, SingleInstance, DesktopEnv | §6 |
| T7 | Platform-conditional UI + recorder zero-buffer heuristic | §7 |
| T8 | Real-Mac verification pass | §8 |
