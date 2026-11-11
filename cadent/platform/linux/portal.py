"""The one D-Bus connection, its receive thread, and the portal plumbing
(spec §2.1–2.2, ADR 0013).

Every Linux seam adapter that talks to a portal is handed one `Portal` in its
constructor. Nothing here imports the GUI toolkit, and nothing opens the bus
until a call needs it — a session with no bus still starts, and the fakes the
tests pass in never touch a socket.

Two timeout classes, named where they are used:

- **bounded** (`call`, `bounded_request`): waits at most `BOUNDED_TIMEOUT`;
  past that the rung fails and the caller falls through.
- **consent** (`consent_request`): raises a dialog a human answers, so it is
  never awaited. The outcome arrives on the `Response` signal, on the portal
  thread.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from typing import Any

from jeepney import (
    DBusAddress,
    HeaderFields,
    MatchRule,
    Message,
    MessageType,
    new_method_call,
)
from jeepney.bus_messages import message_bus

log = logging.getLogger(__name__)

BOUNDED_TIMEOUT = 5.0           # seconds, every method reply (spec §2.2)
THREAD_NAME = "cadent-portal"

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQUEST_PREFIX = "/org/freedesktop/portal/desktop/request"
REQUEST_IFACE = "org.freedesktop.portal.Request"

GLOBAL_SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
CLIPBOARD = "org.freedesktop.portal.Clipboard"
SETTINGS = "org.freedesktop.portal.Settings"

KEYBOARD_DEVICE = 1             # RemoteDesktop device-type bit for keyboards
KEY_PRESSED, KEY_RELEASED = 1, 0


class PortalError(Exception):
    """A portal call failed: no bus, an error reply, or no reply in time."""


Variant = tuple[str, Any]
Options = Mapping[str, Variant]
Builder = Callable[[str], Message]      # handle token -> the call to send


# ---- hand-built messages: pure, explicit signatures, no bus ----------------


def _address(interface: str) -> DBusAddress:
    return DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS, interface=interface)


def _call(interface: str, method: str, signature: str, body: tuple) -> Message:
    return new_method_call(_address(interface), method, signature, body)


def create_session_message(interface: str, token: str,
                           session_token: str) -> Message:
    """`CreateSession` is the same call on GlobalShortcuts and RemoteDesktop."""
    return _call(interface, "CreateSession", "a{sv}", ({
        "handle_token": ("s", token),
        "session_handle_token": ("s", session_token),
    },))


def bind_shortcuts_message(session: str, shortcuts: list[tuple[str, str, str]],
                           token: str) -> Message:
    """`shortcuts` are (id, description, preferred_trigger); consent class."""
    entries = [(sid, {"description": ("s", desc),
                      "preferred_trigger": ("s", trigger)})
               for sid, desc, trigger in shortcuts]
    return _call(GLOBAL_SHORTCUTS, "BindShortcuts", "oa(sa{sv})sa{sv}",
                 (session, entries, "", {"handle_token": ("s", token)}))


def list_shortcuts_message(session: str, token: str) -> Message:
    return _call(GLOBAL_SHORTCUTS, "ListShortcuts", "oa{sv}",
                 (session, {"handle_token": ("s", token)}))


def select_keyboard_message(session: str, token: str,
                            restore_token: str | None = None) -> Message:
    options: dict[str, Variant] = {"handle_token": ("s", token),
                                   "types": ("u", KEYBOARD_DEVICE),
                                   "persist_mode": ("u", 2)}
    if restore_token:
        options["restore_token"] = ("s", restore_token)
    return _call(REMOTE_DESKTOP, "SelectDevices", "oa{sv}", (session, options))


def start_remote_desktop_message(session: str, token: str) -> Message:
    """Raises the grant dialog on first use; consent class."""
    return _call(REMOTE_DESKTOP, "Start", "osa{sv}",
                 (session, "", {"handle_token": ("s", token)}))


def notify_keysym_message(session: str, keysym: int, pressed: bool) -> Message:
    return _call(REMOTE_DESKTOP, "NotifyKeyboardKeysym", "oa{sv}iu",
                 (session, {}, keysym, KEY_PRESSED if pressed else KEY_RELEASED))


def request_clipboard_message(session: str) -> Message:
    return _call(CLIPBOARD, "RequestClipboard", "oa{sv}", (session, {}))


def set_selection_message(session: str, mime_types: list[str]) -> Message:
    return _call(CLIPBOARD, "SetSelection", "oa{sv}",
                 (session, {"mime_types": ("as", mime_types)}))


def read_all_message(namespaces: list[str]) -> Message:
    return _call(SETTINGS, "ReadAll", "as", (namespaces,))


# ---- Request paths ----------------------------------------------------------


def sender_token(unique_name: str) -> str:
    """`:1.42` → `1_42`: the connection's name as it appears in a handle."""
    return unique_name.lstrip(":").replace(".", "_")


def request_path(unique_name: str, token: str) -> str:
    return f"{REQUEST_PREFIX}/{sender_token(unique_name)}/{token}"


def response_rule(path: str) -> MatchRule:
    return MatchRule(type="signal", interface=REQUEST_IFACE, member="Response",
                     path=path)


class _CallbackQueue(queue.Queue):
    """A filter queue that calls back on the receiving thread instead of
    waiting to be read — how a consent response reaches its seam without a
    thread of ours waiting on it."""

    def __init__(self, callback: Callable[[Message], None]) -> None:
        super().__init__(maxsize=1)
        self._callback = callback

    def put_nowait(self, item: Message) -> None:
        self._callback(item)


def _open_router():
    from jeepney.io.threading import DBusRouter, open_dbus_connection

    router = DBusRouter(open_dbus_connection(bus="SESSION"))
    thread = getattr(router, "_rcv_thread", None)
    if thread is not None:
        thread.name = THREAD_NAME
    return router


class Portal:
    """One connection for the whole Linux platform.

    `connect` returns an object shaped like jeepney's `DBusRouter`
    (`unique_name`, `send`, `send_and_get_reply`, `filter`); tests pass a fake.
    """

    def __init__(self, connect: Callable[[], Any] = _open_router) -> None:
        self._connect = connect
        self._router: Any = None
        self._lock = threading.Lock()
        self._tokens = itertools.count(1)
        self._live: dict[str, Any] = {}     # handle path -> open filter
        self._answered: list[str] = []      # handle paths whose Request is over

    # -- connection -----------------------------------------------------

    def router(self) -> Any:
        with self._lock:
            if self._router is None:
                try:
                    self._router = self._connect()
                except Exception as exc:
                    raise PortalError(f"no session bus: {exc}") from exc
            return self._router

    @property
    def unique_name(self) -> str:
        return self.router().unique_name

    def close(self) -> None:
        with self._lock:
            router, self._router = self._router, None
        if router is not None and hasattr(router, "close"):
            router.close()

    def new_token(self) -> str:
        return f"cadent_{next(self._tokens)}"

    # -- bounded --------------------------------------------------------

    def call(self, message: Message, timeout: float = BOUNDED_TIMEOUT) -> tuple:
        """Send a method call and return its reply body (bounded class)."""
        if threading.current_thread() is threading.main_thread():
            log.warning("portal call %s blocks the GUI thread",
                        message.header.fields.get(HeaderFields.member, "?"))
        try:
            reply = self.router().send_and_get_reply(message, timeout=timeout)
        except PortalError:
            raise
        except Exception as exc:
            raise PortalError(f"no reply: {exc!r}") from exc
        if reply.header.message_type == MessageType.error:
            raise PortalError(f"error reply: {reply.body!r}")
        return reply.body

    def bounded_request(self, build: Builder,
                        timeout: float = BOUNDED_TIMEOUT) -> tuple[int, dict]:
        """A portal Request awaited up to `timeout`: (response code, results)."""
        replies: queue.Queue = queue.Queue(maxsize=1)
        self._request(build, replies.put_nowait)
        try:
            return _unpack_response(replies.get(timeout=timeout))
        except queue.Empty:
            raise PortalError("no Response in time") from None

    # -- consent --------------------------------------------------------

    def consent_request(self, build: Builder,
                        on_response: Callable[[int, dict], None] | None = None
                        ) -> None:
        """Fire a request a human answers; never waits. The outcome goes to
        `on_response(code, results)` on the portal thread."""
        def deliver(msg: Message) -> None:
            try:
                code, results = _unpack_response(msg)
                if on_response is not None:
                    on_response(code, results)
            except Exception:
                log.exception("portal response handler failed")

        self._request(build, deliver)

    # -- the Request handshake ------------------------------------------

    def _request(self, build: Builder, deliver: Callable[[Message], None]) -> None:
        """Subscribe to `Response` on the predicted handle *before* sending,
        then check the handle the portal returned (a Response that raced the
        reply would otherwise be lost)."""
        router = self.router()
        self._sweep()
        token = self.new_token()
        expected = request_path(router.unique_name, token)
        self._subscribe(router, expected, deliver)
        try:
            body = self._send_for_handle(router, build(token))
        except Exception:
            self._unsubscribe(expected)
            raise
        if body and body[0] != expected:
            log.warning("portal returned handle %s, expected %s", body[0], expected)
            self._unsubscribe(expected)
            self._subscribe(router, body[0], deliver)

    def _send_for_handle(self, router: Any, message: Message) -> tuple:
        try:
            reply = router.send_and_get_reply(message, timeout=BOUNDED_TIMEOUT)
        except Exception as exc:
            raise PortalError(f"no reply: {exc!r}") from exc
        if reply.header.message_type == MessageType.error:
            raise PortalError(f"error reply: {reply.body!r}")
        return reply.body

    def _subscribe(self, router: Any, path: str,
                   deliver: Callable[[Message], None]) -> None:
        rule = response_rule(path)
        handle = router.filter(rule, queue=_CallbackQueue(
            lambda msg, path=path: self._on_response(path, deliver, msg)))
        handle.__enter__()
        self._live[path] = handle
        router.send_and_get_reply(message_bus.AddMatch(rule),
                                  timeout=BOUNDED_TIMEOUT)

    def _on_response(self, path: str, deliver: Callable[[Message], None],
                     msg: Message) -> None:
        # A Request answers exactly once, but this runs inside jeepney's
        # dispatch loop over its filters: closing the handle here would change
        # that dict mid-iteration, so it is swept by the next request instead.
        self._answered.append(path)
        deliver(msg)

    def _sweep(self) -> None:
        while self._answered:
            self._unsubscribe(self._answered.pop())

    def _unsubscribe(self, path: str) -> None:
        handle = self._live.pop(path, None)
        if handle is not None:
            handle.__exit__(None, None, None)


def _unpack_response(msg: Message) -> tuple[int, dict]:
    code, results = msg.body
    return code, {k: v[1] for k, v in results.items()}
