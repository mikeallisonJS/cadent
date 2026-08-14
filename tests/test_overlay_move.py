"""Settings ▸ "Move overlay…" (spec §4.6).

The real pill is click-through at all times, so this is the only way to
reposition it — and the drag writes nothing until Done.
"""

import json

import pytest
from PySide6.QtCore import QEvent, QObject, Qt

from cadent.config_store import ConfigStore
from cadent.overlay_move import MoveOverlayDialog, StandInPill
from cadent.theme.tokens import tokens


@pytest.fixture
def dialog(qt_app, tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    win = MoveOverlayDialog(store, tokens("dark"))
    yield win
    win.pill.close()
    win.close()
    win.deleteLater()


def written(dialog):
    return json.loads(dialog.store.path.read_text(encoding="utf-8"))


def test_the_stand_in_accepts_the_mouse_the_real_pill_never_does(dialog):
    """PRD §5.7's click-through property is preserved on the real pill; the
    drag happens on an imitation."""
    from PySide6.QtCore import Qt

    assert not dialog.pill.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_stand_in_survives_the_app_being_inactive(dialog):
    """Same macOS Qt.Tool hiding rule as the real pill (#158): a drag that
    deactivates the app must not vanish the ghost mid-move."""
    assert dialog.pill.testAttribute(
        Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


def test_dragging_writes_nothing_until_done(dialog):
    before = written(dialog)
    dialog.pill.move(120, 120)
    assert written(dialog) == before


def test_done_commits_the_anchor_as_one_write(dialog):
    """One write per gesture — the anchor is three fields settled together."""
    dialog.pill.move(200, 200)
    dialog._commit()
    raw = written(dialog)
    assert raw["overlay_position_custom"] is True
    assert 0.0 <= raw["overlay_anchor_x"] <= 1.0
    assert 0.0 <= raw["overlay_anchor_y"] <= 1.0


def test_the_anchor_is_a_fraction_not_a_pixel_count(dialog):
    """So a custom position survives a monitor being unplugged."""
    available = dialog.pill.screen().availableGeometry()
    dialog.pill.move(available.left() + available.width() // 4,
                     available.top() + available.height() // 2)
    x, y = dialog.pill.anchor()
    assert 0.2 < x < 0.35
    assert 0.45 < y < 0.6


def test_reset_returns_to_bottom_centre(dialog):
    dialog.pill.move(40, 40)
    dialog._reset()
    raw = written(dialog)
    assert raw["overlay_position_custom"] is False
    assert raw["overlay_anchor_x"] == 0.5

    available = dialog.pill.screen().availableGeometry()
    t = tokens("dark")
    assert abs(dialog.pill.geometry().center().x() - available.center().x()) <= 1
    assert abs((available.bottom() - dialog.pill.geometry().bottom())
               - int(t["pill_margin_bottom"])) <= 1


def test_snapping_pulls_to_the_centre_guide(qt_app):
    """Snapping is configurable, not always-on."""
    pill = StandInPill(tokens("dark"), snap=True)
    available = pill.screen().availableGeometry()
    centre_x = available.center().x() - pill.width() // 2
    from PySide6.QtCore import QPoint

    snapped = pill._snapped(QPoint(centre_x + 6, 400))
    assert snapped.x() == centre_x
    pill.close()


def test_snapping_off_leaves_the_position_alone(qt_app):
    pill = StandInPill(tokens("dark"), snap=False)
    available = pill.screen().availableGeometry()
    target_x = available.center().x() - pill.width() // 2 + 6
    pill.snap = False
    pill.move(target_x, 400)
    assert pill.pos().x() == target_x
    pill.close()


def test_a_far_drag_is_not_snapped(qt_app):
    pill = StandInPill(tokens("dark"), snap=True)
    from PySide6.QtCore import QPoint

    available = pill.screen().availableGeometry()
    far = available.center().x() - pill.width() // 2 + 200
    assert pill._snapped(QPoint(far, 400)).x() == far
    pill.close()


def test_a_previously_moved_pill_opens_where_it_was(qt_app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"overlay_position_custom": True,
                                "overlay_anchor_x": 0.25,
                                "overlay_anchor_y": 0.5}), encoding="utf-8")
    win = MoveOverlayDialog(ConfigStore(path), tokens("dark"))
    available = win.pill.screen().availableGeometry()
    expected = available.left() + 0.25 * available.width()
    assert abs(win.pill.geometry().center().x() - expected) <= 2
    win.pill.close()
    win.close()


# ---- the stand-in has to actually receive the mouse ------------------------

class BlockWatcher(QObject):
    """Qt delivers WindowBlocked to every window a modal dialog locks out."""

    def __init__(self):
        super().__init__()
        self.blocked = False

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.WindowBlocked:
            self.blocked = True
        elif event.type() == QEvent.Type.WindowUnblocked:
            self.blocked = False
        return False


def test_the_stand_in_is_exempt_from_the_dialog_s_modal_block(qt_app, dialog):
    """The dialog runs modal — moving the overlay is a mode, not a field — and
    Qt blocks mouse input to every window that is not the modal one or a
    transient child of it. A parentless stand-in never gets the press, so the
    "Drag me" invitation is a lie and nothing happens.
    """
    watcher = BlockWatcher()
    dialog.pill.installEventFilter(watcher)
    dialog.pill.show()
    qt_app.processEvents()
    dialog.setModal(True)
    dialog.show()
    qt_app.processEvents()
    assert watcher.blocked is False


def test_a_parentless_stand_in_is_the_bug_this_guards(qt_app, dialog):
    """The shape that shipped broken, kept as the counter-example."""
    orphan = StandInPill(tokens("dark"), snap=False)
    watcher = BlockWatcher()
    orphan.installEventFilter(watcher)
    orphan.show()
    qt_app.processEvents()
    dialog.setModal(True)
    dialog.show()
    qt_app.processEvents()
    assert watcher.blocked is True
    orphan.close()


def test_the_stand_in_is_still_its_own_top_level(dialog):
    """Parented for input, not for layout: it has to be positionable anywhere
    on screen, including outside the dialog."""
    assert dialog.pill.isWindow()
    dialog.pill.move(40, 40)
    assert dialog.pill.pos().x() == 40


def test_a_press_then_move_drags_the_pill(qt_app, dialog):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent

    def event(kind, global_point):
        local = dialog.pill.mapFromGlobal(global_point)
        return QMouseEvent(kind, QPointF(local), QPointF(global_point),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)

    dialog.pill.snap = False
    start = dialog.pill.pos()
    grab = start + QPoint(30, 15)
    qt_app.sendEvent(dialog.pill, event(QMouseEvent.Type.MouseButtonPress, grab))
    qt_app.sendEvent(dialog.pill,
                     event(QMouseEvent.Type.MouseMove, grab + QPoint(120, -50)))
    assert dialog.pill.pos() == start + QPoint(120, -50)


def test_the_grab_point_is_kept_so_the_pill_does_not_jump(qt_app, dialog):
    """Grabbing the right-hand end must not teleport the pill's top-left to
    the cursor."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent

    dialog.pill.snap = False
    start = dialog.pill.pos()
    grab = start + QPoint(dialog.pill.width() - 5, 20)
    qt_app.sendEvent(dialog.pill, QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(dialog.pill.mapFromGlobal(grab)), QPointF(grab),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    qt_app.sendEvent(dialog.pill, QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(dialog.pill.mapFromGlobal(grab)), QPointF(grab),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    assert dialog.pill.pos() == start
