"""The Portal and Reduced tiers: the seams Wayland fills through the desktop
portals, and the compositor protocols that stand in where offered
(spec §2.3, §4, §9; ADR 0007, ADR 0008, ADR 0012).

`PortalSessions` owns the two portal sessions every Wayland adapter shares —
GlobalShortcuts for the chords, RemoteDesktop for keysym typing and the
Clipboard portal that rides it — plus the restore token, the grant state
`permission_granted()` reads, and the one silent recovery after
`xdg-desktop-portal` restarts. The adapters are thin over it.
"""

from __future__ import annotations

import json
import logging
import os
import select
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from ...chord import parse_combo
from ..keycodes import LINUX_KEYCODES
from . import portal as P
from .apps import DesktopIndex
from .portal import PortalConnection, PortalError, Response
from .x11 import SendGate, exe_basename, keysym_for

log = logging.getLogger(__name__)

DICTATE, CLEANUP = "dictate", "cleanup"
SHORTCUT_DESCRIPTIONS = {DICTATE: "Dictate (hold or tap)", CLEANUP: "Toggle cleanup"}

# The freedesktop shortcuts grammar: modifiers then one keysym name.
_TRIGGER_MODIFIERS = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT", "option": "ALT",
                      "cmd": "LOGO", "win": "LOGO", "super": "LOGO"}


def trigger_for(chord: str) -> str | None:
    """A stored chord as a portal trigger (`CTRL+LOGO+space`), or None for a
    modifiers-only chord the grammar cannot express (ADR 0008) — the caller
    binds the tier default instead and says so."""
    mods: list[str] = []
    key: str | None = None
    for part in chord.lower().split("+"):
        part = part.strip()
        if not part:
            continue
        bare = part.strip("<>")
        for side in ("l", "r"):
            if bare.startswith(side) and bare[1:] in _TRIGGER_MODIFIERS:
                bare = bare[1:]
            elif bare.endswith(f"_{side}") and bare[:-2] in _TRIGGER_MODIFIERS:
                bare = bare[:-2]
        if bare in _TRIGGER_MODIFIERS:
            if _TRIGGER_MODIFIERS[bare] not in mods:
                mods.append(_TRIGGER_MODIFIERS[bare])
        elif part.startswith("<"):
            return None
        else:
            key = bare.upper() if bare.startswith("f") and bare[1:].isdigit() else bare
    if key is None:
        return None
    return "+".join(mods + [key])


def chord_keysyms(chord: str) -> list[int]:
    """One keysym per group of a stored chord, in order — what the tap
    synthesizes when the desktop says the shortcut fired."""
    return [min(group) for group in parse_combo(chord, LINUX_KEYCODES)]


class TokenStore:
    """`DATA_DIR/portal-tokens.json` — the RemoteDesktop restore token lives
    here and not in config.json: an opaque credential has no place in a
    hand-edited file, and "Back it up and start fresh" must not revoke the
    grant (ADR 0012)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, key: str) -> str | None:
        try:
            value = json.loads(self._path.read_text(encoding="utf-8")).get(key)
            return value if isinstance(value, str) and value else None
        except (OSError, ValueError):
            return None

    def set(self, key: str, value: str | None) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            log.debug("could not write the portal token store", exc_info=True)


class PortalSessions:
    """The shared portal state of a Wayland run.

    `needs_remote_desktop` says whether the run's typing/paste mechanism
    rides the RemoteDesktop session (KWin, GNOME: yes; a wlroots compositor
    typing through virtual-keyboard and pasting through data-control: no —
    Hyprland's portal ships no RemoteDesktop backend and must never fault
    forever on a session it cannot open, ADR 0008). `wants_clipboard` adds
    the Clipboard portal to that session (`RequestClipboard` before `Start`).
    """

    def __init__(self, conn: PortalConnection | None, tokens: TokenStore, *,
                 needs_remote_desktop: bool, wants_clipboard: bool) -> None:
        self.conn = conn
        self.tokens = tokens
        self.needs_remote_desktop = needs_remote_desktop
        self.wants_clipboard = wants_clipboard
        self._lock = threading.RLock()
        # GlobalShortcuts
        self.gs_session: str | None = None
        self.shortcuts: list[tuple[str, str, str]] = []     # (id, description, trigger)
        self.bound: dict[str, str] = {}                     # id → trigger description
        self._bind_pending = False
        # RemoteDesktop
        self.rd_session: str | None = None
        self.rd_live = False
        self.clipboard_enabled = False
        self._start_pending = False
        self._recovered = {"gs": False, "rd": False}
        self.on_activated: Callable[[str], None] | None = None
        self.on_deactivated: Callable[[str], None] | None = None
        self.on_state_change: Callable[[], None] | None = None
        self._subs: list = []
        if conn is not None:
            self._subs.append(conn.subscribe(P.portal_signal_rule("GlobalShortcuts", "Activated"),
                                             self._on_activated))
            self._subs.append(conn.subscribe(P.portal_signal_rule("GlobalShortcuts", "Deactivated"),
                                             self._on_deactivated))
            self._subs.append(conn.subscribe(
                P.portal_signal_rule("GlobalShortcuts", "ShortcutsChanged"),
                self._on_shortcuts_changed))

    # ---- what the tiers ask ----------------------------------------------------------

    def shortcuts_available(self) -> bool:
        return self.conn is not None and self.conn.interface_available("GlobalShortcuts")

    def remote_desktop_available(self) -> bool:
        return self.conn is not None and self.conn.interface_available("RemoteDesktop")

    def clipboard_portal_available(self) -> bool:
        return self.conn is not None and self.conn.interface_available("Clipboard")

    def granted(self) -> bool:
        """Every grant the run's mechanisms need is live (ADR 0008)."""
        with self._lock:
            shortcuts_ok = all(sid in self.bound for sid, _d, _t in self.shortcuts) \
                and bool(self.shortcuts)
            if not self.needs_remote_desktop:
                return shortcuts_ok
            return shortcuts_ok and self.rd_live

    def _changed(self) -> None:
        if self.on_state_change is not None:
            try:
                self.on_state_change()
            except Exception:
                log.exception("portal state callback failed")

    # ---- GlobalShortcuts ------------------------------------------------------------

    def ensure_shortcuts_session(self) -> str | None:
        """CreateSession (bounded) once, then `bound` from ListShortcuts —
        the honest health check (ADR 0002's rule, transplanted). A session
        that already exists is returned as is; `refresh_bindings` re-asks."""
        if self.conn is None:
            return None
        with self._lock:
            if self.gs_session is not None:
                return self.gs_session
            if self.gs_session is None:
                self.conn.register_app_id()
                try:
                    session_token = P.new_token()
                    response = self.conn.request(
                        "GlobalShortcuts", "CreateSession", "a{sv}",
                        lambda token: (P.vardict(handle_token=("s", token),
                                                 session_handle_token=("s", session_token)),),
                        wait=True)
                except PortalError:
                    log.info("GlobalShortcuts CreateSession failed", exc_info=True)
                    return None
                if not response.ok:
                    return None
                self.gs_session = str(response.results.get("session_handle")
                                      or self.conn.session_path(session_token))
                self._subs.append(self.conn.subscribe(
                    P.session_closed_rule(self.gs_session),
                    lambda _m: self._on_closed("gs"), bus_match=False))
            self.refresh_bindings()
            return self.gs_session

    def refresh_bindings(self) -> None:
        if self.conn is None or self.gs_session is None:
            return
        try:
            response = self.conn.request(
                "GlobalShortcuts", "ListShortcuts", "oa{sv}",
                lambda token: (self.gs_session, P.vardict(handle_token=("s", token))),
                wait=True)
        except PortalError:
            log.info("ListShortcuts failed", exc_info=True)
            return
        self._absorb_shortcuts(response)

    def _absorb_shortcuts(self, response: Response) -> None:
        if not response.ok:
            return
        bound: dict[str, str] = {}
        for entry in response.results.get("shortcuts", []) or []:
            sid, props = entry[0], entry[1] if len(entry) > 1 else {}
            bound[str(sid)] = str(props.get("trigger_description", "")) if props else ""
        with self._lock:
            self.bound = bound
        self._changed()

    def bind_shortcuts(self) -> None:
        """BindShortcuts — consent class, never awaited (spec §2.2)."""
        if self.conn is None or not self.shortcuts:
            return
        session = self.ensure_shortcuts_session()
        if session is None or self._bind_pending:
            return
        self._bind_pending = True

        def done(response: Response) -> None:
            self._bind_pending = False
            if response.ok:
                self._absorb_shortcuts(response)
            else:
                log.info("BindShortcuts answered %s", response.code)
                self._changed()

        try:
            self.conn.request(
                "GlobalShortcuts", "BindShortcuts", "oa(sa{sv})sa{sv}",
                lambda token: (session,
                               [(sid, P.vardict(description=("s", desc),
                                                preferred_trigger=("s", trigger)))
                                for sid, desc, trigger in self.shortcuts],
                               "", P.vardict(handle_token=("s", token))),
                wait=False, on_response=done)
        except PortalError:
            self._bind_pending = False
            log.info("BindShortcuts failed", exc_info=True)

    def _on_activated(self, msg) -> None:
        session, shortcut_id, _ts, _opts = P.signal_body(msg)
        if session == self.gs_session and self.on_activated is not None:
            self.on_activated(str(shortcut_id))

    def _on_deactivated(self, msg) -> None:
        session, shortcut_id, _ts, _opts = P.signal_body(msg)
        if session == self.gs_session and self.on_deactivated is not None:
            self.on_deactivated(str(shortcut_id))

    def _on_shortcuts_changed(self, msg) -> None:
        session, shortcuts = P.signal_body(msg)
        if session == self.gs_session:
            self._absorb_shortcuts(Response(P.RESPONSE_OK, {"shortcuts": shortcuts}))

    # ---- RemoteDesktop + Clipboard ----------------------------------------------------

    def ensure_remote_desktop_session(self) -> str | None:
        if self.conn is None:
            return None
        with self._lock:
            if self.rd_session is not None:
                return self.rd_session
            self.conn.register_app_id()
            try:
                session_token = P.new_token()
                response = self.conn.request(
                    "RemoteDesktop", "CreateSession", "a{sv}",
                    lambda token: (P.vardict(handle_token=("s", token),
                                             session_handle_token=("s", session_token)),),
                    wait=True)
                if not response.ok:
                    return None
                session = str(response.results.get("session_handle")
                              or self.conn.session_path(session_token))
                selected = self.conn.request(
                    "RemoteDesktop", "SelectDevices", "oa{sv}",
                    lambda token: (session, P.vardict(
                        handle_token=("s", token), types=("u", P.DEVICE_KEYBOARD),
                        persist_mode=("u", P.PERSIST_UNTIL_REVOKED),
                        restore_token=(("s", self.tokens.get("remote_desktop"))
                                       if self.tokens.get("remote_desktop") else None))),
                    wait=True)
                if not selected.ok:
                    return None
                if self.wants_clipboard and self.clipboard_portal_available():
                    try:
                        self.conn.send_message(P.clipboard_request(session))
                    except PortalError:
                        log.info("RequestClipboard refused", exc_info=True)
            except PortalError:
                log.info("RemoteDesktop session could not be created", exc_info=True)
                return None
            self.rd_session = session
            self._subs.append(self.conn.subscribe(
                P.session_closed_rule(session), lambda _m: self._on_closed("rd"),
                bus_match=False))
            return session

    def start_remote_desktop(self) -> None:
        """`Start` — consent class. With a valid restore token no dialog
        appears; a lost or rejected one re-asks (ADR 0012)."""
        if self.conn is None or self._start_pending:
            return
        session = self.ensure_remote_desktop_session()
        if session is None:
            return
        self._start_pending = True

        def done(response: Response) -> None:
            self._start_pending = False
            with self._lock:
                self.rd_live = response.ok
                self.clipboard_enabled = bool(response.results.get("clipboard_enabled", False))
            if response.ok:
                self.tokens.set("remote_desktop", response.results.get("restore_token"))
            else:
                log.info("RemoteDesktop.Start answered %s", response.code)
                with self._lock:
                    self.rd_session = None      # a refused Start ends the session
            self._changed()

        try:
            self.conn.request("RemoteDesktop", "Start", "osa{sv}",
                              lambda token: (session, "", P.vardict(handle_token=("s", token))),
                              wait=False, on_response=done)
        except PortalError:
            self._start_pending = False
            log.info("RemoteDesktop.Start failed", exc_info=True)

    def resume_from_token(self) -> None:
        """At startup: a persisted token means the user already granted the
        session, so bring it back without a dialog."""
        if self.needs_remote_desktop and self.tokens.get("remote_desktop"):
            self.start_remote_desktop()

    def request_permission(self) -> None:
        """`DesktopEnv.request_permission` on the Wayland tiers (ADR 0012):
        the shortcut binding always, the input session where the run needs
        it. Fire-and-forget; no retry after a denial."""
        if not self.granted_shortcuts():
            self.bind_shortcuts()
        if self.needs_remote_desktop and not self.rd_live:
            self.start_remote_desktop()

    def granted_shortcuts(self) -> bool:
        with self._lock:
            return bool(self.shortcuts) and all(sid in self.bound
                                                for sid, _d, _t in self.shortcuts)

    def _on_closed(self, which: str) -> None:
        """A session the portal closed under us — one silent recovery on
        the restore token, then an honest dead state (ADR 0008). The
        recovery runs on its own thread: this handler sits on the dispatch
        thread, and a bounded request waits for a Response that only the
        dispatch thread can deliver."""
        with self._lock:
            if which == "gs":
                self.gs_session, self.bound = None, {}
            else:
                self.rd_session, self.rd_live = None, False
            recovered = self._recovered[which]
            self._recovered[which] = True
        if recovered:
            log.warning("portal %s session closed again; staying down", which)
            self._changed()
            return
        log.info("portal %s session closed; recovering once", which)
        threading.Thread(target=self._recover, args=(which,), daemon=True,
                         name="cadent-portal-recover").start()

    def _recover(self, which: str) -> None:
        try:
            if which == "gs":
                self.ensure_shortcuts_session()
            else:
                self.start_remote_desktop()
        except Exception:
            log.exception("portal %s recovery failed", which)
        self._changed()

    # ---- the input session, used by the adapters -----------------------------------

    def notify_keysym(self, keysym: int, pressed: bool) -> None:
        with self._lock:
            session, live = self.rd_session, self.rd_live
        if self.conn is None or session is None or not live:
            raise PortalError("no live RemoteDesktop session to type through")
        self.conn.send_message(P.remote_desktop_notify_keysym(session, keysym, pressed))

    def close(self) -> None:
        for sub in self._subs:
            try:
                sub.close()
            except Exception:
                pass


# ---- the adapters ---------------------------------------------------------------------

class PortalHotkeyTap:
    """GlobalShortcuts: bind both chords in one call, synthesize their
    keysyms from Activated/Deactivated so the chord machine runs unchanged
    (ADR 0008). A modifiers-only chord binds the tier default instead;
    `bindings()` shows what the desktop actually holds."""

    def __init__(self, sessions: PortalSessions, *, default_combo: str,
                 default_cleanup_combo: str) -> None:
        self._sessions = sessions
        self._defaults = {DICTATE: default_combo, CLEANUP: default_cleanup_combo}
        self._keysyms: dict[str, list[int]] = {}
        self._on_event = None
        self._substituted: dict[str, str] = {}
        sessions.on_activated = self._activated
        sessions.on_deactivated = self._deactivated

    def start(self, on_event, chords=()) -> None:
        self._on_event = on_event
        chords = tuple(chords)
        plan: list[tuple[str, str, str]] = []
        self._substituted = {}
        for index, sid in enumerate((DICTATE, CLEANUP)):
            if index >= len(chords) or not chords[index]:
                continue
            chord = chords[index]
            trigger = trigger_for(chord)
            if trigger is None:
                # Unbindable here (no keysym): the tier default binds for
                # this run and the Hotkeys pane says so (spec §4).
                trigger = trigger_for(self._defaults[sid]) or ""
                self._substituted[sid] = self._defaults[sid]
            try:
                self._keysyms[sid] = chord_keysyms(chord)
            except ValueError:
                continue
            plan.append((sid, SHORTCUT_DESCRIPTIONS[sid], trigger))
        self._sessions.shortcuts = plan
        if self._sessions.shortcuts_available():
            self._sessions.ensure_shortcuts_session()

    def stop(self) -> None:
        self._on_event = None

    def _activated(self, shortcut_id: str) -> None:
        self._emit(shortcut_id, True)

    def _deactivated(self, shortcut_id: str) -> None:
        self._emit(shortcut_id, False)

    def _emit(self, shortcut_id: str, pressed: bool) -> None:
        keysyms = self._keysyms.get(shortcut_id)
        on_event = self._on_event
        if not keysyms or on_event is None:
            return
        for keysym in (keysyms if pressed else reversed(keysyms)):
            on_event(keysym, pressed, False)

    def available(self) -> bool:
        return self._sessions.shortcuts_available()

    def bindings(self) -> Mapping[str, str]:
        out: dict[str, str] = {}
        for sid in (DICTATE, CLEANUP):
            trigger = self._sessions.bound.get(sid)
            if trigger:
                out[sid] = trigger
            elif sid in self._substituted:
                out[sid] = self._substituted[sid]
        return out

    @property
    def substituted(self) -> Mapping[str, str]:
        return dict(self._substituted)


class PortalKeyboard:
    """RemoteDesktop keysym typing (KWin, GNOME): one unicode keysym per
    character; the paste chord rides the same path. Nothing reports whether
    the app consumed it, which is why auto-learn is off (ADR 0007)."""

    def __init__(self, sessions: PortalSessions, gate: SendGate) -> None:
        self._sessions = sessions
        self._gate = gate

    def send_text_units(self, units: list[int]) -> bool:
        raw = b"".join(u.to_bytes(2, "little") for u in units)
        with self._gate:
            for char in raw.decode("utf-16-le", errors="replace"):
                keysym = keysym_for(char)
                self._sessions.notify_keysym(keysym, True)
                self._sessions.notify_keysym(keysym, False)
        return True

    def send_chord(self, keys: list[int]) -> None:
        with self._gate:
            for keysym in keys:
                self._sessions.notify_keysym(keysym, True)
            for keysym in reversed(keys):
                self._sessions.notify_keysym(keysym, False)

    def send_mask_key(self) -> None:
        pass

    def modifiers_down(self) -> bool:
        return False    # no whole-keyboard view on Wayland (ADR 0008)


class PortalClipboard:
    """The Clipboard portal riding the RemoteDesktop session: `SetSelection`
    announces, the portal asks us to write through `SelectionTransfer`, and
    `SelectionOwnerChanged` is the counter (ADR 0007). This is what gives
    GNOME Wayland its paste rung."""

    def __init__(self, sessions: PortalSessions) -> None:
        self._sessions = sessions
        self._lock = threading.Lock()
        self._text: str | None = None
        self._exclude = False
        self._seq = 0
        conn = sessions.conn
        if conn is not None:
            conn.subscribe(P.portal_signal_rule("Clipboard", "SelectionOwnerChanged"),
                           self._on_owner_changed)
            conn.subscribe(P.portal_signal_rule("Clipboard", "SelectionTransfer"),
                           self._on_transfer)

    def _session(self) -> str:
        session = self._sessions.rd_session
        if session is None or not self._sessions.rd_live:
            raise PortalError("no live RemoteDesktop session for the clipboard")
        if not self._sessions.clipboard_enabled:
            raise PortalError("the Clipboard portal was not enabled on this session")
        return session

    def _on_owner_changed(self, msg) -> None:
        session, _opts = P.signal_body(msg)
        if session == self._sessions.rd_session:
            with self._lock:
                self._seq += 1

    def _on_transfer(self, msg) -> None:
        session, mime, serial = P.signal_body(msg)
        conn = self._sessions.conn
        if session != self._sessions.rd_session or conn is None:
            return
        ok = False
        try:
            (fd,) = conn.send_message(P.clipboard_selection_write(session, int(serial)))
            raw = fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)
            try:
                payload = b"secret" if mime == "x-kde-passwordManagerHint" else \
                    (self._text or "").encode("utf-8")
                os.write(raw, payload)
                ok = True
            finally:
                os.close(raw)
        except Exception:
            log.debug("clipboard transfer failed", exc_info=True)
        try:
            conn.send_message(P.clipboard_selection_write_done(session, int(serial), ok))
        except PortalError:
            log.debug("SelectionWriteDone failed", exc_info=True)

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        session = self._session()
        with self._lock:
            self._text, self._exclude = text, exclude_from_history
        mimes = ["text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT"]
        if exclude_from_history:
            mimes.append("x-kde-passwordManagerHint")
        self._sessions.conn.send_message(P.clipboard_set_selection(session, mimes))

    def get_text(self) -> str | None:
        try:
            session = self._session()
            (fd,) = self._sessions.conn.send_message(
                P.clipboard_selection_read(session, "text/plain;charset=utf-8"))
        except Exception:
            log.debug("clipboard read failed", exc_info=True)
            return None
        raw = fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)
        chunks = []
        try:
            while True:
                ready, _, _ = select.select([raw], [], [], 1.0)
                if not ready:
                    break
                chunk = os.read(raw, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(raw)
        return b"".join(chunks).decode("utf-8", errors="replace") if chunks else None

    def sequence_number(self) -> int:
        with self._lock:
            return self._seq


class WaylandFocusedApp:
    """Identity from the compositor's toplevel protocol where there is one
    (`tracker`), else "unknown" — Reduced, and a Portal compositor offering
    neither (ADR 0009). The grant state is the sessions'."""

    def __init__(self, sessions: PortalSessions, index: DesktopIndex, tracker=None) -> None:
        self._sessions = sessions
        self._index = index
        self._tracker = tracker

    def name(self) -> str:
        if self._tracker is None:
            return "unknown"
        try:
            app_id, pid = self._tracker.active()
        except Exception:
            log.debug("toplevel probe failed", exc_info=True)
            return "unknown"
        if app_id:
            return app_id
        if pid:
            return exe_basename(int(pid)) or "unknown"
        return "unknown"

    def window_rect(self) -> tuple[int, int, int, int] | None:
        return None     # no overlay on the Wayland tiers in v1 (ADR 0014)

    def injection_blocked(self) -> str | None:
        return None

    def permission_granted(self) -> bool:
        return self._sessions.granted()

    def running_apps(self) -> list[tuple[str, str]]:
        self._index.refresh()
        return self._index.installed()

    def display_name(self, identity: str) -> str | None:
        return self._index.display_name(identity)
