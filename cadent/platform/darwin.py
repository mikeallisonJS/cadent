"""The macOS platform, filling in as its tickets land.

Today: the small utility seams (#147, spec §6), the injection seams (#144,
spec §2/§5.1) — CGEvent keyboard output, NSPasteboard, NSWorkspace app
identity, and the Capabilities column that makes macOS paste by default (ADR
0001) — the hotkey tap (#145, spec §3), and the runtime column (#146, spec
§4): the hardware probes, one speech rung, and no GPU pack because Metal
ships in the build (ADR 0003) — and the UI facts and probes behind #148
(spec §7): the Accessibility grant check, the running-apps picker, the
System Settings deep link, and the zero-buffer microphone hint.

Only the `current()` factory imports this module, and only on macOS — `fcntl`
and the pyobjc frameworks never load anywhere else (ADR 0005).
"""

from __future__ import annotations

import ctypes
import dataclasses
import fcntl
import logging
import os
import plistlib
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .. import config
from ..inject import utf16_chunks
from .base import PermissionPreflight, Platform
from .fallback import CAPABILITIES
from .keycodes import DARWIN_KEYCODES

log = logging.getLogger(__name__)

# ---- speech runtimes: one rung (spec §4.1, ADR 0003) --------------------------

# Both engines run on the CPU and nothing else: ctranslate2 ships no Metal/MPS
# backend on arm64, and the CoreML execution provider crashes on Parakeet at
# first inference (microsoft/onnxruntime#26355). `auto` stays the stored
# default — a one-rung ladder is still a ladder — and stray `cuda`/`directml`
# config values sanitize back to it (config.sanitize reads this fact).
STT_RUNTIMES: dict[str, tuple[str, ...]] = {
    "faster-whisper": ("auto", "cpu"),
    "parakeet": ("auto", "cpu"),
}


# ---- autostart: the LaunchAgent plist (spec §6.1) -----------------------------

LAUNCH_AGENT_LABEL = "com.mikeallisonjs.cadent"


class DarwinAutostart:
    """Run-at-login via `~/Library/LaunchAgents` — 1:1 with the Run-key
    adapter: enable writes the plist, disable deletes it, enabled is file
    existence. `RunAtLoad` only, never `KeepAlive`: a crash-relaunch loop
    fighting single-instance is worse than staying down.

    `agents_dir` is parameterized so tests write to a throwaway directory and
    never touch the real LaunchAgents.
    """

    def __init__(self, agents_dir: Path | None = None) -> None:
        self._agents_dir = agents_dir or Path.home() / "Library" / "LaunchAgents"

    @property
    def _plist_path(self) -> Path:
        return self._agents_dir / f"{LAUNCH_AGENT_LABEL}.plist"

    def _program_arguments(self) -> list[str]:
        """What launchd runs at login: the installed bundle's executable when
        packaged, else the venv interpreter on the module entrypoint.

        Frozen is its own case (#171) — inside Cadent.app, `sys.executable`
        *is* the app, and `-m cadent` would be two strings left sitting in the
        argv of a process that has no module to run. This is the twin of the
        Run-key adapter's frozen branch. No pythonw analogue is needed either
        way: launchd sessions have no console.
        """
        if getattr(sys, "frozen", False):
            return [sys.executable]
        return [sys.executable, "-m", "cadent"]

    def run_command(self) -> str:
        return shlex.join(self._program_arguments())

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._agents_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "Label": LAUNCH_AGENT_LABEL,
                "ProgramArguments": self._program_arguments(),
                "RunAtLoad": True,
            }
            with self._plist_path.open("wb") as f:
                plistlib.dump(payload, f)
        else:
            self._plist_path.unlink(missing_ok=True)

    def is_enabled(self) -> bool:
        return self._plist_path.exists()


# ---- single instance: fcntl.flock (spec §6.2) ---------------------------------


class DarwinSingleInstance:
    """`fcntl.flock` on the data dir's lock file. The kernel releases the lock
    on process death, so there is no stale state to clean up; the fd is held
    on the instance for the process lifetime."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path or (config.DATA_DIR / "cadent.lock")
        self._fd: int | None = None

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True


# ---- desktop environment (spec §6.3) ------------------------------------------


# Apple's documented deep link straight to the Accessibility list — the wizard
# step and the Settings banner both land the user on the exact pane the grant
# lives in rather than at the front door of System Settings.
ACCESSIBILITY_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security"
    "?Privacy_Accessibility")


# The Accessibility grant, described for the surfaces (ADR 0012): the words
# used to live in settings_ui/window.py and wizard.py as darwin copy inside
# cross-platform modules; now the adapter says them and the surfaces render.
ACCESSIBILITY = PermissionPreflight(
    name="accessibility",
    banner=("Cadent needs the Accessibility permission to type what you "
            "dictate. Turn Cadent on under Privacy & Security → Accessibility."),
    wizard_body=("macOS only lets Cadent type into other apps once you grant "
                 "it Accessibility. In System Settings, turn Cadent on under "
                 "Privacy & Security → Accessibility, then come back here."),
    action_label="Open System Settings",
    waiting=("Waiting for the permission — Cadent notices the moment it is "
             "granted."),
    granted="Accessibility is granted — Cadent can type for you.",
)


class DarwinDesktop:
    def open_path(self, path: Path) -> None:
        try:
            subprocess.Popen(["open", str(path)])  # associated app, not a Finder reveal
        except Exception:
            pass

    def request_permission(self) -> None:
        """Deep-link to the Accessibility list; the grant itself is given
        there, and `permission_granted()` reports when it lands."""
        try:
            subprocess.Popen(["open", ACCESSIBILITY_SETTINGS_URL])
        except Exception:
            log.debug("could not open System Settings", exc_info=True)

    def text_scale_factor(self) -> float:
        """An honest no-op: macOS exposes no readable global text scale, and
        Qt's devicePixelRatio already handles display scaling."""
        return 1.0

    def high_contrast(self) -> bool:
        """Qt's contrastPreference() upstream is the sole darwin detector;
        the seam's fallback answer is False."""
        return False

    def animations_enabled(self) -> bool:
        try:
            from AppKit import NSWorkspace

            reduce = NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
            return not reduce
        except Exception:
            log.debug("reduce-motion probe failed", exc_info=True)
            return True

    def tray_ink(self) -> str:
        """Never consulted: `tray_icon_painted_by_os` is true here, so the
        mark ships as a mask and the menu bar picks the ink — including for
        vibrancy and Reduce Transparency, which no value we chose could
        track. Present to satisfy the protocol, black by convention."""
        return "#000000"

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        pass    # the menu bar repaints the mask itself; nothing to watch

    def stop_watching_tray_ink(self) -> None:
        pass


# ---- keyboard output: CGEvent (spec §2) ---------------------------------------

# CGEventKeyboardSetUnicodeString truncates past 20 UTF-16 units per event
# (spec §2) — typing is a paced multi-event sequence, never atomic, which is
# why darwin's ladder never falls through to it.
TYPE_CHUNK_UNITS = 20


class DarwinKeyboard:
    def send_text_units(self, units: list[int]) -> bool:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventKeyboardSetUnicodeString,
            CGEventPost,
            kCGHIDEventTap,
        )

        for chunk in utf16_chunks(units, TYPE_CHUNK_UNITS):
            raw = b"".join(u.to_bytes(2, "little") for u in chunk)
            # The chunker keeps surrogate pairs whole, so real text decodes
            # cleanly; a *lone* surrogate (mojibake input) becomes U+FFFD here
            # rather than raising at the pyobjc str boundary — typed
            # replacement beats an exception that aborts the whole insert.
            text = raw.decode("utf-16-le", errors="replace")
            for is_down in (True, False):
                event = CGEventCreateKeyboardEvent(None, 0, is_down)
                CGEventKeyboardSetUnicodeString(event, len(chunk), text)
                CGEventPost(kCGHIDEventTap, event)
        # CGEventPost reports nothing — there is no detectable short send on
        # darwin, which is exactly why auto-learn is Windows-only (spec §2).
        return True

    def send_chord(self, keys: list[int]) -> None:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGEventFlagMaskAlternate,
            kCGEventFlagMaskCommand,
            kCGEventFlagMaskControl,
            kCGEventFlagMaskShift,
            kCGHIDEventTap,
        )

        # Keyed by the same Carbon codes _CARBON_MODIFIERS names in
        # keycodes.py; the CGEventFlags side is a Quartz fact and so lives
        # here, where Quartz may load.
        flag_for = {55: kCGEventFlagMaskCommand, 56: kCGEventFlagMaskShift,
                    58: kCGEventFlagMaskAlternate, 59: kCGEventFlagMaskControl}
        # Apps read the modifier state off each event's flags, not off which
        # synthetic keys happen to be down — so the chord's mask rides on
        # every event, and drains as each modifier releases (a release still
        # carrying its own flag reads as a stuck modifier to flag-tracking
        # apps).
        mask = 0
        for key in keys:
            mask |= flag_for.get(key, 0)
        for key in keys:
            event = CGEventCreateKeyboardEvent(None, key, True)
            CGEventSetFlags(event, mask)
            CGEventPost(kCGHIDEventTap, event)
        for key in reversed(keys):
            mask &= ~flag_for.get(key, 0)
            event = CGEventCreateKeyboardEvent(None, key, False)
            CGEventSetFlags(event, mask)
            CGEventPost(kCGHIDEventTap, event)

    def send_mask_key(self) -> None:
        """The Windows MenuMaskKey trick; no menu pops on a bare Cmd here."""

    def modifiers_down(self) -> bool:
        from Quartz import (
            CGEventSourceFlagsState,
            kCGEventFlagMaskAlternate,
            kCGEventFlagMaskCommand,
            kCGEventFlagMaskControl,
            kCGEventFlagMaskShift,
            kCGEventSourceStateHIDSystemState,
        )

        mask = (kCGEventFlagMaskCommand | kCGEventFlagMaskShift
                | kCGEventFlagMaskAlternate | kCGEventFlagMaskControl)
        return bool(CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState)
                    & mask)


# ---- clipboard: NSPasteboard (spec §2) ----------------------------------------

# The de-facto standard marker clipboard managers honour for "don't keep this"
# — the ExcludeClipboardContentFromMonitorProcessing analogue.
_TRANSIENT_TYPE = "org.nspasteboard.TransientType"


class DarwinClipboard:
    def get_text(self) -> str | None:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pasteboard = NSPasteboard.generalPasteboard()
        types = [NSPasteboardTypeString] + ([_TRANSIENT_TYPE] if exclude_from_history
                                           else [])
        pasteboard.declareTypes_owner_(types, None)
        pasteboard.setString_forType_(text, NSPasteboardTypeString)
        if exclude_from_history:
            pasteboard.setString_forType_("", _TRANSIENT_TYPE)

    def sequence_number(self) -> int:
        """`changeCount` — the GetClipboardSequenceNumber analogue: moves on
        every write, ours or anyone's, guarding the restore (spec §2)."""
        from AppKit import NSPasteboard

        return int(NSPasteboard.generalPasteboard().changeCount())


# ---- focused app: NSWorkspace + the AX/secure-input preflights (spec §2, §5.1) -


class DarwinFocusedApp:
    def name(self) -> str:
        """Identity ladder (ADR 0004): bundle id → executable basename →
        "unknown". `NSWorkspace.frontmostApplication()` needs no TCC grant and
        matches override granularity (app-level)."""
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return "unknown"
            return self._identity(app)
        except Exception:
            log.debug("frontmost-app probe failed", exc_info=True)
            return "unknown"

    def window_rect(self) -> tuple[int, int, int, int] | None:
        """The frontmost window's frame via the AX API — covered by the same
        Accessibility grant injection already requires; None when the grant is
        missing or the app has no windows. (CGWindowList would need Screen
        Recording TCC and is deliberately avoided, spec §5.1.)"""
        try:
            from AppKit import NSWorkspace
            from ApplicationServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
                AXValueGetValue,
                kAXFocusedWindowAttribute,
                kAXPositionAttribute,
                kAXSizeAttribute,
                kAXValueCGPointType,
                kAXValueCGSizeType,
            )

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            element = AXUIElementCreateApplication(app.processIdentifier())
            err, window = AXUIElementCopyAttributeValue(
                element, kAXFocusedWindowAttribute, None)
            if err or window is None:
                return None
            err, position = AXUIElementCopyAttributeValue(
                window, kAXPositionAttribute, None)
            if err or position is None:
                return None
            err, size = AXUIElementCopyAttributeValue(window, kAXSizeAttribute, None)
            if err or size is None:
                return None
            ok_p, point = AXValueGetValue(position, kAXValueCGPointType, None)
            ok_s, extent = AXValueGetValue(size, kAXValueCGSizeType, None)
            if not (ok_p and ok_s):
                return None
            left, top = int(point.x), int(point.y)
            return (left, top, left + int(extent.width), top + int(extent.height))
        except Exception:
            log.debug("focused-window rect probe failed", exc_info=True)
            return None

    def injection_blocked(self) -> str | None:
        """The two darwin preflights (spec §2). A missing Accessibility grant
        answers synthetic events with silence — never an exception — so this
        check is the only honest one; secure event input is notify-only,
        naming the culprit app. Never block on a *failed* probe."""
        try:
            from ApplicationServices import AXIsProcessTrusted

            if not AXIsProcessTrusted():
                return ("Cadent needs the Accessibility permission "
                        "(System Settings → Privacy & Security → Accessibility)")
        except Exception:
            log.debug("AXIsProcessTrusted probe failed", exc_info=True)
        try:
            if self._secure_input_enabled():
                return (f"secure keyboard input is on "
                        f"({self._secure_input_culprit()})")
        except Exception:
            log.debug("secure-input probe failed", exc_info=True)
        return None

    def permission_granted(self) -> bool:
        """The Accessibility grant, bare — what the wizard step and the
        Settings banner poll. True on a failed probe: a broken check must
        nag nobody (the injection preflight has the same rule)."""
        try:
            from ApplicationServices import AXIsProcessTrusted

            return bool(AXIsProcessTrusted())
        except Exception:
            log.debug("AXIsProcessTrusted probe failed", exc_info=True)
            return True

    def running_apps(self) -> list[tuple[str, str]]:
        """The apps a person can see and target (§5.2): regular activation
        policy only — no menu-bar extras, no daemons. Identity falls down the
        same ladder as `name()`: bundle id → executable basename."""
        try:
            from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace

            rows: list[tuple[str, str]] = []
            for app in NSWorkspace.sharedWorkspace().runningApplications():
                if app.activationPolicy() != NSApplicationActivationPolicyRegular:
                    continue
                identity = self._identity(app)
                if identity == "unknown":
                    continue
                rows.append((str(app.localizedName() or identity), identity))
            return sorted(rows, key=lambda pair: pair[0].lower())
        except Exception:
            log.debug("running-apps probe failed", exc_info=True)
            return []

    def display_name(self, identity: str) -> str | None:
        """The live lookup behind override and history rows (§5.2): resolved
        against what is running right now, case-insensitively — the same rule
        override matching uses — or None, and the row shows the raw id."""
        wanted = identity.lower()
        try:
            from AppKit import NSWorkspace

            for app in NSWorkspace.sharedWorkspace().runningApplications():
                if self._identity(app).lower() == wanted:
                    return str(app.localizedName()) if app.localizedName() else None
            return None
        except Exception:
            log.debug("display-name lookup failed", exc_info=True)
            return None

    @staticmethod
    def _identity(app) -> str:
        """ADR 0004's ladder for one NSRunningApplication."""
        bundle_id = app.bundleIdentifier()
        if bundle_id:
            return str(bundle_id)
        url = app.executableURL()
        path = url.path() if url is not None else None
        return Path(path).name if path else "unknown"

    @staticmethod
    def _secure_input_enabled() -> bool:
        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(carbon.IsSecureEventInputEnabled())

    @staticmethod
    def _secure_input_culprit() -> str:
        """The app holding secure input, by name — the session dictionary
        carries the culprit PID (spec §2)."""
        try:
            from AppKit import NSRunningApplication
            from Quartz import CGSessionCopyCurrentDictionary

            session = CGSessionCopyCurrentDictionary()
            pid = session.get("kCGSSessionSecureInputPID") if session else None
            if not pid:
                return "another app"
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
                int(pid))
            if app is None:
                return f"pid {int(pid)}"
            return str(app.localizedName() or app.bundleIdentifier()
                       or f"pid {int(pid)}")
        except Exception:
            log.debug("secure-input culprit lookup failed", exc_info=True)
            return "another app"


# ---- hardware probes (spec §4) ------------------------------------------------


class DarwinHardware:
    """The Apple-Silicon column of the probe: no CUDA, no Direct3D, no NVIDIA
    driver — three honest constants — and a Metal GPU always, because every
    Apple Silicon Mac ships one and Intel Macs are out of scope (spec: Scope).
    Only the processor name is actually probed."""

    def cuda_total_memory(self) -> float | None:
        return None

    def dx12_gpu_present(self) -> bool:
        return False

    def processor_name(self) -> str:
        try:
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True).strip()
        except Exception:
            log.debug("sysctl brand_string read failed", exc_info=True)
            return ""

    def nvidia_driver_present(self) -> bool:
        return False

    def metal_gpu_present(self) -> bool:
        return True


# ---- hotkey tap: pynput listen-only (spec §3) ---------------------------------


def _pynput_vk(key) -> int | None:
    """The Carbon keycode behind a pynput key object, or None.

    pynput hands named keys as `Key` enum members (whose `.value` is the
    KeyCode) and everything else as a bare `KeyCode`; either way `vk` is the
    Carbon code the chord tables speak. None for events pynput could not
    convert — the caller drops those."""
    if key is None:
        return None
    keycode = getattr(key, "value", key)
    return getattr(keycode, "vk", None)


class DarwinHotkeyTap:
    """A listen-only pynput event tap (spec §3, ADR 0002).

    pynput ≥ 1.8.2 is load-bearing: its darwin listener reports each event's
    injected flag (`kCGEventSourceUnixProcessID != 0` — the LLKHF_INJECTED
    parity) to two-argument callbacks, so our own posted Cmd-V never
    re-enters the chord machine. Modifier-only holds arrive as
    `kCGEventFlagsChanged`, which pynput translates into press/release of the
    sided modifier keys — exactly the codes the Carbon groups in keycodes.py
    name.

    Health is `AXIsProcessTrusted()` — already asked by the injection
    preflight — and never this listener's `running` flag: a tap without the
    Accessibility grant, or one the OS disabled for overrunning its timeout,
    still reports `running == True` while deaf. Tap-timeout deafness (pynput
    never re-enables a timed-out tap) is accepted for v1; the named plan-B,
    if it bites, is a raw pyobjc `CGEventTapCreate` tap that re-enables
    itself on `kCGEventTapDisabledByTimeout` — not a different permission
    model."""

    def __init__(self) -> None:
        self._listener = None

    def start(self, on_event) -> None:
        if self._listener is not None:
            return
        from pynput import keyboard

        def forward(is_down: bool):
            def callback(key, injected: bool) -> None:
                vk = _pynput_vk(key)
                if vk is not None:
                    on_event(vk, is_down, injected)
            return callback

        self._listener = keyboard.Listener(on_press=forward(True),
                                           on_release=forward(False))
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


# ---- the assembled platform ---------------------------------------------------


def create() -> Platform:
    return Platform(
        # The injection column of spec §1.3 (#144): paste by default with no
        # automatic fall-through (ADR 0001), no seed overrides and no
        # learned ones (§5.2), identity is the bundle id (ADR 0004). #145
        # adds the hotkey column: the Accessibility preflight (ADR 0002).
        # #146 adds the runtime column (ADR 0003): one speech rung, no
        # engine gated on a GPU, the runtime combo hidden, no pack to
        # download. #148 adds the UI facts: the running-apps picker and the
        # words a zero-only capture deserves (§7).
        capabilities=dataclasses.replace(
            CAPABILITIES,
            keycode_table=DARWIN_KEYCODES,
            default_injection_strategy="clipboard",
            injection_rungs=("paste", "type"),
            paste_chord="cmd+v",
            default_overrides=(),
            default_override_reasons={},
            auto_learn_overrides=False,
            stt_runtimes=STT_RUNTIMES,
            gpu_only_engines=frozenset(),
            show_runtime_combo=False,
            gpu_pack_available=False,
            permission=ACCESSIBILITY,
            app_identity_placeholder="com.example.app",
            autostart_label="Start at login",
            app_picker=True,
            mic_permission_hint=(
                "Nothing was heard. If this keeps happening, check Microphone "
                "permission for your terminal or IDE in System Settings → "
                "Privacy & Security → Microphone."),
            tray_click_toggles_pause=False,
            tray_icon_painted_by_os=True,
            modifier_captions={"ctrl": "Ctrl", "shift": "Shift",
                               "alt": "Option", "option": "Option",
                               "win": "Cmd", "cmd": "Cmd"},
            # One Appearance control here, not Windows' app/system pair — and
            # the contrast setting is called Increase contrast. Qt's
            # contrastPreference() reports it on this platform too, so the
            # reason line is reachable and must name the right setting.
            theme_subtitle="Follows your Mac's Appearance setting",
            high_contrast_reason=("macOS is set to increase contrast, so "
                                  "Cadent is following your system colours"),
        ),
        keyboard=DarwinKeyboard(),
        clipboard=DarwinClipboard(),
        focused_app=DarwinFocusedApp(),
        hotkey_tap=DarwinHotkeyTap(),
        hardware=DarwinHardware(),
        autostart=DarwinAutostart(),
        single_instance=DarwinSingleInstance(),
        desktop=DarwinDesktop(),
    )
