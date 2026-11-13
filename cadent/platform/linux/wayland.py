"""The focused window's app_id, from KWin's `org_kde_plasma_window_management`
(spec §2.3, §5; ADR 0009).

Wayland gives a background client no portal for "which window is focused", so
Plasma and SteamOS desktop are asked over the compositor's own toplevel
protocol. This is the slice of the wire format that takes: object ids, the
header, uint / string / new_id arguments — no file descriptors, no shared
memory — so it is written out here rather than adding a Wayland binding for
one interface.

KWin only offers this global to clients whose desktop file declares
`X-KDE-Wayland-Interfaces=org_kde_plasma_window_management`. Where the global
is missing the run simply has no per-app identity (`connect` returns None).

Nothing here imports the GUI toolkit.
"""

from __future__ import annotations

import logging
import os
import socket
import struct
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

INTERFACE = "org_kde_plasma_window_management"
BIND_VERSION_CAP = 10           # `window` + `get_window`, before the uuid-only API
SYNC_TIMEOUT = 2.0              # seconds to wait for the registry round-trip

# wl_display / wl_registry / wl_callback
DISPLAY = 1
DISPLAY_SYNC, DISPLAY_GET_REGISTRY = 0, 1
REGISTRY_BIND = 0
EV_DISPLAY_ERROR, EV_DISPLAY_DELETE_ID = 0, 1
EV_REGISTRY_GLOBAL = 0
EV_CALLBACK_DONE = 0
# org_kde_plasma_window_management
MGR_GET_WINDOW = 1
EV_MGR_WINDOW = 1
# org_kde_plasma_window
EV_WIN_APP_ID_CHANGED, EV_WIN_STATE_CHANGED, EV_WIN_UNMAPPED = 1, 2, 5
STATE_ACTIVE = 1


def pack(object_id: int, opcode: int, *args: int | str) -> bytes:
    """One request: header, then uints as-is and strings length-prefixed,
    NUL-terminated and padded to four bytes."""
    body = b""
    for arg in args:
        if isinstance(arg, str):
            raw = arg.encode() + b"\0"
            body += struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)
        else:
            body += struct.pack("<I", arg)
    return struct.pack("<II", object_id, ((8 + len(body)) << 16) | opcode) + body


def unpack_string(payload: bytes, offset: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<I", payload, offset)
    start = offset + 4
    text = payload[start:start + max(length - 1, 0)].decode(errors="replace")
    return text, start + length + (-length % 4)


class Messages:
    """Cuts a byte stream into (object id, opcode, payload) events."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        self._buffer += data
        out = []
        while len(self._buffer) >= 8:
            object_id, word = struct.unpack_from("<II", self._buffer)
            size = word >> 16
            if size < 8 or len(self._buffer) < size:
                break
            out.append((object_id, word & 0xFFFF, self._buffer[8:size]))
            self._buffer = self._buffer[size:]
        return out


class PlasmaWindows:
    """Tracks which window is active and what app_id it carries. Events are
    processed on the reader thread; `focused_app_id` is safe from any thread."""

    def __init__(self, send: Callable[[bytes], None]) -> None:
        self._send = send
        self._lock = threading.Lock()
        self._next_id = DISPLAY + 1
        self._registry = self._new_id()
        self._sync = self._new_id()
        self._manager: int | None = None
        self._manager_version = 0
        self._globals: dict[str, tuple[int, int]] = {}
        self._app_ids: dict[int, str] = {}
        self._active: set[int] = set()
        self._synced = threading.Event()
        self._messages = Messages()

    def _new_id(self) -> int:
        object_id, self._next_id = self._next_id, self._next_id + 1
        return object_id

    def hello(self) -> None:
        self._send(pack(DISPLAY, DISPLAY_GET_REGISTRY, self._registry)
                   + pack(DISPLAY, DISPLAY_SYNC, self._sync))

    @property
    def offered(self) -> bool:
        """Did the compositor list the protocol? Meaningful once synced."""
        return INTERFACE in self._globals

    def wait_synced(self, timeout: float) -> bool:
        return self._synced.wait(timeout)

    def feed(self, data: bytes) -> None:
        for object_id, opcode, payload in self._messages.feed(data):
            self._dispatch(object_id, opcode, payload)

    def _dispatch(self, object_id: int, opcode: int, payload: bytes) -> None:
        if object_id == DISPLAY and opcode == EV_DISPLAY_ERROR:
            log.warning("wayland protocol error: %r", payload)
        elif object_id == self._registry and opcode == EV_REGISTRY_GLOBAL:
            (name,) = struct.unpack_from("<I", payload)
            interface, offset = unpack_string(payload, 4)
            (version,) = struct.unpack_from("<I", payload, offset)
            self._globals[interface] = (name, version)
        elif object_id == self._sync and opcode == EV_CALLBACK_DONE:
            self._bind_manager()
            self._synced.set()
        elif object_id == self._manager and opcode == EV_MGR_WINDOW:
            (internal_id,) = struct.unpack_from("<I", payload)
            window = self._new_id()
            self._send(pack(self._manager, MGR_GET_WINDOW, window, internal_id))
            with self._lock:
                self._app_ids[window] = ""
        elif object_id in self._app_ids:
            self._window_event(object_id, opcode, payload)

    def _bind_manager(self) -> None:
        if not self.offered:
            return
        name, version = self._globals[INTERFACE]
        self._manager = self._new_id()
        self._manager_version = min(version, BIND_VERSION_CAP)
        self._send(pack(self._registry, REGISTRY_BIND, name, INTERFACE,
                        self._manager_version, self._manager))

    def _window_event(self, window: int, opcode: int, payload: bytes) -> None:
        with self._lock:
            if opcode == EV_WIN_APP_ID_CHANGED:
                self._app_ids[window] = unpack_string(payload, 0)[0]
            elif opcode == EV_WIN_STATE_CHANGED:
                (flags,) = struct.unpack_from("<I", payload)
                (self._active.add if flags & STATE_ACTIVE
                 else self._active.discard)(window)
            elif opcode == EV_WIN_UNMAPPED:
                self._app_ids.pop(window, None)
                self._active.discard(window)

    @property
    def focused_app_id(self) -> str | None:
        with self._lock:
            for window in self._active:
                if self._app_ids.get(window):
                    return self._app_ids[window]
        return None


def socket_path(env: dict[str, str]) -> str | None:
    display = env.get("WAYLAND_DISPLAY")
    if not display:
        return None
    if os.path.isabs(display):
        return display
    runtime = env.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime, display) if runtime else None


def connect(env: dict[str, str] | None = None) -> PlasmaWindows | None:
    """A live tracker, or None where the compositor does not offer the
    protocol (or there is no compositor to ask)."""
    path = socket_path(dict(os.environ if env is None else env))
    if path is None:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(SYNC_TIMEOUT)
        sock.connect(path)
        windows = PlasmaWindows(sock.sendall)
        windows.hello()
        while not windows.wait_synced(0):
            windows.feed(_recv(sock))
    except (OSError, ConnectionError) as exc:
        log.info("no wayland window tracking: %s", exc)
        sock.close()
        return None
    if not windows.offered:
        sock.close()
        return None
    sock.settimeout(None)
    threading.Thread(target=_read_forever, args=(sock, windows),
                     name="cadent-wayland", daemon=True).start()
    return windows


def _recv(sock: socket.socket) -> bytes:
    data = sock.recv(65536)
    if not data:
        raise ConnectionError("the compositor closed the connection")
    return data


def _read_forever(sock: socket.socket, windows: PlasmaWindows) -> None:
    try:
        while True:
            windows.feed(_recv(sock))
    except OSError:
        log.info("wayland connection ended; focused app is now unknown")
