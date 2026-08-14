"""Every window, walked for accessible names (spec §8.3, ticket #98).

A per-window walk only ever asserts something about the windows someone
remembered to write a test for. The bar in §8 is *every control in the app*,
so the walk belongs somewhere that enumerates the windows rather than
somewhere that happens to build one.

That distinction is not academic: it is what caught the History window, whose
search box carried a placeholder and no name at all — placeholders are not
accessible names, and M4 never touched `history_ui.py` because §5.6 put it out
of scope for everything except inheriting the theme.

`test_wizard.py` keeps its own walk, which is a stronger check rather than a
duplicate one: it advances through every page, where this file only ever sees
the wizard's first. Pages are built lazily, so there is no single moment at
which one window holds all of the wizard's controls.
"""

from contextlib import contextmanager

import pytest
from PySide6.QtWidgets import QLabel

from cadent import a11y
from cadent.config_store import ConfigStore
from cadent.history import History
from cadent.history_ui import DetailDialog, HistoryWindow
from cadent.overlay_move import MoveOverlayDialog
from cadent.settings_ui import SettingsWindow
from cadent.theme.tokens import tokens

DARK = tokens("dark")


@pytest.fixture
def history(tmp_path):
    h = History(tmp_path / "history.db")
    h.add("hello world", "Hello, world.", 1.0, "notepad.exe", "flow")
    return h


@contextmanager
def _windows(tmp_path, history):
    """Every top-level surface the app can put on screen.

    The overlay pill is deliberately absent: it takes no focus and is marked
    as a tool window rather than announced (§4.6, §8.4), so a walk for
    focusable controls has nothing to say about it.

    Closed on the way out even when an assertion fires mid-walk — the shared
    QApplication repolishes every live widget on each stylesheet change, so
    leaking a window makes the rest of the suite slower rather than failing
    it visibly.
    """
    from cadent.wizard import SetupWizard

    built = [
        ("SettingsWindow", SettingsWindow(ConfigStore(tmp_path / "config.json"),
                                          tokens=DARK, devices=["Rode NT-USB Mini"])),
        ("SetupWizard", SetupWizard(ConfigStore(tmp_path / "wizard.json"),
                                    tokens=DARK, devices=["Rode NT-USB Mini"])),
        ("HistoryWindow", HistoryWindow(history)),
        ("DetailDialog", DetailDialog(history.search("")[0])),
        ("MoveOverlayDialog", MoveOverlayDialog(
            ConfigStore(tmp_path / "move.json"), DARK)),
    ]
    try:
        yield built
    finally:
        for _name, window in built:
            window.close()
            window.deleteLater()


def test_every_window_names_every_focusable_control(qt_app, tmp_path, history):
    """The enforcement seam for the whole app, not one window at a time."""
    with _windows(tmp_path, history) as windows:
        offenders = {name: [type(w).__name__ for w in a11y.unnamed(window)]
                     for name, window in windows if a11y.unnamed(window)}
    assert offenders == {}


def test_every_window_has_at_least_one_focusable_control(qt_app, tmp_path, history):
    """Guards the walk itself: a window that fails to build its controls would
    otherwise pass the name check by having nothing to name."""
    with _windows(tmp_path, history) as windows:
        empty = [name for name, window in windows if not a11y.focusables(window)]
    assert empty == []


def test_the_history_search_box_is_named_by_more_than_its_placeholder(qt_app, history):
    """The specific shape of the History gap, kept as its own statement so a
    later "the placeholder says it already" does not reintroduce it.

    Qt reports `name=''` for a QLineEdit carrying only placeholderText, and a
    placeholder is gone the moment anything is typed — so the one moment a
    screen-reader user is most likely to ask what the field is, is the moment
    it has nothing to answer with.
    """
    window = HistoryWindow(history)
    assert window.search_box.placeholderText() == "Search dictations…"
    assert window.search_box.accessibleName() == "Search dictations"
    window.close()
    window.deleteLater()


def test_the_history_table_names_itself(qt_app, history):
    window = HistoryWindow(history)
    assert window.table.accessibleName() == "Dictations"
    window.close()
    window.deleteLater()


def test_the_two_copy_buttons_say_which_block_they_copy(qt_app, history):
    """Both blocks label their button "Copy", so the visible text alone
    announces as "Copy button" twice with nothing to tell them apart."""
    from PySide6.QtWidgets import QPushButton

    dialog = DetailDialog(history.search("")[0])
    names = sorted(b.accessibleName() for b in dialog.findChildren(QPushButton))
    assert names == ["Copy inserted text", "Copy raw transcript"]
    dialog.close()
    dialog.deleteLater()


# ---- keyboard reachability (§8.3) ------------------------------------------

def _tab_cycle(window) -> set[int]:
    """Every widget Tab lands on, walking one full cycle from the start."""
    window.focusNextChild()
    start = window.focusWidget()
    seen, steps = set(), 0
    # A cycle should close well inside this; the bound only stops a runaway
    # loop if some widget refuses to hand focus on.
    while steps <= len(a11y.focusables(window)) * 3 + 40:
        current = window.focusWidget()
        if current is not None:
            seen.add(id(current))
        if not window.focusNextChild():
            break
        steps += 1
        if window.focusWidget() is start and steps > 1:
            break
    return seen


def test_tab_reaches_every_visible_control_in_every_window(qt_app, tmp_path, history):
    """"Keyboard-complete" as an assertion rather than a walkthrough.

    A control Qt can focus but Tab never arrives at is unreachable without a
    mouse, and nothing else in the suite would notice — the name walk is happy
    to name a control no one can get to.

    Scoped to what is on screen, which is what "reachable by Tab" means: the
    five settings panes that are not the current one hold most of the app's
    controls and none of them are tabbable while stacked behind. It is a
    regression guard for one pane at a time, not a substitute for #98's
    keyboard-only walkthrough of all six.
    """
    unreachable = {}
    with _windows(tmp_path, history) as windows:
        for name, window in windows:
            window.show()
            qt_app.processEvents()
            reached = _tab_cycle(window)
            missed = [type(w).__name__ for w in a11y.focusables(window)
                      if w.isVisibleTo(window) and id(w) not in reached]
            if missed:
                unreachable[name] = missed
    assert unreachable == {}


# ---- text scaling (§8.5) ---------------------------------------------------

@pytest.fixture
def scaled(qt_app):
    """The app as it runs at a given Windows Text size, sheet and all.

    Both halves are load-bearing: the type scale lives in the tokens, and the
    row padding those tokens are measured against lives in the stylesheet.
    """
    from cadent.theme.manager import ThemeManager

    managers = []

    def build(scale: float) -> ThemeManager:
        manager = ThemeManager(qt_app, "dark", text_scale=scale)
        managers.append(manager)
        return manager

    yield build
    for manager in managers:
        manager.setParent(None)
        manager.deleteLater()
    qt_app.setStyleSheet("")


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.25])
@pytest.mark.parametrize("height", [680, 320])
def test_the_sidebar_shows_its_widest_row_at_every_text_size(
        qt_app, tmp_path, scaled, scale, height):
    """Windows' Text size runs to 225%, and §8.5's promise is that nothing
    clips — starting with the sidebar. See `NavRail` for why a minimum width
    alone does not deliver that.

    The short window is the other half of the case, not a variation on it: a
    rail too short for its rows grows a scrollbar, and the scrollbar comes out
    of the viewport. Ten pixels is enough to put the labels back into ellipsis
    at both scales that matter.
    """
    manager = scaled(scale)
    window = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                            tokens=manager.tokens, devices=["Rode NT-USB Mini"])
    window.resize(940, height)
    window.show()
    qt_app.processEvents()
    sidebar = window.sidebar
    assert sidebar.viewport().width() >= sidebar.sizeHintForColumn(0), (
        f"at {scale:.0%} in a {height}px window the nav rail shows "
        f"{sidebar.viewport().width()}px of a {sidebar.sizeHintForColumn(0)}px "
        f"row — the labels elide")
    window.close()
    window.deleteLater()


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.25])
def test_the_device_combo_cap_holds_at_every_text_size(qt_app, tmp_path, scaled,
                                                       scale, long_device_names):
    """#106 capped the microphone combo in **characters**, not pixels, and this
    is why: `minimumContentsLength` is measured against the font, so the cap
    grows with the type exactly as the label beside it does. A pixel maximum
    would have been the same bug again at 225%.

    What is asserted is that the cap *holds* — the combo is the same width, and
    the hint wraps the same way, whether the device list is 16 characters or 44
    — at each scale independently. Not that the label column keeps some share
    of the row: at 225% in a 940px window the hint wraps either way, and that
    is general text-scale pressure rather than anything this cap can fix.
    """
    manager = scaled(scale)

    def measure(devices):
        window = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                                tokens=manager.tokens, devices=devices)
        window.resize(940, 680)
        window.show()
        qt_app.processEvents()
        mic = window.general.mic
        row = mic.parentWidget()
        while row is not None and row.objectName() != "Row":
            row = row.parentWidget()
        hint = next(w for w in row.findChildren(QLabel)
                    if w.objectName() == "RowHint")
        measured = (mic.width(),
                    round(hint.height() / hint.fontMetrics().lineSpacing()))
        window.close()
        window.deleteLater()
        return measured

    assert measure(long_device_names) == measure(["Rode NT-USB Mini"]), (
        f"at {scale:.0%} the device names change how the Microphone row "
        f"divides its width")


def test_the_sidebar_never_shrinks_below_the_design_minimum(qt_app):
    """Deriving the width from the rows must not undo §8.5's other half.

    Driven against a rail whose rows are far narrower than the floor, because
    that is the only state in which the floor is the binding constraint — the
    real sidebar's own labels always exceed it, so the app's own window cannot
    exercise this.
    """
    from cadent.settings_ui.window import NavRail

    rail = NavRail(220)
    rail.addItem("a")
    assert rail.sizeHint().width() == 220
    assert rail.minimumWidth() == 220

    rail.addItem("a label far wider than the design's two hundred and twenty")
    assert rail.sizeHint().width() > 220
    rail.deleteLater()


# ---- the darwin column's controls (M5 §7, #148) -----------------------------

def test_the_darwin_only_controls_are_named_too(qt_app, tmp_path, monkeypatch,
                                                history):
    """The name walk above runs under the host facts, so the darwin-only
    surfaces — the Accessibility banner, the wizard's permission page, the
    running-apps picker — would otherwise ship unwalked. Pin the darwin
    column with the grant missing and walk them the same way."""
    from conftest import pin_darwin_ui_platform

    from cadent.wizard import PERMISSION, SetupWizard

    pin_darwin_ui_platform(monkeypatch, granted=False,
                           running=[("Safari", "com.apple.Safari")])
    settings_window = SettingsWindow(ConfigStore(tmp_path / "config.json"),
                                     tokens=DARK, devices=["X"], history=history)
    wizard = SetupWizard(ConfigStore(tmp_path / "wizard.json"),
                         tokens=DARK, devices=["X"])
    while wizard.page != PERMISSION:
        wizard.advance()
    try:
        assert settings_window.permission_banner.isVisibleTo(settings_window)
        for name, window in (("SettingsWindow", settings_window),
                             ("SetupWizard", wizard)):
            assert not a11y.unnamed(window), (
                name, [type(w).__name__ for w in a11y.unnamed(window)])
    finally:
        wizard._completed = True
        for window in (settings_window, wizard):
            window.close()
            window.deleteLater()
