"""DesktopEnv on Linux: one Settings-portal snapshot and its watch (spec §10).

Every read except `open_path` is a field of a single `ReadAll` taken the first
time anything asks, kept fresh by one `SettingChanged` subscription on the
shared connection. With no portal the snapshot stays empty and the fallback
adapter's answers stand — a missing portal is not a fault.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from jeepney import Message

from .. import fallback
from .portal import SETTINGS, Portal, PortalError, read_all_message, signal_rule

log = logging.getLogger(__name__)

APPEARANCE = "org.freedesktop.appearance"
GNOME_INTERFACE = "org.gnome.desktop.interface"
GNOME_A11Y = "org.gnome.desktop.a11y.interface"
KDE_GLOBALS = "org.kde.kdeglobals.KDE"
NAMESPACES = [APPEARANCE, GNOME_INTERFACE, GNOME_A11Y, KDE_GLOBALS]

WHITE, BLACK = "#ffffff", "#000000"
COLOR_SCHEME_LIGHT = 2

Snapshot = dict[tuple[str, str], object]


def snapshot_from(body: tuple) -> Snapshot:
    """`ReadAll`'s `a{sa{sv}}` reply as {(namespace, key): value}."""
    return {(ns, key): value[1]
            for ns, entries in (body[0] if body else {}).items()
            for key, value in entries.items()}


class PortalDesktop(fallback.NullDesktop):
    def __init__(self, portal: Portal, desktops: tuple[str, ...] = ()) -> None:
        self._portal = portal
        self._gnome = "gnome" in desktops
        self._values: Snapshot = {}
        self._loaded = False
        self._lock = threading.Lock()
        self._changes = threading.Lock()
        self._pending: list[tuple[str, str, object]] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._on_ink_change: Callable[[], None] | None = None

    # -- snapshot --------------------------------------------------------

    def _snapshot(self) -> Snapshot:
        with self._lock:
            if not self._loaded:
                self._loaded = True
                self._load()
            return self._values

    def _load(self) -> None:
        # Changes that land while ReadAll is in flight are newer than its
        # reply, so they are held and replayed over it.
        with self._changes:
            self._pending = []
        try:
            self._subscribe()
            loaded = snapshot_from(
                self._portal.call(read_all_message(NAMESPACES)))
        except PortalError as exc:
            log.info("no settings snapshot, using fallback answers: %s", exc)
            loaded = {}
        with self._changes:
            self._values.update(loaded)
            for namespace, key, value in self._pending or []:
                self._values[(namespace, key)] = value
            self._pending = None

    def _subscribe(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._portal.subscribe_signal(
                signal_rule(SETTINGS, "SettingChanged"), self._on_changed)

    def _on_changed(self, msg: Message) -> None:
        namespace, key, value = msg.body
        with self._changes:
            if self._pending is not None:
                self._pending.append((namespace, key, value[1]))
            else:
                self._values[(namespace, key)] = value[1]
        if (namespace, key) == (APPEARANCE, "color-scheme") \
                and self._on_ink_change is not None:
            self._on_ink_change()

    def _get(self, namespace: str, key: str) -> object | None:
        return self._snapshot().get((namespace, key))

    # -- reads -----------------------------------------------------------

    def text_scale_factor(self) -> float:
        value = self._get(GNOME_INTERFACE, "text-scaling-factor")
        return float(value) if isinstance(value, (int, float)) and value > 0 else 1.0

    def high_contrast(self) -> bool:
        contrast = self._get(APPEARANCE, "contrast")
        if contrast is not None:
            return contrast == 1
        return self._get(GNOME_A11Y, "high-contrast") is True

    def animations_enabled(self) -> bool:
        reduced = self._get(APPEARANCE, "reduced-motion")
        if reduced is not None:
            return reduced != 1
        gnome = self._get(GNOME_INTERFACE, "enable-animations")
        if gnome is not None:
            return bool(gnome)
        return self._get(KDE_GLOBALS, "AnimationDurationFactor") != 0

    def tray_ink(self) -> str:
        """GNOME's panel is dark in every scheme; elsewhere the scheme decides
        and "no preference" reads as dark. `contrast` never changes it."""
        if self._gnome:
            return WHITE
        return BLACK if self._get(APPEARANCE, "color-scheme") == COLOR_SCHEME_LIGHT \
            else WHITE

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        self._on_ink_change = on_change
        self._snapshot()
        try:
            self._subscribe()
        except PortalError as exc:
            log.info("cannot watch the colour scheme: %s", exc)

    def stop_watching_tray_ink(self) -> None:
        self._on_ink_change = None

    def open_path(self, path: Path) -> None:
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            log.debug("xdg-open failed", exc_info=True)

    def request_permission(self) -> None:
        pass
