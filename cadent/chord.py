"""Chord state machine for the push-to-talk hotkey — pure logic, no OS hook.

Implements the resolved "Hotkey capture mechanism" decisions:
- chord-down when every group has a key down; chord-up on the first keyup
- idempotent on auto-repeat keydowns
- injected events (our own SendInput) are ignored by the caller passing injected=True
- hold: sub-min-hold release discards; any non-chord keydown mid-hold cancels
  (the OS owns Ctrl+Win+<key> shortcuts)
- toggle: chord-down flip-flop, re-armed only after full chord release
- tap-or-hold: one chord, both grips. A press that starts recording latches if
  released before min-hold (a tap) and stops on release otherwise (a hold);
  any press while latched stops on release. Re-armed like toggle.
- MASK_MENU fires when the chord activates, so a non-chord key event sits between
  Win-down and Win-up and the Start menu never triggers on release
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .platform import KeycodeTable


class Action(Enum):
    START = auto()       # begin recording
    STOP = auto()        # end recording, run the pipeline
    DISCARD = auto()     # end recording, drop the audio (tap or cancellation)
    MASK_MENU = auto()   # inject the dummy mask VK (Start-menu suppression)
    CLEANUP_TOGGLE = auto()  # turn cleanup on or off (secondary tap chord)


def parse_combo(combo: str, table: KeycodeTable | None = None) -> list[frozenset[int]]:
    """Parse "<ctrl>+<cmd>" (or "<ctrl>+<alt>+f9"-style) into keycode groups.

    The keycode table is per-OS data — VK ints on Windows, Carbon ints on
    macOS, never mixed — so passing the other OS's table tests its chord
    logic anywhere. Defaults to the current platform's (spec §1.2).
    """
    if table is None:
        from . import platform

        table = platform.current().capabilities.keycode_table
    groups: list[frozenset[int]] = []
    for part in combo.lower().split("+"):
        part = part.strip()
        if not part:
            continue
        group = table.group_for(part)
        if group is None:
            raise ValueError(f"Unrecognized hotkey part: {part!r}")
        groups.append(group)
    if not groups:
        raise ValueError(f"Empty hotkey combo: {combo!r}")
    return groups


def describe_combo(combo: str, captions: Mapping[str, str] | None = None) -> str:
    """Render a stored chord for humans: "<ctrl>+<cmd>" → "Ctrl+Win", or
    "Ctrl+Cmd" where the captions say so — what a modifier is *called* is a
    platform fact (#166). Best-effort by design: display copy must not raise
    over a combo parse_combo already polices, so unknown parts just get
    uppercased rather than rejected.
    """
    if captions is None:
        from . import platform

        captions = platform.current().capabilities.modifier_captions
    parts = []
    for part in combo.lower().split("+"):
        part = part.strip().strip("<>")
        if not part:
            continue
        parts.append(_caption(part, captions))
    return "+".join(parts)


_SIDE_WORDS = {"l": "Left", "r": "Right"}


def _caption(part: str, captions: Mapping[str, str]) -> str:
    """One bare part's words. A sided part (`rctrl`, `ctrl_r`, #64) is the
    side word plus the platform's caption for the modifier it sides — the
    caption tables stay a list of modifiers, not of modifiers times sides."""
    if part in captions:
        return captions[part]
    for side, word in _SIDE_WORDS.items():
        base = None
        if part.startswith(side) and part[1:] in captions:
            base = part[1:]
        elif part.endswith(f"_{side}") and part[:-2] in captions:
            base = part[:-2]
        if base is not None:
            return f"{word} {captions[base]}"
    return part.upper()


@dataclass(frozen=True)
class Recorded:
    """What a finished recording came to: a chord in the stored syntax, or
    why the keys pressed cannot be one — in the user's words, for the pane."""

    chord: str | None = None
    problem: str | None = None


UNKNOWN_KEY = ("That key can't be part of a hotkey — use modifier keys with a "
               "letter, digit or function key.")
TWO_KEYS = "Hold one key with the modifiers, not two."
LONE_KEY = ("A letter or digit alone would fire while you type — hold a "
            "modifier with it, or use a function key.")


class ChordRecorder:
    """Turns the keys a user physically presses into stored chord syntax —
    parse_combo's inverse (#63). Feed it the tap's raw events; it answers on
    the release that leaves nothing held, with what those keys spell.

    Press order does not matter: modifiers come out in the table's canonical
    order with the key last, so recording Alt then Ctrl still writes
    "<ctrl>+<alt>". Both Ctrls pressed is one "<ctrl>". A key pressed before
    recording began and released during it is nobody's business.
    """

    def __init__(self, table: KeycodeTable, sided: bool = False) -> None:
        self._table = table
        self._sided = sided
        self._down: set[int] = set()
        self._pressed: dict[int, str | None] = {}    # press order, one entry per key

    def on_event(self, keycode: int, is_down: bool) -> Recorded | None:
        if is_down:
            self._down.add(keycode)
            self._pressed.setdefault(keycode, self._table.part_for(keycode, self._sided))
            return None
        self._down.discard(keycode)
        if self._down or not self._pressed:
            return None
        parts, self._pressed = list(dict.fromkeys(self._pressed.values())), {}
        return self._spell(parts)

    def _spell(self, parts: list[str | None]) -> Recorded:
        if None in parts:
            return Recorded(problem=UNKNOWN_KEY)
        ranked = [(self._table.modifier_rank(p), p) for p in parts]
        modifiers = sorted((r, p) for r, p in ranked if r is not None)
        keys = [p for r, p in ranked if r is None]
        if len(keys) > 1:
            return Recorded(problem=TWO_KEYS)
        if not modifiers and keys[0] not in self._table.function_keys:
            return Recorded(problem=LONE_KEY)
        return Recorded(chord="+".join([p for _r, p in modifiers] + keys))


class TapChord:
    """Fire-once tap chord (the cleanup toggle): fires on release of a clean tap.

    "Clean" means every chord group went down with nothing else pressed and no
    other key arrived while held — Ctrl+Shift+Alt+K stays some app's shortcut
    and never flips the toggle. Re-arms only after every chord key is released.
    """

    def __init__(self, combo: str) -> None:
        self._groups = parse_combo(combo)
        self._chord_vks = frozenset().union(*self._groups)
        self._down: set[int] = set()
        self._primed = False
        self._armed = True

    def _satisfied(self) -> bool:
        return all(group & self._down for group in self._groups)

    def on_event(self, vk: int, is_down: bool, injected: bool) -> bool:
        """Feed one keyboard event; True means the tap fired."""
        if injected:
            return False
        if is_down:
            if vk not in self._down:
                self._down.add(vk)
                if vk not in self._chord_vks or not self._down <= self._chord_vks:
                    self._primed = False
                elif self._armed and self._satisfied():
                    self._primed = True
                    self._armed = False
            return False
        self._down.discard(vk)
        fired = self._primed and vk in self._chord_vks and not self._satisfied()
        if fired:
            self._primed = False
        if not self._chord_vks & self._down:
            self._armed = True
        return fired


MODES = ("hold", "toggle", "tap_or_hold")


class ChordStateMachine:
    def __init__(self, combo: str, mode: str = "hold", min_hold_s: float = 0.2) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown hotkey mode: {mode!r}")
        self.mode = mode
        self.min_hold_s = min_hold_s
        self._groups = parse_combo(combo)
        self._chord_vks = frozenset().union(*self._groups)
        self._down: set[int] = set()
        self._active = False        # hold: recording; toggle: chord currently engaged
        self._toggled = False       # toggle: recording on
        self._armed = True          # toggle: chord fully released since last flip
        self._press_started = False  # tap-or-hold: this press began the recording
        self._start_t = 0.0

    def _satisfied(self) -> bool:
        return all(group & self._down for group in self._groups)

    def on_event(self, vk: int, is_down: bool, injected: bool, now: float) -> list[Action]:
        """Feed one keyboard event; returns the actions the caller must perform."""
        if injected:
            return []
        return self._on_down(vk, now) if is_down else self._on_up(vk, now)

    def _on_down(self, vk: int, now: float) -> list[Action]:
        if vk in self._down:          # auto-repeat
            return []
        self._down.add(vk)

        if vk not in self._chord_vks:
            # The OS owns chord+<key> shortcuts (Ctrl+Win+Arrow, …): cancel a held dictation.
            if self.mode == "hold" and self._active:
                self._active = False
                return [Action.DISCARD]
            if self.mode == "tap_or_hold" and self._active and self._press_started:
                # Still physically held, so it is a hold being cancelled — a
                # latched tap is deliberately immune (the user is typing).
                self._active = False
                self._toggled = False
                return [Action.DISCARD]
            return []

        if not self._satisfied():
            return []

        if self.mode == "hold":
            if not self._active:
                self._active = True
                self._start_t = now
                return [Action.MASK_MENU, Action.START]
            return []

        if not self._armed:
            return []
        self._armed = False

        if self.mode == "tap_or_hold":
            self._active = True
            self._start_t = now
            self._press_started = not self._toggled
            if self._toggled:
                return [Action.MASK_MENU]       # stops on release, tap or hold
            self._toggled = True
            return [Action.MASK_MENU, Action.START]

        # toggle
        if self._toggled:
            self._toggled = False
            return [Action.MASK_MENU, Action.STOP]
        self._toggled = True
        return [Action.MASK_MENU, Action.START]

    def _on_up(self, vk: int, now: float) -> list[Action]:
        self._down.discard(vk)
        actions: list[Action] = []

        if self.mode == "hold":
            if self._active and vk in self._chord_vks and not self._satisfied():
                self._active = False
                held = now - self._start_t
                actions.append(Action.DISCARD if held < self.min_hold_s else Action.STOP)
        elif self.mode == "tap_or_hold":
            if self._active and vk in self._chord_vks and not self._satisfied():
                self._active = False
                held = now - self._start_t
                if not self._press_started or held >= self.min_hold_s:
                    self._toggled = False
                    actions.append(Action.STOP)
                # else: a tap — recording stays latched until the next press
            if not (self._chord_vks & self._down):
                self._armed = True
        elif not (self._chord_vks & self._down):
            self._armed = True        # toggle: full release re-arms

        return actions
