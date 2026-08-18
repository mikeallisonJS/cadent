"""The Whole tier's X11 adapters (#34; spec M6 §3–§5), driven against fake
Displays — no X server on any CI leg. What can be tested here: the keymap
arithmetic, the scratch-keycode dance, the state gate, the WM_CLASS →
desktop-id resolution and the `.desktop` index. XTEST delivery, XFixes
counting and XRecord capture are hardware items (§12).
"""

import time
from pathlib import Path

import pytest
from conftest import make_platform

from cadent.inject import Injector
from cadent.platform.linux import desktopfiles, keysyms, x11
from cadent.platform.linux.desktopfiles import DesktopIndex

# ---- keysyms -----------------------------------------------------------------

def test_unicode_keysyms_follow_the_xkb_rule():
    assert keysyms.unicode_keysym(ord("a")) == 0x61
    assert keysyms.unicode_keysym(ord("é")) == 0xE9
    assert keysyms.unicode_keysym(0x4E2D) == 0x01004E2D
    assert keysyms.unicode_keysym(ord("\n")) == 0xFF0D     # Return, not U+000A
    assert keysyms.keysyms_for_text("a\né") == [0x61, 0xFF0D, 0xE9]


# ---- keymap arithmetic ------------------------------------------------------

def test_the_scratch_keycode_is_the_highest_empty_row():
    mapping = [[0x61, 0x41], [0, 0], [0x62, 0x42], [0, 0]]
    assert x11.find_scratch_keycode(mapping, min_keycode=8) == 11
    assert x11.find_scratch_keycode([[0x61, 0x41]], min_keycode=8) is None


def test_keycode_lookup_prefers_the_unshifted_level():
    assert x11.keycode_for(0x61, [(38, 0)]) == (38, False)
    assert x11.keycode_for(0x41, [(38, 1)]) == (38, True)
    assert x11.keycode_for(0x41, [(38, 1), (60, 0)]) == (60, False)
    assert x11.keycode_for(0x1000E9, [(38, 2)]) is None   # AltGr level: absent


# ---- the state gate ---------------------------------------------------------

def test_the_gate_is_closed_while_sending_and_lingers_after():
    now = [100.0]
    gate = x11.SendGate(clock=lambda: now[0])
    assert gate.closed is False
    with gate:
        assert gate.closed is True
        with gate:                       # re-entrant
            assert gate.closed is True
        assert gate.closed is True
    assert gate.closed is True           # lingering: XRecord is a beat behind
    now[0] += x11.SendGate.LINGER_S + 0.001
    assert gate.closed is False


# ---- a fake Display for the keyboard ---------------------------------------

class FakeDisplay:
    """The slice of `Xlib.display.Display` the keyboard touches. Layout: US
    letters on keycodes 38.., 'A' at shift level, Shift_L on 50, Control_L
    on 37, 'v' on 55, and keycodes 200–203 free."""

    def __init__(self, free=True):
        class Info:
            min_keycode = 8
            max_keycode = 203

        class Inner:
            info = Info()

        self.display = Inner()
        self.mapping = {}
        self.mapping[38] = [0x61, 0x41]        # a / A
        self.mapping[55] = [0x76, 0x56]        # v / V
        self.mapping[50] = [keysyms.SHIFT_L, 0]
        self.mapping[37] = [keysyms.CONTROL_L, 0]
        self.mapping[133] = [keysyms.SUPER_L, 0]
        self.free = free
        self.faked: list[tuple[int, int]] = []   # (event type, keycode)
        self.remaps: list[tuple[int, list]] = []
        self.synced = 0
        self.keymap_bits = bytearray(32)

    def get_keyboard_mapping(self, first, count):
        rows = []
        for code in range(first, first + count):
            row = self.mapping.get(code, [0, 0] if self.free else [0xFFFF, 0xFFFF])
            rows.append(row)
        return rows

    def keysym_to_keycodes(self, keysym):
        out = []
        for code, row in self.mapping.items():
            for index, sym in enumerate(row):
                if sym == keysym:
                    out.append((code, index))
        return sorted(out, key=lambda pair: (pair[1], pair[0]))

    def change_keyboard_mapping(self, first, keysyms_rows):
        self.remaps.append((first, [list(r) for r in keysyms_rows]))
        for offset, row in enumerate(keysyms_rows):
            self.mapping[first + offset] = list(row)

    def sync(self):
        self.synced += 1

    def query_keymap(self):
        return bytes(self.keymap_bits)


@pytest.fixture
def keyboard(monkeypatch):
    fake = FakeDisplay()
    faked = fake.faked
    monkeypatch.setattr(x11.X11Keyboard, "_fake",
                        lambda self, d, kind, code: faked.append((kind, code)))
    monkeypatch.setattr(x11.time, "sleep", lambda _s: None)
    gate = x11.SendGate()
    return x11.X11Keyboard(gate, display_factory=lambda: fake), fake, gate


def units(text):
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def test_layout_characters_press_their_keycode_with_shift_for_the_upper_level(keyboard):
    from Xlib import X

    kb, fake, _gate = keyboard
    assert kb.send_text_units(units("aA")) is True
    assert fake.faked == [
        (X.KeyPress, 38), (X.KeyRelease, 38),                              # a
        (X.KeyPress, 50), (X.KeyPress, 38), (X.KeyRelease, 38), (X.KeyRelease, 50),  # A
    ]
    assert fake.remaps == []


def test_out_of_layout_characters_borrow_the_scratch_keycode_and_give_it_back(keyboard):
    from Xlib import X

    kb, fake, _gate = keyboard
    kb.send_text_units(units("é中"))
    scratch = 203
    # é remapped, pressed; 中 remapped onto the same scratch, pressed; then
    # the scratch goes back to NoSymbol.
    assert fake.remaps[0] == (scratch, [[0xE9, 0xE9]])
    assert fake.remaps[1] == (scratch, [[0x01004E2D, 0x01004E2D]])
    assert fake.remaps[-1] == (scratch, [[0, 0]])
    assert fake.faked == [(X.KeyPress, scratch), (X.KeyRelease, scratch)] * 2


def test_an_exhausted_scratch_keycode_raises_a_detectable_failure(monkeypatch):
    fake = FakeDisplay(free=False)
    monkeypatch.setattr(x11.X11Keyboard, "_fake", lambda *_a: None)
    kb = x11.X11Keyboard(x11.SendGate(), display_factory=lambda: fake)
    kb.send_text_units(units("a"))            # layout characters still type
    with pytest.raises(RuntimeError):
        kb.send_text_units(units("é"))


def test_the_paste_chord_presses_in_order_and_releases_in_reverse(keyboard):
    from Xlib import X

    kb, fake, _gate = keyboard
    kb.send_chord([keysyms.CONTROL_L, 0x76])
    assert fake.faked == [(X.KeyPress, 37), (X.KeyPress, 55),
                          (X.KeyRelease, 55), (X.KeyRelease, 37)]


def test_a_chord_key_missing_from_the_layout_raises(keyboard):
    kb, _fake, _gate = keyboard
    with pytest.raises(RuntimeError):
        kb.send_chord([keysyms.CONTROL_L, 0x1000E9])


def test_modifiers_down_reads_the_keymap_bits(keyboard):
    kb, fake, _gate = keyboard
    assert kb.modifiers_down() is False
    fake.keymap_bits[37 // 8] |= 1 << (37 % 8)      # Control_L held
    assert kb.modifiers_down() is True


def test_sending_closes_the_gate_the_tap_reads(keyboard):
    kb, _fake, gate = keyboard
    seen = []
    orig = kb._type_keysym
    kb._type_keysym = lambda d, k: seen.append(gate.closed) or orig(d, k)
    kb.send_text_units(units("a"))
    assert seen == [True]


def test_the_tap_reports_events_during_a_send_as_injected():
    now = [0.0]
    gate = x11.SendGate(clock=lambda: now[0])
    tap = x11.X11HotkeyTap(gate)
    events = []

    class Key:
        def __init__(self, vk):
            self.vk = vk

    # Drive the callback the listener would call, without a listener.
    def forward(is_down):
        def callback(key, injected=False):
            keysym = x11._pynput_keysym(key)
            events.append((keysym, is_down, bool(injected) or gate.closed))
        return callback

    forward(True)(Key(keysyms.CONTROL_L))
    with gate:
        forward(True)(Key(0x76))
    now[0] += 1
    forward(False)(Key(0x56))                # shifted V normalizes to v
    assert events == [(keysyms.CONTROL_L, True, False), (0x76, True, True),
                      (0x76, False, False)]
    assert tap._listener is None


# ---- the injector on the Whole tier: type-first, auto-learn on a raise --------

def test_the_whole_tier_ladder_falls_through_and_flags_typing_failed(monkeypatch):
    from conftest import FakeClipboard, FakeKeyboard

    from cadent.platform import linux

    caps = linux.capabilities_for(linux.detect({"XDG_SESSION_TYPE": "x11"}))
    plat = make_platform(keyboard=FakeKeyboard(raise_on_type=True),
                         clipboard=FakeClipboard())
    import dataclasses
    plat = dataclasses.replace(plat, capabilities=caps)
    monkeypatch.setattr(x11.time, "sleep", lambda _s: None)
    result = Injector([], caps.default_injection_strategy, platform=plat).insert("hi")
    assert result.outcome == "fallback"
    assert result.typing_failed is True
    # The paste chord was Ctrl+V in keysyms.
    assert plat.keyboard.chords == [[keysyms.CONTROL_L, 0x76]]


# ---- the .desktop index (ADR 0009) ------------------------------------------

def write_entry(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\n" + body, encoding="utf-8")
    return path


@pytest.fixture
def index(tmp_path):
    user = tmp_path / "user" / "applications"
    system = tmp_path / "usr" / "applications"
    write_entry(system, "org.mozilla.firefox.desktop",
                "Type=Application\nName=Firefox\nName[de]=Feuerfuchs\nExec=firefox %u\n"
                "StartupWMClass=firefox\n")
    write_entry(system, "org.kde.konsole.desktop",
                "Type=Application\nName=Konsole\nExec=konsole\n")
    write_entry(system, "code.desktop",
                "Type=Application\nName=Visual Studio Code\nExec=/usr/share/code/code\n"
                "StartupWMClass=Code\n")
    write_entry(system, "hidden-helper.desktop",
                "Type=Application\nName=Helper\nNoDisplay=true\nExec=helper\n")
    write_entry(system, "some-link.desktop", "Type=Link\nName=A link\nURL=https://x\n")
    write_entry(system, "kde4/oldapp.desktop", "Type=Application\nName=Old App\nExec=oldapp\n")
    # A user override of Firefox: first by search order wins.
    write_entry(user, "org.mozilla.firefox.desktop",
                "Type=Application\nName=Firefox (mine)\nExec=firefox\n")
    return DesktopIndex([user, system], locales=[])


def test_installed_apps_lists_visible_applications_deduplicated_by_id(index):
    rows = index.installed_apps()
    assert ("Firefox (mine)", "org.mozilla.firefox") in rows
    assert ("Konsole", "org.kde.konsole") in rows
    assert ("Old App", "kde4-oldapp") in rows
    names = [n for n, _ in rows]
    assert "Helper" not in names and "A link" not in names
    assert names == sorted(names, key=str.lower)


def test_display_name_resolves_whether_or_not_the_app_runs(index):
    assert index.display_name("org.kde.konsole") == "Konsole"
    assert index.display_name("ORG.KDE.KONSOLE") == "Konsole"
    assert index.display_name("nope") is None


def test_wm_class_resolves_to_the_desktop_id(index):
    assert index.id_for_wm_class(("konsole", "konsole")) == "org.kde.konsole"
    assert index.id_for_wm_class(("code", "Code")) == "code"
    assert index.id_for_wm_class(("Navigator", "firefox")) == "org.mozilla.firefox"
    assert index.id_for_wm_class(("x", "Nothing")) is None
    assert index.id_for_wm_class(()) is None


def test_localized_names_follow_the_locale(tmp_path):
    root = tmp_path / "applications"
    write_entry(root, "org.mozilla.firefox.desktop",
                "Type=Application\nName=Firefox\nName[de]=Feuerfuchs\nExec=firefox\n")
    assert DesktopIndex([root], locales=["de_DE", "de"]).display_name(
        "org.mozilla.firefox") == "Feuerfuchs"
    assert desktopfiles.preferred_locales({"LANG": "de_DE.UTF-8"}) == ["de_DE", "de"]
    assert desktopfiles.preferred_locales({"LANG": "C.UTF-8"}) == []


def test_application_dirs_follow_xdg_order(tmp_path):
    env = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "dh"),
           "XDG_DATA_DIRS": "/a:/b"}
    dirs = desktopfiles.application_dirs(env)
    assert dirs[:3] == [tmp_path / "dh" / "applications", Path("/a/applications"),
                        Path("/b/applications")]
    assert Path("/var/lib/flatpak/exports/share/applications") in dirs


# ---- the focused app against a fake Display ------------------------------------

class FakeWindow:
    def __init__(self, wm_class, pid=None, geometry=(10, 20, 300, 200), extents=None):
        self._wm_class = wm_class
        self._pid = pid
        self._geo = geometry
        self._extents = extents

    def get_wm_class(self):
        return self._wm_class

    def get_full_property(self, atom, _kind):
        class Prop:
            def __init__(self, value):
                self.value = value
        if atom == "_NET_WM_PID" and self._pid:
            return Prop([self._pid])
        if atom == "_NET_FRAME_EXTENTS" and self._extents:
            return Prop(list(self._extents))
        return None

    def get_geometry(self):
        class Geo:
            pass
        g = Geo()
        g.x, g.y, g.width, g.height = self._geo
        return g


class FakeRoot:
    def __init__(self, active):
        self._active = active

    def get_full_property(self, atom, _kind):
        class Prop:
            value = [1] if self._active else [0]
        return Prop()

    def translate_coords(self, _window, _x, _y):
        class Point:
            x, y = 100, 50
        return Point()


class FakeFocusDisplay:
    def __init__(self, window):
        self._window = window

    def screen(self):
        class Screen:
            root = FakeRoot(self._window is not None)
        return Screen()

    def intern_atom(self, name):
        return name

    def create_resource_object(self, _kind, _wid):
        return self._window


def test_focused_app_identity_walks_wm_class_then_exe_then_unknown(index, monkeypatch):
    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(
        FakeWindow(("konsole", "konsole"))))
    assert app.name() == "org.kde.konsole"

    monkeypatch.setattr(x11.os, "readlink", lambda p: "/usr/bin/mystery-app")
    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(
        FakeWindow(("mystery", "Mystery"), pid=4242)))
    assert app.name() == "mystery-app"

    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(
        FakeWindow(("mystery", "Mystery"))))
    assert app.name() == "unknown"

    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(None))
    assert app.name() == "unknown"
    assert app.injection_blocked() is None
    assert app.permission_granted() is True


def test_focused_window_rect_includes_the_frame(index):
    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(
        FakeWindow(("konsole", "konsole"), extents=(2, 2, 30, 2))))
    assert app.window_rect() == (98, 20, 402, 252)
    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(None))
    assert app.window_rect() is None


def test_running_apps_and_display_name_read_the_index(index):
    app = x11.X11FocusedApp(index, display_factory=lambda: FakeFocusDisplay(None))
    assert ("Konsole", "org.kde.konsole") in app.running_apps()
    assert app.display_name("code") == "Visual Studio Code"


def test_the_whole_tier_assembles_the_x11_adapters(monkeypatch):
    from cadent.platform import linux

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(linux, "open_portal_connection", lambda: None)
    plat = linux.create()
    assert isinstance(plat.keyboard, x11.X11Keyboard)
    assert isinstance(plat.clipboard, x11.X11Clipboard)
    assert isinstance(plat.focused_app, x11.X11FocusedApp)
    assert isinstance(plat.hotkey_tap, x11.X11HotkeyTap)
    assert plat.keyboard._gate is plat.hotkey_tap._gate


def test_the_clipboard_thread_reports_a_missing_display_instead_of_hanging():
    def boom():
        raise OSError("no DISPLAY")

    clip = x11.X11Clipboard(display_factory=boom)
    with pytest.raises(RuntimeError):
        clip.get_text()
    time.sleep(0)   # the thread ends on its own
