"""Chord state machine: pure-logic tests, no OS hook.

The machines parse combos against the *current platform's* table, and every
event fed below is a win32 VK — so the win32 column is pinned rather than
inherited from the host OS (#144 gave darwin its own table)."""

import pytest

from cadent.chord import Action, ChordStateMachine, TapChord, parse_combo

CTRL_L, WIN_L, KEY_A = 0xA2, 0x5B, ord("A")
SHIFT_L, ALT_L = 0xA0, 0xA4


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    pass


def press_chord(sm, t=0.0):
    acts = sm.on_event(CTRL_L, True, False, t)
    acts += sm.on_event(WIN_L, True, False, t)
    return acts


def release_chord(sm, t):
    acts = sm.on_event(WIN_L, False, False, t)
    acts += sm.on_event(CTRL_L, False, False, t)
    return acts


def make(mode="hold"):
    return ChordStateMachine("<ctrl>+<cmd>", mode, min_hold_s=0.2)


def test_parse_combo_rejects_garbage():
    with pytest.raises(ValueError):
        parse_combo("<bogus>")
    with pytest.raises(ValueError):
        parse_combo("")


def test_hold_start_and_stop():
    sm = make()
    acts = press_chord(sm)
    assert Action.START in acts and Action.MASK_MENU in acts
    acts = release_chord(sm, t=1.0)
    assert acts == [Action.STOP]


def test_hold_short_tap_discards():
    sm = make()
    press_chord(sm, t=0.0)
    acts = release_chord(sm, t=0.1)
    assert acts == [Action.DISCARD]


def test_auto_repeat_idempotent():
    sm = make()
    press_chord(sm)
    for _ in range(5):
        assert sm.on_event(WIN_L, True, False, 0.05) == []
    assert release_chord(sm, 1.0) == [Action.STOP]


def test_injected_events_ignored():
    sm = make()
    press_chord(sm)
    assert sm.on_event(WIN_L, False, True, 0.5) == []      # our own SendInput echo
    assert release_chord(sm, 1.0) == [Action.STOP]


def test_non_chord_key_cancels_hold():
    sm = make()
    press_chord(sm)
    acts = sm.on_event(KEY_A, True, False, 0.5)             # Ctrl+Win+A → OS shortcut
    assert acts == [Action.DISCARD]
    assert release_chord(sm, 1.0) == []                      # already cancelled


def test_no_start_on_partial_chord():
    sm = make()
    assert sm.on_event(CTRL_L, True, False, 0.0) == []
    assert sm.on_event(CTRL_L, False, False, 0.1) == []


def test_toggle_flip_flop_with_rearm():
    sm = make("toggle")
    acts = press_chord(sm)
    assert Action.START in acts
    # still held: pressing more chord keys must not stop
    assert sm.on_event(WIN_L, True, False, 0.3) == []
    release_chord(sm, 0.5)                                   # full release re-arms
    acts = press_chord(sm, t=5.0)
    assert Action.STOP in acts
    release_chord(sm, 5.5)
    acts = press_chord(sm, t=6.0)
    assert Action.START in acts


def test_toggle_no_retrigger_without_full_release():
    sm = make("toggle")
    press_chord(sm)
    sm.on_event(WIN_L, False, False, 0.5)                    # win up, ctrl still down
    acts = sm.on_event(WIN_L, True, False, 0.6)              # win down again
    assert acts == []                                        # not re-armed yet


# ---- TapChord (the cleanup toggle hotkey) ---------------------------------

TAP_KEYS = (CTRL_L, SHIFT_L, ALT_L)


def make_tap():
    return TapChord("<ctrl>+<shift>+<alt>")


def tap_down(tc):
    fired = [tc.on_event(vk, True, False) for vk in TAP_KEYS]
    return any(fired)


def tap_up(tc):
    fired = [tc.on_event(vk, False, False) for vk in TAP_KEYS]
    return any(fired)


def test_tap_fires_on_clean_release():
    tc = make_tap()
    assert not tap_down(tc)                      # nothing fires while held
    assert tap_up(tc)                            # fires once on release


def test_tap_fires_only_once_per_release():
    tc = make_tap()
    tap_down(tc)
    assert tc.on_event(CTRL_L, False, False)     # first key up: chord broken -> fire
    assert not tc.on_event(SHIFT_L, False, False)
    assert not tc.on_event(ALT_L, False, False)


def test_other_key_mid_chord_aborts():
    """Ctrl+Shift+Alt+K is some app's shortcut, not a mode toggle."""
    tc = make_tap()
    tap_down(tc)
    assert not tc.on_event(KEY_A, True, False)
    assert not tc.on_event(KEY_A, False, False)
    assert not tap_up(tc)


def test_rearms_after_full_release():
    tc = make_tap()
    tap_down(tc)
    assert tap_up(tc)
    assert not tap_down(tc)
    assert tap_up(tc)


def test_no_refire_without_full_release():
    tc = make_tap()
    tap_down(tc)
    assert tc.on_event(ALT_L, False, False)      # fire
    assert not tc.on_event(ALT_L, True, False)   # re-press while others held
    assert not tc.on_event(ALT_L, False, False)  # no second fire


def test_partial_chord_release_never_fires():
    tc = make_tap()
    tc.on_event(CTRL_L, True, False)
    assert not tc.on_event(CTRL_L, False, False)


def test_tap_ignores_injected_events():
    tc = make_tap()
    tap_down(tc)
    assert not tc.on_event(CTRL_L, False, True)  # our own SendInput echo
    assert tc.on_event(CTRL_L, False, False)


# ---- describing a chord for humans (#166) ----------------------------------

WIN32_CAPTIONS = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                  "option": "Alt", "win": "Win", "cmd": "Win"}
DARWIN_CAPTIONS = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Option",
                   "option": "Option", "win": "Cmd", "cmd": "Cmd"}


def test_describe_combo_speaks_each_platforms_words():
    """The stored chord is one string; what it is called is a platform fact —
    "<cmd>" is the Win key on win32 and the Cmd key on darwin (#166)."""
    from cadent.chord import describe_combo

    assert describe_combo("<ctrl>+<cmd>", WIN32_CAPTIONS) == "Ctrl+Win"
    assert describe_combo("<ctrl>+<cmd>", DARWIN_CAPTIONS) == "Ctrl+Cmd"
    assert describe_combo("<ctrl>+<alt>", DARWIN_CAPTIONS) == "Ctrl+Option"


def test_describe_combo_uppercases_the_plain_keys():
    from cadent.chord import describe_combo

    assert describe_combo("<ctrl>+<alt>+f9", WIN32_CAPTIONS) == "Ctrl+Alt+F9"
    assert describe_combo("<ctrl>+<shift>+a", DARWIN_CAPTIONS) == "Ctrl+Shift+A"


def test_describe_combo_never_raises_on_a_combo_parse_would_reject():
    """Display copy must not crash a label over a config parse_combo already
    polices; best-effort words are better than an exception in a paint."""
    from cadent.chord import describe_combo

    assert describe_combo("<ctrl>+<nonsense>", WIN32_CAPTIONS)


def test_a_mac_authored_option_reads_as_alt_on_win32():
    """Hand-edited configs cross OSes with their users: the caption tables
    are mirror images, so "<option>" names the key it lands on."""
    from cadent.chord import describe_combo

    assert describe_combo("<ctrl>+<option>", WIN32_CAPTIONS) == "Ctrl+Alt"
