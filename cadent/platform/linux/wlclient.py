"""A small Wayland client connection that can pass file descriptors (spec §2.3,
§3; ADR 0007).

`wayland.py` tracks windows over a connection that never moves an fd. Paste and
typing on the compositors that offer them natively do: a clipboard transfer is
a pipe handed to the other client, a keymap is a memfd handed to the compositor.
This is the slice of the wire format that needs — requests with uint / string /
fd arguments, events dispatched per object on one reader thread, a roundtrip —
written out here for the same reason `wayland.py` is: no Wayland binding for
two small interfaces.

Handlers run on the reader thread; callers on other threads only ever `send`
and `roundtrip`. Nothing here imports the GUI toolkit.
"""

from __future__ import annotations

import array
import collections
import logging
import os
import select
import socket
import struct
import threading
import time
from collections.abc import Callable

from . import wayland

log = logging.getLogger(__name__)

SYNC_TIMEOUT = 2.0
MAX_FDS = 8

Handler = Callable[[int, bytes], None]      # (opcode, payload)


class Connection:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._next_id = wayland.DISPLAY + 1
        self._handlers: dict[int, Handler] = {}
        self._fds: collections.deque[int] = collections.deque()
        self._messages = wayland.Messages()
        self._pending: dict[int, threading.Event] = {}
        self.globals: dict[str, tuple[int, int]] = {}
        self.alive = True
        self._registry = self.new_id()
        self.on(wayland.DISPLAY, self._display_event)
        self.on(self._registry, self._registry_event)

    # ---- ids and requests -----------------------------------------------

    def new_id(self) -> int:
        with self._id_lock:
            object_id, self._next_id = self._next_id, self._next_id + 1
        return object_id

    def on(self, object_id: int, handler: Handler) -> None:
        self._handlers[object_id] = handler

    def forget(self, object_id: int) -> None:
        self._handlers.pop(object_id, None)

    def send(self, object_id: int, opcode: int, *args: int | str,
             fds: tuple[int, ...] = ()) -> None:
        """One request. Raises OSError when the compositor is gone."""
        data = wayland.pack(object_id, opcode, *args)
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                      array.array("i", fds))] if fds else []
        with self._send_lock:
            if not self.alive:
                raise ConnectionError("the compositor connection is closed")
            self._sock.sendmsg([data], ancillary)

    def take_fd(self) -> int | None:
        """The next descriptor that arrived with an event, oldest first."""
        try:
            return self._fds.popleft()
        except IndexError:
            return None

    # ---- the registry ---------------------------------------------------

    def hello(self) -> None:
        self.send(wayland.DISPLAY, wayland.DISPLAY_GET_REGISTRY, self._registry)

    def offers(self, *interfaces: str) -> str | None:
        """The first of `interfaces` the compositor lists, or None."""
        for interface in interfaces:
            if interface in self.globals:
                return interface
        return None

    def bind(self, interface: str, version_cap: int) -> int | None:
        if interface not in self.globals:
            return None
        name, version = self.globals[interface]
        bound = min(version, version_cap)
        object_id = self.new_id()
        self.send(self._registry, wayland.REGISTRY_BIND, name, interface,
                  bound, object_id)
        return object_id

    def roundtrip(self, timeout: float = SYNC_TIMEOUT) -> bool:
        """Everything sent so far has been handled by the compositor, and what
        it answered has been dispatched, when this returns True."""
        callback = self.new_id()
        done = threading.Event()
        self._pending[callback] = done
        self.on(callback, lambda _op, _payload: done.set())
        try:
            self.send(wayland.DISPLAY, wayland.DISPLAY_SYNC, callback)
        except OSError:
            return False
        finished = done.wait(timeout)
        self._pending.pop(callback, None)
        self.forget(callback)
        return finished

    # ---- the reader -----------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._read_forever, name="cadent-wayland-io",
                         daemon=True).start()

    def close(self) -> None:
        self.alive = False
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
            self._sock.close()
        except OSError:
            pass

    def _read_forever(self) -> None:
        try:
            while True:
                data, ancillary, _flags, _addr = self._sock.recvmsg(
                    65536, socket.CMSG_SPACE(MAX_FDS * 4))
                if not data:
                    break
                for level, kind, payload in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        fds = array.array("i")
                        fds.frombytes(payload[:len(payload) - len(payload) % 4])
                        self._fds.extend(fds)
                for object_id, opcode, body in self._messages.feed(data):
                    handler = self._handlers.get(object_id)
                    if handler is not None:
                        self._dispatch(handler, opcode, body)
        except OSError:
            pass
        finally:
            self.alive = False
            for event in list(self._pending.values()):
                event.set()
            log.info("wayland connection ended")

    @staticmethod
    def _dispatch(handler: Handler, opcode: int, body: bytes) -> None:
        try:
            handler(opcode, body)
        except Exception:
            log.exception("wayland event handler failed")

    def _display_event(self, opcode: int, payload: bytes) -> None:
        if opcode == wayland.EV_DISPLAY_ERROR:
            log.warning("wayland protocol error: %r", payload)

    def _registry_event(self, opcode: int, payload: bytes) -> None:
        if opcode != wayland.EV_REGISTRY_GLOBAL:
            return
        (name,) = struct.unpack_from("<I", payload)
        interface, offset = wayland.unpack_string(payload, 4)
        (version,) = struct.unpack_from("<I", payload, offset)
        self.globals[interface] = (name, version)


def connect(env: dict[str, str] | None = None) -> Connection | None:
    """A synced connection with the registry listed, or None with no
    compositor to talk to."""
    path = wayland.socket_path(dict(os.environ if env is None else env))
    if path is None:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
    except OSError as exc:
        log.info("no wayland connection: %s", exc)
        sock.close()
        return None
    conn = Connection(sock)
    conn.start()
    try:
        conn.hello()
    except OSError:
        conn.close()
        return None
    if not conn.roundtrip():
        conn.close()
        return None
    return conn


def write_all(fd: int, data: bytes) -> None:
    """Write a transfer to a pipe another client reads, then close it. Off the
    reader thread: a slow reader must not stall every other event."""
    def run() -> None:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        except OSError:
            pass
        finally:
            os.close(fd)

    threading.Thread(target=run, name="cadent-wayland-write", daemon=True).start()


def read_all(fd: int, timeout: float, limit: int = 1 << 20) -> bytes | None:
    """Everything a pipe carries until EOF; None when it stalls past `timeout`.
    Closes `fd`."""
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + timeout
    try:
        while size < limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)
    return b"".join(chunks)

