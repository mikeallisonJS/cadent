"""The Whole tier's adapters: everything that talks to an X server (spec §3–5,
ADR 0007–0009).

Hotkey capture is pynput's XRecord tap, typing is XTEST, the clipboard rung
owns the CLIPBOARD selection, and the focused app is read from EWMH
properties. No grant exists on X11, so nothing here asks for one — and nothing
swallows a failure either: a dead X connection, an XTEST error or an exhausted
scratch keycode raises, which is what makes `auto_learn_overrides` true.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

import Xlib.display
import Xlib.error
import Xlib.ext.xfixes
import Xlib.protocol.event
import Xlib.X

from .desktop_files import DesktopIndex

log = logging.getLogger(__name__)

# Keysyms a typed character or chord may need (keysymdef.h).
KEYSYM_RETURN, KEYSYM_TAB, KEYSYM_SHIFT_L = 0xFF0D, 0xFF09, 0xFFE1
UNICODE_KEYSYM_BASE = 0x01000000

# Held-modifier bits in a pointer query's mask: Shift, Control, Alt (Mod1) and
# Super (Mod4). Lock and NumLock are state, not a held key, and stay out.
HELD_MODIFIER_MASK = (Xlib.X.ShiftMask | Xlib.X.ControlMask
                      | Xlib.X.Mod1Mask | Xlib.X.Mod4Mask)

# How long a client gets to notice a remapped scratch keycode before the key is
# pressed, and to read the keypress before the mapping is put back.
REMAP_SETTLE_S = 0.03
# XRecord delivers our own fake events a beat after the request returns; the
# gate outlives the send by this much so they are still seen as ours.
GATE_TAIL_S = 0.08


def open_display() -> Xlib.display.Display:
    """A connection to the X server, or an exception that says why not."""
    return Xlib.display.Display()


class InjectionGate:
    """Injected-event suppression as a state gate (spec §3). XTEST fakes reach
    XRecord with `send_event` false — indistinguishable from a real key — so
    the keyboard raises this gate around every send and the tap reports events
    seen while it is up as injected."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth = 0
        self._until = 0.0

    def __enter__(self) -> InjectionGate:
        with self._lock:
            self._depth += 1
        return self

    def __exit__(self, *exc) -> None:
        with self._lock:
            self._depth -= 1
            self._until = time.monotonic() + GATE_TAIL_S

    def active(self) -> bool:
        with self._lock:
            return self._depth > 0 or time.monotonic() < self._until


class _Connection:
    """A lazily opened display that is dropped on error and reopened on the
    next use, so a restarted X server heals instead of wedging the adapter."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._display: Xlib.display.Display | None = None

    @property
    def display(self) -> Xlib.display.Display:
        if self._display is None:
            self._display = open_display()
        return self._display

    def reset(self) -> None:
        display, self._display = self._display, None
        if display is not None:
            try:
                display.close()
            except Exception:
                log.debug("closing a dead X connection failed", exc_info=True)


class _ErrorTrap:
    """Collects the asynchronous X errors a batch of requests produced, so a
    rejected XTEST request raises at the call site instead of vanishing into
    python-xlib's default handler."""

    def __init__(self, display: Xlib.display.Display) -> None:
        self._display = display
        self.errors: list[Xlib.error.XError] = []

    def __enter__(self) -> _ErrorTrap:
        self._display.set_error_handler(lambda err, request: self.errors.append(err))
        return self

    def __exit__(self, *exc) -> None:
        self._display.sync()
        self._display.set_error_handler(None)
        if exc[0] is None and self.errors:
            raise RuntimeError(f"X server rejected a request: {self.errors[0]}")


def keysym_for(char: str) -> int:
    if char in ("\n", "\r"):
        return KEYSYM_RETURN
    if char == "\t":
        return KEYSYM_TAB
    code = ord(char)
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code             # Latin-1 keysyms are their code points
    return UNICODE_KEYSYM_BASE | code


# ---- typing ------------------------------------------------------------------


class X11Keyboard:
    """XTEST typing. A character the layout has is pressed on its own key (with
    Shift where it sits on the second level); anything else borrows a scratch
    keycode, remapped to that keysym for the length of one keypress."""

    def __init__(self, gate: InjectionGate | None = None) -> None:
        self._gate = gate or InjectionGate()
        self._conn = _Connection()

    # -- seam --

    def send_text_units(self, units: list[int]) -> bool:
        raw = b"".join(u.to_bytes(2, "little") for u in units)
        text = raw.decode("utf-16-le", errors="replace")
        with self._conn.lock, self._gate:
            try:
                self._type(self._conn.display, text)
            except Exception:
                self._conn.reset()
                raise
        return True

    def send_chord(self, keys: list[int]) -> None:
        with self._conn.lock, self._gate:
            try:
                display = self._conn.display
                self._require_xtest(display)
                codes = [self._keycode(display, ks) for ks in keys]
                with _ErrorTrap(display):
                    for code in codes:
                        self._fake(display, Xlib.X.KeyPress, code)
                    for code in reversed(codes):
                        self._fake(display, Xlib.X.KeyRelease, code)
            except Exception:
                self._conn.reset()
                raise

    def send_mask_key(self) -> None:
        pass    # whether bare Super pops a launcher mid-chord is a hardware item

    def modifiers_down(self) -> bool:
        with self._conn.lock:
            try:
                display = self._conn.display
                return bool(display.screen().root.query_pointer().mask
                            & HELD_MODIFIER_MASK)
            except Exception:
                self._conn.reset()
                raise

    # -- internals --

    @staticmethod
    def _require_xtest(display: Xlib.display.Display) -> None:
        if not display.has_extension("XTEST"):
            raise RuntimeError("the X server has no XTEST extension")

    @staticmethod
    def _fake(display: Xlib.display.Display, event_type: int, keycode: int) -> None:
        from Xlib.ext import xtest

        xtest.fake_input(display, event_type, keycode)

    @staticmethod
    def _keycode(display: Xlib.display.Display, keysym: int) -> int:
        code = display.keysym_to_keycode(keysym)
        if not code:
            raise RuntimeError(f"no key produces keysym {keysym:#x}")
        return code

    def _type(self, display: Xlib.display.Display, text: str) -> None:
        self._require_xtest(display)
        shift = display.keysym_to_keycode(KEYSYM_SHIFT_L)
        scratch: int | None = None
        for char in text:
            keysym = keysym_for(char)
            found = [(code, level) for code, level in display.keysym_to_keycodes(keysym)
                     if level in (0, 1)]
            if found:
                code, level = min(found, key=lambda f: f[1])
                with _ErrorTrap(display):
                    self._press(display, code, shift if level == 1 else None)
                continue
            if scratch is None:
                scratch = self._scratch_keycode(display)
            self._press_scratch(display, scratch, keysym)

    def _press(self, display: Xlib.display.Display, code: int,
               shift: int | None) -> None:
        if shift:
            self._fake(display, Xlib.X.KeyPress, shift)
        self._fake(display, Xlib.X.KeyPress, code)
        self._fake(display, Xlib.X.KeyRelease, code)
        if shift:
            self._fake(display, Xlib.X.KeyRelease, shift)

    def _press_scratch(self, display: Xlib.display.Display, scratch: int,
                       keysym: int) -> None:
        per_keycode = len(display.get_keyboard_mapping(scratch, 1)[0])
        try:
            with _ErrorTrap(display):
                display.change_keyboard_mapping(scratch, [[keysym] * per_keycode])
            time.sleep(REMAP_SETTLE_S)
            with _ErrorTrap(display):
                self._press(display, scratch, None)
            time.sleep(REMAP_SETTLE_S)
        finally:
            display.change_keyboard_mapping(scratch, [[0] * per_keycode])
            display.sync()

    @staticmethod
    def _scratch_keycode(display: Xlib.display.Display) -> int:
        """A keycode no key uses: every level unmapped. The server keeps a few
        spare, but not guaranteed — none free is a detectable failure."""
        first = display.display.info.min_keycode
        count = display.display.info.max_keycode - first + 1
        for offset, keysyms in enumerate(display.get_keyboard_mapping(first, count)):
            if not any(keysyms):
                return first + offset
        raise RuntimeError("no free keycode to type a character the layout lacks")


# ---- clipboard ---------------------------------------------------------------

TEXT_TARGETS = ("UTF8_STRING", "STRING", "TEXT", "text/plain;charset=utf-8",
                "text/plain")
SELECTION_WAIT_S = 0.5


class X11Clipboard:
    """Owns the CLIPBOARD selection to paste (spec §3). One thread services
    `SelectionRequest`s for the text we hold and counts XFixes selection-owner
    notifications, whose total is `sequence_number()` — every write by anyone
    moves it, so restoring is skipped when someone else copied meanwhile."""

    def __init__(self) -> None:
        self._conn = _Connection()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cond = threading.Condition()
        self._text: str | None = None       # what we are serving, while we own it
        self._notifies = 0                  # every owner change, ours included
        self._own_notifies = 0              # ... the ones that made us owner
        self._replies: queue.Queue[str | None] = queue.Queue()
        self._window = None
        self._atoms: dict[str, int] = {}

    # -- seam --

    def get_text(self) -> str | None:
        self._ensure_running()
        with self._cond:
            if self._text is not None:
                return self._text
        return self._fetch()

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        self._ensure_running()
        with self._conn.lock:
            display = self._conn.display
            before = self._own_notifies
            with self._cond:
                self._text = text
            self._window.set_selection_owner(self._atom("CLIPBOARD"), Xlib.X.CurrentTime)
            display.flush()
            if display.get_selection_owner(self._atom("CLIPBOARD")) != self._window:
                with self._cond:
                    self._text = None
                raise RuntimeError("could not take ownership of the X selection")
        # Our own announcement lands asynchronously; wait for it so the caller's
        # `sequence_number()` read already includes it.
        with self._cond:
            if not self._cond.wait_for(lambda: self._own_notifies > before,
                                       SELECTION_WAIT_S):
                log.debug("no XFixes notification for our own selection")

    def sequence_number(self) -> int:
        with self._cond:
            return self._notifies

    # -- the service thread --

    def _atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self._conn.display.intern_atom(name)
        return self._atoms[name]

    def _ensure_running(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._conn.reset()
            display = self._conn.display
            if not display.has_extension("XFIXES"):
                raise RuntimeError("the X server has no XFIXES extension")
            display.xfixes_query_version()
            self._atoms.clear()
            self._window = display.screen().root.create_window(
                0, 0, 1, 1, 0, display.screen().root_depth,
                event_mask=Xlib.X.PropertyChangeMask)
            display.xfixes_select_selection_input(
                self._window, self._atom("CLIPBOARD"),
                Xlib.ext.xfixes.XFixesSetSelectionOwnerNotifyMask)
            display.flush()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="cadent-x11-selection")
            self._thread.start()

    def _loop(self) -> None:
        display = self._conn.display
        while True:
            try:
                event = display.next_event()
            except Exception:
                log.debug("selection thread lost its X connection", exc_info=True)
                return
            try:
                self._dispatch(event)
            except Exception:
                log.debug("selection event failed", exc_info=True)

    def _dispatch(self, event) -> None:
        if isinstance(event, Xlib.ext.xfixes.SelectionNotify):
            with self._cond:
                self._notifies += 1
                if event.owner == self._window:
                    self._own_notifies += 1
                elif self._text is not None:
                    self._text = None       # someone took the selection from us
                self._cond.notify_all()
        elif event.type == Xlib.X.SelectionRequest:
            self._serve(event)
        elif event.type == Xlib.X.SelectionNotify:
            self._receive(event)

    def _serve(self, event) -> None:
        with self._cond:
            text = self._text
        target = event.target
        prop = event.property or event.target
        ok = text is not None and event.selection == self._atom("CLIPBOARD")
        requestor = event.requestor
        if ok and target == self._atom("TARGETS"):
            names = ("TARGETS", *TEXT_TARGETS)
            requestor.change_property(prop, self._atom("ATOM"), 32,
                                      [self._atom(n) for n in names])
        elif ok and target in (self._atom(n) for n in TEXT_TARGETS):
            requestor.change_property(prop, target, 8, text.encode("utf-8"))
        else:
            prop = 0
        notify = Xlib.protocol.event.SelectionNotify(
            time=event.time, requestor=requestor, selection=event.selection,
            target=target, property=prop)
        requestor.send_event(notify)
        self._conn.display.flush()

    # -- reading someone else's selection --

    def _fetch(self) -> str | None:
        with self._conn.lock:
            display = self._conn.display
            while not self._replies.empty():
                self._replies.get_nowait()
            if not display.get_selection_owner(self._atom("CLIPBOARD")):
                return None
            self._window.convert_selection(
                self._atom("CLIPBOARD"), self._atom("UTF8_STRING"),
                self._atom("CADENT_SELECTION"), Xlib.X.CurrentTime)
            display.flush()
        try:
            return self._replies.get(timeout=SELECTION_WAIT_S)
        except queue.Empty:
            return None

    def _receive(self, event) -> None:
        text = None
        if event.property:
            prop = self._window.get_full_property(event.property, Xlib.X.AnyPropertyType)
            self._window.delete_property(event.property)
            if prop is not None and prop.property_type == self._atom("UTF8_STRING"):
                value = prop.value
                text = (value if isinstance(value, bytes) else bytes(value)
                        ).decode("utf-8", errors="replace")
        self._replies.put(text)


# ---- the focused app ----------------------------------------------------------


class X11FocusedApp:
    """Identity from `WM_CLASS` through the desktop files, then the executable
    basename, then "unknown" (ADR 0009); the rect for the overlay. Where no
    permission exists, `permission_granted()` is simply true."""

    UNKNOWN = "unknown"

    def __init__(self, index: DesktopIndex | None = None) -> None:
        self._conn = _Connection()
        self._index = index or DesktopIndex()

    def name(self) -> str:
        try:
            with self._conn.lock:
                window = self._active_window()
                if window is None:
                    return self.UNKNOWN
                wm_class = window.get_wm_class()
                if wm_class:
                    found = self._index.resolve(tuple(wm_class))
                    if found:
                        return found
                return self._executable(window) or self.UNKNOWN
        except Exception:
            log.debug("focused app lookup failed", exc_info=True)
            self._conn.reset()
            return self.UNKNOWN

    def window_rect(self) -> tuple[int, int, int, int] | None:
        try:
            with self._conn.lock:
                window = self._active_window()
                if window is None:
                    return None
                geometry = window.get_geometry()
                origin = window.translate_coords(self._conn.display.screen().root, 0, 0)
                left, top = origin.x, origin.y
                return (left, top, left + geometry.width, top + geometry.height)
        except Exception:
            log.debug("window rect lookup failed", exc_info=True)
            self._conn.reset()
            return None

    def injection_blocked(self) -> str | None:
        return None

    def permission_granted(self) -> bool:
        return True

    def running_apps(self) -> list[tuple[str, str]]:
        return self._index.installed()

    def display_name(self, identity: str) -> str | None:
        return self._index.display_name(identity)

    # -- internals --

    def _active_window(self):
        display = self._conn.display
        root = display.screen().root
        prop = root.get_full_property(display.intern_atom("_NET_ACTIVE_WINDOW"),
                                      Xlib.X.AnyPropertyType)
        wid = int(prop.value[0]) if prop is not None and len(prop.value) else 0
        if not wid:
            return None
        return display.create_resource_object("window", wid)

    def _executable(self, window) -> str | None:
        display = self._conn.display
        prop = window.get_full_property(display.intern_atom("_NET_WM_PID"),
                                        Xlib.X.AnyPropertyType)
        if prop is None or not len(prop.value):
            return None
        try:
            import psutil

            proc = psutil.Process(int(prop.value[0]))
            try:
                return Path(proc.exe()).name or proc.name()
            except psutil.Error:
                return proc.name()
        except Exception:
            return None


# ---- the hotkey tap -----------------------------------------------------------

_LOWER_OFFSET = ord("a") - ord("A")


def keysym_of(key) -> int | None:
    """The keysym a pynput key stands for, folded to the table's lower case
    (a shifted letter reports the capital's keysym)."""
    from pynput.keyboard import Key

    code = key.value if isinstance(key, Key) else key
    keysym = getattr(code, "vk", None)
    if keysym is None:
        return None
    if ord("A") <= keysym <= ord("Z"):
        keysym += _LOWER_OFFSET
    return keysym


class X11HotkeyTap:
    """pynput's XRecord listener: listen-only, press and release, no grant.
    The listener's own thread runs the callbacks; events seen while the
    keyboard's gate is up are our own sends and are reported as injected."""

    def __init__(self, gate: InjectionGate | None = None) -> None:
        self._gate = gate or InjectionGate()
        self._listener = None

    def start(self, on_event, chords: tuple[str, ...] = ()) -> None:
        if self._listener is not None:
            return
        # pynput resolves its backend at import and opens its connections on
        # its own thread; probing here is what makes a dead display or a
        # missing XRecord raise at the caller rather than die silently.
        probe = open_display()
        try:
            if not probe.has_extension("RECORD"):
                raise RuntimeError("the X server has no RECORD extension")
        finally:
            probe.close()
        from pynput import keyboard

        def forward(is_down: bool):
            def callback(key, injected: bool = False) -> None:
                keysym = keysym_of(key)
                if keysym is not None:
                    on_event(keysym, is_down, bool(injected) or self._gate.active())
            return callback

        self._listener = keyboard.Listener(on_press=forward(True),
                                           on_release=forward(False))
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def bindings(self) -> dict[str, str]:
        return {}

