"""A small Wayland client, enough for the three privileged protocols the
Portal tier rides where a compositor offers them (ADR 0007, ADR 0009):
`ext-data-control-v1` (the clipboard without focus), `zwp_virtual_keyboard_v1`
(typing with our own keymap, wlroots only) and the two toplevel-listing
protocols that name the focused app (`plasma-window-management` on KWin,
`wlr-foreign-toplevel-management` on wlroots).

Pure Python over the Unix socket — no libwayland, no scanner output, no
native extension in the bundle. The wire format is small: every message is
`object id, (size << 16 | opcode)` and then arguments padded to four bytes;
file descriptors ride in `SCM_RIGHTS` ancillary data. Interfaces are
described as plain tables below; unknown events are skipped by size, so a
newer compositor version cannot desynchronise the stream.

One reader thread (`cadent-wayland`) dispatches events to the object they
belong to. Callbacks run on it; callers marshal, as everywhere on the seam.
"""

from __future__ import annotations

import array
import logging
import os
import socket
import struct
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

# Argument codes: i int32, u uint32, f fixed, s string, o object, n new_id,
# a array, h fd. wl_registry.bind's new_id is the special (s u n) triple.
INTERFACES: dict[str, dict] = {
    "wl_display": {"requests": {"sync": (0, "n"), "get_registry": (1, "n")},
                   "events": {0: ("error", "ous"), 1: ("delete_id", "u")}},
    "wl_registry": {"requests": {"bind": (0, "usun")},
                    "events": {0: ("global", "usu"), 1: ("global_remove", "u")}},
    "wl_callback": {"requests": {}, "events": {0: ("done", "u")}},
    "wl_seat": {"requests": {}, "events": {0: ("capabilities", "u"), 1: ("name", "s")}},
    "ext_data_control_manager_v1": {
        "requests": {"create_data_source": (0, "n"), "get_data_device": (1, "no"),
                     "destroy": (2, "")}, "events": {}},
    "ext_data_control_device_v1": {
        "requests": {"set_selection": (0, "o"), "destroy": (1, "")},
        "events": {0: ("data_offer", "n"), 1: ("selection", "o"), 2: ("finished", ""),
                   3: ("primary_selection", "o")}},
    "ext_data_control_source_v1": {
        "requests": {"offer": (0, "s"), "destroy": (1, "")},
        "events": {0: ("send", "sh"), 1: ("cancelled", "")}},
    "ext_data_control_offer_v1": {
        "requests": {"receive": (0, "sh"), "destroy": (1, "")},
        "events": {0: ("offer", "s")}},
    "zwp_virtual_keyboard_manager_v1": {
        "requests": {"create_virtual_keyboard": (0, "on")}, "events": {}},
    "zwp_virtual_keyboard_v1": {
        "requests": {"keymap": (0, "uhu"), "key": (1, "uuu"), "modifiers": (2, "uuuu"),
                     "destroy": (3, "")}, "events": {}},
    "org_kde_plasma_window_management": {
        "requests": {"get_window": (1, "nu"), "get_window_by_uuid": (2, "ns")},
        "events": {0: ("show_desktop_changed", "u"), 1: ("window", "u"),
                   2: ("stacking_order_changed", "a"),
                   3: ("stacking_order_uuid_changed", "s"),
                   4: ("window_with_uuid", "us"), 5: ("stacking_order_changed_2", "")}},
    "org_kde_plasma_window": {
        "requests": {"destroy": (7, "")},
        "events": {0: ("title_changed", "s"), 1: ("app_id_changed", "s"),
                   2: ("state_changed", "u"), 3: ("virtual_desktop_changed", "i"),
                   4: ("themed_icon_name_changed", "s"), 5: ("unmapped", ""),
                   6: ("initial_state", ""), 7: ("parent_window", "o"),
                   8: ("geometry", "iiuu"), 9: ("icon_changed", ""),
                   10: ("pid_changed", "u"), 11: ("virtual_desktop_entered", "s"),
                   12: ("virtual_desktop_left", "s"), 13: ("application_menu", "ss"),
                   14: ("activity_entered", "s"), 15: ("activity_left", "s"),
                   16: ("resource_name_changed", "s"), 17: ("client_geometry", "iiuu"),
                   18: ("mapped", "")}},
    "zwlr_foreign_toplevel_manager_v1": {
        "requests": {"stop": (0, "")},
        "events": {0: ("toplevel", "n"), 1: ("finished", "")}},
    "zwlr_foreign_toplevel_handle_v1": {
        "requests": {"destroy": (7, "")},
        "events": {0: ("title", "s"), 1: ("app_id", "s"), 2: ("output_enter", "o"),
                   3: ("output_leave", "o"), 4: ("state", "a"), 5: ("done", ""),
                   6: ("closed", ""), 7: ("parent", "o")}},
}

# Which interface a server-created new_id event carries.
_NEW_ID_INTERFACES = {
    ("ext_data_control_device_v1", "data_offer"): "ext_data_control_offer_v1",
    ("zwlr_foreign_toplevel_manager_v1", "toplevel"): "zwlr_foreign_toplevel_handle_v1",
}

PLASMA_STATE_ACTIVE = 0x1
WLR_STATE_ACTIVATED = 2


def _pad(n: int) -> int:
    return (n + 3) & ~3


class WaylandError(Exception):
    pass


class Proxy:
    """One protocol object: an id, its interface and an event handler."""

    def __init__(self, conn: WaylandConnection, object_id: int, interface: str) -> None:
        self.conn = conn
        self.id = object_id
        self.interface = interface
        self.handler: Callable[[str, tuple], None] | None = None
        self.data: dict = {}

    def request(self, name: str, *args, fds: list[int] | None = None,
                new_id: Proxy | None = None) -> Proxy | None:
        opcode, signature = INTERFACES[self.interface]["requests"][name]
        return self.conn.send(self, opcode, signature, args, fds or [], new_id=new_id)

    def __repr__(self) -> str:
        return f"<{self.interface}#{self.id}>"


class WaylandConnection:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._lock = threading.Lock()
        self._objects: dict[int, Proxy] = {}
        self._next_id = 2
        self._pending_interface: dict[int, str] = {}
        self._fds_in: list[int] = []
        self.display = Proxy(self, 1, "wl_display")
        self._objects[1] = self.display
        self.display.handler = self._on_display_event
        self.globals: dict[str, tuple[int, int]] = {}    # interface → (name, version)
        self.registry = self.request_new(self.display, "get_registry", "wl_registry")
        self.registry.handler = self._on_registry_event
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="cadent-wayland")
        self._reader.start()
        self.roundtrip()

    @classmethod
    def connect(cls, env: dict | None = None) -> WaylandConnection | None:
        env = os.environ if env is None else env
        display = env.get("WAYLAND_DISPLAY")
        runtime = env.get("XDG_RUNTIME_DIR")
        if not display or not runtime:
            return None
        path = display if display.startswith("/") else os.path.join(runtime, display)
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
            return cls(sock)
        except OSError:
            log.debug("could not connect to the Wayland display", exc_info=True)
            return None

    # ---- objects ------------------------------------------------------------------

    def new_proxy(self, interface: str, object_id: int | None = None) -> Proxy:
        with self._lock:
            if object_id is None:
                object_id = self._next_id
                self._next_id += 1
            proxy = Proxy(self, object_id, interface)
            self._objects[object_id] = proxy
        return proxy

    def bind(self, interface: str, version: int, handler=None) -> Proxy | None:
        """`wl_registry.bind` a global, at the lower of its version and
        ours. None where the compositor does not offer it."""
        found = self.globals.get(interface)
        if found is None:
            return None
        name, offered = found
        proxy = self.new_proxy(interface)
        proxy.handler = handler
        self.send(self.registry, 0, "usun", (name, interface, min(version, offered)),
                  new_id=proxy)
        return proxy

    # ---- sending ------------------------------------------------------------------

    def send(self, obj: Proxy, opcode: int, signature: str, args: tuple,
             fds: list[int] | None = None, *, new_id: Proxy | None = None) -> Proxy | None:
        """One request. A `new_id` argument never comes from `args`: it is
        either the proxy handed in, or one allocated here with the
        interface `expect_new_id` named — so positional arguments and the
        created object cannot be confused."""
        body = bytearray()
        created: Proxy | None = None
        args = list(args)
        i = 0
        for code in signature:
            if code == "n":
                created = new_id or self.new_proxy(
                    self._pending_interface.pop(obj.id, "unknown"))
                body += struct.pack("<I", created.id)
                continue
            if code == "h":
                continue    # rides in ancillary data, never in the body
            value = args[i]
            i += 1
            if code in "iu":
                body += struct.pack("<i" if code == "i" else "<I", value)
            elif code == "s":
                raw = value.encode("utf-8") + b"\0"
                body += struct.pack("<I", len(raw)) + raw.ljust(_pad(len(raw)), b"\0")
            elif code == "o":
                body += struct.pack("<I", value.id if isinstance(value, Proxy) else int(value))
            elif code == "a":
                body += struct.pack("<I", len(value)) + bytes(value).ljust(_pad(len(value)), b"\0")
            elif code == "f":
                body += struct.pack("<i", int(value * 256))
        header = struct.pack("<II", obj.id, ((8 + len(body)) << 16) | opcode)
        with self._lock:
            if fds:
                self._sock.sendmsg([header + body],
                                   [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                     array.array("i", fds).tobytes())])
            else:
                self._sock.sendall(header + body)
        return created

    def expect_new_id(self, obj: Proxy, interface: str) -> None:
        """The interface the next client-allocated new_id on `obj` carries
        (`create_data_source` etc.), where the table cannot say."""
        self._pending_interface[obj.id] = interface

    def request_new(self, obj: Proxy, name: str, interface: str, *args, fds=None) -> Proxy:
        self.expect_new_id(obj, interface)
        created = obj.request(name, *args, fds=fds)
        assert created is not None
        return created

    def roundtrip(self, timeout: float = 5.0) -> None:
        done = threading.Event()
        callback = self.new_proxy("wl_callback")
        callback.handler = lambda _name, _args: done.set()
        self.send(self.display, 0, "n", (), new_id=callback)
        if not done.wait(timeout):
            raise WaylandError("the compositor did not answer a sync in time")

    # ---- receiving ----------------------------------------------------------------

    def _read_loop(self) -> None:
        buf = bytearray()
        try:
            while True:
                data, ancdata, _flags, _addr = self._sock.recvmsg(
                    65536, socket.CMSG_SPACE(16 * 4))
                if not data:
                    return
                for level, kind, payload in ancdata:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        fds = array.array("i")
                        fds.frombytes(payload[:len(payload) - len(payload) % 4])
                        self._fds_in.extend(fds)
                buf += data
                while len(buf) >= 8:
                    object_id, word = struct.unpack_from("<II", buf, 0)
                    size, opcode = word >> 16, word & 0xFFFF
                    if len(buf) < size:
                        break
                    message = bytes(buf[8:size])
                    del buf[:size]
                    self._dispatch(object_id, opcode, message)
        except OSError:
            log.debug("wayland connection closed", exc_info=True)

    def _dispatch(self, object_id: int, opcode: int, message: bytes) -> None:
        proxy = self._objects.get(object_id)
        if proxy is None:
            return
        events = INTERFACES.get(proxy.interface, {}).get("events", {})
        if opcode not in events:
            return      # newer than our table: skipped by size
        name, signature = events[opcode]
        args = self._parse(proxy, name, signature, message)
        if proxy.handler is not None:
            try:
                proxy.handler(name, args)
            except Exception:
                log.exception("wayland event handler failed")

    def _parse(self, proxy: Proxy, name: str, signature: str, message: bytes) -> tuple:
        out = []
        offset = 0
        for code in signature:
            if code in "iuo":
                (value,) = struct.unpack_from("<i" if code == "i" else "<I", message, offset)
                offset += 4
                if code == "o":
                    value = self._objects.get(value, value)
                out.append(value)
            elif code == "n":
                (new_id,) = struct.unpack_from("<I", message, offset)
                offset += 4
                interface = _NEW_ID_INTERFACES.get((proxy.interface, name), "unknown")
                out.append(self.new_proxy(interface, new_id))
            elif code == "s":
                (length,) = struct.unpack_from("<I", message, offset)
                offset += 4
                raw = message[offset:offset + length]
                offset += _pad(length)
                out.append(raw.rstrip(b"\0").decode("utf-8", errors="replace"))
            elif code == "a":
                (length,) = struct.unpack_from("<I", message, offset)
                offset += 4
                out.append(message[offset:offset + length])
                offset += _pad(length)
            elif code == "h":
                out.append(self._fds_in.pop(0) if self._fds_in else -1)
            elif code == "f":
                (value,) = struct.unpack_from("<i", message, offset)
                offset += 4
                out.append(value / 256)
        return tuple(out)

    def _on_display_event(self, name: str, args: tuple) -> None:
        if name == "error":
            log.warning("wayland protocol error on %s: code %s: %s", *args)
        elif name == "delete_id":
            with self._lock:
                self._objects.pop(args[0], None)

    def _on_registry_event(self, name: str, args: tuple) -> None:
        if name == "global":
            number, interface, version = args
            self.globals[interface] = (number, version)
        elif name == "global_remove":
            for interface, (number, _v) in list(self.globals.items()):
                if number == args[0]:
                    del self.globals[interface]

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# ---- the clipboard without focus: ext-data-control-v1 ---------------------------------

TEXT_MIMES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")
KDE_HINT_MIME = "x-kde-passwordManagerHint"


class DataControlClipboard:
    """`Clipboard` over ext-data-control-v1 (KWin, wlroots): `set_selection`
    without focus, and the device's `selection` event as the counter."""

    def __init__(self, conn: WaylandConnection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._seq = 0
        self._text: str | None = None
        self._exclude = False
        self._source: Proxy | None = None
        self._offer: Proxy | None = None
        self._offer_mimes: list[str] = []
        seat = conn.bind("wl_seat", 1)
        manager = conn.bind("ext_data_control_manager_v1", 1)
        if seat is None or manager is None:
            raise WaylandError("ext-data-control-v1 is not offered")
        self._manager = manager
        self._device = conn.request_new(manager, "get_data_device",
                                        "ext_data_control_device_v1", seat)
        self._device.handler = self._on_device_event
        conn.roundtrip()

    @staticmethod
    def offered(conn: WaylandConnection) -> bool:
        return "ext_data_control_manager_v1" in conn.globals and "wl_seat" in conn.globals

    def _on_device_event(self, name: str, args: tuple) -> None:
        if name == "data_offer":
            offer = args[0]
            offer.data["mimes"] = []
            offer.handler = lambda n, a, o=offer: o.data["mimes"].append(a[0]) if n == "offer" else None
        elif name == "selection":
            with self._lock:
                self._seq += 1
                old, self._offer = self._offer, (args[0] if isinstance(args[0], Proxy) else None)
            if old is not None and old is not self._offer:
                old.request("destroy")

    def _on_source_event(self, name: str, args: tuple) -> None:
        if name == "send":
            mime, fd = args
            try:
                if fd >= 0:
                    payload = b"secret" if mime == KDE_HINT_MIME else \
                        (self._text or "").encode("utf-8")
                    os.write(fd, payload)
            except OSError:
                log.debug("clipboard send failed", exc_info=True)
            finally:
                if fd >= 0:
                    os.close(fd)
        elif name == "cancelled":
            with self._lock:
                self._text = None

    def get_text(self) -> str | None:
        with self._lock:
            offer = self._offer
            own = self._text
            if offer is None:
                return None
            if self._source is not None and own is not None and offer.data.get("mimes") == []:
                return own
            mimes = list(offer.data.get("mimes", []))
        mime = next((m for m in TEXT_MIMES if m in mimes), None)
        if mime is None:
            return own if own is not None else None
        r, w = os.pipe()
        try:
            offer.request("receive", mime, fds=[w])
            self._conn.roundtrip()
        finally:
            os.close(w)
        chunks = []
        try:
            import select

            deadline = 1.0
            while True:
                ready, _, _ = select.select([r], [], [], deadline)
                if not ready:
                    break
                chunk = os.read(r, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(r)
        return b"".join(chunks).decode("utf-8", errors="replace") if chunks else None

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        source = self._conn.request_new(self._manager, "create_data_source",
                                        "ext_data_control_source_v1")
        source.handler = self._on_source_event
        for mime in TEXT_MIMES:
            source.request("offer", mime)
        if exclude_from_history:
            source.request("offer", KDE_HINT_MIME)
        with self._lock:
            self._text = text
            self._exclude = exclude_from_history
            old, self._source = self._source, source
        self._device.request("set_selection", source)
        self._conn.roundtrip()
        if old is not None:
            old.request("destroy")

    def sequence_number(self) -> int:
        with self._lock:
            return self._seq


# ---- typing with our own keymap: zwp_virtual_keyboard_v1 ---------------------------------

# Real modifier bits in an xkb state mask.
_MOD_SHIFT, _MOD_CONTROL, _MOD_ALT, _MOD_LOGO = 1, 4, 8, 64
_MODIFIER_KEYSYMS = {0xFFE1: ("Shift_L", "Shift", _MOD_SHIFT),
                     0xFFE3: ("Control_L", "Control", _MOD_CONTROL),
                     0xFFE9: ("Alt_L", "Mod1", _MOD_ALT),
                     0xFFEB: ("Super_L", "Mod4", _MOD_LOGO)}


def keymap_text(keysyms: list[int]) -> str:
    """A one-level xkb keymap with one key per keysym (wtype's shape):
    keycode 8+n carries keysyms[n]. Unicode keysyms spell as `UXXXX`."""
    lines = ["xkb_keymap {", 'xkb_keycodes "(unnamed)" {', "minimum = 8;",
             f"maximum = {8 + len(keysyms) + 1};"]
    for n, _ in enumerate(keysyms, start=1):
        lines.append(f"<K{n}> = {8 + n};")
    lines += ["};", 'xkb_types "(unnamed)" { include "complete" };',
              'xkb_compatibility "(unnamed)" { include "complete" };',
              'xkb_symbols "(unnamed)" {']
    for n, keysym in enumerate(keysyms, start=1):
        if keysym in _MODIFIER_KEYSYMS:
            name, real, _bit = _MODIFIER_KEYSYMS[keysym]
            lines.append(f"key <K{n}> {{[{name}]}};")
            lines.append(f"modifier_map {real} {{ <K{n}> }};")
        elif keysym == 0xFF0D:
            lines.append(f"key <K{n}> {{[Return]}};")
        elif keysym == 0xFF09:
            lines.append(f"key <K{n}> {{[Tab]}};")
        else:
            cp = keysym - 0x01000000 if keysym >= 0x01000000 else keysym
            lines.append(f"key <K{n}> {{[U{cp:04X}]}};")
    lines += ["};", "};", ""]
    return "\n".join(lines)


class VirtualKeyboardOutput:
    """`KeyboardOutput` over zwp_virtual_keyboard_v1: upload a keymap holding
    exactly the keysyms about to be typed, then press each key. Full
    unicode, no dialog — the wlroots family's typing path (ADR 0007)."""

    XKB_KEYMAP_FORMAT_TEXT_V1 = 1

    def __init__(self, conn: WaylandConnection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        seat = conn.bind("wl_seat", 1)
        manager = conn.bind("zwp_virtual_keyboard_manager_v1", 1)
        if seat is None or manager is None:
            raise WaylandError("zwp_virtual_keyboard_v1 is not offered")
        self._keyboard = conn.request_new(manager, "create_virtual_keyboard",
                                          "zwp_virtual_keyboard_v1", seat)
        self._time = 0

    @staticmethod
    def offered(conn: WaylandConnection) -> bool:
        return "zwp_virtual_keyboard_manager_v1" in conn.globals and "wl_seat" in conn.globals

    def _upload(self, keysyms: list[int]) -> None:
        text = keymap_text(keysyms).encode("utf-8") + b"\0"
        fd = os.memfd_create("cadent-keymap")
        try:
            os.write(fd, text)
            self._keyboard.request("keymap", self.XKB_KEYMAP_FORMAT_TEXT_V1, len(text), fds=[fd])
        finally:
            os.close(fd)

    def _key(self, index: int, pressed: bool) -> None:
        self._time += 1
        self._keyboard.request("key", self._time, index + 1, 1 if pressed else 0)

    def _send_keysyms(self, keysyms: list[int], chord: bool) -> None:
        unique = list(dict.fromkeys(keysyms))
        with self._lock:
            self._upload(unique)
            self._conn.roundtrip()
            if chord:
                mask = 0
                for keysym in keysyms:
                    mask |= _MODIFIER_KEYSYMS.get(keysym, ("", "", 0))[2]
                    self._keyboard.request("modifiers", mask, 0, 0, 0)
                    self._key(unique.index(keysym), True)
                for keysym in reversed(keysyms):
                    self._key(unique.index(keysym), False)
                    mask &= ~_MODIFIER_KEYSYMS.get(keysym, ("", "", 0))[2]
                    self._keyboard.request("modifiers", mask, 0, 0, 0)
            else:
                for keysym in keysyms:
                    index = unique.index(keysym)
                    self._key(index, True)
                    self._key(index, False)
            self._conn.roundtrip()

    def send_text_units(self, units: list[int]) -> bool:
        from .x11 import keysym_for

        raw = b"".join(u.to_bytes(2, "little") for u in units)
        text = raw.decode("utf-16-le", errors="replace")
        # One keymap holds at most ~240 keys; type in slices of distinct chars.
        start = 0
        while start < len(text):
            slice_ = text[start:start + 200]
            self._send_keysyms([keysym_for(c) for c in slice_], chord=False)
            start += 200
        return True

    def send_chord(self, keys: list[int]) -> None:
        self._send_keysyms(list(keys), chord=True)

    def send_mask_key(self) -> None:
        pass

    def modifiers_down(self) -> bool:
        return False    # a Wayland client sees no keyboard but its own


# ---- the focused toplevel: KWin's and wlroots' listing protocols ---------------------------

class ToplevelTracker:
    """Keeps the app_id (and pid where the protocol carries one) of the
    active toplevel, from whichever listing protocol the compositor offers.
    `active()` is (app_id, pid) or (None, None)."""

    def __init__(self, conn: WaylandConnection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._windows: dict[int, dict] = {}
        self._active: int | None = None
        self.protocol: str | None = None
        if "org_kde_plasma_window_management" in conn.globals:
            self.protocol = "plasma-window-management"
            self._manager = conn.bind("org_kde_plasma_window_management", 16,
                                      self._on_plasma_manager_event)
        elif "zwlr_foreign_toplevel_manager_v1" in conn.globals:
            self.protocol = "wlr-foreign-toplevel-management"
            self._manager = conn.bind("zwlr_foreign_toplevel_manager_v1", 3,
                                      self._on_wlr_manager_event)
        else:
            raise WaylandError("no toplevel listing protocol is offered")
        conn.roundtrip()

    @staticmethod
    def offered(conn: WaylandConnection) -> bool:
        return ("org_kde_plasma_window_management" in conn.globals
                or "zwlr_foreign_toplevel_manager_v1" in conn.globals)

    def active(self) -> tuple[str | None, int | None]:
        with self._lock:
            window = self._windows.get(self._active) if self._active is not None else None
            if window is None:
                return None, None
            return window.get("app_id") or None, window.get("pid")

    # KWin: window_with_uuid announces each window; we fetch an object for it.
    def _on_plasma_manager_event(self, name: str, args: tuple) -> None:
        if name == "window_with_uuid":
            _id, uuid = args
            window = self._conn.request_new(self._manager, "get_window_by_uuid",
                                            "org_kde_plasma_window", uuid)
            window.handler = lambda n, a, w=window: self._on_plasma_window_event(w, n, a)
            with self._lock:
                self._windows[window.id] = {}

    def _on_plasma_window_event(self, window: Proxy, name: str, args: tuple) -> None:
        with self._lock:
            record = self._windows.setdefault(window.id, {})
            if name == "app_id_changed":
                record["app_id"] = args[0]
            elif name == "pid_changed":
                record["pid"] = args[0]
            elif name == "state_changed":
                if args[0] & PLASMA_STATE_ACTIVE:
                    self._active = window.id
                elif self._active == window.id:
                    self._active = None
            elif name == "unmapped":
                self._windows.pop(window.id, None)
                if self._active == window.id:
                    self._active = None

    def _on_wlr_manager_event(self, name: str, args: tuple) -> None:
        if name == "toplevel":
            handle = args[0]
            handle.handler = lambda n, a, h=handle: self._on_wlr_handle_event(h, n, a)
            with self._lock:
                self._windows[handle.id] = {}

    def _on_wlr_handle_event(self, handle: Proxy, name: str, args: tuple) -> None:
        with self._lock:
            record = self._windows.setdefault(handle.id, {})
            if name == "app_id":
                record["app_id"] = args[0]
            elif name == "state":
                states = struct.unpack(f"<{len(args[0]) // 4}I", args[0])
                if WLR_STATE_ACTIVATED in states:
                    self._active = handle.id
                elif self._active == handle.id:
                    self._active = None
            elif name == "closed":
                self._windows.pop(handle.id, None)
                if self._active == handle.id:
                    self._active = None
