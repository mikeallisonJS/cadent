"""Keycode tables as pure data, importable on any OS.

The table for an OS is that OS's fact, but a dict of ints has no OS imports —
keeping the tables here (rather than inside the per-OS adapter modules the
factory alone may import) is what lets either OS's chord logic test anywhere,
which spec §1.2 asks for by name.
"""

from __future__ import annotations

from .base import KeycodeTable

# Virtual-key groups: a chord part matches if any VK in its group is down.
_VK_GROUPS: dict[str, frozenset[int]] = {
    "<ctrl>": frozenset({0x11, 0xA2, 0xA3}),    # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
    "<shift>": frozenset({0x10, 0xA0, 0xA1}),
    "<alt>": frozenset({0x12, 0xA4, 0xA5}),
    "<cmd>": frozenset({0x5B, 0x5C}),           # VK_LWIN, VK_RWIN
}

# What a synthetic chord presses for each modifier name (SendInput wants the
# generic VK, not the sided ones).
_VK_MODIFIERS: dict[str, int] = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}

WIN32_KEYCODES = KeycodeTable(
    groups=_VK_GROUPS,
    modifiers=_VK_MODIFIERS,
    # A-Z and 0-9 VKs are their ASCII uppercase codes.
    chars={c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
    # F1–F24 is the whole VK F-key range (0x70–0x87). The pre-seam parser
    # accepted any f<digits> and mapped f25+ onto unrelated VKs — a deliberate
    # narrowing: garbage now raises at parse like any other unknown part.
    function_keys={f"f{n}": 0x70 + n - 1 for n in range(1, 25)},
    ord_fallback=True,
)

# Carbon/CGEvent codes (HIToolbox Events.h, kVK_ANSI_*): layout positions on
# the ANSI keyboard, not character codes — hence no ord_fallback, ever; an
# unknown part must drop rather than press whatever key ord() lands on.
# #144 shipped modifiers + letters/digits for paste chords and the modifier
# *groups* for the default combos; #145 added the function keys and the
# DarwinHotkeyTap that feeds these groups real events.
_CARBON_CHARS: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16,
    "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "5": 22, "6": 23, "9": 25,
    "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37,
    "j": 38, "k": 40, "n": 45, "m": 46,
}

# What a synthetic chord presses: the left-hand variants, matching how
# CGEventCreateKeyboardEvent expects a concrete key. "option" and "win" are
# accepted spellings — hand-edited configs cross OSes with their users.
_CARBON_MODIFIERS: dict[str, int] = {
    "cmd": 55, "win": 55, "shift": 56, "alt": 58, "option": 58, "ctrl": 59,
}

# Carbon F-key codes (kVK_F1…kVK_F20) are scattered, not a contiguous range
# like the VK F-block — and F21+ simply has no Carbon code, so it raises at
# parse like any other unknown part instead of landing on an unrelated key.
_CARBON_FUNCTION_KEYS: dict[str, int] = {
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105,
    "f14": 107, "f15": 113, "f16": 106, "f17": 64, "f18": 79, "f19": 80,
    "f20": 90,
}

# Both sides of each modifier, mirroring the VK groups: a chord part matches
# if either the left or right key is down.
_CARBON_GROUPS: dict[str, frozenset[int]] = {
    "<ctrl>": frozenset({59, 62}),
    "<shift>": frozenset({56, 60}),
    "<alt>": frozenset({58, 61}),
    "<cmd>": frozenset({55, 54}),
}

DARWIN_KEYCODES = KeycodeTable(
    groups=_CARBON_GROUPS,
    modifiers=_CARBON_MODIFIERS,
    chars=_CARBON_CHARS,
    function_keys=_CARBON_FUNCTION_KEYS,
    ord_fallback=False,
)
