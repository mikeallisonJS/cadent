"""The overlay pill: the app's guaranteed feedback channel (spec §4).

Same shape and job as before — a small, click-through, bottom-centre HUD — but
it stops being decorative. It is the *primary* failure channel because Windows
11 Do Not Disturb silently suppresses tray toasts, and nothing suppresses our
own always-on-top window.

**Construction is hybrid.** The chrome is QSS: a child `QFrame#overlayPill`
inside the translucent top-level, so background, radius, hairline and padding
render from the same token set as every other surface. The indicators are
custom-painted child widgets reading the token dict *directly* — QSS has no
vocabulary for a frame-by-frame meter, and faking it with a stretched
QProgressBar fights the shape and gains nothing.

Three live defects this rebuild fixes rather than repaints:

- `self.screen()` on a never-shown widget is always the **primary** screen, so
  dictating into a window on a second monitor put the pill where you weren't
  looking. Position now resolves from the foreground window's rect.
- `show_processing()` didn't re-place or even re-show; it mutated `state` and
  rode the recording timer.
- `Overlay()` was constructed at startup but never shown until the first
  dictation, so Qt created the native window then — and the first pill of
  every session paid window-creation and first-paint cost no later one does.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from . import pill
from .config import Config
from .platform import Platform
from .theme import qss
from .theme.tokens import tokens


class _Glyph(QWidget):
    """Mode and failure glyphs, drawn by the same hand as the mark (§2).

    They only ever sit on `hud_surface`, which is dark in **both** themes, so
    the both-taskbar-polarity constraint the tray lives under does not apply.
    """

    def __init__(self, t: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t
        self.kind = pill.MIC
        self.setFixedSize(int(t["pill_glyph"]), int(t["pill_glyph"]))

    def _pill_alpha(self) -> int:
        return int(self._t["hud_alpha"])

    def set_tokens(self, t: dict) -> None:
        self._t = t
        self.setFixedSize(int(t["pill_glyph"]), int(t["pill_glyph"]))
        self.update()

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        unit = self.width() / 24          # the mark's 24-unit master grid
        colour = QColor(self._t["hud_danger"] if self.kind == pill.WARNING
                        else self._t["hud_text"])
        if self.kind == pill.WARNING:
            self._warning(p, unit, colour)
        else:
            self._mic(p, unit, colour, cleanup=self.kind == pill.CLEANUP)
        p.end()

    def _mic(self, p: QPainter, u: float, colour: QColor, cleanup: bool) -> None:
        # With cleanup on, the mic shifts left to make room for the flowing
        # lines. A subtle mark is no mark at 18px: the placeholder's diagonal
        # cut through the capsule was invisible, so the cleanup variant needs
        # a silhouette difference readable at a glance.
        dx = -4.5 * u if cleanup else 0.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(QRectF(9 * u + dx, 2.5 * u, 6 * u, 10.5 * u),
                          3 * u, 3 * u)
        pen = QPen(colour)
        pen.setWidthF(2 * u)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(5.5 * u + dx, 4 * u, 13 * u, 13 * u), 0, -180 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(QRectF(11 * u + dx, 16.5 * u, 2 * u, 3.5 * u), 0, 0)
        p.drawRoundedRect(QRectF(7.5 * u + dx, 19.6 * u, 9 * u, 2.2 * u),
                          1.1 * u, 1.1 * u)
        if cleanup:
            # Three lines flowing out of the mic. At 18px a subtle mark is no
            # mark — the placeholder's diagonal cut through the capsule was
            # invisible — so the cleanup variant differs in silhouette.
            for width, y in ((9, 7.5), (7, 12), (5, 16.5)):
                p.drawRoundedRect(QRectF(14 * u, y * u, width * u, 2 * u), u, u)

    def _warning(self, p: QPainter, u: float, colour: QColor) -> None:
        path = QPainterPath()
        path.moveTo(12 * u, 2.4 * u)
        path.lineTo(23 * u, 21.4 * u)
        path.lineTo(1 * u, 21.4 * u)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawPath(path)
        p.setBrush(QColor(self._t["hud_surface"]))
        p.drawRoundedRect(QRectF(10.9 * u, 8.5 * u, 2.2 * u, 7 * u), u, u)
        p.drawEllipse(QRectF(10.7 * u, 17 * u, 2.6 * u, 2.6 * u))


class Meter(QWidget):
    """Symmetric multi-bar equalizer — it reads as *voice* at a glance.

    Painted from the token dict directly rather than parsed back out of a
    stylesheet. The same widget renders the indeterminate sweep, because the
    meter **morphs** rather than swapping: recording -> transcribing is one
    continuous act.
    """

    # A fixed spectrum-ish profile, so the bars have shape instead of all
    # moving as one block. We have overall RMS, not an FFT — the profile plus
    # a slow phase rotation is what makes it read as a voice rather than a
    # single bar wearing stripes.
    _PROFILE = (0.45, 0.62, 0.80, 0.95, 1.00, 0.92, 0.78, 0.70, 0.82, 0.95,
                0.86, 0.68, 0.52, 0.40)

    def __init__(self, t: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t = t
        self.mode: str | None = pill.VOICE
        self.level_source: Callable[[], float] | None = None
        self._bars = [0.0] * int(t["meter_bars"])
        self._silent_since: float | None = None
        self._last_tick = time.monotonic()
        self._phase = 0.0
        self._apply_size()

    def _apply_size(self) -> None:
        t = self._t
        n, bw, gap = int(t["meter_bars"]), int(t["meter_bar_w"]), int(t["meter_gap"])
        self.setFixedSize(n * bw + (n - 1) * gap, int(t["meter_span"]))

    def _pill_alpha(self) -> int:
        return int(self._t["hud_alpha"])

    def set_tokens(self, t: dict) -> None:
        self._t = t
        self._bars = [0.0] * int(t["meter_bars"])
        self._apply_size()
        self.update()

    def set_mode(self, mode: str | None) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        if mode != pill.VOICE:
            self._silent_since = None
        self.update()

    def reset(self) -> None:
        self._bars = [0.0] * len(self._bars)
        self._silent_since = None
        self._phase = 0.0

    # ---- animation --------------------------------------------------------

    def tick(self) -> None:
        """One frame. Fast attack, slow decay: without decay the bars flicker
        at 30 fps; with it the meter looks alive between syllables."""
        now = time.monotonic()
        dt_ms = max(1.0, (now - self._last_tick) * 1000)
        self._last_tick = now
        self._phase += dt_ms / 900

        if self.mode == pill.VOICE:
            level = pill.meter_scale(
                self.level_source() if self.level_source else 0.0)
            self._track_silence(level, now)
            decay = dt_ms / max(1.0, float(self._t["meter_decay_ms"]))
            for i, profile in enumerate(self._profile_for(len(self._bars))):
                target = level * profile
                self._bars[i] = (target if target >= self._bars[i]
                                 else max(target, self._bars[i] - decay))
        elif self.mode == pill.INDETERMINATE:
            n = len(self._bars)
            for i in range(n):
                # A travelling bump across the same bars, same geometry.
                offset = (i / n - self._phase) % 1.0
                self._bars[i] = max(0.0, math.cos(offset * math.pi * 2)) ** 3
        self.update()

    def _profile_for(self, n: int) -> list[float]:
        return [self._PROFILE[i % len(self._PROFILE)] for i in range(n)]

    def _track_silence(self, level: float, now: float) -> None:
        """If the level holds at the floor while recording, the bars go muted —
        free diagnosis for the most common real failure, a wrong input device,
        which the old single-bar meter hid entirely."""
        if level > 0.02:
            self._silent_since = None
        elif self._silent_since is None:
            self._silent_since = now

    @property
    def silent(self) -> bool:
        if self._silent_since is None:
            return False
        held_ms = (time.monotonic() - self._silent_since) * 1000
        return held_ms >= float(self._t["meter_silence_ms"])

    # ---- painting ---------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bw, gap = int(t["meter_bar_w"]), int(t["meter_gap"])
        span, floor = float(t["meter_span"]), float(t["meter_floor_h"])
        radius = float(t["meter_bar_r"])
        centre_y = self.height() / 2

        if self.silent:
            brush = QColor(t["hud_meter_silent"])
        else:
            brush = QLinearGradient(0, 0, self.width(), 0)
            brush.setColorAt(0, QColor(t["hud_meter_a"]))
            brush.setColorAt(1, QColor(t["hud_meter_b"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush)
        for i, value in enumerate(self._bars):
            # A visible floor: bars never fully collapse, because a meter at
            # literal zero is indistinguishable from a frozen one.
            h = max(floor, value * span)
            p.drawRoundedRect(
                QRectF(i * (bw + gap), centre_y - h / 2, bw, h), radius, radius)
        p.end()


class _PillFrame(QFrame):
    """The pill's chrome.

    The border and radius come from the shared stylesheet; the *surface* is
    painted here because `hud_alpha` (248/250 of 255) has nowhere to live in
    a QSS `background:` colour. Near-opaque, not today's 230: the pill sits
    over white pages, video and code, and legibility beats translucency —
    there is no subpixel crispness left to trade away.
    """

    def __init__(self, parent: QWidget, alpha: int) -> None:
        super().__init__(parent)
        self.alpha = alpha

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = self.palette().window().color()
        colour.setAlpha(self.alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.end()
        super().paintEvent(event)


class Overlay(QWidget):
    """The always-on-top HUD. Click-through at all times."""

    def __init__(self, config: Config | None = None,
                 theme_tokens: dict | None = None,
                 platform: Platform | None = None) -> None:
        if platform is None:
            from . import platform as platform_pkg

            platform = platform_pkg.current()
        self._platform = platform
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool,
        )
        self._t = theme_tokens or tokens("dark")
        self._config = config or Config()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # PRD §5.7's click-through property, preserved at all times: there is
        # never a dead zone that eats clicks or a stray drag that relocates the
        # overlay. Moving it is a deliberate mode in Settings instead (§4.6).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # macOS hides Qt.Tool windows whenever the application is inactive —
        # and this app is always inactive while dictating (the target app owns
        # focus). Ignored on every other platform, so no gate (#158).
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        # A tool window with no focus and no interaction: marked as such rather
        # than announced (§8.4). Normal operation is covered by toasts, which
        # Windows announces itself.
        self.setAccessibleName("")

        self.level_source: Callable[[], float] | None = None
        self._policy = pill.PillPolicy(
            show_activity=self._config.show_overlay,
            min_visible_ms=int(self._t["min_visible_ms"]),
            transient_ms=int(self._t["transient_ms"]),
            paused_ms=int(self._t["paused_ms"]))
        self._cleanup = False
        self._realized = False

        self._build()
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._meter.tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_overlay)
        self._fade: QPropertyAnimation | None = None
        self._width_anim: QVariantAnimation | None = None

    # ---- construction ------------------------------------------------------

    def _build(self) -> None:
        margin = int(self._t["pill_shadow_margin"])
        outer = QHBoxLayout(self)
        outer.setContentsMargins(margin, margin, margin, margin)

        self.pill = _PillFrame(self, self._pill_alpha())
        self.pill.setObjectName("overlayPill")
        # Applied here as well as app-wide, so the chrome survives the
        # stylesheet being dropped under a Windows contrast theme — the pill
        # opts out of High Contrast (§4.6).
        self.pill.setStyleSheet(qss.render_pill(self._t))
        outer.addWidget(self.pill)

        inner = QHBoxLayout(self.pill)
        pad = int(self._t["pill_pad_h"])
        inner.setContentsMargins(pad, 0, pad, 0)
        inner.setSpacing(int(self._t["pill_gap"]))
        self._glyph = _Glyph(self._t, self.pill)
        self._meter = Meter(self._t, self.pill)
        self._meter.level_source = lambda: (
            self.level_source() if self.level_source else 0.0)
        self._label = QLabel("", self.pill)
        self._label.setObjectName(pill.PLAIN)
        inner.addStretch()
        inner.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(self._meter, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addStretch()

        self._shadow = QGraphicsDropShadowEffect(self)
        self._apply_shadow()
        self.pill.setGraphicsEffect(self._shadow)
        self.pill.setFixedHeight(int(self._t["pill_h"]))
        self._apply_frame(pill.frame(pill.HIDDEN))

    def _apply_shadow(self) -> None:
        t = self._t
        # The token is CSS-shaped so the same string can serve QSS; QColor
        # can't parse rgba() with a 0..1 alpha, so unpack it here.
        spec = str(t["hud_shadow"])
        r, g, b, a = (x.strip() for x in spec[5:-1].split(","))
        colour = QColor(int(r), int(g), int(b), round(float(a) * 255))
        self._shadow.setColor(colour)
        self._shadow.setBlurRadius(float(t["hud_shadow_blur"]))
        self._shadow.setOffset(0, float(t["hud_shadow_dy"]))

    def _pill_alpha(self) -> int:
        return int(self._t["hud_alpha"])

    def set_tokens(self, t: dict) -> None:
        """Follow a theme change. The pill is themed but never inverts — light
        mode gives it a *different dark*, because its background is the user's
        document (§4.7)."""
        self._t = t
        self.pill.setStyleSheet(qss.render_pill(t))
        self.pill.setFixedHeight(int(t["pill_h"]))
        self.pill.alpha = self._pill_alpha()
        self._glyph.set_tokens(t)
        self._meter.set_tokens(t)
        self._apply_shadow()
        self.update()

    # ---- lifecycle ---------------------------------------------------------

    def realize(self) -> None:
        """Create and position the native window at startup, held hidden.

        A requirement, not an optimization: without it Qt defers window
        creation to the first `show()`, so the first pill of every session
        pays window-creation and first-paint cost no later one does.
        """
        if self._realized:
            return
        self._place()
        self.setWindowOpacity(0.0)
        self.show()
        self.hide()
        self.setWindowOpacity(1.0)
        self._realized = True

    def set_cleanup(self, on: bool) -> None:
        self._cleanup = on
        if self._policy.state != pill.HIDDEN:
            self._glyph.set_kind(pill.CLEANUP if on else pill.MIC)

    def set_show_activity(self, show: bool) -> None:
        self._policy.show_activity = show

    # ---- the seven states --------------------------------------------------

    def show_recording(self) -> None:
        self._go(pill.RECORDING)

    def show_transcribing(self) -> None:
        self._go(pill.TRANSCRIBING)

    def show_cleaning(self) -> None:
        self._go(pill.CLEANING)

    def show_cancelled(self) -> None:
        self._go(pill.CANCELLED)

    def show_paused(self) -> None:
        self._go(pill.PAUSED)

    def show_failure(self, detail: str = "") -> None:
        self._go(pill.FAILURE, detail=detail)

    def hide_overlay(self) -> None:
        self._go(pill.HIDDEN)

    def _go(self, state: str, detail: str = "") -> None:
        target, delay = self._policy.request(state, cleanup=self._cleanup,
                                            detail=detail)
        if delay:
            # The floor defers the exit rather than cancelling it.
            QTimer.singleShot(delay, self.hide_overlay)
            return
        self._hide_timer.stop()
        if not target.visible:
            self._fade_out()
            return
        was_visible = self.isVisible()
        previous_width = self.pill.width()
        self._apply_frame(target)
        self._place()
        if was_visible:
            self._tween_width(previous_width, self.pill.width())
        if not self.isVisible():
            self.setWindowOpacity(0.0 if target.fade_in and self._animated() else 1.0)
            self.show()
        if target.fade_in and self._animated():
            self._animate_opacity(1.0, int(self._t["fade_in_ms"]))
        else:
            self.setWindowOpacity(1.0)
        self._frame_timer.start(round(1000 / int(self._t["fps"])))
        if target.timeout_ms:
            self._hide_timer.start(target.timeout_ms)

    def _apply_frame(self, target: pill.PillFrame) -> None:
        self._glyph.setVisible(target.glyph is not None)
        if target.glyph is not None:
            self._glyph.set_kind(target.glyph)
        self._meter.setVisible(target.meter is not None)
        self._meter.set_mode(target.meter)
        if target.meter == pill.VOICE and self._policy.state != pill.RECORDING:
            self._meter.reset()
        self._label.setVisible(bool(target.label))
        if self._label.objectName() != target.label_style:
            self._label.setObjectName(target.label_style)
            self._label.setStyleSheet(qss.render_pill(self._t))
        self._label.setText(self._fitted(target))
        self.pill.adjustSize()

    def _fitted(self, target: pill.PillFrame) -> str:
        """Elide rather than grow past `pill_max_w`.

        The cap is load-bearing, not cosmetic: if a failure label doesn't fit,
        the copy is wrong, not the pill.
        """
        if not target.label:
            return ""
        t = self._t
        room = int(t["pill_max_w"]) - 2 * int(t["pill_pad_h"])
        if target.glyph is not None:
            room -= int(t["pill_glyph"]) + int(t["pill_gap"])
        if target.meter is not None:
            room -= self._meter.width() + int(t["pill_gap"])
        metrics = QFontMetrics(self._label.font())
        return metrics.elidedText(target.label, Qt.TextElideMode.ElideRight,
                                  max(room, 40))

    # ---- geometry ----------------------------------------------------------

    def _target_width(self) -> int:
        """Fixed height, content-sized width, clamped to min/max.

        `pill_max_w` is load-bearing: "Speech model still loading" — the
        longest label anyone has proposed — measures ~215px with its glyph,
        and 280 leaves headroom for one more word and nothing else. If a
        failure label doesn't fit, the copy is wrong, not the pill.
        """
        hint = self.pill.sizeHint().width()
        return int(max(int(self._t["pill_min_w"]),
                       min(int(self._t["pill_max_w"]), hint)))

    def _place(self) -> None:
        screen = self._target_screen()
        available = screen.availableGeometry()
        margin = int(self._t["pill_shadow_margin"])
        width = self._target_width()
        height = int(self._t["pill_h"])

        if self._config.overlay_position_custom:
            centre_x = available.left() + self._config.overlay_anchor_x * available.width()
            centre_y = available.top() + self._config.overlay_anchor_y * available.height()
        else:
            centre_x = available.center().x()
            centre_y = (available.bottom() - int(self._t["pill_margin_bottom"])
                        - height / 2)

        # Pin the frame's width rather than letting the layout negotiate it:
        # a sizeHint the layout rounds up by 2px would push a right-anchored
        # pill past the clamp and off the screen edge.
        self.pill.setFixedWidth(width)

        # Grow symmetrically about the anchor, then clamp so a pill dragged
        # near an edge can't grow off-screen.
        left = round(centre_x - width / 2)
        top = round(centre_y - height / 2)
        left = max(available.left(), min(left, available.right() - width + 1))
        top = max(available.top(), min(top, available.bottom() - height + 1))

        # The translucent top-level exceeds the frame by the shadow margin on
        # every side, so _place must offset by it — otherwise the pill floats
        # 28px above its bottom margin.
        self.setGeometry(QRect(left - margin, top - margin,
                               width + 2 * margin, height + 2 * margin))

    def _target_screen(self):
        """The screen holding the window being dictated into.

        `self.screen()` on a never-shown widget is the *primary* screen, which
        is the live multi-monitor bug. Falls back to the cursor's screen, then
        primary.
        """
        rect = self._foreground_window_rect()
        if rect is not None:
            found = QGuiApplication.screenAt(rect.center())
            if found is not None:
                return found
        found = QGuiApplication.screenAt(QCursor.pos())
        return found or QGuiApplication.primaryScreen()

    def _foreground_window_rect(self) -> QRect | None:
        """The focused window's rect, or None when nothing is focused (or the
        platform has no probe yet)."""
        rect = self._platform.focused_app.window_rect()
        if rect is None:
            return None
        left, top, right, bottom = rect
        return QRect(QPoint(left, top), QPoint(right, bottom))

    # ---- motion ------------------------------------------------------------

    def _animated(self) -> bool:
        """When Windows animations are off, fades and tweens are skipped and
        states snap — but the meter keeps animating: it is information, not
        decoration, and stopping it removes the only proof the mic is live."""
        return self._platform.desktop.animations_enabled()

    def _animate_opacity(self, to: float, duration_ms: int) -> None:
        if self._fade is not None:
            self._fade.stop()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(duration_ms)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(to)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()

    def _tween_width(self, start: int, end: int) -> None:
        """A failure label must not snap the pill wider under your eye."""
        if start == end or not self._animated():
            return
        if self._width_anim is not None:
            self._width_anim.stop()
        margin = 2 * int(self._t["pill_shadow_margin"])
        anchor_x = self.geometry().center().x()
        top = self.geometry().top()

        def apply(value: int) -> None:
            self.pill.setFixedWidth(value)
            self.setGeometry(QRect(round(anchor_x - (value + margin) / 2), top,
                                   value + margin, self.height()))

        self._width_anim = QVariantAnimation(self)
        self._width_anim.setDuration(int(self._t["width_tween_ms"]))
        self._width_anim.setStartValue(start)
        self._width_anim.setEndValue(end)
        self._width_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._width_anim.valueChanged.connect(lambda v: apply(int(v)))
        self._width_anim.start()

    def _fade_out(self) -> None:
        """All exits fade, including the silent success exit — an instant
        disappearance reads as a glitch, a fade reads as completion."""
        self._frame_timer.stop()
        self._meter.reset()
        if not self.isVisible():
            return
        if not self._animated():
            self.hide()
            return
        self._animate_opacity(0.0, int(self._t["fade_out_ms"]))
        self._fade.finished.connect(self._hide_if_faded)

    def _hide_if_faded(self) -> None:
        if self.windowOpacity() <= 0.01:
            self.hide()
