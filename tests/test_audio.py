"""Microphone capture (#105).

`Recorder` is push-to-talk: it opens on a hotkey press and buffers. The
wizard's microphone page needs the opposite — a level with no recording, while
nobody is holding anything — which is why `LevelMonitor` is its own thing
rather than a mode on the recorder.
"""

import numpy as np
import pytest

from cadent import audio


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def feed(self, samples):
        """Deliver a block the way PortAudio would, on its own thread."""
        block = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.kwargs["callback"](block, len(block), None, None)


@pytest.fixture
def fake_sd(monkeypatch):
    made = []

    def input_stream(**kwargs):
        stream = FakeStream(**kwargs)
        made.append(stream)
        return stream

    monkeypatch.setattr(audio.sd, "InputStream", input_stream)
    return made


def test_the_monitor_reports_the_level_of_what_it_hears(fake_sd):
    monitor = audio.LevelMonitor()
    monitor.start(None)
    assert monitor.level == 0.0

    fake_sd[0].feed([0.5, -0.5, 0.5, -0.5])
    assert monitor.level == pytest.approx(0.5)

    fake_sd[0].feed([0.0, 0.0, 0.0, 0.0])
    assert monitor.level == pytest.approx(0.0)


def test_the_monitor_opens_the_device_it_was_given(fake_sd):
    monitor = audio.LevelMonitor()
    monitor.start("Rode NT-USB Mini")
    assert fake_sd[0].kwargs["device"] == "Rode NT-USB Mini"
    assert fake_sd[0].started


def test_starting_again_re_points_at_the_new_device(fake_sd):
    """The device combo is right beside the meter, so switching input has to
    move the meter with it — that is the question the page exists to answer."""
    monitor = audio.LevelMonitor()
    monitor.start(None)
    monitor.start("Rode NT-USB Mini")
    assert fake_sd[0].closed
    assert len(fake_sd) == 2
    assert fake_sd[1].kwargs["device"] == "Rode NT-USB Mini"


def test_stopping_closes_the_stream_and_flattens_the_level(fake_sd):
    monitor = audio.LevelMonitor()
    monitor.start(None)
    fake_sd[0].feed([0.5, -0.5])
    monitor.stop()
    assert fake_sd[0].closed
    assert monitor.level == 0.0


def test_stopping_twice_is_harmless(fake_sd):
    monitor = audio.LevelMonitor()
    monitor.start(None)
    monitor.stop()
    monitor.stop()


def test_suspending_yields_the_device_and_resuming_takes_it_back(fake_sd):
    """A dictation must never contend with a settings meter for the input.

    `_on_start` opens the recorder on the hotkey worker thread *before* it
    emits `started`, so anything listening to that signal yields the device
    after it has already been taken. Suspend is called on the way in instead,
    which is why it remembers the device rather than needing to be told it
    again.
    """
    monitor = audio.LevelMonitor()
    monitor.start("Rode NT-USB Mini")
    monitor.suspend()
    assert fake_sd[0].closed
    assert monitor.level == 0.0

    monitor.resume()
    assert len(fake_sd) == 2
    assert fake_sd[1].kwargs["device"] == "Rode NT-USB Mini"
    assert fake_sd[1].started


def test_resuming_something_that_was_stopped_stays_stopped(fake_sd):
    """Stop is a decision — the pane closed, the page moved on. A dictation
    ending must not reopen a microphone nobody is looking at any more."""
    monitor = audio.LevelMonitor()
    monitor.start(None)
    monitor.stop()
    monitor.resume()
    assert len(fake_sd) == 1


def test_resuming_when_nothing_ever_started_does_nothing(fake_sd):
    audio.LevelMonitor().resume()
    assert fake_sd == []


def test_suspending_twice_leaves_one_stream_to_resume(fake_sd):
    monitor = audio.LevelMonitor()
    monitor.start(None)
    monitor.suspend()
    monitor.suspend()
    monitor.resume()
    assert len(fake_sd) == 2


def test_a_machine_with_no_microphone_leaves_a_flat_meter(monkeypatch):
    """No mic, or a device held exclusively by something else. The page must
    still open — a flat meter is the same thing the user would see anyway, and
    it is not worth taking the wizard down for."""
    def boom(**_kwargs):
        raise OSError("no default input device")

    monkeypatch.setattr(audio.sd, "InputStream", boom)
    monitor = audio.LevelMonitor()
    monitor.start(None)
    assert monitor.level == 0.0
    monitor.stop()


def test_a_named_device_that_is_gone_falls_back_to_the_default(monkeypatch):
    """An unplugged headset still in config.json. Mirrors Recorder.start."""
    made = []

    def input_stream(**kwargs):
        if kwargs["device"] is not None:
            raise OSError("device unavailable")
        stream = FakeStream(**kwargs)
        made.append(stream)
        return stream

    monkeypatch.setattr(audio.sd, "InputStream", input_stream)
    monitor = audio.LevelMonitor()
    monitor.start("Unplugged Headset")
    assert made and made[0].kwargs["device"] is None
    assert made[0].started
