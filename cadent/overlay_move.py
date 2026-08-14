"""Settings ▸ "Move overlay…" — the only way to reposition the pill (spec §4.6).

The real pill stays `WA_TransparentForMouseEvents` at **all** times during
dictation: PRD §5.7's click-through property is preserved, and there is never
a dead zone that eats clicks or a stray drag that relocates the overlay. So
moving it is a deliberate mode, driven by a mouse-enabled *stand-in*.

The anchor is stored **normalized** — a fraction of available geometry rather
than absolute pixels — so a custom position survives a monitor being unplugged
and still follows the focused window's screen. The drag writes nothing; the
anchor commits on **Done**, one write per gesture.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .config_store import ConfigStore

# How close to a guide counts as "on it". Snapping is configurable, not
# always-on: the toggle lives next to this in Settings ▸ General.
SNAP_PX = 18


class StandInPill(QWidget):
    """A draggable imitation of the pill. Never the real one.

    **It must be parented to the dialog that owns it.** The dialog runs
    modal — moving the overlay is a mode, not a field — and Qt blocks mouse
    input to every window that is not the modal one or a transient child of
    it. A parentless stand-in is therefore un-draggable: the press never
    arrives, so nothing appears to happen and the "Drag me" invitation is a
    lie. Parenting puts it in the modal window's own chain, which exempts it.
    """

    def __init__(self, t: dict, snap: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent,
                         Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self._t = t
        self.snap = snap
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Same macOS Qt.Tool hiding rule as the real pill (#158): a drag that
        # deactivates the app must not vanish the ghost mid-move.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAccessibleName("Overlay position")
        self.resize(int(t["pill_min_w"]), int(t["pill_h"]))
        self._grab_offset = QPoint()

    # ---- dragging ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._grab_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        target = event.globalPosition().toPoint() - self._grab_offset
        if self.snap:
            target = self._snapped(target)
        self.move(target)

    def _snapped(self, target: QPoint) -> QPoint:
        """Soft-snap to the two guides that matter: horizontal centre, and the
        default bottom margin."""
        available = self.screen().availableGeometry()
        centre_x = available.center().x() - self.width() // 2
        margin_y = (available.bottom() - int(self._t["pill_margin_bottom"])
                    - self.height())
        x = centre_x if abs(target.x() - centre_x) <= SNAP_PX else target.x()
        y = margin_y if abs(target.y() - margin_y) <= SNAP_PX else target.y()
        return QPoint(x, y)

    def move_to_default(self) -> None:
        available = self.screen().availableGeometry()
        self.move(available.center().x() - self.width() // 2,
                  available.bottom() - int(self._t["pill_margin_bottom"])
                  - self.height())

    # ---- the anchor -------------------------------------------------------

    def anchor(self) -> tuple[float, float]:
        """The pill's centre as a fraction of available geometry."""
        available = self.screen().availableGeometry()
        centre = self.geometry().center()
        return (
            (centre.x() - available.left()) / max(available.width(), 1),
            (centre.y() - available.top()) / max(available.height(), 1),
        )

    # ---- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        surface = QColor(t["hud_surface"])
        surface.setAlpha(int(t["hud_alpha"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(surface)
        radius = float(t["pill_r"])
        p.drawRoundedRect(self.rect(), radius, radius)
        p.setPen(QColor(t["hud_text_dim"]))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drag me")
        p.end()


class MoveOverlayDialog(QDialog):
    """Drag / Done / Reset, with the stand-in floating above everything."""

    def __init__(self, store: ConfigStore, tokens: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("Cadent — Move overlay")
        self.store = store

        self.pill = StandInPill(tokens, store.config.overlay_snap, parent=self)
        if store.config.overlay_position_custom:
            self._place_from_config()
        else:
            self.pill.move_to_default()
        self.pill.show()

        layout = QVBoxLayout(self)
        message = QLabel("Drag the pill to where you want it, then choose Done.")
        message.setWordWrap(True)
        layout.addWidget(message)

        buttons = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self._reset)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        self.done_button = QPushButton("Done")
        self.done_button.setObjectName("Accent")
        self.done_button.setDefault(True)
        self.done_button.clicked.connect(self._commit)
        buttons.addWidget(self.done_button)
        layout.addLayout(buttons)

    def _place_from_config(self) -> None:
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        centre_x = available.left() + self.store.config.overlay_anchor_x * available.width()
        centre_y = available.top() + self.store.config.overlay_anchor_y * available.height()
        self.pill.move(round(centre_x - self.pill.width() / 2),
                       round(centre_y - self.pill.height() / 2))

    def _reset(self) -> None:
        """Back to bottom-centre at `pill_margin_bottom`."""
        self.pill.move_to_default()
        self.store.set_many({"overlay_position_custom": False,
                             "overlay_anchor_x": 0.5,
                             "overlay_anchor_y": 1.0})

    def _commit(self) -> None:
        x, y = self.pill.anchor()
        self.store.set_many({"overlay_position_custom": True,
                             "overlay_anchor_x": round(x, 4),
                             "overlay_anchor_y": round(y, 4)})
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.pill.close()
        super().closeEvent(event)
