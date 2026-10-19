"""Desktop files: the app identity Linux uses on every tier (ADR 0009), and
the entry Cadent writes for itself (ADR 0011).

The identity stored in `AppOverride.process` and History is the freedesktop
**desktop-file id** — `org.mozilla.firefox`, `org.kde.konsole` — because it
is the one name that survives the X11↔Wayland flip on the same machine:
xdg-shell hands it over as `app_id`, and on X11 it resolves from `WM_CLASS`
the way every taskbar resolves a window to its launcher. Everything in here
is a pure XDG lookup over files, identical on all three tiers.
"""

from __future__ import annotations

import configparser
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..base import APP_ID

log = logging.getLogger(__name__)

_FLATPAK_EXPORTS = ("/var/lib/flatpak/exports/share",)


def data_home(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    home = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME", Path.home())) / ".local/share")
    return Path(home)


def config_home(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    home = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME", Path.home())) / ".config")
    return Path(home)


def application_dirs(env: dict | None = None) -> list[Path]:
    """`$XDG_DATA_HOME/applications` first — a user entry shadows the system
    one — then each `$XDG_DATA_DIRS` entry's, then Flatpak's exports (which
    distros usually put on `XDG_DATA_DIRS` already; listed explicitly so a
    session that forgot still sees them). Deduplicated, order kept."""
    env = os.environ if env is None else env
    data_dirs = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [data_home(env)] + [Path(p) for p in data_dirs.split(":") if p]
    roots += [data_home(env) / "flatpak/exports/share"]
    roots += [Path(p) for p in _FLATPAK_EXPORTS]
    seen: list[Path] = []
    for root in roots:
        path = root / "applications"
        if path not in seen:
            seen.append(path)
    return seen


@dataclass(frozen=True)
class DesktopApp:
    id: str                 # the desktop-file id: "org.mozilla.firefox"
    name: str               # the localized Name=
    wm_class: str           # StartupWMClass=, lowercased, or ""
    exec_name: str          # basename of the first Exec= word, lowercased, or ""
    path: Path
    listed: bool            # Type=Application, neither NoDisplay nor Hidden


def _locale_keys(env: dict | None = None) -> list[str]:
    """`Name[xx_YY]`, `Name[xx]` lookups in order, from the session locale."""
    env = os.environ if env is None else env
    lang = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    lang = lang.split(".")[0].split("@")[0]
    if not lang or lang in ("C", "POSIX"):
        return []
    keys = [lang]
    if "_" in lang:
        keys.append(lang.split("_")[0])
    return keys


def parse_desktop_file(path: Path, desktop_id: str,
                       env: dict | None = None) -> DesktopApp | None:
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str   # keys are case-sensitive in desktop entries
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            parser.read_file(f)
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    entry = parser["Desktop Entry"]
    name = ""
    for key in _locale_keys(env):
        name = entry.get(f"Name[{key}]", "")
        if name:
            break
    name = name or entry.get("Name", "") or desktop_id
    listed = (entry.get("Type", "") == "Application"
              and entry.get("NoDisplay", "false").lower() != "true"
              and entry.get("Hidden", "false").lower() != "true")
    exec_name = ""
    exec_line = entry.get("TryExec", "") or entry.get("Exec", "")
    if exec_line:
        first = exec_line.split()[0] if exec_line.split() else ""
        exec_name = Path(first).name.lower()
    return DesktopApp(id=desktop_id, name=name,
                      wm_class=entry.get("StartupWMClass", "").lower(),
                      exec_name=exec_name, path=path, listed=listed)


class DesktopIndex:
    """Every `.desktop` file the session can see, keyed by id. First match by
    id wins across the search path, so a user-installed entry shadows the
    system one and the picker deduplicates the same way.

    Built lazily and rebuilt on demand — the picker relists on every visit
    of the pane, which is the one moment a stale index could show."""

    def __init__(self, dirs: list[Path] | None = None, env: dict | None = None) -> None:
        self._env = env
        self._dirs = dirs if dirs is not None else application_dirs(env)
        self._apps: dict[str, DesktopApp] | None = None

    def refresh(self) -> None:
        self._apps = None

    def apps(self) -> dict[str, DesktopApp]:
        if self._apps is None:
            self._apps = self._scan()
        return self._apps

    def _scan(self) -> dict[str, DesktopApp]:
        found: dict[str, DesktopApp] = {}
        for root in self._dirs:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.desktop")):
                # Subdirectories join the id with '-' per the spec:
                # applications/kde/foo.desktop → kde-foo.
                desktop_id = "-".join(path.relative_to(root).with_suffix("").parts)
                if desktop_id in found:
                    continue
                app = parse_desktop_file(path, desktop_id, self._env)
                if app is not None:
                    found[desktop_id] = app
        return found

    # ---- the three questions the seam asks ------------------------------------

    def installed(self) -> list[tuple[str, str]]:
        """(Name, id) for every listed application, sorted by name — the
        picker's rows on every tier."""
        rows = [(app.name, app.id) for app in self.apps().values() if app.listed]
        return sorted(rows, key=lambda pair: pair[0].lower())

    def display_name(self, identity: str) -> str | None:
        """The localized `Name=` behind an id, running or not; None when no
        entry carries that id and the row shows the raw identity."""
        wanted = identity.lower()
        for app in self.apps().values():
            if app.id.lower() == wanted:
                return app.name
        return None

    def id_for_wm_class(self, instance: str, cls: str) -> str | None:
        """`WM_CLASS` → desktop-file id (ADR 0009): a file whose id stem or
        `StartupWMClass=` matches either half, case-insensitively; the exe
        basename of `Exec=` as the last resort the launcher heuristics use.
        None when nothing resolves — the caller falls to the pid's exe."""
        candidates = [c.lower() for c in (cls, instance) if c]
        if not candidates:
            return None
        apps = self.apps().values()
        for candidate in candidates:
            for app in apps:
                if app.id.lower() == candidate or app.id.lower().split(".")[-1] == candidate:
                    return app.id
        for candidate in candidates:
            for app in apps:
                if app.wm_class and app.wm_class == candidate:
                    return app.id
        for candidate in candidates:
            for app in apps:
                if app.exec_name and app.exec_name == candidate:
                    return app.id
        return None


# ---- Cadent's own entry (ADR 0011) ---------------------------------------------------

_ICON_SIZES = (48, 128, 256)


def exec_command() -> str:
    """What a launcher runs: the AppImage itself where we are one (its
    `sys.executable` is an ephemeral `/tmp/.mount_*`), the frozen binary,
    or the interpreter on the module. Resolved at write time, because
    desktop entries expand no variables."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return appimage
    if getattr(sys, "frozen", False):
        return sys.executable
    return f"{sys.executable} -m cadent"


def desktop_entry_text(exec_line: str) -> str:
    try_exec = exec_line.split()[0]
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Cadent",
        "Comment=Push-to-talk dictation that runs entirely on this PC",
        f"Exec={exec_line}",
        f"TryExec={try_exec}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Utility;Accessibility;",
        f"StartupWMClass={APP_ID}",
        "X-GNOME-UsesNotifications=true",
        "",
    ])


def system_entry_exists(env: dict | None = None) -> bool:
    """A system-wide entry (the AUR package's) is authoritative; Cadent
    writes nothing beside it."""
    return any((root / f"{APP_ID}.desktop").exists()
               for root in application_dirs(env)[1:])


def write_desktop_entry(icons_dir: Path, env: dict | None = None,
                        exec_line: str | None = None) -> bool:
    """Write `$XDG_DATA_HOME/applications/<APP_ID>.desktop` and the hicolor
    icons, idempotently — unchanged content is not rewritten. False when a
    system entry exists or nothing could be written; never raises."""
    if system_entry_exists(env):
        return False
    home = data_home(env)
    try:
        text = desktop_entry_text(exec_line or exec_command())
        target = home / "applications" / f"{APP_ID}.desktop"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8")
        for size in _ICON_SIZES:
            source = icons_dir / "png" / f"app-{size}.png"
            if not source.exists():
                continue
            dest = home / "icons/hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.stat().st_size != source.stat().st_size:
                dest.write_bytes(source.read_bytes())
        return True
    except OSError:
        log.debug("could not write the desktop entry", exc_info=True)
        return False
