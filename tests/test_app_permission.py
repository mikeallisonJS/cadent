"""The grant poll lives in the app (ADR 0012, #31).

A grant given outside Cadent — System Settings on darwin, the portal dialog
on Linux — fires no signal of ours, so the app polls. It owns the
`permission-needed` fault, which is what makes a Mac granted Accessibility
with no window open see the tray recover; the Settings banner and the wizard
page read the same tick. Built like `test_app_downloads.py`: the object is
made without `__init__` and given exactly what the methods reach for.
"""


import pytest
from conftest import make_platform, pin_darwin_ui_platform

from cadent import app as app_mod
from cadent.tray import FAULTS


class FakeTray:
    def __init__(self) -> None:
        self.faults: dict[str, bool] = {}

    def set_fault(self, kind, active=True) -> None:
        assert kind in FAULTS
        self.faults[kind] = active


class FakeSurface:
    def __init__(self, visible=True) -> None:
        self.visible = visible
        self.refreshes = 0

    def isVisible(self) -> bool:  # noqa: N802 (Qt naming)
        return self.visible

    def refresh_permission(self) -> None:
        self.refreshes += 1


@pytest.fixture
def app(qt_app, monkeypatch):
    plat = pin_darwin_ui_platform(monkeypatch, granted=False)
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.platform = plat
    instance.tray = FakeTray()
    instance.wizard = None
    instance.settings_window = None
    yield instance, plat
    timer = getattr(instance, "_permission_timer", None)
    if timer is not None:
        timer.stop()


def test_the_fault_is_set_and_cleared_with_no_window_open(app):
    instance, plat = app
    instance._start_permission_poll()
    assert instance.tray.faults["permission-needed"] is True
    plat.focused_app.granted = True
    instance._poll_permission()
    assert instance.tray.faults["permission-needed"] is False


def test_the_poll_only_runs_where_the_platform_names_a_grant(qt_app):
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.platform = make_platform()       # win32-shaped: permission=None
    instance.tray = FakeTray()
    instance.wizard = instance.settings_window = None
    instance._start_permission_poll()
    assert instance._permission_timer is None
    assert instance.tray.faults == {}


def test_open_surfaces_read_the_same_tick(app):
    instance, plat = app
    instance.settings_window = FakeSurface()
    instance.wizard = FakeSurface(visible=False)
    instance._poll_permission()
    assert instance.settings_window.refreshes == 1
    assert instance.wizard.refreshes == 0     # hidden surfaces are told nothing


def test_permission_copy_lives_only_in_adapters():
    """ADR 0012: no permission wording in cross-platform UI modules — the
    words are the platform's (`PermissionPreflight`), the surfaces render."""
    from pathlib import Path

    import cadent

    root = Path(cadent.__file__).parent
    for name in ("wizard.py", "settings_ui/window.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "Accessibility" not in text, name
