"""The small Linux seams: SingleInstance (`flock`) and Autostart (XDG entry)
— spec M6 §8.4, ADR 0011.

**SingleInstance** is darwin's `fcntl.flock` on `DATA_DIR/cadent.lock`,
duplicated rather than shared (one file per platform), plus the second
door a tray-less desktop needs (§10.2): the holder writes its pid into the
lock file; a second launch sends it `SIGUSR1`, and the holder's watcher
(a `signal.set_wakeup_fd` pipe read on its own thread — the only way a
signal reaches Python while Qt's loop owns the main thread) calls back.

**Autostart** is an XDG autostart entry, `Exec=`/`TryExec=` the absolute path
read from `$APPIMAGE` at write time (desktop entries expand no variables)
where set, else `sys.executable`; a stale `Exec=` is rewritten in place on
the next `set_enabled(True)` / `is_enabled()`, and `TryExec` disables a
deleted tarball or a moved AppImage at login rather than erroring.
"""

from __future__ import annotations

import logging
import os
import shlex
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "com.mikeallisonjs.cadent"


# ---- single instance ------------------------------------------------------------

class LinuxSingleInstance:
    def __init__(self, lock_path: Path | None = None) -> None:
        if lock_path is None:
            from ... import config

            lock_path = config.DATA_DIR / "cadent.lock"
        self._lock_path = lock_path
        self._fd: int | None = None
        self._watch_thread: threading.Thread | None = None

    def acquire(self) -> bool:
        import fcntl  # Unix-only; here so the autostart half imports anywhere

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        # The holder's pid, for `notify_running()`; the lock, not the pid,
        # is what says we are alone.
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        self._fd = fd
        return True

    def holder_pid(self) -> int | None:
        try:
            text = self._lock_path.read_text(encoding="utf-8").strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None

    def notify_running(self) -> bool:
        pid = self.holder_pid()
        if not pid or pid == os.getpid():
            return False
        try:
            os.kill(pid, signal.SIGUSR1)
            return True
        except OSError:
            return False

    def watch(self, on_second_launch: Callable[[], None]) -> None:
        if self._watch_thread is not None:
            return
        try:
            read_fd, write_fd = os.pipe()
            os.set_blocking(write_fd, False)
            signal.signal(signal.SIGUSR1, lambda *_a: None)   # unblock the default action
            signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
        except (ValueError, OSError):
            log.debug("second-launch watcher unavailable off the main thread",
                      exc_info=True)
            return

        def pump() -> None:
            while True:
                try:
                    data = os.read(read_fd, 64)
                except OSError:
                    return
                if not data:
                    return
                if signal.SIGUSR1 in data:
                    try:
                        on_second_launch()
                    except Exception:
                        log.exception("second-launch callback failed")

        self._watch_thread = threading.Thread(target=pump, name="cadent-second-launch",
                                              daemon=True)
        self._watch_thread.start()


# ---- autostart ------------------------------------------------------------------

def launch_command(env=None) -> list[str]:
    """What login runs: the AppImage's real path where `$APPIMAGE` is set
    (its `sys.executable` is an ephemeral `/tmp/.mount_*`), else the frozen
    binary, else the venv interpreter on the module entrypoint."""
    env = os.environ if env is None else env
    appimage = env.get("APPIMAGE")
    if appimage:
        return [appimage]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "cadent"]


def autostart_dir(env=None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
    return Path(base) / "autostart"


class LinuxAutostart:
    """`~/.config/autostart/com.mikeallisonjs.cadent.desktop` — enable writes
    it, disable deletes it, enabled is file existence; a stale `Exec=` is
    healed in place, because desktop entries cannot follow a moved AppImage
    on their own."""

    def __init__(self, directory: Path | None = None, env=None) -> None:
        self._env = os.environ if env is None else env
        self._dir = directory or autostart_dir(self._env)

    @property
    def path(self) -> Path:
        return self._dir / f"{APP_ID}.desktop"

    def run_command(self) -> str:
        return shlex.join(launch_command(self._env))

    def _entry(self) -> str:
        command = launch_command(self._env)
        exec_line = " ".join(_desktop_quote(part) for part in command)
        return ("[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Cadent\n"
                "Comment=Push-to-talk dictation\n"
                f"Exec={exec_line}\n"
                f"TryExec={command[0]}\n"
                f"Icon={APP_ID}\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n")

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self._entry(), encoding="utf-8")
        else:
            self.path.unlink(missing_ok=True)

    def is_enabled(self) -> bool:
        if not self.path.exists():
            return False
        # Path healing (§8.4): an entry pointing at a path that moved is
        # rewritten to where we run from now — an enabled entry stays enabled.
        try:
            current = self.path.read_text(encoding="utf-8")
        except OSError:
            return True
        if current != self._entry():
            try:
                self.path.write_text(self._entry(), encoding="utf-8")
            except OSError:
                log.debug("could not heal the autostart entry", exc_info=True)
        return True


def _desktop_quote(part: str) -> str:
    """Desktop-entry Exec quoting: double quotes around anything with a
    reserved character, backslashes and quotes escaped inside."""
    if not any(ch in part for ch in " \t\n\"'\\><~|&;$*?#()`"):
        return part
    escaped = part.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`") \
        .replace("$", "\\$")
    return f'"{escaped}"'
