"""The Whole tier: X11 fills for the input seams (spec M6 §3–§5, ADR 0007–0009).

Windows parity with no permission UX — every seam fills 1:1:

- **HotkeyTap**: pynput's XRecord listener, listen-only, press + release,
  sided modifiers as keysyms; no grant, and a missing X connection raises
  loudly at `start()` — nothing to poll.
- **KeyboardOutput**: XTEST. Characters the layout has are pressed on their
  keycode (Shift held for the shifted level); characters it lacks go through
  a *scratch keycode* temporarily remapped to the keysym. An XTEST error or
  an exhausted scratch keycode **raises** — a detectable failure, which is
  what makes `auto_learn_overrides=True` honest here.
- **Suppression is a state gate, not a per-event flag.** XTEST fakes arrive
  with `send_event` False, indistinguishable from the user's keys, so the
  keyboard closes a `SendGate` while sending and the tap flags every event
  during that window as injected — including the posted Ctrl+V.
- **Clipboard**: own the CLIPBOARD selection from a thread that serves
  `SelectionRequest`s (an X selection is a rendezvous, not a buffer) and
  counts XFixes `SelectionNotify` events as `sequence_number()`.
- **FocusedApp**: `_NET_ACTIVE_WINDOW` → `WM_CLASS` → desktop-file id via
  the `.desktop` index; `_NET_WM_PID` → executable basename as the fallback;
  the window rect for the overlay from the frame geometry.

`Xlib` loads only here (spec §2.5); nothing imports Qt.
"""

from __future__ import annotations

import logging
import os
import select
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .desktopfiles import DesktopIndex
from .keysyms import (
    ALT_L,
    ALT_R,
    CONTROL_L,
    CONTROL_R,
    SHIFT_L,
    SHIFT_R,
    SUPER_L,
    SUPER_R,
    keysyms_for_text,
)

log = logging.getLogger(__name__)

NO_SYMBOL = 0
UNKNOWN = "unknown"


# ---- the state gate ----------------------------------------------------------

class SendGate:
    """Closed while `KeyboardOutput` is mid-send. The tap reads it: every
    event seen while closed is reported injected, because on X11 nothing on
    the event says so (spec §3). Re-entrant — a chord inside a text send is
    one closed window — and it **lingers** `LINGER_S` after the last send:
    XRecord delivers our fakes to the tap's thread a beat after `sync()`
    returns, and a gate that reopened first would let the tail of a paste
    chord re-enter the chord machine."""

    LINGER_S = 0.08

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._depth = 0
        self._lock = threading.Lock()
        self._clock = clock
        self._open_after = 0.0

    @property
    def closed(self) -> bool:
        return self._depth > 0 or self._clock() < self._open_after

    def __enter__(self) -> SendGate:
        with self._lock:
            self._depth += 1
        return self

    def __exit__(self, *_exc) -> None:
        with self._lock:
            self._depth = max(0, self._depth - 1)
            self._open_after = self._clock() + self.LINGER_S


# ---- keymap arithmetic (pure) --------------------------------------------------

def find_scratch_keycode(mapping: list[list[int]], min_keycode: int) -> int | None:
    """The highest keycode whose whole row is NoSymbol — free to borrow for
    a character the layout lacks. None when every keycode is spoken for
    (spec §3: an exhausted scratch keycode raises)."""
    for offset in range(len(mapping) - 1, -1, -1):
        if all(sym == NO_SYMBOL for sym in mapping[offset]):
            return min_keycode + offset
    return None


def keycode_for(keysym: int,
                candidates: list[tuple[int, int]]) -> tuple[int, bool] | None:
    """(keycode, needs_shift) for the first candidate at level 1 or 2 of
    group 1 — index 0 is unshifted, 1 is shifted; anything higher needs a
    group/level modifier this path does not press, so it counts as absent."""
    best: tuple[int, bool] | None = None
    for keycode, index in candidates:
        if index == 0:
            return keycode, False
        if index == 1 and best is None:
            best = (keycode, True)
    return best


# ---- keyboard output: XTEST ----------------------------------------------------

class X11Keyboard:
    """XTEST typing and chords on a private Display, opened on first use
    (typing runs on the pipeline worker; nothing here touches the GUI
    thread). Raises on any XTEST/keymap failure — the detectable failure
    the injector's fall-through and auto-learn read."""

    def __init__(self, gate: SendGate, display_factory: Callable | None = None) -> None:
        self._gate = gate
        self._factory = display_factory
        self._display = None
        self._lock = threading.Lock()
        self._scratch: int | None = None
        self._scratch_keysym: int | None = None
        self._syms_per_code = 1
        self._min_keycode = 8

    # ---- display ------------------------------------------------------------

    def _open(self):
        if self._display is None:
            if self._factory is not None:
                self._display = self._factory()
            else:
                from Xlib import display as xdisplay
                from Xlib.ext import xtest  # noqa: F401  (registers the extension)

                self._display = xdisplay.Display()
            self._load_keymap()
        return self._display

    def _load_keymap(self) -> None:
        d = self._display
        self._min_keycode = d.display.info.min_keycode
        max_keycode = d.display.info.max_keycode
        count = max_keycode - self._min_keycode + 1
        mapping = d.get_keyboard_mapping(self._min_keycode, count)
        self._mapping = [list(row) for row in mapping]
        self._syms_per_code = len(self._mapping[0]) if self._mapping else 1
        self._scratch = find_scratch_keycode(self._mapping, self._min_keycode)
        self._scratch_keysym = None

    # ---- the seam -----------------------------------------------------------

    def send_text_units(self, units: list[int]) -> bool:
        raw = b"".join(u.to_bytes(2, "little") for u in units)
        text = raw.decode("utf-16-le", errors="replace")
        with self._lock, self._gate:
            d = self._open()
            try:
                for keysym in keysyms_for_text(text):
                    self._type_keysym(d, keysym)
                d.sync()
            finally:
                self._release_scratch(d)
        return True

    def send_chord(self, keys: list[int]) -> None:
        """Press keysyms in order, release in reverse — the paste chord."""
        from Xlib import X

        with self._lock, self._gate:
            d = self._open()
            codes = []
            for keysym in keys:
                found = keycode_for(keysym, list(d.keysym_to_keycodes(keysym)))
                if found is None:
                    raise RuntimeError(f"keysym {keysym:#x} has no keycode in this layout")
                codes.append(found[0])
            for code in codes:
                self._fake(d, X.KeyPress, code)
            for code in reversed(codes):
                self._fake(d, X.KeyRelease, code)
            d.sync()

    def send_mask_key(self) -> None:
        """No-op on Linux for v1 (spec §4); whether a bare Super pops a
        launcher mid-chord is a hardware item (§12)."""

    def modifiers_down(self) -> bool:
        with self._lock:
            d = self._open()
            keymap = d.query_keymap()
            for keysym in (SHIFT_L, SHIFT_R, CONTROL_L, CONTROL_R, ALT_L, ALT_R,
                           SUPER_L, SUPER_R):
                for keycode, _index in d.keysym_to_keycodes(keysym):
                    if keymap[keycode // 8] & (1 << (keycode % 8)):
                        return True
            return False

    # ---- internals ----------------------------------------------------------

    def _fake(self, d, kind: int, keycode: int) -> None:
        from Xlib.ext import xtest

        xtest.fake_input(d, kind, keycode)

    def _type_keysym(self, d, keysym: int) -> None:
        from Xlib import X

        found = keycode_for(keysym, list(d.keysym_to_keycodes(keysym)))
        if found is not None:
            keycode, shifted = found
            shift = None
            if shifted:
                shift_found = keycode_for(SHIFT_L, list(d.keysym_to_keycodes(SHIFT_L)))
                shift = shift_found[0] if shift_found else None
                if shift is not None:
                    self._fake(d, X.KeyPress, shift)
            self._fake(d, X.KeyPress, keycode)
            self._fake(d, X.KeyRelease, keycode)
            if shift is not None:
                self._fake(d, X.KeyRelease, shift)
            return
        # Out of the layout: borrow the scratch keycode.
        keycode = self._borrow_scratch(d, keysym)
        self._fake(d, X.KeyPress, keycode)
        self._fake(d, X.KeyRelease, keycode)
        d.sync()

    def _borrow_scratch(self, d, keysym: int) -> int:
        if self._scratch is None:
            raise RuntimeError("no free keycode to type a character the layout lacks")
        if self._scratch_keysym != keysym:
            d.change_keyboard_mapping(self._scratch,
                                      [[keysym] * self._syms_per_code])
            d.sync()
            # The server broadcasts MappingNotify; give clients a beat to
            # refresh before the key lands on the old (empty) mapping.
            time.sleep(0.005)
            self._scratch_keysym = keysym
        return self._scratch

    def _release_scratch(self, d) -> None:
        if self._scratch is not None and self._scratch_keysym is not None:
            try:
                d.change_keyboard_mapping(self._scratch,
                                          [[NO_SYMBOL] * self._syms_per_code])
                d.sync()
            except Exception:
                log.debug("restoring the scratch keycode failed", exc_info=True)
            self._scratch_keysym = None


# ---- clipboard: selection ownership on its own thread ---------------------------

_TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "STRING")
_KDE_HINT = "x-kde-passwordManagerHint"     # Klipper: value "secret" = don't keep


class X11Clipboard:
    """CLIPBOARD ownership served from `cadent-x11-clipboard`.

    One Display, one thread: the loop `select()`s on the X socket and a
    wake-up pipe, so other threads never touch the Display — they post a
    command and wait on its result. `set_text` takes ownership and the loop
    answers `SelectionRequest`s with the text (plus Klipper's hint when
    history exclusion is asked); `get_text` asks the current owner via
    `ConvertSelection` and reads the reply property; `sequence_number()` is
    the XFixes `SelectionNotify` count for CLIPBOARD, bumped on every owner
    change — ours or anyone's — guarding the restore (spec §3)."""

    def __init__(self, display_factory: Callable | None = None) -> None:
        self._factory = display_factory
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._commands: list[tuple[str, tuple, _Result]] = []
        self._wake_r, self._wake_w = os.pipe()
        self._seq = 0
        self._text: str | None = None
        self._exclude = False
        self._pending_get: _Result | None = None
        self._display = None
        self._window = None
        self._atoms: dict[str, int] = {}

    # ---- the seam -----------------------------------------------------------

    def get_text(self) -> str | None:
        return self._run("get", (), timeout=1.5)

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        self._run("set", (text, exclude_from_history), timeout=1.5)

    def sequence_number(self) -> int:
        self._ensure_thread()
        return self._seq

    # ---- command plumbing ---------------------------------------------------

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready = threading.Event()
            self._error: BaseException | None = None
            self._thread = threading.Thread(target=self._loop,
                                            name="cadent-x11-clipboard", daemon=True)
            self._thread.start()
        if not self._ready.wait(2.0):
            raise RuntimeError("X11 clipboard thread did not start")
        if self._error is not None:
            raise RuntimeError(f"X11 clipboard unavailable: {self._error}")

    def _run(self, kind: str, args: tuple, timeout: float):
        self._ensure_thread()
        result = _Result()
        with self._lock:
            self._commands.append((kind, args, result))
        os.write(self._wake_w, b"x")
        if not result.event.wait(timeout):
            raise TimeoutError(f"X11 clipboard {kind} timed out")
        if result.error is not None:
            raise result.error
        return result.value

    # ---- the thread ---------------------------------------------------------

    def _loop(self) -> None:
        try:
            self._open()
        except BaseException as exc:  # reported to the waiter, thread ends
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        d = self._display
        fd = d.fileno()
        while True:
            try:
                readable, _w, _x = select.select([fd, self._wake_r], [], [])
            except (OSError, ValueError):
                return
            if self._wake_r in readable:
                os.read(self._wake_r, 64)
                self._drain_commands()
            if fd in readable:
                self._pump()

    def _open(self) -> None:
        from Xlib import X
        from Xlib import display as xdisplay
        from Xlib.ext import xfixes

        d = self._factory() if self._factory else xdisplay.Display()
        self._display = d
        self._xfixes_counts = False
        root = d.screen().root
        self._window = root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
        for name in ("CLIPBOARD", "TARGETS", "INCR", "CADENT_SELECTION", _KDE_HINT,
                     *_TEXT_TARGETS):
            self._atoms[name] = d.intern_atom(name)
        # XFixes: one event per ownership change of CLIPBOARD.
        try:
            d.xfixes_query_version()
            d.xfixes_select_selection_input(
                self._window, self._atoms["CLIPBOARD"],
                xfixes.XFixesSetSelectionOwnerNotifyMask)
        except Exception:
            log.debug("XFixes selection events unavailable; sequence_number "
                      "will only move on our own writes", exc_info=True)
        else:
            self._xfixes_counts = True
        d.flush()

    def _drain_commands(self) -> None:
        with self._lock:
            commands, self._commands = self._commands, []
        for kind, args, result in commands:
            try:
                if kind == "set":
                    self._do_set(*args)
                    result.set(None)
                elif kind == "get":
                    self._start_get(result)
            except BaseException as exc:
                result.fail(exc)

    def _do_set(self, text: str, exclude: bool) -> None:
        from Xlib import X

        self._text = text
        self._exclude = exclude
        self._window.set_selection_owner(self._atoms["CLIPBOARD"], X.CurrentTime)
        self._display.flush()
        if self._display.get_selection_owner(self._atoms["CLIPBOARD"]) != self._window:
            raise RuntimeError("could not take CLIPBOARD ownership")
        if not self._xfixes_counts:
            self._seq += 1      # no XFixes: at least our own writes move it

    def _start_get(self, result: _Result) -> None:
        from Xlib import X

        # Answer from our own buffer while we own the selection — the X
        # round trip would land back on this very thread.
        if self._display.get_selection_owner(self._atoms["CLIPBOARD"]) == self._window:
            result.set(self._text)
            return
        if self._display.get_selection_owner(self._atoms["CLIPBOARD"]) == X.NONE:
            result.set(None)
            return
        self._pending_get = result
        self._window.convert_selection(self._atoms["CLIPBOARD"],
                                       self._atoms["UTF8_STRING"],
                                       self._atoms["CADENT_SELECTION"], X.CurrentTime)
        self._display.flush()

    def _pump(self) -> None:
        d = self._display
        while d.pending_events():
            event = d.next_event()
            self._handle(event)

    def _handle(self, event) -> None:
        from Xlib import X
        from Xlib.ext import xfixes

        etype = getattr(event, "type", None)
        if etype == X.SelectionRequest:
            self._answer_request(event)
        elif etype == X.SelectionClear:
            self._text = None       # someone else owns it now
        elif etype == X.SelectionNotify:
            self._finish_get(event)
        elif isinstance(event, xfixes.SelectionNotify) or \
                getattr(event, "subcode", None) is not None and \
                type(event).__name__ == "SelectionNotify":
            self._seq += 1

    def _answer_request(self, event) -> None:
        from Xlib import X
        from Xlib.protocol import event as xevent

        d = self._display
        atoms = self._atoms
        target = event.target
        prop = event.property if event.property != X.NONE else event.target
        ok = False
        if self._text is not None:
            if target == atoms["TARGETS"]:
                targets = [atoms["TARGETS"], *(atoms[t] for t in _TEXT_TARGETS)]
                if self._exclude:
                    targets.append(atoms[_KDE_HINT])
                event.requestor.change_property(prop, d.intern_atom("ATOM"), 32, targets)
                ok = True
            elif target in (atoms[t] for t in _TEXT_TARGETS):
                encoding = "utf-8" if target != atoms["STRING"] else "latin-1"
                data = self._text.encode(encoding, errors="replace")
                event.requestor.change_property(prop, target, 8, data)
                ok = True
            elif target == atoms[_KDE_HINT] and self._exclude:
                event.requestor.change_property(prop, atoms["UTF8_STRING"], 8, b"secret")
                ok = True
        notify = xevent.SelectionNotify(
            time=event.time, requestor=event.requestor, selection=event.selection,
            target=target, property=prop if ok else X.NONE)
        event.requestor.send_event(notify)
        d.flush()

    def _finish_get(self, event) -> None:
        from Xlib import X

        result, self._pending_get = self._pending_get, None
        if result is None:
            return
        try:
            if event.property == X.NONE:
                result.set(None)
                return
            reply = self._window.get_full_property(event.property, X.AnyPropertyType)
            self._window.delete_property(event.property)
            if reply is None or reply.property_type == self._atoms["INCR"]:
                result.set(None)         # incremental transfers: too big to restore
                return
            value = reply.value
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            result.set(str(value))
        except BaseException as exc:
            result.fail(exc)


class _Result:
    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value = None
        self.error: BaseException | None = None

    def set(self, value) -> None:
        self.value = value
        self.event.set()

    def fail(self, error: BaseException) -> None:
        self.error = error
        self.event.set()


# ---- focused app: _NET_ACTIVE_WINDOW + the .desktop index -------------------------

class X11FocusedApp:
    """Identity per ADR 0009: `WM_CLASS` → desktop-file id → executable
    basename (`_NET_WM_PID`) → "unknown". The window rect (frame included)
    places the overlay. No preflight, no secure-input analogue: `permission
    _granted()` is True and `injection_blocked()` None on every X11 desktop."""

    def __init__(self, index: DesktopIndex | None = None,
                 display_factory: Callable | None = None) -> None:
        self._index = index or DesktopIndex()
        self._factory = display_factory
        self._display = None
        self._lock = threading.Lock()

    def _open(self):
        if self._display is None:
            from Xlib import display as xdisplay

            self._display = self._factory() if self._factory else xdisplay.Display()
        return self._display

    def _active_window(self):
        from Xlib import X

        d = self._open()
        root = d.screen().root
        prop = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"),
                                      X.AnyPropertyType)
        if prop is None or not prop.value or not prop.value[0]:
            return None
        return d.create_resource_object("window", int(prop.value[0]))

    def name(self) -> str:
        try:
            with self._lock:
                window = self._active_window()
                if window is None:
                    return UNKNOWN
                wm_class = window.get_wm_class() or ()
                identity = self._index.id_for_wm_class(wm_class)
                if identity:
                    return identity
                exe = self._exe_basename(window)
                return exe or UNKNOWN
        except Exception:
            log.debug("active-window identity probe failed", exc_info=True)
            return UNKNOWN

    def _exe_basename(self, window) -> str | None:
        from Xlib import X

        d = self._display
        prop = window.get_full_property(d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
        if prop is None or not prop.value:
            return None
        pid = int(prop.value[0])
        try:
            return Path(os.readlink(f"/proc/{pid}/exe")).name or None
        except OSError:
            try:
                return Path(f"/proc/{pid}/comm").read_text().strip() or None
            except OSError:
                return None

    def window_rect(self) -> tuple[int, int, int, int] | None:
        try:
            with self._lock:
                window = self._active_window()
                if window is None:
                    return None
                d = self._display
                geo = window.get_geometry()
                root = d.screen().root
                origin = root.translate_coords(window, 0, 0)
                left, top = int(origin.x), int(origin.y)
                right, bottom = left + int(geo.width), top + int(geo.height)
                from Xlib import X

                extents = window.get_full_property(d.intern_atom("_NET_FRAME_EXTENTS"),
                                                   X.AnyPropertyType)
                if extents is not None and len(extents.value) >= 4:
                    fl, fr, ft, fb = (int(v) for v in extents.value[:4])
                    left, right, top, bottom = left - fl, right + fr, top - ft, bottom + fb
                return (left, top, right, bottom)
        except Exception:
            log.debug("active-window rect probe failed", exc_info=True)
            return None

    def injection_blocked(self) -> str | None:
        return None

    def permission_granted(self) -> bool:
        return True

    def running_apps(self) -> list[tuple[str, str]]:
        return self._index.installed_apps()

    def display_name(self, identity: str) -> str | None:
        return self._index.display_name(identity)


# ---- hotkey tap: pynput XRecord, keysyms out ---------------------------------------

def _pynput_keysym(key) -> int | None:
    """The keysym behind a pynput key: `Key` members carry a `KeyCode` in
    `.value`, plain keys are `KeyCode`s; either way `.vk` is the keysym on
    the Xorg backend. Uppercase Latin letters normalize to lowercase so a
    shifted C still satisfies "c" in a chord."""
    if key is None:
        return None
    keycode = getattr(key, "value", key)
    keysym = getattr(keycode, "vk", None)
    if keysym is None:
        return None
    if 0x41 <= keysym <= 0x5A:
        keysym += 0x20
    return keysym


class X11HotkeyTap:
    """A listen-only XRecord tap. Every event during a `SendGate` window is
    reported injected — the state gate of spec §3 — because XTEST fakes
    carry no marker of their own. `chords` is ignored: XRecord sees the
    whole keyboard."""

    def __init__(self, gate: SendGate) -> None:
        self._gate = gate
        self._listener = None

    def start(self, on_event, chords=()) -> None:
        if self._listener is not None:
            return
        # Fail loudly here rather than silently inside pynput's thread: no
        # DISPLAY / no X server is an exception the caller sees.
        from Xlib import display as xdisplay

        xdisplay.Display().close()
        from pynput import keyboard

        gate = self._gate

        def forward(is_down: bool):
            def callback(key, injected: bool = False) -> None:
                keysym = _pynput_keysym(key)
                if keysym is not None:
                    on_event(keysym, is_down, bool(injected) or gate.closed)
            return callback

        self._listener = keyboard.Listener(on_press=forward(True),
                                           on_release=forward(False))
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def bound_shortcuts(self):
        return None    # the hook sees the whole keyboard; nobody else binds

    def available(self) -> bool:
        return True