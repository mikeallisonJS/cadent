"""Linux packaging (spec m6 §8): the bundle check, launcher identity,
autostart and the flock single instance. Adapter internals take throwaway
directories, so nothing here touches the real home."""

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("build_script", ROOT / "scripts" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

if sys.platform == "win32":
    pytest.skip("Linux adapter internals", allow_module_level=True)
pytest.importorskip("jeepney")

from cadent.platform.linux import autostart, desktop_files, identity  # noqa: E402
from cadent.platform.linux.instance import FlockSingleInstance  # noqa: E402

# ---- LOAD_BEARING --------------------------------------------------------------

GOOD = [
    "Cadent",
    "_internal/ctranslate2/libctranslate2-abc.so.4",
    "_internal/llama_cpp/lib/libllama.so",
    "_internal/llama_cpp/lib/libggml-vulkan.so",
    "_internal/faster_whisper/assets/silero_vad_v6.onnx",
    "_internal/onnx_asr/preprocessors/data/nemo128.onnx",
    "_internal/onnxruntime/capi/libonnxruntime_providers_cuda.so",
    "_internal/PySide6/Qt/plugins/platforms/libqxcb.so",
    "_internal/PySide6/Qt/plugins/platforms/libqwayland-generic.so",
    "_internal/PySide6/Qt/plugins/wayland-shell-integration/libxdg-shell.so",
    "_internal/libportaudio.so.2",
    "_internal/libjack.so.0",
]


def test_linux_has_eleven_load_bearing_rows():
    assert len(build.LOAD_BEARING["linux"]) == 11


def test_a_complete_bundle_passes():
    assert build.check_bundle(GOOD, "linux") == []


@pytest.mark.parametrize("row", range(11))
def test_a_bundle_missing_any_row_fails(row):
    pattern = build.LOAD_BEARING["linux"][row]
    remaining = [f for f in GOOD if not build.fnmatch(f, pattern)]
    problems = build.check_bundle(remaining, "linux")
    assert problems == [f"missing load-bearing file: {pattern}"]


def test_a_bundled_libasound_fails_the_build():
    problems = build.check_bundle([*GOOD, "_internal/libasound.so.2"], "linux")
    assert len(problems) == 1 and "libasound.so.2" in problems[0]


def test_libjack_is_present_in_a_good_bundle():
    assert any(f.endswith("libjack.so.0") for f in GOOD)
    assert build.check_bundle(GOOD, "linux") == []


# ---- desktop entry and identity ------------------------------------------------


def _env(tmp_path, **extra):
    return {"XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_DATA_DIRS": str(tmp_path / "system"), **extra}


def test_first_run_writes_entry_and_icons(tmp_path):
    env = _env(tmp_path)
    assert identity.install(env, "/opt/cadent/Cadent") is True
    entry = tmp_path / "data" / "applications" / "com.mikeallisonjs.cadent.desktop"
    text = entry.read_text()
    assert "Exec=/opt/cadent/Cadent" in text
    assert "Icon=com.mikeallisonjs.cadent" in text
    assert "StartupWMClass=com.mikeallisonjs.cadent" in text
    for size in (48, 128, 256):
        assert (tmp_path / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
                / "com.mikeallisonjs.cadent.png").is_file()


def test_install_is_idempotent(tmp_path):
    env = _env(tmp_path)
    identity.install(env, "/opt/cadent/Cadent")
    entry = tmp_path / "data" / "applications" / "com.mikeallisonjs.cadent.desktop"
    before = entry.stat().st_mtime_ns
    identity.install(env, "/opt/cadent/Cadent")
    assert entry.stat().st_mtime_ns == before


def test_a_system_entry_stays_authoritative(tmp_path):
    system = tmp_path / "system" / "applications"
    system.mkdir(parents=True)
    (system / "com.mikeallisonjs.cadent.desktop").write_text("[Desktop Entry]\n")
    assert identity.install(_env(tmp_path), "/opt/cadent/Cadent") is False
    assert not (tmp_path / "data").exists()


def test_appimage_path_wins_over_the_ephemeral_mount(tmp_path):
    env = _env(tmp_path, APPIMAGE="/home/me/Apps/Cadent.AppImage")
    identity.install(env, "/tmp/.mount_CadentXYZ/usr/lib/cadent/Cadent")
    text = (tmp_path / "data" / "applications"
            / "com.mikeallisonjs.cadent.desktop").read_text()
    assert "Exec=/home/me/Apps/Cadent.AppImage" in text
    assert ".mount_" not in text


def test_paths_with_spaces_are_quoted():
    assert identity.quote("/home/me/My Apps/Cadent") == '"/home/me/My Apps/Cadent"'


# ---- autostart -----------------------------------------------------------------


def _autostart(tmp_path, executable="/opt/cadent/Cadent", **env):
    return autostart.XdgAutostart(tmp_path / "autostart", env=env,
                                  executable=executable)


def test_enable_writes_exec_and_tryexec(tmp_path):
    a = _autostart(tmp_path)
    a.set_enabled(True)
    assert a.is_enabled()
    fields = desktop_files.parse((tmp_path / "autostart"
                              / "com.mikeallisonjs.cadent.desktop").read_text())
    assert fields["Exec"] == fields["TryExec"] == "/opt/cadent/Cadent"


def test_disable_removes_the_entry_and_is_quiet_when_absent(tmp_path):
    a = _autostart(tmp_path)
    a.set_enabled(False)
    a.set_enabled(True)
    a.set_enabled(False)
    assert not a.is_enabled()


def test_autostart_follows_a_moved_appimage(tmp_path):
    _autostart(tmp_path, APPIMAGE="/home/me/old/Cadent.AppImage").set_enabled(True)
    moved = _autostart(tmp_path, APPIMAGE="/home/me/new/Cadent.AppImage")
    assert moved.is_enabled()
    fields = desktop_files.parse((tmp_path / "autostart"
                              / "com.mikeallisonjs.cadent.desktop").read_text())
    assert fields["Exec"] == fields["TryExec"] == "/home/me/new/Cadent.AppImage"


def test_autostart_label_is_start_at_login():
    from cadent.platform.linux import Session, capabilities_for
    assert capabilities_for(Session("x11", ())).autostart_label == "Start at login"


# ---- single instance -----------------------------------------------------------


def _lock_in_child(lock) -> int:
    code = ("import fcntl, os, sys\n"
            f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT)\n"
            "try:\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except OSError:\n    sys.exit(1)\n")
    return subprocess.run([sys.executable, "-c", code]).returncode


def test_first_acquire_wins_and_holds(tmp_path):
    lock = tmp_path / "cadent.lock"
    first = FlockSingleInstance(lock)
    assert first.acquire() is True
    assert _lock_in_child(lock) == 1
    assert FlockSingleInstance(lock).acquire() is False


def test_second_launch_knocks_on_the_running_instance(tmp_path):
    lock = tmp_path / "cadent.lock"
    first = FlockSingleInstance(lock)
    assert first.acquire()
    knocked = threading.Event()
    first.listen(knocked.set)
    assert FlockSingleInstance(lock).acquire() is False
    assert knocked.wait(2)


def test_a_launch_with_nobody_listening_is_quiet(tmp_path):
    lock = tmp_path / "cadent.lock"
    holder = FlockSingleInstance(lock)
    assert holder.acquire()
    assert FlockSingleInstance(lock).acquire() is False


def test_the_desktop_file_name_is_the_entry_id():
    from cadent.platform.linux import Session, capabilities_for
    caps = capabilities_for(Session("wayland", ("kde",)))
    assert caps.desktop_file_name == identity.APP_ID == "com.mikeallisonjs.cadent"
