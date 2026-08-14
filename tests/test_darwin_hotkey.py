"""The darwin hotkey tap (#145, spec §3) — adapter internals, platform-skipped
per ADR 0005; they run on real Macs (the M1 Max, CI's macos-14 leg).

The listener tests need more than the OS: an untrusted process's event tap
hears nothing (`AXIsProcessTrusted()` false — CI's macos-14 leg), and
creating a real event tap in a process that has already pumped AppKit
through the Qt widget tests trips a HIToolbox assertion (SIGTRAP) that kills
the whole pytest run. So every test that starts the listener sits behind
CADENT_REAL_INPUT_TESTS=1 like the #144 typing tests, and the deliberate
pass runs this file alone:

    CADENT_REAL_INPUT_TESTS=1 uv run pytest tests/test_darwin_hotkey.py
"""

import os
import sys
import threading
import time

import pytest

if sys.platform != "darwin":
    pytest.skip("darwin adapter internals", allow_module_level=True)

from cadent.platform.darwin import DarwinHotkeyTap, _pynput_vk  # noqa: E402

REAL_INPUT = os.environ.get("CADENT_REAL_INPUT_TESTS") == "1"


def _trusted() -> bool:
    from ApplicationServices import AXIsProcessTrusted

    return bool(AXIsProcessTrusted())


# ---- the pynput-key → Carbon-code translation ---------------------------------


def test_pynput_keys_translate_to_carbon_codes():
    """Named keys arrive as `Key` enum members (their value is the KeyCode),
    everything else as a bare `KeyCode`; both must yield the Carbon codes the
    groups in keycodes.py name — including both sides of a modifier."""
    from pynput import keyboard

    assert _pynput_vk(keyboard.Key.ctrl) == 59
    assert _pynput_vk(keyboard.Key.ctrl_r) == 62
    assert _pynput_vk(keyboard.Key.cmd) == 55
    assert _pynput_vk(keyboard.Key.cmd_r) == 54
    assert _pynput_vk(keyboard.KeyCode.from_vk(9)) == 9
    # pynput hands None for events it could not convert; drop, don't crash.
    assert _pynput_vk(None) is None


# ---- the listener itself (needs the grant, and a Qt-free process) -------------

_LISTENER_GATE = pytest.mark.skipif(
    not REAL_INPUT or not _trusted(),
    reason="starts a real event tap; run this file alone in the #149 pass "
           "with CADENT_REAL_INPUT_TESTS=1")


@_LISTENER_GATE
def test_start_and_stop_are_idempotent():
    tap = DarwinHotkeyTap()
    tap.start(lambda *a: None)
    tap.start(lambda *a: None)    # second start is a no-op, not a second tap
    tap.stop()
    tap.stop()                    # second stop is quiet


@_LISTENER_GATE
def test_modifier_hold_and_release_arrive_with_the_injected_flag():
    """The acceptance walk (#145): a Ctrl hold-and-release reaches the tap as
    press then release — the system delivers modifier changes as
    `kCGEventFlagsChanged`, which pynput translates into the sided key events
    the Carbon groups name. Posted events carry a source PID, so both must
    arrive flagged injected (`kCGEventSourceUnixProcessID != 0`) — the same
    filter that keeps our own Cmd-V out of the chord machine."""
    from pynput import keyboard

    seen: list[tuple[int, bool, bool]] = []
    got_release = threading.Event()

    def on_event(vk: int, is_down: bool, injected: bool) -> None:
        seen.append((vk, is_down, injected))
        if vk == 59 and not is_down:
            got_release.set()

    tap = DarwinHotkeyTap()
    tap.start(on_event)
    try:
        time.sleep(0.3)           # let the tap attach before posting
        controller = keyboard.Controller()
        controller.press(keyboard.Key.ctrl)
        controller.release(keyboard.Key.ctrl)
        assert got_release.wait(timeout=5.0)
    finally:
        tap.stop()
    assert (59, True, True) in seen
    assert (59, False, True) in seen
