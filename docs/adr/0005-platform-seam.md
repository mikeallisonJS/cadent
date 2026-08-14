# The platform seam: eight adapters and a capabilities table behind one factory

Per-OS code moves into a `cadent/platform/` package: `base.py` (the protocol
interfaces and a frozen `Capabilities` dataclass), one flat module per OS
(`win32.py`, `darwin.py` — split into subpackages only if one bloats), and an
`__init__.py` whose `current()` selects by `sys.platform` exactly once. This
formalizes the seam the tests already invented — `test_inject.py` monkeypatches
`_foreground_blocked` / `_sendinput_unicode` / `_clipboard_paste` and calls them
"the platform boundary" — rather than inventing a new one. Inline
`sys.platform` branches and import-time module swapping were rejected: both
leave the contract discoverable only by grep, and the macOS port needs the
contract stated.

Eight named seams, one protocol each, small on purpose: **KeyboardOutput**
(`winput`'s send-unicode/chord/mask primitives; CGEvent on darwin),
**Clipboard** (today `inject.py` privates; NSPasteboard + changeCount on
darwin), **FocusedApp** (app identity per ADR 0004, foreground window rect —
absorbing `overlay.py`'s duplicate probe — and the blocked/secure-input
preflights of ADR 0001), **HotkeyTap** (the pynput listener wiring and
injected-event suppression; `chord.py` stays pure), **HardwareProbe**
(cuda/dx12/processor/driver probes; `suggest_model()` stays portable),
**Autostart** (Run key vs LaunchAgent), **SingleInstance** (named mutex vs
file lock, out of `app.py`), and **DesktopEnv** (the guard-tail collected:
open-in-shell, text scale, high contrast, animations). A single god-Platform
interface was rejected as shallow — its surface would be the sum of its parts.

Policy stays portable; platforms supply primitives and facts. The injection
ladder (rung order, fall-through, auto-learn) lives once in `inject.py`, with
the per-OS default rung order as data — `("paste", "type")` on darwin per ADR
0001. The per-OS keycode table is plain data in the OS module, carried by
`Capabilities`; `chord.parse_combo` takes the table as a parameter defaulting
to the current platform's, so either OS's chord logic tests anywhere.
`Capabilities` also carries the accumulated platform facts — `stt_runtimes`,
`gpu_only_engines`, default injection strategy and paste chord, default
overrides, UI visibility flags, autostart label, app-identity placeholder —
and UI/settings/wizard read only it, never the behavioral adapters.

Wiring: `CadentApp.__init__` grows an optional `platform=` parameter
defaulting to `current()` — dependency injection at exactly one constructor.
Old modules move and callers re-point, no re-export shims: shims would keep
legacy import paths alive and defeat the import rule. That rule is the
import-safety guarantee: OS-specific imports (`winreg`, `ctypes.windll`,
pyobjc) may appear only inside per-OS platform modules, which only the factory
imports — enforced by a test that imports every non-adapter module, run on
both OSes by a new CI test workflow (`windows-latest` + `macos-14`), which
today does not exist at all. App-logic tests use fakes at the seams; adapter
internals are platform-skipped and run on the real machines — deep-mocking
pyobjc/ctypes mostly tests the mocks.
