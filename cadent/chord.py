"""Chord state machine for the push-to-talk hotkey — pure logic, no OS hook.

Implements the resolved "Hotkey capture mechanism" decisions:
- chord-down when every group has a key down; chord-up on the first keyup
- idempotent on auto-repeat keydowns
- injected events (our own SendInput) are ignored by the caller passing injected=True
- hold: sub-min-hold release discards; any non-chord keydown mid-hold cancels
  (the OS owns Ctrl+Win+<key> shortcuts)
- toggle: chord-down flip-flop, re-armed only after full chord release
- MASK_MENU fires when the chord activates, so a non-chord key event sits between
  Win-down and Win-up and the Start menu never triggers on release
"""

from __future__ import annotations

from collections.abc import Mapping
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
        parts.append(captions.get(part, part.upper()))
    return "+".join(parts)


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


class ChordStateMachine:
    def __init__(self, combo: str, mode: str = "hold", min_hold_s: float = 0.2) -> None:
        if mode not in ("hold", "toggle"):
            raise ValueError(f"Unknown hotkey mode: {mode!r}")
        self.mode = mode
        self.min_hold_s = min_hold_s
        self._groups = parse_combo(combo)
        self._chord_vks = frozenset().union(*self._groups)
        self._down: set[int] = set()
        self._active = False        # hold: recording; toggle: chord currently engaged
        self._toggled = False       # toggle: recording on
        self._armed = True          # toggle: chord fully released since last flip
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
            return []

        if not self._satisfied():
            return []

        if self.mode == "hold":
            if not self._active:
                self._active = True
                self._start_t = now
                return [Action.MASK_MENU, Action.START]
            return []

        # toggle
        if not self._armed:
            return []
        self._armed = False
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
        elif not (self._chord_vks & self._down):
            self._armed = True        # toggle: full release re-arms

        return actions
