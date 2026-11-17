"""SingleInstance on Linux: an flock, plus a socket a second launch knocks on.

The lock is darwin's, duplicated rather than shared (spec §2.3). The kernel
drops it on process death. The knock is what lets a tray-less desktop reach
Settings by launching Cadent again (spec §10.2).
"""

from __future__ import annotations

import fcntl
import logging
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from ... import config

log = logging.getLogger(__name__)

KNOCK = b"settings\n"


class LinuxSingleInstance:
    def __init__(self, lock_path: Path | None = None,
                 socket_path: Path | None = None) -> None:
        self._lock_path = lock_path or (config.DATA_DIR / "cadent.lock")
        self._socket_path = socket_path or self._lock_path.with_suffix(".sock")
        self._fd: int | None = None
        self._server: socket.socket | None = None

    def acquire(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            self._knock()
            return False
        self._fd = fd
        return True

    def _knock(self) -> None:
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(1.0)
                client.connect(str(self._socket_path))
                client.sendall(KNOCK)
        except OSError:
            log.debug("no running Cadent answered the knock", exc_info=True)

    def serve_second_launch(self, on_launch: Callable[[], None]) -> None:
        # Holding the lock makes any socket file left here stale.
        self._socket_path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX)
        try:
            server.bind(str(self._socket_path))
        except OSError:
            log.warning("cannot listen for a second launch", exc_info=True)
            server.close()
            return
        server.listen(1)
        self._server = server
        threading.Thread(target=self._serve, args=(server, on_launch),
                         name="cadent-second-launch", daemon=True).start()

    @staticmethod
    def _serve(server: socket.socket, on_launch: Callable[[], None]) -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(1.0)
                    if conn.recv(64).startswith(KNOCK.strip()):
                        on_launch()
                except Exception:
                    log.debug("second-launch handler failed", exc_info=True)
