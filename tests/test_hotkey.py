"""PushToTalk over a fake tap: the capture seam the chord recorder uses (#63).

The chord machines themselves are covered in test_chord.py; what is left here
is the diversion — while Settings records a hotkey, the one OS hook keeps
running, its events go to the recorder, and the chords stay quiet.
"""

import time

import pytest

from cadent.hotkey import PushToTalk

CTRL_L, WIN_L = 0xA2, 0x5B


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    pass


@pytest.fixture
def ptt(fake_platform):
    started = []
    p = PushToTalk("<ctrl>+<cmd>", "hold", lambda: started.append(True), lambda: None, lambda: None,
                   min_hold_s=0.0, platform=fake_platform)
    p.start()
    p.started = started
    yield p
    p.stop()


def settle(ptt):
    """The worker thread runs the callbacks; give it a moment."""
    deadline = time.monotonic() + 1.0
    while not ptt._queue.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.02)


def test_the_chord_fires_when_nobody_is_recording(ptt, fake_platform):
    tap = fake_platform.hotkey_tap
    tap.press(CTRL_L)
    tap.press(WIN_L)
    settle(ptt)
    assert len(ptt.started) == 1


def test_a_capture_hears_every_key_and_the_chord_stays_quiet(ptt, fake_platform):
    tap = fake_platform.hotkey_tap
    heard = []
    ptt.capture(lambda code, down: heard.append((code, down)))
    tap.press(CTRL_L)
    tap.press(WIN_L)
    tap.release(WIN_L)
    tap.release(CTRL_L)
    settle(ptt)
    assert heard == [(CTRL_L, True), (WIN_L, True), (WIN_L, False), (CTRL_L, False)]
    assert ptt.started == []
    # The Win key went down while we listened: the Start menu is masked the
    # way the chord masks it, or recording Ctrl+Win would open Start.
    assert fake_platform.keyboard.mask_keys == 1


def test_our_own_injected_keys_never_reach_the_recorder(ptt, fake_platform):
    heard = []
    ptt.capture(lambda code, down: heard.append(code))
    fake_platform.hotkey_tap.press(WIN_L, injected=True)
    assert heard == []


def test_releasing_the_capture_gives_the_chord_back(ptt, fake_platform):
    tap = fake_platform.hotkey_tap
    ptt.capture(lambda code, down: None)
    ptt.release_capture()
    tap.press(CTRL_L)
    tap.press(WIN_L)
    settle(ptt)
    assert len(ptt.started) == 1
