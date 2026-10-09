"""The Accessibility grant landing while the app is already running (#60).

macOS answers a missing grant with silence (ADR 0002): a listener started
without it reports `running` and hears nothing, and keeps hearing nothing after
the grant lands. Only a listener built *after* the grant works, so the app
watches `permission_granted()` — never the listener — and rebuilds on the flip.

Built without `__init__`, like test_app_downloads: the watch reads a platform,
a timer and a listener, and nothing else.
"""

import dataclasses

import pytest
from conftest import FakeFocusedApp, make_platform

from cadent import app as app_mod


class FakeTimer:
    def __init__(self) -> None:
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class FakePtt:
    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def start(self) -> None:
        self.log.append(f"start {self.name}")

    def stop(self) -> None:
        self.log.append(f"stop {self.name}")


def platform_with(*, preflight, granted):
    plat = make_platform(focused_app=FakeFocusedApp(granted=granted))
    caps = dataclasses.replace(plat.capabilities, permission_preflight=preflight)
    return dataclasses.replace(plat, capabilities=caps)


@pytest.fixture
def app():
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.log = []
    instance.ptt = FakePtt(instance.log, "deaf")
    instance._make_ptt = lambda: FakePtt(instance.log, "fresh")
    instance._permission_timer = FakeTimer()
    return instance


def test_a_missing_grant_is_watched(app):
    app.platform = platform_with(preflight="accessibility", granted=False)
    app._watch_permission()
    assert app._permission_timer.running


def test_a_granted_machine_is_not_watched(app):
    app.platform = platform_with(preflight="accessibility", granted=True)
    app._watch_permission()
    assert not app._permission_timer.running


def test_a_platform_without_a_preflight_is_not_watched(app):
    app.platform = platform_with(preflight=None, granted=False)
    app._watch_permission()
    assert not app._permission_timer.running


def test_the_listener_is_left_alone_while_the_grant_is_missing(app):
    app.platform = platform_with(preflight="accessibility", granted=False)
    app._watch_permission()
    app._poll_permission()
    assert app.log == []
    assert app._permission_timer.running


def test_the_grant_landing_rebuilds_the_listener_once(app):
    app.platform = platform_with(preflight="accessibility", granted=False)
    app._watch_permission()
    app.platform.focused_app.granted = True
    app._poll_permission()
    assert app.log == ["stop deaf", "start fresh"]
    assert not app._permission_timer.running
