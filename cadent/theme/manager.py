"""Owns which token column is live, and when the stylesheet is dropped (§1.2, §1.3).

Three inputs decide what the app looks like: the user's Appearance preference,
the Windows *app* colour mode, and whether a Windows contrast theme is active.
The last one outranks the other two — under a contrast theme the stylesheet is
removed entirely and Fusion draws from the system palette, which under a
contrast theme already *is* the GetSysColor contrast palette. We lose the
visual identity; the user gets a readable app. Never wrong.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from . import pixmaps, qss
from .tokens import tokens

log = logging.getLogger(__name__)

PREFERENCES = ("system", "light", "dark")

# Residual limit worth stating where anyone would look for it: native file
# dialogs are OS-drawn and follow the *Windows* setting regardless of our
# override, so any path-browsing dialog Cadent ever grows must pass
# QFileDialog.DontUseNativeDialog to stay in the app's theme. QMessageBox is a
# Qt widget and follows the palette, so it needs nothing (§1.2).

# Type-scale tokens multiplied by the Windows Text size setting (§8.5).
_TYPE_SCALE_KEYS = ("fs_display", "fs_title", "fs_row", "fs_body", "fs_desc",
                    "fs_caps")


def high_contrast_active(hints=None) -> bool:
    """Is a Windows contrast theme on?

    Detected by `contrastPreference()`, **never** by `colorScheme()`: Qt
    reports `ColorScheme.Unknown` under High Contrast, which makes the colour
    scheme a trigger for re-rendering but useless as a discriminator.
    """
    if hints is not None and hasattr(hints, "accessibility"):
        try:
            # PySide6 gotcha, reproducible: chaining
            # `app.styleHints().accessibility().contrastPreference()` raises
            # "Internal C++ object (QAccessibilityHints) already deleted".
            # The caller holds the QStyleHints reference; we hold this one.
            a11y_hints = hints.accessibility()
            return (a11y_hints.contrastPreference()
                    == Qt.ContrastPreference.HighContrast)
        except Exception:
            log.debug("contrastPreference() unavailable", exc_info=True)
    from .. import platform

    return platform.current().desktop.high_contrast()


def tracked_caps(text: str, t: dict, object_name: str = "Caps") -> QLabel:
    """A tracked-capitals label.

    Qt QSS has no `letter-spacing` and ignores it *silently* — the winning
    mockup's tracked capitals never actually shipped in the prototype that won
    it. Tracking can only come from code, so it comes from here.
    """
    label = QLabel(text.upper())
    label.setObjectName(object_name)
    font = label.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,
                          float(t["tracking_caps"]))
    label.setFont(font)
    return label


def repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet against a widget whose dynamic properties
    changed. Required after any property used in a selector moves (§1.1)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class ThemeManager(QObject):
    """Applies the stylesheet, and re-applies it whenever anything moves."""

    changed = Signal()   # theme or contrast state flipped: custom painters repaint

    def __init__(self, app: QApplication, preference: str = "system",
                 text_scale: float = 1.0) -> None:
        super().__init__(app)
        self._app = app
        self._preference = preference if preference in PREFERENCES else "system"
        self._text_scale = text_scale
        # Held for the lifetime of the manager — see high_contrast_active().
        self._hints = app.styleHints()
        self._a11y_hints = (self._hints.accessibility()
                            if hasattr(self._hints, "accessibility") else None)

        # Fusion unconditionally, on every Windows version: it is what the
        # design was proven on, the Win11 default draws its own control assets
        # and resists restyling, and on Windows 10 `windowsvista` cannot render
        # dark at all. Under a contrast theme Fusion costs nothing.
        app.setStyle("fusion")

        self._scheme = self._hints.colorScheme()
        self._high_contrast = high_contrast_active(self._hints)

        self._hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
        if self._a11y_hints is not None:
            try:
                self._a11y_hints.contrastPreferenceChanged.connect(
                    self._on_contrast_changed)
            except AttributeError:      # pragma: no cover - Qt < 6.10
                log.debug("contrastPreferenceChanged unavailable")

        self._apply_color_scheme()
        self.apply()

    # ---- state -----------------------------------------------------------

    @property
    def preference(self) -> str:
        return self._preference

    @property
    def high_contrast(self) -> bool:
        return self._high_contrast

    @property
    def theme(self) -> str:
        """The live colour column: "light" or "dark"."""
        if self._preference in ("light", "dark"):
            return self._preference
        # Written Light -> light, else -> dark. The naive inverse silently
        # serves light tokens under a dark contrast theme, because Qt reports
        # ColorScheme.Unknown there (§1.3).
        return "light" if self._scheme == Qt.ColorScheme.Light else "dark"

    @property
    def tokens(self) -> dict:
        """The live token dict, for the custom painters that cannot read QSS."""
        t = dict(tokens(self.theme))
        if self._text_scale != 1.0:
            for key in _TYPE_SCALE_KEYS:
                t[key] = round(float(t[key]) * self._text_scale, 1)
        return t

    # ---- changing it ------------------------------------------------------

    def set_preference(self, preference: str) -> None:
        if preference not in PREFERENCES or preference == self._preference:
            return
        self._preference = preference
        self._apply_color_scheme()
        self.apply()

    def _apply_color_scheme(self) -> None:
        """Push the preference at Qt so the palette — and with it the native
        dark title bar — follows. Qt 6.8+."""
        if self._preference == "system":
            self._hints.unsetColorScheme()
        else:
            self._hints.setColorScheme(Qt.ColorScheme.Light
                                       if self._preference == "light"
                                       else Qt.ColorScheme.Dark)

    def apply(self) -> None:
        """Render and install the stylesheet — or drop it under High Contrast.

        Re-applying is required even when only the palette moved: QSS resolves
        `palette(<role>)` brushes into a per-widget cache that nothing
        invalidates on ApplicationPaletteChange, so a palette change alone
        leaves existing widgets stale while new ones update.
        """
        if self._high_contrast:
            self._app.setStyleSheet("")
            return
        t = self.tokens
        self._app.setStyleSheet(qss.render(t, pixmaps.generate(self.theme, t)))

    # ---- signals ----------------------------------------------------------

    def _on_color_scheme_changed(self, scheme: Qt.ColorScheme) -> None:
        """Decide off the signal's argument, never QGuiApplication.palette():
        the old palette is still in effect when the signal fires."""
        self._scheme = scheme
        # colorSchemeChanged also fires on a contrast-theme toggle, so this
        # handler runs at the right moment for free — as a trigger only.
        self._high_contrast = high_contrast_active(self._hints)
        self.apply()
        self.changed.emit()

    def _on_contrast_changed(self, *_args) -> None:
        self._high_contrast = high_contrast_active(self._hints)
        self.apply()
        self.changed.emit()
