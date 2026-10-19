"""The small Linux seams: run-at-login, the single-instance claim, the
hardware probes, and `DesktopEnv` over one Settings-portal snapshot
(spec §8.4, §10; ADR 0011, ADR 0014).
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import os
import shlex
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from ... import config
from ..base import APP_ID
from . import apps
from .portal import PortalConnection, PortalError, settings_read_all, unwrap_variants

log = logging.getLogger(__name__)


# ---- autostart: an XDG autostart entry (spec §8.4) ------------------------------------

class LinuxAutostart:
    """`$XDG_CONFIG_HOME/autostart/<APP_ID>.desktop`, enable writes it,
    disable deletes it, enabled is file existence — the Run-key adapter's
    shape. `Exec=`/`TryExec=` carry the absolute path resolved at write time
    (`$APPIMAGE` where set), and the autostart spec's own `TryExec` rule
    disables a deleted binary at login. A stale `Exec=` — the AppImage was
    moved — is rewritten in place on the next read: the checkbox said on, so
    it stays on and points at the binary actually running."""

    def __init__(self, autostart_dir: Path | None = None,
                 exec_line: Callable[[], str] = apps.exec_command) -> None:
        self._dir = autostart_dir or (apps.config_home() / "autostart")
        self._exec_line = exec_line

    @property
    def _path(self) -> Path:
        return self._dir / f"{APP_ID}.desktop"

    def run_command(self) -> str:
        return self._exec_line()

    def _text(self) -> str:
        exec_line = self._exec_line()
        return "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            "Name=Cadent",
            f"Exec={exec_line}",
            f"TryExec={shlex.split(exec_line)[0]}",
            f"Icon={APP_ID}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ])

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(self._text(), encoding="utf-8")
        else:
            self._path.unlink(missing_ok=True)

    def is_enabled(self) -> bool:
        if not self._path.exists():
            return False
        try:
            if self._path.read_text(encoding="utf-8") != self._text():
                self._path.write_text(self._text(), encoding="utf-8")   # heal
        except OSError:
            log.debug("could not heal the autostart entry", exc_info=True)
        return True


# ---- single instance: flock, plus a wake socket for the second launch (§10.2) ----------

class LinuxSingleInstance:
    """`fcntl.flock` on the data dir's lock file — darwin's, duplicated not
    shared (one file per platform). The kernel drops the lock with the
    process. Beside it a Unix socket the holder listens on: a second launch
    connects and hangs up, which is how a desktop with no tray icon still
    has a door to Settings (ADR 0014)."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path or (config.DATA_DIR / "cadent.lock")
        self._fd: int | None = None
        self._server: socket.socket | None = None

    @property
    def _socket_path(self) -> Path:
        return self._lock_path.with_suffix(".sock")

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def wake_other(self) -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(str(self._socket_path))
        except OSError:
            log.debug("could not reach the running instance", exc_info=True)

    def on_wake(self, callback: Callable[[], None]) -> None:
        if self._server is not None:
            return
        try:
            self._socket_path.unlink(missing_ok=True)   # ours: we hold the lock
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self._socket_path))
            server.listen(4)
        except OSError:
            log.debug("could not open the wake socket", exc_info=True)
            return
        self._server = server

        def serve() -> None:
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                conn.close()
                try:
                    callback()
                except Exception:
                    log.exception("wake callback failed")

        threading.Thread(target=serve, daemon=True, name="cadent-wake").start()


# ---- hardware probes (spec §6, ADR 0010) -------------------------------------------------

class LinuxHardware:
    """The Windows probes with `libcuda.so.1` where `nvcuda.dll` was, plus
    the driver-CUDA-version fill that gates the Parakeet pack edition. No
    Direct3D, no Metal, and no Vulkan probe — cleanup enumerates its own
    accelerator."""

    @staticmethod
    def _libcuda():
        return ctypes.CDLL("libcuda.so.1")

    def cuda_total_memory(self) -> float | None:
        try:
            cuda = self._libcuda()
            if cuda.cuInit(0) != 0:
                return None
            device = ctypes.c_int()
            if cuda.cuDeviceGet(ctypes.byref(device), 0) != 0:
                return None
            total = ctypes.c_size_t()
            get_mem = getattr(cuda, "cuDeviceTotalMem_v2", None) or cuda.cuDeviceTotalMem
            if get_mem(ctypes.byref(total), device) != 0:
                return None
            return total.value / (1024 ** 3)
        except Exception:
            log.debug("CUDA driver probe failed", exc_info=True)
            return None

    def dx12_gpu_present(self) -> bool:
        return False

    def processor_name(self) -> str:
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            log.debug("/proc/cpuinfo read failed", exc_info=True)
        return ""

    def nvidia_driver_present(self) -> bool:
        try:
            self._libcuda()
            return True
        except OSError:
            return False

    def metal_gpu_present(self) -> bool:
        return False

    def driver_cuda_version(self) -> int | None:
        try:
            cuda = self._libcuda()
            version = ctypes.c_int()
            if cuda.cuDriverGetVersion(ctypes.byref(version)) != 0:
                return None
            return int(version.value)
        except Exception:
            return None


# ---- desktop environment: one portal snapshot (spec §10, ADR 0014) ---------------------

APPEARANCE = "org.freedesktop.appearance"
GNOME_INTERFACE = "org.gnome.desktop.interface"
GNOME_A11Y = "org.gnome.desktop.a11y.interface"
SNAPSHOT_NAMESPACES = [APPEARANCE, GNOME_INTERFACE, GNOME_A11Y]

# org.freedesktop.appearance color-scheme values.
SCHEME_NO_PREFERENCE, SCHEME_DARK, SCHEME_LIGHT = 0, 1, 2


def tray_ink_for(desktop: str, scheme: int | None) -> str:
    """Desktop first, then scheme (ADR 0014): the stock GNOME shell keeps its
    top bar dark under both schemes, so GNOME is white always; everyone
    else follows the scheme — dark or no preference → white, light →
    black. "Invisible is worse than slightly wrong" picks white whenever
    the answer is unknown."""
    if desktop == "gnome":
        return "#ffffff"
    return "#000000" if scheme == SCHEME_LIGHT else "#ffffff"


def kdeglobals_animations(path: Path) -> bool | None:
    """`[KDE] AnimationDurationFactor` = 0 means animations are off; None
    when the file or the key is absent."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "KDE" and line.startswith("AnimationDurationFactor="):
            try:
                return float(line.split("=", 1)[1]) != 0.0
            except ValueError:
                return None
    return None


class SettingsSnapshot:
    """The `ReadAll` snapshot of the three namespaces every `DesktopEnv`
    read is a field of, kept fresh by one `SettingChanged` subscription."""

    def __init__(self, values: dict[str, dict] | None = None) -> None:
        self.values: dict[str, dict] = values or {}

    def get(self, namespace: str, key: str, default=None):
        return self.values.get(namespace, {}).get(key, default)

    def update(self, namespace: str, key: str, value) -> None:
        self.values.setdefault(namespace, {})[key] = value

    def color_scheme(self) -> int | None:
        value = self.get(APPEARANCE, "color-scheme")
        return int(value) if isinstance(value, int) else None

    def text_scale(self) -> float:
        value = self.get(GNOME_INTERFACE, "text-scaling-factor")
        try:
            return max(1.0, min(float(value), 2.25)) if value is not None else 1.0
        except (TypeError, ValueError):
            return 1.0

    def high_contrast(self) -> bool:
        contrast = self.get(APPEARANCE, "contrast")
        if contrast is not None:
            return int(contrast) == 1
        return bool(self.get(GNOME_A11Y, "high-contrast", False))

    def animations(self, kdeglobals: Path | None = None) -> bool:
        reduced = self.get(APPEARANCE, "reduced-motion")
        if reduced is not None:
            return int(reduced) != 1
        gnome = self.get(GNOME_INTERFACE, "enable-animations")
        if gnome is not None:
            return bool(gnome)
        if kdeglobals is not None:
            answer = kdeglobals_animations(kdeglobals)
            if answer is not None:
                return answer
        return True


class LinuxDesktop:
    """`DesktopEnv` on Linux. `connection` may be None (no session bus), in
    which case every read is the fallback adapter's answer and nothing is
    faulted. `desktop` is the normalized desktop name from tier detection;
    `request_permission` and `install_desktop_entry` are delegated to the
    callables the platform wires in, since the grants live with the portal
    sessions and the entry with the packaging seam."""

    def __init__(self, connection: PortalConnection | None, desktop: str, *,
                 request_permission: Callable[[], None] | None = None,
                 icons_dir: Path | None = None,
                 kdeglobals: Path | None = None) -> None:
        self._conn = connection
        self._desktop = desktop
        self._request_permission = request_permission
        self._icons_dir = icons_dir
        self._kdeglobals = kdeglobals if kdeglobals is not None else \
            apps.config_home() / "kdeglobals"
        self._snapshot = SettingsSnapshot()
        self._loaded = False
        self._watch = None
        self._on_ink_change: Callable[[], None] | None = None
        self._lock = threading.Lock()

    # ---- the snapshot ----------------------------------------------------------

    def _ensure_snapshot(self) -> SettingsSnapshot:
        with self._lock:
            if self._loaded or self._conn is None:
                return self._snapshot
            self._loaded = True
            try:
                (values,) = self._conn.send_message(settings_read_all(SNAPSHOT_NAMESPACES))
                self._snapshot = SettingsSnapshot({ns: unwrap_variants(keys)
                                                   for ns, keys in values.items()})
            except PortalError:
                log.info("Settings portal unavailable; using fallback answers",
                         exc_info=True)
            return self._snapshot

    def _on_setting_changed(self, msg) -> None:
        namespace, key, value = (unwrap_variants(v) for v in msg.body)
        if namespace not in SNAPSHOT_NAMESPACES:
            return
        was_ink = self.tray_ink()
        with self._lock:
            self._snapshot.update(namespace, key, value)
        if self._on_ink_change is not None and self.tray_ink() != was_ink:
            self._on_ink_change()

    # ---- DesktopEnv ------------------------------------------------------------

    def open_path(self, path: Path) -> None:
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            log.debug("xdg-open failed", exc_info=True)

    def request_permission(self) -> None:
        if self._request_permission is not None:
            try:
                self._request_permission()
            except Exception:
                log.debug("permission request failed", exc_info=True)

    def install_desktop_entry(self) -> None:
        if self._icons_dir is not None:
            apps.write_desktop_entry(self._icons_dir)

    def text_scale_factor(self) -> float:
        return self._ensure_snapshot().text_scale()

    def high_contrast(self) -> bool:
        return self._ensure_snapshot().high_contrast()

    def animations_enabled(self) -> bool:
        return self._ensure_snapshot().animations(self._kdeglobals)

    def tray_ink(self) -> str:
        return tray_ink_for(self._desktop, self._ensure_snapshot().color_scheme())

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        if self._conn is None or self._watch is not None:
            return
        from .portal import portal_signal_rule

        self._ensure_snapshot()
        self._on_ink_change = on_change
        self._watch = self._conn.subscribe(portal_signal_rule("Settings", "SettingChanged"),
                                           self._on_setting_changed)

    def stop_watching_tray_ink(self) -> None:
        watch, self._watch = self._watch, None
        self._on_ink_change = None
        if watch is not None:
            watch.close()


__all__ = ["LinuxAutostart", "LinuxSingleInstance", "LinuxHardware", "LinuxDesktop",
           "SettingsSnapshot", "tray_ink_for", "kdeglobals_animations", "sys"]
