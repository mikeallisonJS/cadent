"""The clipboard through `ext-data-control-v1` (spec §2.3, §3; ADR 0007).

KWin and the wlroots compositors let a background client own and read the
selection without focus. `zwlr_data_control_v1` is the same wire protocol under
another name and is used where only that is offered. Objects used: the manager,
one data device per seat, a data source while Cadent owns the selection, and
the offers other clients make.

`sequence_number()` counts selection events from other clients — the guard for
restoring only what nobody else touched. Cadent's own `set_text` ends in a
roundtrip, so any selection event it provokes has been counted before the
caller reads the counter.
"""

from __future__ import annotations

import logging
import os
import struct
import threading
from dataclasses import dataclass

from . import wayland, wlclient

log = logging.getLogger(__name__)

MANAGERS = ("ext_data_control_manager_v1", "zwlr_data_control_manager_v1")
SEAT = "wl_seat"
BIND_VERSION_CAP = 1

# manager
MGR_CREATE_SOURCE, MGR_GET_DEVICE = 0, 1
# device
DEV_SET_SELECTION = 0
EV_DEV_DATA_OFFER, EV_DEV_SELECTION, EV_DEV_PRIMARY = 0, 1, 3
# source / offer
SRC_OFFER, SRC_DESTROY = 0, 1
EV_SRC_SEND, EV_SRC_CANCELLED = 0, 1
OFFER_RECEIVE, OFFER_DESTROY = 0, 1
EV_OFFER_OFFER = 0

TEXT_MIMES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain",
              "TEXT", "STRING")
# Klipper and the other history managers skip a selection that carries this.
HIDE_FROM_HISTORY = "x-kde-passwordManagerHint"
HIDE_VALUE = b"secret"
READ_TIMEOUT = 1.0


@dataclass
class _Own:
    source: int
    text: str


def available(conn: wlclient.Connection) -> bool:
    return conn.offers(*MANAGERS) is not None and conn.offers(SEAT) is not None


class DataControlClipboard:
    def __init__(self, conn: wlclient.Connection) -> None:
        manager_name = conn.offers(*MANAGERS)
        manager = conn.bind(manager_name, BIND_VERSION_CAP) if manager_name else None
        seat = conn.bind(SEAT, 1) if conn.offers(SEAT) else None
        if manager is None or seat is None:
            raise ValueError("the compositor offers no data-control protocol")
        self._conn = conn
        self._manager = manager
        self._lock = threading.Lock()
        self._seq = 0
        self._offer = 0
        self._mimes: dict[int, list[str]] = {}
        self._own: _Own | None = None
        self._device = conn.new_id()
        conn.on(self._device, self._device_event)
        conn.send(manager, MGR_GET_DEVICE, self._device, seat)
        conn.roundtrip()

    # ---- Clipboard seam ---------------------------------------------------

    def get_text(self) -> str | None:
        with self._lock:
            if self._own is not None:
                return self._own.text
            offer, mimes = self._offer, self._mimes.get(self._offer, [])
        mime = next((m for m in TEXT_MIMES if m in mimes), None)
        if not offer or mime is None:
            return None
        read, write = os.pipe()
        try:
            self._conn.send(offer, OFFER_RECEIVE, mime, fds=(write,))
        except OSError:
            os.close(read)
            return None
        finally:
            os.close(write)
        data = wlclient.read_all(read, READ_TIMEOUT)
        return None if data is None else data.decode("utf-8", errors="replace")

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        source = self._conn.new_id()
        self._conn.on(source, lambda op, payload: self._source_event(source, op, payload))
        self._conn.send(self._manager, MGR_CREATE_SOURCE, source)
        for mime in TEXT_MIMES:
            self._conn.send(source, SRC_OFFER, mime)
        if exclude_from_history:
            self._conn.send(source, SRC_OFFER, HIDE_FROM_HISTORY)
        with self._lock:
            previous, self._own = self._own, _Own(source, text)
        self._conn.send(self._device, DEV_SET_SELECTION, source)
        if previous is not None:
            self._drop_source(previous.source)
        if not self._conn.roundtrip():
            raise OSError("the compositor did not take the selection")

    def sequence_number(self) -> int:
        with self._lock:
            return self._seq

    # ---- events -----------------------------------------------------------

    def _device_event(self, opcode: int, payload: bytes) -> None:
        (object_id,) = struct.unpack_from("<I", payload)
        if opcode == EV_DEV_DATA_OFFER:
            self._mimes[object_id] = []
            self._conn.on(object_id, lambda op, body: self._offer_event(object_id, op, body))
        elif opcode == EV_DEV_SELECTION:
            with self._lock:
                old, self._offer = self._offer, object_id
                self._seq += 1
            if old and old != object_id:
                self._drop_offer(old)
        elif opcode == EV_DEV_PRIMARY:
            if object_id:
                self._drop_offer(object_id)

    def _offer_event(self, offer: int, opcode: int, payload: bytes) -> None:
        if opcode == EV_OFFER_OFFER and offer in self._mimes:
            self._mimes[offer].append(wayland.unpack_string(payload, 0)[0])

    def _source_event(self, source: int, opcode: int, payload: bytes) -> None:
        if opcode == EV_SRC_SEND:
            mime = wayland.unpack_string(payload, 0)[0]
            fd = self._conn.take_fd()
            if fd is None:
                return
            with self._lock:
                own = self._own if self._own and self._own.source == source else None
            if own is None:
                os.close(fd)
            elif mime == HIDE_FROM_HISTORY:
                wlclient.write_all(fd, HIDE_VALUE)
            else:
                wlclient.write_all(fd, own.text.encode("utf-8"))
        elif opcode == EV_SRC_CANCELLED:
            with self._lock:
                if self._own and self._own.source == source:
                    self._own = None
            self._drop_source(source)

    def _drop_source(self, source: int) -> None:
        try:
            self._conn.send(source, SRC_DESTROY)
        except OSError:
            pass
        self._conn.forget(source)

    def _drop_offer(self, offer: int) -> None:
        self._mimes.pop(offer, None)
        try:
            self._conn.send(offer, OFFER_DESTROY)
        except OSError:
            pass
        self._conn.forget(offer)
