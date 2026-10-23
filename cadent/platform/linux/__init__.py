"""The Linux platform: three support tiers, one build (spec §1, ADR 0005).

`current()` on Linux lands here. `detect_tier` reads `XDG_SESSION_TYPE` and
`XDG_CURRENT_DESKTOP` **once** and names the tier — Whole on X11, Portal on
Wayland under Plasma / wlroots / SteamOS desktop, Reduced on GNOME Wayland —
and `create()` assembles the eight adapters for it: X11's from `x11.py`, the
Wayland tiers' from `wayland.py` over one jeepney connection (`portal.py`)
and, where the compositor offers them, the wire protocols in `wire.py`.
Nothing is re-probed after start; a mechanism found missing drops its rung
for the run and the copy says so (§1.3) — never a fourth tier, never a
forced XWayland fallback.

Only the factory imports this package, and only on Linux: jeepney, Xlib and
everything that reads `/dev` or the portals lives below this line.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ... import config
from ..base import APP_ID, Capabilities, GpuPackEdition, PermissionPreflight, Platform
from ..fallback import CAPABILITIES, MemoryClipboard, NullFocusedApp, NullHotkeyTap, NullKeyboard
from ..keycodes import LINUX_KEYCODES
from . import apps, shell, wayland, wire
from .portal import PortalConnection
from .x11 import SendGate, X11Clipboard, X11FocusedApp, X11HotkeyTap, X11Keyboard

log = logging.getLogger(__name__)

WHOLE, PORTAL, REDUCED = "whole", "portal", "reduced"

# Desktop names worth saying to the user. Anything else drops the desktop
# clause from the tier line rather than printing a raw `ubuntu:GNOME`
# (ADR 0012).
DESKTOP_LABELS = {
    "gnome": "GNOME", "kde": "KDE Plasma", "sway": "Sway", "hyprland": "Hyprland",
    "river": "river", "wayfire": "Wayfire", "niri": "niri", "xfce": "Xfce",
    "cinnamon": "Cinnamon", "mate": "MATE", "lxqt": "LXQt", "budgie": "Budgie",
    "cosmic": "COSMIC", "unity": "Unity", "pantheon": "Pantheon",
}
_WLROOTS_FAMILY = {"sway", "hyprland", "river", "wayfire", "niri"}
_GNOME_FAMILY = {"gnome", "ubuntu", "pop", "zorin"}


@dataclass(frozen=True)
class Session:
    tier: str
    session_type: str       # "x11" | "wayland" | ""
    desktop: str            # normalized key, "" when unknown
    wlroots: bool


def detect_tier(env: dict | None = None) -> Session:
    """Session type and desktop fix the tier (spec §1.1). GNOME is matched
    on any entry of the colon-separated `XDG_CURRENT_DESKTOP` list, so
    `ubuntu:GNOME` reads as GNOME."""
    env = os.environ if env is None else env
    session_type = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    entries = [e.strip().lower() for e in (env.get("XDG_CURRENT_DESKTOP") or "").split(":")
               if e.strip()]
    desktop = ""
    for entry in entries:
        if entry in _GNOME_FAMILY:
            desktop = "gnome"
            break
        if entry in DESKTOP_LABELS:
            desktop = entry
            break
    if not desktop and entries:
        desktop = entries[0]
    if not session_type:
        # No session variable (a bare TTY launch, an odd greeter): a DISPLAY
        # means X11, a Wayland socket means Wayland.
        if env.get("WAYLAND_DISPLAY"):
            session_type = "wayland"
        elif env.get("DISPLAY"):
            session_type = "x11"
    if session_type != "wayland":
        return Session(WHOLE, "x11" if session_type in ("x11", "") else session_type,
                       desktop, False)
    if desktop == "gnome":
        return Session(REDUCED, "wayland", desktop, False)
    return Session(PORTAL, "wayland", desktop, desktop in _WLROOTS_FAMILY)


def support_tier_summary(session: Session, *, no_overrides: bool = False) -> str:
    """The Settings ▸ General row (spec §9.4): tier word, then its
    consequence. Desktop from the known-name map or dropped."""
    label = DESKTOP_LABELS.get(session.desktop)
    where = f"{'X11' if session.tier == WHOLE else 'Wayland'} session"
    if label:
        where += f" on {label}"
    if session.tier == WHOLE:
        return f"{where} — Whole support: everything Cadent does works here."
    if session.tier == PORTAL:
        tail = ("your desktop owns the hotkey, Cadent won't learn on its own which "
                "apps need pasting, and there's no overlay")
        if no_overrides:
            tail += " or per-app overrides"
        return f"{where} — Portal support: {tail}."
    return f"{where} — Reduced support: no overlay, no per-app overrides."


# ---- the one grant on the Wayland tiers (ADR 0012) ---------------------------------------

PORTAL_GRANT = PermissionPreflight(
    name="portal",
    banner=("Cadent needs your desktop's permission to hear its hotkey and type "
            "what you dictate. Allow the prompts to turn dictation on."),
    wizard_body=("Your desktop asks before an app can listen for a shortcut or "
                 "type into other windows. Press the button and allow the prompts; "
                 "Cadent remembers the answer, so this is a one-time step."),
    action_label="Ask for permission",
    waiting="Waiting for your desktop — allow the prompts and Cadent notices.",
    granted="Permission granted — Cadent can hear its hotkey and type for you.",
)

# The Wayland tiers cannot bind a modifiers-only chord (ADR 0008).
WAYLAND_COMBO, WAYLAND_CLEANUP_COMBO = "<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c"

# ---- the two GPU support pack editions (ADR 0010) ----------------------------------------

CUBLAS_LINUX = GpuPackEdition(
    engine="faster-whisper", label="NVIDIA cuBLAS 12", size="~600 MB",
    wheels=(("nvidia-cublas-cu12", "12.9.2.10", "manylinux",
             ("libcublas.so.12", "libcublasLt.so.12")),),
    activation="preload")

# ONNX Runtime 1.28's CUDA provider dlopens the CUDA-13 userspace; an R580+
# driver (CUDA 13) is the floor, checked off `cuDriverGetVersion`.
CUDA13_LINUX = GpuPackEdition(
    engine="parakeet", label="NVIDIA CUDA 13 runtime", size="~1.3 GB",
    wheels=(
        ("nvidia-cuda-runtime", "13.0.96", "manylinux", ("libcudart.so.13",)),
        ("nvidia-cublas", "13.1.0.3", "manylinux", ("libcublasLt.so.13", "libcublas.so.13")),
        ("nvidia-cuda-nvrtc", "13.0.88", "manylinux", ("libnvrtc.so.13",)),
        ("nvidia-nvjitlink", "13.0.88", "manylinux", ("libnvJitLink.so.13",)),
        ("nvidia-cufft", "12.0.0.61", "manylinux", ("libcufft.so.12",)),
        ("nvidia-curand", "10.4.0.35", "manylinux", ("libcurand.so.10",)),
        ("nvidia-cudnn-cu13", "9.14.0.64", "manylinux",
         ("libcudnn.so.9", "libcudnn_graph.so.9", "libcudnn_ops.so.9",
          "libcudnn_engines_precompiled.so.9", "libcudnn_engines_runtime_compiled.so.9",
          "libcudnn_heuristic.so.9", "libcudnn_adv.so.9", "libcudnn_cnn.so.9")),
    ),
    activation="preload", min_driver_cuda=13000)

LINUX_EDITIONS = {"faster-whisper": CUBLAS_LINUX, "parakeet": CUDA13_LINUX}

STT_RUNTIMES: dict[str, tuple[str, ...]] = {
    "faster-whisper": ("auto", "cuda", "cpu"),
    "parakeet": ("auto", "cuda", "cpu"),
}


def capabilities(session: Session, *, per_app_overrides: bool, paste: bool) -> Capabilities:
    """The Linux columns of spec §2.4, by tier and by what the run found."""
    import dataclasses

    whole = session.tier == WHOLE
    rungs: tuple[str, ...] = ("type", "paste") if whole else (("paste", "type") if paste
                                                             else ("type",))
    return dataclasses.replace(
        CAPABILITIES,
        keycode_table=LINUX_KEYCODES,
        default_injection_strategy="type" if whole or not paste else "clipboard",
        injection_rungs=rungs,
        paste_chord="ctrl+v",
        default_overrides=(),
        default_override_reasons={},
        auto_learn_overrides=whole,
        stt_runtimes=STT_RUNTIMES,
        gpu_only_engines=frozenset(),
        show_runtime_combo=True,
        gpu_pack_available=True,
        gpu_pack_editions=LINUX_EDITIONS,
        permission=None if whole else PORTAL_GRANT,
        autostart_label="Start at login",
        app_identity_placeholder="org.mozilla.firefox",
        app_picker=True,
        mic_permission_hint=None,
        tray_click_toggles_pause=False,
        modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                           "option": "Alt", "win": "Super", "cmd": "Super",
                           "super": "Super"},
        tray_icon_painted_by_os=False,
        theme_subtitle="Follows your desktop's colour scheme",
        high_contrast_reason=("Your desktop is set to higher contrast, so Cadent is "
                              "following your system colours"),
        per_app_overrides=per_app_overrides,
        sided_hotkeys=whole,
        overlay="windowed" if whole else None,
        support_tier=session.tier,
        support_tier_summary=support_tier_summary(
            session, no_overrides=session.tier == PORTAL and not per_app_overrides),
        default_combo="<ctrl>+<cmd>" if whole else WAYLAND_COMBO,
        default_cleanup_combo="<ctrl>+<shift>+<alt>" if whole else WAYLAND_CLEANUP_COMBO,
        parakeet_on_cpu_recommended=True,
    )


def icons_dir() -> Path:
    import sys

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "icons"
    return Path(__file__).resolve().parents[3] / "packaging" / "icons"


def create(env: dict | None = None) -> Platform:
    session = detect_tier(env)
    index = apps.DesktopIndex(env=env)
    conn = PortalConnection.open(APP_ID)
    if session.tier == WHOLE:
        return _create_whole(session, index, conn)
    return _create_wayland(session, index, conn)


def _create_whole(session: Session, index: apps.DesktopIndex,
                  conn: PortalConnection | None) -> Platform:
    gate = SendGate()
    return Platform(
        capabilities=capabilities(session, per_app_overrides=True, paste=True),
        keyboard=X11Keyboard(gate),
        clipboard=X11Clipboard(),
        focused_app=X11FocusedApp(index),
        hotkey_tap=X11HotkeyTap(gate),
        hardware=shell.LinuxHardware(),
        autostart=shell.LinuxAutostart(),
        single_instance=shell.LinuxSingleInstance(),
        desktop=shell.LinuxDesktop(conn, session.desktop, icons_dir=icons_dir()),
    )


def _create_wayland(session: Session, index: apps.DesktopIndex,
                    conn: PortalConnection | None) -> Platform:
    """Mechanism per compositor, chosen once inside the tier (ADR 0007):
    typing via virtual-keyboard on the wlroots family else RemoteDesktop
    keysyms; paste via ext-data-control where offered else the Clipboard
    portal on that session; identity via the compositor's toplevel
    protocol where it is exposed."""
    wl = wire.WaylandConnection.connect() if os.environ.get("WAYLAND_DISPLAY") else None
    gate = SendGate()
    keyboard = None
    clipboard = None
    tracker = None
    if wl is not None:
        try:
            if session.wlroots and wire.VirtualKeyboardOutput.offered(wl):
                keyboard = wire.VirtualKeyboardOutput(wl)
        except wire.WaylandError:
            log.info("virtual-keyboard probe failed", exc_info=True)
        try:
            if wire.DataControlClipboard.offered(wl):
                clipboard = wire.DataControlClipboard(wl)
        except wire.WaylandError:
            log.info("data-control probe failed", exc_info=True)
        try:
            if session.tier == PORTAL and wire.ToplevelTracker.offered(wl):
                tracker = wire.ToplevelTracker(wl)
        except wire.WaylandError:
            log.info("toplevel-listing probe failed", exc_info=True)
    needs_remote_desktop = keyboard is None or clipboard is None
    sessions = wayland.PortalSessions(
        conn, wayland.TokenStore(config.DATA_DIR / "portal-tokens.json"),
        needs_remote_desktop=needs_remote_desktop,
        wants_clipboard=clipboard is None)
    remote_desktop = sessions.remote_desktop_available()
    if keyboard is None:
        keyboard = wayland.PortalKeyboard(sessions, gate) if remote_desktop else NullKeyboard()
        if not remote_desktop:
            log.warning("no typing mechanism on this compositor; injection is inert")
    if clipboard is None:
        if remote_desktop and sessions.clipboard_portal_available():
            clipboard = wayland.PortalClipboard(sessions)
        else:
            # No paste mechanism at all: the run collapses to ("type",) and
            # the overrides pane says so (spec §1.3).
            clipboard = MemoryClipboard()
    paste = not isinstance(clipboard, MemoryClipboard)
    if not needs_remote_desktop or not remote_desktop:
        sessions.needs_remote_desktop = needs_remote_desktop and remote_desktop
    sessions.resume_from_token()
    per_app = tracker is not None
    caps = capabilities(session, per_app_overrides=per_app, paste=paste)
    return Platform(
        capabilities=caps,
        keyboard=keyboard,
        clipboard=clipboard,
        focused_app=wayland.WaylandFocusedApp(sessions, index, tracker),
        hotkey_tap=wayland.PortalHotkeyTap(
            sessions, default_combo=caps.default_combo,
            default_cleanup_combo=caps.default_cleanup_combo)
        if conn is not None else NullHotkeyTap(),
        hardware=shell.LinuxHardware(),
        autostart=shell.LinuxAutostart(),
        single_instance=shell.LinuxSingleInstance(),
        desktop=shell.LinuxDesktop(conn, session.desktop,
                                   request_permission=sessions.request_permission,
                                   icons_dir=icons_dir()),
    )


__all__ = ["create", "detect_tier", "capabilities", "support_tier_summary", "Session",
           "PORTAL_GRANT", "LINUX_EDITIONS", "CUBLAS_LINUX", "CUDA13_LINUX",
           "WHOLE", "PORTAL", "REDUCED", "NullFocusedApp"]
