"""The Linux small seams and the desktop-file index, over temp directories
on any OS that can import them (spec §5, §8.3, §8.4, §10; ADR 0009, 0011,
0014). The portal snapshot is driven from a dict; the flock and wake socket
run for real, as the darwin twin does."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("jeepney", reason="jeepney is a Linux-only dependency")
from cadent.platform.base import APP_ID  # noqa: E402
from cadent.platform.linux import apps, shell  # noqa: E402

# ---- desktop files (ADR 0009) --------------------------------------------------

FIREFOX = """[Desktop Entry]
Type=Application
Name=Firefox
Name[de]=Feuerfuchs
Exec=firefox %u
StartupWMClass=firefox
"""
KONSOLE = """[Desktop Entry]
Type=Application
Name=Konsole
Exec=konsole
"""
HIDDEN = """[Desktop Entry]
Type=Application
Name=Secret
NoDisplay=true
Exec=secret
"""
SERVICE = """[Desktop Entry]
Type=Service
Name=A daemon
Exec=daemon
"""


@pytest.fixture
def dirs(tmp_path):
    user = tmp_path / "home/.local/share/applications"
    system = tmp_path / "usr/share/applications"
    for d in (user, system):
        d.mkdir(parents=True)
    (system / "org.mozilla.firefox.desktop").write_text(FIREFOX)
    (system / "org.kde.konsole.desktop").write_text(KONSOLE)
    (system / "hidden.desktop").write_text(HIDDEN)
    (system / "daemon.desktop").write_text(SERVICE)
    (system / "kde").mkdir()
    (system / "kde" / "nested.desktop").write_text(KONSOLE.replace("Konsole", "Nested"))
    return user, system


def index(dirs, env=None):
    return apps.DesktopIndex(dirs=list(dirs), env=env or {})


def test_the_picker_lists_installed_applications_only(dirs):
    rows = index(dirs).installed()
    assert ("Firefox", "org.mozilla.firefox") in rows
    assert ("Konsole", "org.kde.konsole") in rows
    assert ("Nested", "kde-nested") in rows             # subdirectory ids join with '-'
    assert not any(identity == "hidden" for _n, identity in rows)
    assert not any(identity == "daemon" for _n, identity in rows)
    assert rows == sorted(rows, key=lambda r: r[0].lower())


def test_display_names_resolve_for_apps_that_are_not_running(dirs):
    assert index(dirs).display_name("org.mozilla.firefox") == "Firefox"
    assert index(dirs).display_name("ORG.MOZILLA.FIREFOX") == "Firefox"
    assert index(dirs).display_name("no.such.app") is None


def test_names_are_localized_from_the_session_locale(dirs):
    assert index(dirs, {"LANG": "de_DE.UTF-8"}).display_name("org.mozilla.firefox") == \
        "Feuerfuchs"


def test_a_user_entry_shadows_the_system_one(dirs):
    user, _system = dirs
    (user / "org.mozilla.firefox.desktop").write_text(FIREFOX.replace("Firefox", "Mine"))
    assert index(dirs).display_name("org.mozilla.firefox") == "Mine"
    assert sum(1 for _n, i in index(dirs).installed() if i == "org.mozilla.firefox") == 1


def test_wm_class_resolves_to_the_desktop_file_id(dirs):
    ix = index(dirs)
    assert ix.id_for_wm_class("Navigator", "firefox") == "org.mozilla.firefox"
    assert ix.id_for_wm_class("konsole", "konsole") == "org.kde.konsole"
    assert ix.id_for_wm_class("org.kde.konsole", "org.kde.konsole") == "org.kde.konsole"
    assert ix.id_for_wm_class("x", "nothing-here") is None
    assert ix.id_for_wm_class("", "") is None


def test_application_dirs_follow_xdg_and_add_flatpak_exports():
    env = {"XDG_DATA_HOME": "/h/.local/share", "XDG_DATA_DIRS": "/a:/b", "HOME": "/h"}
    found = apps.application_dirs(env)
    assert found[:3] == [Path("/h/.local/share/applications"), Path("/a/applications"),
                         Path("/b/applications")]
    assert Path("/var/lib/flatpak/exports/share/applications") in found


# ---- Cadent's own entry (ADR 0011) -----------------------------------------------

def test_first_run_writes_the_entry_and_icons(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_DATA_DIRS": str(tmp_path / "sys")}
    icons = tmp_path / "icons/png"
    icons.mkdir(parents=True)
    (icons / "app-48.png").write_bytes(b"png48")
    assert apps.write_desktop_entry(tmp_path / "icons", env, "/opt/Cadent.AppImage") is True
    entry = tmp_path / "data/applications" / f"{APP_ID}.desktop"
    text = entry.read_text()
    assert "Exec=/opt/Cadent.AppImage" in text
    assert "TryExec=/opt/Cadent.AppImage" in text
    assert f"StartupWMClass={APP_ID}" in text
    assert f"Icon={APP_ID}" in text
    assert (tmp_path / "data/icons/hicolor/48x48/apps" / f"{APP_ID}.png").read_bytes() == b"png48"
    stamp = entry.stat().st_mtime_ns
    time.sleep(0.01)
    apps.write_desktop_entry(tmp_path / "icons", env, "/opt/Cadent.AppImage")
    assert entry.stat().st_mtime_ns == stamp       # idempotent


def test_a_system_entry_is_authoritative(tmp_path):
    system = tmp_path / "sys/applications"
    system.mkdir(parents=True)
    (system / f"{APP_ID}.desktop").write_text("[Desktop Entry]\nType=Application\n")
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_DATA_DIRS": str(tmp_path / "sys")}
    assert apps.write_desktop_entry(tmp_path / "icons", env, "/x") is False
    assert not (tmp_path / "data/applications").exists()


def test_the_exec_line_prefers_the_appimage_path(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/me/Apps/Cadent.AppImage")
    assert apps.exec_command() == "/home/me/Apps/Cadent.AppImage"
    monkeypatch.delenv("APPIMAGE")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", "/opt/cadent/Cadent")
    assert apps.exec_command() == "/opt/cadent/Cadent"
    monkeypatch.setattr("sys.frozen", False, raising=False)
    assert apps.exec_command().endswith("-m cadent")


# ---- autostart (spec §8.4) --------------------------------------------------------

def test_enable_writes_an_xdg_autostart_entry(tmp_path):
    autostart = shell.LinuxAutostart(tmp_path / "autostart",
                                     exec_line=lambda: "/opt/Cadent.AppImage")
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    text = (tmp_path / "autostart" / f"{APP_ID}.desktop").read_text()
    assert "Exec=/opt/Cadent.AppImage" in text
    assert "TryExec=/opt/Cadent.AppImage" in text
    assert autostart.run_command() == "/opt/Cadent.AppImage"


def test_disable_deletes_the_entry_and_is_quiet_when_absent(tmp_path):
    autostart = shell.LinuxAutostart(tmp_path / "autostart", exec_line=lambda: "/x")
    autostart.set_enabled(False)
    autostart.set_enabled(True)
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_a_stale_exec_is_healed_in_place(tmp_path):
    """The AppImage moved: the checkbox said on, so it stays on and points at
    the binary actually running."""
    where = ["/old/Cadent.AppImage"]
    autostart = shell.LinuxAutostart(tmp_path / "autostart", exec_line=lambda: where[0])
    autostart.set_enabled(True)
    where[0] = "/new/Cadent.AppImage"
    assert autostart.is_enabled()
    text = (tmp_path / "autostart" / f"{APP_ID}.desktop").read_text()
    assert "Exec=/new/Cadent.AppImage" in text


# ---- single instance (spec §8.4, ADR 0014) ----------------------------------------

def _flock_in_child(lock) -> int:
    program = (
        "import fcntl, os, sys\n"
        f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    return subprocess.run([sys.executable, "-c", program]).returncode


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="flock")
def test_the_lock_is_exclusive_and_dies_with_the_holder(tmp_path):
    lock = tmp_path / "Cadent" / "cadent.lock"
    instance = shell.LinuxSingleInstance(lock)
    assert instance.acquire() is True
    assert _flock_in_child(lock) == 1
    assert shell.LinuxSingleInstance(tmp_path / "other.lock").acquire() is True
    assert _flock_in_child(tmp_path / "fresh.lock") == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="unix sockets")
def test_a_second_launch_wakes_the_holder(tmp_path):
    lock = tmp_path / "cadent.lock"
    holder = shell.LinuxSingleInstance(lock)
    assert holder.acquire()
    woken = []
    holder.on_wake(lambda: woken.append(1))
    second = shell.LinuxSingleInstance(lock)
    assert second.acquire() is False
    second.wake_other()
    for _ in range(100):
        if woken:
            break
        time.sleep(0.01)
    assert woken == [1]
    # Nobody listening: a knock is a no-op, never a raise.
    os.unlink(lock.with_suffix(".sock"))
    second.wake_other()


# ---- the hardware probes (ADR 0010) ------------------------------------------------

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc and libcuda")
def test_the_probes_answer_without_raising():
    probe = shell.LinuxHardware()
    assert isinstance(probe.processor_name(), str)
    assert probe.dx12_gpu_present() is False
    assert probe.metal_gpu_present() is False
    assert probe.nvidia_driver_present() in (True, False)
    version = probe.driver_cuda_version()
    assert version is None or version >= 1000
    assert probe.cuda_total_memory() is None or probe.cuda_total_memory() > 0


# ---- DesktopEnv: ink and the snapshot (ADR 0014) ---------------------------------

def test_the_ink_is_keyed_desktop_first_then_scheme():
    assert shell.tray_ink_for("gnome", shell.SCHEME_LIGHT) == "#ffffff"
    assert shell.tray_ink_for("gnome", shell.SCHEME_DARK) == "#ffffff"
    assert shell.tray_ink_for("kde", shell.SCHEME_LIGHT) == "#000000"
    assert shell.tray_ink_for("kde", shell.SCHEME_DARK) == "#ffffff"
    assert shell.tray_ink_for("kde", shell.SCHEME_NO_PREFERENCE) == "#ffffff"
    assert shell.tray_ink_for("", None) == "#ffffff"


def test_the_snapshot_reads_each_fact_with_its_fallbacks(tmp_path):
    snap = shell.SettingsSnapshot({
        shell.APPEARANCE: {"color-scheme": 2, "contrast": 1},
        shell.GNOME_INTERFACE: {"text-scaling-factor": 1.5},
    })
    assert snap.color_scheme() == 2
    assert snap.high_contrast() is True
    assert snap.text_scale() == 1.5
    assert snap.animations() is True               # nothing says otherwise
    snap.update(shell.APPEARANCE, "reduced-motion", 1)
    assert snap.animations() is False
    empty = shell.SettingsSnapshot()
    assert empty.text_scale() == 1.0 and empty.high_contrast() is False
    gnome = shell.SettingsSnapshot({shell.GNOME_INTERFACE: {"enable-animations": False},
                                    shell.GNOME_A11Y: {"high-contrast": True}})
    assert gnome.animations() is False and gnome.high_contrast() is True
    kde = tmp_path / "kdeglobals"
    kde.write_text("[General]\nfoo=1\n[KDE]\nAnimationDurationFactor=0\n")
    assert shell.SettingsSnapshot().animations(kde) is False
    assert shell.kdeglobals_animations(tmp_path / "missing") is None


def test_no_portal_means_the_fallback_answers_and_no_fault(tmp_path):
    desktop = shell.LinuxDesktop(None, "gnome", kdeglobals=tmp_path / "none")
    assert desktop.text_scale_factor() == 1.0
    assert desktop.high_contrast() is False
    assert desktop.animations_enabled() is True
    assert desktop.tray_ink() == "#ffffff"
    desktop.watch_tray_ink(lambda: None)        # nothing to watch, nothing raised
    desktop.stop_watching_tray_ink()
    desktop.request_permission()
    desktop.install_desktop_entry()
