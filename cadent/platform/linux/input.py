"""The Wayland tiers' two portal sessions: GlobalShortcuts for the hotkey and
RemoteDesktop for typing (spec §2.2, §4, §9; ADR 0008, 0012, 0013).

One `PortalInput` is shared by the seam adapters that need it — the hotkey tap,
the keyboard, the focused-app grant check and the desktop's
`request_permission()` — because the grant is one fact across them: Cadent is
usable only while the shortcut is bound **and** the input session is live.

Threading follows spec §2.2. Nothing here blocks the GUI thread: the grant
request and the health refresh run on short-lived workers, `permission_granted`
answers from a cache, and the two consent dialogs are never awaited. Their
outcome arrives on the portal thread.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jeepney import Message

from ... import chord
from ..keycodes import LINUX_KEYCODES
from . import portal as P
from .portal import Portal, PortalError

log = logging.getLogger(__name__)

SESSION_IFACE = "org.freedesktop.portal.Session"
DICTATE, CLEANUP = "dictate", "cleanup"
DESCRIPTIONS = {DICTATE: "Dictate with Cadent",
                CLEANUP: "Turn Cadent's cleanup on or off"}
HEALTH_TTL = 2.0            # seconds a health reading is trusted

TOKEN_KEY = "remote_desktop"

_MODIFIER_NAMES = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT",
                   "option": "ALT", "cmd": "LOGO", "win": "LOGO",
                   "super": "LOGO"}

KEYSYM_RETURN, KEYSYM_TAB = 0xFF0D, 0xFF09
KEYSYM_UNICODE_BASE = 0x01000000


def trigger_for(combo: str) -> str:
    """A chord as the freedesktop shortcuts grammar spells it:
    `<ctrl>+<cmd>+space` → `CTRL+LOGO+space`. Only a suggestion — the
    compositor owns the binding."""
    parts = []
    for part in combo.split("+"):
        name = part.strip().lower().strip("<>")
        if name[:1] in ("l", "r") and name[1:] in _MODIFIER_NAMES:
            name = name[1:]
        if name in _MODIFIER_NAMES:
            parts.append(_MODIFIER_NAMES[name])
        elif name.startswith("f") and name[1:].isdigit():
            parts.append(name.upper())
        else:
            parts.append(name)
    return "+".join(parts)


def keys_for(combo: str) -> list[int]:
    """The keysyms a chord stands for, in press order: one key per slot, the
    left-hand one where either side satisfies it."""
    return [min(group) for group in chord.parse_combo(combo, LINUX_KEYCODES)]


def keysym_for(codepoint: int) -> int:
    if codepoint == 0x0A:
        return KEYSYM_RETURN
    if codepoint == 0x09:
        return KEYSYM_TAB
    if codepoint < 0x100:
        return codepoint        # Latin-1 keysyms are their code points
    return KEYSYM_UNICODE_BASE + codepoint


def codepoints(units: list[int]) -> list[int]:
    raw = b"".join(u.to_bytes(2, "little") for u in units)
    return [ord(c) for c in raw.decode("utf-16-le", errors="replace")]


class TokenStore:
    """`DATA_DIR/portal-tokens.json` — the RemoteDesktop restore token. Not in
    config.json: an opaque credential has no place in a hand-edited file, and
    "Back it up and start fresh" must not revoke the grant (spec §9.4)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, key: str) -> str | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, str) and value else None

    def put(self, key: str, token: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({key: token}), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            log.warning("could not save the portal restore token", exc_info=True)


class PortalInput:
    def __init__(self, portal: Portal, tokens: TokenStore,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._portal = portal
        self._tokens = tokens
        self._clock = clock
        self._lock = threading.RLock()
        self._chords: dict[str, str] = {}
        self._on_shortcut: Callable[[str, bool], None] | None = None
        self._shortcuts_session: str | None = None
        self._input_session: str | None = None
        self._unsubscribe: list[Callable[[], None]] = []
        self._bindings: dict[str, str] = {}     # shortcut id → compositor's words
        self._granted = False
        self._checked_at = float("-inf")
        self._refreshing = False
        self._recovering: set[str] = set()      # sessions already recovered once
        self._wired = False                     # Activated/Deactivated subscribed

    # ---- the hotkey side ---------------------------------------------------

    def attach(self, chords: dict[str, str],
               on_shortcut: Callable[[str, bool], None]) -> None:
        """Start listening for `chords` (shortcut id → combo). Returns at
        once; the session is made on a worker."""
        with self._lock:
            self._chords = dict(chords)
            self._on_shortcut = on_shortcut
        self._spawn("cadent-portal-attach", self._attach)

    def detach(self) -> None:
        with self._lock:
            unsubscribe, self._unsubscribe = self._unsubscribe, []
            self._wired = False
            self._on_shortcut = None
            self._shortcuts_session = None
            self._granted = False
        for undo in unsubscribe:
            undo()

    def bindings(self) -> dict[str, str]:
        with self._lock:
            return dict(self._bindings)

    def _attach(self) -> None:
        try:
            self._open_shortcuts()
            if self._tokens.get(TOKEN_KEY):
                # Granted before: binding again and restoring the input
                # session are both answered without a dialog.
                self._bind()
                self._open_input()
        except PortalError as exc:
            log.warning("portal input could not start: %s", exc)
        self._refresh()

    def _open_shortcuts(self) -> None:
        session = self._create_session(P.GLOBAL_SHORTCUTS, "cadent_shortcuts")
        with self._lock:
            self._shortcuts_session = session
        self._watch(session, "shortcuts")
        if not self._wired:
            self._wired = True
            self._on_signal(P.GLOBAL_SHORTCUTS, "Activated", True)
            self._on_signal(P.GLOBAL_SHORTCUTS, "Deactivated", False)

    def _on_signal(self, interface: str, member: str, down: bool) -> None:
        def handle(msg: Message) -> None:
            _session, shortcut, *_ = msg.body
            with self._lock:
                callback = self._on_shortcut
                combo = self._chords.get(shortcut)
            if callback is not None and combo is not None:
                callback(shortcut, down)

        self._unsubscribe.append(self._portal.subscribe_signal(
            P.signal_rule(interface, member, P.PORTAL_PATH), handle))

    def _bind(self) -> None:
        with self._lock:
            session = self._shortcuts_session
            shortcuts = [(sid, DESCRIPTIONS.get(sid, sid), trigger_for(combo))
                         for sid, combo in self._chords.items()]
        if session is None or not shortcuts:
            return
        self._portal.consent_request(
            lambda token: P.bind_shortcuts_message(session, shortcuts, token),
            self._on_bound)

    def _on_bound(self, code: int, results: dict) -> None:
        if code != 0:
            log.info("the shortcut binding was not granted (response %s)", code)
            return
        self._record_bindings(results.get("shortcuts", []))
        self._recovering.discard("shortcuts")
        self._spawn("cadent-portal-health", self._refresh)

    def _record_bindings(self, shortcuts: list) -> None:
        bound = {}
        for sid, props in shortcuts:
            words = props.get("trigger_description", ("s", ""))[1]
            if words:
                bound[sid] = words
        with self._lock:
            self._bindings = bound

    # ---- the typing side ----------------------------------------------------

    def _open_input(self) -> None:
        """CreateSession + SelectDevices (carrying the restore token), then
        Start — a consent request: instant when the token is honoured, a
        dialog when it is not."""
        session = self._create_session(P.REMOTE_DESKTOP, "cadent_input")
        token = self._tokens.get(TOKEN_KEY)
        code, _ = self._portal.bounded_request(
            lambda t: P.select_keyboard_message(session, t, token))
        if code != 0:
            raise PortalError(f"SelectDevices refused (response {code})")
        self._watch(session, "input")
        self._portal.consent_request(
            lambda t: P.start_remote_desktop_message(session, t),
            lambda code, results: self._on_started(session, code, results))

    def _on_started(self, session: str, code: int, results: dict) -> None:
        if code != 0:
            log.info("the input session was not granted (response %s)", code)
            return
        restore = results.get("restore_token")
        if restore:
            self._tokens.put(TOKEN_KEY, restore)
        with self._lock:
            self._input_session = session
        self._recovering.discard("input")
        self._spawn("cadent-portal-health", self._refresh)

    def send_keysyms(self, presses: list[tuple[int, bool]]) -> bool:
        """(keysym, pressed) in order. False when there is no live session or
        the portal does not answer in time — the rung fails, never raises."""
        with self._lock:
            session = self._input_session
        if session is None:
            return False
        try:
            for keysym, pressed in presses:
                self._portal.call(P.notify_keysym_message(session, keysym, pressed))
        except PortalError as exc:
            log.warning("typing through the portal failed: %s", exc)
            return False
        return True

    # ---- the grant -----------------------------------------------------------

    def request_permission(self) -> None:
        """Raise whichever dialogs are still owed. Fire-and-forget, and never
        retried on its own: a denial leaves the button for the user to press."""
        self._spawn("cadent-portal-grant", self._request)

    def _request(self) -> None:
        try:
            with self._lock:
                have_shortcuts = self._shortcuts_session is not None
                have_input = self._input_session is not None
            if not have_shortcuts:
                self._open_shortcuts()
            self._bind()
            if not have_input:
                self._open_input()
        except PortalError as exc:
            log.warning("the portal permission request failed: %s", exc)

    def granted(self) -> bool:
        """The last health reading; a stale one starts a refresh. Never
        blocks — the grant poll runs on the GUI thread."""
        with self._lock:
            stale = self._clock() - self._checked_at >= HEALTH_TTL
            if stale and not self._refreshing:
                self._refreshing = True
                self._spawn("cadent-portal-health", self._refresh)
            return self._granted

    def _refresh(self) -> None:
        """ListShortcuts must report our shortcut, and the input session must
        be live — never the listener's own flag (ADR 0002)."""
        with self._lock:
            self._refreshing = True
            session = self._shortcuts_session
            have_input = self._input_session is not None
        granted = False
        try:
            if session is not None and have_input:
                _code, results = self._portal.bounded_request(
                    lambda t: P.list_shortcuts_message(session, t))
                self._record_bindings(results.get("shortcuts", []))
                with self._lock:
                    granted = DICTATE in self._bindings
        except PortalError as exc:
            log.info("portal health check failed: %s", exc)
        finally:
            with self._lock:
                self._granted = granted
                self._checked_at = self._clock()
                self._refreshing = False

    # ---- sessions ------------------------------------------------------------

    def _create_session(self, interface: str, handle: str) -> str:
        code, results = self._portal.bounded_request(
            lambda t: P.create_session_message(interface, t, handle))
        session = results.get("session_handle")
        if code != 0 or not session:
            raise PortalError(f"CreateSession refused (response {code})")
        return session

    def _watch(self, session: str, kind: str) -> None:
        self._unsubscribe.append(self._portal.subscribe_signal(
            P.signal_rule(SESSION_IFACE, "Closed", session),
            lambda _msg: self._on_closed(kind)))

    def _on_closed(self, kind: str) -> None:
        """`xdg-desktop-portal` restarting closes sessions silently. Recover
        once with the saved token; if that session closes again before it came
        back, admit it — the grant goes false and nothing retries (§2.2)."""
        with self._lock:
            if kind == "shortcuts":
                self._shortcuts_session = None
            else:
                self._input_session = None
            self._granted = False
            if kind in self._recovering or self._on_shortcut is None:
                log.warning("the %s session closed again; leaving it closed", kind)
                return
            self._recovering.add(kind)
        self._spawn("cadent-portal-recover", lambda: self._recover(kind))

    def _recover(self, kind: str) -> None:
        try:
            if kind == "shortcuts":
                self._open_shortcuts()
                self._bind()
            else:
                self._open_input()
        except PortalError as exc:
            log.warning("could not recover the %s session: %s", kind, exc)

    @staticmethod
    def _spawn(name: str, target: Callable[[], Any]) -> None:
        threading.Thread(target=target, name=name, daemon=True).start()
