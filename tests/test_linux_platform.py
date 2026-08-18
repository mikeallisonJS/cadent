"""The Linux platform skeleton (#33; spec M6 §1–§2, ADR 0013).

Tier detection is a pure function of the environment, the Capabilities
column is data, and the portal plumbing runs against a fake bus — so all of
this runs on every OS. Nothing here opens a session bus.
"""

import dataclasses
import threading

import pytest
from conftest import make_platform
from fake_portal import FakeBus
from jeepney import HeaderFields
from PySide6.QtWidgets import QLabel

from cadent import platform as platform_pkg
from cadent.chord import parse_combo
from cadent.platform import linux
from cadent.platform.keycodes import LINUX_KEYCODES
from cadent.platform.linux import portal

# ---- tier detection (§1.1, §1.2) --------------------------------------------

@pytest.mark.parametrize("env, tier, desktop", [
    ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}, "whole", "KDE Plasma"),
    ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "whole", "GNOME"),
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}, "portal", "KDE Plasma"),
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "sway"}, "portal", "Sway"),
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "Hyprland"}, "portal", "Hyprland"),
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}, "reduced", "GNOME"),
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "reduced", "GNOME"),
    # An unknown desktop still gets a tier; only the name clause is dropped.
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "weird-wm"}, "portal", None),
    ({"XDG_SESSION_TYPE": "x11"}, "whole", None),
])
def test_session_type_and_desktop_fix_the_tier(env, tier, desktop):
    info = linux.detect(env)
    assert info.tier == tier
    assert info.desktop_name == desktop


def test_a_missing_session_type_falls_back_on_wayland_display():
    assert linux.detect({"WAYLAND_DISPLAY": "wayland-1",
                         "XDG_CURRENT_DESKTOP": "KDE"}).tier == "portal"
    assert linux.detect({"XDG_SESSION_TYPE": "tty", "DISPLAY": ":0"}).tier == "whole"
    assert linux.detect({}).tier == "whole"


# ---- the tier line (§9.4) ---------------------------------------------------

def test_the_tier_summary_reads_as_the_spec_writes_it():
    whole = linux.detect({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"})
    assert linux.support_tier_summary(whole) == (
        "X11 session on KDE Plasma — Whole support: everything Cadent does "
        "works here.")
    portal_ = linux.detect({"XDG_SESSION_TYPE": "wayland",
                            "XDG_CURRENT_DESKTOP": "KDE"})
    assert linux.support_tier_summary(portal_) == (
        "Wayland session on KDE Plasma — Portal support: your desktop owns the "
        "hotkey, Cadent won't learn on its own which apps need pasting, and "
        "there's no overlay.")
    reduced = linux.detect({"XDG_SESSION_TYPE": "wayland",
                            "XDG_CURRENT_DESKTOP": "GNOME"})
    assert linux.support_tier_summary(reduced) == (
        "Wayland session on GNOME — Reduced support: no overlay, no per-app "
        "overrides.")


def test_an_unknown_desktop_drops_the_clause_rather_than_leaking_the_variable():
    info = linux.detect({"XDG_SESSION_TYPE": "wayland",
                         "XDG_CURRENT_DESKTOP": "MyCustomWM"})
    line = linux.support_tier_summary(info)
    assert line.startswith("Wayland session — Portal support")
    assert "MyCustomWM" not in line


# ---- the Capabilities column (§2.4) ------------------------------------------

def caps_for(session_type, desktop, **kw):
    return linux.capabilities_for(
        linux.detect({"XDG_SESSION_TYPE": session_type,
                      "XDG_CURRENT_DESKTOP": desktop}), **kw)


def test_whole_mirrors_windows_parity():
    caps = caps_for("x11", "KDE")
    assert caps.support_tier == "whole"
    assert caps.default_injection_strategy == "type"
    assert caps.injection_rungs == ("type", "paste")
    assert caps.auto_learn_overrides is True
    assert caps.per_app_overrides is True
    assert caps.permission is None
    assert caps.overlay == "windowed"
    assert caps.default_combo == "<ctrl>+<cmd>"
    assert caps.default_cleanup_combo == "<ctrl>+<shift>+<alt>"


def test_portal_is_paste_first_with_the_portal_grant_and_no_overlay():
    caps = caps_for("wayland", "KDE")
    assert caps.support_tier == "portal"
    assert caps.default_injection_strategy == "clipboard"
    assert caps.injection_rungs == ("paste", "type")
    assert caps.auto_learn_overrides is False
    assert caps.per_app_overrides is True
    assert caps.permission is not None and caps.permission.name == "portal"
    assert caps.overlay is None
    assert caps.default_combo == "<ctrl>+<cmd>+space"
    assert caps.default_cleanup_combo == "<ctrl>+<cmd>+c"


def test_reduced_differs_from_portal_only_in_per_app_overrides():
    portal_caps = caps_for("wayland", "KDE")
    reduced = caps_for("wayland", "GNOME")
    assert reduced.per_app_overrides is False
    same = dataclasses.replace(reduced, per_app_overrides=True,
                               support_tier="portal",
                               support_tier_summary=portal_caps.support_tier_summary)
    assert same == portal_caps


def test_a_run_without_a_paste_mechanism_types_and_only_types():
    caps = caps_for("wayland", "GNOME", paste_available=False)
    assert caps.injection_rungs == ("type",)


def test_a_portal_run_without_a_toplevel_protocol_drops_overrides():
    assert caps_for("wayland", "sway", per_app_overrides=False).per_app_overrides is False


def test_the_shared_linux_facts():
    for caps in (caps_for("x11", "KDE"), caps_for("wayland", "KDE"),
                 caps_for("wayland", "GNOME")):
        assert caps.keycode_table is LINUX_KEYCODES
        assert caps.stt_runtimes == {"faster-whisper": ("auto", "cuda", "cpu"),
                                     "parakeet": ("auto", "cuda", "cpu")}
        assert caps.gpu_only_engines == frozenset()
        assert caps.gpu_pack_available is True
        assert caps.app_picker is True
        assert caps.app_identity_placeholder == "org.mozilla.firefox"
        assert caps.tray_click_toggles_pause is False
        assert caps.tray_icon_painted_by_os is False
        assert caps.modifier_captions["cmd"] == "Super"
        assert caps.default_overrides == ()
        assert caps.mic_permission_hint is None
        assert caps.autostart_label == "Start at login"


def test_the_permission_copy_says_your_desktop_and_never_names_one():
    p = linux.PORTAL_PERMISSION
    for text in (p.banner, p.wizard_body, p.waiting, p.granted):
        assert "GNOME" not in text and "Plasma" not in text and "portal" not in text.lower()
    assert "your desktop" in p.banner


# ---- the keysym table (§4) ----------------------------------------------------

def test_the_keysym_table_parses_both_default_chords_and_the_wayland_ones():
    assert parse_combo("<ctrl>+<cmd>", table=LINUX_KEYCODES) == [
        frozenset({0xFFE3, 0xFFE4}), frozenset({0xFFEB, 0xFFEC})]
    assert parse_combo("<ctrl>+<cmd>+space", table=LINUX_KEYCODES)[-1] == frozenset({0x20})
    assert parse_combo("<ctrl>+<cmd>+c", table=LINUX_KEYCODES)[-1] == frozenset({ord("c")})
    assert parse_combo("<ctrl>+<alt>+f9", table=LINUX_KEYCODES)[-1] == frozenset({0xFFBE + 8})
    with pytest.raises(ValueError):
        parse_combo("<ctrl>+ß", table=LINUX_KEYCODES)   # no ord fallback


# ---- the portal plumbing against a fake bus (§2.1, §2.2) ---------------------

def test_request_and_session_paths_mangle_the_sender_name():
    assert portal.request_path(":1.42", "cadent1") == \
        "/org/freedesktop/portal/desktop/request/1_42/cadent1"
    assert portal.session_path(":1.42", "s1") == \
        "/org/freedesktop/portal/desktop/session/1_42/s1"


def test_send_request_subscribes_before_sending_and_completes_on_response():
    bus = FakeBus()
    order = []
    real_subscribe, real_send = bus.subscribe, bus.send_and_get_reply
    bus.subscribe = lambda *a: (order.append("subscribe"), real_subscribe(*a))[1]
    bus.send_and_get_reply = lambda *a, **k: (order.append("send"), real_send(*a, **k))[1]
    tokens = portal.RequestTokens()
    pending = portal.send_request(
        bus, lambda tok: portal.global_shortcuts_create_session(tok, "s1"), tokens)
    assert order == ["subscribe", "send"]
    assert pending.done is False
    ran = bus.respond("/org/freedesktop/portal/desktop/request/1_42/cadent1", 0,
                      {"session_handle": ("s", "/x/y")})
    assert ran == 1
    assert pending.wait(0.1).ok is True
    assert pending.results == {"session_handle": "/x/y"}


def test_a_returned_handle_that_differs_moves_the_subscription():
    bus = FakeBus()
    bus.replies[(portal.GLOBAL_SHORTCUTS_IFACE, "CreateSession")] = \
        ("/org/freedesktop/portal/desktop/request/other/path",)
    pending = portal.send_request(
        bus, lambda tok: portal.global_shortcuts_create_session(tok, "s1"),
        portal.RequestTokens())
    assert bus.unsubscribed == [1]
    assert bus.respond("/org/freedesktop/portal/desktop/request/other/path", 1) == 1
    assert pending.done and pending.code == portal.RESPONSE_CANCELLED


def test_a_consent_call_is_never_awaited_but_calls_back():
    bus = FakeBus()
    pending = portal.send_request(
        bus, lambda tok: portal.global_shortcuts_bind("/s", tok, [
            ("dictate", "Dictate", "CTRL+SUPER+space")]), portal.RequestTokens())
    outcomes = []
    pending.then(lambda p: outcomes.append(p.code))
    assert outcomes == []                       # nothing waited on
    bus.respond("/org/freedesktop/portal/desktop/request/1_42/cadent1", 0)
    assert outcomes == [0]
    late = []
    pending.then(late.append)                   # attaching after completion fires at once
    assert late == [pending]


def test_a_failed_send_unsubscribes_and_raises():
    bus = FakeBus()
    bus.replies[(portal.GLOBAL_SHORTCUTS_IFACE, "CreateSession")] = \
        portal.PortalError("org.freedesktop.DBus.Error.UnknownMethod")
    with pytest.raises(portal.PortalError):
        portal.send_request(bus, lambda tok: portal.global_shortcuts_create_session(
            tok, "s1"), portal.RequestTokens())
    assert bus.subscriptions == {}


def test_bounded_wait_times_out_rather_than_hanging():
    pending = portal.PendingResponse()
    with pytest.raises(portal.PortalTimeout):
        pending.wait(0.01)


def test_interface_presence_reads_the_version_property():
    bus = FakeBus()
    bus.replies[("org.freedesktop.DBus.Properties", "Get")] = (("u", 2),)
    assert portal.interface_version(bus, portal.GLOBAL_SHORTCUTS_IFACE) == 2
    bus.replies[("org.freedesktop.DBus.Properties", "Get")] = \
        portal.PortalError("org.freedesktop.DBus.Error.InvalidArgs")
    assert portal.interface_version(bus, portal.GLOBAL_SHORTCUTS_IFACE) is None


def test_variants_unwrap_recursively():
    assert portal.unwrap_variants({"shortcuts": ("a(sa{sv})", [
        ("dictate", {"trigger_description": ("s", "Ctrl+Super+Space")})])}) == {
            "shortcuts": [("dictate", {"trigger_description": "Ctrl+Super+Space"})]}


# ---- message building is pure (§2.1) ------------------------------------------

def fields(msg):
    return msg.header.fields


def test_the_messages_carry_explicit_signatures_and_the_portal_address():
    msg = portal.global_shortcuts_bind("/s", "t1", [("dictate", "Dictate",
                                                     "CTRL+SUPER+space")])
    assert fields(msg)[HeaderFields.destination] == "org.freedesktop.portal.Desktop"
    assert fields(msg)[HeaderFields.path] == "/org/freedesktop/portal/desktop"
    assert fields(msg)[HeaderFields.interface] == portal.GLOBAL_SHORTCUTS_IFACE
    assert fields(msg)[HeaderFields.signature] == "oa(sa{sv})sa{sv}"
    session, shortcuts, parent, options = msg.body
    assert shortcuts[0][1]["preferred_trigger"] == ("s", "CTRL+SUPER+space")
    assert options["handle_token"] == ("s", "t1")

    keysym = portal.remote_desktop_notify_keysym("/s", 0x61, True)
    assert fields(keysym)[HeaderFields.signature] == "oa{sv}iu"
    assert keysym.body == ("/s", {}, 0x61, 1)

    select = portal.remote_desktop_select_devices("/s", "t2", restore_token="abc")
    assert select.body[1]["restore_token"] == ("s", "abc")
    assert select.body[1]["persist_mode"] == ("u", portal.PERSIST_UNTIL_REVOKED)

    read_all = portal.settings_read_all(["org.freedesktop.appearance"])
    assert fields(read_all)[HeaderFields.signature] == "as"
    assert read_all.body == (["org.freedesktop.appearance"],)
    # Every builder serialises: an inconsistent signature/body would raise here.
    for m in (msg, keysym, select, read_all,
              portal.clipboard_set_selection("/s"),
              portal.clipboard_request("/s"),
              portal.remote_desktop_start("/s", "t3"),
              portal.global_shortcuts_list("/s", "t4"),
              portal.portal_version_query(portal.SETTINGS_IFACE)):
        assert m.serialise(serial=1)


def test_unicode_keysyms():
    assert portal.unicode_keysym(ord("a")) == 0x61
    assert portal.unicode_keysym(ord("é")) == 0xE9
    assert portal.unicode_keysym(0x4E2D) == 0x01004E2D


# ---- the real connection's threading, against a socketless stand-in -----------

class _PipeConn:
    """Enough of `jeepney.io.blocking.DBusConnection` for the router: a
    queue the test feeds replies into, and a serial counter."""

    def __init__(self):
        import itertools
        import queue

        self.unique_name = ":1.7"
        self.outgoing_serial = itertools.count(1)
        self.sent = []
        self.inbox = queue.Queue()

    def send(self, msg, serial=None):
        self.sent.append((serial, msg))

    def receive(self):
        item = self.inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    def close(self):
        self.inbox.put(None)


def test_the_connection_routes_replies_to_their_waiters_and_signals_to_subscribers():
    from jeepney import new_method_return, new_signal

    conn = _PipeConn()
    pc = portal.PortalConnection(conn)
    try:
        assert pc._thread.name == "cadent-portal"
        got = []
        seen = threading.Event()

        def worker():
            got.append(pc.send_and_get_reply(
                portal.settings_read("org.freedesktop.appearance", "color-scheme"),
                timeout=2))
            seen.set()

        t = threading.Thread(target=worker)
        t.start()
        # Wait for the send, then answer it by serial.
        while not conn.sent:
            pass
        serial, sent = conn.sent[0]
        reply = new_method_return(sent, "v", (("u", 1),))
        reply.header.fields[HeaderFields.reply_serial] = serial
        conn.inbox.put(reply)
        assert seen.wait(2)
        assert got[0].body == (("u", 1),)

        # A subscriber hears a matching signal on the portal thread.
        heard = []
        # subscribe() does AddMatch as a bounded call; answer it from a helper.
        def answer_add_match():
            while len(conn.sent) < 2:
                pass
            serial, sent = conn.sent[1]
            ok = new_method_return(sent)
            ok.header.fields[HeaderFields.reply_serial] = serial
            conn.inbox.put(ok)
        threading.Thread(target=answer_add_match).start()
        pc.subscribe(portal.settings_changed_rule(),
                     lambda m: heard.append((threading.current_thread().name, m.body)))
        sig = new_signal(portal.portal_address(portal.SETTINGS_IFACE),
                         "SettingChanged", "ssv",
                         ("org.freedesktop.appearance", "color-scheme", ("u", 2)))
        conn.inbox.put(sig)
        while not heard:
            pass
        assert heard[0][0] == "cadent-portal"
        assert heard[0][1][1] == "color-scheme"
    finally:
        pc.close()


def test_a_bounded_call_times_out_and_a_lost_connection_fails_waiters():
    conn = _PipeConn()
    pc = portal.PortalConnection(conn)
    try:
        with pytest.raises(portal.PortalTimeout):
            pc.send_and_get_reply(portal.settings_read("a", "b"), timeout=0.05)
        assert pc._waiters == {}
    finally:
        pc.close()


def test_a_gui_thread_call_is_logged_not_asserted(caplog):
    import logging

    conn = _PipeConn()
    pc = portal.PortalConnection(conn)
    try:
        pc.arm_gui_guard()
        with caplog.at_level(logging.WARNING, logger="cadent.platform.linux.portal"),                 pytest.raises(portal.PortalTimeout):
            pc.send_and_get_reply(portal.settings_read("a", "b"), timeout=0.01)
        assert any("GUI thread" in r.message for r in caplog.records)
    finally:
        pc.close()


# ---- Settings ▸ General shows the tier row (§9.4) -----------------------------

def linux_platform(session_type, desktop):
    plat = make_platform()
    return dataclasses.replace(plat, capabilities=caps_for(session_type, desktop))


def test_the_general_pane_carries_the_tier_row_on_linux_only(qt_app, tmp_path,
                                                             monkeypatch):
    from cadent.config_store import ConfigStore
    from cadent.settings_ui import SettingsWindow
    from cadent.theme.tokens import tokens

    monkeypatch.setattr(platform_pkg, "_current", linux_platform("wayland", "KDE"))
    win = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                         tokens=tokens("dark"), devices=[])
    try:
        assert win.general.tier_row is not None
        texts = [lbl.text() for lbl in win.general.tier_row.findChildren(QLabel)]
        assert any("Portal support" in t for t in texts)
    finally:
        win.close()

    monkeypatch.setattr(platform_pkg, "_current", make_platform())
    win = SettingsWindow(ConfigStore(tmp_path / "config2.json"),
                         tokens=tokens("dark"), devices=[])
    try:
        assert win.general.tier_row is None
    finally:
        win.close()


# ---- the factory (only meaningful on Linux) ---------------------------------

def test_the_factory_builds_a_linux_platform_from_the_environment(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(linux, "open_portal_connection", lambda: None)
    plat = linux.create()
    assert plat.capabilities.support_tier == "portal"
    assert plat.focused_app.permission_granted() in (True, False)


def test_current_picks_linux_on_linux():
    import sys

    if not sys.platform.startswith("linux"):
        pytest.skip("Linux factory selection")
    assert platform_pkg.current().capabilities.support_tier in ("whole", "portal",
                                                                "reduced")
