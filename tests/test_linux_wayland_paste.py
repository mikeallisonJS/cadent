"""Wayland paste and per-compositor typing (spec §2.3, §3; ADR 0007): the wire
client against a scripted compositor, the keymap text, the Clipboard portal's
signal handling, and which mechanism a run picks — no real compositor or bus."""

import array
import os
import socket
import threading
import time

import pytest

pytest.importorskip("jeepney")

from test_linux_portal_input import WAYLAND_KDE, WAYLAND_SWAY, FakePortal  # noqa: E402

from cadent.platform.linux import (  # noqa: E402
    capabilities_for,
    choose_mechanisms,
    create,
    datacontrol,
    detect_session,
    vkeyboard,
    wayland,
    wlclient,
)
from cadent.platform.linux import portal as P  # noqa: E402
from cadent.platform.linux.portal import PortalError  # noqa: E402
from cadent.platform.linux.portalclip import PortalClipboard  # noqa: E402

LIVE = []


@pytest.fixture(autouse=True)
def _hang_up_compositors():
    """Every connection a test opened ends with it: its reader threads would
    otherwise outlive the test."""
    yield
    while LIVE:
        LIVE.pop().close()


class Compositor:
    """The far end of a socketpair: answers the registry and sync, records
    every request, and lets a test push events (with descriptors)."""

    def __init__(self, globals_):
        self.client_sock, self.sock = socket.socketpair()
        self.globals = globals_
        self.requests = []          # (object id, opcode, payload, fds)
        self.on_request = None
        self._messages = wayland.Messages()
        self.conn = None
        LIVE.append(self)
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        for sock in (self.sock, self.client_sock):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def connection(self):
        conn = wlclient.Connection(self.client_sock)
        conn.start()
        conn.hello()
        assert conn.roundtrip()
        return conn

    def push(self, object_id, opcode, *args, fds=()):
        anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
        self.sock.sendmsg([wayland.pack(object_id, opcode, *args)], anc)

    def _serve(self):
        try:
            while True:
                data, anc, _, _ = self.sock.recvmsg(65536, socket.CMSG_SPACE(16))
                if not data:
                    return
                fds = []
                for _level, kind, payload in anc:
                    if kind == socket.SCM_RIGHTS:
                        arr = array.array("i")
                        arr.frombytes(payload[:len(payload) - len(payload) % 4])
                        fds.extend(arr)
                for obj, opcode, payload in self._messages.feed(data):
                    self._handle(obj, opcode, payload, fds)
        except OSError:
            return

    def _handle(self, obj, opcode, payload, fds):
        import struct
        if obj == 1 and opcode == 1:                    # get_registry
            (registry,) = struct.unpack("<I", payload)
            for name, (interface, version) in enumerate(self.globals.items(), 1):
                self.push(registry, 0, name, interface, version)
        elif obj == 1 and opcode == 0:                  # sync
            (callback,) = struct.unpack("<I", payload)
            self.push(callback, 0, 0)
        else:
            self.requests.append((obj, opcode, payload, fds))
            if self.on_request:
                self.on_request(obj, opcode, payload, fds)


def source_of(comp, clip):
    import struct
    (source,) = struct.unpack("<I", next(
        p for o, op, p, _ in comp.requests
        if o == clip._manager and op == datacontrol.MGR_CREATE_SOURCE))
    return source


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


EXT = {"ext_data_control_manager_v1": 1, "wl_seat": 1}
SWAY = {**EXT, "zwp_virtual_keyboard_manager_v1": 1}


# ---- ext-data-control ------------------------------------------------------


def clipboard_on(globals_=EXT):
    comp = Compositor(globals_)
    return comp, datacontrol.DataControlClipboard(comp.connection())


def test_a_foreign_selection_is_counted_and_read_through_a_pipe():
    comp, clip = clipboard_on()
    device = clip._device
    offer = 0xFF000001

    def serve(obj, opcode, payload, fds):
        if obj == offer and opcode == datacontrol.OFFER_RECEIVE:
            os.write(fds[0], b"from elsewhere")
            os.close(fds[0])
    comp.on_request = serve
    comp.push(device, datacontrol.EV_DEV_DATA_OFFER, offer)
    comp.push(offer, datacontrol.EV_OFFER_OFFER, "text/plain;charset=utf-8")
    comp.push(device, datacontrol.EV_DEV_SELECTION, offer)
    assert wait_for(lambda: clip.sequence_number() == 1)
    assert clip.get_text() == "from elsewhere"


def test_an_empty_clipboard_reads_as_none():
    _comp, clip = clipboard_on()
    assert clip.get_text() is None


def test_set_text_offers_text_and_hides_it_from_history_managers():
    comp, clip = clipboard_on()
    clip.set_text("pasted", exclude_from_history=True)
    source = source_of(comp, clip)
    offers = [wayland.unpack_string(p, 0)[0] for o, op, p, _ in comp.requests
              if o == source and op == datacontrol.SRC_OFFER]
    assert "text/plain;charset=utf-8" in offers
    assert datacontrol.HIDE_FROM_HISTORY in offers
    assert any(o == clip._device and op == datacontrol.DEV_SET_SELECTION
               for o, op, _, _ in comp.requests)
    assert clip.get_text() == "pasted"


def test_a_plain_write_does_not_offer_the_hint():
    comp, clip = clipboard_on()
    clip.set_text("restored", exclude_from_history=False)
    offers = [wayland.unpack_string(p, 0)[0] for o, op, p, _ in comp.requests
              if op == datacontrol.SRC_OFFER]
    assert datacontrol.HIDE_FROM_HISTORY not in offers


def test_a_paste_target_reading_the_selection_gets_the_text():
    comp, clip = clipboard_on()
    clip.set_text("héllo", exclude_from_history=True)
    source = source_of(comp, clip)
    read, write = os.pipe()
    comp.push(source, datacontrol.EV_SRC_SEND, "text/plain;charset=utf-8", fds=(write,))
    os.close(write)
    assert wlclient.read_all(read, 2.0) == "héllo".encode()


def test_someone_taking_the_selection_ends_ownership_and_moves_the_counter():
    comp, clip = clipboard_on()
    clip.set_text("mine", exclude_from_history=True)
    seq = clip.sequence_number()
    comp.push(source_of(comp, clip), datacontrol.EV_SRC_CANCELLED)
    offer = 0xFF000002
    comp.push(clip._device, datacontrol.EV_DEV_DATA_OFFER, offer)
    comp.push(clip._device, datacontrol.EV_DEV_SELECTION, offer)
    assert wait_for(lambda: clip.sequence_number() > seq)
    assert clip.get_text() is None          # theirs has no text mime


def test_the_wlr_protocol_is_used_where_only_it_is_offered():
    comp = Compositor({"zwlr_data_control_manager_v1": 1, "wl_seat": 1})
    conn = comp.connection()
    assert datacontrol.available(conn)
    datacontrol.DataControlClipboard(conn)


# ---- virtual keyboard -------------------------------------------------------


def keyboard_on():
    comp = Compositor(SWAY)
    return comp, vkeyboard.VirtualKeyboard(comp.connection())


def keys_sent(comp, kb):
    import struct
    kb._conn.roundtrip()        # the compositor has handled everything sent
    return [struct.unpack("<III", p)[1:] for o, op, p, _ in comp.requests
            if op == vkeyboard.KB_KEY and o == kb._keyboard]


def test_a_keymap_gives_every_keysym_its_own_key():
    text = vkeyboard.build_keymap([0x61, 0x01000000 + 0x1F600, vkeyboard.KEYSYM_RETURN])
    assert "<K0> = 9;" in text and "<K2> = 11;" in text
    assert "key <K0> { [ U0061 ] };" in text
    assert "key <K1> { [ U1F600 ] };" in text
    assert "key <K2> { [ Return ] };" in text


def test_modifier_keys_are_mapped_to_the_modifier_they_stand_for():
    text = vkeyboard.build_keymap([0xFFE3, 0x76])
    assert "modifier_map Control { <K0> };" in text


def test_typing_uploads_one_keymap_and_presses_each_character():
    comp, kb = keyboard_on()
    assert kb.send_text_units([ord("h"), ord("i"), ord("h")])
    keymaps = [(p, f) for o, op, p, f in comp.requests
               if o == kb._keyboard and op == vkeyboard.KB_KEYMAP]
    assert len(keymaps) == 1 and len(keymaps[0][1]) == 1
    codes = [(code, state) for code, state in keys_sent(comp, kb)]
    # h, i distinct → evdev 1, 2; h again reuses key 1
    assert codes == [(1, 1), (1, 0), (2, 1), (2, 0), (1, 1), (1, 0)]


def test_unicode_outside_any_layout_types_without_a_dialog():
    comp, kb = keyboard_on()
    assert kb.send_text_units([0xD83D, 0xDE00])          # one emoji, as UTF-16
    assert len(keys_sent(comp, kb)) == 2


def test_the_same_keymap_is_not_uploaded_twice():
    comp, kb = keyboard_on()
    kb.send_text_units([ord("a")])
    kb.send_text_units([ord("a")])
    assert len([1 for o, op, _, _ in comp.requests
             if o == kb._keyboard and op == vkeyboard.KB_KEYMAP]) == 1


def test_the_paste_chord_sets_and_clears_the_modifier_state():
    import struct
    comp, kb = keyboard_on()
    kb.send_chord([0xFFE3, 0x76])
    kb._conn.roundtrip()
    events = [(op, struct.unpack("<I", p[:4])[0]) for o, op, p, _ in comp.requests
              if o == kb._keyboard and op in (vkeyboard.KB_MODIFIERS, vkeyboard.KB_KEY)]
    mods = [value for op, value in events if op == vkeyboard.KB_MODIFIERS]
    assert mods == [0x04, 0]
    # modifiers on before the v key goes down
    assert events[0][0] == vkeyboard.KB_MODIFIERS


def test_a_dead_compositor_fails_the_rung_without_raising():
    comp, kb = keyboard_on()
    kb._conn.close()
    assert kb.send_text_units([ord("a")]) is False


# ---- Clipboard portal -------------------------------------------------------


class ClipPortal(FakePortal):
    def __init__(self):
        super().__init__(interfaces=(P.GLOBAL_SHORTCUTS, P.CLIPBOARD))
        self.read_pipe = None

    def call(self, message, timeout=5.0):
        self.sent.append(message)
        member = message.header.fields[3]
        if member in ("SelectionRead", "SelectionWrite") and self.read_pipe:
            return (self.read_pipe,)
        return ()


class FakeFd:
    def __init__(self, fd):
        self.fd = fd

    def to_raw_fd(self):
        return self.fd


class Msg:
    def __init__(self, *body):
        self.body = body


def members(portal):
    return [m.header.fields[3] for m in portal.sent]


def test_the_clipboard_is_requested_on_the_session_before_it_starts():
    portal = ClipPortal()
    clip = PortalClipboard(portal, lambda: "/s")
    clip.request("/s")
    assert members(portal) == ["RequestClipboard"]
    assert {"SelectionTransfer", "SelectionOwnerChanged"} <= set(portal.signals)


def test_set_text_claims_the_selection_and_our_own_change_is_not_counted():
    portal = ClipPortal()
    clip = PortalClipboard(portal, lambda: "/s")
    clip.set_text("x", exclude_from_history=True)
    msg = portal.sent[-1]
    assert msg.header.fields[3] == "SetSelection"
    clip.request("/s")
    portal.signals["SelectionOwnerChanged"](
        Msg("/s", {"session_is_owner": ("b", True), "mime_types": ("as", ["text/plain"])}))
    assert clip.sequence_number() == 0 and clip.get_text() == "x"
    portal.signals["SelectionOwnerChanged"](
        Msg("/s", {"session_is_owner": ("b", False), "mime_types": ("as", ["text/plain"])}))
    assert clip.sequence_number() == 1


def test_a_transfer_request_is_answered_with_the_text_and_a_done():
    portal = ClipPortal()
    clip = PortalClipboard(portal, lambda: "/s")
    clip.request("/s")
    clip.set_text("payload", exclude_from_history=False)
    read, write = os.pipe()
    portal.read_pipe = FakeFd(write)
    portal.signals["SelectionTransfer"](Msg("/s", "text/plain", 9))
    assert wlclient.read_all(read, 2.0) == b"payload"
    assert wait_for(lambda: "SelectionWriteDone" in members(portal))
    done = [m for m in portal.sent if m.header.fields[3] == "SelectionWriteDone"][0]
    assert done.body == ("/s", 9, True)


def test_a_foreign_selection_is_read_through_the_portal():
    portal = ClipPortal()
    clip = PortalClipboard(portal, lambda: "/s")
    clip.request("/s")
    portal.signals["SelectionOwnerChanged"](
        Msg("/s", {"session_is_owner": ("b", False),
                   "mime_types": ("as", ["text/plain;charset=utf-8"])}))
    read, write = os.pipe()
    os.write(write, b"theirs")
    os.close(write)
    portal.read_pipe = FakeFd(read)
    assert clip.get_text() == "theirs"


def test_with_no_input_session_the_portal_clipboard_cannot_write():
    clip = PortalClipboard(ClipPortal(), lambda: None)
    with pytest.raises(PortalError):
        clip.set_text("x", exclude_from_history=True)
    assert clip.get_text() is None


# ---- choosing the mechanisms -------------------------------------------------


def fake_conn(*interfaces):
    class Conn:
        def offers(self, *names):
            return next((n for n in names if n in interfaces), None)
    return Conn()


def test_wlroots_types_with_the_virtual_keyboard_and_pastes_with_data_control():
    m = choose_mechanisms(fake_conn(*SWAY), lambda: pytest.fail("not asked"))
    assert (m.typing, m.paste) == ("virtual-keyboard", "data-control")
    assert not m.needs_input_session


def test_kwin_pastes_with_data_control_but_types_through_the_portal():
    m = choose_mechanisms(fake_conn(*EXT), lambda: pytest.fail("not asked"))
    assert (m.typing, m.paste) == ("remote-desktop", "data-control")
    assert m.needs_input_session


def test_gnome_pastes_through_the_clipboard_portal():
    m = choose_mechanisms(fake_conn("wl_seat"), lambda: True)
    assert (m.typing, m.paste) == ("remote-desktop", "portal")


def test_an_older_gnome_backend_collapses_to_type_only_and_says_so():
    m = choose_mechanisms(None, lambda: False)
    assert m.paste is None
    gnome = detect_session({"XDG_SESSION_TYPE": "wayland",
                            "XDG_CURRENT_DESKTOP": "GNOME"})
    caps = capabilities_for(gnome, paste_available=False)
    assert caps.injection_rungs == ("type",)
    assert caps.default_injection_strategy == "type"
    assert "Pasting isn't available in this session" in caps.paste_note


def test_a_paste_mechanism_makes_paste_the_default_rung_with_no_fall_through():
    caps = capabilities_for(detect_session(WAYLAND_SWAY), paste_available=True)
    assert caps.default_injection_strategy == "clipboard"
    assert caps.injection_rungs == ("paste", "type")
    assert caps.paste_note is None and not caps.auto_learn_overrides


def test_type_it_stays_a_reachable_strategy():
    from cadent import settings
    assert "type" in [value for value, _ in settings.STRATEGY_LABELS]


def test_create_on_wlroots_needs_no_remote_desktop_grant(monkeypatch):
    comp = Compositor(SWAY)
    portal = FakePortal(interfaces=(P.GLOBAL_SHORTCUTS,))
    plat = create(WAYLAND_SWAY, portal=portal, connect=lambda env: comp.connection())
    assert plat.capabilities.injection_rungs == ("paste", "type")
    assert plat.capabilities.permission is not None
    plat.hotkey_tap.start(lambda *a: None, ("<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c"))
    assert plat.keyboard.send_text_units([ord("a")])        # no input session needed


def test_create_on_kde_pastes_with_data_control():
    comp = Compositor(EXT)
    plat = create(WAYLAND_KDE, portal=FakePortal(), windows=lambda: None,
                  connect=lambda env: comp.connection())
    assert plat.capabilities.default_injection_strategy == "clipboard"
    plat.clipboard.set_text("a", exclude_from_history=True)
    assert plat.clipboard.get_text() == "a"
