"""The Linux platform: three support tiers, one build (spec M6 §1–§2).

`create()` probes `XDG_SESSION_TYPE` + `XDG_CURRENT_DESKTOP` **once**, names
the tier — **Whole** on X11, **Portal** on Wayland under Plasma / wlroots /
SteamOS desktop, **Reduced** on GNOME Wayland — fills the frozen
`Capabilities` for it, opens the one jeepney connection (`portal.py`) the
portal seams share, and assembles the eight adapters. Nothing is re-probed
after start; a tier's name is not a promise its bus can keep, so the Wayland
adapters probe their interfaces and drop rungs for the run with copy that
says so (§1.3).

Only the `current()` factory imports this package, and only on Linux; `Xlib`,
`jeepney` and anything reading the portals live under it and nowhere else
(ADR 0005, ADR 0013). Nothing here imports the GUI toolkit.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Mapping

from ..base import Capabilities, PermissionPreflight, Platform
from ..fallback import CAPABILITIES as FALLBACK_CAPABILITIES
from ..gpu_packs import LINUX_EDITIONS
from ..keycodes import LINUX_KEYCODES

log = logging.getLogger(__name__)

WHOLE, PORTAL, REDUCED = "whole", "portal", "reduced"

# `XDG_CURRENT_DESKTOP` is a colon-separated list ("ubuntu:GNOME", "KDE");
# the first entry we know names the desktop. Only these words ever reach
# copy — on no match the desktop clause is dropped, never the raw variable
# (spec §9.4).
KNOWN_DESKTOPS: Mapping[str, str] = {
    "kde": "KDE Plasma",
    "gnome": "GNOME",
    "ubuntu": "GNOME",           # Ubuntu's session names itself first
    "sway": "Sway",
    "hyprland": "Hyprland",
    "xfce": "Xfce",
    "x-cinnamon": "Cinnamon",
    "cinnamon": "Cinnamon",
    "mate": "MATE",
    "lxqt": "LXQt",
    "budgie": "Budgie",
    "budgie:gnome": "Budgie",
    "cosmic": "COSMIC",
    "deepin": "Deepin",
    "pantheon": "Pantheon",
    "river": "River",
    "niri": "Niri",
    "labwc": "Labwc",
}

# The desktops whose Wayland session lands on Reduced: no toplevel protocol
# and no data-control — GNOME's Mutter, and the desktops built on it.
GNOME_FAMILY = frozenset({"gnome", "ubuntu", "budgie", "pantheon"})


@dataclasses.dataclass(frozen=True)
class SessionInfo:
    """What the environment said, once, at startup."""

    tier: str                    # WHOLE / PORTAL / REDUCED
    session_type: str            # "x11" / "wayland"
    desktop_key: str | None      # the matched KNOWN_DESKTOPS key, or None
    desktop_name: str | None     # its human name, or None

    @property
    def wayland(self) -> bool:
        return self.session_type == "wayland"


def desktop_parts(env: Mapping[str, str]) -> list[str]:
    raw = env.get("XDG_CURRENT_DESKTOP", "")
    return [part.strip().lower() for part in raw.split(":") if part.strip()]


def detect(env: Mapping[str, str] | None = None) -> SessionInfo:
    """Decide the tier from `XDG_SESSION_TYPE` and `XDG_CURRENT_DESKTOP`.

    A missing or `tty` session type falls back on `WAYLAND_DISPLAY`: set →
    Wayland, else X11 (the common case for a login shell exec'ing a
    compositor). Wayland under the GNOME family is Reduced; every other
    Wayland desktop is Portal; X11 anywhere is Whole.
    """
    env = os.environ if env is None else env
    session_type = env.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type not in ("x11", "wayland"):
        session_type = "wayland" if env.get("WAYLAND_DISPLAY") else "x11"
    parts = desktop_parts(env)
    key = next((p for p in parts if p in KNOWN_DESKTOPS), None)
    name = KNOWN_DESKTOPS.get(key) if key else None
    if session_type == "x11":
        tier = WHOLE
    elif any(p in GNOME_FAMILY for p in parts):
        tier = REDUCED
    else:
        tier = PORTAL
    return SessionInfo(tier=tier, session_type=session_type,
                       desktop_key=key, desktop_name=name)


# ---- the tier line (spec §9.4) -----------------------------------------------

_SESSION_WORDS = {"x11": "X11 session", "wayland": "Wayland session"}

_TIER_CLAUSES = {
    WHOLE: "Whole support: everything Cadent does works here.",
    PORTAL: ("Portal support: your desktop owns the hotkey, Cadent won't learn "
             "on its own which apps need pasting, and there's no overlay."),
    REDUCED: "Reduced support: no overlay, no per-app overrides.",
}


def support_tier_summary(info: SessionInfo) -> str:
    """The finished sentence Settings ▸ General shows — built here, never by
    cross-platform UI. Tier word first, then its consequence; the desktop
    clause only for a known name."""
    where = _SESSION_WORDS[info.session_type]
    if info.desktop_name:
        where = f"{where} on {info.desktop_name}"
    return f"{where} — {_TIER_CLAUSES[info.tier]}"


# ---- the permission preflight (spec §9, ADR 0012) ----------------------------

# One Wayland wording for Portal and Reduced: "your desktop", never a desktop
# or portal name; "the prompts" without a count (the number of dialogs
# depends on the compositor).
PORTAL_PERMISSION = PermissionPreflight(
    name="portal",
    banner=("Cadent needs your desktop's permission to hear the hotkey and "
            "type what you dictate. Ask for it, then accept the prompts."),
    wizard_body=("Your desktop asks before an app can hear a global shortcut "
                 "or type into other windows. Ask for permission below and "
                 "accept the prompts your desktop shows; Cadent notices the "
                 "moment they're granted."),
    action_label="Ask for permission",
    waiting=("Waiting for your desktop — accept the prompts and Cadent will "
             "notice."),
    granted="Permission granted — Cadent can hear the hotkey and type for you.",
)


# ---- Capabilities per tier (spec §2.4) ---------------------------------------

# Three rungs for both engines (ADR 0010): the CUDA provider is a runtime
# choice on Linux for faster-whisper *and* Parakeet — the two-edition GPU pack
# supplies the userspace either needs.
STT_RUNTIMES: dict[str, tuple[str, ...]] = {
    "faster-whisper": ("auto", "cuda", "cpu"),
    "parakeet": ("auto", "cuda", "cpu"),
}

WAYLAND_DEFAULT_COMBO = "<ctrl>+<cmd>+space"
WAYLAND_DEFAULT_CLEANUP_COMBO = "<ctrl>+<cmd>+c"


def capabilities_for(info: SessionInfo, *,
                     paste_available: bool = True,
                     per_app_overrides: bool | None = None) -> Capabilities:
    """The Linux column for one tier. `paste_available` is what the Wayland
    adapters found at startup (no data-control and no Clipboard portal →
    rungs collapse to typing); `per_app_overrides` overrides the tier's
    default for a Portal run whose compositor offers no toplevel protocol."""
    shared = dict(
        keycode_table=LINUX_KEYCODES,
        paste_chord="ctrl+v",
        default_overrides=(),
        default_override_reasons={},
        stt_runtimes=STT_RUNTIMES,
        gpu_only_engines=frozenset(),
        show_runtime_combo=True,
        gpu_pack_available=True,
        gpu_pack_editions=LINUX_EDITIONS,
        # Bench: 0.66 s median at 4+ cores / 8 GB (linux-parakeet-cpu-bench).
        parakeet_cpu_floor=(4, 8.0),
        autostart_label="Start at login",
        app_identity_placeholder="org.mozilla.firefox",
        app_picker=True,
        mic_permission_hint=None,
        tray_click_toggles_pause=False,
        modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                           "option": "Alt", "win": "Super", "cmd": "Super"},
        tray_icon_painted_by_os=False,
        theme_subtitle="Follows your desktop's colour scheme",
        high_contrast_reason=("Your desktop is set to higher contrast, so "
                              "Cadent is following your system colours"),
        support_tier=info.tier,
        support_tier_summary=support_tier_summary(info),
    )
    if info.tier == WHOLE:
        return dataclasses.replace(
            FALLBACK_CAPABILITIES, **shared,
            default_injection_strategy="type",
            injection_rungs=("type", "paste"),
            auto_learn_overrides=True,
            per_app_overrides=True,
            permission=None,
            overlay="windowed",
            default_combo="<ctrl>+<cmd>",
            default_cleanup_combo="<ctrl>+<shift>+<alt>",
        )
    if per_app_overrides is None:
        per_app_overrides = info.tier == PORTAL
    return dataclasses.replace(
        FALLBACK_CAPABILITIES, **shared,
        default_injection_strategy="clipboard",
        # Paste-first with no automatic fall-through (ADR 0007); typing is
        # the only rung when the run has no paste mechanism — not a
        # fall-through, the copy says so.
        injection_rungs=("paste", "type") if paste_available else ("type",),
        auto_learn_overrides=False,
        per_app_overrides=per_app_overrides,
        permission=PORTAL_PERMISSION,
        overlay=None,
        default_combo=WAYLAND_DEFAULT_COMBO,
        default_cleanup_combo=WAYLAND_DEFAULT_CLEANUP_COMBO,
    )


# ---- assembly ----------------------------------------------------------------

def open_portal_connection():
    """The shared jeepney connection, or None where no session bus answers —
    every portal seam then behaves as if its interface were absent."""
    from .portal import PortalConnection, PortalUnavailable

    try:
        return PortalConnection.open()
    except PortalUnavailable as exc:
        log.warning("no session bus: %s — portal seams disabled for this run",
                    exc)
        return None


def create() -> Platform:
    from .desktop import LinuxDesktop
    from .hardware import LinuxHardware
    from .session import LinuxAutostart, LinuxSingleInstance

    info = detect()
    bus = open_portal_connection()
    request_permission = None
    if info.tier == WHOLE:
        caps = capabilities_for(info)
        keyboard, clipboard, focused_app, hotkey_tap = _whole_adapters()
    else:
        run = _wayland_run(info, bus)
        caps = capabilities_for(info, paste_available=run.paste_available,
                                per_app_overrides=(info.tier == PORTAL
                                                   and run.per_app_overrides))
        keyboard, clipboard = run.keyboard, run.clipboard
        focused_app, hotkey_tap = run.focused_app, run.tap
        request_permission = run.request_permission
        run.warm()
    log.info("Linux support tier: %s (%s)", info.tier, caps.support_tier_summary)
    plat = Platform(
        capabilities=caps,
        keyboard=keyboard,
        clipboard=clipboard,
        focused_app=focused_app,
        hotkey_tap=hotkey_tap,
        hardware=LinuxHardware(),
        autostart=LinuxAutostart(),
        single_instance=LinuxSingleInstance(),
        # One Settings-portal snapshot for every desktop read (§10.4); the
        # ink watcher rides the same connection.
        desktop=LinuxDesktop(bus, info.desktop_key, request_permission),
    )
    if bus is not None:
        bus.arm_gui_guard()
    return plat


def _whole_adapters():
    """The X11 seams (#34), sharing one `SendGate` so the tap can tell our
    XTEST fakes from the user's keys (spec §3)."""
    from .desktopfiles import DesktopIndex
    from .x11 import SendGate, X11Clipboard, X11FocusedApp, X11HotkeyTap, X11Keyboard

    gate = SendGate()
    index = DesktopIndex()
    return (X11Keyboard(gate), X11Clipboard(), X11FocusedApp(index),
            X11HotkeyTap(gate))


def _wayland_run(info: SessionInfo, bus):
    """The Wayland tiers (#35, #36): probe the compositor and the portals
    once and assemble the seams around what is there."""
    from ... import config
    from .desktopfiles import DesktopIndex
    from .wayland import WaylandClient, WaylandError
    from .wayland_tiers import TokenStore, WaylandRun

    client = None
    try:
        client = WaylandClient.connect()
    except WaylandError as exc:
        log.warning("no Wayland connection (%s); compositor protocols off", exc)
    except Exception:
        log.warning("Wayland connection failed", exc_info=True)
    return WaylandRun(bus, tier=info.tier,
                      default_combo=WAYLAND_DEFAULT_COMBO,
                      default_cleanup_combo=WAYLAND_DEFAULT_CLEANUP_COMBO,
                      token_store=TokenStore(config.DATA_DIR / "portal-tokens.json"),
                      desktop_index=DesktopIndex(), wayland_client=client)


__all__ = [
    "PORTAL", "PORTAL_PERMISSION", "REDUCED", "STT_RUNTIMES", "SessionInfo",
    "WHOLE", "capabilities_for", "create", "detect", "support_tier_summary",
]
