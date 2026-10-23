"""The portal plumbing against a fake bus (spec §2.1, §2.2; ADR 0013): the
Request dance, the two timeout classes, the sessions' grant state, the one
silent recovery after `Closed`, and the tap that turns Activated into
keysyms. jeepney's I/O-free core lets every message be built and parsed
without a socket."""

import threading
import time

import pytest

pytest.importorskip("jeepney", reason="jeepney is a Linux-only dependency")
from jeepney import (  # noqa: E402
    DBusAddress,  # noqa: E402
    HeaderFields,
    new_method_return,
    new_signal,
)

from cadent.platform.linux import portal as P  # noqa: E402
from cadent.platform.linux import wayland  # noqa: E402


class FakeBus:
    """A `Transport`: answers method calls from a table and lets a test emit
    signals as the portal would."""

    unique_name = ":1.42"

    def __init__(self):
        self.sent = []
        self.replies = {}        # member → callable(msg) -> body tuple, or Exception
        self.queue = None
        self.closed = False
        self.interfaces = {"GlobalShortcuts", "RemoteDesktop", "Clipboard", "Settings"}

    def send(self, message, *, serial=None):
        self.sent.append(message)

    def send_and_get_reply(self, message, *, timeout=None):
        self.sent.append(message)
        member = message.header.fields.get(HeaderFields.member)
        if member == "Get":
            interface = message.body[0].rsplit(".", 1)[-1]
            if interface in self.interfaces:
                return new_method_return(message, "v", (("u", 2),))
            raise RuntimeError("no such interface")
        if member in ("AddMatch", "RemoveMatch", "Register"):
            return new_method_return(message, "", ())
        reply = self.replies.get(member)
        if reply is None:
            raise RuntimeError(f"unexpected call {member}")
        if isinstance(reply, Exception):
            raise reply
        body, signature = reply(message)
        return new_method_return(message, signature, body)

    def filter(self, rule, *, queue=None, bufsize=1):
        self.queue = queue
        return _Handle()

    def close(self):
        self.closed = True

    # ---- what a test does to the app ------------------------------------------

    def emit(self, path, interface, member, signature, body):
        msg = new_signal(DBusAddress(path, interface=interface), member, signature, body)
        msg.header.fields[HeaderFields.sender] = "org.freedesktop.portal.Desktop"
        self.queue.put(msg)

    def respond(self, handle, code, results):
        self.emit(handle, P.REQUEST_IFACE, "Response", "ua{sv}", (code, results))

    def last(self, member):
        return next(m for m in reversed(self.sent)
                    if m.header.fields.get(HeaderFields.member) == member)


class _Handle:
    def close(self):
        pass


def request_handle(message):
    """The handle the portal would mint for this Request, from its token."""
    options = message.body[-1] if isinstance(message.body[-1], dict) else message.body[0]
    token = options["handle_token"][1]
    return f"{P.PORTAL_PATH}/request/1_42/{token}"


def handle_reply(message):
    return (request_handle(message),), "o"


@pytest.fixture
def bus():
    return FakeBus()


@pytest.fixture
def conn(bus):
    c = P.PortalConnection(bus, thread_name="test-portal")
    yield c
    c.close()


def settle():
    time.sleep(0.05)


# ---- the Request dance --------------------------------------------------------

def test_request_paths_derive_from_the_unique_name(conn):
    assert conn.sender == "1_42"
    assert conn.request_path("tok") == "/org/freedesktop/portal/desktop/request/1_42/tok"
    assert conn.session_path("tok") == "/org/freedesktop/portal/desktop/session/1_42/tok"


def test_a_bounded_request_waits_for_its_response_and_unwraps_it(bus, conn):
    bus.replies["ListShortcuts"] = handle_reply

    def answer():
        settle()
        bus.respond(request_handle(bus.last("ListShortcuts")), 0,
                    {"shortcuts": ("a(sa{sv})", [("dictate", {"trigger_description":
                                                              ("s", "Ctrl+Meta+Space")})])})

    threading.Thread(target=answer, daemon=True).start()
    response = conn.request("GlobalShortcuts", "ListShortcuts", "oa{sv}",
                            lambda token: ("/s", P.vardict(handle_token=("s", token))),
                            wait=True, timeout=2)
    assert response.ok
    assert response.results["shortcuts"][0][1]["trigger_description"] == "Ctrl+Meta+Space"


def test_a_bounded_request_that_never_answers_times_out(bus, conn):
    bus.replies["ListShortcuts"] = handle_reply
    with pytest.raises(P.PortalTimeout):
        conn.request("GlobalShortcuts", "ListShortcuts", "oa{sv}",
                     lambda token: ("/s", P.vardict(handle_token=("s", token))),
                     wait=True, timeout=0.1)


def test_a_consent_request_returns_at_once_and_delivers_later(bus, conn):
    """BindShortcuts raises a dialog: never awaited, the outcome arrives on
    the dispatch thread whenever the human answers."""
    bus.replies["BindShortcuts"] = handle_reply
    answers = []
    pending = conn.request("GlobalShortcuts", "BindShortcuts", "oa(sa{sv})sa{sv}",
                           lambda token: ("/s", [], "", P.vardict(handle_token=("s", token))),
                           wait=False, on_response=answers.append)
    assert isinstance(pending, P.PendingRequest)
    assert answers == []
    bus.respond(pending.handle, 1, {})
    settle()
    assert [a.code for a in answers] == [1]
    assert threading.current_thread().name != "test-portal"


def test_the_watch_moves_when_an_older_portal_picks_its_own_handle(bus, conn):
    bus.replies["ListShortcuts"] = lambda m: (("/org/freedesktop/portal/desktop/request/1_42/elsewhere",), "o")

    def answer():
        settle()
        bus.respond("/org/freedesktop/portal/desktop/request/1_42/elsewhere", 0, {})

    threading.Thread(target=answer, daemon=True).start()
    assert conn.request("GlobalShortcuts", "ListShortcuts", "oa{sv}",
                        lambda token: ("/s", P.vardict(handle_token=("s", token))),
                        wait=True, timeout=2).ok


def test_a_dbus_error_is_a_portal_error_not_a_hang(bus, conn):
    bus.replies["ListShortcuts"] = RuntimeError("NotAllowed")
    with pytest.raises(P.PortalError):
        conn.request("GlobalShortcuts", "ListShortcuts", "oa{sv}",
                     lambda token: ("/s", {}), wait=True)


def test_interface_probes_answer_from_the_version_property(bus, conn):
    assert conn.interface_available("GlobalShortcuts") is True
    bus.interfaces.discard("GlobalShortcuts")
    assert conn.interface_available("GlobalShortcuts") is True     # cached once probed
    assert conn.interface_available("Nope") is False


def test_messages_carry_explicit_signatures():
    msg = P.global_shortcuts_bind("/s", [("dictate", "Dictate", "CTRL+LOGO+space")], "tok")
    assert msg.header.fields[HeaderFields.signature] == "oa(sa{sv})sa{sv}"
    assert msg.body[1][0][1]["preferred_trigger"] == ("s", "CTRL+LOGO+space")
    assert P.remote_desktop_notify_keysym("/s", 0x61, True).body == ("/s", {}, 0x61, 1)
    assert P.clipboard_set_selection("/s", ["text/plain"]).body[1]["mime_types"] == \
        ("as", ["text/plain"])
    sel = P.remote_desktop_select_devices("/s", "tok", None)
    assert "restore_token" not in sel.body[1]
    assert sel.body[1]["persist_mode"] == ("u", P.PERSIST_UNTIL_REVOKED)


# ---- the sessions: grant state, recovery, the tap (ADR 0008, ADR 0012) ----------

def session_reply(sessions_bus, member, session_token_holder):
    def reply(message):
        return (request_handle(message),), "o"
    return reply


@pytest.fixture
def sessions(bus, conn, tmp_path):
    for member in ("CreateSession", "ListShortcuts", "BindShortcuts", "SelectDevices",
                   "Start"):
        bus.replies[member] = handle_reply
    bus.replies["RequestClipboard"] = lambda m: ((), "")
    bus.replies["NotifyKeyboardKeysym"] = lambda m: ((), "")
    s = wayland.PortalSessions(conn, wayland.TokenStore(tmp_path / "portal-tokens.json"),
                               needs_remote_desktop=True, wants_clipboard=True)
    yield s
    s.close()


def answer_requests(bus, plan):
    """Answer each listed Request as soon as it is sent, in order."""
    def run():
        for member, code, results in plan:
            sent = []
            for _ in range(500):
                sent = [m for m in bus.sent
                        if m.header.fields.get(HeaderFields.member) == member]
                if sent:
                    break
                time.sleep(0.01)
            if not sent:
                return      # the test's own assertion says what went missing
            settle()
            bus.respond(request_handle(sent[-1]), code, results)
            bus.sent = [m for m in bus.sent if m is not sent[-1]]
    threading.Thread(target=run, daemon=True).start()


def test_nothing_is_granted_until_both_portals_answer(bus, sessions):
    tap = wayland.PortalHotkeyTap(sessions, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [])})])
    tap.start(lambda *a: None, chords=("<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c"))
    assert sessions.gs_session == "/gs"
    assert sessions.granted() is False
    assert tap.bindings() == {}


def test_request_permission_binds_and_starts_without_waiting(bus, sessions):
    """Both consent calls fire and return; the grant lands when the dialogs
    are answered, on the portal's signal path."""
    tap = wayland.PortalHotkeyTap(sessions, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [])})])
    tap.start(lambda *a: None, chords=("<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c"))
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/rd")}),
                          ("SelectDevices", 0, {})])
    sessions.request_permission()
    for _ in range(100):
        if any(m.header.fields.get(HeaderFields.member) == "Start" for m in bus.sent):
            break
        time.sleep(0.01)
    assert sessions.granted() is False
    bind = bus.last("BindShortcuts")
    assert [entry[0] for entry in bind.body[1]] == ["dictate", "cleanup"]
    bus.respond(request_handle(bind), 0, {"shortcuts": ("a(sa{sv})", [
        ("dictate", {"trigger_description": ("s", "Ctrl+Meta+Space")}),
        ("cleanup", {"trigger_description": ("s", "Ctrl+Meta+C")})])})
    bus.respond(request_handle(bus.last("Start")), 0, {"restore_token": ("s", "tok-1"),
                                                        "clipboard_enabled": ("b", True)})
    settle()
    assert sessions.granted() is True
    assert sessions.tokens.get("remote_desktop") == "tok-1"
    assert sessions.clipboard_enabled is True
    assert tap.bindings() == {"dictate": "Ctrl+Meta+Space", "cleanup": "Ctrl+Meta+C"}
    # RequestClipboard went out before Start, on the same session.
    members = [m.header.fields.get(HeaderFields.member) for m in bus.sent]
    assert members.index("RequestClipboard") < members.index("Start")


def test_a_denial_raises_nothing_and_retries_nothing(bus, sessions):
    tap = wayland.PortalHotkeyTap(sessions, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [])})])
    tap.start(lambda *a: None, chords=("<ctrl>+<cmd>+space",))
    sessions.needs_remote_desktop = False
    sessions.request_permission()
    settle()
    bus.respond(request_handle(bus.last("BindShortcuts")), 1, {})
    settle()
    assert sessions.granted() is False
    binds = [m for m in bus.sent if m.header.fields.get(HeaderFields.member) == "BindShortcuts"]
    assert len(binds) == 1


def test_a_modifiers_only_chord_binds_the_tier_default_and_says_so(bus, sessions):
    tap = wayland.PortalHotkeyTap(sessions, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [])})])
    tap.start(lambda *a: None, chords=("<ctrl>+<cmd>", "<ctrl>+<shift>+<alt>"))
    assert [t for _i, _d, t in sessions.shortcuts] == ["CTRL+LOGO+space", "CTRL+LOGO+c"]
    assert tap.bindings() == {"dictate": "<ctrl>+<cmd>+space", "cleanup": "<ctrl>+<cmd>+c"}


def test_activated_and_deactivated_become_the_chords_keysyms(bus, sessions):
    heard = []
    tap = wayland.PortalHotkeyTap(sessions, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [])})])
    tap.start(lambda k, down, inj: heard.append((k, down, inj)),
              chords=("<ctrl>+<cmd>+space", "<ctrl>+<cmd>+c"))
    bus.emit(P.PORTAL_PATH, "org.freedesktop.portal.GlobalShortcuts", "Activated",
             "osta{sv}", ("/gs", "dictate", 1, {}))
    bus.emit(P.PORTAL_PATH, "org.freedesktop.portal.GlobalShortcuts", "Deactivated",
             "osta{sv}", ("/gs", "dictate", 2, {}))
    settle()
    assert heard == [(0xFFE3, True, False), (0xFFEB, True, False), (0x20, True, False),
                     (0x20, False, False), (0xFFEB, False, False), (0xFFE3, False, False)]
    # Another session's shortcut is not ours.
    bus.emit(P.PORTAL_PATH, "org.freedesktop.portal.GlobalShortcuts", "Activated",
             "osta{sv}", ("/other", "dictate", 3, {}))
    settle()
    assert len(heard) == 6


def test_a_closed_session_is_recovered_once_then_admitted(bus, sessions):
    tap = wayland.PortalHotkeyTap(sessions, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [
                              ("dictate", {"trigger_description": ("s", "X")})])})])
    tap.start(lambda *a: None, chords=("<ctrl>+<cmd>+space",))
    sessions.needs_remote_desktop = False
    assert sessions.granted() is True
    # First Closed: a silent re-create, and the shortcut is still bound.
    answer_requests(bus, [("CreateSession", 0, {"session_handle": ("s", "/gs2")}),
                          ("ListShortcuts", 0, {"shortcuts": ("a(sa{sv})", [
                              ("dictate", {"trigger_description": ("s", "X")})])})])
    bus.emit("/gs", P.SESSION_IFACE, "Closed", "a{sv}", ({},))
    for _ in range(100):
        if sessions.gs_session == "/gs2":
            break
        time.sleep(0.01)
    assert sessions.gs_session == "/gs2"
    assert sessions.granted() is True
    # Second Closed: stays down, honestly.
    bus.emit("/gs2", P.SESSION_IFACE, "Closed", "a{sv}", ({},))
    settle()
    assert sessions.gs_session is None
    assert sessions.granted() is False


def test_typing_needs_a_live_session(sessions):
    keyboard = wayland.PortalKeyboard(sessions, wayland.SendGate())
    with pytest.raises(P.PortalError):
        keyboard.send_text_units([ord("a")])


def test_typing_sends_one_keysym_per_character_inside_the_gate(bus, sessions):
    gate = wayland.SendGate()
    keyboard = wayland.PortalKeyboard(sessions, gate)
    sessions.rd_session, sessions.rd_live = "/rd", True
    assert keyboard.send_text_units(list("hé".encode("utf-16-le")[::2])) is True
    sent = [m.body for m in bus.sent
            if m.header.fields.get(HeaderFields.member) == "NotifyKeyboardKeysym"]
    assert sent[:2] == [("/rd", {}, ord("h"), 1), ("/rd", {}, ord("h"), 0)]
    assert gate.active is False
    keyboard.send_chord([0xFFE3, ord("v")])
    sent = [m.body for m in bus.sent
            if m.header.fields.get(HeaderFields.member) == "NotifyKeyboardKeysym"]
    assert sent[-4:] == [("/rd", {}, 0xFFE3, 1), ("/rd", {}, ord("v"), 1),
                         ("/rd", {}, ord("v"), 0), ("/rd", {}, 0xFFE3, 0)]


def test_the_token_store_is_its_own_file(tmp_path):
    store = wayland.TokenStore(tmp_path / "portal-tokens.json")
    assert store.get("remote_desktop") is None
    store.set("remote_desktop", "abc")
    assert store.get("remote_desktop") == "abc"
    store.set("remote_desktop", None)
    assert store.get("remote_desktop") is None
    assert not (tmp_path / "config.json").exists()


def test_no_bus_means_nothing_is_available_and_nothing_raises(tmp_path):
    s = wayland.PortalSessions(None, wayland.TokenStore(tmp_path / "t.json"),
                               needs_remote_desktop=True, wants_clipboard=True)
    assert s.shortcuts_available() is False
    assert s.granted() is False
    s.request_permission()
    tap = wayland.PortalHotkeyTap(s, default_combo="<ctrl>+<cmd>+space",
                                  default_cleanup_combo="<ctrl>+<cmd>+c")
    tap.start(lambda *a: None, chords=("<ctrl>+<cmd>+space",))
    assert tap.available() is False
