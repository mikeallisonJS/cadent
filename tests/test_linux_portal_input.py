"""The Portal tier's input path (spec §2.2, §4, §9): GlobalShortcuts binding,
keysym synthesis, the grant health check, restore tokens, session recovery and
the missing-interface fault — all against a fake `Portal`, no bus."""

import socket
import threading

import pytest

pytest.importorskip("jeepney")

from cadent import hotkey as hotkey_mod  # noqa: E402
from cadent.platform.keycodes import LINUX_KEYCODES  # noqa: E402
from cadent.platform.linux import (  # noqa: E402
    capabilities_for,
    create,
    detect_session,
    wayland,
)
from cadent.platform.linux import (
    input as input_mod,
)
from cadent.platform.linux import portal as P  # noqa: E402
from cadent.platform.linux.portal import PortalError  # noqa: E402

WAYLAND_KDE = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}
WAYLAND_SWAY = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "sway"}


class FakePortal:
    """Just the surface `PortalInput` and `create` use."""

    def __init__(self, interfaces=(P.GLOBAL_SHORTCUTS,), listed=()):
        self.interfaces = set(interfaces)
        self.listed = list(listed)      # shortcut ids ListShortcuts reports
        self.sent = []
        self.signals = {}               # member -> callback
        self.consents = []              # (message, on_response)
        self.fail = False
        self.restore_seen = None

    def has_interface(self, interface):
        return interface in self.interfaces

    def subscribe_signal(self, rule, callback):
        self.signals[rule.header_fields["member"]] = callback
        return lambda: None

    def call(self, message, timeout=5.0):
        if self.fail:
            raise PortalError("no reply")
        self.sent.append(message)
        return ()

    def bounded_request(self, build, timeout=5.0):
        message = build("tok")
        member = message.header.fields[3]
        self.sent.append(message)
        if member == "CreateSession":
            handle = message.body[0]["session_handle_token"][1]
            return 0, {"session_handle": f"/session/{handle}"}
        if member == "SelectDevices":
            self.restore_seen = message.body[1].get("restore_token")
            return 0, {}
        if member == "ListShortcuts":
            return 0, {"shortcuts": [
                (sid, {"trigger_description": ("s", "Ctrl+Meta+Space")})
                for sid in self.listed]}
        raise AssertionError(member)

    def consent_request(self, build, on_response=None):
        message = build("tok")
        self.sent.append(message)
        self.consents.append((message, on_response))


def settle(grants):
    """Workers are threads; wait for those spawned so far."""
    for thread in threading.enumerate():
        if thread.name.startswith("cadent-portal"):
            thread.join(2)


@pytest.fixture
def grants(tmp_path):
    portal = FakePortal()
    return input_mod.PortalInput(
        portal, input_mod.TokenStore(tmp_path / "portal-tokens.json"),
        clock=lambda: 1e9)


# ---- pure helpers -------------------------------------------------------------


def test_chords_become_freedesktop_triggers():
    assert input_mod.trigger_for("<ctrl>+<cmd>+space") == "CTRL+LOGO+space"
    assert input_mod.trigger_for("<ctrl>+<cmd>+c") == "CTRL+LOGO+c"
    assert input_mod.trigger_for("<rctrl>+f9") == "CTRL+F9"


def test_a_chord_stands_for_left_hand_keysyms_in_order():
    assert input_mod.keys_for("<ctrl>+<cmd>+space") == [0xFFE3, 0xFFEB, 0x20]


def test_text_maps_to_keysyms():
    assert input_mod.keysym_for(ord("a")) == ord("a")
    assert input_mod.keysym_for(0x0A) == input_mod.KEYSYM_RETURN
    assert input_mod.keysym_for(ord("é")) == 0xE9
    assert input_mod.keysym_for(0x1F600) == 0x01000000 + 0x1F600
    assert input_mod.codepoints([0xD83D, 0xDE00, 0x61]) == [0x1F600, 0x61]


def test_a_modifier_only_chord_gives_way_to_the_default_on_the_wayland_tiers():
    wayland_caps = capabilities_for(detect_session(WAYLAND_KDE))
    assert hotkey_mod.runnable_combo(
        "<ctrl>+<cmd>", wayland_caps.default_combo, wayland_caps) == \
        "<ctrl>+<cmd>+space"
    assert hotkey_mod.runnable_combo(
        "<ctrl>+f9", wayland_caps.default_combo, wayland_caps) == "<ctrl>+f9"
    whole = capabilities_for(detect_session(
        {"XDG_SESSION_TYPE": "x11"}))
    assert hotkey_mod.runnable_combo("<ctrl>+<cmd>", "x", whole) == "<ctrl>+<cmd>"


# ---- binding and synthesizing the chords -------------------------------------


def start_tap(grants, chords=("<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c")):
    from cadent.platform.linux import LinuxHotkeyTap

    events = []
    tap = LinuxHotkeyTap(grants._portal, grants)
    tap.start(lambda key, down, injected: events.append((key, down, injected)),
              chords=chords)
    settle(grants)
    return tap, events


def test_both_chords_are_bound_in_one_call_when_a_grant_exists(grants, tmp_path):
    grants._tokens.put(input_mod.TOKEN_KEY, "held")
    start_tap(grants)
    bind = [m for m, _ in grants._portal.consents
            if m.header.fields[3] == "BindShortcuts"]
    assert len(bind) == 1
    assert [s[0] for s in bind[0].body[1]] == ["dictate", "cleanup"]
    assert grants._portal.restore_seen == ("s", "held")


def test_no_dialog_is_raised_at_start_without_a_prior_grant(grants):
    start_tap(grants)
    assert grants._portal.consents == []


def test_activated_and_deactivated_synthesize_the_chords_keys(grants):
    tap, events = start_tap(grants)
    grants._portal.signals["Activated"](
        type("M", (), {"body": ("/s", "dictate", 0, {})}))
    grants._portal.signals["Deactivated"](
        type("M", (), {"body": ("/s", "dictate", 0, {})}))
    assert events == [(0xFFE3, True, False), (0xFFEB, True, False),
                      (0x20, True, False), (0x20, False, False),
                      (0xFFEB, False, False), (0xFFE3, False, False)]


def test_the_cleanup_shortcut_is_its_own_chord(grants):
    _tap, events = start_tap(grants)
    grants._portal.signals["Activated"](
        type("M", (), {"body": ("/s", "cleanup", 0, {})}))
    assert events[-1] == (ord("c"), True, False)


# ---- the grant ---------------------------------------------------------------


def test_request_permission_raises_both_dialogs_and_never_awaits_them(grants):
    start_tap(grants)
    grants.request_permission()
    settle(grants)
    members = [m.header.fields[3] for m, _ in grants._portal.consents]
    assert members == ["BindShortcuts", "Start"]


def test_a_denial_leaves_the_grant_false_and_does_not_retry(grants):
    start_tap(grants)
    grants.request_permission()
    settle(grants)
    for _message, respond in list(grants._portal.consents):
        respond(1, {})
    settle(grants)
    assert grants.granted() is False
    settle(grants)
    assert len(grants._portal.consents) == 2    # nothing asked again


def test_the_grant_needs_the_shortcut_listed_and_a_live_input_session(grants):
    portal = grants._portal
    start_tap(grants)
    grants.request_permission()
    settle(grants)
    start = next(r for m, r in portal.consents if m.header.fields[3] == "Start")
    start(0, {"restore_token": "fresh"})
    settle(grants)
    grants._checked_at = float("-inf")
    assert grants.granted() is False        # ListShortcuts reports nothing
    settle(grants)

    portal.listed = ["dictate"]
    grants._checked_at = float("-inf")
    grants.granted()
    settle(grants)
    assert grants.granted() is True
    assert grants._tokens.get(input_mod.TOKEN_KEY) == "fresh"

    portal.listed = []                      # unbound in the compositor dialog
    grants._checked_at = float("-inf")
    grants.granted()
    settle(grants)
    assert grants.granted() is False


def test_the_restore_token_lives_in_its_own_file_and_is_private(tmp_path):
    store = input_mod.TokenStore(tmp_path / "portal-tokens.json")
    assert store.get("remote_desktop") is None
    store.put("remote_desktop", "abc")
    assert store.get("remote_desktop") == "abc"
    assert (tmp_path / "portal-tokens.json").stat().st_mode & 0o077 == 0
    (tmp_path / "portal-tokens.json").write_text("not json")
    assert store.get("remote_desktop") is None


def test_a_portal_restart_is_recovered_once_then_admitted(grants):
    portal = grants._portal
    grants._tokens.put(input_mod.TOKEN_KEY, "held")
    start_tap(grants)
    grants.request_permission()
    settle(grants)
    start = next(r for m, r in portal.consents if m.header.fields[3] == "Start")
    start(0, {})
    assert grants._input_session is not None

    before = len(portal.consents)
    grants._on_closed("input")
    settle(grants)
    assert len(portal.consents) > before    # re-created with the token
    assert portal.restore_seen == ("s", "held")

    after = len(portal.consents)
    grants._on_closed("input")              # closed again before it came back
    settle(grants)
    assert len(portal.consents) == after
    assert grants._input_session is None


# ---- typing --------------------------------------------------------------------


def test_text_is_typed_as_keysym_presses_and_releases(grants):
    from cadent.platform.linux import LinuxKeyboard

    grants._input_session = "/session/cadent_input"
    keyboard = LinuxKeyboard(grants._portal, grants)
    assert keyboard.send_text_units([ord("h"), ord("é")]) is True
    notes = [m.body[2:] for m in grants._portal.sent
             if m.header.fields[3] == "NotifyKeyboardKeysym"]
    assert notes == [(ord("h"), 1), (ord("h"), 0), (0xE9, 1), (0xE9, 0)]


def test_typing_without_a_live_session_fails_the_rung_rather_than_raising(grants):
    from cadent.platform.linux import LinuxKeyboard

    keyboard = LinuxKeyboard(grants._portal, grants)
    assert keyboard.send_text_units([ord("x")]) is False
    grants._input_session = "/session/x"
    grants._portal.fail = True
    assert keyboard.send_text_units([ord("x")]) is False


# ---- the missing interface ---------------------------------------------------


def test_stock_sway_disarms_the_hotkey_without_asking_for_a_permission():
    plat = create(WAYLAND_SWAY, portal=FakePortal(interfaces=()),
                  windows=lambda: None)
    caps = plat.capabilities
    assert caps.hotkey_available is False
    assert caps.permission is None          # never alongside the fault
    assert "doesn't let apps listen" in caps.hotkey_note
    assert plat.focused_app.permission_granted() is True
    assert plat.hotkey_tap.bindings() == {}


def test_plasma_binds_and_keys_identity_on_the_compositors_toplevels():
    tracker = object()
    plat = create(WAYLAND_KDE, portal=FakePortal(), windows=lambda: tracker)
    assert plat.capabilities.hotkey_available is True
    assert plat.capabilities.permission is not None
    assert plat.capabilities.per_app_overrides is True


def test_no_toplevel_protocol_drops_per_app_overrides_for_the_run():
    plat = create(WAYLAND_KDE, portal=FakePortal(), windows=lambda: None)
    assert plat.capabilities.per_app_overrides is False
    assert plat.focused_app.name() == "unknown"


# ---- plasma-window-management ------------------------------------------------


def test_the_wire_format_round_trips():
    data = wayland.pack(4, 1, 7, "app", 3)
    [(obj, opcode, payload)] = wayland.Messages().feed(data)
    assert (obj, opcode) == (4, 1)
    assert wayland.unpack_string(payload, 4)[0] == "app"
    messages = wayland.Messages()
    assert messages.feed(data[:5]) == [] and len(messages.feed(data[5:])) == 1


def test_the_focused_app_id_follows_the_active_window():
    sent = []
    windows = wayland.PlasmaWindows(sent.append)
    windows.hello()
    registry, sync = windows._registry, windows._sync
    windows.feed(wayland.pack(registry, 0, 9, wayland.INTERFACE, 16))
    windows.feed(wayland.pack(sync, 0))
    assert windows.offered
    bind = sent[-1]
    assert wayland.INTERFACE.encode() in bind
    manager = windows._manager

    windows.feed(wayland.pack(manager, 1, 100))     # a window appears
    window = windows._next_id - 1
    windows.feed(wayland.pack(window, 1, "org.kde.dolphin"))
    assert windows.focused_app_id is None           # not active yet
    windows.feed(wayland.pack(window, 2, wayland.STATE_ACTIVE))
    assert windows.focused_app_id == "org.kde.dolphin"
    windows.feed(wayland.pack(window, 2, 0))
    assert windows.focused_app_id is None
    windows.feed(wayland.pack(window, 2, wayland.STATE_ACTIVE))
    windows.feed(wayland.pack(window, 5))           # unmapped
    assert windows.focused_app_id is None


def test_connect_against_a_compositor_that_offers_the_protocol(tmp_path):
    path = str(tmp_path / "wayland-test")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(1)

    def compositor():
        conn, _ = server.accept()
        conn.recv(4096)                             # get_registry + sync
        conn.sendall(wayland.pack(2, 0, 9, wayland.INTERFACE, 4)
                     + wayland.pack(3, 0))
        conn.recv(4096)                             # the bind
        conn.close()

    thread = threading.Thread(target=compositor, daemon=True)
    thread.start()
    tracker = wayland.connect({"WAYLAND_DISPLAY": path})
    thread.join(2)
    server.close()
    assert tracker is not None and tracker.offered


def test_connect_is_none_without_a_compositor_or_the_global(tmp_path):
    assert wayland.connect({}) is None
    assert wayland.connect({"WAYLAND_DISPLAY": str(tmp_path / "absent")}) is None
    path = str(tmp_path / "plain")
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(1)

    def compositor():
        conn, _ = server.accept()
        conn.recv(4096)
        conn.sendall(wayland.pack(2, 0, 1, "wl_compositor", 4) + wayland.pack(3, 0))
        conn.close()

    thread = threading.Thread(target=compositor, daemon=True)
    thread.start()
    assert wayland.connect({"WAYLAND_DISPLAY": path}) is None
    thread.join(2)
    server.close()


def test_the_linux_keycode_table_is_what_the_synthesis_parses():
    assert LINUX_KEYCODES.groups["<ctrl>"] == frozenset({0xFFE3, 0xFFE4})
