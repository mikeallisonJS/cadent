"""The tier-shaped Capabilities facts (#32; ADR 0013, ADR 0014).

Introduced before any Linux adapter exists so the surfaces gate on facts,
never on `sys.platform`: `overlay`, `per_app_overrides`, `support_tier`,
`support_tier_summary`, the default-combo facts, and `HotkeyTap.start`
learning the chords. Behaviour under the Linux values is tested by pinning
those values on a fake — the host OS never decides.
"""

import dataclasses

from conftest import FakeHotkeyTap, make_platform

from cadent import app as app_mod
from cadent import platform as platform_pkg
from cadent.overlay import NoOverlay
from cadent.pipeline import DictationReport


def test_win32_and_darwin_fill_the_new_facts():
    from cadent.platform import fallback

    caps = fallback.CAPABILITIES
    assert caps.overlay == "windowed"
    assert caps.per_app_overrides is True
    assert caps.support_tier is None
    assert caps.support_tier_summary is None
    assert caps.default_combo == "<ctrl>+<cmd>"
    assert caps.default_cleanup_combo == "<ctrl>+<shift>+<alt>"


def test_the_shipped_config_defaults_match_the_platform_facts():
    """`Config` keeps its literals (a hand-edited file is never rewritten);
    the platform fact is what a Linux Wayland run substitutes for a chord
    its portal cannot bind. On win32/darwin the two must agree."""
    from cadent.config import Config
    from cadent.platform import fallback

    assert Config().hotkey == fallback.CAPABILITIES.default_combo
    assert Config().cleanup_hotkey == fallback.CAPABILITIES.default_cleanup_combo


# ---- HotkeyTap.start learns the chords ------------------------------------

def test_push_to_talk_tells_the_tap_which_chords_it_recognizes():
    from cadent.hotkey import PushToTalk

    tap = FakeHotkeyTap()
    plat = make_platform(hotkey_tap=tap)
    ptt = PushToTalk("<ctrl>+<cmd>", "hold", lambda: None, lambda: None,
                     lambda: None, cleanup_combo="<ctrl>+<shift>+<alt>",
                     on_cleanup_toggle=lambda: None, platform=plat)
    ptt.start()
    try:
        assert tap.chords == ("<ctrl>+<cmd>", "<ctrl>+<shift>+<alt>")
    finally:
        ptt.stop()


def test_without_a_cleanup_toggle_only_the_dictation_chord_is_bound():
    from cadent.hotkey import PushToTalk

    tap = FakeHotkeyTap()
    plat = make_platform(hotkey_tap=tap)
    ptt = PushToTalk("<ctrl>+<cmd>", "hold", lambda: None, lambda: None,
                     lambda: None, platform=plat)
    ptt.start()
    try:
        assert tap.chords == ("<ctrl>+<cmd>",)
    finally:
        ptt.stop()


def test_the_existing_adapters_accept_and_ignore_the_chords():
    """The protocol widened; the fallback tap (and win32/darwin, which share
    its signature) take the argument without complaint."""
    from cadent.platform import fallback

    fallback.NullHotkeyTap().start(lambda *_: None, chords=("<ctrl>+<cmd>",))
    fallback.NullHotkeyTap().start(lambda *_: None)


# ---- overlay gating (ADR 0014) --------------------------------------------

def no_overlay_platform():
    plat = make_platform()
    return dataclasses.replace(
        plat, capabilities=dataclasses.replace(plat.capabilities, overlay=None))


class FakeTray:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.faults: dict = {}

    def message(self, title, body, *_rest) -> None:
        self.messages.append(title)

    def set_fault(self, kind, active=True) -> None:
        self.faults[kind] = active


def app_with(overlay):
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.tray = FakeTray()
    instance.overlay = overlay
    instance.wizard = None
    instance.settings_window = None
    instance.platform = no_overlay_platform()
    instance._toast_target = None
    instance._stt = None
    return instance


def test_a_failed_dictation_reaches_the_tray_when_there_is_no_overlay(qt_app):
    """`show_failure` on the no-overlay shim is silent; the failure paths
    already toast, and on the overlay-less tiers that toast is a desktop
    notification — the failure channel."""
    instance = app_with(NoOverlay())
    instance._handle_report(DictationReport(text="hello", outcome="failed"))
    assert instance.tray.messages == ["Cadent — insertion failed"]


def test_the_no_overlay_shim_answers_every_call_the_app_makes():
    shim = NoOverlay()
    shim.level_source = lambda: 0.0
    shim.realize()
    shim.set_tokens({})
    shim.set_cleanup(True)
    shim.set_show_activity(False)
    for method in ("show_recording", "show_transcribing", "show_cleaning",
                   "show_cancelled", "show_paused", "hide_overlay"):
        getattr(shim, method)()
    shim.show_failure("Dictation failed")


def test_the_general_pane_hides_the_overlay_rows_where_there_is_no_overlay(
        qt_app, tmp_path, monkeypatch):
    from cadent.config_store import ConfigStore
    from cadent.settings_ui import SettingsWindow
    from cadent.theme.tokens import tokens

    monkeypatch.setattr(platform_pkg, "_current", no_overlay_platform())
    win = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                         tokens=tokens("dark"), devices=[])
    try:
        assert win.general.overlay_card.isVisibleTo(win.general) is False
        assert win.general.overlay_section.isVisibleTo(win.general) is False
    finally:
        win.close()


def test_the_general_pane_shows_the_overlay_rows_where_one_is_windowed(
        qt_app, tmp_path, monkeypatch):
    from cadent.config_store import ConfigStore
    from cadent.settings_ui import SettingsWindow
    from cadent.theme.tokens import tokens

    monkeypatch.setattr(platform_pkg, "_current", make_platform())
    win = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                         tokens=tokens("dark"), devices=[])
    try:
        assert win.general.overlay_card.isVisibleTo(win.general) is True
        assert win.general.move_button.isVisibleTo(win.general) is True
    finally:
        win.close()
