"""One Cadent per user: `fcntl.flock` (spec §8.4), duplicated from darwin's.

The kernel drops the lock with the process, so there is no stale state. A
second launch also knocks on a socket beside the lock; the running instance
decides what that means (open Settings when there is no tray to reach).
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


class FlockSingleInstance:
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
            self._knock()
            return False
        self._fd = fd
        return True

    def _knock(self) -> None:
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(1)
                client.connect(str(self._socket_path))
        except OSError:
            log.debug("no running instance to tell about this launch")

    def listen(self, on_second_launch: Callable[[], None]) -> None:
        """Call `on_second_launch` (on a worker thread) for every later
        launch. Only the lock holder gets here, so unlinking a stale socket
        file is safe."""
        path = self._socket_path
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX)
        try:
            server.bind(str(path))
        except OSError:
            server.close()
            log.exception("could not listen for second launches")
            return
        server.listen()
        self._server = server

        def serve() -> None:
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                conn.close()
                on_second_launch()

        threading.Thread(target=serve, name="cadent-second-launch",
                         daemon=True).start()
