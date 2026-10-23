"""The platform-conditional surface over the Linux columns (spec §11),
built by pinning each tier's facts — never by the host desktop: the tier
row, the overlay gating, the pane notes, the permission step's copy, the
Done-page line, the two grant faults, the tray-less footer and the
failure-to-notification routing."""

import types

import pytest
from conftest import make_platform, pin_linux_ui_platform
from PySide6.QtWidgets import QLabel

from cadent import app as app_mod
from cadent import hardware
from cadent.config import Config
from cadent.config_store import ConfigStore
from cadent.settings_ui import SettingsWindow
from cadent.settings_ui.general import NO_TRAY
from cadent.settings_ui.hotkeys import DESKTOP_OWNS
from cadent.settings_ui.overrides import NO_PASTE, NOT_APPLIED
from cadent.theme.tokens import tokens
from cadent.tray import Tray
from cadent.wizard import DONE, PERMISSION, SetupWizard


@pytest.fixture(autouse=True)
def _a_model_is_on_disk(monkeypatch):
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: True)


def window(tmp_path, **kwargs):
    return SettingsWindow(ConfigStore(tmp_path / "config.json"), tokens=tokens("dark"),
                          devices=[], **kwargs)


def labels(widget):
    return [w.text() for w in widget.findChildren(QLabel)]


# ---- General: the tier row and the overlay card (§9.4, ADR 0014) -----------------

def test_the_general_pane_names_the_tier_on_linux_and_not_elsewhere(qt_app, tmp_path,
                                                                     monkeypatch):
    plat = pin_linux_ui_platform(monkeypatch, tier="portal")
    win = window(tmp_path)
    assert plat.capabilities.support_tier_summary in labels(win.general)
    assert win.general.tier_row is not None
    win.close()
    monkeypatch.setattr("cadent.platform._current", make_platform())
    win = window(tmp_path)
    assert win.general.tier_row is None
    win.close()


def test_overlay_rows_exist_only_where_there_is_an_overlay(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="whole")
    win = window(tmp_path)
    assert win.general.overlay_card.isVisibleTo(win) is True
    win.close()
    pin_linux_ui_platform(monkeypatch, tier="portal")
    win = window(tmp_path)
    assert win.general.overlay_card.isVisibleTo(win) is False
    assert win.general.overlay_caps.isVisibleTo(win) is False
    win.close()


# ---- the tray-less door (§10.2) -------------------------------------------------------

def test_a_tray_less_session_gets_pause_and_quit_in_the_window(qt_app, tmp_path,
                                                               monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="reduced")
    win = window(tmp_path, tray_available=False, paused=False)
    assert win.footer.isVisibleTo(win) is True
    assert NO_TRAY in labels(win.general)
    assert win.general.tray_row.isVisibleTo(win) is True
    pauses, quits = [], []
    win.pause_requested.connect(pauses.append)
    win.quit_requested.connect(lambda: quits.append(1))
    win.pause_button.click()
    assert pauses == [True]
    assert win.pause_button.text() == "Resume dictation"
    win.quit_button.click()
    assert quits == [1]
    # A host arrives mid-session: the footer and the row go away.
    win.set_tray_available(True)
    assert win.footer.isVisibleTo(win) is False
    assert win.general.tray_row.isVisibleTo(win) is False
    win.close()


def test_the_footer_is_absent_where_a_tray_exists(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="whole")
    win = window(tmp_path, tray_available=True)
    assert win.footer.isVisibleTo(win) is False
    win.set_paused(True)
    assert win.pause_button.isChecked() is True
    win.close()


# ---- Hotkeys: the desktop owns the binding (§4) ----------------------------------------

def test_the_hotkeys_pane_shows_the_desktops_binding(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="portal",
                          bound={"dictate": "Ctrl+Meta+Space", "cleanup": "<ctrl>+<cmd>+c"})
    win = window(tmp_path)
    text = win.hotkeys.desktop_binding.text()
    assert win.hotkeys.desktop_binding.isVisibleTo(win.hotkeys) is True
    assert DESKTOP_OWNS in text
    assert "Ctrl+Meta+Space" in text
    assert "Ctrl+Super+C" in text          # the substituted chord, in Linux words
    win.close()
    pin_linux_ui_platform(monkeypatch, tier="whole")
    win = window(tmp_path)
    assert win.hotkeys.desktop_binding.isVisibleTo(win.hotkeys) is False
    win.close()


def test_the_note_explains_a_stored_chord_the_desktop_could_not_bind(qt_app, tmp_path,
                                                                     monkeypatch):
    """A modifiers-only cleanup chord (the X11 default) has no portal trigger,
    so the desktop kept an earlier binding while the button still shows the
    stored chord. The note has to say why the two disagree."""
    (tmp_path / "config.json").write_text(
        '{"hotkey": "<ctrl>+<cmd>+space", "cleanup_hotkey": "<ctrl>+<shift>+<alt>"}')
    pin_linux_ui_platform(monkeypatch, tier="portal",
                          bound={"dictate": "Ctrl+Meta+Space", "cleanup": "Meta+Ctrl+C"})
    win = window(tmp_path)
    text = win.hotkeys.desktop_binding.text()
    assert "Cleanup is bound to Meta+Ctrl+C" in text
    assert "Ctrl+Shift+Alt, has no key and can't bind here" in text
    assert "Dictation is bound to Ctrl+Meta+Space." in text      # bindable: no note
    win.close()


def test_left_and_right_are_offered_only_where_cadent_binds_the_hotkey(qt_app, tmp_path,
                                                                       monkeypatch):
    """The portal's trigger grammar has no sides: a Right Ctrl chord binds as
    either Ctrl, so the toggle's promise is false on the Wayland tiers."""
    pin_linux_ui_platform(monkeypatch, tier="portal")
    win = window(tmp_path)
    assert "Tell left from right" not in labels(win.hotkeys)
    win.close()
    pin_linux_ui_platform(monkeypatch, tier="whole")
    win = window(tmp_path)
    assert "Tell left from right" in labels(win.hotkeys)
    win.close()


def test_the_hotkeys_pane_says_super_not_win(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="whole")
    win = window(tmp_path)
    assert any("Super" in text for text in labels(win.hotkeys))
    assert not any("Win" in text.split() for text in labels(win.hotkeys))
    win.close()


# ---- Overrides: the Reduced note and the no-paste note (§5.2, §9.4) -------------------

def test_the_overrides_pane_stays_editable_with_a_note_on_reduced(qt_app, tmp_path,
                                                                   monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="reduced", running=[("Firefox", "org.mozilla.firefox")])
    win = window(tmp_path)
    assert win.overrides.session_note.isVisibleTo(win.overrides) is True
    assert NOT_APPLIED in win.overrides.session_note.text()
    assert win.overrides.add_button.isEnabled()
    assert win.overrides.add_combo.lineEdit().placeholderText() == "org.mozilla.firefox"
    assert win.overrides.add_combo.itemText(0) == "Firefox — org.mozilla.firefox"
    win.close()


def test_the_overrides_pane_says_when_pasting_is_unavailable(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="reduced", paste=False)
    win = window(tmp_path)
    text = win.overrides.session_note.text()
    assert NO_PASTE in text and NOT_APPLIED in text
    win.close()
    pin_linux_ui_platform(monkeypatch, tier="portal")
    win = window(tmp_path)
    assert win.overrides.session_note.isVisibleTo(win.overrides) is False
    win.close()


def empty_state_of(tmp_path):
    win = window(tmp_path)
    for override in list(win.store.config.app_overrides):
        win.store.config.app_overrides.remove(override)
    win.overrides.reload()
    text = win.overrides.empty.text()
    win.close()
    return text


def test_the_empty_state_says_a_paste_first_tier_pastes(qt_app, tmp_path, monkeypatch):
    """The Windows line — types everywhere, learns on failure — is false twice
    over on a tier that pastes first and never learns (ADR 0007)."""
    pin_linux_ui_platform(monkeypatch, tier="portal")
    text = empty_state_of(tmp_path)
    assert "pastes into every app" in text
    assert "automatically" not in text


def test_the_empty_state_says_typing_where_pasting_is_unavailable(qt_app, tmp_path,
                                                                    monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="portal", paste=False)
    text = empty_state_of(tmp_path)
    assert "types into every app" in text
    assert "automatically" not in text


def test_the_empty_state_teaches_auto_learn_where_x11_learns(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="whole")
    assert "one automatically if typing fails" in empty_state_of(tmp_path)


def test_the_empty_state_says_macos_pastes_too(qt_app, tmp_path, monkeypatch):
    from conftest import pin_darwin_ui_platform

    pin_darwin_ui_platform(monkeypatch)
    assert "pastes into every app" in empty_state_of(tmp_path)


# ---- the permission surfaces (§9, ADR 0012) ---------------------------------------------

def test_the_banner_carries_the_portal_copy_and_asks_through_the_seam(qt_app, tmp_path,
                                                                       monkeypatch):
    from PySide6.QtWidgets import QPushButton

    plat = pin_linux_ui_platform(monkeypatch, tier="portal", granted=False)
    win = window(tmp_path)
    assert win.permission_banner.isVisibleTo(win) is True
    assert win.permission_banner.message.text() == plat.capabilities.permission.banner
    win.permission_banner.findChild(QPushButton).click()
    assert plat.desktop.permission_requests == 1
    plat.focused_app.granted = True
    win.refresh_permission()
    assert win.permission_banner.isVisibleTo(win) is False
    win.close()


def test_the_wizard_step_uses_the_platforms_words_and_never_gates(qt_app, tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    plat = pin_linux_ui_platform(monkeypatch, tier="reduced", granted=False)
    wiz = SetupWizard(ConfigStore(tmp_path / "config.json"), devices=[])
    try:
        assert PERMISSION in wiz.state.pages()
        while wiz.page != PERMISSION:
            wiz.advance()
        grant = plat.capabilities.permission
        assert wiz.open_settings.text() == grant.action_label
        assert grant.wizard_body in labels(wiz)
        assert wiz.permission_status.text() == grant.waiting
        assert wiz.next_button.isEnabled() is True
        wiz.open_settings.click()
        assert plat.desktop.permission_requests == 1
        plat.focused_app.granted = True
        wiz.refresh_permission()
        assert wiz.permission_status.text() == grant.granted
        # A denial retries nothing: the button is still there to click again.
        assert wiz.open_settings.isEnabled()
    finally:
        wiz._completed = True
        wiz.close()
        wiz.deleteLater()


def test_the_done_page_carries_the_tier_line_only_on_the_wayland_tiers(qt_app, tmp_path,
                                                                        monkeypatch):
    for tier, expected in (("portal", True), ("reduced", True), ("whole", False)):
        plat = pin_linux_ui_platform(monkeypatch, tier=tier)
        wiz = SetupWizard(ConfigStore(tmp_path / f"{tier}.json"), devices=[])
        try:
            while wiz.page != DONE:
                wiz.advance()
            assert (plat.capabilities.support_tier_summary in labels(wiz)) is expected
        finally:
            wiz._completed = True
            wiz.close()
            wiz.deleteLater()


def test_the_hotkey_copy_names_the_bound_chord_on_wayland(qt_app, tmp_path, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="portal", bound={"dictate": "<ctrl>+<cmd>+space"})
    wiz = SetupWizard(ConfigStore(tmp_path / "config.json"), devices=[])
    try:
        while wiz.page != DONE:
            wiz.advance()
        assert any("Ctrl+Super+Space" in text for text in labels(wiz))
    finally:
        wiz._completed = True
        wiz.close()
        wiz.deleteLater()


# ---- the two grant faults and the failure routing, in the app (§9.3, §10.3) -----------

class FakeTheme:
    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _T(QObject):
            changed = Signal()

        self._t = _T()
        self.changed = self._t.changed
        self.high_contrast = False


def bare_app(plat):
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.config = Config()
    instance.platform = plat
    instance.store = types.SimpleNamespace(readable=True)
    instance.bridge = app_mod._Bridge()
    instance.theme = FakeTheme()
    instance.settings_window = None
    instance.wizard = None
    instance._setup_tray()
    return instance


def test_the_faults_are_mutually_exclusive(qt_app, monkeypatch):
    """Interface absent (stock Sway) → hotkey-unavailable; present and
    ungranted → permission-needed; never both."""
    plat = pin_linux_ui_platform(monkeypatch, tier="portal", granted=False,
                                 hotkey_available=False)
    app = bare_app(plat)
    try:
        app._poll_permission()
        concerns = app.tray.ledger.concerns()
        assert "this desktop can't give Cadent a hotkey" in concerns
        assert "permission needed" not in concerns
        plat.hotkey_tap._available = True
        app._poll_permission()
        concerns = app.tray.ledger.concerns()
        assert "permission needed" in concerns
        assert "this desktop can't give Cadent a hotkey" not in concerns
        plat.focused_app.granted = True
        app._poll_permission()
        assert app.tray.ledger.concerns() == []
    finally:
        app.tray.icon.hide()


def test_no_fault_is_raised_where_there_is_no_preflight(qt_app, monkeypatch):
    plat = pin_linux_ui_platform(monkeypatch, tier="whole", granted=False)
    app = bare_app(plat)
    try:
        app._poll_permission()
        assert app.tray.ledger.concerns() == []
    finally:
        app.tray.icon.hide()


def test_a_failure_with_no_overlay_reaches_the_tray_exactly_once(qt_app, monkeypatch):
    """No start/stop chatter, and one desktop notification per failure —
    the report handler's toast, which Qt hands to the notification daemon
    with or without a tray host (ADR 0014)."""
    from cadent.pipeline import DictationReport

    plat = pin_linux_ui_platform(monkeypatch, tier="portal")
    app = bare_app(plat)
    shown = []
    app.tray.message = lambda title, *rest: shown.append(title)
    app.overlay = app_mod.TrayOverlay(app._notify_failure)
    app._dropped_hotwords = []
    app._toast_target = None
    try:
        app.overlay.show_recording()
        app.overlay.show_transcribing()
        app.overlay.hide_overlay()
        assert shown == []
        app._handle_report(DictationReport(outcome="failed", detail="boom", text=""))
        assert shown == ["Cadent — dictation failed"]
        app._handle_report(DictationReport(outcome="inserted", text="hello"))
        assert shown == ["Cadent — dictation failed"]         # success stays silent
    finally:
        app.tray.icon.hide()


def test_the_tray_item_names_the_edition_for_the_engine(qt_app, monkeypatch):
    pin_linux_ui_platform(monkeypatch, tier="whole")
    widget = Tray(on_toggle_cleanup=lambda _on: None, on_toggle_pause=lambda _on: None,
                  on_settings=lambda: None, on_history=lambda: None,
                  on_wizard=lambda: None, on_gpu_download=lambda: None,
                  on_quit=lambda: None, stt_engine="parakeet")
    try:
        assert "~1.3 GB" in widget.gpu_action.text()
        widget.set_gpu_edition("faster-whisper")
        assert "~600 MB" in widget.gpu_action.text()
    finally:
        widget.icon.hide()
