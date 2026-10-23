"""The one D-Bus connection to the desktop portals, and the plumbing every
portal call needs (ADR 0013).

Transport is jeepney: pure Python, speaks the socket itself, no native
library in the bundle. One connection, owned by the Linux platform module and
handed to every seam adapter by constructor — portal `Request` and `Session`
paths derive from the connection's unique name, and `permission_granted()`
reads two portals at once, so three connections would be three truths.

Threads: jeepney's router runs its own receive loop; everything that arrives
through a filter lands on **one `cadent-portal` dispatch thread**, which is
where every signal callback in this package runs. That is the same "the
hook's own thread; callers marshal" contract `HotkeyTap.start` and
`watch_tray_ink` document, so nothing above the seam learns there is a bus.

Two timeout classes, named here and nowhere else (spec §2.2):

- **bounded** (`call`, `request(wait=True)`) — every plain method reply and
  every Request whose answer is data (`CreateSession`, `ListShortcuts`,
  `ReadAll`, `NotifyKeyboardKeysym`, `SetSelection`) waits at most
  `BOUNDED_S`; on timeout the caller's rung fails and dictation falls
  through to inserting the raw transcript.
- **consent** (`request(wait=False)`) — `BindShortcuts` and
  `RemoteDesktop.Start` raise a dialog a human answers, so they are never
  awaited: the outcome arrives on the `Response` signal, delivered to the
  caller's callback on the dispatch thread.

Messages are hand-built with explicit signatures. Generated proxies would
hide the `handle_token` / sender-path dance every portal call needs:
subscribe to `Response` on
`/org/freedesktop/portal/desktop/request/<sender>/<token>` *before* sending,
then check the handle the reply names — an older portal may answer with a
different path, in which case the subscription moves.
"""

from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from jeepney import DBusAddress, DBusErrorResponse, MatchRule, MessageType, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.wrappers import unwrap_msg

log = logging.getLogger(__name__)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_PREFIX = "org.freedesktop.portal."
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
# The host-app registry (xdg-desktop-portal ≥ 1.18): an unsandboxed app has
# no `.flatpak-info` to name it, and GlobalShortcuts refuses a session for a
# caller with no app id — so the first call on the connection says who we
# are. Must precede every other portal call from this connection.
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"

# The bounded timeout class (spec §2.2). Five seconds is long for a method
# reply a healthy portal answers in milliseconds and short enough that a
# wedged portal costs one dictation, not a frozen worker.
BOUNDED_S = 5.0

# Response codes on org.freedesktop.portal.Request.Response.
RESPONSE_OK, RESPONSE_CANCELLED, RESPONSE_OTHER = 0, 1, 2


class PortalError(Exception):
    """A portal call that failed outright — a D-Bus error, or no bus."""


class PortalTimeout(PortalError):
    """A bounded call that did not answer inside `BOUNDED_S`."""


@dataclass(frozen=True)
class Response:
    """What a portal Request came back with: the response code and its
    results vardict, variants already unwrapped to plain values."""

    code: int
    results: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code == RESPONSE_OK


class Transport(Protocol):
    """What `PortalConnection` needs from jeepney's router — and what a test
    double provides instead, so every adapter in this package drives against
    a fake with no bus (spec §2.1)."""

    @property
    def unique_name(self) -> str: ...

    def send(self, message, *, serial=None) -> None: ...

    def send_and_get_reply(self, message, *, timeout=None): ...

    def filter(self, rule, *, queue=None, bufsize=1): ...

    def close(self) -> None: ...


# ---- message building: pure, testable without a bus ----------------------------

def portal_address(interface: str, path: str = PORTAL_PATH) -> DBusAddress:
    """The portal object for one interface — short names (`"GlobalShortcuts"`)
    are expanded, fully-qualified ones pass through."""
    if "." not in interface:
        interface = PORTAL_PREFIX + interface
    return DBusAddress(path, bus_name=PORTAL_BUS, interface=interface)


def portal_call(interface: str, method: str, signature: str, body: tuple,
                path: str = PORTAL_PATH):
    return new_method_call(portal_address(interface, path), method, signature, body)


def vardict(**values) -> dict[str, tuple[str, Any]]:
    """An `a{sv}` body element from keyword pairs of (signature, value)."""
    return {key: value for key, value in values.items() if value is not None}


def unwrap_variants(value):
    """Variants arrive as (signature, value) pairs; the adapters want values.
    Walks dicts and lists so a nested `a{sv}` inside a struct reads cleanly."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) \
            and _looks_like_signature(value[0]):
        return unwrap_variants(value[1])
    if isinstance(value, dict):
        return {k: unwrap_variants(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap_variants(v) for v in value]
    if isinstance(value, tuple):
        return tuple(unwrap_variants(v) for v in value)
    return value


def _looks_like_signature(text: str) -> bool:
    return bool(text) and all(c in "ybnqiuxtdsogavh(){}" for c in text)


def mangle_sender(unique_name: str) -> str:
    """`:1.42` → `1_42`: the sender fragment of every Request and Session
    path, per the portal spec."""
    return unique_name.lstrip(":").replace(".", "_")


def new_token() -> str:
    return "cadent_" + secrets.token_hex(8)


# ---- the connection -------------------------------------------------------------

class _Subscription:
    def __init__(self, owner: PortalConnection, rule: MatchRule,
                 callback: Callable[[Any], None], bus_match: bool) -> None:
        self.rule = rule
        self.callback = callback
        self.bus_match = bus_match
        self._owner = owner

    def close(self) -> None:
        self._owner._unsubscribe(self)


class PendingRequest:
    """A consent request in flight (spec §2.2): never awaited, the outcome
    reaches `callback` on the dispatch thread whenever the human answers."""

    def __init__(self, handle: str) -> None:
        self.handle = handle


class PortalConnection:
    def __init__(self, transport: Transport, *, thread_name: str = "cadent-portal") -> None:
        self._transport = transport
        self._lock = threading.Lock()
        self._subscriptions: list[_Subscription] = []
        self._closed = False
        self.app_id: str | None = None
        self.registered = False
        self._interfaces: dict[str, bool] = {}
        self._opened_at = time.monotonic()
        # One local filter catching every signal, dispatched here against the
        # subscriptions' own rules — two router filters sharing a queue would
        # hand the same message over twice.
        self._queue: queue.Queue = queue.Queue(maxsize=256)
        self._filter = transport.filter(MatchRule(type="signal"), queue=self._queue)
        self._thread = threading.Thread(target=self._dispatch, daemon=True,
                                        name=thread_name)
        self._thread.start()

    @classmethod
    def open(cls, app_id: str | None = None) -> PortalConnection | None:
        """The session bus, or None where there is none (a bare TTY, a CI
        runner) — in which case every portal-backed seam reports its
        mechanism absent and the fallback answers stand. `app_id` is
        registered with the portal first thing (see `REGISTRY_IFACE`)."""
        try:
            from jeepney.io.threading import DBusRouter, open_dbus_connection

            conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        except Exception:
            log.info("no session bus; portals are unavailable", exc_info=True)
            return None
        connection = cls(DBusRouter(conn))
        connection.app_id = app_id
        if app_id:
            connection.register_app_id()
        return connection

    def register_app_id(self) -> bool:
        """Tell the portal which app this connection is. The registry looks
        the id up as a desktop entry, so this only succeeds once Cadent's
        own `.desktop` file exists (ADR 0011) — the adapters retry it right
        before the first session they open, after first run has written it.
        A portal too old to carry the registry answers UnknownMethod, which
        is worth a log line and nothing more: it refuses the shortcuts
        session later, and the hotkey-unavailable fault names that."""
        if self.registered or not self.app_id:
            return self.registered
        try:
            self.send_message(new_method_call(
                DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS, interface=REGISTRY_IFACE),
                "Register", "sa{sv}", (self.app_id, {})))
            self.registered = True
        except PortalError:
            log.info("could not register %s with the portal", self.app_id, exc_info=True)
        return self.registered

    @property
    def unique_name(self) -> str:
        return self._transport.unique_name

    @property
    def sender(self) -> str:
        return mangle_sender(self.unique_name)

    def request_path(self, token: str) -> str:
        return f"{PORTAL_PATH}/request/{self.sender}/{token}"

    def session_path(self, token: str) -> str:
        return f"{PORTAL_PATH}/session/{self.sender}/{token}"

    # ---- the bounded class ---------------------------------------------------

    def call(self, interface: str, method: str, signature: str, body: tuple = (),
             *, path: str = PORTAL_PATH, timeout: float = BOUNDED_S) -> tuple:
        """One method call, reply awaited for at most `timeout` — the bounded
        class. Raises `PortalTimeout` or `PortalError`; never hangs."""
        return self.send_message(portal_call(interface, method, signature, body, path),
                                 timeout=timeout)

    def send_message(self, message, *, timeout: float = BOUNDED_S) -> tuple:
        self._note_gui_thread(message)
        try:
            reply = self._transport.send_and_get_reply(message, timeout=timeout)
        except TimeoutError as exc:   # concurrent.futures' and the builtin alias
            raise PortalTimeout(f"{_member(message)} did not answer in {timeout:g} s") from exc
        except Exception as exc:
            raise PortalError(str(exc)) from exc
        try:
            return unwrap_msg(reply)
        except DBusErrorResponse as exc:
            raise PortalError(f"{_member(message)}: {exc.name} {exc.data}") from exc

    def get_property(self, interface: str, name: str, *, path: str = PORTAL_PATH):
        """`org.freedesktop.DBus.Properties.Get`, value unwrapped, or None on
        any error — the probe behind `interface_available`."""
        if "." not in interface:
            interface = PORTAL_PREFIX + interface
        address = DBusAddress(path, bus_name=PORTAL_BUS, interface=PROPERTIES_IFACE)
        try:
            (value,) = self.send_message(new_method_call(address, "Get", "ss",
                                                         (interface, name)))
        except PortalError:
            return None
        return unwrap_variants(value)

    def interface_available(self, interface: str) -> bool:
        """Does the portal frontend export this interface at all? Every
        portal interface carries a `version` property, so a readable one is
        the honest probe — a missing backend (stock Sway's GlobalShortcuts)
        answers with an error here rather than a dialog later."""
        if interface not in self._interfaces:
            self._interfaces[interface] = self.get_property(interface, "version") is not None
        return self._interfaces[interface]

    # ---- requests: subscribe, send, check the handle --------------------------

    def request(self, interface: str, method: str, signature: str,
                body: Callable[[str], tuple], *, wait: bool,
                on_response: Callable[[Response], None] | None = None,
                timeout: float = BOUNDED_S) -> Response | PendingRequest:
        """A portal method that answers through a `Request` object.

        `body(token)` builds the arguments with the `handle_token` in place.
        `wait=True` is the bounded class — the Response is awaited for
        `timeout` and returned. `wait=False` is the consent class — the call
        returns at once and `on_response` is called on the dispatch thread
        when the dialog is answered.
        """
        token = new_token()
        expected = self.request_path(token)
        box: queue.Queue = queue.Queue(maxsize=1)
        state: dict[str, Any] = {"sub": None}

        def deliver(msg) -> None:
            response = _parse_response(msg)
            sub = state.get("sub")
            if sub is not None:
                sub.close()
            if wait:
                box.put(response)
            elif on_response is not None:
                on_response(response)

        state["sub"] = self.subscribe(MatchRule(
            type="signal", interface=REQUEST_IFACE, member="Response", path=expected),
            deliver, bus_match=False)
        try:
            (handle,) = self.call(interface, method, signature, body(token), timeout=timeout)
        except PortalError:
            state["sub"].close()
            raise
        if handle != expected:
            # An older portal that ignored handle_token: move the watch to
            # the handle it chose. A Response that raced this move is lost,
            # which is the documented reason the token scheme exists.
            state["sub"].close()
            state["sub"] = self.subscribe(MatchRule(
                type="signal", interface=REQUEST_IFACE, member="Response", path=handle),
                deliver, bus_match=False)
        if not wait:
            return PendingRequest(handle)
        try:
            return box.get(timeout=timeout)
        except queue.Empty as exc:
            state["sub"].close()
            raise PortalTimeout(f"{method}: no Response in {timeout:g} s") from exc

    # ---- signals --------------------------------------------------------------

    def subscribe(self, rule: MatchRule, callback: Callable[[Any], None],
                  *, bus_match: bool = True) -> _Subscription:
        """Deliver every signal matching `rule` to `callback`, on the
        dispatch thread. `bus_match` adds the rule on the bus as well, which
        broadcast signals (`SettingChanged`) need; signals the portal directs
        at our unique name (`Response`, a session's `Closed`) do not."""
        sub = _Subscription(self, rule, callback, bus_match)
        with self._lock:
            self._subscriptions.append(sub)
        if bus_match:
            try:
                self._transport.send_and_get_reply(message_bus.AddMatch(rule),
                                                   timeout=BOUNDED_S)
            except Exception:
                log.debug("AddMatch failed for %s", rule, exc_info=True)
        return sub

    def _unsubscribe(self, sub: _Subscription) -> None:
        with self._lock:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)
            else:
                return
        if sub.bus_match and not self._closed:
            try:
                self._transport.send(message_bus.RemoveMatch(sub.rule))
            except Exception:
                log.debug("RemoveMatch failed", exc_info=True)

    def _dispatch(self) -> None:
        while True:
            msg = self._queue.get()
            if msg is None:
                return
            with self._lock:
                targets = [s for s in self._subscriptions if s.rule.matches(msg)]
            for sub in targets:
                try:
                    sub.callback(msg)
                except Exception:
                    log.exception("portal signal handler failed")

    def close(self) -> None:
        self._closed = True
        try:
            self._filter.close()
        except Exception:
            pass
        self._queue.put(None)
        try:
            self._transport.close()
        except Exception:
            log.debug("closing the portal transport failed", exc_info=True)

    # ---- the one rule about threads -------------------------------------------

    def _note_gui_thread(self, message) -> None:
        # A blocking call from the GUI thread is a bug — logged, never
        # asserted: an assert here can take the tray down with it (§2.2).
        # Startup reads before the event loop exists are the one exception,
        # so the first seconds after opening log at debug.
        if threading.current_thread() is threading.main_thread():
            level = logging.DEBUG if time.monotonic() - self._opened_at < 15 else logging.WARNING
            log.log(level, "portal call %s is blocking the GUI thread", _member(message))


def _member(message) -> str:
    from jeepney import HeaderFields

    return str(message.header.fields.get(HeaderFields.member, "?"))


def _parse_response(msg) -> Response:
    code, results = msg.body
    return Response(int(code), unwrap_variants(results))


def signal_body(msg) -> tuple:
    """A signal's arguments, variants unwrapped — what callbacks read."""
    return tuple(unwrap_variants(v) for v in msg.body)


def is_signal(msg) -> bool:
    from jeepney import HeaderFields

    return msg.header.message_type == MessageType.signal and \
        HeaderFields.member in msg.header.fields


# ---- the hand-built messages, one per portal call (ADR 0013) ----------------------

def global_shortcuts_create_session(token: str, session_token: str):
    return portal_call("GlobalShortcuts", "CreateSession", "a{sv}", (vardict(
        handle_token=("s", token), session_handle_token=("s", session_token)),))


def global_shortcuts_bind(session: str, shortcuts: list[tuple[str, str, str]],
                          token: str, parent_window: str = ""):
    """`shortcuts` is (id, description, preferred trigger) per shortcut — all
    bound in one call, since the portal allows one bind per session."""
    body = [(sid, vardict(description=("s", description),
                          preferred_trigger=("s", trigger)))
            for sid, description, trigger in shortcuts]
    return portal_call("GlobalShortcuts", "BindShortcuts", "oa(sa{sv})sa{sv}",
                       (session, body, parent_window, vardict(handle_token=("s", token))))


def global_shortcuts_list(session: str, token: str):
    return portal_call("GlobalShortcuts", "ListShortcuts", "oa{sv}",
                       (session, vardict(handle_token=("s", token))))


def remote_desktop_create_session(token: str, session_token: str):
    return portal_call("RemoteDesktop", "CreateSession", "a{sv}", (vardict(
        handle_token=("s", token), session_handle_token=("s", session_token)),))


# RemoteDesktop device types and persist modes, per the portal spec.
DEVICE_KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2


def remote_desktop_select_devices(session: str, token: str, restore_token: str | None):
    return portal_call("RemoteDesktop", "SelectDevices", "oa{sv}", (session, vardict(
        handle_token=("s", token), types=("u", DEVICE_KEYBOARD),
        persist_mode=("u", PERSIST_UNTIL_REVOKED),
        restore_token=("s", restore_token) if restore_token else None)))


def remote_desktop_start(session: str, token: str, parent_window: str = ""):
    return portal_call("RemoteDesktop", "Start", "osa{sv}",
                       (session, parent_window, vardict(handle_token=("s", token))))


def remote_desktop_notify_keysym(session: str, keysym: int, pressed: bool):
    return portal_call("RemoteDesktop", "NotifyKeyboardKeysym", "oa{sv}iu",
                       (session, {}, keysym, 1 if pressed else 0))


def clipboard_request(session: str):
    return portal_call("Clipboard", "RequestClipboard", "oa{sv}", (session, {}))


def clipboard_set_selection(session: str, mime_types: list[str]):
    return portal_call("Clipboard", "SetSelection", "oa{sv}",
                       (session, vardict(mime_types=("as", mime_types))))


def clipboard_selection_write(session: str, serial: int):
    return portal_call("Clipboard", "SelectionWrite", "ou", (session, serial))


def clipboard_selection_write_done(session: str, serial: int, ok: bool):
    return portal_call("Clipboard", "SelectionWriteDone", "oub", (session, serial, ok))


def clipboard_selection_read(session: str, mime_type: str):
    return portal_call("Clipboard", "SelectionRead", "os", (session, mime_type))


def settings_read_all(namespaces: list[str]):
    return portal_call("Settings", "ReadAll", "as", (namespaces,))


def session_closed_rule(session: str) -> MatchRule:
    return MatchRule(type="signal", interface=SESSION_IFACE, member="Closed", path=session)


def portal_signal_rule(interface: str, member: str) -> MatchRule:
    return MatchRule(type="signal", interface=PORTAL_PREFIX + interface, member=member,
                     path=PORTAL_PATH)
