"""The Whole tier: every seam filled from X11, no grant anywhere (spec §3–§5,
ADR 0007–0009).

Four adapters over python-xlib: XTEST typing with a scratch keycode remapped
for characters the layout lacks, selection ownership with an XFixes
`SelectionNotify` counter standing in for a sequence number, `WM_CLASS` →
desktop-file id for the focused app, and an XRecord tap through pynput.

**Injected-event suppression is a state gate.** XTEST fakes arrive with
`send_event` false — indistinguishable from the user's keys — so pynput's
`injected` flag lies on X11. The keyboard adapter holds `SendGate` while it
is mid-send and the tap reports every event in that window as injected,
which keeps our own Ctrl+V out of the chord machine (ADR 0007).
"""

from __future__ import annotations

import logging
import os
import queue
import select
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ...inject import utf16_chunks
from .apps import DesktopIndex

log = logging.getLogger(__name__)

# X keysyms the typing path needs by name (X11/keysymdef.h).
XK_RETURN, XK_TAB, XK_SHIFT_L = 0xFF0D, 0xFF09, 0xFFE1
# Latin-1 keysyms equal their codepoints; everything else is the xkb
# unicode keysym, 0x01000000 + codepoint.
_UNICODE_KEYSYM = 0x01000000

# Modifier indices in the X modifier mapping (Shift, Lock, Control, Mod1…Mod5).
_SHIFT, _CONTROL, _MOD1, _MOD4 = 0, 2, 3, 6


def keysym_for(char: str) -> int:
    cp = ord(char)
    if char == "\n":
        return XK_RETURN
    if char == "\t":
        return XK_TAB
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return _UNICODE_KEYSYM + cp


def normalize_keysym(keysym: int) -> int:
    """Shifted Latin letters report their uppercase keysym; the chord tables
    store lowercase, so A–Z fold down. Everything else passes through."""
    if 0x41 <= keysym <= 0x5A:
        return keysym + 0x20
    return keysym


class SendGate:
    """Held by the keyboard adapter around every synthetic send; read by the
    tap to flag what it hears meanwhile as injected."""

    def __init__(self) -> None:
        self._depth = 0
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self._depth += 1
        return self

    def __exit__(self, *exc) -> None:
        with self._lock:
            self._depth -= 1

    @property
    def active(self) -> bool:
        return self._depth > 0


def _open_display(name: str | None = None):
    from Xlib import display

    return display.Display(name)


# ---- keyboard output: XTEST + a scratch keycode (spec §3) ----------------------------

class X11Keyboard:
    def __init__(self, gate: SendGate, display_name: str | None = None) -> None:
        self._gate = gate
        self._display_name = display_name
        self._d = None
        self._lock = threading.RLock()
        self._errors: list = []
        self._scratch: int | None = None
        self._width = 0

    def _display(self):
        if self._d is None:
            from Xlib.ext import xtest  # noqa: F401 - registers the extension

            d = _open_display(self._display_name)
            if d.query_extension("XTEST") is None:
                raise RuntimeError("the X server has no XTEST extension")
            # Async X errors (a bad keycode, a refused remap) surface here on
            # the next sync, which is what turns them into a detectable
            # typing failure the ladder can learn from.
            d.set_error_handler(self._on_error)
            self._d = d
            info = d.display.info
            self._min, self._max = info.min_keycode, info.max_keycode
            mapping = d.get_keyboard_mapping(self._min, self._max - self._min + 1)
            self._width = len(mapping[0]) if mapping else 0
        return self._d

    def _on_error(self, error, request) -> int:
        self._errors.append(error)
        return 0

    def _checked_sync(self) -> None:
        self._d.sync()
        if self._errors:
            errors, self._errors = self._errors, []
            raise RuntimeError(f"X request failed: {errors[0]}")

    def _fake(self, keycode: int, pressed: bool) -> None:
        from Xlib import X
        from Xlib.ext import xtest

        xtest.fake_input(self._d, X.KeyPress if pressed else X.KeyRelease, keycode)

    # ---- the seam ---------------------------------------------------------------

    def send_text_units(self, units: list[int]) -> bool:
        """Type each character; raise on anything X refuses (a detectable
        failure, so auto-learn fires — ADR 0007)."""
        with self._lock, self._gate:
            d = self._display()
            for chunk in utf16_chunks(units, 64):
                raw = b"".join(u.to_bytes(2, "little") for u in chunk)
                for char in raw.decode("utf-16-le", errors="replace"):
                    self._type_char(d, keysym_for(char))
                self._checked_sync()
        return True

    def _type_char(self, d, keysym: int) -> None:
        keycode = d.keysym_to_keycode(keysym)
        if keycode:
            for index, shift in ((0, False), (1, True)):
                if d.keycode_to_keysym(keycode, index) == keysym:
                    self._tap(d, keycode, shift)
                    return
        # Not on the layout at a plain or shifted level: remap the scratch
        # keycode for one keystroke, the xdotool trick.
        scratch = self._scratch_keycode(d)
        d.change_keyboard_mapping(scratch, [[keysym] * self._width])
        d.sync()
        time.sleep(0.004)   # let clients refresh their keymap
        try:
            self._tap(d, scratch, False)
            self._checked_sync()
        finally:
            d.change_keyboard_mapping(scratch, [[0] * self._width])
            d.sync()

    def _tap(self, d, keycode: int, shift: bool) -> None:
        shift_kc = d.keysym_to_keycode(XK_SHIFT_L) if shift else 0
        if shift_kc:
            self._fake(shift_kc, True)
        self._fake(keycode, True)
        self._fake(keycode, False)
        if shift_kc:
            self._fake(shift_kc, False)

    def _scratch_keycode(self, d) -> int:
        if self._scratch is not None:
            return self._scratch
        mapping = d.get_keyboard_mapping(self._min, self._max - self._min + 1)
        for offset, keysyms in reversed(list(enumerate(mapping))):
            if not any(keysyms):
                self._scratch = self._min + offset
                return self._scratch
        raise RuntimeError("no free keycode to type an off-layout character")

    def send_chord(self, keys: list[int]) -> None:
        """`keys` are keysyms from the Linux table; pressed in order,
        released in reverse, all inside the gate."""
        with self._lock, self._gate:
            d = self._display()
            keycodes = [d.keysym_to_keycode(k) for k in keys]
            if not all(keycodes):
                raise RuntimeError(f"paste chord has a keysym the layout lacks: {keys}")
            for kc in keycodes:
                self._fake(kc, True)
            for kc in reversed(keycodes):
                self._fake(kc, False)
            self._checked_sync()

    def send_mask_key(self) -> None:
        """No-op for v1 (ADR 0008): whether bare Super pops a launcher is a
        hardware item, and the fix would be an XTEST keysym here."""

    def modifiers_down(self) -> bool:
        with self._lock:
            try:
                d = self._display()
                keymap = d.query_keymap()
                mods = d.get_modifier_mapping()
            except Exception:
                log.debug("modifier probe failed", exc_info=True)
                return False
        for index in (_SHIFT, _CONTROL, _MOD1, _MOD4):
            for keycode in mods[index]:
                if keycode and keymap[keycode // 8] & (1 << (keycode % 8)):
                    return True
        return False


# ---- clipboard: selection ownership + XFixes (spec §3) --------------------------------

_TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "TEXT", "STRING")
# Klipper's "don't keep this" hint — the ExcludeClipboardContentFromMonitor
# Processing analogue; other managers have no GNOME-wide equivalent.
_KDE_HINT = "x-kde-passwordManagerHint"


class X11Clipboard:
    """Owns CLIPBOARD on `set_text` and serves it for as long as we hold it,
    from one thread that also counts XFixes selection-owner changes — the
    seam's `sequence_number`. Every X request runs on that thread; callers
    post work and wait, because a Display is not shareable across threads."""

    def __init__(self, display_name: str | None = None) -> None:
        self._display_name = display_name
        self._thread: threading.Thread | None = None
        self._jobs: queue.Queue = queue.Queue()
        self._wake_r, self._wake_w = os.pipe()
        self._text: str | None = None
        self._exclude = False
        self._seq = 0
        self._d = None
        self._win = None
        self._atoms: dict[str, int] = {}
        self._pending_read: dict | None = None

    # ---- the thread --------------------------------------------------------------

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        ready: queue.Queue = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                        name="cadent-x11-clipboard")
        self._thread.start()
        outcome = ready.get(timeout=5)
        if isinstance(outcome, Exception):
            raise outcome

    def _run(self, ready: queue.Queue) -> None:
        try:
            from Xlib import X
            from Xlib.ext import xfixes

            d = _open_display(self._display_name)
            self._d = d
            root = d.screen().root
            self._win = root.create_window(0, 0, 1, 1, 0, 0, window_class=X.InputOnly)
            self._atoms = {name: d.intern_atom(name) for name in
                           ("CLIPBOARD", "TARGETS", "MULTIPLE", "INCR", "CADENT_SEL",
                            _KDE_HINT, *_TEXT_TARGETS)}
            if d.query_extension("XFIXES") is not None:
                xfixes.query_version(d)
                d.xfixes_select_selection_input(
                    self._win, self._atoms["CLIPBOARD"],
                    xfixes.XFixesSetSelectionOwnerNotifyMask)
            d.flush()
        except Exception as exc:
            ready.put(exc)
            return
        ready.put(None)
        fd = self._d.fileno()
        while True:
            self._drain_events()
            readable, _, _ = select.select([fd, self._wake_r], [], [])
            if self._wake_r in readable:
                os.read(self._wake_r, 64)
                while not self._jobs.empty():
                    fn, box = self._jobs.get()
                    try:
                        box.put((fn(), None))
                    except Exception as exc:
                        box.put((None, exc))
            if fd in readable:
                self._drain_events()

    def _run_on_thread(self, fn: Callable, timeout: float = 5.0):
        self._start()
        box: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((fn, box))
        os.write(self._wake_w, b"x")
        result, error = box.get(timeout=timeout)
        if error is not None:
            raise error
        return result

    def _drain_events(self) -> None:
        from Xlib import X

        d = self._d
        while d.pending_events():
            event = d.next_event()
            if event.type == X.SelectionRequest:
                self._serve(event)
            elif event.type == X.SelectionClear:
                self._text = None
            elif event.type == X.SelectionNotify and self._pending_read is not None:
                self._pending_read["event"] = event
            elif getattr(event, "type", None) == d.get_extension_event_base("XFIXES") \
                    if False else False:
                pass
            elif self._is_xfixes_selection_notify(event):
                self._seq += 1

    def _is_xfixes_selection_notify(self, event) -> bool:
        return type(event).__name__ == "SetSelectionOwnerNotify" or (
            hasattr(event, "selection") and hasattr(event, "owner")
            and hasattr(event, "selection_timestamp"))

    def _serve(self, event) -> None:
        from Xlib import X, Xatom
        from Xlib.protocol import event as xevent

        a = self._atoms
        target = event.target
        prop = event.property or target
        ok = self._text is not None
        if ok and target == a["TARGETS"]:
            targets = [a["TARGETS"]] + [a[t] for t in _TEXT_TARGETS]
            if self._exclude:
                targets.append(a[_KDE_HINT])
            event.requestor.change_property(prop, Xatom.ATOM, 32, targets)
        elif ok and target == a[_KDE_HINT]:
            event.requestor.change_property(prop, target, 8, b"secret")
        elif ok and target in (a[t] for t in _TEXT_TARGETS):
            data = self._text.encode("utf-8")
            kind = Xatom.STRING if target == a["STRING"] else target
            event.requestor.change_property(prop, kind, 8, data)
        else:
            prop = X.NONE
        notify = xevent.SelectionNotify(
            time=event.time, requestor=event.requestor, selection=event.selection,
            target=target, property=prop)
        event.requestor.send_event(notify)
        self._d.flush()

    # ---- the seam --------------------------------------------------------------------

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        from Xlib import X

        def own() -> None:
            self._text = text
            self._exclude = exclude_from_history
            self._win.set_selection_owner(self._atoms["CLIPBOARD"], X.CurrentTime)
            self._d.flush()
            if self._d.get_selection_owner(self._atoms["CLIPBOARD"]) != self._win:
                raise RuntimeError("could not take ownership of CLIPBOARD")

        self._run_on_thread(own)

    def get_text(self) -> str | None:
        from Xlib import X

        def read() -> str | None:
            owner = self._d.get_selection_owner(self._atoms["CLIPBOARD"])
            if owner == X.NONE:
                return None
            if owner == self._win:
                return self._text
            self._pending_read = {}
            self._win.convert_selection(self._atoms["CLIPBOARD"], self._atoms["UTF8_STRING"],
                                        self._atoms["CADENT_SEL"], X.CurrentTime)
            self._d.flush()
            deadline = time.monotonic() + 1.0
            while "event" not in self._pending_read and time.monotonic() < deadline:
                readable, _, _ = select.select([self._d.fileno()], [], [], 0.05)
                if readable:
                    self._drain_events()
            event = self._pending_read.get("event")
            self._pending_read = None
            if event is None or event.property == X.NONE:
                return None
            reply = self._win.get_full_property(self._atoms["CADENT_SEL"], X.AnyPropertyType)
            self._win.delete_property(self._atoms["CADENT_SEL"])
            if reply is None or reply.property_type == self._atoms["INCR"]:
                return None     # incremental transfers (huge clips) are not restored
            value = reply.value
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value)

        try:
            return self._run_on_thread(read)
        except Exception:
            log.debug("clipboard read failed", exc_info=True)
            return None

    def sequence_number(self) -> int:
        try:
            return self._run_on_thread(lambda: self._seq)
        except Exception:
            return self._seq


# ---- focused app: WM_CLASS → desktop-file id (spec §5) ---------------------------------

class X11FocusedApp:
    def __init__(self, index: DesktopIndex, display_name: str | None = None) -> None:
        self._index = index
        self._display_name = display_name
        self._d = None
        self._lock = threading.Lock()

    def _display(self):
        if self._d is None:
            self._d = _open_display(self._display_name)
        return self._d

    def _active_window(self):
        from Xlib import X

        d = self._display()
        root = d.screen().root
        reply = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
        if reply is None or not reply.value or not reply.value[0]:
            return None
        return d.create_resource_object("window", int(reply.value[0]))

    def name(self) -> str:
        """Desktop-file id from WM_CLASS, else the exe basename behind
        `_NET_WM_PID`, else "unknown" (ADR 0009)."""
        with self._lock:
            try:
                win = self._active_window()
                if win is None:
                    return "unknown"
                wm_class = win.get_wm_class() or ("", "")
                resolved = self._index.id_for_wm_class(*wm_class)
                if resolved:
                    return resolved
                pid = win.get_full_property(self._display().intern_atom("_NET_WM_PID"), 0)
                if pid is not None and pid.value:
                    exe = exe_basename(int(pid.value[0]))
                    if exe:
                        return exe
                return "unknown"
            except Exception:
                log.debug("focused-window probe failed", exc_info=True)
                return "unknown"

    def window_rect(self) -> tuple[int, int, int, int] | None:
        with self._lock:
            try:
                win = self._active_window()
                if win is None:
                    return None
                geom = win.get_geometry()
                root = self._display().screen().root
                coords = win.translate_coords(root, 0, 0)
                left, top = coords.x, coords.y
                return (left, top, left + geom.width, top + geom.height)
            except Exception:
                log.debug("focused-window rect probe failed", exc_info=True)
                return None

    def injection_blocked(self) -> str | None:
        return None     # nothing like UIPI or secure input to name (ADR 0007)

    def permission_granted(self) -> bool:
        return True     # no grant exists on X11

    def running_apps(self) -> list[tuple[str, str]]:
        self._index.refresh()
        return self._index.installed()

    def display_name(self, identity: str) -> str | None:
        return self._index.display_name(identity)


def exe_basename(pid: int) -> str:
    try:
        return Path(os.readlink(f"/proc/{pid}/exe")).name.lower()
    except OSError:
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip().lower()
        except OSError:
            return ""


# ---- hotkey tap: XRecord through pynput (spec §4) --------------------------------------

def _pynput_keysym(key) -> int | None:
    if key is None:
        return None
    keycode = getattr(key, "value", key)
    vk = getattr(keycode, "vk", None)
    return normalize_keysym(vk) if vk is not None else None


class X11HotkeyTap:
    """Listen-only, press and release, sided modifiers, no grant: the X
    connection fails loudly with an exception, so there is nothing to poll.
    `chords` is ignored — the hook hears the whole keyboard."""

    def __init__(self, gate: SendGate) -> None:
        self._gate = gate
        self._listener = None

    def start(self, on_event, chords=()) -> None:
        if self._listener is not None:
            return
        from pynput import keyboard

        def forward(is_down: bool):
            def callback(key, injected: bool) -> None:
                keysym = _pynput_keysym(key)
                if keysym is not None:
                    on_event(keysym, is_down, injected or self._gate.active)
            return callback

        self._listener = keyboard.Listener(on_press=forward(True),
                                           on_release=forward(False))
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def available(self) -> bool:
        return True

    def bindings(self):
        return {}
