"""The Linux platform: tier detection and assembly (spec §1–2, ADR 0013).

`create()` reads the session type and desktop **once** and fills the same
frozen `Capabilities` every other OS does; nothing is re-probed afterwards. It
owns the one `Portal` connection and hands it to every seam adapter by
constructor. Adapters whose real behaviour lands in later tickets are filled
from `fallback`'s inert ones, so the app starts on every tier today.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from typing import Literal

from .. import fallback
from ..base import Capabilities, PermissionPreflight, Platform
from ..keycodes import LINUX_KEYCODES
from . import desktopfiles
from .portal import Portal

Tier = Literal["whole", "portal", "reduced"]

# Desktops by the token `XDG_CURRENT_DESKTOP` carries, lower-cased. A desktop
# not listed here is simply not named: the summary drops the clause rather than
# print a raw environment value (spec §9.4).
DESKTOP_NAMES = {
    "kde": "KDE Plasma", "gnome": "GNOME", "sway": "Sway",
    "hyprland": "Hyprland", "xfce": "Xfce", "x-cinnamon": "Cinnamon",
    "cinnamon": "Cinnamon", "mate": "MATE", "lxqt": "LXQt",
    "budgie": "Budgie", "cosmic": "COSMIC", "pantheon": "Pantheon",
}

# Literals, not `config.Config()`: its defaults read this very factory.
WHOLE_DEFAULT_COMBO = "<ctrl>+<cmd>"
WHOLE_DEFAULT_CLEANUP_COMBO = "<ctrl>+<shift>+<alt>"
WAYLAND_DEFAULT_COMBO = "<ctrl>+<cmd>+space"
WAYLAND_DEFAULT_CLEANUP_COMBO = "<ctrl>+<cmd>+c"

PORTAL_PERMISSION = PermissionPreflight(
    name="portal",
    banner=("Cadent needs your desktop's permission to listen for its "
            "shortcut and to type for you. Approve the prompts when your "
            "desktop asks."),
    wizard_body=("On this desktop Cadent asks permission to listen for its "
                 "shortcut and to type what you dictate. Choose Allow in the "
                 "prompts, then come back here."),
    action_label="Ask my desktop",
    waiting="Waiting for the permission — Cadent notices the moment it's granted.",
    granted="Permission granted — Cadent can listen and type for you.",
)


@dataclasses.dataclass(frozen=True)
class Session:
    kind: Literal["x11", "wayland"]
    desktops: tuple[str, ...]       # lower-cased XDG_CURRENT_DESKTOP tokens

    @property
    def tier(self) -> Tier:
        if self.kind == "x11":
            return "whole"
        return "reduced" if "gnome" in self.desktops else "portal"

    @property
    def desktop_name(self) -> str | None:
        for token in self.desktops:
            if token in DESKTOP_NAMES:
                return DESKTOP_NAMES[token]
        return None


def detect_session(env: Mapping[str, str]) -> Session:
    """Session type and desktop from the environment. An unset or unknown
    `XDG_SESSION_TYPE` (a bare tty, a container) falls back to which display
    variable exists, and then to X11 — the whole tier, which asks the least
    of a bus that may not be there."""
    kind = env.get("XDG_SESSION_TYPE", "").lower()
    if kind not in ("x11", "wayland"):
        kind = "wayland" if env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY") \
            else "x11"
    desktops = tuple(t.strip().lower() for t in
                     env.get("XDG_CURRENT_DESKTOP", "").split(":") if t.strip())
    return Session(kind, desktops)


def tier_summary(session: Session) -> str:
    """The finished sentence Settings ▸ General shows (spec §9.4)."""
    where = f"{'X11' if session.kind == 'x11' else 'Wayland'} session"
    if session.desktop_name:
        where += f" on {session.desktop_name}"
    if session.tier == "whole":
        return f"{where} — Whole support: everything Cadent does works here."
    if session.tier == "portal":
        return (f"{where} — Portal support: your desktop owns the hotkey, "
                "Cadent won't learn on its own which apps need pasting, and "
                "there's no overlay.")
    return f"{where} — Reduced support: no overlay, no per-app overrides."


def capabilities_for(session: Session) -> Capabilities:
    whole = session.tier == "whole"
    reduced = session.tier == "reduced"
    return dataclasses.replace(
        fallback.CAPABILITIES,
        keycode_table=LINUX_KEYCODES,
        default_injection_strategy="type" if whole else "clipboard",
        injection_rungs=("type", "paste") if whole else ("paste", "type"),
        default_overrides=(),
        default_override_reasons={},
        auto_learn_overrides=whole,
        per_app_overrides=not reduced,
        stt_runtimes={"faster-whisper": ("auto", "cuda", "cpu"),
                      "parakeet": ("auto", "cuda", "cpu")},
        gpu_only_engines=frozenset(),
        permission=None if whole else PORTAL_PERMISSION,
        autostart_label="Start at login",
        app_identity_placeholder="org.mozilla.firefox",
        app_picker=True,
        tray_click_toggles_pause=False,
        modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                           "option": "Alt", "win": "Super", "cmd": "Super"},
        theme_subtitle="Follows your desktop's colour scheme",
        high_contrast_reason=("Your desktop is set to higher contrast, so "
                              "Cadent is following your system colours"),
        overlay="windowed" if whole else None,
        support_tier=session.tier,
        support_tier_summary=tier_summary(session),
        default_combo=WHOLE_DEFAULT_COMBO if whole else WAYLAND_DEFAULT_COMBO,
        default_cleanup_combo=(WHOLE_DEFAULT_CLEANUP_COMBO if whole
                               else WAYLAND_DEFAULT_CLEANUP_COMBO),
    )


# One class per seam, each holding the shared connection. Behaviour is the
# fallback's inert version until the ticket for that seam lands; the point of
# the skeleton is that the wiring — one `Portal`, constructor-injected — is
# already the shape the real adapters will fill.

class _PortalAdapter:
    def __init__(self, portal: Portal) -> None:
        self._portal = portal


class LinuxKeyboard(_PortalAdapter, fallback.NullKeyboard): ...
class LinuxClipboard(_PortalAdapter, fallback.MemoryClipboard):
    def __init__(self, portal: Portal) -> None:
        fallback.MemoryClipboard.__init__(self)
        _PortalAdapter.__init__(self, portal)


class LinuxFocusedApp(_PortalAdapter, fallback.NullFocusedApp):
    def name(self) -> str:
        return "unknown"

    def running_apps(self) -> list[tuple[str, str]]:
        """Installed applications, not running ones (§5.2): `(Name, id)`."""
        return [(e.name, e.id) for e in desktopfiles.installed_apps()]

    def display_name(self, identity: str) -> str | None:
        return desktopfiles.display_name(identity)


class LinuxHotkeyTap(_PortalAdapter, fallback.NullHotkeyTap): ...
class LinuxHardware(_PortalAdapter, fallback.NullHardware): ...
class LinuxAutostart(_PortalAdapter, fallback.NullAutostart): ...
class LinuxSingleInstance(_PortalAdapter, fallback.NullSingleInstance): ...
class LinuxDesktop(_PortalAdapter, fallback.NullDesktop): ...


def create(env: Mapping[str, str] | None = None,
           portal: Portal | None = None) -> Platform:
    session = detect_session(os.environ if env is None else env)
    portal = portal or Portal()
    return Platform(
        capabilities=capabilities_for(session),
        keyboard=LinuxKeyboard(portal),
        clipboard=LinuxClipboard(portal),
        focused_app=LinuxFocusedApp(portal),
        hotkey_tap=LinuxHotkeyTap(portal),
        hardware=LinuxHardware(portal),
        autostart=LinuxAutostart(portal),
        single_instance=LinuxSingleInstance(portal),
        desktop=LinuxDesktop(portal),
    )
