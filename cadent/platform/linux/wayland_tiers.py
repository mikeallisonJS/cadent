"""The Portal and Reduced tiers: the seams over the portals and the
compositor protocols (spec M6 §2.2–§2.3, §3, §4, §5, §9; ADR 0007, 0008,
0009, 0012, 0013).

One `WaylandRun` probes, once at startup, what this session actually offers
and assembles the seams around it:

- **HotkeyTap** — `org.freedesktop.portal.GlobalShortcuts`: both chords bound
  in one `BindShortcuts` (consent), `Activated`/`Deactivated` synthesized
  into the parsed keysym events the chord machine already understands.
  Health = `ListShortcuts` reporting our ids (cached, refreshed off the GUI
  thread) — never the listener's own flag. Interface absent → the hotkey is
  disarmed and `hotkey_unavailable` is set (spec §1.3).
- **KeyboardOutput** — `zwp_virtual_keyboard_v1` where the compositor offers
  it (wlroots: full unicode, no dialog), else `RemoteDesktop.
  NotifyKeyboardKeysym` on a restore-token session (KWin, GNOME).
- **Clipboard** — `ext-data-control-v1` where offered (KWin, wlroots), else
  the Clipboard portal riding the same RemoteDesktop session
  (`RequestClipboard` before `Start`); neither → the paste rung is dropped
  for the run and the copy says so.
- **FocusedApp** — `plasma-window-management` (KWin/SteamOS) or
  `wlr-foreign-toplevel-management` (wlroots), else `"unknown"` and
  `per_app_overrides=False` for the run; `permission_granted()` = shortcut
  bound **and** the RemoteDesktop session live *when the run's mechanisms
  need it*.
- **`request_permission()`** — issues `CreateSession`/`BindShortcuts` and
  `RemoteDesktop.Start` (consent, never awaited) on a helper thread; no
  auto-retry after a denial. Restore tokens live in
  `DATA_DIR/portal-tokens.json`, never `config.json`. A portal restart
  closes sessions silently: each is re-created **once** on `Closed`, and on
  failure the seam stops and `permission_granted()` goes false.

Everything here runs against the `Bus` protocol in `portal.py` and the
`WaylandClient` in `wayland.py`, both of which tests replace with fakes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from ..keycodes import LINUX_KEYCODES
from . import portal
from .keysyms import keysyms_for_text
from .portal import (
    Bus,
    PendingResponse,
    PortalError,
    RequestTokens,
    interface_version,
    send_request,
    session_path,
    unwrap_variants,
    watch_session_closed,
)

log = logging.getLogger(__name__)

UNKNOWN = "unknown"
DICTATE_ID, CLEANUP_ID = "dictate", "cleanup"
HEALTH_REFRESH_S = 5.0


# ---- restore tokens (spec §9.4) ------------------------------------------------

class TokenStore:
    """`DATA_DIR/portal-tokens.json`: an opaque credential kept out of the
    hand-edited config, so "Back it up and start fresh" cannot revoke the
    grant. Lost or rejected tokens re-ask silently."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, key: str) -> str | None:
        value = self._load().get(key)
        return value if isinstance(value, str) and value else None

    def set(self, key: str, token: str | None) -> None:
        with self._lock:
            data = self._load()
            if token:
                data[key] = token
            else:
                data.pop(key, None)
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except OSError:
                log.debug("could not persist portal token", exc_info=True)


# ---- chord ↔ portal trigger ----------------------------------------------------

_TRIGGER_MODIFIERS = {"<ctrl>": "CTRL", "<shift>": "SHIFT", "<alt>": "ALT",
                      "<cmd>": "LOGO", "<win>": "LOGO", "<super>": "LOGO"}


def combo_to_trigger(combo: str) -> str | None:
    """`<ctrl>+<cmd>+space` → `CTRL+LOGO+space` in the freedesktop shortcuts
    grammar; None for a modifier-only chord, which that grammar cannot
    express (ADR 0008 — the tier's default binds instead)."""
    mods: list[str] = []
    key: str | None = None
    for part in combo.lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part in _TRIGGER_MODIFIERS:
            mods.append(_TRIGGER_MODIFIERS[part])
        else:
            key = part
    if key is None:
        return None
    if key.startswith("f") and key[1:].isdigit():
        key = key.upper()
    return "+".join([*mods, key])


def chord_keysyms(combo: str) -> list[int]:
    """The keysyms to synthesize for a chord: one representative per group
    (the left-hand modifier, the key itself), in the chord's order — what
    the chord machine built for that combo expects to see go down."""
    from ...chord import parse_combo

    return [min(group) for group in parse_combo(combo, table=LINUX_KEYCODES)]


# ---- GlobalShortcuts tap -----------------------------------------------------------

class GlobalShortcutsTap:
    """`HotkeyTap` over the GlobalShortcuts portal (spec §4).

    `start(on_event, chords)` creates the session, subscribes to
    `Activated`/`Deactivated`, and asks `ListShortcuts` (off the GUI thread)
    whether a previous run's grant already covers our ids. If it does not,
    the tap stays disarmed until `request_bind()` — issued by
    `request_permission()` — gets a `Response`. A modifier-only chord is
    bound as the tier default (`default_combo`) while the synthesized events
    stay the chord machine's own; `substituted` tells the pane why."""

    def __init__(self, bus: Bus | None, tokens: RequestTokens,
                 default_combo: str, default_cleanup_combo: str,
                 worker: Callable[[Callable[[], None]], None] | None = None) -> None:
        self._bus = bus
        self._tokens = tokens
        self._default_combo = default_combo
        self._default_cleanup = default_cleanup_combo
        self._worker = worker or _spawn
        self._lock = threading.Lock()
        self._on_event: Callable[[int, bool, bool], None] | None = None
        self._chords: dict[str, list[int]] = {}         # id → keysyms to synthesize
        self._triggers: dict[str, str] = {}             # id → preferred trigger
        self._session: str | None = None
        self._bound: dict[str, str] = {}                # id → compositor description
        self._subs: list[int] = []
        self._last_health = 0.0
        self._refreshing = False
        self._recovered_once = False
        self._rebind_after_recovery = False
        self.substituted: dict[str, str] = {}           # id → the default that replaced it
        self.interface_present = bus is not None and \
            interface_version(bus, portal.GLOBAL_SHORTCUTS_IFACE) is not None

    # ---- the seam -----------------------------------------------------------

    def start(self, on_event, chords=()) -> None:
        self._on_event = on_event
        ids = [DICTATE_ID, CLEANUP_ID]
        defaults = [self._default_combo, self._default_cleanup]
        for sid, combo, default in zip(ids, chords, defaults, strict=False):
            trigger = combo_to_trigger(combo)
            if trigger is None:
                trigger = combo_to_trigger(default) or "CTRL+LOGO+space"
                self.substituted[sid] = default
            self._triggers[sid] = trigger
            try:
                self._chords[sid] = chord_keysyms(combo)
            except ValueError:
                log.warning("hotkey %r unparseable; not bound", combo)
        if not self.interface_present or self._bus is None:
            return
        self._worker(self._open_session)

    def stop(self) -> None:
        self._on_event = None
        if self._bus is None:
            return
        for token in self._subs:
            try:
                self._bus.unsubscribe(token)
            except PortalError:
                pass
        self._subs = []

    def bound_shortcuts(self) -> Mapping[str, str] | None:
        if not self.interface_present:
            return None     # nobody binds here; the tray says hotkey-unavailable
        with self._lock:
            return dict(self._bound)

    def available(self) -> bool:
        """False on a desktop whose portal has no GlobalShortcuts backend
        (stock Sway): the hotkey stays disarmed and the app shows
        `hotkey-unavailable` (spec §1.3) — never `permission-needed` too."""
        return self.interface_present

    # ---- health (ADR 0002's rule: never the listener's own flag) ----------------

    def bound(self) -> bool:
        """Our shortcut ids are bound as far as the last `ListShortcuts`
        said. Reading this schedules a refresh (never blocks the caller)."""
        self._maybe_refresh()
        with self._lock:
            return DICTATE_ID in self._bound

    def _maybe_refresh(self) -> None:
        if self._bus is None or self._session is None:
            return
        now = time.monotonic()
        with self._lock:
            if self._refreshing or now - self._last_health < HEALTH_REFRESH_S:
                return
            self._refreshing = True
        self._worker(self._refresh_health)

    def _refresh_health(self) -> None:
        try:
            self._list_shortcuts()
        finally:
            with self._lock:
                self._refreshing = False
                self._last_health = time.monotonic()

    # ---- portal calls (helper thread) ----------------------------------------------

    def _open_session(self) -> None:
        assert self._bus is not None
        session_token = self._tokens.next()
        try:
            pending = send_request(
                self._bus,
                lambda tok: portal.global_shortcuts_create_session(tok, session_token),
                self._tokens)
            pending.wait()
        except PortalError:
            log.warning("GlobalShortcuts CreateSession failed", exc_info=True)
            return
        if not pending.ok:
            log.warning("GlobalShortcuts CreateSession refused (%s)", pending.code)
            return
        handle = pending.results.get("session_handle") or \
            session_path(self._bus.unique_name, session_token)
        with self._lock:
            self._session = handle
            self._bound = {}
        for member, is_down in (("Activated", True), ("Deactivated", False)):
            self._subs.append(self._bus.subscribe(
                portal.global_shortcuts_signal_rule(member),
                lambda msg, down=is_down: self._on_signal(msg, down)))
        self._subs.append(self._bus.subscribe(
            portal.global_shortcuts_signal_rule("ShortcutsChanged"),
            self._on_shortcuts_changed))
        self._subs.append(watch_session_closed(self._bus, handle, self._on_closed))
        self._list_shortcuts()
        if self._rebind_after_recovery and not self._bound:
            # A recovered session whose bindings did not carry over: ask
            # once more (a good grant answers without a dialog).
            self._rebind_after_recovery = False
            self.request_bind()

    def _list_shortcuts(self) -> None:
        if self._bus is None or self._session is None:
            return
        try:
            pending = send_request(
                self._bus, lambda tok: portal.global_shortcuts_list(self._session, tok),
                self._tokens)
            pending.wait()
        except PortalError:
            log.debug("ListShortcuts failed", exc_info=True)
            return
        if pending.ok:
            self._absorb_shortcuts(pending.results.get("shortcuts", []))

    def _absorb_shortcuts(self, shortcuts) -> None:
        bound: dict[str, str] = {}
        for item in shortcuts or []:
            try:
                sid, props = item[0], item[1]
            except (TypeError, IndexError):
                continue
            if sid in self._chords:
                bound[sid] = str(props.get("trigger_description") or
                                 props.get("preferred_trigger") or "")
        with self._lock:
            self._bound = bound

    def request_bind(self) -> PendingResponse | None:
        """The consent call: bind both chords in one `BindShortcuts`. Never
        awaited; the `Response` (or `ShortcutsChanged`) updates the health.
        Returns None where there is nothing to ask."""
        if self._bus is None or self._session is None or not self._chords:
            return None
        shortcuts = [(sid, "Dictate (hold or toggle)" if sid == DICTATE_ID
                      else "Toggle cleanup", self._triggers[sid])
                     for sid in self._chords]
        try:
            pending = send_request(
                self._bus,
                lambda tok: portal.global_shortcuts_bind(self._session, tok, shortcuts),
                self._tokens)
        except PortalError:
            log.warning("BindShortcuts failed", exc_info=True)
            return None
        pending.then(lambda p: self._absorb_shortcuts(p.results.get("shortcuts", []))
                     if p.ok else None)
        return pending

    # ---- signals (portal thread) ---------------------------------------------------

    def _on_signal(self, msg, is_down: bool) -> None:
        try:
            session_handle, shortcut_id = msg.body[0], msg.body[1]
        except (IndexError, TypeError):
            return
        if session_handle != self._session:
            return
        keysyms = self._chords.get(shortcut_id)
        on_event = self._on_event
        if not keysyms or on_event is None:
            return
        for keysym in (keysyms if is_down else reversed(keysyms)):
            on_event(keysym, is_down, False)

    def _on_shortcuts_changed(self, msg) -> None:
        try:
            if msg.body[0] != self._session:
                return
            self._absorb_shortcuts(unwrap_variants(msg.body[1]))
        except (IndexError, TypeError):
            pass

    def _on_closed(self) -> None:
        """A portal restart: re-create the session once, then admit defeat."""
        with self._lock:
            was_bound = bool(self._bound)
            self._session = None
            self._bound = {}
            if self._recovered_once:
                log.warning("GlobalShortcuts session closed again; hotkey is dead "
                            "for this run")
                return
            self._recovered_once = True
            self._rebind_after_recovery = was_bound
        log.info("GlobalShortcuts session closed; re-creating once")
        self._worker(self._open_session)


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="cadent-portal-work", daemon=True).start()


# ---- the RemoteDesktop session -----------------------------------------------------

class RemoteDesktopSession:
    """One keyboard-capable RemoteDesktop session, restore-token backed, that
    the keysym typing path and the Clipboard portal share. `live` flips on
    the `Start` Response; `Closed` re-creates it once."""

    TOKEN_KEY = "remote-desktop"

    def __init__(self, bus: Bus | None, tokens: RequestTokens, store: TokenStore,
                 want_clipboard: bool,
                 worker: Callable[[Callable[[], None]], None] | None = None) -> None:
        self._bus = bus
        self._tokens = tokens
        self._store = store
        self._want_clipboard = want_clipboard
        self._worker = worker or _spawn
        self._lock = threading.Lock()
        self.handle: str | None = None
        self.live = False
        self.clipboard_enabled = False
        self._recovered_once = False
        self._starting = False
        self._closed_sub: int | None = None
        self.interface_present = bus is not None and \
            interface_version(bus, portal.REMOTE_DESKTOP_IFACE) is not None
        self.clipboard_interface_present = bus is not None and \
            interface_version(bus, portal.CLIPBOARD_IFACE) is not None
        self.on_live: Callable[[], None] | None = None

    def request_start(self) -> None:
        """Consent: CreateSession → SelectDevices → [RequestClipboard] →
        Start, on a helper thread; the outcome lands on `live`."""
        with self._lock:
            if self._starting or self._bus is None or not self.interface_present:
                return
            self._starting = True
        self._worker(self._start)

    def _start(self) -> None:
        assert self._bus is not None
        try:
            session_token = self._tokens.next()
            created = send_request(
                self._bus,
                lambda tok: portal.remote_desktop_create_session(tok, session_token),
                self._tokens).wait()
            if not created.ok:
                self._reset_starting()
                return
            handle = created.results.get("session_handle") or \
                session_path(self._bus.unique_name, session_token)
            selected = send_request(
                self._bus,
                lambda tok: portal.remote_desktop_select_devices(
                    handle, tok, restore_token=self._store.get(self.TOKEN_KEY)),
                self._tokens).wait()
            if not selected.ok:
                self._reset_starting()
                return
            if self._want_clipboard and self.clipboard_interface_present:
                try:
                    self._bus.send_and_get_reply(portal.clipboard_request(handle))
                except PortalError:
                    log.debug("RequestClipboard failed", exc_info=True)
            with self._lock:
                self.handle = handle
            if self._closed_sub is not None:
                try:
                    self._bus.unsubscribe(self._closed_sub)
                except PortalError:
                    pass
            self._closed_sub = watch_session_closed(self._bus, handle, self._on_closed)
            started = send_request(
                self._bus, lambda tok: portal.remote_desktop_start(handle, tok),
                self._tokens)
            started.then(self._on_started)          # consent: never awaited
        except PortalError:
            log.warning("RemoteDesktop session setup failed", exc_info=True)
            with self._lock:
                self._starting = False

    def _reset_starting(self) -> None:
        with self._lock:
            self._starting = False

    def _on_started(self, response: PendingResponse) -> None:
        with self._lock:
            self._starting = False
            if not response.ok:
                # A rejected token re-asks next time; a denial raises the
                # banner and waits for the user — no auto-retry.
                if response.code != portal.RESPONSE_CANCELLED:
                    self._store.set(self.TOKEN_KEY, None)
                self.live = False
                return
            token = response.results.get("restore_token")
            if isinstance(token, str):
                self._store.set(self.TOKEN_KEY, token)
            self.clipboard_enabled = bool(response.results.get("clipboard_enabled", False))
            self.live = True
        if self.on_live is not None:
            self.on_live()

    def _on_closed(self) -> None:
        with self._lock:
            self.live = False
            self.handle = None
            if self._recovered_once:
                log.warning("RemoteDesktop session closed again; typing/paste dead "
                            "for this run")
                return
            self._recovered_once = True
        log.info("RemoteDesktop session closed; re-creating once on the restore token")
        self.request_start()

    # ---- the calls the seams make (bounded, from the worker) --------------------------

    def notify_keysym(self, keysym: int, pressed: bool) -> None:
        if not self.live or self._bus is None or self.handle is None:
            raise PortalError("RemoteDesktop session is not live")
        self._bus.send_and_get_reply(
            portal.remote_desktop_notify_keysym(self.handle, keysym, pressed))


# ---- KeyboardOutput -----------------------------------------------------------------

class PortalKeyboard:
    """Typing through whichever mechanism the run has: the virtual keyboard
    (wlroots) or the RemoteDesktop keysym path. Raises when neither can
    deliver — the injector then falls to its last resort. `send_mask_key` is
    a no-op; there is no held-modifier query on Wayland, so
    `modifiers_down()` is False (the portal releases the chord for us)."""

    def __init__(self, session: RemoteDesktopSession | None,
                 virtual_keyboard=None) -> None:
        self._session = session
        self._vk = virtual_keyboard

    @property
    def mechanism(self) -> str | None:
        if self._vk is not None:
            return "virtual-keyboard"
        if self._session is not None and self._session.interface_present:
            return "remote-desktop"
        return None

    def send_text_units(self, units: list[int]) -> bool:
        raw = b"".join(u.to_bytes(2, "little") for u in units)
        text = raw.decode("utf-16-le", errors="replace")
        keysyms = keysyms_for_text(text)
        if self._vk is not None:
            self._vk.type_keysyms(keysyms)
            return True
        if self._session is None:
            raise RuntimeError("no typing mechanism in this session")
        for keysym in keysyms:
            self._session.notify_keysym(keysym, True)
            self._session.notify_keysym(keysym, False)
        return True

    def send_chord(self, keys: list[int]) -> None:
        if self._vk is not None:
            self._vk.chord(keys)
            return
        if self._session is None:
            raise RuntimeError("no typing mechanism in this session")
        for keysym in keys:
            self._session.notify_keysym(keysym, True)
        for keysym in reversed(keys):
            self._session.notify_keysym(keysym, False)

    def send_mask_key(self) -> None:
        pass

    def modifiers_down(self) -> bool:
        return False


# ---- Clipboard ---------------------------------------------------------------------

class DataControlClipboard:
    """`Clipboard` over ext-data-control (KWin, wlroots)."""

    def __init__(self, data_control) -> None:
        self._dc = data_control

    def get_text(self) -> str | None:
        return self._dc.get_text()

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        self._dc.set_text(text, exclude_from_history)

    def sequence_number(self) -> int:
        return self._dc.sequence


class PortalClipboard:
    """`Clipboard` over the Clipboard portal riding the RemoteDesktop session
    (GNOME's only route). `SetSelection` announces ownership; the portal
    asks for the bytes with `SelectionTransfer`, answered by `SelectionWrite`
    + `SelectionWriteDone`; `SelectionOwnerChanged` is the counter."""

    def __init__(self, bus: Bus | None, session: RemoteDesktopSession) -> None:
        self._bus = bus
        self._session = session
        self._lock = threading.Lock()
        self._text: str | None = None
        self._seq = 0
        self._subs: list[int] = []
        if bus is not None:
            self._subs.append(bus.subscribe(
                portal.clipboard_signal_rule("SelectionOwnerChanged"), self._on_owner))
            self._subs.append(bus.subscribe(
                portal.clipboard_signal_rule("SelectionTransfer"), self._on_transfer))

    def _on_owner(self, msg) -> None:
        try:
            if msg.body[0] != self._session.handle:
                return
        except (IndexError, TypeError):
            return
        with self._lock:
            self._seq += 1
            options = unwrap_variants(msg.body[1]) if len(msg.body) > 1 else {}
            if not options.get("session_is_owner", False):
                self._text = None

    def _on_transfer(self, msg) -> None:
        """Signal on the portal thread. The write needs bounded replies,
        which only that thread dispatches — so the work moves to a helper
        thread rather than deadlocking on itself."""
        try:
            handle, mime, serial = msg.body[0], msg.body[1], int(msg.body[2])
        except (IndexError, TypeError, ValueError):
            return
        if handle != self._session.handle or self._bus is None:
            return
        _spawn(lambda: self._write_selection(handle, mime, serial))

    def _write_selection(self, handle: str, mime: str, serial: int) -> None:
        text = self._text
        ok = False
        try:
            reply = self._bus.send_and_get_reply(
                portal.clipboard_selection_write(handle, serial))
            fd = reply.body[0]
            raw_fd = fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)
            payload = (text or "").encode("utf-8", errors="replace") \
                if mime != portal.CLIPBOARD_IFACE else b""
            import os

            try:
                _write_fd(raw_fd, payload)
                ok = True
            finally:
                os.close(raw_fd)
        except Exception:
            log.debug("SelectionWrite failed", exc_info=True)
        try:
            self._bus.send_and_get_reply(
                portal.clipboard_selection_write_done(handle, serial, ok))
        except PortalError:
            log.debug("SelectionWriteDone failed", exc_info=True)

    def get_text(self) -> str | None:
        with self._lock:
            if self._text is not None:
                return self._text
        if self._bus is None or not self._session.live or self._session.handle is None:
            return None
        try:
            reply = self._bus.send_and_get_reply(
                portal.clipboard_selection_read(self._session.handle))
            fd = reply.body[0]
            raw_fd = fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)
            try:
                chunks = []
                while True:
                    chunk = os.read(raw_fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(raw_fd)
            return b"".join(chunks).decode("utf-8", errors="replace")
        except Exception:
            log.debug("SelectionRead failed", exc_info=True)
            return None

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        if self._bus is None or not self._session.live or self._session.handle is None:
            raise RuntimeError("Clipboard portal: no live session")
        with self._lock:
            self._text = text
        self._bus.send_and_get_reply(portal.clipboard_set_selection(self._session.handle))

    def sequence_number(self) -> int:
        return self._seq


def _write_fd(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        n = os.write(fd, view)
        view = view[n:]


# ---- FocusedApp -----------------------------------------------------------------------

class WaylandFocusedApp:
    """Identity from the compositor's toplevel protocol where there is one,
    else `"unknown"`; `permission_granted()` = shortcut bound and, where the
    run's typing/paste ride the RemoteDesktop session, that session live."""

    def __init__(self, toplevels, index, tap: GlobalShortcutsTap,
                 session: RemoteDesktopSession | None, needs_session: bool) -> None:
        self._toplevels = toplevels
        self._index = index
        self._tap = tap
        self._session = session
        self._needs_session = needs_session

    def name(self) -> str:
        if self._toplevels is None:
            return UNKNOWN
        try:
            active = self._toplevels.active()
        except Exception:
            log.debug("toplevel probe failed", exc_info=True)
            return UNKNOWN
        if not active:
            return UNKNOWN
        app_id = active.get("app_id")
        if app_id:
            return str(app_id)
        pid = active.get("pid")
        if pid:
            exe = _exe_basename(int(pid))
            if exe:
                return exe
        return UNKNOWN

    def window_rect(self) -> tuple[int, int, int, int] | None:
        if self._toplevels is None:
            return None
        try:
            active = self._toplevels.active()
        except Exception:
            return None
        return active.get("rect") if active else None

    def injection_blocked(self) -> str | None:
        return None

    def permission_granted(self) -> bool:
        if not self._tap.interface_present:
            # No GlobalShortcuts at all: that is `hotkey-unavailable`, not a
            # missing grant — the two faults are mutually exclusive (§9.3).
            return True
        if not self._tap.bound():
            return False
        if self._needs_session and self._session is not None:
            return self._session.live
        return True

    def running_apps(self) -> list[tuple[str, str]]:
        return self._index.installed_apps()

    def display_name(self, identity: str) -> str | None:
        return self._index.display_name(identity)


def _exe_basename(pid: int) -> str | None:
    try:
        return Path(os.readlink(f"/proc/{pid}/exe")).name or None
    except OSError:
        return None


# ---- the run: probe once, assemble -----------------------------------------------------

class WaylandRun:
    """What this Wayland session offers, probed once (spec §1.3), and the
    seams built on it. `hotkey_unavailable` and `paste_available` are what
    the Capabilities and the tray faults read; `request_permission()` is
    what `DesktopEnv.request_permission` calls."""

    def __init__(self, bus: Bus | None, *, tier: str, default_combo: str,
                 default_cleanup_combo: str, token_store: TokenStore,
                 desktop_index, wayland_client=None,
                 worker: Callable[[Callable[[], None]], None] | None = None) -> None:
        from . import wayland as wl

        self.tier = tier
        self.tokens = RequestTokens()
        self._worker = worker or _spawn
        client = wayland_client
        seat = None
        if client is not None:
            try:
                seat = wl.bind_seat(client)
                client.roundtrip()
            except Exception:
                log.debug("Wayland seat bind failed", exc_info=True)
                seat = None

        def try_build(factory, label):
            if client is None or (seat is None and label != "toplevels"):
                return None
            try:
                return factory()
            except Exception:
                log.debug("%s unavailable in this session", label, exc_info=True)
                return None

        # Typing: virtual keyboard where the compositor offers it, else the
        # RemoteDesktop keysym path.
        virtual_keyboard = try_build(lambda: wl.VirtualKeyboard(client, seat),
                                     "zwp_virtual_keyboard_v1")
        # Paste: data-control where offered, else the Clipboard portal.
        data_control = try_build(lambda: wl.DataControl(client, seat), "data-control")
        # Identity: plasma-window-management, then wlr-foreign-toplevel.
        toplevels = None
        if client is not None:
            for factory in (lambda: wl.PlasmaWindows(client),
                            lambda: wl.WlrToplevels(client)):
                toplevels = try_build(factory, "toplevels")
                if toplevels is not None:
                    try:
                        client.roundtrip()
                    except Exception:
                        pass
                    break

        self.tap = GlobalShortcutsTap(bus, self.tokens, default_combo,
                                      default_cleanup_combo, worker=self._worker)
        needs_session = virtual_keyboard is None or data_control is None
        self.session: RemoteDesktopSession | None = None
        if needs_session:
            self.session = RemoteDesktopSession(bus, self.tokens, token_store,
                                                want_clipboard=data_control is None,
                                                worker=self._worker)
            if not self.session.interface_present:
                self.session = None
        typing_via_session = virtual_keyboard is None and self.session is not None
        paste_via_portal = data_control is None and self.session is not None \
            and self.session.clipboard_interface_present
        self.paste_available = data_control is not None or paste_via_portal
        self.hotkey_unavailable = not self.tap.interface_present
        self.needs_session = typing_via_session or paste_via_portal
        self.per_app_overrides = toplevels is not None
        self.typing_mechanism = ("virtual-keyboard" if virtual_keyboard is not None
                                 else "remote-desktop" if typing_via_session else None)
        self.paste_mechanism = ("data-control" if data_control is not None
                                else "clipboard-portal" if paste_via_portal else None)

        self.keyboard = PortalKeyboard(self.session if typing_via_session else None,
                                       virtual_keyboard)
        if data_control is not None:
            self.clipboard = DataControlClipboard(data_control)
        elif paste_via_portal:
            self.clipboard = PortalClipboard(bus, self.session)
        else:
            from ..fallback import MemoryClipboard

            self.clipboard = MemoryClipboard()      # the paste rung is dropped anyway
        self.focused_app = WaylandFocusedApp(toplevels, desktop_index, self.tap,
                                             self.session, self.needs_session)
        log.info("Wayland run: tier=%s typing=%s paste=%s identity=%s hotkey=%s",
                 tier, self.typing_mechanism, self.paste_mechanism,
                 "toplevel-protocol" if toplevels else "unknown",
                 "portal" if not self.hotkey_unavailable else "unavailable")

    def request_permission(self) -> None:
        """Fire-and-forget (ADR 0012): the shortcut binding always, the
        RemoteDesktop session where the run's mechanisms ride it. Denials
        leave the button enabled; nothing here retries on its own."""
        def work():
            if self.tap.interface_present:
                if self.tap._session is None:
                    self.tap._open_session()
                self.tap.request_bind()
            if self.needs_session and self.session is not None and not self.session.live:
                self.session.request_start()
        self._worker(work)

    def warm(self) -> None:
        """At startup: reconnect a previous grant's RemoteDesktop session on
        its restore token (no dialog when the token is good) so the loop is
        live without a click. The tap does the same for shortcuts in `start`."""
        if self.needs_session and self.session is not None and \
                self._has_token(self.session):
            self.session.request_start()

    @staticmethod
    def _has_token(session: RemoteDesktopSession) -> bool:
        return session._store.get(session.TOKEN_KEY) is not None
