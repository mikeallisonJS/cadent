"""Cadent's own launcher identity on Linux (spec §8.3, ADR 0011).

A tarball and an AppImage have no install step, so nothing writes a desktop
entry for them. On first run Cadent writes its own and the hicolor icons.
The one reverse-DNS id is the `.desktop` basename, `Icon=`, `StartupWMClass` and
`Capabilities.desktop_file_name`, which Qt derives the Wayland `app_id` from.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "com.mikeallisonjs.cadent"
ICON_SIZES = (48, 128, 256)


def icons_dir() -> Path:
    """The bundled PNGs, frozen or from a checkout (`cadent.icons` is the
    Qt-side twin; the platform package imports no GUI toolkit)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "icons"
    return Path(__file__).resolve().parents[3] / "packaging" / "icons"


def data_home(env: Mapping[str, str]) -> Path:
    """`$XDG_DATA_HOME`, else `~/.local/share` — the only write target;
    `$XDG_DATA_DIRS` is a search path."""
    return Path(env.get("XDG_DATA_HOME") or "~/.local/share").expanduser()


def system_entry_exists(env: Mapping[str, str]) -> bool:
    """A desktop file of our id in a system-wide data dir — a package manager
    installed it, so it stays authoritative."""
    home = data_home(env).resolve()
    dirs = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return any((Path(d) / "applications" / f"{APP_ID}.desktop").is_file()
               for d in dirs if d and Path(d).expanduser().resolve() != home)


def entry_text(exec_path: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Cadent",
        "Comment=Local push-to-talk dictation",
        f"Exec={quote(exec_path)}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Utility;AudioVideo;",
        f"StartupWMClass={APP_ID}",
        "",
    ])


def quote(path: str) -> str:
    """A path as one `Exec=` argument: desktop entries expand no variables,
    and a space or reserved character needs double quotes (Desktop Entry
    spec, "The Exec key")."""
    if not any(c in path for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return path
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in path)
    return f'"{escaped}"'.replace("%", "%%")


def launcher_path(env: Mapping[str, str], executable: str) -> str:
    """The absolute path to launch: `$APPIMAGE` where set — inside an
    AppImage `sys.executable` is an ephemeral `/tmp/.mount_*` — else the
    running executable."""
    appimage = env.get("APPIMAGE")
    return str(Path(appimage).absolute()) if appimage else executable


def install(env: Mapping[str, str], executable: str,
            icon_source: Path | None = None) -> bool:
    """Write the desktop entry and hicolor PNGs, idempotently. Returns False
    when a system-wide entry exists and nothing was written."""
    if system_entry_exists(env):
        return False
    home = data_home(env)
    source = icon_source or icons_dir() / "png"
    for size in ICON_SIZES:
        target = (home / "icons" / "hicolor" / f"{size}x{size}" / "apps"
                  / f"{APP_ID}.png")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / f"cadent-{size}.png", target)
    entry = home / "applications" / f"{APP_ID}.desktop"
    text = entry_text(launcher_path(env, executable))
    if not entry.exists() or entry.read_text(encoding="utf-8") != text:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(text, encoding="utf-8")
    return True


def register(env: Mapping[str, str] | None = None,
             executable: str | None = None) -> None:
    """Install the launcher entry. Never raises: a read-only home costs the
    menu entry, not the app."""
    try:
        install(os.environ if env is None else env, executable or sys.executable)
    except OSError:
        log.exception("could not write the desktop entry")
