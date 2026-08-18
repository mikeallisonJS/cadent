"""Keysym arithmetic shared by the X11 and portal typing paths — pure data,
no display, no bus (ADR 0008: one keysym namespace for all three tiers)."""

from __future__ import annotations

# The X11 modifier keysyms (X11/keysymdef.h) the chord tables name.
SHIFT_L, SHIFT_R = 0xFFE1, 0xFFE2
CONTROL_L, CONTROL_R = 0xFFE3, 0xFFE4
ALT_L, ALT_R = 0xFFE9, 0xFFEA
SUPER_L, SUPER_R = 0xFFEB, 0xFFEC
META_L, META_R = 0xFFE7, 0xFFE8
MODIFIER_KEYSYMS = frozenset({SHIFT_L, SHIFT_R, CONTROL_L, CONTROL_R, ALT_L,
                              ALT_R, SUPER_L, SUPER_R, META_L, META_R})

# A few named keysyms the typing path prefers over their unicode aliases —
# apps treat Return and Tab as keys, not characters, and 0x0100000A would
# insert nothing in most toolkits.
_NAMED: dict[int, int] = {
    0x0A: 0xFF0D,     # newline → Return
    0x0D: 0xFF0D,     # carriage return → Return
    0x09: 0xFF09,     # tab → Tab
    0x08: 0xFF08,     # backspace → BackSpace
    0x1B: 0xFF1B,     # escape → Escape
}


def unicode_keysym(codepoint: int) -> int:
    """The keysym for a character: Latin-1 keysyms are their code points, a
    handful of controls have named keys, and everything else lives at
    `0x01000000 + code point` (xkbcommon's rule, honoured by X servers and
    the RemoteDesktop portal alike)."""
    if codepoint in _NAMED:
        return _NAMED[codepoint]
    if 0x20 <= codepoint <= 0x7E or 0xA0 <= codepoint <= 0xFF:
        return codepoint
    return 0x01000000 + codepoint


def keysyms_for_text(text: str) -> list[int]:
    """One keysym per code point (surrogate pairs already joined by `str`)."""
    return [unicode_keysym(ord(ch)) for ch in text]
