"""WORKSPACE ▸ General: startup, microphone, appearance, overlay (spec §5.1)."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import a11y, pill, platform, settings
from ..overlay import Meter
from ..theme.manager import PREFERENCES
from .context import PaneContext
from .widgets import CappedCombo, Notice, caps, card, label, page_title, row

THEME_CHOICES = (("system", "Follow system"), ("light", "Light"), ("dark", "Dark"))

# The two lines below name an OS setting, so they are platform facts read from
# Capabilities (`theme_subtitle`, `high_contrast_reason`) rather than constants
# here: Windows has an app colour mode and a contrast theme, macOS has one
# Appearance control and Increase contrast.


class GeneralPane(QWidget):
    def __init__(self, ctx: PaneContext, high_contrast: bool = False) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        t = ctx.tokens
        self._caps = platform.current().capabilities
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_3"]))

        self.autostart = QCheckBox()
        self.autostart.setChecked(ctx.config.autostart)
        self.autostart.toggled.connect(
            lambda on: ctx.set("autostart", on))

        # Capped and eliding: real Windows device names run to 44 characters,
        # and a stock combo sizes to its widest item (#106).
        self.mic = CappedCombo()
        self.mic.addItem("System default", None)
        for name in ctx.devices:
            self.mic.addItem(name, name)
        if ctx.config.input_device is not None and \
                ctx.config.input_device not in ctx.devices:
            self.mic.addItem(f"{ctx.config.input_device} (not connected)",
                             ctx.config.input_device)
        self.mic.setCurrentIndex(max(self.mic.findData(ctx.config.input_device), 0))
        self.mic.currentIndexChanged.connect(self._on_device_changed)

        # The same live meter the wizard's microphone page carries (#105).
        # Picking a microphone is the same question in both places, and it is
        # the one setting whose correctness cannot be read off the control —
        # a device name tells you nothing about whether it hears you.
        self.level = Meter(t)
        self.level.level_source = (
            (lambda: ctx.mic_monitor.level) if ctx.mic_monitor else None)
        self.level.mode = pill.VOICE
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(round(1000 / int(t["fps"])))
        self._meter_timer.timeout.connect(self.level.tick)

        self.theme = QComboBox()
        for value, caption in THEME_CHOICES:
            self.theme.addItem(caption, value)
        self.theme.setCurrentIndex(
            max(self.theme.findData(ctx.config.theme), 0))
        self.theme.currentIndexChanged.connect(self._on_theme)

        # A quiet line for anything config.json disagrees with us
        # about in this pane's own fields (§7.4, §7.5).
        self.notice = Notice(t, "", [])
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        layout.addWidget(page_title("General"))

        # First card, because Settings is the only way back into the wizard
        # now that the tray item is gone (#110). "again" is what keeps it
        # reading as redo rather than as something still to do.
        self.wizard_button = QPushButton("Run setup wizard")
        self.wizard_button.clicked.connect(ctx.request_wizard)
        layout.addWidget(caps("Setup", t))
        layout.addWidget(card([
            row(t, "Setup", self.wizard_button,
                desc="Walk through microphone, model and hotkey again"),
        ]))

        # Every group is named now: Setup taking the bare-first-card slot would
        # otherwise leave these rows heading-less and reading as Setup's own.
        layout.addSpacing(int(t["sp_2"]))
        layout.addWidget(caps("Basics", t))
        layout.addWidget(card([
            row(t, self._caps.autostart_label, self.autostart,
                desc="Run Cadent when you sign in"),
            row(t, "Microphone", self.mic,
                desc="Which input device dictation records from",
                hint=settings.restart_hint("input_device")),
            row(t, "Say something to check it", self.level,
                desc="The bars follow your voice"),
            row(t, "Theme", self.theme, desc=self._caps.theme_subtitle),
        ]))
        # Carried *visibly* as well as as accessibleDescription: the control
        # stays enabled under a contrast theme, and the reason it currently
        # appears to do nothing has to be readable (§1.3).
        self.contrast_reason = label(self._caps.high_contrast_reason, "RowHint")
        self.contrast_reason.setVisible(False)
        layout.addWidget(self.contrast_reason)

        self.show_overlay = QCheckBox()
        self.show_overlay.setChecked(ctx.config.show_overlay)
        self.show_overlay.toggled.connect(
            lambda on: ctx.set("show_overlay", on))
        self.snap = QCheckBox()
        self.snap.setChecked(ctx.config.overlay_snap)
        self.snap.toggled.connect(lambda on: ctx.set("overlay_snap", on))
        self.move_button = QPushButton("Move overlay…")
        self.move_button.clicked.connect(ctx.request_move_overlay)

        layout.addSpacing(int(t["sp_2"]))
        layout.addWidget(caps("Overlay", t))
        layout.addWidget(card([
            # The toggle governs the *activity* states only — failures appear
            # regardless, so turning off the indicator never turns off the
            # alarm (§4.8).
            row(t, "Show overlay while dictating", self.show_overlay,
                desc="Failures still appear, so a failed dictation is never silent"),
            row(t, "Snap to guides", self.snap,
                desc="Soft-snap to the centre and the bottom margin when moving it"),
            row(t, "Overlay position", self.move_button,
                desc="Drag a stand-in pill; the real one stays click-through"),
        ]))
        layout.addStretch()

        self.set_high_contrast(high_contrast)

    # ---- the microphone meter ---------------------------------------------

    def _on_device_changed(self, _index: int) -> None:
        device = self.mic.currentData()
        self.ctx.set("input_device", device)
        if self.ctx.mic_monitor is not None and self._meter_timer.isActive():
            self.ctx.mic_monitor.start(device)

    def start_monitoring(self) -> None:
        """Listen, because this pane is what the user is looking at."""
        if self.ctx.mic_monitor is None or self._meter_timer.isActive():
            return
        self.ctx.mic_monitor.start(self.ctx.config.input_device)
        self._meter_timer.start()

    def stop_monitoring(self) -> None:
        """Let go of the microphone the moment this pane is not on screen.

        Settings is long-lived in a way a wizard page is not — it can sit open
        on a second monitor all afternoon — so the input stream is scoped to
        the pane being visible rather than to the window existing. Holding it
        any longer would keep the Windows mic-in-use indicator lit, which is a
        bad look for an app whose pitch is that nothing leaves the machine.
        """
        self._meter_timer.stop()
        if self.ctx.mic_monitor is not None:
            self.ctx.mic_monitor.stop()

    def _on_theme(self) -> None:
        value = self.theme.currentData()
        if value in PREFERENCES:
            self.ctx.set("theme", value)
            self.ctx.request_theme(value)

    def set_high_contrast(self, active: bool) -> None:
        """The Appearance control **stays enabled** under a contrast theme.

        It still saves and takes effect the moment High Contrast goes off, and
        it carries its reason both visibly and as its accessibleDescription.
        Disabling it would be the accessibility smell: Qt drops disabled
        widgets from the tab order, so the explanation would be unreachable by
        exactly the user who needs it (§1.3).
        """
        self.theme.setEnabled(True)
        self.contrast_reason.setVisible(active)
        subtitle, reason = self._caps.theme_subtitle, self._caps.high_contrast_reason
        if active:
            self.theme.setToolTip(reason)
            a11y.describe(self.theme, f"{subtitle}. {reason}")
        else:
            self.theme.setToolTip("")
            a11y.describe(self.theme, subtitle)
