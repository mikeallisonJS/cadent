"""The clipboard through the Clipboard portal, riding the RemoteDesktop session
(spec §2.3, §3; ADR 0007).

Where no data-control protocol is offered (GNOME), the portal is the only way
to own or read the selection. `RequestClipboard` goes in before the session's
`Start`; after that `SetSelection` claims the clipboard, the portal asks for
the bytes with `SelectionTransfer` (answered through `SelectionWrite`), and
`SelectionOwnerChanged` is the counter behind `sequence_number()` — only
changes by someone else are counted, so Cadent's own write never reads as a
foreign one.

Every reply the portal waits on is made from a worker: a signal handler runs
on the portal's receive thread, which cannot also wait for a reply.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from jeepney import Message

from . import portal as P
from . import wlclient
from .datacontrol import HIDE_FROM_HISTORY, HIDE_VALUE, READ_TIMEOUT, TEXT_MIMES
from .portal import Portal, PortalError

log = logging.getLogger(__name__)


class PortalClipboard:
    def __init__(self, portal: Portal, session: Callable[[], str | None]) -> None:
        self._portal = portal
        self._session = session
        self._lock = threading.Lock()
        self._seq = 0
        self._mimes: list[str] = []
        self._own: str | None = None
        self._wired = False

    def request(self, session: str) -> None:
        """Ask for the clipboard on `session`; before it is started."""
        self._portal.call(P.request_clipboard_message(session))
        if not self._wired:
            self._wired = True
            self._portal.subscribe_signal(
                P.signal_rule(P.CLIPBOARD, "SelectionTransfer", P.PORTAL_PATH),
                self._on_transfer)
            self._portal.subscribe_signal(
                P.signal_rule(P.CLIPBOARD, "SelectionOwnerChanged", P.PORTAL_PATH),
                self._on_owner)

    # ---- Clipboard seam ---------------------------------------------------

    def get_text(self) -> str | None:
        with self._lock:
            own, mimes = self._own, list(self._mimes)
        if own is not None:
            return own
        session = self._session()
        if session is None:
            return None
        # A selection made before the session started was never announced:
        # ask for plain text and let the portal say no.
        mime = next((m for m in TEXT_MIMES if m in mimes), TEXT_MIMES[0])
        try:
            fd = self._take_fd(self._portal.call(P.selection_read_message(session, mime)))
        except PortalError:
            return None
        data = wlclient.read_all(fd, READ_TIMEOUT)
        return None if data is None else data.decode("utf-8", errors="replace")

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        session = self._session()
        if session is None:
            raise PortalError("no input session to own the clipboard on")
        mimes = list(TEXT_MIMES) + ([HIDE_FROM_HISTORY] if exclude_from_history else [])
        with self._lock:
            self._own = text
        self._portal.call(P.set_selection_message(session, mimes))

    def sequence_number(self) -> int:
        with self._lock:
            return self._seq

    # ---- signals (portal thread) -------------------------------------------

    def _on_owner(self, msg: Message) -> None:
        _session, options = msg.body[:2]
        mimes = options.get("mime_types", ("as", []))[1]
        if options.get("session_is_owner", ("b", False))[1]:
            return
        with self._lock:
            self._seq += 1
            self._mimes = list(mimes)
            self._own = None

    def _on_transfer(self, msg: Message) -> None:
        session, mime, serial = msg.body[:3]
        threading.Thread(target=self._answer, args=(session, mime, serial),
                         name="cadent-portal-clipboard", daemon=True).start()

    def _answer(self, session: str, mime: str, serial: int) -> None:
        with self._lock:
            text = self._own
        ok = False
        try:
            if text is not None:
                fd = self._take_fd(self._portal.call(
                    P.selection_write_message(session, serial)))
                data = HIDE_VALUE if mime == HIDE_FROM_HISTORY else text.encode("utf-8")
                try:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(fd, view):]
                    ok = True
                finally:
                    os.close(fd)
        except (OSError, PortalError) as exc:
            log.warning("could not hand the clipboard to the portal: %s", exc)
        try:
            self._portal.call(P.selection_write_done_message(session, serial, ok))
        except PortalError as exc:
            log.warning("could not finish the clipboard transfer: %s", exc)

    @staticmethod
    def _take_fd(body: tuple) -> int:
        try:
            return body[0].to_raw_fd()
        except (IndexError, AttributeError) as exc:
            raise PortalError("the portal sent no pipe") from exc
