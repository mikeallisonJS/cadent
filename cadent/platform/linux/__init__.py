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
from collections.abc import Callable, Mapping
from typing import Literal

from ... import config
from .. import fallback
from ..base import Capabilities, PermissionPreflight, Platform
from ..keycodes import LINUX_KEYCODES
from . import datacontrol, vkeyboard, wayland, wlclient
from .input import (
    CLEANUP,
    DICTATE,
    PortalInput,
    TokenStore,
    codepoints,
    keys_for,
    keysym_for,
)
from .portal import CLIPBOARD, GLOBAL_SHORTCUTS, Portal
from .portalclip import PortalClipboard

TOKENS_FILENAME = "portal-tokens.json"

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


HOTKEY_OWNED_NOTE = ("Your desktop owns this shortcut. Cadent asked for the "
                     "chord below; change it in your desktop's shortcut "
                     "settings, not here.")
HOTKEY_UNAVAILABLE_NOTE = ("Your desktop doesn't let apps listen for a global "
                           "shortcut, so Cadent's hotkey is off in this "
                           "session.")
NO_PASTE_NOTE = "Pasting isn't available in this session, so Cadent types into every app."


@dataclasses.dataclass(frozen=True)
class Mechanisms:
    """What this run types and pastes with, fixed at startup from what the
    compositor and the portals offered (spec §2.3, §3)."""

    typing: Literal["virtual-keyboard", "remote-desktop"] = "remote-desktop"
    paste: Literal["data-control", "portal"] | None = None

    @property
    def needs_input_session(self) -> bool:
        return self.typing == "remote-desktop" or self.paste == "portal"


def choose_mechanisms(conn: wlclient.Connection | None,
                      clipboard_portal: Callable[[], bool]) -> Mechanisms:
    """Virtual keyboard and data-control where the compositor lists them
    (the wlroots family, KWin for the clipboard); else RemoteDesktop keysyms
    and the Clipboard portal; else no paste rung. `clipboard_portal` is asked
    only when the compositor cannot do it itself."""
    typing = "remote-desktop"
    paste = None
    if conn is not None:
        if vkeyboard.available(conn):
            typing = "virtual-keyboard"
        if datacontrol.available(conn):
            paste = "data-control"
    if paste is None and clipboard_portal():
        paste = "portal"
    return Mechanisms(typing, paste)


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


def capabilities_for(session: Session, *, hotkey_available: bool = True,
                     toplevel_protocol: bool = True,
                     paste_available: bool = False) -> Capabilities:
    """`hotkey_available`, `toplevel_protocol` and `paste_available` are what
    the startup probe of the Wayland tiers found (spec §1.3); the table is
    fixed from them for the whole run."""
    whole = session.tier == "whole"
    reduced = session.tier == "reduced"
    note = None
    if not whole:
        note = HOTKEY_OWNED_NOTE if hotkey_available else HOTKEY_UNAVAILABLE_NOTE
    return dataclasses.replace(
        fallback.CAPABILITIES,
        keycode_table=LINUX_KEYCODES,
        # The Wayland tiers paste first, with no automatic fall-through to
        # typing; with no paste mechanism (neither a data-control protocol nor
        # the Clipboard portal) typing is the only rung for the run (§1.3).
        default_injection_strategy=(
            "type" if whole or not paste_available else "clipboard"),
        injection_rungs=(("type", "paste") if whole
                         else ("paste", "type") if paste_available else ("type",)),
        default_overrides=(),
        default_override_reasons={},
        auto_learn_overrides=whole,
        per_app_overrides=whole or (not reduced and toplevel_protocol),
        stt_runtimes={"faster-whisper": ("auto", "cuda", "cpu"),
                      "parakeet": ("auto", "cuda", "cpu")},
        gpu_only_engines=frozenset(),
        # Never alongside `hotkey-unavailable`: with no shortcut interface
        # there is no grant to ask for.
        permission=None if whole or not hotkey_available else PORTAL_PERMISSION,
        autostart_label="Start at login",
        app_identity_placeholder="org.mozilla.firefox",
        app_picker=True,
        tray_click_toggles_pause=False,
        modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                           "option": "Alt", "win": "Super", "cmd": "Super",
                           "space": "Space"},
        theme_subtitle="Follows your desktop's colour scheme",
        high_contrast_reason=("Your desktop is set to higher contrast, so "
                              "Cadent is following your system colours"),
        overlay="windowed" if whole else None,
        support_tier=session.tier,
        support_tier_summary=tier_summary(session),
        default_combo=WHOLE_DEFAULT_COMBO if whole else WAYLAND_DEFAULT_COMBO,
        default_cleanup_combo=(WHOLE_DEFAULT_CLEANUP_COMBO if whole
                               else WAYLAND_DEFAULT_CLEANUP_COMBO),
        chords_need_key=not whole,
        hotkey_available=hotkey_available,
        hotkey_note=note,
        paste_note=None if whole or paste_available else NO_PASTE_NOTE,
    )


# One class per seam, each holding the shared connection. Behaviour is the
# fallback's inert version until the ticket for that seam lands; the point of
# the skeleton is that the wiring — one `Portal`, constructor-injected — is
# already the shape the real adapters will fill.

class _PortalAdapter:
    def __init__(self, portal: Portal) -> None:
        self._portal = portal


class LinuxKeyboard(_PortalAdapter, fallback.NullKeyboard):
    """Wayland typing: RemoteDesktop `NotifyKeyboardKeysym` on the input
    session. Whole's XTEST typing replaces this adapter on X11."""

    def __init__(self, portal: Portal, grants: PortalInput | None = None,
                 virtual: vkeyboard.VirtualKeyboard | None = None) -> None:
        super().__init__(portal)
        self._grants = grants
        self._virtual = virtual

    def send_text_units(self, units: list[int]) -> bool:
        if self._virtual is not None:
            return self._virtual.send_text_units(units)
        if self._grants is None:
            return super().send_text_units(units)
        presses: list[tuple[int, bool]] = []
        for point in codepoints(units):
            keysym = keysym_for(point)
            presses += [(keysym, True), (keysym, False)]
        return self._grants.send_keysyms(presses)

    def send_chord(self, keys: list[int]) -> None:
        if self._virtual is not None:
            self._virtual.send_chord(keys)
            return
        if self._grants is None:
            return
        self._grants.send_keysyms([(k, True) for k in keys]
                                  + [(k, False) for k in reversed(keys)])


class LinuxClipboard(_PortalAdapter, fallback.MemoryClipboard):
    """The run's paste mechanism: a data-control selection or the Clipboard
    portal. With neither, the in-process fallback keeps the seam harmless —
    the ladder is `("type",)` then and nothing pastes."""

    def __init__(self, portal: Portal,
                 backend: datacontrol.DataControlClipboard | PortalClipboard | None = None
                 ) -> None:
        fallback.MemoryClipboard.__init__(self)
        _PortalAdapter.__init__(self, portal)
        self._backend = backend

    def get_text(self) -> str | None:
        return super().get_text() if self._backend is None else self._backend.get_text()

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        if self._backend is None:
            super().set_text(text, exclude_from_history)
        else:
            self._backend.set_text(text, exclude_from_history)

    def sequence_number(self) -> int:
        return (super().sequence_number() if self._backend is None
                else self._backend.sequence_number())


class LinuxFocusedApp(_PortalAdapter, fallback.NullFocusedApp):
    """Identity is the compositor's app_id where it offers a toplevel
    protocol; otherwise `"unknown"`. The grant is the portal's (spec §2.3)."""

    def __init__(self, portal: Portal, grants: PortalInput | None = None,
                 windows: wayland.PlasmaWindows | None = None) -> None:
        super().__init__(portal)
        self._grants = grants
        self._windows = windows

    def name(self) -> str:
        return (self._windows.focused_app_id if self._windows else None) or "unknown"

    def permission_granted(self) -> bool:
        return True if self._grants is None else self._grants.granted()


class LinuxHotkeyTap(_PortalAdapter, fallback.NullHotkeyTap):
    """GlobalShortcuts: both chords bound in one call, and the keysym events
    each stands for synthesized from `Activated` / `Deactivated`, so the chord
    state machine runs unchanged (spec §4)."""

    IDS = (DICTATE, CLEANUP)

    def __init__(self, portal: Portal, grants: PortalInput | None = None) -> None:
        super().__init__(portal)
        self._grants = grants

    def start(self, on_event, chords: tuple[str, ...] = ()) -> None:
        if self._grants is None:
            return
        by_id = dict(zip(self.IDS, chords, strict=False))
        keys = {sid: keys_for(combo) for sid, combo in by_id.items()}

        def shortcut(sid: str, down: bool) -> None:
            ordered = keys[sid] if down else reversed(keys[sid])
            for key in ordered:
                on_event(key, down, False)

        self._grants.attach(by_id, shortcut)

    def stop(self) -> None:
        if self._grants is not None:
            self._grants.detach()

    def bindings(self) -> dict[str, str]:
        return self._grants.bindings() if self._grants is not None else {}


class LinuxHardware(_PortalAdapter, fallback.NullHardware): ...
class LinuxAutostart(_PortalAdapter, fallback.NullAutostart): ...
class LinuxSingleInstance(_PortalAdapter, fallback.NullSingleInstance): ...
class LinuxDesktop(_PortalAdapter, fallback.NullDesktop):
    def __init__(self, portal: Portal, grants: PortalInput | None = None) -> None:
        super().__init__(portal)
        self._grants = grants

    def request_permission(self) -> None:
        if self._grants is not None:
            self._grants.request_permission()


def create(env: Mapping[str, str] | None = None,
           portal: Portal | None = None,
           windows: Callable[[], wayland.PlasmaWindows | None] | None = None,
           connect: Callable[[dict[str, str]], wlclient.Connection | None] | None = None
           ) -> Platform:
    environ = dict(os.environ if env is None else env)
    session = detect_session(environ)
    portal = portal or Portal()
    if session.tier == "whole":
        return _assemble(capabilities_for(session), portal)
    # The Wayland tiers probe the compositor and the portals once; the answers
    # fix the run.
    hotkey_available = portal.has_interface(GLOBAL_SHORTCUTS)
    tracker = None
    if "kde" in session.desktops:
        tracker = (windows or wayland.connect)()
    conn = (connect or wlclient.connect)(environ)
    mechanisms = choose_mechanisms(conn, lambda: portal.has_interface(CLIPBOARD))
    caps = capabilities_for(session, hotkey_available=hotkey_available,
                            toplevel_protocol=tracker is not None,
                            paste_available=mechanisms.paste is not None)
    grants = PortalInput(portal, TokenStore(config.DATA_DIR / TOKENS_FILENAME),
                         needs_input=mechanisms.needs_input_session)
    clipboard: datacontrol.DataControlClipboard | PortalClipboard | None = None
    if mechanisms.paste == "data-control":
        clipboard = datacontrol.DataControlClipboard(conn)
    elif mechanisms.paste == "portal":
        clipboard = grants.clipboard = PortalClipboard(portal, grants.input_session)
    virtual = (vkeyboard.VirtualKeyboard(conn)
               if mechanisms.typing == "virtual-keyboard" else None)
    return _assemble(caps, portal, grants if hotkey_available else None, tracker,
                     clipboard, virtual)


def _assemble(caps: Capabilities, portal: Portal, grants: PortalInput | None = None,
              windows: wayland.PlasmaWindows | None = None,
              clipboard: datacontrol.DataControlClipboard | PortalClipboard | None = None,
              virtual: vkeyboard.VirtualKeyboard | None = None) -> Platform:
    return Platform(
        capabilities=caps,
        keyboard=LinuxKeyboard(portal, grants, virtual),
        clipboard=LinuxClipboard(portal, clipboard),
        focused_app=LinuxFocusedApp(portal, grants, windows),
        hotkey_tap=LinuxHotkeyTap(portal, grants),
        hardware=LinuxHardware(portal),
        autostart=LinuxAutostart(portal),
        single_instance=LinuxSingleInstance(portal),
        desktop=LinuxDesktop(portal, grants),
    )
