"""DesktopEnv on Linux (spec §10): one Settings snapshot, tray ink keyed
desktop-then-scheme, the second-launch knock. A fake portal; no bus."""

import threading

import pytest

pytest.importorskip("jeepney")

from jeepney import new_signal  # noqa: E402

from cadent.platform.linux import desktop as D  # noqa: E402
from cadent.platform.linux.instance import LinuxSingleInstance  # noqa: E402
from cadent.platform.linux.portal import PortalError  # noqa: E402


def body(**namespaces):
    return ({ns: {k: ("v", v) for k, v in keys.items()}
             for ns, keys in namespaces.items()},)


class FakePortal:
    def __init__(self, reply=None):
        self.reply, self.callback = reply, None

    def call(self, message, timeout=5.0):
        if self.reply is None:
            raise PortalError("no portal")
        return self.reply

    def subscribe_signal(self, rule, callback):
        self.callback = callback
        return lambda: None

    def change(self, ns, key, value):
        self.callback(new_signal(
            D.signal_rule(D.SETTINGS, "SettingChanged") and
            __import__("jeepney").DBusAddress("/p", interface=D.SETTINGS),
            "SettingChanged", "ssv", (ns, key, ("u", value))))


def make(reply, desktops=()):
    portal = FakePortal(reply)
    return D.PortalDesktop(portal, desktops), portal


def scheme(n):
    return body(**{D.APPEARANCE: {"color-scheme": n}})


@pytest.mark.parametrize("n,ink", [(0, "#ffffff"), (1, "#ffffff"), (2, "#000000")])
def test_ink_follows_the_scheme_off_gnome(n, ink):
    assert make(scheme(n), ("kde",))[0].tray_ink() == ink


def test_gnome_is_always_white():
    assert make(scheme(2), ("gnome",))[0].tray_ink() == "#ffffff"


def test_contrast_never_changes_the_ink():
    reply = body(**{D.APPEARANCE: {"color-scheme": 2, "contrast": 1}})
    assert make(reply, ("kde",))[0].tray_ink() == "#000000"


def test_a_scheme_flip_repaints_the_mark():
    env, portal = make(scheme(1), ("kde",))
    seen = []
    env.watch_tray_ink(lambda: seen.append(env.tray_ink()))
    portal.change(D.APPEARANCE, "color-scheme", 2)
    assert seen == ["#000000"]
    portal.change(D.APPEARANCE, "contrast", 1)
    assert seen == ["#000000"] and env.high_contrast()


def test_a_change_during_the_snapshot_read_wins():
    portal = FakePortal(scheme(1))
    env = D.PortalDesktop(portal)
    call = portal.call

    def call_with_change(message, timeout=5.0):
        reply = call(message, timeout)
        portal.change(D.APPEARANCE, "color-scheme", 2)
        return reply

    portal.call = call_with_change
    assert env.tray_ink() == "#000000"


def test_snapshot_values():
    env, _ = make(body(**{
        D.GNOME_INTERFACE: {"text-scaling-factor": 1.5,
                            "enable-animations": False},
        D.GNOME_A11Y: {"high-contrast": True}}), ("gnome",))
    assert env.text_scale_factor() == 1.5
    assert env.high_contrast() is True
    assert env.animations_enabled() is False


def test_kde_animation_factor_zero_disables_animations():
    env, _ = make(body(**{D.KDE_GLOBALS: {"AnimationDurationFactor": 0.0},
                          D.APPEARANCE: {"contrast": 1}}))
    assert env.animations_enabled() is False
    assert env.high_contrast() is True


def test_reduced_motion_wins_over_gnome():
    env, _ = make(body(**{D.APPEARANCE: {"reduced-motion": 1},
                          D.GNOME_INTERFACE: {"enable-animations": True}}))
    assert env.animations_enabled() is False


def test_no_portal_answers_with_the_fallbacks():
    env, _ = make(None)
    assert (env.text_scale_factor(), env.high_contrast(),
            env.animations_enabled(), env.tray_ink()) == (1.0, False, True, "#ffffff")


def test_a_second_launch_knocks_on_the_first(tmp_path):
    first = LinuxSingleInstance(tmp_path / "c.lock", tmp_path / "c.sock")
    second = LinuxSingleInstance(tmp_path / "c.lock", tmp_path / "c.sock")
    knocked = threading.Event()
    assert first.acquire()
    first.serve_second_launch(knocked.set)
    assert not second.acquire()
    assert knocked.wait(2)
    first._server.close()


def test_a_knock_with_nobody_home_is_harmless(tmp_path):
    held = LinuxSingleInstance(tmp_path / "c.lock", tmp_path / "none.sock")
    assert held.acquire()
    other = LinuxSingleInstance(tmp_path / "c.lock", tmp_path / "none.sock")
    assert not other.acquire()


def test_a_knock_before_the_listener_is_registered_is_delivered(tmp_path):
    first = LinuxSingleInstance(tmp_path / "c.lock", tmp_path / "c.sock")
    second = LinuxSingleInstance(tmp_path / "c.lock", tmp_path / "c.sock")
    knocked = threading.Event()
    assert first.acquire()
    assert not second.acquire()
    first.serve_second_launch(knocked.set)
    assert knocked.wait(2)
    first._server.close()
