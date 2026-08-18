"""A fake portal bus (spec M6 §2.1): the three methods a Linux seam adapter
needs from `portal.PortalConnection`, with scripted replies and a way to
raise signals — so the adapters and the Request plumbing are tested with no
session bus, on every OS.

`FakeBus.replies` maps `(interface, member)` to either a body tuple, a
callable `(msg) -> body`, or an exception instance to raise. Unscripted
methods get an empty method return. `sent` records every message; `emit()`
delivers a signal to whichever subscriptions match, on the calling thread —
tests drive the "portal thread" themselves.
"""

from __future__ import annotations

from collections.abc import Callable

from jeepney import HeaderFields, MatchRule, Message, new_method_return, new_signal

from cadent.platform.linux import portal


def _reply(parent: Message, body: tuple) -> Message:
    return new_method_return(parent, body=tuple(body))


class FakeBus:
    def __init__(self, unique_name: str = ":1.42") -> None:
        self.unique_name = unique_name
        self.sent: list[Message] = []
        self.replies: dict[tuple[str | None, str | None], object] = {}
        self.subscriptions: dict[int, tuple[MatchRule, Callable[[Message], None]]] = {}
        self._next = 1
        self.unsubscribed: list[int] = []
        # When set, every Request-bearing call gets this Response code right
        # after its reply unless a scripted handler already answered — keeps
        # tests from sitting through the bounded 5 s wait.
        self.auto_response: int | None = None

    # ---- the Bus contract ---------------------------------------------------

    def send_and_get_reply(self, msg: Message,
                           timeout: float = portal.BOUNDED_TIMEOUT) -> Message:
        self.sent.append(msg)
        key = (msg.header.fields.get(HeaderFields.interface),
               msg.header.fields.get(HeaderFields.member))
        scripted = self.replies.get(key, ())
        if isinstance(scripted, BaseException):
            raise scripted
        if callable(scripted):
            scripted = scripted(msg)
        reply = _reply(msg, tuple(scripted))
        if self.auto_response is not None:
            for arg in msg.body:
                if isinstance(arg, dict) and "handle_token" in arg:
                    self.respond(portal.request_path(self.unique_name,
                                                     arg["handle_token"][1]),
                                 self.auto_response)
        return reply

    def send(self, msg: Message) -> None:
        self.sent.append(msg)

    def subscribe(self, rule: MatchRule, callback: Callable[[Message], None]) -> int:
        token = self._next
        self._next += 1
        self.subscriptions[token] = (rule, callback)
        return token

    def unsubscribe(self, token: int) -> None:
        self.subscriptions.pop(token, None)
        self.unsubscribed.append(token)

    def arm_gui_guard(self) -> None:
        pass

    # ---- test helpers -------------------------------------------------------

    def calls(self, member: str) -> list[Message]:
        return [m for m in self.sent
                if m.header.fields.get(HeaderFields.member) == member]

    def emit(self, path: str, interface: str, member: str, signature: str,
             body: tuple) -> int:
        """Deliver a signal to every matching subscription; returns how many
        callbacks ran."""
        msg = new_signal(portal.DBusAddress(path, interface=interface),
                         member, signature, body)
        ran = 0
        for rule, callback in list(self.subscriptions.values()):
            if rule.matches(msg):
                callback(msg)
                ran += 1
        return ran

    def respond(self, handle: str, code: int, results: dict | None = None) -> int:
        """The portal's `Response` on a Request handle."""
        return self.emit(handle, portal.REQUEST_IFACE, "Response", "ua{sv}",
                         (code, results or {}))
