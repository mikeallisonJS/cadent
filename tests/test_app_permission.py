"""The Accessibility grant landing while the app is already running (#60).

macOS answers a missing grant with silence (ADR 0002): a listener started
without it reports `running` and hears nothing, and keeps hearing nothing after
the grant lands. Only a listener built *after* the grant works, so the app's
permission poll (ADR 0012) — which reads `permission_granted()`, never the
listener — rebuilds it on the flip from missing to granted.

Built without `__init__`, like test_app_downloads: the poll reads a platform, a
tray and a listener, and nothing else.
"""

import types

import pytest
from conftest import FakeFocusedApp, make_platform

from cadent import app as app_mod


class FakePtt:
    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def start(self) -> None:
        self.log.append(f"start {self.name}")

    def stop(self) -> None:
        self.log.append(f"stop {self.name}")


@pytest.fixture
def app():
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.log = []
    instance.ptt = FakePtt(instance.log, "deaf")
    instance._make_ptt = lambda: FakePtt(instance.log, "fresh")
    instance.tray = types.SimpleNamespace(set_fault=lambda *_: None)
    instance.settings_window = None
    instance.wizard = None
    return instance


def grant(app, granted: bool) -> None:
    app.platform.focused_app.granted = granted
    app._poll_permission()


def test_the_listener_is_left_alone_while_the_grant_is_missing(app):
    app.platform = make_platform(focused_app=FakeFocusedApp(granted=False))
    app._poll_permission()
    app._poll_permission()
    assert app.log == []


def test_a_machine_granted_from_the_start_keeps_its_listener(app):
    """The first reading has no earlier one to differ from: a listener built
    with the grant already in place hears fine and must not be torn down."""
    app.platform = make_platform(focused_app=FakeFocusedApp(granted=True))
    app._poll_permission()
    app._poll_permission()
    assert app.log == []


def test_the_grant_landing_rebuilds_the_listener_once(app):
    app.platform = make_platform(focused_app=FakeFocusedApp(granted=False))
    app._poll_permission()
    grant(app, True)
    assert app.log == ["stop deaf", "start fresh"]
    app._poll_permission()
    assert app.log == ["stop deaf", "start fresh"]


def test_a_grant_taken_away_and_given_back_rebuilds_again(app):
    """The poll never stops, because the grant can be revoked while the app
    runs. A listener that sat through a revocation is deaf the same way."""
    app.platform = make_platform(focused_app=FakeFocusedApp(granted=False))
    app._poll_permission()
    grant(app, True)
    grant(app, False)
    assert app.log == ["stop deaf", "start fresh"]
    grant(app, True)
    assert app.log == ["stop deaf", "start fresh", "stop fresh", "start fresh"]
