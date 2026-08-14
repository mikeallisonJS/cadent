"""The model picker: two-line rows, one chip, shared by both surfaces.

Settings and the first-run wizard show the *same* list of speech models in the
same `QComboBox`. A model that reads one way during setup and another way in
Settings is two lists to maintain and two to trust — so the rendering (the
delegate) and the policy (which row is recommended, which is out of reach and
why) both live here, and each surface only says what is different about it.

Separate from `widgets.py`, which is the generic chrome every pane is built
from: a row, a card, a notice. This module knows what a *model* is.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from .. import hardware, models, platform, settings
from .widgets import CappedCombo

TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2
CHIP_ROLE = Qt.ItemDataRole.UserRole + 3
CHIP_TONE_ROLE = Qt.ItemDataRole.UserRole + 4

GOOD, WARN = "good", "warn"


def spoken_row(title: str, subtitle: str, chip: str = "") -> str:
    """One string carrying everything the eye gets (§8.4).

    The chip leads: it is the part that changes what the row *means*, and a
    screen-reader user meeting "Needs a graphics card" after two clauses of
    praise has already formed the wrong idea.
    """
    return ", ".join(part for part in (chip, title, subtitle) if part)


def add_model_row(combo: QComboBox, value: object, title: str, subtitle: str,
                  *, badge: str = "", warning: str = "",
                  enabled: bool = True) -> None:
    """Append one two-line row, with at most one chip.

    **Warning beats badge**, and the rule is here rather than in the delegate
    because it is a statement about models, not about painting: a model good
    enough to recommend is by definition not one to warn about, so a row
    wearing both would be the app disagreeing with itself.
    """
    index = combo.count()
    combo.addItem(title, value)
    chip, tone = (warning, WARN) if warning else (badge, GOOD)
    for role, data in ((TITLE_ROLE, title), (SUBTITLE_ROLE, subtitle),
                       (CHIP_ROLE, chip), (CHIP_TONE_ROLE, tone),
                       (Qt.ItemDataRole.AccessibleTextRole,
                        spoken_row(title, subtitle, chip))):
        combo.setItemData(index, data, role)
    if not enabled:
        # Shown and disabled rather than hidden: a missing row explains
        # nothing, and a disabled row that carries its own reason is not a
        # trap. Hiding it would make the feature undiscoverable to anyone who
        # later adds a graphics card (#111).
        combo.model().item(index).setEnabled(False)


class TwoLineDelegate(QStyledItemDelegate):
    """Title and one chip on line one; what-you-get and what-it-costs on line two.

    The chip shares line one rather than sitting right-aligned on line two,
    which is where the prototype put it first. Two things went wrong there:
    long subtitles elided into nothing, and the chip was suppressed on the
    selected row — hiding the warning on exactly the model the user had gone
    and chosen.
    """

    def __init__(self, t: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t
        self._pad_x = int(t["sp_3"])
        self._pad_y = int(t["sp_2"])
        # Tighter than sp_1: the two lines are one row, and a full spacing
        # step between them reads as two.
        self._gap = int(t["sp_1"]) - 1

    def _subtitle_font(self, base: QFont) -> QFont:
        font = QFont(base)
        font.setPointSizeF(float(self._t["fs_desc"]))
        return font

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        title_h = option.fontMetrics.height()
        subtitle_h = QFontMetrics(self._subtitle_font(option.font)).height()
        width = super().sizeHint(option, index).width()
        return QSize(width, self._pad_y * 2 + title_h + self._gap + subtitle_h)

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        t = self._t
        if selected and enabled:
            painter.fillRect(opt.rect, QColor(t["selected"]))
        title_colour = QColor(t["text"] if enabled else t["text_faint"])
        subtitle_colour = QColor(t["text_dim"] if enabled else t["text_faint"])

        area = opt.rect.adjusted(self._pad_x, self._pad_y,
                                 -self._pad_x, -self._pad_y)
        title_font = QFont(opt.font)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        metrics = painter.fontMetrics()
        line = QRect(area.left(), area.top(), area.width(), metrics.height())

        chip = index.data(CHIP_ROLE) or ""
        used = self._paint_chip(painter, line, chip, index.data(CHIP_TONE_ROLE),
                                enabled) if chip else 0
        painter.setFont(title_font)
        painter.setPen(title_colour)
        room = line.adjusted(0, 0, -used, 0)
        painter.drawText(room, int(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter),
                         metrics.elidedText(index.data(TITLE_ROLE) or "",
                                            Qt.TextElideMode.ElideRight,
                                            room.width()))

        painter.setFont(self._subtitle_font(opt.font))
        metrics = painter.fontMetrics()
        second = QRect(area.left(), line.bottom() + self._gap,
                       area.width(), metrics.height())
        painter.setPen(subtitle_colour)
        painter.drawText(second, int(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignVCenter),
                         metrics.elidedText(index.data(SUBTITLE_ROLE) or "",
                                            Qt.TextElideMode.ElideRight,
                                            second.width()))
        painter.restore()

    def _paint_chip(self, painter, line: QRect, text: str, tone: str,
                    enabled: bool) -> int:
        """Draw the chip at the right of line one; return the width it claimed.

        Chip colours carry meaning, so they hold on the selected row too —
        which is where a warning matters most.
        """
        t = self._t
        font = QFont(painter.font())
        font.setPointSizeF(float(t["fs_caps"]))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + int(t["sp_3"])
        height = metrics.height() + int(t["sp_1"])
        rect = QRect(line.right() - width,
                     line.top() + (line.height() - height) // 2, width, height)
        if tone == WARN:
            background, foreground = t["warning_soft_bg"], t["warning"]
        else:
            background, foreground = t["accent_soft_bg"], t["accent_text"]
        if not enabled:
            background, foreground = t["field"], t["text_faint"]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        return width + int(t["sp_3"])


def model_combo(t: dict) -> QComboBox:
    """A combo whose rows are two lines and a chip.

    Never editable: a paste-a-path combo box looks like a dropdown and behaves
    like a text field, and the one thing it was for — bring your own GGUF — is
    a file picker's job (#112).

    Capped and eliding for the same reason the microphone combo is (#106): a
    bring-your-own row is titled with a filename, and a filename is as long as
    it likes. The cap is on the *closed* control only — the popup still sizes
    to the delegate, which is where the two lines and the chip are read.
    """
    combo = CappedCombo()
    combo.setItemDelegate(TwoLineDelegate(t, combo))
    return combo


def fill_speech_models(combo: QComboBox, *, configured: str = "",
                       carve_out_engine: str = "") -> str:
    """Fill a combo with the speech list, chipped for this machine.

    The one place the list's *policy* lives, so Settings and the wizard cannot
    drift: which row is recommended, which is out of reach, and what a row that
    is out of reach says about itself.

    Returns the id wearing the "Recommended" chip, which is what a surface with
    nothing configured yet — the wizard — should preselect. Filling and
    selecting are separate because the selection is the only thing the two
    surfaces disagree about: Settings opens on what is running, setup opens on
    what this PC should take.

    `carve_out_engine` is Settings' escape hatch and the wizard's absence of
    one. The GPU probe is best-effort, so a user already on Parakeet must never
    be stranded on a setting with no way back to it — and the carve-out stays
    at *engine* granularity so they can still move between the two checkpoints
    rather than merely keep the one they have. During first-run setup there is
    nothing to be stranded on yet.
    """
    # The cheap probe, not `hardware.detect_cached()`: D3D12CreateDevice with
    # null arguments answers in microseconds and is the actual gate.
    gpu = platform.current().hardware.dx12_gpu_present()
    recommended = models.recommended_speech_model(
        hardware.suggest_for_this_machine().model)
    for model in models.listed_speech_models():
        runnable = (gpu or not settings.requires_gpu(model.engine)
                    or model.engine == carve_out_engine)
        add_model_row(combo, model.id, model.title, model.subtitle,
                      badge=(models.RECOMMENDED
                             if runnable and model.id == recommended else ""),
                      warning="" if runnable else models.NEEDS_A_GPU,
                      enabled=runnable)
    known = models.speech_model(configured)
    if configured and (known is None or not known.listed):
        # A hand-edited config.json may name a model we don't list; it is still
        # what is running, so it belongs in the list, saying where it came from
        # rather than posing as a curated rung.
        add_model_row(combo, configured, configured,
                      models.unlisted_subtitle(configured))
    return recommended
