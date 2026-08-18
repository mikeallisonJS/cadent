"""First-run desktop integration (spec M6 §8.3, ADR 0011).

Nothing installs a desktop entry for a tarball or an AppImage, so on first
run Cadent writes `$XDG_DATA_HOME/applications/com.mikeallisonjs.cadent.desktop`
(default `~/.local/share/applications`) plus the tile at 48/128/256 into the
user's hicolor theme — idempotently, and **skipped where a system-wide entry
exists** (the AUR package stays authoritative). `com.mikeallisonjs.cadent` is
the `.desktop` basename, `Icon=`, `StartupWMClass` and what
`QGuiApplication.setDesktopFileName()` is given — the only thing Qt derives
the Wayland `app_id` from.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from .session import APP_ID, _desktop_quote, launch_command

log = logging.getLogger(__name__)

HICOLOR_SIZES = (48, 128, 256)


def bundled_icons_dir() -> Path:
    """Where the rasterised tiles live, frozen or from a checkout — the same
    rule `cadent.icons.icons_dir` follows, repeated here because that module
    imports Qt and this package may not (ADR 0013)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "icons"
    return Path(__file__).resolve().parents[3] / "packaging" / "icons"


def data_home(env: Mapping[str, str]) -> Path:
    home = Path(env.get("HOME") or Path.home())
    return Path(env.get("XDG_DATA_HOME") or str(home / ".local" / "share"))


def system_entry_exists(env: Mapping[str, str]) -> bool:
    dirs = [d for d in env.get("XDG_DATA_DIRS", "").split(":") if d] \
        or ["/usr/local/share", "/usr/share"]
    return any((Path(d) / "applications" / f"{APP_ID}.desktop").exists() for d in dirs)


def desktop_entry_text(env: Mapping[str, str]) -> str:
    command = launch_command(env)
    exec_line = " ".join(_desktop_quote(part) for part in command)
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Cadent\n"
            "GenericName=Dictation\n"
            "Comment=Push-to-talk dictation that types where you are\n"
            f"Exec={exec_line}\n"
            f"TryExec={command[0]}\n"
            f"Icon={APP_ID}\n"
            "Terminal=false\n"
            "Categories=Utility;Accessibility;\n"
            f"StartupWMClass={APP_ID}\n"
            "Keywords=dictation;speech;voice;typing;\n")


def ensure_desktop_entry(env: Mapping[str, str] | None = None,
                         icons_dir: Path | None = None) -> bool:
    """Write the entry and icons if this user has none and no package
    provides one. Returns True when something was written; never raises."""
    env = os.environ if env is None else env
    try:
        if system_entry_exists(env):
            return False
        base = data_home(env)
        entry = base / "applications" / f"{APP_ID}.desktop"
        wanted = desktop_entry_text(env)
        wrote = False
        if not entry.exists() or entry.read_text(encoding="utf-8") != wanted:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(wanted, encoding="utf-8")
            wrote = True
        source = icons_dir or bundled_icons_dir()
        for size in HICOLOR_SIZES:
            src = source / "png" / f"app-{size}.png"
            if not src.exists():
                continue
            dest = base / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                wrote = True
        return wrote
    except OSError:
        log.debug("desktop entry could not be written", exc_info=True)
        return False
