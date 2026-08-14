"""The suite's own machinery, tested like anything else (#119).

`conftest.py`'s autouse fixtures are not scaffolding — they are the reason a
green run means anything. `_no_window_outlives_its_test` fails silently:
windows quietly pile up again, the suite gets slower, and months later
somebody spends an afternoon on an access violation with no assertion
attached to it. So it gets a test that fails loudly instead.
"""

from conftest import destroy_windows_since, top_level_widgets
from shiboken6 import isValid

from cadent.config_store import ConfigStore
from cadent.settings_ui import SettingsWindow
from cadent.theme.tokens import tokens


def test_a_window_a_test_forgot_to_close_is_destroyed_anyway(qt_app, tmp_path):
    """Driven against the mechanism rather than the fixture wrapping it, so it
    says nothing about test ordering: the fixture is three lines around this
    call, and a test that asserted "the previous test's window is gone" would
    only hold while these two stayed adjacent."""
    before = top_level_widgets()
    window = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                            tokens=tokens("dark"))
    destroy_windows_since(before)
    assert not isValid(window), "a window nobody closed is still alive"


def test_the_windows_a_test_inherited_are_left_alone(qt_app, tmp_path):
    """The guard destroys what a test *added*, never what it was handed.

    Without that distinction the first fixture-owned window to be built inside
    an autouse fixture's setup — or the shared QApplication's own hidden
    top-levels — would be torn down under the test that asked for it.
    """
    inherited = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                               tokens=tokens("dark"))
    before = top_level_widgets()
    destroy_windows_since(before)
    assert isValid(inherited)
    inherited.close()
    inherited.deleteLater()
