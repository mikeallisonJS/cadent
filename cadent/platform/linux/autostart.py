"""Start at login via an XDG autostart entry (spec §8.4, ADR 0011).

`Exec=` and `TryExec=` carry the same absolute path, written resolved because
desktop entries expand no variables. `TryExec=` is what makes a deleted
tarball or a moved AppImage disable itself at login instead of erroring.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .desktop_files import parse
from .identity import APP_ID, launcher_path, quote

ENTRY_NAME = f"{APP_ID}.desktop"


def config_home(env: Mapping[str, str]) -> Path:
    return Path(env.get("XDG_CONFIG_HOME") or "~/.config").expanduser()


def entry_text(path: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Cadent",
        f"Exec={quote(path)}",
        f"TryExec={path}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


class XdgAutostart:
    """`autostart_dir`, `env` and `executable` are parameters so tests write
    to a throwaway directory and never touch the real autostart folder."""

    def __init__(self, autostart_dir: Path | None = None,
                 env: Mapping[str, str] | None = None,
                 executable: str | None = None) -> None:
        self._env = os.environ if env is None else env
        self._dir = autostart_dir or config_home(self._env) / "autostart"
        self._executable = executable or sys.executable

    @property
    def _path(self) -> Path:
        return self._dir / ENTRY_NAME

    def run_command(self) -> str:
        return launcher_path(self._env, self._executable)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(entry_text(self.run_command()), encoding="utf-8")
        else:
            self._path.unlink(missing_ok=True)

    def is_enabled(self) -> bool:
        """Whether the entry exists. An entry whose path no longer matches
        the binary running is rewritten in place first: the checkbox said on,
        so it stays on and points at what is actually running."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return False
        wanted = entry_text(self.run_command())
        if text != wanted and parse(text).get("TryExec") != self.run_command():
            self._path.write_text(wanted, encoding="utf-8")
        return True
