"""What the overlay shows, as pure logic (spec §4.3, §4.4, §4.8).

Separated from `overlay.py` so the seven states, the hide-toggle rule and the
minimum-visible floor can be driven without a screen. `overlay.py` renders
whatever this hands it and owns nothing about *which* state is right.

Two rules carry most of the weight:

- **Shape carries mode, colour carries state.** The leading glyph is the mode,
  always positively rendered — so the pill answers "which mode am I in?"
  *before* you speak, rather than by whether cleanup happened. Violet is
  activity, `hud_danger` is failure, `hud_muted` is a non-event.
- **The pill carries the terse failure; the toast carries the detail.** The
  pill is the *primary* channel because Windows 11 Do Not Disturb silently
  suppresses tray toasts — and turns itself on during presentations and
  full-screen games, exactly when you'd most notice text not appearing.
  Nothing suppresses our own always-on-top window.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

# Glyph slots. The state-aware leading glyph is why Cancelled and Paused look
# different in *size* as well as text: they collapse to a short label in a
# small neutral pill, and that difference is itself a signal.
MIC, CLEANUP, WARNING = "mic", "cleanup", "warning"

# Label styles, matching the QSS object names in theme/qss.py.
PLAIN, MUTED, DANGER = "overlayLabel", "overlayLabelMuted", "overlayLabelDanger"

# Meter modes. The meter *morphs* rather than swapping: recording's bars stop
# tracking your voice and become an indeterminate sweep in the same geometry,
# so recording -> transcribing feels like one continuous act.
VOICE, INDETERMINATE = "voice", "indeterminate"

HIDDEN = "hidden"
RECORDING = "recording"
TRANSCRIBING = "transcribing"
CLEANING = "cleaning"
CANCELLED = "cancelled"
PAUSED = "paused"
FAILURE = "failure"

# The states the "Show overlay while dictating" toggle governs. Failure is
# deliberately absent: the setting turns off the ambient indicator, not the
# alarm. Otherwise overlay-off plus Do Not Disturb would fail dictations into
# total silence and the app would read as broken.
ACTIVITY_STATES = frozenset({RECORDING, TRANSCRIBING, CLEANING, CANCELLED, PAUSED})


# The meter's range (§4.5). Today's `min(1.0, level * 12)` is an unexplained
# gain constant on a *linear* RMS->width map, so normal speech pins it near
# full while quiet speech barely moves it: it reads as "on", not as "hearing
# you", which is the reassurance the meter exists to give. Ranged so
# conversational speech (~0.02-0.08 RMS) lands mid-scale with headroom above.
METER_FLOOR_DB = -60.0
METER_CEIL_DB = -6.0


def meter_scale(rms: float) -> float:
    """Map an RMS level to 0..1 logarithmically, the way hearing works."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    span = METER_CEIL_DB - METER_FLOOR_DB
    return max(0.0, min(1.0, (db - METER_FLOOR_DB) / span))


@dataclass(frozen=True)
class PillFrame:
    """Everything the renderer needs for one state."""

    state: str
    glyph: str | None = None
    label: str = ""
    label_style: str = PLAIN
    meter: str | None = None
    timeout_ms: int | None = None    # auto-hide after this long
    fade_in: bool = True             # Recording snaps in; reactive states fade

    @property
    def visible(self) -> bool:
        return self.state != HIDDEN


def frame(state: str, *, cleanup: bool = False, detail: str = "",
          transient_ms: int = 1000, paused_ms: int = 1500) -> PillFrame:
    """The one place a state turns into glyph, label, meter and timeout."""
    mode_glyph = CLEANUP if cleanup else MIC
    if state == RECORDING:
        # Zero fade: the pill is the "I'm listening" signal and nothing may
        # delay it.
        return PillFrame(RECORDING, mode_glyph, meter=VOICE, fade_in=False)
    if state == TRANSCRIBING:
        return PillFrame(TRANSCRIBING, mode_glyph, meter=INDETERMINATE)
    if state == CLEANING:
        # Cleaned-up dictation's <=3.5 s vs raw's <=2 s is entirely the LLM.
        # Naming the second phase explains the wait rather than leaving a
        # longer spinner.
        return PillFrame(CLEANING, CLEANUP, "Cleaning up", MUTED, INDETERMINATE)
    if state == CANCELLED:
        # You can talk for eight seconds, hit a key, and today the pill just
        # vanishes — indistinguishable from a crash.
        return PillFrame(CANCELLED, None, "Cancelled", MUTED,
                         timeout_ms=transient_ms)
    if state == PAUSED:
        return PillFrame(PAUSED, None, "Paused", MUTED, timeout_ms=paused_ms)
    if state == FAILURE:
        return PillFrame(FAILURE, WARNING, detail or "Dictation failed", DANGER,
                         timeout_ms=2500)
    return PillFrame(HIDDEN)


class PillPolicy:
    """Whether a requested state is shown, and when it may leave.

    Holds the two rules that need memory: the hide toggle (§4.8) and the
    minimum on-screen time (§4.6). A two-word dictation on GPU can go
    recording -> transcribing -> hidden in under 200 ms; the floor stops it
    strobing.
    """

    def __init__(self, show_activity: bool = True, min_visible_ms: int = 400,
                 transient_ms: int = 1000, paused_ms: int = 1500,
                 clock=time.monotonic) -> None:
        self.show_activity = show_activity
        self.min_visible_ms = min_visible_ms
        self.transient_ms = transient_ms
        self.paused_ms = paused_ms
        self._clock = clock
        self._shown_at: float | None = None
        self.state = HIDDEN

    def request(self, state: str, *, cleanup: bool = False,
                detail: str = "") -> tuple[PillFrame, int]:
        """(frame to render, milliseconds to wait before rendering it).

        A non-zero delay only ever happens on the way *out*: the pill has not
        been up long enough to be readable yet.
        """
        if state in ACTIVITY_STATES and not self.show_activity:
            state = HIDDEN

        target = frame(state, cleanup=cleanup, detail=detail,
                       transient_ms=self.transient_ms, paused_ms=self.paused_ms)

        if target.state == HIDDEN:
            delay = self._remaining_floor()
            self.state = HIDDEN
            self._shown_at = None
            return target, delay

        if self._shown_at is None:
            self._shown_at = self._clock()
        self.state = target.state
        return target, 0

    def _remaining_floor(self) -> int:
        if self._shown_at is None:
            return 0
        elapsed_ms = (self._clock() - self._shown_at) * 1000
        return max(0, round(self.min_visible_ms - elapsed_ms))
