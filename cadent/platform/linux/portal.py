"""The one D-Bus connection Linux's seams share, and the portal plumbing on it.

Transport is jeepney (ADR 0013): pure Python, no native library, no Qt. This
module owns

- **one connection** to the session bus and **one receive thread**
  (`cadent-portal`) that routes method replies to their waiters and fans
  signals out to subscribers *on that thread* — the same "the hook's own
  thread; callers marshal" contract `HotkeyTap.start` and `watch_tray_ink`
  document. Nothing else runs on it.
- **two call classes**, named: *bounded* — every method reply waits at most
  `BOUNDED_TIMEOUT` seconds and then the rung fails; *consent* — a call that
  raises a dialog a human answers (`BindShortcuts`, `RemoteDesktop.Start`) is
  never awaited: it returns a `PendingResponse` whose outcome arrives on the
  portal's `Response` signal.
- the **Request / Session handle dance** every portal call needs: subscribe
  to `Response` on `/org/freedesktop/portal/desktop/request/<sender>/<token>`
  *before* sending, then check the handle the reply actually returned.
- **hand-built messages** with explicit signatures for the four interfaces
  Cadent speaks (GlobalShortcuts, RemoteDesktop, Clipboard, Settings) — no
  generated proxies. The builders are pure functions of their arguments, so
  tests assert on messages with no bus at all.

Seam adapters take the connection in their constructors; tests hand them a
fake implementing the same three methods (`send_and_get_reply`, `subscribe`,
`unsubscribe`). A blocking call from the GUI thread is a bug — logged, never
asserted: an assert here can kill the tray.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from jeepney import (
    DBusAddress,
    HeaderFields,
    MatchRule,
    Message,
    MessageType,
    message_bus,
    new_method_call,
)

from .keysyms import unicode_keysym  # noqa: F401  (re-exported for adapters)

log = logging.getLogger(__name__)

# ---- constants -------------------------------------------------------------

BOUNDED_TIMEOUT = 5.0           # seconds; every method reply (spec §2.2)

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE_PREFIX = "org.freedesktop.portal."
REQUEST_IFACE = PORTAL_IFACE_PREFIX + "Request"
SESSION_IFACE = PORTAL_IFACE_PREFIX + "Session"
GLOBAL_SHORTCUTS_IFACE = PORTAL_IFACE_PREFIX + "GlobalShortcuts"
REMOTE_DESKTOP_IFACE = PORTAL_IFACE_PREFIX + "RemoteDesktop"
CLIPBOARD_IFACE = PORTAL_IFACE_PREFIX + "Clipboard"
SETTINGS_IFACE = PORTAL_IFACE_PREFIX + "Settings"

# Portal Response codes (org.freedesktop.portal.Request::Response).
RESPONSE_SUCCESS = 0
RESPONSE_CANCELLED = 1
RESPONSE_OTHER = 2

# RemoteDesktop device bitmask and persist modes.
DEVICE_KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2


class PortalError(Exception):
    """A portal call failed: D-Bus error, missing interface, no bus."""


class PortalTimeout(PortalError):
    """A bounded call got no reply within `BOUNDED_TIMEOUT`."""


class PortalUnavailable(PortalError):
    """No session bus, or `xdg-desktop-portal` is not on it."""


# ---- the bus contract adapters code against ---------------------------------

class Bus(Protocol):
    """What a seam adapter needs from the connection — the fake in the tests
    implements exactly this."""

    unique_name: str

    def send_and_get_reply(self, msg: Message,
                           timeout: float = BOUNDED_TIMEOUT) -> Message: ...

    def subscribe(self, rule: MatchRule,
                  callback: Callable[[Message], None]) -> int: ...

    def unsubscribe(self, token: int) -> None: ...


def sender_token(unique_name: str) -> str:
    """The bus name as the portals mangle it into Request/Session paths: the
    leading colon dropped, dots to underscores (`:1.42` → `1_42`)."""
    return unique_name.lstrip(":").replace(".", "_")


def request_path(unique_name: str, handle_token: str) -> str:
    return f"{PORTAL_PATH}/request/{sender_token(unique_name)}/{handle_token}"


def session_path(unique_name: str, session_token: str) -> str:
    return f"{PORTAL_PATH}/session/{sender_token(unique_name)}/{session_token}"


# ---- the real connection ------------------------------------------------------

class PortalConnection:
    """One jeepney connection and its `cadent-portal` receive thread.

    Build with `open()` (connects to the session bus, says Hello) or hand a
    connected `jeepney.io.blocking.DBusConnection` to the constructor. The
    factory calls `arm_gui_guard()` once its startup probes are done: from
    then on a bounded call issued from that (the main, GUI) thread is the
    bug §2.2 wants logged.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.unique_name: str = conn.unique_name
        self._send_lock = threading.Lock()
        self._waiters: dict[int, _Waiter] = {}
        self._subs: dict[int, tuple[MatchRule, Callable[[Message], None]]] = {}
        self._sub_ids = itertools.count(1)
        self._state_lock = threading.Lock()
        self._gui_thread: int | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._receive_loop,
                                        name="cadent-portal", daemon=True)
        self._thread.start()

    @classmethod
    def open(cls) -> PortalConnection:
        """Connect to the session bus, or raise `PortalUnavailable`."""
        from jeepney.io.blocking import open_dbus_connection

        try:
            # FDs on: the Clipboard portal hands selection data over unix fds.
            conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        except Exception as exc:  # no DBUS_SESSION_BUS_ADDRESS, refused, ...
            raise PortalUnavailable(f"session bus unavailable: {exc}") from exc
        return cls(conn)

    def arm_gui_guard(self) -> None:
        """Remember the calling thread as the GUI thread; startup probes ran
        before this, the Qt loop runs after."""
        self._gui_thread = threading.get_ident()

    def close(self) -> None:
        self._closed = True
        try:
            self._conn.close()
        except Exception:
            log.debug("closing the portal connection failed", exc_info=True)

    # ---- sending ------------------------------------------------------------

    def send_and_get_reply(self, msg: Message,
                           timeout: float = BOUNDED_TIMEOUT) -> Message:
        """A *bounded* call: the reply, or `PortalTimeout` after `timeout`
        seconds, or `PortalError` for a D-Bus error reply."""
        if threading.get_ident() == self._gui_thread:
            # Logged, never asserted (spec §2.2): the tray must survive it.
            log.warning("blocking portal call %s.%s issued from the GUI thread",
                        msg.header.fields.get(HeaderFields.interface),
                        msg.header.fields.get(HeaderFields.member),
                        stack_info=log.isEnabledFor(logging.DEBUG))
        waiter = _Waiter()
        with self._send_lock:
            serial = next(self._conn.outgoing_serial)
            with self._state_lock:
                self._waiters[serial] = waiter
            try:
                self._conn.send(msg, serial=serial)
            except Exception as exc:
                with self._state_lock:
                    self._waiters.pop(serial, None)
                raise PortalError(f"send failed: {exc}") from exc
        if not waiter.event.wait(timeout):
            with self._state_lock:
                self._waiters.pop(serial, None)
            raise PortalTimeout(
                f"no reply within {timeout:g}s to "
                f"{msg.header.fields.get(HeaderFields.member)}")
        reply = waiter.reply
        if reply is None:
            raise PortalError("portal connection lost")
        if reply.header.message_type == MessageType.error:
            raise PortalError(reply.header.fields.get(HeaderFields.error_name,
                                                      "unknown D-Bus error"))
        return reply

    def send(self, msg: Message) -> None:
        """Fire a message with no reply wanted (signals, no-reply calls)."""
        with self._send_lock:
            self._conn.send(msg)

    # ---- signals ------------------------------------------------------------

    def subscribe(self, rule: MatchRule,
                  callback: Callable[[Message], None]) -> int:
        """Register `callback` for signals matching `rule` and tell the bus.
        The callback runs on the `cadent-portal` thread and must stay fast."""
        token = next(self._sub_ids)
        with self._state_lock:
            self._subs[token] = (rule, callback)
        try:
            self.send_and_get_reply(message_bus.AddMatch(rule))
        except PortalError:
            with self._state_lock:
                self._subs.pop(token, None)
            raise
        return token

    def unsubscribe(self, token: int) -> None:
        with self._state_lock:
            entry = self._subs.pop(token, None)
        if entry is None:
            return
        try:
            self.send_and_get_reply(message_bus.RemoveMatch(entry[0]))
        except PortalError:
            log.debug("RemoveMatch failed", exc_info=True)

    # ---- the receive thread -------------------------------------------------

    def _receive_loop(self) -> None:
        while not self._closed:
            try:
                msg = self._conn.receive()
            except Exception:
                if not self._closed:
                    log.warning("portal connection lost; the Linux portal "
                                "seams are dead for this run", exc_info=True)
                self._fail_all_waiters()
                return
            self._dispatch(msg)

    def _dispatch(self, msg: Message) -> None:
        kind = msg.header.message_type
        if kind in (MessageType.method_return, MessageType.error):
            serial = msg.header.fields.get(HeaderFields.reply_serial)
            with self._state_lock:
                waiter = self._waiters.pop(serial, None)
            if waiter is not None:
                waiter.reply = msg
                waiter.event.set()
            return
        if kind == MessageType.signal:
            with self._state_lock:
                callbacks = [cb for rule, cb in self._subs.values()
                             if rule.matches(msg)]
            for cb in callbacks:
                try:
                    cb(msg)
                except Exception:  # a subscriber must never kill the thread
                    log.exception("portal signal handler failed")

    def _fail_all_waiters(self) -> None:
        with self._state_lock:
            waiters = list(self._waiters.values())
            self._waiters.clear()
        for waiter in waiters:
            waiter.event.set()      # reply stays None → PortalError at the waiter


class _Waiter:
    __slots__ = ("event", "reply")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.reply: Message | None = None


# ---- Request plumbing ---------------------------------------------------------

class PendingResponse:
    """The outcome of a portal request, arriving on its `Response` signal.

    Bounded callers `wait()`; consent callers attach `then(callback)` and
    return at once — the callback runs on the portal thread. `results` is
    the portal's `a{sv}` payload as a plain dict."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[PendingResponse], None]] = []
        self._lock = threading.Lock()
        self.code: int | None = None
        self.results: dict[str, Any] = {}

    @property
    def done(self) -> bool:
        return self._event.is_set()

    @property
    def ok(self) -> bool:
        return self.code == RESPONSE_SUCCESS

    def wait(self, timeout: float = BOUNDED_TIMEOUT) -> PendingResponse:
        if not self._event.wait(timeout):
            raise PortalTimeout(f"no Response within {timeout:g}s")
        return self

    def then(self, callback: Callable[[PendingResponse], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback(self)

    def _complete(self, code: int, results: Mapping[str, Any]) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.code = int(code)
            self.results = unwrap_variants(results)
            callbacks = list(self._callbacks)
            self._callbacks.clear()
            self._event.set()
        for cb in callbacks:
            try:
                cb(self)
            except Exception:
                log.exception("portal response callback failed")


def unwrap_variants(value: Any) -> Any:
    """jeepney hands `a{sv}` values as `(signature, value)` pairs; adapters
    want plain Python. Recursive, because portal results nest (`shortcuts`
    is `a(sa{sv})`)."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) \
            and _looks_like_signature(value[0]) and _fits(value[0], value[1]):
        return unwrap_variants(value[1])
    if isinstance(value, dict):
        return {k: unwrap_variants(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(unwrap_variants(v) for v in value)
    return value


def _fits(sig: str, value: Any) -> bool:
    """Does `value` have the shape `sig` says? Guards against a plain
    (id, dict) pair — a shortcut id like "tab" is signature-shaped text."""
    head = sig[0]
    if head in "sog":
        return isinstance(value, str) and len(sig) == 1
    if head in "ynqiuxth":
        return isinstance(value, int) and not isinstance(value, bool) and len(sig) == 1
    if head == "b":
        return isinstance(value, bool) and len(sig) == 1
    if head == "d":
        return isinstance(value, (int, float)) and len(sig) == 1
    if head == "a":
        return isinstance(value, (list, dict, bytes))
    if head in "(v":
        return isinstance(value, tuple)
    return False


def _looks_like_signature(text: str) -> bool:
    return bool(text) and all(c in "ybnqiuxtdsogav(){}h" for c in text)


class RequestTokens:
    """Handle tokens unique within this connection — the portal keys Request
    and Session objects by (sender, token)."""

    def __init__(self, prefix: str = "cadent") -> None:
        self._prefix = prefix
        self._count = itertools.count(1)

    def next(self) -> str:
        return f"{self._prefix}{next(self._count)}"


def send_request(bus: Bus, build: Callable[[str], Message],
                 tokens: RequestTokens, *,
                 timeout: float = BOUNDED_TIMEOUT) -> PendingResponse:
    """The portal Request dance. `build(handle_token)` returns the method
    call carrying that token in its options.

    Subscribes to `Response` on the path the token predicts *before* sending
    (a fast portal can answer before a late subscription lands), sends as a
    bounded call, then checks the handle the reply returned — a portal older
    than 0.9 hands back its own path, in which case the subscription moves.
    Returns the pending response; whether to `wait()` is the caller's call
    class."""
    token = tokens.next()
    expected = request_path(bus.unique_name, token)
    pending = PendingResponse()
    subscription: list[int] = []

    def on_response(msg: Message) -> None:
        code, results = msg.body
        pending._complete(code, results)
        # One Response per Request: drop the match rather than leak an
        # AddMatch per call (ListShortcuts runs every few seconds).
        for tok in subscription:
            try:
                bus.unsubscribe(tok)
            except PortalError:
                pass
        subscription.clear()

    def rule_for(path: str) -> MatchRule:
        return MatchRule(type="signal", interface=REQUEST_IFACE,
                         member="Response", path=path)

    subscription.append(bus.subscribe(rule_for(expected), on_response))
    try:
        reply = bus.send_and_get_reply(build(token), timeout=timeout)
    except PortalError:
        for tok in subscription:
            bus.unsubscribe(tok)
        raise
    handle = reply.body[0] if reply.body else expected
    if handle != expected and not pending.done:
        for tok in subscription:
            bus.unsubscribe(tok)
        subscription[:] = [bus.subscribe(rule_for(handle), on_response)]
    pending.handle = handle  # type: ignore[attr-defined]
    return pending


def watch_session_closed(bus: Bus, session_handle: str,
                         callback: Callable[[], None]) -> int:
    """Subscribe to `Session.Closed` for one session; the portal restarting
    closes every session silently, and this is the only notice."""
    rule = MatchRule(type="signal", interface=SESSION_IFACE, member="Closed",
                     path=session_handle)
    return bus.subscribe(rule, lambda _msg: callback())


# ---- message builders (pure) --------------------------------------------------

def portal_address(interface: str) -> DBusAddress:
    return DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS_NAME, interface=interface)


def variant(sig: str, value: Any) -> tuple[str, Any]:
    return (sig, value)


def _options(handle_token: str, **extra: tuple[str, Any]) -> dict[str, tuple[str, Any]]:
    opts = {"handle_token": variant("s", handle_token)}
    opts.update(extra)
    return opts


# GlobalShortcuts (spec §4)

def global_shortcuts_create_session(handle_token: str,
                                    session_token: str) -> Message:
    return new_method_call(
        portal_address(GLOBAL_SHORTCUTS_IFACE), "CreateSession", "a{sv}",
        (_options(handle_token,
                  session_handle_token=variant("s", session_token)),))


def global_shortcuts_bind(session_handle: str, handle_token: str,
                          shortcuts: Iterable[tuple[str, str, str]],
                          parent_window: str = "") -> Message:
    """`shortcuts` is (id, description, preferred_trigger) triples; the
    trigger follows the freedesktop shortcuts grammar (`CTRL+SUPER+space`).
    The compositor owns the binding — this is a suggestion (ADR 0008)."""
    body = [(sid, {"description": variant("s", desc),
                   "preferred_trigger": variant("s", trigger)})
            for sid, desc, trigger in shortcuts]
    return new_method_call(
        portal_address(GLOBAL_SHORTCUTS_IFACE), "BindShortcuts", "oa(sa{sv})sa{sv}",
        (session_handle, body, parent_window, _options(handle_token)))


def global_shortcuts_list(session_handle: str, handle_token: str) -> Message:
    return new_method_call(
        portal_address(GLOBAL_SHORTCUTS_IFACE), "ListShortcuts", "oa{sv}",
        (session_handle, _options(handle_token)))


# RemoteDesktop (spec §3) — the keysym typing path and the session the
# Clipboard portal rides.

def remote_desktop_create_session(handle_token: str,
                                  session_token: str) -> Message:
    return new_method_call(
        portal_address(REMOTE_DESKTOP_IFACE), "CreateSession", "a{sv}",
        (_options(handle_token,
                  session_handle_token=variant("s", session_token)),))


def remote_desktop_select_devices(session_handle: str, handle_token: str, *,
                                  restore_token: str | None = None,
                                  persist_mode: int = PERSIST_UNTIL_REVOKED,
                                  types: int = DEVICE_KEYBOARD) -> Message:
    extra: dict[str, tuple[str, Any]] = {
        "types": variant("u", types),
        "persist_mode": variant("u", persist_mode),
    }
    if restore_token:
        extra["restore_token"] = variant("s", restore_token)
    return new_method_call(
        portal_address(REMOTE_DESKTOP_IFACE), "SelectDevices", "oa{sv}",
        (session_handle, _options(handle_token, **extra)))


def remote_desktop_start(session_handle: str, handle_token: str,
                         parent_window: str = "") -> Message:
    return new_method_call(
        portal_address(REMOTE_DESKTOP_IFACE), "Start", "osa{sv}",
        (session_handle, parent_window, _options(handle_token)))


def remote_desktop_notify_keysym(session_handle: str, keysym: int,
                                 pressed: bool) -> Message:
    """`NotifyKeyboardKeysym(session, options, keysym, state)` — state 1 is
    pressed, 0 released. xkb defines a keysym for every code point
    (`0x01000000 + cp`), which is what makes this a full-unicode path."""
    return new_method_call(
        portal_address(REMOTE_DESKTOP_IFACE), "NotifyKeyboardKeysym", "oa{sv}iu",
        (session_handle, {}, keysym, 1 if pressed else 0))


# Clipboard portal (spec §3): rides an existing RemoteDesktop session;
# `RequestClipboard` must precede `Start`.

def clipboard_request(session_handle: str) -> Message:
    return new_method_call(
        portal_address(CLIPBOARD_IFACE), "RequestClipboard", "oa{sv}",
        (session_handle, {}))


def clipboard_set_selection(session_handle: str,
                            mime_types: Iterable[str] = ("text/plain;charset=utf-8",
                                                         "text/plain")) -> Message:
    return new_method_call(
        portal_address(CLIPBOARD_IFACE), "SetSelection", "oa{sv}",
        (session_handle, {"mime_types": variant("as", list(mime_types))}))


def clipboard_selection_write(session_handle: str, serial: int) -> Message:
    return new_method_call(
        portal_address(CLIPBOARD_IFACE), "SelectionWrite", "ou",
        (session_handle, serial))


def clipboard_selection_write_done(session_handle: str, serial: int,
                                   success: bool) -> Message:
    return new_method_call(
        portal_address(CLIPBOARD_IFACE), "SelectionWriteDone", "oub",
        (session_handle, serial, success))


def clipboard_selection_read(session_handle: str,
                             mime_type: str = "text/plain;charset=utf-8") -> Message:
    return new_method_call(
        portal_address(CLIPBOARD_IFACE), "SelectionRead", "os",
        (session_handle, mime_type))


# Settings portal (spec §10): one ReadAll snapshot plus SettingChanged.

def settings_read_all(namespaces: Iterable[str] = ("org.freedesktop.appearance",
                                                    "org.gnome.desktop.interface",
                                                    "org.gnome.desktop.a11y.interface",
                                                    "org.kde.kdeglobals.KDE")) -> Message:
    return new_method_call(
        portal_address(SETTINGS_IFACE), "ReadAll", "as", (list(namespaces),))


def settings_read(namespace: str, key: str) -> Message:
    return new_method_call(
        portal_address(SETTINGS_IFACE), "Read", "ss", (namespace, key))


def settings_changed_rule() -> MatchRule:
    return MatchRule(type="signal", interface=SETTINGS_IFACE,
                     member="SettingChanged", path=PORTAL_PATH)


def global_shortcuts_signal_rule(member: str) -> MatchRule:
    """`Activated` / `Deactivated` / `ShortcutsChanged` on the portal object."""
    return MatchRule(type="signal", interface=GLOBAL_SHORTCUTS_IFACE,
                     member=member, path=PORTAL_PATH)


def clipboard_signal_rule(member: str) -> MatchRule:
    """`SelectionOwnerChanged` / `SelectionTransfer` on the portal object."""
    return MatchRule(type="signal", interface=CLIPBOARD_IFACE,
                     member=member, path=PORTAL_PATH)


# Interface presence: the D-Bus properties every portal interface exposes.

def portal_version_query(interface: str) -> Message:
    """`org.freedesktop.DBus.Properties.Get(interface, "version")` — the
    cheapest way to ask whether `xdg-desktop-portal` has a backend for an
    interface: an error reply means it does not (spec §1.3)."""
    return new_method_call(
        DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS_NAME,
                    interface="org.freedesktop.DBus.Properties"),
        "Get", "ss", (interface, "version"))


def interface_version(bus: Bus, interface: str) -> int | None:
    """The interface's version, or None where the portal has no backend for
    it (or no portal answers at all)."""
    try:
        reply = bus.send_and_get_reply(portal_version_query(interface))
    except PortalError:
        return None
    try:
        return int(unwrap_variants(reply.body[0]))
    except (IndexError, TypeError, ValueError):
        return None
