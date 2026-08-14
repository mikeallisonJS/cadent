"""Strategy resolution (pure), the ladder over fake seams, UTF-16 expansion."""

import sys

import pytest
from conftest import FakeClipboard, FakeFocusedApp, FakeKeyboard, make_platform

from cadent.config import AppOverride
from cadent.inject import Injector, _utf16_units, learn_override, resolve_override, utf16_chunks

OVERRIDES = [
    AppOverride(process="WindowsTerminal.exe", strategy="clipboard",
                paste_chord="ctrl+shift+v"),
    AppOverride(process="evil.exe", strategy="not-a-strategy"),
    AppOverride(process="admin.exe", strategy="notify-only"),
]


def test_match_is_case_insensitive():
    ov = resolve_override("windowsterminal.exe", OVERRIDES)
    assert ov.strategy == "clipboard"
    assert ov.paste_chord == "ctrl+shift+v"


def test_no_match_uses_default_strategy():
    assert resolve_override("chrome.exe", OVERRIDES).strategy == "type"
    assert resolve_override("chrome.exe", OVERRIDES, "clipboard").strategy == "clipboard"


def test_invalid_override_strategy_skipped():
    assert resolve_override("evil.exe", OVERRIDES).strategy == "type"


def test_invalid_default_strategy_sanitized():
    assert resolve_override("chrome.exe", [], "bogus").strategy == "type"


def test_notify_only_respected():
    assert resolve_override("admin.exe", OVERRIDES).strategy == "notify-only"


def test_the_sendinput_alias_still_resolves():
    """Pre-macOS configs say "sendinput" for the strategy now called "type";
    the read alias is permanent (spec §2)."""
    overrides = [AppOverride(process="Old.exe", strategy="sendinput")]
    assert resolve_override("old.exe", overrides).strategy == "sendinput"
    assert resolve_override("chrome.exe", [], "sendinput").strategy == "type"


# ---- auto-learn (#45) ------------------------------------------------------

def test_learn_appends_clipboard_override_for_unseen_app():
    overrides = []
    learned = learn_override("slack.exe", overrides)
    assert learned is not None and learned in overrides
    assert learned.process == "slack.exe"
    assert learned.strategy == "clipboard"
    assert learned.learned is True
    # Byte-for-byte the fallback configuration that just succeeded there —
    # "" is the platform-default sentinel (Capabilities.paste_chord).
    assert learned.paste_chord == ""
    assert learned.settle_delay_ms == 150
    assert learned.restore_clipboard is True


def test_learned_override_wins_next_resolution():
    overrides = []
    learn_override("slack.exe", overrides)
    assert resolve_override("Slack.exe", overrides).strategy == "clipboard"


def test_any_existing_entry_blocks_learning():
    """Hand-authored precedence is automatic: any entry for the process —
    even one with an invalid strategy — means we never learn over it."""
    for existing in (AppOverride(process="Slack.exe", strategy="type"),
                     AppOverride(process="slack.exe", strategy="not-a-strategy")):
        overrides = [existing]
        assert learn_override("slack.exe", overrides) is None
        assert overrides == [existing]


def test_learning_is_idempotent():
    overrides = []
    assert learn_override("slack.exe", overrides) is not None
    assert learn_override("slack.exe", overrides) is None
    assert len(overrides) == 1


def test_unidentifiable_app_never_learns():
    """focused_app_name() degrades to 'unknown'; an override keyed on it would
    catch every app whose name can't be resolved."""
    for name in (None, "", "unknown"):
        overrides = []
        assert learn_override(name, overrides) is None
        assert overrides == []


# ---- the ladder over fake seams ---------------------------------------------
# The failure-reason distinction that gates learning, tested at insert() with
# fakes at the platform seams (the formalized monkeypatch seam, ADR 0005).

def fallback_injector(*, released=True, short_send=False, raises=False):
    keyboard = FakeKeyboard(short_send=short_send, raise_on_type=raises,
                            held=not released)
    plat = make_platform(keyboard=keyboard, clipboard=FakeClipboard(),
                         focused_app=FakeFocusedApp())
    inj = Injector([], platform=plat)
    if not released:
        # Don't sit out the real 2 s release timeout in a unit test.
        inj._wait_modifiers_released = lambda timeout_s=0: False
    return inj, plat


def test_clean_typing_reports_no_failure():
    inj, plat = fallback_injector()
    result = inj.insert("hi", "app.exe")
    assert result.outcome == "inserted"
    assert result.typing_failed is False
    assert plat.keyboard.typed == [_utf16_units("hi")]
    assert plat.clipboard.writes == []


def test_short_send_fallback_flags_typing_failure():
    inj, plat = fallback_injector(short_send=True)
    result = inj.insert("hi", "app.exe")
    assert result.outcome == "fallback"
    assert result.typing_failed is True
    assert plat.clipboard.writes[0] == "hi"


def test_typing_exception_fallback_flags_typing_failure():
    inj, _ = fallback_injector(raises=True)
    result = inj.insert("hi", "app.exe")
    assert result.outcome == "fallback"
    assert result.typing_failed is True


def test_modifier_timeout_fallback_never_teaches():
    """Held modifiers at timeout are the user's hand, not the app rejecting
    the input — that fallback must not look like a learnable failure."""
    inj, _ = fallback_injector(released=False)
    result = inj.insert("hi", "app.exe")
    assert result.outcome == "fallback"
    assert result.typing_failed is False


def test_blocked_foreground_is_notify_only():
    plat = make_platform(
        focused_app=FakeFocusedApp(blocked="focused window is elevated (admin)"))
    result = Injector([], platform=plat).insert("hi", "app.exe")
    assert result.outcome == "notify-only"
    assert "elevated" in result.detail


def test_paste_restores_the_prior_clipboard():
    plat = make_platform()
    plat.clipboard.set_text("previous", exclude_from_history=False)
    overrides = [AppOverride(process="term.exe", strategy="clipboard",
                             settle_delay_ms=0)]
    result = Injector(overrides, platform=plat).insert("hello", "term.exe")
    assert result.outcome == "inserted"
    assert plat.clipboard.get_text() == "previous"
    assert "hello" in plat.clipboard.writes
    assert plat.keyboard.chords  # the paste chord went out


def test_an_unparseable_paste_chord_falls_back_to_the_platforms():
    """A typo'd chord must never send nothing — the platform's own paste
    chord (Capabilities.paste_chord) goes out instead."""
    plat = make_platform()
    overrides = [AppOverride(process="term.exe", strategy="clipboard",
                             paste_chord="bogus+nothing", settle_delay_ms=0)]
    result = Injector(overrides, platform=plat).insert("hello", "term.exe")
    assert result.outcome == "inserted"
    assert plat.keyboard.chords == [[0x11, ord("V")]]   # ctrl+v on this table


def test_paste_does_not_restore_when_someone_else_wrote():
    plat = make_platform()
    plat.clipboard.set_text("previous", exclude_from_history=False)
    overrides = [AppOverride(process="term.exe", strategy="clipboard",
                             settle_delay_ms=0)]
    inj = Injector(overrides, platform=plat)
    chord = plat.keyboard.send_chord

    def chord_and_clobber(keys):
        chord(keys)
        plat.clipboard.set_text("their copy", exclude_from_history=False)

    plat.keyboard.send_chord = chord_and_clobber
    inj.insert("hello", "term.exe")
    assert plat.clipboard.get_text() == "their copy"


def test_total_failure_leaves_the_transcript_on_the_clipboard():
    keyboard = FakeKeyboard(raise_on_type=True)
    plat = make_platform(keyboard=keyboard)
    clipboard_calls = []
    real_set = plat.clipboard.set_text

    def set_text(text, exclude_from_history):
        clipboard_calls.append((text, exclude_from_history))
        if exclude_from_history:
            raise OSError("clipboard busy")   # the paste rung fails...
        real_set(text, exclude_from_history)  # ...the last resort succeeds

    plat.clipboard.set_text = set_text
    result = Injector([], platform=plat).insert("hi", "app.exe")
    assert result.outcome == "failed"
    assert result.on_clipboard is True
    # Last resort deliberately skips the exclusion format (PRD §6).
    assert clipboard_calls[-1] == ("hi", False)


# ---- the darwin-shaped ladder (#144: paste by default, no fall-through) -----
# Behavior tests over fakes, so they run on any OS; the darwin adapter
# internals are platform-skipped in test_darwin_inject.py.

def darwin_platform(**overrides):
    import dataclasses

    from cadent.platform.keycodes import DARWIN_KEYCODES

    plat = make_platform(**overrides)
    caps = dataclasses.replace(
        plat.capabilities, keycode_table=DARWIN_KEYCODES,
        default_injection_strategy="clipboard", injection_rungs=("paste", "type"),
        paste_chord="cmd+v", default_overrides=(), default_override_reasons={},
        auto_learn_overrides=False)
    return dataclasses.replace(plat, capabilities=caps)


def test_darwin_default_pastes_with_cmd_v():
    plat = darwin_platform()
    overrides = [AppOverride(process="com.apple.terminal", strategy="clipboard",
                             settle_delay_ms=0)]
    result = Injector(overrides, "clipboard", platform=plat) \
        .insert("hello", "com.apple.terminal")
    assert result.outcome == "inserted"
    assert plat.keyboard.typed == []              # never the type rung
    assert plat.keyboard.chords == [[55, 9]]      # Cmd-V, not Ctrl-V
    assert "hello" in plat.clipboard.writes


def test_darwin_default_override_carries_the_platform_chord():
    """resolve_override's synthetic default has no stored chord — the platform's
    own paste chord goes out, so an unlisted app pastes with Cmd-V."""
    plat = darwin_platform()
    result = Injector([], "clipboard", platform=plat).insert("hi", "com.new.app")
    assert result.outcome == "inserted"
    assert plat.keyboard.chords == [[55, 9]]


def test_darwin_type_override_never_falls_through_to_paste():
    """§2: the rung order is ("paste", "type") — "type" is reachable only as an
    explicit override, and a typing failure lands on the last resort, never on
    an automatic paste, and never flags typing_failed (auto-learn is win32's)."""
    keyboard = FakeKeyboard(raise_on_type=True)
    plat = darwin_platform(keyboard=keyboard)
    overrides = [AppOverride(process="legacy.app", strategy="type")]
    result = Injector(overrides, "clipboard", platform=plat) \
        .insert("hi", "legacy.app")
    assert result.outcome == "failed"
    assert result.on_clipboard is True            # transcript left pasteable
    assert result.typing_failed is False
    assert plat.keyboard.chords == []             # no paste chord went out


# ---- Win32 adapter internals (platform-skipped, run on the real machine) ----

def test_input_struct_matches_win32_layout():
    """SendInput validates cbSize against the real INPUT struct (40 bytes on
    x64, 28 on x86); a lone-KEYBDINPUT union silently breaks every send."""
    if sys.platform != "win32":
        pytest.skip("LP64 ctypes gives different sizes off Windows (#129)")
    import ctypes

    from cadent.platform.win32 import INPUT

    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(INPUT) == expected


def test_clipboard_roundtrip_survives_x64_handles():
    """GetClipboardData needs an explicit 64-bit restype; ctypes' default c_int
    truncated the HANDLE and crashed the restore path (found by verify_e2e)."""
    if sys.platform != "win32":
        pytest.skip("needs the real Windows clipboard")
    from cadent.platform.win32 import Win32Clipboard

    clipboard = Win32Clipboard()
    saved = clipboard.get_text()
    try:
        clipboard.set_text("lf-roundtrip ✓ 𝄞", exclude_from_history=False)
        assert clipboard.get_text() == "lf-roundtrip ✓ 𝄞"
    finally:
        if saved is not None:
            clipboard.set_text(saved, exclude_from_history=False)


def test_utf16_units_surrogate_pairs():
    assert _utf16_units("ab") == [0x61, 0x62]
    units = _utf16_units("🎤")           # U+1F3A4 → surrogate pair
    assert len(units) == 2
    assert 0xD800 <= units[0] <= 0xDBFF
    assert 0xDC00 <= units[1] <= 0xDFFF


# ---- utf16_chunks (spec §2: CGEvent typing chunks at 20 UTF-16 units) --------

def test_chunks_cap_at_the_event_limit():
    units = _utf16_units("a" * 47)
    chunks = utf16_chunks(units, 20)
    assert [len(c) for c in chunks] == [20, 20, 7]
    assert [u for c in chunks for u in c] == units


def test_chunks_never_split_a_surrogate_pair():
    # 13 BMP chars then emoji: units 13-14 are a pair straddling a naive
    # 14-unit boundary — the chunk backs off one unit instead of splitting.
    units = _utf16_units("a" * 13 + "🎤🎤🎤🎤")
    chunks = utf16_chunks(units, 14)
    assert [u for c in chunks for u in c] == units
    for chunk in chunks:
        assert not (0xD800 <= chunk[-1] <= 0xDBFF)


def test_chunks_pass_a_lone_high_surrogate_through():
    """A lone trailing high surrogate (mojibake input) must still be emitted,
    not spin the chunker or vanish."""
    units = [0x61, 0xD83C]
    assert utf16_chunks(units, 2) == [[0x61, 0xD83C]]
    assert utf16_chunks([0xD83C], 1) == [[0xD83C]]


def test_empty_text_yields_no_chunks():
    assert utf16_chunks([], 20) == []
