"""The darwin injection seams (#144, spec §2/§5.1) — adapter internals,
platform-skipped per ADR 0005; they run on real Macs (the M1 Max, CI's
macos-14 leg).

Tests that post real keyboard events are additionally gated behind
CADENT_REAL_INPUT_TESTS=1: on an Accessibility-trusted machine they type
into whatever is focused, which belongs in the deliberate #149 verification
pass, not in every pytest run. Pasteboard tests restore what they found.
"""

import os
import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("darwin adapter internals", allow_module_level=True)

from cadent.inject import _utf16_units  # noqa: E402
from cadent.platform.darwin import (  # noqa: E402
    DarwinClipboard,
    DarwinFocusedApp,
    DarwinKeyboard,
)

REAL_INPUT = os.environ.get("CADENT_REAL_INPUT_TESTS") == "1"


@pytest.fixture
def clipboard():
    """A DarwinClipboard that puts the user's pasteboard back afterwards."""
    clipboard = DarwinClipboard()
    saved = clipboard.get_text()
    yield clipboard
    if saved is not None:
        clipboard.set_text(saved, exclude_from_history=False)


# ---- NSPasteboard (spec §2) ---------------------------------------------------


def test_pasteboard_round_trip(clipboard):
    clipboard.set_text("lf-roundtrip ✓ 𝄞", exclude_from_history=False)
    assert clipboard.get_text() == "lf-roundtrip ✓ 𝄞"


def test_change_count_moves_on_every_write(clipboard):
    """`changeCount` is the restore guard: it must move when *we* write, so
    equality after the settle really means nobody else wrote in between."""
    before = clipboard.sequence_number()
    clipboard.set_text("first", exclude_from_history=True)
    after_ours = clipboard.sequence_number()
    assert after_ours > before
    clipboard.set_text("second", exclude_from_history=False)
    assert clipboard.sequence_number() > after_ours


def test_transient_marker_rides_along_when_excluded(clipboard):
    from AppKit import NSPasteboard

    clipboard.set_text("secret-ish", exclude_from_history=True)
    types = NSPasteboard.generalPasteboard().types()
    assert "org.nspasteboard.TransientType" in types

    clipboard.set_text("plain", exclude_from_history=False)
    types = NSPasteboard.generalPasteboard().types()
    assert "org.nspasteboard.TransientType" not in types


# ---- NSWorkspace identity + preflights (spec §2, §5.1) ------------------------


def test_identity_is_a_bundle_id_or_the_sentinel():
    """Whatever is frontmost while the suite runs resolves down the ADR 0004
    ladder — a non-empty identity, never an exception, never None."""
    name = DarwinFocusedApp().name()
    assert isinstance(name, str) and name


def test_window_rect_is_a_rect_or_none():
    rect = DarwinFocusedApp().window_rect()
    if rect is not None:
        left, top, right, bottom = rect
        assert right >= left and bottom >= top


def test_injection_blocked_answers_without_raising():
    """CI has no Accessibility grant → the permission message; a trusted M1
    with no password field focused → None. Both are honest answers."""
    blocked = DarwinFocusedApp().injection_blocked()
    assert blocked is None or (isinstance(blocked, str) and blocked)


def test_secure_input_probe_is_callable():
    assert DarwinFocusedApp._secure_input_enabled() in (True, False)


# ---- CGEvent output (spec §2) — posts real events, #149 territory -------------


@pytest.mark.skipif(not REAL_INPUT, reason="posts real keyboard events; "
                    "set CADENT_REAL_INPUT_TESTS=1 (see #149)")
def test_unicode_typing_posts_without_error():
    assert DarwinKeyboard().send_text_units(_utf16_units("lf ✓ 🎤")) is True


@pytest.mark.skipif(not REAL_INPUT, reason="posts a real Cmd-V; "
                    "set CADENT_REAL_INPUT_TESTS=1 (see #149)")
def test_paste_chord_posts_without_error():
    DarwinKeyboard().send_chord([55, 9])    # Cmd-V


def test_modifiers_down_reads_the_hid_state():
    assert DarwinKeyboard().modifiers_down() in (True, False)
