"""Typing through `zwp_virtual_keyboard_v1` on the wlroots family (spec §2.3, §3;
ADR 0007).

The client brings its own keymap, so any character types without a dialog and
without caring what layout the user has: each call uploads a keymap holding
just the keysyms it needs, one key apiece, then presses those keys. The paste
chord rides the same mechanism, with the modifier state sent explicitly —
a virtual keyboard's modifier keys do not move it on their own.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from . import wlclient
from .input import KEYSYM_RETURN, KEYSYM_TAB, KEYSYM_UNICODE_BASE, codepoints, keysym_for

log = logging.getLogger(__name__)

MANAGER = "zwp_virtual_keyboard_manager_v1"
SEAT = "wl_seat"

MGR_CREATE_KEYBOARD = 0
KB_KEYMAP, KB_KEY, KB_MODIFIERS = 0, 1, 2
KEYMAP_FORMAT_XKB_V1 = 1
KEY_RELEASED, KEY_PRESSED = 0, 1

KEYCODE_BASE = 9                    # the first xkb keycode we assign
MAX_KEYS = 200                      # distinct keysyms per uploaded keymap

# keysym -> (xkb name, real modifier it stands for, mask bit)
MODIFIERS = {
    0xFFE1: ("Shift_L", "Shift", 0x01), 0xFFE2: ("Shift_R", "Shift", 0x01),
    0xFFE3: ("Control_L", "Control", 0x04), 0xFFE4: ("Control_R", "Control", 0x04),
    0xFFE9: ("Alt_L", "Mod1", 0x08), 0xFFEA: ("Alt_R", "Mod1", 0x08),
    0xFFEB: ("Super_L", "Mod4", 0x40), 0xFFEC: ("Super_R", "Mod4", 0x40),
}
NAMED = {
    KEYSYM_RETURN: "Return", KEYSYM_TAB: "Tab", 0xFF08: "BackSpace",
    0xFF1B: "Escape", 0xFFFF: "Delete", 0xFF50: "Home", 0xFF51: "Left",
    0xFF52: "Up", 0xFF53: "Right", 0xFF54: "Down", 0xFF55: "Prior",
    0xFF56: "Next", 0xFF57: "End", 0xFF63: "Insert",
    **{0xFFBE + n: f"F{n + 1}" for n in range(12)},
    **{keysym: name for keysym, (name, _, _) in MODIFIERS.items()},
}


def keysym_name(keysym: int) -> str | None:
    """How a keymap file spells `keysym`; None for one it has no spelling for."""
    if keysym in NAMED:
        return NAMED[keysym]
    if keysym >= KEYSYM_UNICODE_BASE:
        return f"U{keysym - KEYSYM_UNICODE_BASE:04X}"
    if 0x20 <= keysym < 0x7F or 0xA0 <= keysym < 0x100:
        return f"U{keysym:04X}"
    return None


def build_keymap(keysyms: list[int]) -> str:
    """An XKB keymap giving each keysym its own key, `KEYCODE_BASE` upward.
    The `key` request takes the evdev code, which is the xkb one minus eight."""
    names = [keysym_name(k) for k in keysyms]
    codes = [f"<K{i}> = {KEYCODE_BASE + i};" for i in range(len(keysyms))]
    keys = [f"key <K{i}> {{ [ {name} ] }};"
            for i, name in enumerate(names) if name]
    mods = [f"modifier_map {MODIFIERS[k][1]} {{ <K{i}> }};"
            for i, k in enumerate(keysyms) if k in MODIFIERS]
    return "\n".join([
        "xkb_keymap {",
        f'xkb_keycodes "cadent" {{ minimum = 8; maximum = {KEYCODE_BASE + len(keysyms)};',
        *codes, "};",
        'xkb_types "cadent" { include "complete" };',
        'xkb_compat "cadent" { include "complete" };',
        'xkb_symbols "cadent" {', *keys, *mods, "};",
        "};", ""])


def evdev_code(index: int) -> int:
    return KEYCODE_BASE + index - 8


def available(conn: wlclient.Connection) -> bool:
    return conn.offers(MANAGER) is not None and conn.offers(SEAT) is not None


class VirtualKeyboard:
    def __init__(self, conn: wlclient.Connection) -> None:
        manager = conn.bind(MANAGER, 1)
        seat = conn.bind(SEAT, 1)
        if manager is None or seat is None:
            raise ValueError("the compositor offers no virtual keyboard")
        self._conn = conn
        self._lock = threading.Lock()
        self._keyboard = conn.new_id()
        self._loaded: tuple[int, ...] = ()
        conn.send(manager, MGR_CREATE_KEYBOARD, seat, self._keyboard)

    def send_text_units(self, units: list[int]) -> bool:
        keysyms = [keysym_for(p) for p in codepoints(units)]
        try:
            with self._lock:
                for start in range(0, len(keysyms), MAX_KEYS):
                    self._type(keysyms[start:start + MAX_KEYS])
        except OSError as exc:
            log.warning("typing through the virtual keyboard failed: %s", exc)
            return False
        return True

    def send_chord(self, keys: list[int]) -> None:
        try:
            with self._lock:
                self._chord(keys)
        except OSError as exc:
            log.warning("the paste chord could not be sent: %s", exc)

    # ---- the wire -------------------------------------------------------

    def _type(self, keysyms: list[int]) -> None:
        distinct = list(dict.fromkeys(keysyms))
        self._load(distinct)
        for keysym in keysyms:
            code = evdev_code(distinct.index(keysym))
            self._key(code, KEY_PRESSED)
            self._key(code, KEY_RELEASED)

    def _chord(self, keys: list[int]) -> None:
        distinct = list(dict.fromkeys(keys))
        self._load(distinct)
        mask = 0
        for keysym in keys:
            if keysym in MODIFIERS:
                mask |= MODIFIERS[keysym][2]
                self._conn.send(self._keyboard, KB_MODIFIERS, mask, 0, 0, 0)
            self._key(evdev_code(distinct.index(keysym)), KEY_PRESSED)
        for keysym in reversed(keys):
            self._key(evdev_code(distinct.index(keysym)), KEY_RELEASED)
            if keysym in MODIFIERS:
                mask &= ~MODIFIERS[keysym][2]
                self._conn.send(self._keyboard, KB_MODIFIERS, mask, 0, 0, 0)

    def _key(self, code: int, state: int) -> None:
        millis = int(time.monotonic() * 1000) & 0xFFFFFFFF
        self._conn.send(self._keyboard, KB_KEY, millis, code, state)

    def _load(self, keysyms: list[int]) -> None:
        """Upload the keymap for `keysyms` unless it is the one already
        loaded, and wait for the compositor to have applied it."""
        if tuple(keysyms) == self._loaded:
            return
        text = build_keymap(keysyms).encode("ascii")
        fd = os.memfd_create("cadent-keymap")
        try:
            os.write(fd, text)
            # The fd is not in the byte stream: only format and size are.
            self._conn.send(self._keyboard, KB_KEYMAP, KEYMAP_FORMAT_XKB_V1,
                            len(text), fds=(fd,))
        finally:
            os.close(fd)
        self._loaded = tuple(keysyms)
        self._conn.roundtrip()
