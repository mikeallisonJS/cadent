"""The Wayland tiers (#35, #36; spec M6 §2.2–§2.3, §3, §4, §9) against fakes:
the wire marshalling with no socket, the protocol wrappers with scripted
events, and the portal seams with the fake bus. Compositor and portal
behaviour is verified on hardware (§12).
"""

import dataclasses
import struct
import threading

from conftest import make_platform
from fake_portal import FakeBus

from cadent import platform as platform_pkg
from cadent.chord import Action, ChordStateMachine
from cadent.platform.linux import portal, wayland, wayland_tiers
from cadent.platform.linux.desktopfiles import DesktopIndex
from cadent.platform.linux.keysyms import CONTROL_L, SUPER_L
from cadent.platform.linux.wayland_tiers import (
    CLEANUP_ID,
    DICTATE_ID,
    GlobalShortcutsTap,
    RemoteDesktopSession,
    TokenStore,
    WaylandRun,
    chord_keysyms,
    combo_to_trigger,
)

# ---- the wire (pure) ---------------------------------------------------------

def test_marshal_packs_header_and_padded_strings():
    data, fds = wayland.marshal(2, 0, "uN", (7, ("wl_seat", 5, 3)))
    object_id, size_op = struct.unpack_from("<II", data)
    assert object_id == 2 and size_op & 0xFFFF == 0 and size_op >> 16 == len(data)
    assert len(data) % 4 == 0
    # name, then string length 8 ("wl_seat\0"), the string padded to 8, version, id
    assert data[8:12] == struct.pack("<I", 7)
    assert data[12:16] == struct.pack("<I", 8)
    assert data[16:24] == b"wl_seat\0"
    assert data[24:32] == struct.pack("<II", 5, 3)
    assert fds == []


def test_marshal_carries_fds_out_of_band():
    data, fds = wayland.marshal(9, 0, "uhu", (1, 42, 100))
    assert fds == [42]
    assert len(data) == 8 + 8            # the fd takes no bytes in the body


def test_unmarshal_round_trips_strings_arrays_and_nulls():
    body = wayland.marshal(1, 0, "us?sa", (3, "hi", None, b"\x01\x02\x03"))[0][8:]
    assert wayland.unmarshal_args("us?sa", body, []) == (3, "hi", None, b"\x01\x02\x03")


def test_the_client_parses_partial_messages_and_dispatches_events():
    class Sock:
        def close(self):
            pass

        def sendall(self, data):
            pass

    client = wayland.WaylandClient.__new__(wayland.WaylandClient)
    client._sock = Sock()
    client._send_lock = threading.Lock()
    client._objects = {}
    client._next_id = 2
    client._closed = True          # no thread
    client.globals = {}
    client._callbacks = {}
    client._lock = threading.RLock()
    client._pending_data = b""
    client._pending_fds = []
    client._error = None
    display = wayland.WaylandObject(client, 1, "wl_display", {1: ("delete_id", "u")})
    client._objects[1] = display
    registry = client.new_object("wl_registry", {0: ("global", "usu")})
    registry.handler = client._on_registry_event
    event = wayland.marshal(registry.id, 0, "usu", (4, "zwp_virtual_keyboard_manager_v1", 1))[0]
    client.feed(event[:5], [])
    assert client.globals == {}
    client.feed(event[5:], [])
    assert client.globals == {"zwp_virtual_keyboard_manager_v1": (4, 1)}


def test_build_keymap_names_every_keysym_once():
    text, codes = wayland.build_keymap([0x61, 0x01004E2D, 0xFF0D, 0x61])
    assert codes == {0x61: 1, 0x01004E2D: 2, 0xFF0D: 3}
    assert "<K1> = 9;" in text
    assert "key <K1> {[U0061]};" in text
    assert "key <K2> {[U4E2D]};" in text
    assert "key <K3> {[Return]};" in text
    assert 'include "complete"' in text


# ---- protocol wrappers with a scripted client -------------------------------

class ScriptedClient:
    """A `WaylandClient` stand-in: records requests, exposes globals, and
    lets a test deliver events to the objects wrappers created."""

    def __init__(self, globals_):
        self.globals = {name: (i + 1, 9) for i, name in enumerate(globals_)}
        self.sent = []
        self.objects = {}
        self._next = 2
        self.roundtrips = 0

    def bind(self, interface, events, max_version):
        if interface not in self.globals:
            return None
        return self.new_object(interface, events)

    def new_object(self, interface, events):
        obj = wayland.WaylandObject(self, self._next, interface, events)
        self._next += 1
        self.objects[obj.id] = obj
        return obj

    def adopt_server_object(self, object_id, interface, events):
        obj = wayland.WaylandObject(self, object_id, interface, events)
        self.objects[object_id] = obj
        return obj

    def forget(self, object_id):
        self.objects.pop(object_id, None)

    def send(self, object_id, opcode, signature, args):
        self.sent.append((self.objects[object_id].interface, opcode, args))

    def roundtrip(self, timeout=2.0):
        self.roundtrips += 1

    def has_global(self, name):
        return name in self.globals

    def deliver(self, obj, name, args):
        obj.handler(name, args)


def test_plasma_windows_track_the_active_app_id():
    client = ScriptedClient(["org_kde_plasma_window_management"])
    windows = wayland.PlasmaWindows(client)
    manager = client.objects[2]
    client.deliver(manager, "window", (77,))
    win = client.objects[3]
    assert ("org_kde_plasma_window_management", 1, (3, 77)) in client.sent   # get_window
    client.deliver(win, "app_id_changed", ("org.kde.konsole",))
    client.deliver(win, "pid_changed", (4242,))
    client.deliver(win, "geometry", (10, 20, 300, 200))
    assert windows.active() is None
    client.deliver(win, "state_changed", (wayland.PlasmaWindows.STATE_ACTIVE,))
    assert windows.active()["app_id"] == "org.kde.konsole"
    assert windows.active()["rect"] == (10, 20, 310, 220)
    client.deliver(win, "unmapped", ())
    assert windows.active() is None


def test_wlr_toplevels_track_activation():
    client = ScriptedClient(["zwlr_foreign_toplevel_manager_v1"])
    toplevels = wayland.WlrToplevels(client)
    manager = client.objects[2]
    client.deliver(manager, "toplevel", (0xFF000001,))
    handle = client.objects[0xFF000001]
    client.deliver(handle, "app_id", ("kitty",))
    client.deliver(handle, "state", (struct.pack("<2I", 0, 2),))
    assert toplevels.active()["app_id"] == "kitty"
    client.deliver(handle, "closed", ())
    assert toplevels.active() is None


def test_data_control_offers_text_and_counts_selection_changes(monkeypatch):
    client = ScriptedClient(["wl_seat", "ext_data_control_manager_v1"])
    seat = wayland.bind_seat(client)
    dc = wayland.DataControl(client, seat)
    device = client.objects[4]
    dc.set_text("hello", exclude_from_history=True)
    kinds = [(iface, op) for iface, op, _ in client.sent]
    assert ("ext_data_control_manager_v1", 0) in kinds          # create_data_source
    offers = [a[0] for iface, op, a in client.sent
              if iface == "ext_data_control_source_v1" and op == 0]
    assert "text/plain;charset=utf-8" in offers and wayland.KDE_HINT_MIME in offers
    assert ("ext_data_control_device_v1", 0) in kinds           # set_selection
    assert dc.get_text() == "hello"                             # we own it
    # Someone else takes the clipboard: the counter moves and our text is gone.
    client.deliver(device, "data_offer", (0xFF000002,))
    offer = client.objects[0xFF000002]
    client.deliver(offer, "offer", ("text/plain;charset=utf-8",))
    before = dc.sequence
    source = client.objects[5]
    client.deliver(source, "cancelled", ())
    client.deliver(device, "selection", (0xFF000002,))
    assert dc.sequence == before + 1


def test_the_virtual_keyboard_sends_a_keymap_then_keys(monkeypatch, tmp_path):
    import os

    client = ScriptedClient(["wl_seat", "zwp_virtual_keyboard_manager_v1"])
    seat = wayland.bind_seat(client)
    written = {}

    def fake_memfd(name):
        fd, path = os.pipe()
        os.close(path)
        return fd
    monkeypatch.setattr(wayland.os, "memfd_create", lambda name: os.open(
        tmp_path / "km", os.O_RDWR | os.O_CREAT), raising=False)
    vk = wayland.VirtualKeyboard(client, seat)
    vk.type_keysyms([0x61, 0x01004E2D])
    ops = [(iface, op) for iface, op, _ in client.sent if iface == "zwp_virtual_keyboard_v1"]
    assert ops[0] == ("zwp_virtual_keyboard_v1", 0)              # keymap first
    assert ops[1:] == [("zwp_virtual_keyboard_v1", 1)] * 4       # press/release ×2
    keys = [a for iface, op, a in client.sent if iface == "zwp_virtual_keyboard_v1" and op == 1]
    assert [(k[1], k[2]) for k in keys] == [(1, 1), (1, 0), (2, 1), (2, 0)]
    del written


# ---- chords ↔ triggers -----------------------------------------------------------

def test_combo_to_trigger_needs_a_keysym():
    assert combo_to_trigger("<ctrl>+<cmd>+space") == "CTRL+LOGO+space"
    assert combo_to_trigger("<ctrl>+<alt>+f9") == "CTRL+ALT+F9"
    assert combo_to_trigger("<ctrl>+<cmd>") is None
    assert chord_keysyms("<ctrl>+<cmd>") == [CONTROL_L, SUPER_L]
    assert chord_keysyms("<ctrl>+<cmd>+space") == [CONTROL_L, SUPER_L, 0x20]


# ---- the GlobalShortcuts tap on the fake bus ------------------------------------------

def inline(fn):
    fn()


def bus_with_portal(*interfaces):
    bus = FakeBus()
    bus.auto_response = portal.RESPONSE_OTHER
    present = set(interfaces)

    def version(msg):
        iface = msg.body[0]
        if iface in present:
            return (("u", 2),)
        raise portal.PortalError("org.freedesktop.DBus.Error.InvalidArgs")
    bus.replies[("org.freedesktop.DBus.Properties", "Get")] = version
    return bus


def request_path(bus, n):
    return portal.request_path(bus.unique_name, f"cadent{n}")


def test_the_tap_binds_both_chords_and_synthesizes_the_chord_machines_events(monkeypatch):
    bus = bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE)
    bus.auto_response = None                          # consent answered by hand below
    tap = GlobalShortcutsTap(bus, portal.RequestTokens(), "<ctrl>+<cmd>+space",
                             "<ctrl>+<cmd>+c", worker=inline)
    assert tap.available() is True
    events = []
    # CreateSession is request token cadent2 (cadent1 was the session token).
    def answer_create(msg):
        return (request_path(bus, 2),)
    bus.replies[(portal.GLOBAL_SHORTCUTS_IFACE, "CreateSession")] = answer_create
    # Respond to CreateSession and ListShortcuts as they are sent.
    real = bus.send_and_get_reply

    def auto(msg, timeout=5.0):
        member = msg.header.fields[portal.HeaderFields.member]
        if member == "CreateSession":
            handle = "/org/freedesktop/portal/desktop/session/1_42/cadent1"
            bus.respond(request_path(bus, 2), 0, {"session_handle": ("s", handle)})
        elif member == "ListShortcuts":
            bus.respond(msg.body[1]["handle_token"][1] and
                        portal.request_path(bus.unique_name, msg.body[1]["handle_token"][1]),
                        0, {"shortcuts": ("a(sa{sv})", [])})
        return real(msg, timeout)
    bus.send_and_get_reply = auto

    tap.start(lambda k, d, i: events.append((k, d, i)),
              chords=("<ctrl>+<cmd>", "<ctrl>+<shift>+<alt>"))
    # Modifier-only chords bind as the tier defaults; the pane learns why.
    assert tap.substituted == {DICTATE_ID: "<ctrl>+<cmd>+space",
                               CLEANUP_ID: "<ctrl>+<cmd>+c"}
    assert tap._triggers == {DICTATE_ID: "CTRL+LOGO+space", CLEANUP_ID: "CTRL+LOGO+c"}
    assert tap.bound() is False                     # nothing bound yet
    # The consent call binds both in one BindShortcuts.
    pending = tap.request_bind()
    binds = bus.calls("BindShortcuts")
    assert len(binds) == 1
    ids = [sid for sid, _props in binds[0].body[1]]
    assert ids == [DICTATE_ID, CLEANUP_ID]
    bus.respond(pending.handle, 0, {"shortcuts": ("a(sa{sv})", [
        (DICTATE_ID, {"trigger_description": ("s", "Ctrl+Meta+Space")}),
        (CLEANUP_ID, {"trigger_description": ("s", "Ctrl+Meta+C")})])})
    assert tap.bound() is True
    assert tap.bound_shortcuts() == {DICTATE_ID: "Ctrl+Meta+Space",
                                     CLEANUP_ID: "Ctrl+Meta+C"}
    # Activated/Deactivated become the parsed keysym events for <ctrl>+<cmd>
    # — what the chord machine built for the *stored* combo expects.
    session = tap._session
    bus.emit(portal.PORTAL_PATH, portal.GLOBAL_SHORTCUTS_IFACE, "Activated", "osta{sv}",
             (session, DICTATE_ID, 1, {}))
    bus.emit(portal.PORTAL_PATH, portal.GLOBAL_SHORTCUTS_IFACE, "Deactivated", "osta{sv}",
             (session, DICTATE_ID, 2, {}))
    assert events == [(CONTROL_L, True, False), (SUPER_L, True, False),
                      (SUPER_L, False, False), (CONTROL_L, False, False)]
    # And a chord machine built for the stored combo starts and stops on them.
    from cadent.platform import linux

    caps = linux.capabilities_for(linux.detect({"XDG_SESSION_TYPE": "wayland",
                                                "XDG_CURRENT_DESKTOP": "KDE"}))
    plat = dataclasses.replace(make_platform(), capabilities=caps)
    monkeypatch.setattr(platform_pkg, "_current", plat)
    sm = ChordStateMachine("<ctrl>+<cmd>", "hold", min_hold_s=0.0)
    acts = []
    for t, (keysym, down, injected) in enumerate(events, start=1):
        acts += sm.on_event(keysym, down, injected, float(t))
    assert Action.START in acts and Action.STOP in acts


def test_a_missing_interface_disarms_without_a_permission_fault():
    bus = bus_with_portal()                          # no GlobalShortcuts
    tap = GlobalShortcutsTap(bus, portal.RequestTokens(), "<ctrl>+<cmd>+space",
                             "<ctrl>+<cmd>+c", worker=inline)
    assert tap.available() is False
    tap.start(lambda *_: None, chords=("<ctrl>+<cmd>",))
    assert bus.calls("CreateSession") == []
    focused = wayland_tiers.WaylandFocusedApp(None, DesktopIndex([]), tap, None, False)
    assert focused.permission_granted() is True     # hotkey-unavailable, not permission-needed
    assert focused.name() == "unknown"


def test_a_closed_session_is_recreated_once_then_admitted_dead():
    bus = bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE)
    tap = GlobalShortcutsTap(bus, portal.RequestTokens(), "<ctrl>+<cmd>+space",
                             "<ctrl>+<cmd>+c", worker=inline)
    real = bus.send_and_get_reply
    handles = []

    def auto(msg, timeout=5.0):
        member = msg.header.fields[portal.HeaderFields.member]
        token = msg.body[-1]["handle_token"][1] if member in ("CreateSession", "ListShortcuts") \
            else None
        if member == "CreateSession":
            handle = f"/org/freedesktop/portal/desktop/session/1_42/{len(handles)}"
            handles.append(handle)
            bus.respond(portal.request_path(bus.unique_name, token), 0,
                        {"session_handle": ("s", handle)})
        elif member == "ListShortcuts":
            bus.respond(portal.request_path(bus.unique_name, token), 0,
                        {"shortcuts": ("a(sa{sv})", [(DICTATE_ID, {})])})
        return real(msg, timeout)
    bus.send_and_get_reply = auto
    tap.start(lambda *_: None, chords=("<ctrl>+<cmd>+space",))
    assert tap.bound() is True
    bus.emit(handles[0], portal.SESSION_IFACE, "Closed", "", ())
    assert len(handles) == 2 and tap.bound() is True          # recovered once
    bus.emit(handles[1], portal.SESSION_IFACE, "Closed", "", ())
    assert len(handles) == 2 and tap.bound() is False         # then honest and dead


# ---- the RemoteDesktop session and restore tokens ------------------------------------

def test_the_session_persists_its_restore_token_outside_config(tmp_path):
    bus = bus_with_portal(portal.REMOTE_DESKTOP_IFACE, portal.CLIPBOARD_IFACE)
    bus.auto_response = None
    store = TokenStore(tmp_path / "portal-tokens.json")
    store.set("remote-desktop", "old-token")
    session = RemoteDesktopSession(bus, portal.RequestTokens(), store,
                                   want_clipboard=True, worker=inline)
    real = bus.send_and_get_reply

    def auto(msg, timeout=5.0):
        member = msg.header.fields[portal.HeaderFields.member]
        if member in ("CreateSession", "SelectDevices"):
            token = msg.body[-1]["handle_token"][1]
            bus.respond(portal.request_path(bus.unique_name, token), 0,
                        {"session_handle": ("s", "/s/rd")})
        return real(msg, timeout)
    bus.send_and_get_reply = auto
    session.request_start()
    select = bus.calls("SelectDevices")[0]
    assert select.body[1]["restore_token"] == ("s", "old-token")
    assert select.body[1]["persist_mode"] == ("u", portal.PERSIST_UNTIL_REVOKED)
    # RequestClipboard before Start, on the same session.
    members = [m.header.fields[portal.HeaderFields.member] for m in bus.sent]
    assert members.index("RequestClipboard") < members.index("Start")
    assert session.live is False                              # consent: not awaited
    start_token = bus.calls("Start")[0].body[-1]["handle_token"][1]
    bus.respond(portal.request_path(bus.unique_name, start_token), 0,
                {"restore_token": ("s", "new-token"), "clipboard_enabled": ("b", True)})
    assert session.live is True and session.clipboard_enabled is True
    assert store.get("remote-desktop") == "new-token"
    assert (tmp_path / "portal-tokens.json").exists()
    # Typing rides the session as keysym presses.
    session.notify_keysym(0x61, True)
    assert bus.calls("NotifyKeyboardKeysym")[0].body[2:] == (0x61, 1)


def test_a_denied_start_leaves_the_grant_missing_with_no_retry(tmp_path):
    bus = bus_with_portal(portal.REMOTE_DESKTOP_IFACE)
    bus.auto_response = None
    store = TokenStore(tmp_path / "t.json")
    session = RemoteDesktopSession(bus, portal.RequestTokens(), store,
                                   want_clipboard=False, worker=inline)
    real = bus.send_and_get_reply

    def auto(msg, timeout=5.0):
        member = msg.header.fields[portal.HeaderFields.member]
        if member in ("CreateSession", "SelectDevices"):
            token = msg.body[-1]["handle_token"][1]
            bus.respond(portal.request_path(bus.unique_name, token), 0,
                        {"session_handle": ("s", "/s/rd")})
        return real(msg, timeout)
    bus.send_and_get_reply = auto
    session.request_start()
    start_token = bus.calls("Start")[0].body[-1]["handle_token"][1]
    bus.respond(portal.request_path(bus.unique_name, start_token), portal.RESPONSE_CANCELLED)
    assert session.live is False
    assert len(bus.calls("Start")) == 1                        # nobody retried


# ---- the run: mechanisms per compositor -----------------------------------------

def run_for(tier, bus, client, tmp_path):
    return WaylandRun(bus, tier=tier, default_combo="<ctrl>+<cmd>+space",
                      default_cleanup_combo="<ctrl>+<cmd>+c",
                      token_store=TokenStore(tmp_path / "t.json"),
                      desktop_index=DesktopIndex([]), wayland_client=client, worker=inline)


def test_kwin_rides_data_control_and_remote_desktop_typing(tmp_path, monkeypatch):
    client = ScriptedClient(["wl_seat", "ext_data_control_manager_v1",
                             "org_kde_plasma_window_management"])
    bus = bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE, portal.REMOTE_DESKTOP_IFACE,
                          portal.CLIPBOARD_IFACE)
    run = run_for("portal", bus, client, tmp_path)
    assert run.typing_mechanism == "remote-desktop"
    assert run.paste_mechanism == "data-control"
    assert run.paste_available is True and run.per_app_overrides is True
    assert run.needs_session is True and run.hotkey_unavailable is False


def test_wlroots_with_virtual_keyboard_needs_no_remote_desktop(tmp_path, monkeypatch):
    client = ScriptedClient(["wl_seat", "zwp_virtual_keyboard_manager_v1",
                             "zwlr_data_control_manager_v1",
                             "zwlr_foreign_toplevel_manager_v1"])
    monkeypatch.setattr(wayland.os, "memfd_create", None, raising=False)
    bus = bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE)      # Hyprland: no RemoteDesktop
    run = run_for("portal", bus, client, tmp_path)
    assert run.typing_mechanism == "virtual-keyboard"
    assert run.paste_mechanism == "data-control"
    assert run.needs_session is False
    assert run.session is None
    # permission_granted needs only the shortcut grant here.
    run.tap._bound = {DICTATE_ID: "x"}
    run.tap._session = "/s"
    assert run.focused_app.permission_granted() is True


def test_gnome_rides_the_clipboard_portal_and_a_missing_one_drops_paste(tmp_path):
    bus = bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE, portal.REMOTE_DESKTOP_IFACE,
                          portal.CLIPBOARD_IFACE)
    run = run_for("reduced", bus, None, tmp_path)
    assert run.typing_mechanism == "remote-desktop"
    assert run.paste_mechanism == "clipboard-portal"
    assert run.paste_available is True and run.per_app_overrides is False
    assert run.focused_app.name() == "unknown"

    older = bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE, portal.REMOTE_DESKTOP_IFACE)
    run = run_for("reduced", older, None, tmp_path)
    assert run.paste_mechanism is None and run.paste_available is False


def test_stock_sway_starts_with_the_hotkey_unavailable(tmp_path):
    client = ScriptedClient(["wl_seat", "zwp_virtual_keyboard_manager_v1",
                             "zwlr_data_control_manager_v1"])
    bus = bus_with_portal()                          # xdg-desktop-portal-wlr: no shortcuts
    run = run_for("portal", bus, client, tmp_path)
    assert run.hotkey_unavailable is True
    assert run.tap.available() is False
    assert run.focused_app.permission_granted() is True
    assert run.per_app_overrides is False


def test_the_factory_builds_wayland_capabilities_from_the_run(monkeypatch, tmp_path):
    from cadent.platform import linux

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(linux, "open_portal_connection",
                        lambda: bus_with_portal(portal.GLOBAL_SHORTCUTS_IFACE,
                                                portal.REMOTE_DESKTOP_IFACE))
    monkeypatch.setattr(linux, "_wayland_run", lambda info, bus: run_for(
        info.tier, bus, None, tmp_path))
    plat = linux.create()
    assert plat.capabilities.support_tier == "reduced"
    assert plat.capabilities.injection_rungs == ("type",)      # no Clipboard portal
    assert plat.capabilities.per_app_overrides is False
    assert plat.hotkey_tap.available() is True
    plat.desktop.request_permission()                          # wired to the run
    assert plat.desktop.text_scale_factor() == 1.0             # fallback reads still answer


# ---- app: the hotkey-unavailable fault ------------------------------------------

def test_the_app_raises_hotkey_unavailable_from_the_tap(qt_app):
    from cadent import app as app_mod
    from cadent.tray import FAULTS

    class FakeTray:
        def __init__(self):
            self.faults = {}

        def set_fault(self, kind, active=True):
            assert kind in FAULTS
            self.faults[kind] = active

    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.tray = FakeTray()
    instance.platform = make_platform()
    instance.platform.hotkey_tap.available = lambda: False
    instance._check_hotkey_available()
    assert instance.tray.faults == {"hotkey-unavailable": True}
    instance.platform.hotkey_tap.available = lambda: True
    instance._check_hotkey_available()
    assert instance.tray.faults == {"hotkey-unavailable": False}


# ---- the panes read the tap and the facts (§9.4, §11) -----------------------

def linux_platform(session_type, desktop, **kw):
    from cadent.platform import linux

    plat = make_platform()
    caps = linux.capabilities_for(linux.detect({"XDG_SESSION_TYPE": session_type,
                                                "XDG_CURRENT_DESKTOP": desktop}), **kw)
    return dataclasses.replace(plat, capabilities=caps)


def settings_window(tmp_path):
    from cadent.config_store import ConfigStore
    from cadent.settings_ui import SettingsWindow
    from cadent.theme.tokens import tokens

    return SettingsWindow(ConfigStore(tmp_path / "config.json"),
                          tokens=tokens("dark"), devices=[])


def test_the_hotkeys_pane_says_the_desktop_owns_the_shortcut(qt_app, tmp_path,
                                                            monkeypatch):
    plat = linux_platform("wayland", "KDE")
    plat.hotkey_tap.bound_shortcuts = lambda: {"dictate": "Ctrl+Meta+Space"}
    monkeypatch.setattr(platform_pkg, "_current", plat)
    win = settings_window(tmp_path)
    try:
        note = win.hotkeys.desktop_note
        assert note.isVisibleTo(win.hotkeys) is True
        assert "Your desktop owns this shortcut" in note.text()
        assert "Ctrl+Meta+Space" in note.text()
    finally:
        win.close()


def test_the_hotkeys_pane_names_the_wayland_defaults_when_nothing_is_bound(
        qt_app, tmp_path, monkeypatch):
    plat = linux_platform("wayland", "GNOME")
    plat.hotkey_tap.bound_shortcuts = lambda: {}
    monkeypatch.setattr(platform_pkg, "_current", plat)
    win = settings_window(tmp_path)
    try:
        assert "<ctrl>+<cmd>+space" in win.hotkeys.desktop_note.text()
    finally:
        win.close()


def test_the_hotkeys_pane_stays_quiet_where_the_hook_sees_the_keyboard(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_pkg, "_current", make_platform())
    win = settings_window(tmp_path)
    try:
        assert win.hotkeys.desktop_note.isVisibleTo(win.hotkeys) is False
    finally:
        win.close()


def test_the_overrides_pane_carries_the_session_notes(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_pkg, "_current",
                        linux_platform("wayland", "GNOME", paste_available=False))
    win = settings_window(tmp_path)
    try:
        note = win.overrides.session_note.text()
        assert "aren't applied in this session" in note
        assert "Pasting isn't available" in note
        assert win.overrides.table.isEnabled()          # still editable
    finally:
        win.close()
    monkeypatch.setattr(platform_pkg, "_current", linux_platform("wayland", "KDE"))
    win = settings_window(tmp_path)
    try:
        assert win.overrides.session_note.isVisibleTo(win.overrides) is False
    finally:
        win.close()


def test_the_done_page_carries_the_tier_line_only_on_the_wayland_tiers(
        qt_app, tmp_path, monkeypatch):
    from cadent import hardware
    from cadent.config_store import ConfigStore
    from cadent.wizard import DONE, SetupWizard

    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: True)
    for session, desktop, expected in (("wayland", "KDE", True), ("x11", "KDE", False)):
        plat = linux_platform(session, desktop)
        monkeypatch.setattr(platform_pkg, "_current", plat)
        win = SetupWizard(ConfigStore(tmp_path / f"{session}.json"), devices=[])
        try:
            while win.page != DONE:
                win.advance()
            assert hasattr(win, "tier_line") is expected or \
                (expected and win.tier_line.text() == plat.capabilities.support_tier_summary)
        finally:
            win._completed = True
            win.close()
            win.deleteLater()


# ---- review fixes: no bounded call from the portal thread; re-ask after a refusal

def test_selection_transfer_writes_off_the_portal_thread(monkeypatch):
    """`SelectionTransfer` arrives on the portal thread, whose bounded replies
    are dispatched by that same thread — the write must not block it."""
    from cadent.platform.linux import wayland_tiers as wt

    bus = bus_with_portal(portal.REMOTE_DESKTOP_IFACE, portal.CLIPBOARD_IFACE)
    session = RemoteDesktopSession(bus, portal.RequestTokens(), TokenStore(
        __import__("pathlib").Path("unused")), want_clipboard=True, worker=inline)
    session.handle, session.live = "/s/rd", True
    clip = wt.PortalClipboard(bus, session)
    spawned = []
    monkeypatch.setattr(wt, "_spawn", lambda fn: spawned.append(fn))
    bus.emit(portal.PORTAL_PATH, portal.CLIPBOARD_IFACE, "SelectionTransfer", "osu",
             ("/s/rd", "text/plain;charset=utf-8", 7))
    assert len(spawned) == 1 and bus.calls("SelectionWrite") == []


def test_a_refused_create_session_leaves_request_start_re_askable(tmp_path):
    bus = bus_with_portal(portal.REMOTE_DESKTOP_IFACE)          # auto Response 2
    session = RemoteDesktopSession(bus, portal.RequestTokens(), TokenStore(tmp_path / "t"),
                                   want_clipboard=False, worker=inline)
    session.request_start()
    assert len(bus.calls("CreateSession")) == 1
    session.request_start()                                       # not stuck
    assert len(bus.calls("CreateSession")) == 2
