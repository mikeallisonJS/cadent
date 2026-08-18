"""Linux packaging inputs (#40; spec M6 §8, ADR 0011), checked on every OS:
the LOAD_BEARING rows and the excluded-on-purpose twin, one app id across
the spec, the platform files and the PKGBUILD, and the first-run desktop
entry writer against a temp XDG tree. The build itself runs on the
ubuntu-24.04 leg and on hardware (§12.12).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from cadent.platform.linux import desktopentry, session

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "scripts" / "build.py"
PKGBUILD = ROOT / "packaging" / "aur" / "PKGBUILD"
SPEC = ROOT / "packaging" / "cadent.spec"


def load_build():
    spec = importlib.util.spec_from_file_location("build_script", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_linux_load_bearing_rows_and_the_excluded_twin():
    build = load_build()
    rows = build.LOAD_BEARING["linux"]
    assert len(rows) == 11
    assert "*/onnxruntime/capi/libonnxruntime_providers_cuda.so" in rows
    assert "*/platforms/libqxcb.so" in rows and "*/platforms/libqwayland-generic.so" in rows
    assert build.EXCLUDED_ON_PURPOSE["linux"] == ["*/libasound.so*"]


def test_check_tree_fails_on_a_missing_row_and_on_a_bundled_libasound():
    build = load_build()
    complete = {
        "Cadent": 1, "_internal/libctranslate2.so.4": 1,
        "_internal/llama_cpp/lib/libllama.so": 1, "_internal/llama_cpp/lib/libggml-vulkan.so": 1,
        "_internal/faster_whisper/assets/silero_vad_v6.onnx": 1,
        "_internal/onnx_asr/preprocessors/data/nemo128.onnx": 1,
        "_internal/onnxruntime/capi/libonnxruntime_providers_cuda.so": 1,
        "_internal/PySide6/Qt/plugins/platforms/libqxcb.so": 1,
        "_internal/PySide6/Qt/plugins/platforms/libqwayland-generic.so": 1,
        "_internal/PySide6/Qt/plugins/wayland-shell-integration/libxdg-shell.so": 1,
        "_internal/libportaudio.so.2": 1, "_internal/libjack.so.0": 1,
    }
    assert build.check_tree(complete, "linux") == []
    without_cuda = dict(complete)
    del without_cuda["_internal/onnxruntime/capi/libonnxruntime_providers_cuda.so"]
    assert any("libonnxruntime_providers_cuda" in p for p in build.check_tree(without_cuda, "linux"))
    with_alsa = dict(complete, **{"_internal/libasound.so.2": 1})
    problems = build.check_tree(with_alsa, "linux")
    assert any("excluded on purpose" in p and "libasound" in p for p in problems)
    # win32 keeps its own rows and has no exclusion list.
    assert build.check_tree({}, "win32") != []


def test_one_app_id_everywhere():
    """`com.mikeallisonjs.cadent` is the .desktop basename, Icon=,
    StartupWMClass, setDesktopFileName's argument and the AUR entry — and
    equal to the darwin bundle id / LaunchAgent label (one identity per OS)."""
    from cadent import app as app_mod

    app_id = session.APP_ID
    assert app_id == app_mod.APP_DESKTOP_ID
    assert re.search(rf'BUNDLE_ID = "{re.escape(app_id)}"', SPEC.read_text(encoding="utf-8"))
    pkgbuild = PKGBUILD.read_text(encoding="utf-8")
    assert f"{app_id}.desktop" in pkgbuild
    assert f"Icon={app_id}" in pkgbuild and f"StartupWMClass={app_id}" in pkgbuild
    assert "depends=('alsa-lib'" in pkgbuild
    assert "pipewire-alsa" in pkgbuild
    entry = desktopentry.desktop_entry_text({"APPIMAGE": "/x/Cadent.AppImage"})
    assert f"Icon={app_id}\n" in entry and f"StartupWMClass={app_id}\n" in entry


def test_the_workflow_pins_ubuntu_24_04_and_stages_portaudio():
    text = (ROOT / ".github" / "workflows" / "build-installer-linux.yml").read_text()
    assert "runs-on: ubuntu-24.04" in text
    assert "libportaudio2" in text
    assert "scripts/build_linux.py" in text
    assert "build-installer-linux.yml" in (ROOT / ".github" / "workflows" /
                                           "tag-release.yml").read_text()


# ---- first-run desktop entry (§8.3) --------------------------------------------

def icons_tree(tmp_path):
    src = tmp_path / "icons" / "png"
    src.mkdir(parents=True, exist_ok=True)
    for size in (48, 128, 256):
        (src / f"app-{size}.png").write_bytes(b"\x89PNG" + bytes([size % 256]))
    return tmp_path / "icons"


def test_first_run_writes_the_entry_and_icons_once(tmp_path):
    home = tmp_path / "home"
    env = {"HOME": str(home), "XDG_DATA_DIRS": str(tmp_path / "usr"),
           "APPIMAGE": "/home/me/Cadent.AppImage"}
    assert desktopentry.ensure_desktop_entry(env, icons_dir=icons_tree(tmp_path)) is True
    entry = home / ".local/share/applications/com.mikeallisonjs.cadent.desktop"
    assert entry.exists()
    assert "Exec=/home/me/Cadent.AppImage" in entry.read_text()
    icon = home / ".local/share/icons/hicolor/256x256/apps/com.mikeallisonjs.cadent.png"
    assert icon.exists()
    # Idempotent: a second run writes nothing.
    assert desktopentry.ensure_desktop_entry(env, icons_dir=icons_tree(tmp_path)) is False


def test_a_system_wide_entry_makes_the_writer_stand_down(tmp_path):
    system = tmp_path / "usr" / "applications"
    system.mkdir(parents=True)
    (system / "com.mikeallisonjs.cadent.desktop").write_text("[Desktop Entry]\n")
    home = tmp_path / "home"
    env = {"HOME": str(home), "XDG_DATA_DIRS": str(tmp_path / "usr")}
    assert desktopentry.ensure_desktop_entry(env, icons_dir=icons_tree(tmp_path)) is False
    assert not (home / ".local").exists()


def test_the_bundled_icons_dir_carries_the_tiles():
    for size in desktopentry.HICOLOR_SIZES:
        assert (desktopentry.bundled_icons_dir() / "png" / f"app-{size}.png").exists()
