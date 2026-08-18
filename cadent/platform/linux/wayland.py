"""A small Wayland client for the four compositor protocols the Wayland tiers
speak, in pure Python (spec M6 §2.3, ADR 0007/0009).

The core protocol wire format is simple — an 8-byte header (object id, then
`size << 16 | opcode`) followed by 32-bit-aligned arguments; file
descriptors ride as `SCM_RIGHTS` ancillary data — and the interfaces Cadent
needs are few, so this speaks the socket directly rather than pulling in
libwayland (a native library the AppImage would then have to stage, and a
cffi build step for `pywayland`). Only `WaylandClient` knows the wire; the
protocol wrappers below hold the opcodes transcribed from the protocol XMLs:

- `zwp_virtual_keyboard_v1` — full-unicode typing on the wlroots family with
  a client-supplied xkb keymap, no dialog (spec §3).
- `ext-data-control-v1` (and its `zwlr` predecessor) — the clipboard without
  focus on KWin and wlroots; the `selection` event is the sequence counter.
- `org_kde_plasma_window_management` — the active window's `app_id` on KWin
  / SteamOS; `zwlr_foreign_toplevel_management_v1` — the same on wlroots.

Every wire behaviour here is a real-hardware item (spec §12): the CI leg
tests marshalling against a fake socket and the wrappers against scripted
events. One thread (`cadent-wayland`) reads the socket; wrappers' state
changes happen there and are read under a lock.
"""

from __future__ import annotations

import logging
import os
import select
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from .keysyms import CONTROL_L, SHIFT_L

log = logging.getLogger(__name__)

WL_DISPLAY = 1
_HEADER = struct.Struct("<II")
_SERVER_ID_BASE = 0xFF000000


class WaylandError(Exception):
    """No socket, a protocol error, or a global that never came."""


# ---- wire marshalling (pure) ---------------------------------------------------

def _pad(n: int) -> int:
    return (n + 3) & ~3


def marshal(object_id: int, opcode: int, signature: str, args: tuple) -> tuple[bytes, list[int]]:
    """Encode one request. `signature` uses libwayland's letters: i u f s o n
    a h (int, uint, fixed, string, object, new_id, array, fd); `?` prefix
    marks a nullable string/object; `N` is bind's untyped new_id
    (interface, version, id). Returns (bytes, fds)."""
    body = bytearray()
    fds: list[int] = []
    for arg, spec in zip(args, _iter_signature(signature), strict=True):
        kind = spec[-1]
        if kind in "iu":
            body += struct.pack("<i" if kind == "i" else "<I", int(arg))
        elif kind == "f":
            body += struct.pack("<i", int(round(float(arg) * 256)))
        elif kind == "s":
            if arg is None:
                body += struct.pack("<I", 0)
            else:
                raw = str(arg).encode("utf-8") + b"\0"
                body += struct.pack("<I", len(raw)) + raw + b"\0" * (_pad(len(raw)) - len(raw))
        elif kind in "on":
            body += struct.pack("<I", 0 if arg is None else int(arg))
        elif kind == "N":
            interface, version, new_id = arg
            raw = str(interface).encode("utf-8") + b"\0"
            body += struct.pack("<I", len(raw)) + raw + b"\0" * (_pad(len(raw)) - len(raw))
            body += struct.pack("<II", int(version), int(new_id))
        elif kind == "a":
            raw = bytes(arg)
            body += struct.pack("<I", len(raw)) + raw + b"\0" * (_pad(len(raw)) - len(raw))
        elif kind == "h":
            fds.append(int(arg))
        else:
            raise ValueError(f"unknown signature letter {kind!r}")
    size = _HEADER.size + len(body)
    return _HEADER.pack(object_id, (size << 16) | opcode) + bytes(body), fds


def _iter_signature(signature: str):
    spec = ""
    for ch in signature:
        if ch == "?":
            spec = "?"
            continue
        yield spec + ch
        spec = ""


def unmarshal_args(signature: str, body: bytes, fds: list[int]) -> tuple:
    """Decode an event body per `signature` (same letters; `h` pops an fd)."""
    out: list[Any] = []
    pos = 0
    for spec in _iter_signature(signature):
        kind = spec[-1]
        if kind in "iu":
            (value,) = struct.unpack_from("<i" if kind == "i" else "<I", body, pos)
            pos += 4
            out.append(value)
        elif kind == "f":
            (value,) = struct.unpack_from("<i", body, pos)
            pos += 4
            out.append(value / 256)
        elif kind == "s":
            (length,) = struct.unpack_from("<I", body, pos)
            pos += 4
            if length == 0:
                out.append(None)
            else:
                out.append(body[pos:pos + length - 1].decode("utf-8", errors="replace"))
                pos += _pad(length)
        elif kind in "on":
            (value,) = struct.unpack_from("<I", body, pos)
            pos += 4
            out.append(value or None)
        elif kind == "a":
            (length,) = struct.unpack_from("<I", body, pos)
            pos += 4
            out.append(bytes(body[pos:pos + length]))
            pos += _pad(length)
        elif kind == "h":
            out.append(fds.pop(0) if fds else None)
        else:
            raise ValueError(f"unknown signature letter {kind!r}")
    return tuple(out)


# ---- the client --------------------------------------------------------------

class WaylandObject:
    """One protocol object: id, interface, the event signatures by opcode
    and a handler the wrapper installs."""

    def __init__(self, client: WaylandClient, object_id: int, interface: str,
                 events: dict[int, tuple[str, str]]) -> None:
        self.client = client
        self.id = object_id
        self.interface = interface
        self.events = events           # opcode → (name, signature)
        self.handler: Callable[[str, tuple], None] | None = None

    def request(self, opcode: int, signature: str = "", *args) -> None:
        self.client.send(self.id, opcode, signature, args)


class WaylandClient:
    """The socket, the object table, the receive thread. Wrappers register
    objects with their event tables and get called back on `cadent-wayland`.
    Construct with `connect()`; `sock` is injectable for tests."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self._objects: dict[int, WaylandObject] = {}
        self._next_id = 2
        self._closed = False
        self.globals: dict[str, tuple[int, int]] = {}    # interface → (name, version)
        self._callbacks: dict[int, threading.Event] = {}
        self._lock = threading.RLock()
        self._pending_data: bytes = b""
        self._pending_fds: list[int] = []
        self._error: str | None = None
        display = WaylandObject(self, WL_DISPLAY, "wl_display",
                                {0: ("error", "ous"), 1: ("delete_id", "u")})
        display.handler = self._on_display_event
        self._objects[WL_DISPLAY] = display
        self._registry = self.new_object("wl_registry", {0: ("global", "usu"),
                                                         1: ("global_remove", "u")})
        self._registry.handler = self._on_registry_event
        display.request(1, "n", self._registry.id)          # get_registry
        self._thread = threading.Thread(target=self._loop, name="cadent-wayland",
                                        daemon=True)
        self._thread.start()

    @classmethod
    def connect(cls, env=None) -> WaylandClient:
        env = os.environ if env is None else env
        display = env.get("WAYLAND_DISPLAY") or "wayland-0"
        path = display if display.startswith("/") else \
            os.path.join(env.get("XDG_RUNTIME_DIR", ""), display)
        if not env.get("XDG_RUNTIME_DIR") and not display.startswith("/"):
            raise WaylandError("XDG_RUNTIME_DIR unset; no Wayland socket to open")
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
        except OSError as exc:
            raise WaylandError(f"cannot connect to Wayland at {path}: {exc}") from exc
        client = cls(sock)
        client.roundtrip(timeout=3.0)          # the initial globals burst
        return client

    # ---- objects ------------------------------------------------------------

    def new_object(self, interface: str, events: dict[int, tuple[str, str]]) -> WaylandObject:
        with self._lock:
            object_id = self._next_id
            self._next_id += 1
            obj = WaylandObject(self, object_id, interface, events)
            self._objects[object_id] = obj
            return obj

    def adopt_server_object(self, object_id: int, interface: str,
                            events: dict[int, tuple[str, str]]) -> WaylandObject:
        """A server-allocated new_id (≥ 0xff000000) named in an event."""
        with self._lock:
            obj = WaylandObject(self, object_id, interface, events)
            self._objects[object_id] = obj
            return obj

    def forget(self, object_id: int) -> None:
        with self._lock:
            self._objects.pop(object_id, None)

    def bind(self, interface: str, events: dict[int, tuple[str, str]],
             max_version: int) -> WaylandObject | None:
        """Bind a global, or None when the compositor does not offer it."""
        with self._lock:
            entry = self.globals.get(interface)
            if entry is None:
                return None
            name, version = entry
            obj = self.new_object(interface, events)
        self._registry.request(0, "uN", name, (interface, min(version, max_version), obj.id))
        return obj

    def has_global(self, interface: str) -> bool:
        with self._lock:
            return interface in self.globals

    # ---- sending ------------------------------------------------------------

    def send(self, object_id: int, opcode: int, signature: str, args: tuple) -> None:
        data, fds = marshal(object_id, opcode, signature, args)
        with self._send_lock:
            if self._closed:
                raise WaylandError("Wayland connection closed")
            try:
                if fds:
                    socket.send_fds(self._sock, [data], fds)
                else:
                    self._sock.sendall(data)
            except OSError as exc:
                raise WaylandError(f"Wayland send failed: {exc}") from exc

    def roundtrip(self, timeout: float = 2.0) -> None:
        """`wl_display.sync`: returns once the compositor has processed
        everything sent so far (and its events reached us)."""
        done = threading.Event()
        cb = self.new_object("wl_callback", {0: ("done", "u")})
        with self._lock:
            self._callbacks[cb.id] = done
        cb.handler = lambda _name, _args: done.set()
        self._objects[WL_DISPLAY].request(0, "n", cb.id)      # sync
        if not done.wait(timeout):
            raise WaylandError("Wayland roundtrip timed out")
        if self._error:
            raise WaylandError(self._error)

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    # ---- receiving ----------------------------------------------------------

    def _loop(self) -> None:
        while not self._closed:
            try:
                readable, _, _ = select.select([self._sock], [], [], 0.5)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            try:
                data, fds, _flags, _addr = socket.recv_fds(self._sock, 65536, 16)
            except OSError:
                if not self._closed:
                    log.warning("Wayland connection lost", exc_info=True)
                self._closed = True
                return
            if not data:
                self._closed = True
                return
            self.feed(data, fds)

    def feed(self, data: bytes, fds: list[int]) -> None:
        """Parse whatever arrived (possibly partial messages) and dispatch."""
        self._pending_data += data
        self._pending_fds.extend(fds)
        while len(self._pending_data) >= _HEADER.size:
            object_id, size_op = _HEADER.unpack_from(self._pending_data, 0)
            size, opcode = size_op >> 16, size_op & 0xFFFF
            if len(self._pending_data) < size:
                return
            body = self._pending_data[_HEADER.size:size]
            self._pending_data = self._pending_data[size:]
            self._dispatch(object_id, opcode, body)

    def _dispatch(self, object_id: int, opcode: int, body: bytes) -> None:
        with self._lock:
            obj = self._objects.get(object_id)
        if obj is None:
            return
        spec = obj.events.get(opcode)
        if spec is None:
            return
        name, signature = spec
        try:
            args = unmarshal_args(signature, body, self._pending_fds)
        except Exception:
            log.debug("bad Wayland event %s.%s", obj.interface, name, exc_info=True)
            return
        if obj.handler is not None:
            try:
                obj.handler(name, args)
            except Exception:
                log.exception("Wayland event handler %s.%s failed", obj.interface, name)

    def _on_display_event(self, name: str, args: tuple) -> None:
        if name == "error":
            self._error = f"Wayland protocol error on object {args[0]}: {args[2]}"
            log.error(self._error)
        elif name == "delete_id":
            self.forget(args[0])

    def _on_registry_event(self, name: str, args: tuple) -> None:
        with self._lock:
            if name == "global":
                gname, interface, version = args
                self.globals[interface] = (gname, version)
            elif name == "global_remove":
                for iface, (gname, _v) in list(self.globals.items()):
                    if gname == args[0]:
                        del self.globals[iface]


# ---- wl_seat -----------------------------------------------------------------

def bind_seat(client: WaylandClient) -> WaylandObject | None:
    return client.bind("wl_seat", {0: ("capabilities", "u"), 1: ("name", "s")}, 7)


# ---- zwp_virtual_keyboard_v1 --------------------------------------------------

_XKB_NAMED = {0xFF0D: "Return", 0xFF09: "Tab", 0xFF08: "BackSpace",
              0xFF1B: "Escape", 0x20: "space", CONTROL_L: "Control_L",
              SHIFT_L: "Shift_L"}


def keysym_name(keysym: int) -> str:
    """A name xkbcommon's keymap parser accepts: the few named keys, else
    the `Uxxxx` form (which it normalizes to the Latin-1 keysym where one
    exists)."""
    if keysym in _XKB_NAMED:
        return _XKB_NAMED[keysym]
    if keysym >= 0x01000000:
        return f"U{keysym - 0x01000000:04X}"
    if 0x20 <= keysym <= 0xFF:
        return f"U{keysym:04X}"
    return f"0x{keysym:x}"


def build_keymap(keysyms: list[int]) -> tuple[str, dict[int, int]]:
    """An xkb keymap mapping evdev keycodes 1.. to the given keysyms — the
    trick wtype uses: the client authors its own layout, so any code point
    is one key press away. Returns (keymap text, keysym → evdev keycode)."""
    codes: dict[int, int] = {}
    lines_codes = []
    lines_syms = []
    for n, keysym in enumerate(dict.fromkeys(keysyms), start=1):
        codes[keysym] = n
        lines_codes.append(f"    <K{n}> = {n + 8};")
        lines_syms.append(f"    key <K{n}> {{[{keysym_name(keysym)}]}};")
    text = ("xkb_keymap {\n"
            "xkb_keycodes \"(unnamed)\" {\n"
            "    minimum = 8;\n"
            f"    maximum = {len(codes) + 8 + 1};\n"
            + "\n".join(lines_codes) + "\n"
            "};\n"
            "xkb_types \"(unnamed)\" { include \"complete\" };\n"
            "xkb_compatibility \"(unnamed)\" { include \"complete\" };\n"
            "xkb_symbols \"(unnamed)\" {\n"
            + "\n".join(lines_syms) + "\n"
            "};\n"
            "};\n")
    return text, codes


MAX_KEYMAP_KEYS = 240      # evdev keycodes 9..248 in one keymap
XKB_MOD_SHIFT, XKB_MOD_CONTROL = 1, 4


class VirtualKeyboard:
    """`zwp_virtual_keyboard_v1` on the seat: keymap per text batch, then
    press/release per character. No dialog, no permission beyond the global
    being offered (wlroots family only)."""

    MANAGER = "zwp_virtual_keyboard_manager_v1"

    def __init__(self, client: WaylandClient, seat: WaylandObject) -> None:
        self._client = client
        self._manager = client.bind(self.MANAGER, {}, 1)
        if self._manager is None:
            raise WaylandError("no zwp_virtual_keyboard_manager_v1")
        self._kbd = client.new_object("zwp_virtual_keyboard_v1", {})
        self._manager.request(0, "on", seat.id, self._kbd.id)   # create_virtual_keyboard
        self._lock = threading.Lock()
        self._t0 = time.monotonic()

    def _now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000) & 0xFFFFFFFF

    def _send_keymap(self, keysyms: list[int]) -> dict[int, int]:
        text, codes = build_keymap(keysyms)
        raw = text.encode("utf-8") + b"\0"
        fd = os.memfd_create("cadent-keymap") if hasattr(os, "memfd_create") \
            else _tmp_fd()
        try:
            os.write(fd, raw)
            os.lseek(fd, 0, os.SEEK_SET)
            self._kbd.request(0, "uhu", 1, fd, len(raw))     # keymap(xkb_v1)
        finally:
            os.close(fd)
        self._client.roundtrip()
        return codes

    def _key(self, code: int, pressed: bool) -> None:
        self._kbd.request(1, "uuu", self._now_ms(), code, 1 if pressed else 0)

    def _modifiers(self, depressed: int) -> None:
        self._kbd.request(2, "uuuu", depressed, 0, 0, 0)

    def type_keysyms(self, keysyms: list[int]) -> None:
        with self._lock:
            for start in range(0, len(keysyms), MAX_KEYMAP_KEYS):
                batch = keysyms[start:start + MAX_KEYMAP_KEYS]
                codes = self._send_keymap(batch)
                for keysym in batch:
                    code = codes[keysym]
                    self._key(code, True)
                    self._key(code, False)
                self._client.roundtrip()

    def chord(self, keysyms: list[int]) -> None:
        """Press in order, release in reverse, with the xkb modifier mask
        for Control/Shift sent alongside (compositors read either)."""
        with self._lock:
            codes = self._send_keymap(keysyms)
            mask = 0
            for keysym in keysyms:
                if keysym == CONTROL_L:
                    mask |= XKB_MOD_CONTROL
                elif keysym == SHIFT_L:
                    mask |= XKB_MOD_SHIFT
                if mask:
                    self._modifiers(mask)
                self._key(codes[keysym], True)
            for keysym in reversed(keysyms):
                self._key(codes[keysym], False)
            self._modifiers(0)
            self._client.roundtrip()


def _tmp_fd() -> int:
    import tempfile

    fd, path = tempfile.mkstemp(prefix="cadent-keymap-")
    os.unlink(path)
    return fd


# ---- ext-data-control-v1 / zwlr-data-control-v1 --------------------------------

_DC_DEVICE_EVENTS = {0: ("data_offer", "n"), 1: ("selection", "?o"), 2: ("finished", ""),
                     3: ("primary_selection", "?o")}
_DC_SOURCE_EVENTS = {0: ("send", "sh"), 1: ("cancelled", "")}
_DC_OFFER_EVENTS = {0: ("offer", "s")}
TEXT_MIMES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "TEXT", "STRING")
KDE_HINT_MIME = "x-kde-passwordManagerHint"


class DataControl:
    """The clipboard without focus. `set_text` creates a data source
    offering the text mimes (plus Klipper's hint when history exclusion is
    asked) and makes it the selection; the receive thread answers `send`
    by writing into the fd. Every `selection` event — ours or anyone's —
    bumps `sequence`. `get_text` receives from the current offer through a
    pipe."""

    MANAGERS = ("ext_data_control_manager_v1", "zwlr_data_control_manager_v1")

    def __init__(self, client: WaylandClient, seat: WaylandObject) -> None:
        self._client = client
        self._manager = None
        for name in self.MANAGERS:
            self._manager = client.bind(name, {}, 1)
            if self._manager is not None:
                self._prefix = name.rsplit("_manager", 1)[0]
                break
        if self._manager is None:
            raise WaylandError("no data-control manager")
        self._device = client.new_object(f"{self._prefix}_device_v1", _DC_DEVICE_EVENTS)
        self._device.handler = self._on_device
        self._manager.request(1, "no", self._device.id, seat.id)      # get_data_device
        self._lock = threading.Lock()
        self._offers: dict[int, tuple[WaylandObject, list[str]]] = {}
        self._current_offer: WaylandObject | None = None
        self._source: WaylandObject | None = None
        self._text: str | None = None
        self._exclude = False
        self.sequence = 0

    # ---- events (receive thread) --------------------------------------------

    def _on_device(self, name: str, args: tuple) -> None:
        if name == "data_offer":
            offer = self._client.adopt_server_object(args[0], f"{self._prefix}_offer_v1",
                                                     _DC_OFFER_EVENTS)
            mimes: list[str] = []
            offer.handler = lambda n, a, m=mimes: m.append(a[0]) if n == "offer" else None
            with self._lock:
                self._offers[args[0]] = (offer, mimes)
        elif name == "selection":
            with self._lock:
                self.sequence += 1
                previous = self._current_offer
                self._current_offer = self._offers.get(args[0], (None,))[0] if args[0] else None
                if previous is not None and previous is not self._current_offer:
                    self._offers.pop(previous.id, None)
                    previous.request(1)            # destroy the stale offer
                    self._client.forget(previous.id)
        elif name == "finished":
            log.warning("data-control device finished; clipboard seam is dead for this run")

    def _on_source(self, name: str, args: tuple) -> None:
        if name == "send":
            mime, fd = args
            payload = b""
            if mime == KDE_HINT_MIME:
                payload = b"secret"
            elif self._text is not None:
                payload = self._text.encode("utf-8", errors="replace")
            try:
                if fd is not None:
                    _write_all(fd, payload)
            finally:
                if fd is not None:
                    os.close(fd)
        elif name == "cancelled":
            with self._lock:
                if self._source is not None:
                    self._source.request(1)         # destroy
                    self._client.forget(self._source.id)
                    self._source = None
                    self._text = None

    # ---- the seam -----------------------------------------------------------

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        source = self._client.new_object(f"{self._prefix}_source_v1", _DC_SOURCE_EVENTS)
        source.handler = self._on_source
        self._manager.request(0, "n", source.id)                   # create_data_source
        for mime in TEXT_MIMES:
            source.request(0, "s", mime)                           # offer
        if exclude_from_history:
            source.request(0, "s", KDE_HINT_MIME)
        with self._lock:
            old = self._source
            self._source, self._text, self._exclude = source, text, exclude_from_history
        self._device.request(0, "?o", source.id)                   # set_selection
        self._client.roundtrip()
        if old is not None:
            old.request(1)
            self._client.forget(old.id)

    def get_text(self) -> str | None:
        with self._lock:
            if self._source is not None and self._text is not None:
                return self._text                  # we own it
            offer = self._current_offer
            mimes = self._offers.get(offer.id, (None, []))[1] if offer else []
        if offer is None:
            return None
        mime = next((m for m in TEXT_MIMES if m in mimes), None)
        if mime is None:
            return None
        rfd, wfd = os.pipe()
        try:
            offer.request(0, "sh", mime, wfd)                      # receive
            os.close(wfd)
            self._client.roundtrip()
            chunks = []
            deadline = time.monotonic() + 1.0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                readable, _, _ = select.select([rfd], [], [], remaining)
                if not readable:
                    return None
                chunk = os.read(rfd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(rfd)
        return b"".join(chunks).decode("utf-8", errors="replace")


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            n = os.write(fd, view)
        except BlockingIOError:
            select.select([], [fd], [], 1.0)
            continue
        view = view[n:]


# ---- toplevel identity: plasma-window-management / wlr-foreign-toplevel ---------

class PlasmaWindows:
    """`org_kde_plasma_window_management`: tracks every window's app_id, pid
    and active flag; `active()` is what `FocusedApp.name()` reads."""

    INTERFACE = "org_kde_plasma_window_management"
    _MGR_EVENTS = {0: ("show_desktop_changed", "u"), 1: ("window", "u"),
                   2: ("stacking_order_changed", "a"), 3: ("stacking_order_uuid_changed", "s"),
                   4: ("window_with_uuid", "us")}
    _WIN_EVENTS = {0: ("title_changed", "s"), 1: ("app_id_changed", "s"),
                   2: ("state_changed", "u"), 3: ("virtual_desktop_changed", "i"),
                   4: ("themed_icon_name_changed", "s"), 5: ("unmapped", ""),
                   6: ("initial_state", ""), 7: ("parent_window", "?o"),
                   8: ("geometry", "iiuu"), 9: ("icon_changed", ""), 10: ("pid_changed", "u"),
                   17: ("client_geometry", "iiuu")}
    STATE_ACTIVE = 1 << 0

    def __init__(self, client: WaylandClient) -> None:
        self._client = client
        # ≤ 16: the `window` event (get_window by internal id) rather than the
        # uuid pair introduced later — one path to handle.
        self._manager = client.bind(self.INTERFACE, self._MGR_EVENTS, 16)
        if self._manager is None:
            raise WaylandError("no org_kde_plasma_window_management")
        self._manager.handler = self._on_manager
        self._lock = threading.Lock()
        self._windows: dict[int, dict] = {}       # object id → {app_id, pid, active, rect}

    def _on_manager(self, name: str, args: tuple) -> None:
        if name in ("window", "window_with_uuid"):
            win = self._client.new_object("org_kde_plasma_window", self._WIN_EVENTS)
            win.handler = lambda n, a, w=win: self._on_window(w, n, a)
            with self._lock:
                self._windows[win.id] = {"app_id": None, "pid": None, "active": False,
                                         "rect": None}
            if name == "window":
                self._manager.request(1, "nu", win.id, args[0])         # get_window
            else:
                self._manager.request(2, "ns", win.id, args[1])         # get_window_by_uuid

    def _on_window(self, win: WaylandObject, name: str, args: tuple) -> None:
        with self._lock:
            entry = self._windows.get(win.id)
            if entry is None:
                return
            if name == "app_id_changed":
                entry["app_id"] = args[0]
            elif name == "pid_changed":
                entry["pid"] = args[0]
            elif name == "state_changed":
                entry["active"] = bool(args[0] & self.STATE_ACTIVE)
            elif name == "geometry":
                x, y, w, h = args
                entry["rect"] = (x, y, x + w, y + h)
            elif name == "unmapped":
                self._windows.pop(win.id, None)
                win.request(7)                    # destroy
                self._client.forget(win.id)

    def active(self) -> dict | None:
        with self._lock:
            for entry in self._windows.values():
                if entry["active"]:
                    return dict(entry)
        return None


class WlrToplevels:
    """`zwlr_foreign_toplevel_manager_v1`: app_id + activated per toplevel
    on the wlroots family. No geometry — the overlay is off on Portal anyway."""

    INTERFACE = "zwlr_foreign_toplevel_manager_v1"
    _MGR_EVENTS = {0: ("toplevel", "n"), 1: ("finished", "")}
    _HANDLE_EVENTS = {0: ("title", "s"), 1: ("app_id", "s"), 2: ("output_enter", "o"),
                      3: ("output_leave", "o"), 4: ("state", "a"), 5: ("done", ""),
                      6: ("closed", ""), 7: ("parent", "?o")}
    STATE_ACTIVATED = 2

    def __init__(self, client: WaylandClient) -> None:
        self._client = client
        self._manager = client.bind(self.INTERFACE, self._MGR_EVENTS, 3)
        if self._manager is None:
            raise WaylandError("no zwlr_foreign_toplevel_manager_v1")
        self._manager.handler = self._on_manager
        self._lock = threading.Lock()
        self._toplevels: dict[int, dict] = {}

    def _on_manager(self, name: str, args: tuple) -> None:
        if name == "toplevel":
            handle = self._client.adopt_server_object(
                args[0], "zwlr_foreign_toplevel_handle_v1", self._HANDLE_EVENTS)
            handle.handler = lambda n, a, h=handle: self._on_handle(h, n, a)
            with self._lock:
                self._toplevels[handle.id] = {"app_id": None, "active": False, "pid": None,
                                              "rect": None}

    def _on_handle(self, handle: WaylandObject, name: str, args: tuple) -> None:
        with self._lock:
            entry = self._toplevels.get(handle.id)
            if entry is None:
                return
            if name == "app_id":
                entry["app_id"] = args[0]
            elif name == "state":
                states = struct.unpack(f"<{len(args[0]) // 4}I", args[0])
                entry["active"] = self.STATE_ACTIVATED in states
            elif name == "closed":
                self._toplevels.pop(handle.id, None)
                handle.request(7)                 # destroy
                self._client.forget(handle.id)

    def active(self) -> dict | None:
        with self._lock:
            for entry in self._toplevels.values():
                if entry["active"]:
                    return dict(entry)
        return None
