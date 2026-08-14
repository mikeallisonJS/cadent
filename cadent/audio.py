"""Microphone capture: start on hotkey press, stop on release, return 16 kHz mono float32.

Two shapes, because the app asks the microphone two different questions.
`Recorder` is push-to-talk and buffers what it hears. `LevelMonitor` answers
"is this the right microphone?" — a level with no recording, while nobody is
holding anything — which the recorder cannot do, because it only has a level
while it is capturing a dictation.
"""

from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    """Push-to-talk recorder. start() on keydown, stop() on keyup -> np.ndarray."""

    def __init__(self, sample_rate: int = 16_000, device: str | None = None,
                 max_seconds: int = 120) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.max_seconds = max_seconds
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.level: float = 0.0  # RMS of the latest chunk, for the overlay animation

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        with self._lock:
            if sum(len(c) for c in self._chunks) < self.max_seconds * self.sample_rate:
                self._chunks.append(indata[:, 0].copy())
                self.level = float(np.sqrt(np.mean(indata[:, 0] ** 2)))

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        try:
            self._stream = self._open(self.device)
        except Exception:
            if self.device is None:
                raise
            # Configured device gone (unplugged headset): fall back to default.
            self._stream = self._open(None)
        self._stream.start()

    def _open(self, device: str | None) -> sd.InputStream:
        return sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._callback,
        )

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)

    @staticmethod
    def list_devices() -> list[dict]:
        return [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]


class LevelMonitor:
    """A live input level, buffering nothing.

    Opened while the wizard's microphone page is showing and closed when it
    leaves, so speaking answers "is this the right microphone?" on the spot.
    Deliberately not a mode on `Recorder`: this keeps no audio at all, which
    is the property that makes it safe to hold open on a settings page.

    Every failure lands on a flat meter rather than an exception. No mic, a
    device held exclusively by something else, an unplugged headset still
    named in `config.json` — none of them is worth taking the wizard down
    for, and a flat meter is what the user would see in those cases anyway.
    """

    def __init__(self, sample_rate: int = 16_000) -> None:
        self.sample_rate = sample_rate
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._device: str | None = None
        self._wanted = False        # a surface is showing a meter right now
        self.level: float = 0.0     # RMS of the latest block

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        with self._lock:
            self.level = float(np.sqrt(np.mean(indata[:, 0] ** 2)))

    def start(self, device: str | None) -> None:
        """Listen to `device`, replacing whatever was open before."""
        self._close()
        self._device = device
        self._wanted = True
        self._listen()

    def suspend(self) -> None:
        """Yield the device, remembering that a meter still wants it.

        A dictation takes the microphone the moment the hotkey goes down, on
        the hotkey worker thread and *before* anything is emitted — so the
        settings meter has to let go on the way in rather than in response to
        a signal that arrives after the recorder already opened the device.
        """
        self._close()

    def resume(self) -> None:
        """Take it back, unless the meter has since been stopped outright."""
        if self._wanted and self._stream is None:
            self._listen()

    def _listen(self) -> None:
        try:
            self._stream = self._open(self._device)
        except Exception:
            if self._device is None:
                log.debug("no input device to monitor", exc_info=True)
                return
            # Configured device gone (unplugged headset): fall back to
            # default, the same way Recorder.start does.
            try:
                self._stream = self._open(None)
            except Exception:
                log.debug("no input device to monitor", exc_info=True)
                return
        self._stream.start()

    def _open(self, device: str | None) -> sd.InputStream:
        return sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._callback,
        )

    def stop(self) -> None:
        """Done with the microphone — the pane closed, the page moved on."""
        self._wanted = False
        self._close()

    def _close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self.level = 0.0
