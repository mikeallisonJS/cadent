"""The Whole tier's adapters (spec §3–5, ADR 0007–0009): desktop-file identity
and the X11 pieces that need no server run anywhere; the rest drive a private
Xvfb and skip where there is none."""

import os
import shutil
import subprocess
import threading
import time

import pytest

pytest.importorskip("Xlib")
import Xlib.error  # noqa: E402

from cadent.inject import _utf16_units  # noqa: E402
from cadent.platform.linux import create, x11  # noqa: E402
from cadent.platform.linux import desktop_files as df  # noqa: E402


def desktop(tmp_path, rel, **fields):
    fields = {"Type": "Application", **fields}
    path = tmp_path / "applications" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\n" + "".join(f"{k}={v}\n" for k, v in fields.items())
                    + "[Desktop Action x]\nName=Not the app\n")
    return path


def index(tmp_path, home=None, **env):
    env = {"XDG_DATA_HOME": str(home or tmp_path), "XDG_DATA_DIRS": str(tmp_path / "sys"),
           "HOME": str(tmp_path / "nohome"), **env}
    return df.DesktopIndex(env)


# ---- identity -----------------------------------------------------------------


def test_wm_class_resolves_by_filename_stem_or_startup_wm_class(tmp_path):
    desktop(tmp_path, "org.mozilla.firefox.desktop", Name="Firefox")
    desktop(tmp_path, "code.desktop", Name="Code", StartupWMClass="Code-OSS")
    idx = index(tmp_path)
    assert idx.resolve(("Navigator", "org.mozilla.firefox")) == "org.mozilla.firefox"
    assert idx.resolve(("code-oss", "Code-OSS")) == "code"
    assert idx.resolve(("nothing", "Unknown")) is None


def test_the_user_directory_shadows_the_system_one_and_the_picker_lists_one_row(tmp_path):
    home, system = tmp_path / "home", tmp_path / "sys"
    desktop(home, "foo.desktop", Name="Mine")
    desktop(system, "foo.desktop", Name="Theirs")
    desktop(system, "hidden.desktop", Name="Hidden", NoDisplay="true")
    desktop(system, "gone.desktop", Name="Gone", Hidden="true")
    desktop(system, "tool.desktop", Name="Tool", Type="Link")
    idx = index(tmp_path, home=home)
    assert idx.installed() == [("Mine", "foo")]
    assert idx.display_name("foo") == "Mine"
    assert idx.display_name("missing") is None


def test_subdirectory_ids_use_dashes_and_names_follow_the_locale(tmp_path):
    desktop(tmp_path, "kde/org.kde.konsole.desktop", Name="Konsole", **{"Name[de]": "Konsole DE"})
    idx = index(tmp_path, LANG="de_DE.UTF-8")
    assert idx.installed() == [("Konsole DE", "kde-org.kde.konsole")]


def test_the_scan_follows_the_directory_when_an_app_is_installed(tmp_path):
    idx = index(tmp_path)
    assert idx.installed() == []
    time.sleep(0.01)
    desktop(tmp_path, "new.desktop", Name="New")
    assert idx.installed() == [("New", "new")]


# ---- pure pieces --------------------------------------------------------------


@pytest.mark.parametrize("char,keysym", [
    ("a", 0x61), ("é", 0xE9), ("\n", 0xFF0D), ("\t", 0xFF09),
    ("☃", 0x1002603), ("😀", 0x101F600),
])
def test_characters_map_to_keysyms(char, keysym):
    assert x11.keysym_for(char) == keysym


def test_a_shifted_letter_reports_the_tables_lower_case_keysym(xserver):
    from pynput.keyboard import KeyCode

    assert x11.keysym_of(KeyCode.from_vk(0x41)) == 0x61
    assert x11.keysym_of(KeyCode.from_vk(0xFFEB)) == 0xFFEB


def test_the_gate_stays_up_a_beat_past_the_send():
    gate = x11.InjectionGate()
    assert not gate.active()
    with gate:
        assert gate.active()
    assert gate.active()
    time.sleep(x11.GATE_TAIL_S + 0.05)
    assert not gate.active()


def test_the_whole_tier_assembles_x11_adapters_without_touching_the_server():
    plat = create({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":nope"})
    assert isinstance(plat.keyboard, x11.X11Keyboard)
    assert isinstance(plat.clipboard, x11.X11Clipboard)
    assert isinstance(plat.focused_app, x11.X11FocusedApp)
    assert isinstance(plat.hotkey_tap, x11.X11HotkeyTap)


def test_a_dead_display_raises_loudly_from_the_tap_and_the_keyboard(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises((Xlib.error.DisplayError, OSError)):
        x11.X11HotkeyTap().start(lambda *a: None)
    with pytest.raises((Xlib.error.DisplayError, OSError)):
        x11.X11Keyboard().send_text_units(_utf16_units("a"))


class FakeDisplay:
    """Just enough of a display to exhaust the scratch keycodes."""

    class _Info:
        min_keycode, max_keycode = 8, 10

    display = type("D", (), {"info": _Info})()

    def get_keyboard_mapping(self, first, count):
        return [(0x61, 0, 0, 0)] * count


def test_an_exhausted_scratch_keycode_raises_so_auto_learn_can_hear_it():
    with pytest.raises(RuntimeError, match="free keycode"):
        x11.X11Keyboard._scratch_keycode(FakeDisplay())


# ---- against a real server ----------------------------------------------------


@pytest.fixture(scope="module")
def xserver():
    if not shutil.which("Xvfb"):
        pytest.skip("no Xvfb")
    r, w = os.pipe()
    proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", "640x480x24",
                             "-nolisten", "tcp"], pass_fds=(w,))
    os.close(w)
    number = os.read(r, 16).decode().strip()
    os.close(r)
    old = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = f":{number}"
    yield os.environ["DISPLAY"]
    proc.terminate()
    proc.wait(5)
    if old is None:
        os.environ.pop("DISPLAY")
    else:
        os.environ["DISPLAY"] = old


@pytest.fixture
def receiver(xserver, monkeypatch):
    """A focused window that records the keysyms it is sent."""
    import Xlib.display
    import Xlib.X

    monkeypatch.setattr(x11, "REMAP_SETTLE_S", 0.3)
    d = Xlib.display.Display()
    win = d.screen().root.create_window(0, 0, 50, 50, 0, d.screen().root_depth,
                                        event_mask=Xlib.X.KeyPressMask)
    win.map()
    d.sync()
    d.set_input_focus(win, Xlib.X.RevertToParent, Xlib.X.CurrentTime)
    d.sync()
    got = []

    def read():
        while True:
            try:
                e = d.next_event()
            except Exception:
                return
            if e.type == Xlib.X.MappingNotify:
                d.refresh_keyboard_mapping(e)
            elif e.type == Xlib.X.KeyPress:
                keysym = d.keycode_to_keysym(e.detail, 1 if e.state & 1 else 0)
                if keysym < 0xFF00 or keysym in (0xFF0D, 0xFF09) or keysym >= 0x1000000:
                    got.append(chr(keysym - 0x1000000 if keysym >= 0x1000000
                                   else 0x0A if keysym == 0xFF0D else keysym))
    threading.Thread(target=read, daemon=True).start()
    yield got
    d.close()


def test_typing_covers_ascii_accents_and_characters_the_layout_lacks(receiver):
    kb = x11.X11Keyboard()
    assert kb.send_text_units(_utf16_units("Hi é☃!\n"))
    time.sleep(0.3)
    assert "".join(receiver) == "Hi é☃!\n"


# pynput's stopped listener thread only exits on its next X event, so it meets
# the fixture's server teardown with a broken pipe; that is its shutdown, not a
# failure of ours.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_our_own_sends_reach_the_tap_as_injected(receiver):
    gate = x11.InjectionGate()
    seen = []
    tap = x11.X11HotkeyTap(gate)
    tap.start(lambda keysym, down, injected: seen.append((keysym, down, injected)))
    time.sleep(0.5)
    try:
        x11.X11Keyboard(gate).send_chord([0xFFE3, ord("v")])
        time.sleep(0.4)
    finally:
        tap.stop()
    assert seen
    assert all(injected for _, _, injected in seen)
    assert {k for k, _, _ in seen} == {0xFFE3, ord("v")}


def test_the_selection_round_trips_and_its_counter_moves_on_every_write(xserver):
    cb = x11.X11Clipboard()
    assert cb.get_text() is None
    start = cb.sequence_number()
    cb.set_text("héllo ☃", exclude_from_history=True)
    assert cb.sequence_number() == start + 1
    assert cb.get_text() == "héllo ☃"
    cb.set_text("second", exclude_from_history=False)
    assert cb.sequence_number() == start + 2


def test_with_no_window_manager_the_focused_app_is_unknown_not_a_crash(xserver):
    app = x11.X11FocusedApp()
    assert app.name() == "unknown"
    assert app.window_rect() is None
    assert app.permission_granted() and app.injection_blocked() is None
