"""The pill's state machine, driven without a screen (spec §4, §10)."""

import pytest

from cadent import pill


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance_ms(self, ms):
        self.now += ms / 1000


# ---- shape carries mode -----------------------------------------------------

def test_the_leading_glyph_is_the_mode_always_positively_rendered():
    """Cleanup used to be a dot that was either there or not there, so with
    nothing to compare against you could only tell 'there is a dot'."""
    assert pill.frame(pill.RECORDING, cleanup=False).glyph == pill.MIC
    assert pill.frame(pill.RECORDING, cleanup=True).glyph == pill.CLEANUP
    assert pill.frame(pill.TRANSCRIBING, cleanup=True).glyph == pill.CLEANUP


def test_failure_swaps_the_glyph_for_a_warning():
    assert pill.frame(pill.FAILURE, detail="Mic unavailable").glyph == pill.WARNING


def test_cancelled_and_paused_carry_no_glyph_at_all():
    """They collapse to a short label in a small neutral pill, so the size
    difference is itself a signal."""
    assert pill.frame(pill.CANCELLED).glyph is None
    assert pill.frame(pill.PAUSED).glyph is None


# ---- colour carries state ---------------------------------------------------

def test_failure_is_danger_and_non_events_are_muted():
    assert pill.frame(pill.FAILURE, detail="x").label_style == pill.DANGER
    assert pill.frame(pill.CANCELLED).label_style == pill.MUTED
    assert pill.frame(pill.PAUSED).label_style == pill.MUTED


def test_the_meter_morphs_rather_than_swapping():
    """Recording's bars become an indeterminate sweep in the same geometry, so
    recording -> transcribing feels like one continuous act."""
    assert pill.frame(pill.RECORDING).meter == pill.VOICE
    assert pill.frame(pill.TRANSCRIBING).meter == pill.INDETERMINATE
    assert pill.frame(pill.CLEANING).meter == pill.INDETERMINATE


def test_processing_splits_into_transcribing_and_cleaning_up():
    """Cleaned-up dictation's <=3.5 s vs raw's <=2 s is entirely the LLM;
    naming the second
    phase explains the wait."""
    assert pill.frame(pill.TRANSCRIBING).label == ""
    assert pill.frame(pill.CLEANING).label == "Cleaning up"


# ---- the three events that had no feedback at all --------------------------

def test_a_mid_hold_cancel_says_something():
    """Today the pill just vanishes, indistinguishable from a crash."""
    cancelled = pill.frame(pill.CANCELLED)
    assert cancelled.label == "Cancelled"
    assert cancelled.timeout_ms == 1000


def test_a_hotkey_press_while_paused_says_something():
    """Currently inert, which reads as broken."""
    paused = pill.frame(pill.PAUSED)
    assert paused.label == "Paused"
    assert paused.timeout_ms == 1500


def test_a_failure_is_terse_and_carries_the_caller_s_words():
    assert pill.frame(pill.FAILURE, detail="Mic unavailable").label == \
        "Mic unavailable"


def test_a_failure_without_detail_still_says_something():
    assert pill.frame(pill.FAILURE).label == "Dictation failed"


def test_hidden_is_not_visible_and_everything_else_is():
    assert pill.frame(pill.HIDDEN).visible is False
    for state in (pill.RECORDING, pill.TRANSCRIBING, pill.CLEANING,
                  pill.CANCELLED, pill.PAUSED, pill.FAILURE):
        assert pill.frame(state).visible is True


def test_an_unknown_state_hides_rather_than_rendering_garbage():
    assert pill.frame("nonsense").state == pill.HIDDEN


# ---- motion -----------------------------------------------------------------

def test_recording_snaps_in_with_zero_fade():
    """The pill is the "I'm listening" signal and nothing may delay it."""
    assert pill.frame(pill.RECORDING).fade_in is False


def test_reactive_states_fade_in():
    for state in (pill.FAILURE, pill.CANCELLED, pill.PAUSED):
        assert pill.frame(state).fade_in is True


# ---- the hide toggle governs activity only (§4.8) --------------------------

@pytest.mark.parametrize("state", sorted(pill.ACTIVITY_STATES))
def test_the_toggle_hides_every_activity_state(state):
    policy = pill.PillPolicy(show_activity=False)
    rendered, _delay = policy.request(state)
    assert rendered.state == pill.HIDDEN


def test_the_toggle_never_hides_a_failure():
    """It turns off the ambient indicator, not the alarm — otherwise overlay
    off plus Do Not Disturb fails dictations into total silence."""
    policy = pill.PillPolicy(show_activity=False)
    rendered, _delay = policy.request(pill.FAILURE, detail="Mic unavailable")
    assert rendered.state == pill.FAILURE
    assert rendered.label == "Mic unavailable"


def test_with_the_toggle_on_everything_shows():
    policy = pill.PillPolicy(show_activity=True)
    for state in sorted(pill.ACTIVITY_STATES):
        assert policy.request(state)[0].state == state


# ---- the minimum-visible floor (§4.6) --------------------------------------

def test_an_immediate_hide_is_deferred_to_the_floor():
    """A two-word dictation on GPU can go recording -> transcribing -> hidden
    in under 200 ms; the floor stops it strobing."""
    clock = FakeClock()
    policy = pill.PillPolicy(min_visible_ms=400, clock=clock)
    policy.request(pill.RECORDING)
    clock.advance_ms(150)
    _hidden, delay = policy.request(pill.HIDDEN)
    assert delay == 250


def test_a_hide_after_the_floor_is_immediate():
    clock = FakeClock()
    policy = pill.PillPolicy(min_visible_ms=400, clock=clock)
    policy.request(pill.RECORDING)
    clock.advance_ms(900)
    assert policy.request(pill.HIDDEN)[1] == 0


def test_the_floor_measures_from_the_first_show_not_the_last_state():
    """Recording -> transcribing is one continuous appearance, not two."""
    clock = FakeClock()
    policy = pill.PillPolicy(min_visible_ms=400, clock=clock)
    policy.request(pill.RECORDING)
    clock.advance_ms(300)
    policy.request(pill.TRANSCRIBING)
    clock.advance_ms(150)
    assert policy.request(pill.HIDDEN)[1] == 0


def test_hiding_an_already_hidden_pill_waits_for_nothing():
    policy = pill.PillPolicy()
    assert policy.request(pill.HIDDEN)[1] == 0


def test_a_suppressed_activity_state_never_starts_the_floor(qt_app=None):
    """With the overlay hidden there is nothing on screen to keep readable."""
    clock = FakeClock()
    policy = pill.PillPolicy(show_activity=False, clock=clock)
    policy.request(pill.RECORDING)
    assert policy.request(pill.HIDDEN)[1] == 0


# ---- the meter's mapping (§4.5) --------------------------------------------

def test_silence_maps_to_the_bottom_of_the_scale():
    assert pill.meter_scale(0.0) == 0.0
    assert pill.meter_scale(-1.0) == 0.0


def test_conversational_speech_lands_mid_scale_with_headroom_above():
    """The old linear map with its gain of 12 pinned normal speech near full,
    so the meter read as "on" rather than as "hearing you"."""
    for rms in (0.02, 0.05, 0.08):
        assert 0.4 < pill.meter_scale(rms) < 0.9


def test_quiet_speech_still_visibly_moves_the_meter():
    """A linear map buries this range; a log one is why the meter can tell you
    the wrong input device is selected."""
    assert pill.meter_scale(0.004) > 0.2
    assert pill.meter_scale(0.004) < pill.meter_scale(0.02)


def test_the_scale_is_monotonic_and_clamped():
    previous = -1.0
    for rms in (0.0005, 0.001, 0.01, 0.1, 0.5, 1.0, 4.0):
        value = pill.meter_scale(rms)
        assert 0.0 <= value <= 1.0
        assert value >= previous
        previous = value


def test_the_policy_tracks_the_state_it_last_rendered():
    policy = pill.PillPolicy()
    policy.request(pill.RECORDING)
    assert policy.state == pill.RECORDING
    policy.request(pill.HIDDEN)
    assert policy.state == pill.HIDDEN
