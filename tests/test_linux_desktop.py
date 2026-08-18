"""The Linux DesktopEnv, tray ink and the tray-less door (#39; spec M6 §10),
plus the small seams — autostart and single instance (#40; §8.4) — against
fakes. The portal itself, the panel and the AppIndicator extension are
hardware items (§12).
"""

import sys
from pathlib import Path

import pytest
from conftest import make_platform
from fake_portal import FakeBus

from cadent import platform as platform_pkg
from cadent.platform.linux import desktop as desktop_mod
from cadent.platform.linux import portal
from cadent.platform.linux.desktop import LinuxDesktop, tray_ink_for
from cadent.platform.linux.session import LinuxAutostart, LinuxSingleInstance, launch_command


def snapshot_bus(values):
    """A bus whose Settings portal answers ReadAll with `values`
    ({namespace: {key: (sig, value)}})."""
    bus = FakeBus()
    bus.replies[(portal.SETTINGS_IFACE, "ReadAll")] = (values,)
    return bus


def test_the_two_ink_map():
    assert tray_ink_for("gnome", 2) == "#ffffff"       # GNOME: white under both
    assert tray_ink_for("gnome", 1) == "#ffffff"
    assert tray_ink_for("kde", 1) == "#ffffff"         # dark → white
    assert tray_ink_for("kde", 2) == "#000000"         # light → black
    assert tray_ink_for("kde", 0) == "#ffffff"         # no preference → white
    assert tray_ink_for(None, None) == "#ffffff"


def test_the_snapshot_answers_every_desktop_read():
    bus = snapshot_bus({
        "org.freedesktop.appearance": {"color-scheme": ("u", 2), "contrast": ("u", 1)},
        "org.gnome.desktop.interface": {"text-scaling-factor": ("d", 1.25),
                                        "enable-animations": ("b", False)},
    })
    desk = LinuxDesktop(bus, "kde")
    assert desk.portal_present is True
    assert desk.tray_ink() == "#000000"
    assert desk.text_scale_factor() == 1.25
    assert desk.high_contrast() is True
    assert desk.animations_enabled() is False
    # One ReadAll, one subscription — nothing polled per read.
    assert len(bus.calls("ReadAll")) == 1
    for _ in range(3):
        desk.tray_ink()
    assert len(bus.calls("ReadAll")) == 1


def test_setting_changed_refreshes_the_snapshot_and_repaints_the_ink():
    bus = snapshot_bus({"org.freedesktop.appearance": {"color-scheme": ("u", 1)}})
    desk = LinuxDesktop(bus, "kde")
    repaints = []
    desk.watch_tray_ink(lambda: repaints.append(desk.tray_ink()))
    assert desk.tray_ink() == "#ffffff"
    bus.emit(portal.PORTAL_PATH, portal.SETTINGS_IFACE, "SettingChanged", "ssv",
             ("org.freedesktop.appearance", "color-scheme", ("u", 2)))
    assert repaints == ["#000000"]
    # Contrast never changes the ink; the callback stays quiet.
    bus.emit(portal.PORTAL_PATH, portal.SETTINGS_IFACE, "SettingChanged", "ssv",
             ("org.freedesktop.appearance", "contrast", ("u", 1)))
    assert repaints == ["#000000"]
    assert desk.high_contrast() is True
    desk.stop_watching_tray_ink()
    bus.emit(portal.PORTAL_PATH, portal.SETTINGS_IFACE, "SettingChanged", "ssv",
             ("org.freedesktop.appearance", "color-scheme", ("u", 1)))
    assert repaints == ["#000000"]


def test_gnome_stays_white_whatever_the_scheme_says():
    bus = snapshot_bus({"org.freedesktop.appearance": {"color-scheme": ("u", 2)}})
    assert LinuxDesktop(bus, "gnome").tray_ink() == "#ffffff"


def test_the_fallback_reads_hold_without_a_portal():
    bus = FakeBus()
    bus.replies[(portal.SETTINGS_IFACE, "ReadAll")] = portal.PortalError(
        "org.freedesktop.DBus.Error.ServiceUnknown")
    desk = LinuxDesktop(bus, "kde")
    assert desk.portal_present is False
    assert (desk.text_scale_factor(), desk.high_contrast(), desk.animations_enabled(),
            desk.tray_ink()) == (1.0, False, True, "#ffffff")
    desk = LinuxDesktop(None, "kde")           # no bus at all
    assert desk.tray_ink() == "#ffffff"


def test_animation_and_contrast_fallback_ladders():
    kde = LinuxDesktop(snapshot_bus({"org.kde.kdeglobals.KDE": {
        "AnimationDurationFactor": ("d", 0.0)}}), "kde")
    assert kde.animations_enabled() is False
    reduced = LinuxDesktop(snapshot_bus({"org.freedesktop.appearance": {
        "reduced-motion": ("u", 1)}}), "kde")
    assert reduced.animations_enabled() is False
    a11y = LinuxDesktop(snapshot_bus({"org.gnome.desktop.a11y.interface": {
        "high-contrast": ("b", True)}}), "gnome")
    assert a11y.high_contrast() is True
    assert a11y.text_scale_factor() == 1.0


def test_request_permission_is_wired_to_the_run_and_open_path_uses_xdg_open(monkeypatch):
    calls = []
    desk = LinuxDesktop(None, "kde", request_permission=lambda: calls.append("asked"))
    desk.request_permission()
    assert calls == ["asked"]
    LinuxDesktop(None, "kde").request_permission()       # X11: no-op
    launched = []
    monkeypatch.setattr(desktop_mod.subprocess, "Popen", lambda args: launched.append(args))
    desk.open_path(Path("/tmp/x.json"))
    assert launched == [["xdg-open", str(Path("/tmp/x.json"))]]


# ---- the tray-less door (§10.2) -----------------------------------------------

def settings_window(tmp_path, **kw):
    from cadent.config_store import ConfigStore
    from cadent.settings_ui import SettingsWindow
    from cadent.theme.tokens import tokens

    return SettingsWindow(ConfigStore(tmp_path / "config.json"),
                          tokens=tokens("dark"), devices=[], **kw)


def test_without_a_tray_host_settings_carries_pause_quit_and_the_row(qt_app, tmp_path,
                                                                     monkeypatch):
    monkeypatch.setattr(platform_pkg, "_current", make_platform())
    win = settings_window(tmp_path, tray_available=False)
    try:
        assert win.footer.isVisibleTo(win) is True
        assert win.general.tray_row is not None
        paused, quits = [], []
        win.pause_requested.connect(paused.append)
        win.quit_requested.connect(lambda: quits.append(True))
        win.pause_button.setChecked(True)
        win.quit_button.click()
        assert paused == [True] and quits == [True]
    finally:
        win.close()
    win = settings_window(tmp_path, tray_available=True)
    try:
        assert win.footer.isVisibleTo(win) is False
        assert win.general.tray_row is None
    finally:
        win.close()


def test_a_second_launch_opens_settings_only_when_tray_less(qt_app, monkeypatch):
    from cadent import app as app_mod

    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    shown = []
    instance._show_settings = lambda: shown.append(True)
    monkeypatch.setattr(app_mod.QSystemTrayIcon, "isSystemTrayAvailable",
                        staticmethod(lambda: False))
    instance._on_second_launch()
    monkeypatch.setattr(app_mod.QSystemTrayIcon, "isSystemTrayAvailable",
                        staticmethod(lambda: True))
    instance._on_second_launch()
    assert shown == [True]


# ---- autostart (§8.4) -----------------------------------------------------------

def test_the_autostart_entry_names_the_appimage_or_the_interpreter(tmp_path):
    env = {"APPIMAGE": "/home/me/Apps/Cadent-1.0-x86_64.AppImage"}
    auto = LinuxAutostart(tmp_path, env=env)
    assert auto.is_enabled() is False
    auto.set_enabled(True)
    text = auto.path.read_text()
    assert auto.path.name == "com.mikeallisonjs.cadent.desktop"
    assert "Exec=/home/me/Apps/Cadent-1.0-x86_64.AppImage\n" in text
    assert "TryExec=/home/me/Apps/Cadent-1.0-x86_64.AppImage\n" in text
    assert auto.is_enabled() is True
    auto.set_enabled(False)
    assert not auto.path.exists()
    assert launch_command({}) == [sys.executable, "-m", "cadent"] or \
        launch_command({}) == [sys.executable]


def test_a_moved_appimage_heals_the_entry_in_place(tmp_path):
    auto = LinuxAutostart(tmp_path, env={"APPIMAGE": "/old/Cadent.AppImage"})
    auto.set_enabled(True)
    moved = LinuxAutostart(tmp_path, env={"APPIMAGE": "/new/Cadent.AppImage"})
    assert moved.is_enabled() is True
    assert "Exec=/new/Cadent.AppImage" in auto.path.read_text()


def test_paths_with_spaces_are_quoted_the_desktop_entry_way(tmp_path):
    auto = LinuxAutostart(tmp_path, env={"APPIMAGE": "/home/me/My Apps/Cadent.AppImage"})
    auto.set_enabled(True)
    assert 'Exec="/home/me/My Apps/Cadent.AppImage"' in auto.path.read_text()


# ---- single instance (§8.4) --------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="fcntl.flock")
def test_flock_holds_the_lock_and_records_the_pid(tmp_path):
    first = LinuxSingleInstance(tmp_path / "cadent.lock")
    assert first.acquire() is True
    import os

    assert first.holder_pid() == os.getpid()
    second = LinuxSingleInstance(tmp_path / "cadent.lock")
    assert second.acquire() is False
    assert second.notify_running() is False       # our own pid: nothing to poke


def test_notify_running_without_a_holder_is_false(tmp_path):
    assert LinuxSingleInstance(tmp_path / "none.lock").notify_running() is False


def test_the_fallback_and_darwin_seams_answer_the_widened_protocol():
    from cadent.platform import fallback

    assert fallback.NullSingleInstance().notify_running() is False
    fallback.NullSingleInstance().watch(lambda: None)
