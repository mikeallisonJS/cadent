"""The Linux `DesktopEnv` (spec M6 §10, ADR 0014): one Settings-portal
snapshot for everything, and the tray ink keyed desktop-then-scheme.

Every read is a field of one `org.freedesktop.portal.Settings.ReadAll`
snapshot taken at construction and kept fresh by the one `SettingChanged`
subscription on the shared jeepney connection:

- `tray_ink()`: GNOME → white always (its panel is dark under both schemes
  and the AppIndicator extension recolours nothing); elsewhere
  `org.freedesktop.appearance color-scheme` dark (1) → white, light (2) →
  black, no preference (0) → white. `contrast` never changes the ink.
- `text_scale_factor()` ← `org.gnome.desktop.interface text-scaling-factor`
  where proxied, else 1.0.
- `high_contrast()` ← `org.freedesktop.appearance contrast == 1`, falling
  back to `org.gnome.desktop.a11y.interface high-contrast`.
- `animations_enabled()` ← `reduced-motion` where present, else GNOME's
  `enable-animations`, else `kdeglobals [KDE] AnimationDurationFactor == 0`
  → False; True otherwise.
- `open_path` ← `xdg-open`; `request_permission` ← the Wayland run's, a
  no-op on X11.

No portal (no bus, no Settings backend) → the fallback adapter's answers,
no fault. Copy is desktop-independent; the tray-ink watcher's callback runs
on the portal thread, callers marshal.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import portal
from .portal import Bus, PortalError, unwrap_variants

log = logging.getLogger(__name__)

APPEARANCE = "org.freedesktop.appearance"
GNOME_INTERFACE = "org.gnome.desktop.interface"
GNOME_A11Y = "org.gnome.desktop.a11y.interface"
KDE_GLOBALS = "org.kde.kdeglobals.KDE"
NAMESPACES = (APPEARANCE, GNOME_INTERFACE, GNOME_A11Y, KDE_GLOBALS)

WHITE, BLACK = "#ffffff", "#000000"


def tray_ink_for(desktop_key: str | None, color_scheme: int | None) -> str:
    """The two-ink map (spec §10.1). Keyed desktop first: GNOME is white
    under both schemes; everyone else follows the scheme."""
    if desktop_key in ("gnome", "ubuntu", "budgie", "pantheon"):
        return WHITE
    if color_scheme == 2:
        return BLACK
    return WHITE


class LinuxDesktop:
    def __init__(self, bus: Bus | None, desktop_key: str | None,
                 request_permission: Callable[[], None] | None = None) -> None:
        self._bus = bus
        self._desktop_key = desktop_key
        self._request_permission = request_permission
        self._snapshot: dict[str, dict[str, Any]] = {}
        self._ink_callback: Callable[[], None] | None = None
        self._sub: int | None = None
        self.portal_present = False
        self._take_snapshot()

    # ---- the snapshot -------------------------------------------------------

    def _take_snapshot(self) -> None:
        if self._bus is None:
            return
        try:
            reply = self._bus.send_and_get_reply(portal.settings_read_all(NAMESPACES))
        except PortalError:
            log.info("Settings portal unavailable; desktop reads use fallbacks")
            return
        try:
            raw = reply.body[0]
        except (IndexError, TypeError):
            return
        snapshot: dict[str, dict[str, Any]] = {}
        for namespace, values in dict(raw).items():
            snapshot[str(namespace)] = {str(k): unwrap_variants(v)
                                        for k, v in dict(values).items()}
        self._snapshot = snapshot
        self.portal_present = True
        try:
            self._sub = self._bus.subscribe(portal.settings_changed_rule(),
                                            self._on_setting_changed)
        except PortalError:
            log.debug("SettingChanged subscription failed", exc_info=True)

    def _on_setting_changed(self, msg) -> None:
        try:
            namespace, key, value = msg.body[0], msg.body[1], unwrap_variants(msg.body[2])
        except (IndexError, TypeError):
            return
        before = self.tray_ink()
        self._snapshot.setdefault(str(namespace), {})[str(key)] = value
        if self._ink_callback is not None and self.tray_ink() != before:
            self._ink_callback()          # portal thread; the app marshals

    def _read(self, namespace: str, key: str, default: Any = None) -> Any:
        return self._snapshot.get(namespace, {}).get(key, default)

    # ---- the seam -----------------------------------------------------------

    def open_path(self, path: Path) -> None:
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            log.debug("xdg-open failed", exc_info=True)

    def request_permission(self) -> None:
        if self._request_permission is not None:
            self._request_permission()

    def text_scale_factor(self) -> float:
        value = self._read(GNOME_INTERFACE, "text-scaling-factor")
        try:
            scale = float(value) if value is not None else 1.0
        except (TypeError, ValueError):
            return 1.0
        return scale if 0.5 <= scale <= 4.0 else 1.0

    def high_contrast(self) -> bool:
        contrast = self._read(APPEARANCE, "contrast")
        if contrast is not None:
            return int(contrast) == 1
        return bool(self._read(GNOME_A11Y, "high-contrast", False))

    def animations_enabled(self) -> bool:
        reduced = self._read(APPEARANCE, "reduced-motion")
        if reduced is not None:
            return not bool(reduced)
        gnome = self._read(GNOME_INTERFACE, "enable-animations")
        if gnome is not None:
            return bool(gnome)
        factor = self._read(KDE_GLOBALS, "AnimationDurationFactor")
        if factor is not None:
            try:
                return float(factor) != 0
            except (TypeError, ValueError):
                return True
        return True

    def tray_ink(self) -> str:
        scheme = self._read(APPEARANCE, "color-scheme")
        try:
            scheme = int(scheme) if scheme is not None else None
        except (TypeError, ValueError):
            scheme = None
        return tray_ink_for(self._desktop_key, scheme)

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        self._ink_callback = on_change

    def stop_watching_tray_ink(self) -> None:
        self._ink_callback = None
