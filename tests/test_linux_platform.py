"""The Linux skeleton (spec §1–2, ADR 0013): tier detection from the
environment, the Capabilities columns, and the portal plumbing against a fake
router. No bus, no portal, no compositor — these run on the headless
ubuntu-24.04 leg and skip anywhere jeepney is absent."""

import dataclasses
import logging
import sys
import types

import pytest

pytest.importorskip("jeepney")

from jeepney import MessageType, new_signal  # noqa: E402
from jeepney.io.common import FilterHandle, MessageFilters  # noqa: E402

from cadent import chord, config  # noqa: E402
from cadent.platform.keycodes import LINUX_KEYCODES  # noqa: E402
from cadent.platform.linux import (  # noqa: E402
    capabilities_for,
    create,
    detect_session,
    tier_summary,
)
from cadent.platform.linux import portal as portal_mod  # noqa: E402
from cadent.platform.linux.portal import Portal, PortalError  # noqa: E402

X11_KDE = {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}
WAYLAND_KDE = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}
WAYLAND_GNOME = {"XDG_SESSION_TYPE": "wayland",
                 "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
WAYLAND_SWAY = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "sway"}


# ---- tier detection ---------------------------------------------------------


@pytest.mark.parametrize("env,tier", [
    (X11_KDE, "whole"),
    ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "GNOME"}, "whole"),
    (WAYLAND_KDE, "portal"),
    (WAYLAND_SWAY, "portal"),
    (WAYLAND_GNOME, "reduced"),
    ({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}, "reduced"),
    ({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}, "portal"),
    ({"DISPLAY": ":0"}, "whole"),
    ({}, "whole"),
])
def test_the_tier_follows_session_type_and_desktop(env, tier):
    assert detect_session(env).tier == tier
    assert create(env).capabilities.support_tier == tier


def test_the_tier_line_names_the_session_and_what_it_means():
    assert tier_summary(detect_session(X11_KDE)) == (
        "X11 session on KDE Plasma — Whole support: everything Cadent does "
        "works here.")
    assert "Portal support" in tier_summary(detect_session(WAYLAND_KDE))
    assert tier_summary(detect_session(WAYLAND_GNOME)) == (
        "Wayland session on GNOME — Reduced support: no overlay, no per-app "
        "overrides.")


def test_an_unknown_desktop_is_not_named_rather_than_echoed():
    line = tier_summary(detect_session(
        {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "WeirdWM"}))
    assert line.startswith("X11 session — Whole")
    assert "WeirdWM" not in line


# ---- the Capabilities columns ------------------------------------------------


def caps(env):
    return capabilities_for(detect_session(env))


def test_whole_is_windows_parity_with_no_grant():
    c = caps(X11_KDE)
    assert (c.default_injection_strategy, c.injection_rungs) == \
        ("type", ("type", "paste"))
    assert c.auto_learn_overrides and c.per_app_overrides
    assert c.overlay == "windowed" and c.permission is None
    assert c.default_combo == config.Config().hotkey
    assert c.default_cleanup_combo == config.Config().cleanup_hotkey


def test_portal_types_until_a_paste_mechanism_exists_and_has_no_overlay():
    c = caps(WAYLAND_KDE)
    assert (c.default_injection_strategy, c.injection_rungs) == \
        ("type", ("type",))
    assert not c.auto_learn_overrides and c.per_app_overrides
    assert c.overlay is None and c.permission.name == "portal"
    assert (c.default_combo, c.default_cleanup_combo) == \
        ("<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c")


def test_reduced_differs_from_portal_only_in_per_app_overrides():
    portal_caps, reduced = caps(WAYLAND_KDE), caps(WAYLAND_GNOME)
    differing = {f.name for f in dataclasses.fields(portal_caps)
                 if getattr(portal_caps, f.name) != getattr(reduced, f.name)}
    assert differing == {"per_app_overrides", "support_tier",
                         "support_tier_summary"}
    assert portal_caps.per_app_overrides and not reduced.per_app_overrides


def test_every_tier_shares_the_linux_facts():
    for env in (X11_KDE, WAYLAND_KDE, WAYLAND_GNOME):
        c = caps(env)
        assert c.keycode_table is LINUX_KEYCODES
        assert c.default_overrides == ()
        assert c.stt_runtimes["parakeet"] == ("auto", "cuda", "cpu")
        assert c.gpu_only_engines == frozenset()
        assert c.app_picker and not c.tray_click_toggles_pause
        assert c.modifier_captions["cmd"] == "Super"


def test_the_keysym_table_parses_both_default_chords_on_every_tier():
    for env in (X11_KDE, WAYLAND_KDE):
        c = caps(env)
        for combo in (c.default_combo, c.default_cleanup_combo):
            assert chord.parse_combo(combo, LINUX_KEYCODES)
    assert chord.parse_combo("<cmd>+space", LINUX_KEYCODES)[-1] == frozenset({0x20})
    assert chord.parse_combo("<rctrl>", LINUX_KEYCODES) == [frozenset({0xFFE4})]
    with pytest.raises(ValueError):
        chord.parse_combo("<ctrl>+é", LINUX_KEYCODES)


@pytest.mark.skipif(sys.platform != "linux", reason="the factory picks by OS")
def test_current_returns_the_linux_platform_on_linux(monkeypatch):
    from cadent import platform as platform_pkg

    for key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(platform_pkg, "_current", None)
    assert platform_pkg.current().capabilities.support_tier == "reduced"


def test_every_wayland_seam_adapter_is_handed_the_one_connection():
    class Probed(Portal):
        """Answers the one startup probe (spec §1.3) without a bus."""

        def has_interface(self, interface: str) -> bool:
            return True

    portal = Probed(connect=lambda: pytest.fail("only the probe may ask the bus"))
    plat = create(WAYLAND_KDE, portal=portal, windows=lambda: None,
                  connect=lambda environ: None)
    for adapter in (plat.keyboard, plat.clipboard, plat.focused_app,
                    plat.hotkey_tap, plat.hardware, plat.autostart,
                    plat.single_instance, plat.desktop):
        assert adapter._portal is portal


def test_the_whole_tier_starts_without_opening_the_bus():
    """X11 talks to the server for input, so only the seams that still go
    through the portal hold the connection — and none of them opens it."""
    portal = Portal(connect=lambda: pytest.fail("startup must not open the bus"))
    plat = create(X11_KDE, portal=portal)
    for adapter in (plat.hardware, plat.autostart, plat.single_instance,
                    plat.desktop):
        assert adapter._portal is portal


# ---- portal: message building -------------------------------------------------


def test_messages_carry_explicit_signatures():
    token = "cadent_1"
    msg = portal_mod.create_session_message(
        portal_mod.GLOBAL_SHORTCUTS, token, "cadent_s")
    assert msg.header.fields[3] == "CreateSession"
    assert msg.header.fields[8] == "a{sv}"
    assert msg.body[0]["handle_token"] == ("s", token)

    bind = portal_mod.bind_shortcuts_message(
        "/session", [("dictate", "Dictate", "CTRL+LOGO+space")], token)
    assert bind.header.fields[8] == "oa(sa{sv})sa{sv}"
    assert bind.body[1] == [("dictate", {
        "description": ("s", "Dictate"),
        "preferred_trigger": ("s", "CTRL+LOGO+space")})]

    notify = portal_mod.notify_keysym_message("/session", 0x20, True)
    assert notify.header.fields[8] == "oa{sv}iu"
    assert notify.body == ("/session", {}, 0x20, 1)

    assert portal_mod.start_remote_desktop_message("/s", token) \
        .header.fields[8] == "osa{sv}"
    assert portal_mod.read_all_message(["org.freedesktop.appearance"]) \
        .header.fields[8] == "as"
    assert portal_mod.set_selection_message("/s", ["text/plain"]) \
        .body[1] == {"mime_types": ("as", ["text/plain"])}
    assert portal_mod.list_shortcuts_message("/s", token).header.fields[8] == "oa{sv}"
    assert portal_mod.request_clipboard_message("/s").header.fields[8] == "oa{sv}"
    keyboard = portal_mod.select_keyboard_message("/s", token, "restore")
    assert keyboard.body[1]["restore_token"] == ("s", "restore")


def test_request_paths_mangle_the_unique_name():
    assert portal_mod.sender_token(":1.42") == "1_42"
    assert portal_mod.request_path(":1.42", "cadent_3") == \
        "/org/freedesktop/portal/desktop/request/1_42/cadent_3"


# ---- portal: the Request handshake against a fake -----------------------------


class FakeRouter:
    """Shaped like jeepney's DBusRouter: records sends, hands out filters."""

    unique_name = ":1.42"

    def __init__(self, handle=None, error=False):
        self.sent = []
        self.filters = MessageFilters()
        self.handle = handle
        self.error = error
        self.filter_count_at_send = []

    def filter(self, rule, *, queue=None, bufsize=1):
        return FilterHandle(self.filters, rule, queue)

    def send_and_get_reply(self, msg, *, timeout=None):
        self.sent.append(msg)
        self.filter_count_at_send.append(len(self.filters.filters))
        kind = (MessageType.error if self.error
                else MessageType.method_return)
        body = (self.handle or self._expected(),) \
            if msg.header.fields[3] != "AddMatch" else ()
        return types.SimpleNamespace(
            header=types.SimpleNamespace(message_type=kind), body=body)

    def _expected(self):
        return portal_mod.request_path(self.unique_name, "cadent_1")

    def respond(self, path, code=0, results=None):
        signal = new_signal(
            portal_mod.DBusAddress(path, interface=portal_mod.REQUEST_IFACE),
            "Response", "ua{sv}", (code, results or {}))
        for handle in list(self.filters.matches(signal)):
            handle.queue.put_nowait(signal)


def build(token):
    return portal_mod.list_shortcuts_message("/session", token)


def test_a_request_subscribes_before_it_sends_and_checks_the_handle():
    router = FakeRouter()
    portal = Portal(connect=lambda: router)
    got = []
    portal.consent_request(build, lambda code, results: got.append((code, results)))

    assert [m.header.fields[3] for m in router.sent] == ["AddMatch", "ListShortcuts"]
    assert router.filter_count_at_send == [1, 1]    # filter was live for both
    rule = list(router.filters.filters.values())[0].rule
    assert rule.header_fields["path"] == router._expected()
    assert got == []                                # never awaited

    router.respond(router._expected(), 0, {"shortcuts": ("a(sa{sv})", [])})
    assert got == [(0, {"shortcuts": []})]


def test_a_handle_the_portal_renamed_is_resubscribed():
    renamed = "/org/freedesktop/portal/desktop/request/1_42/other"
    router = FakeRouter(handle=renamed)
    portal = Portal(connect=lambda: router)
    got = []
    portal.consent_request(build, lambda code, results: got.append(code))
    router.respond(renamed, 1)
    assert got == [1]


def test_a_bounded_request_times_out_without_a_response():
    portal = Portal(connect=FakeRouter)
    with pytest.raises(PortalError):
        portal.bounded_request(build, timeout=0.05)


def test_a_bounded_request_returns_the_response_that_arrives():
    router = FakeRouter()
    portal = Portal(connect=lambda: router)
    original = router.send_and_get_reply

    def answer_after_send(msg, *, timeout=None):
        reply = original(msg, timeout=timeout)
        if msg.header.fields[3] == "ListShortcuts":
            router.respond(router._expected(), 0, {"ok": ("b", True)})
        return reply

    router.send_and_get_reply = answer_after_send
    assert portal.bounded_request(build) == (0, {"ok": True})


def test_an_error_reply_fails_the_request_and_leaves_no_subscription():
    router = FakeRouter(error=True)
    portal = Portal(connect=lambda: router)
    with pytest.raises(PortalError):
        portal.consent_request(build)
    assert portal._live == {}


def test_no_bus_is_a_portal_error_not_a_crash():
    def refuse():
        raise OSError("no bus")

    with pytest.raises(PortalError):
        Portal(connect=refuse).call(build("t"))


def test_blocking_the_gui_thread_is_logged_not_asserted(caplog):
    router = FakeRouter()
    portal = Portal(connect=lambda: router)
    with caplog.at_level(logging.WARNING, logger=portal_mod.log.name):
        portal.call(build("t"))
    assert "blocks the GUI thread" in caplog.text


def test_the_bounded_timeout_is_five_seconds():
    assert portal_mod.BOUNDED_TIMEOUT == 5.0
