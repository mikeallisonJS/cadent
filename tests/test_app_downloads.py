"""The download wiring in app.py (#114, #115).

`CadentApp.__init__` builds a QApplication, a tray icon, an overlay and a
keyboard hook — none of which a test of "what happens when a download is
cancelled" has any use for. The methods under test read a handful of
attributes between them, so the object is built without running `__init__` and
given exactly those. The narrowness is the point: if one of these methods grows
a dependency, this file says so by failing to build.
"""

import threading
import types

import pytest

from cadent import app as app_mod
from cadent import stt
from cadent.config import Config
from cadent.downloads import Progress


class Sig:
    """A bridge signal, recorded rather than delivered."""

    def __init__(self) -> None:
        self.emitted: list[tuple] = []

    def emit(self, *args) -> None:
        self.emitted.append(args)


class FakeTray:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.shown: list[tuple] = []

    def message(self, title, body, *_rest) -> None:
        self.messages.append(title)

    def set_download(self, what, progress=None) -> None:
        self.shown.append((what, progress))


class FakeWizard:
    def __init__(self) -> None:
        self.cancelled = 0
        self.finished: list[tuple] = []
        self.readings: list[Progress] = []

    def mark_download_cancelled(self) -> None:
        self.cancelled += 1

    def mark_download_finished(self, ok, detail="") -> None:
        self.finished.append((ok, detail))

    def report_progress(self, reading) -> None:
        self.readings.append(reading)

    def isVisible(self) -> bool:  # noqa: N802 (Qt naming)
        return True


@pytest.fixture
def app():
    instance = app_mod.CadentApp.__new__(app_mod.CadentApp)
    instance.config = Config()
    instance.tray = FakeTray()
    instance.bridge = types.SimpleNamespace(
        stt_failed=Sig(), stt_loaded=Sig(), gpu_offer=Sig(),
        download_progress=Sig(), download_finished=Sig())
    instance._stt = None
    instance._stt_lock = threading.Lock()
    instance._downloads = {}
    instance.wizard = None
    return instance


def no_model_on_disk(*_args, **_kwargs):
    raise OSError("model is not cached locally")


# ---- a cancel is not a failure (#114) ---------------------------------------


def test_a_cancelled_speech_download_is_not_a_failure(app, monkeypatch):
    """No amber, no Critical toast, no "the speech model failed to load". The
    user asked for this. Routing a cancel through the failure path would put a
    fault on the tray for doing what you were told."""
    def cancel_partway(_model, download):
        download.cancel()
        download.raise_if_cancelled()

    monkeypatch.setattr(app_mod, "make_engine", no_model_on_disk)
    monkeypatch.setattr(stt, "prefetch", cancel_partway)

    assert app._load_stt() == app_mod.CANCELLED
    assert app.bridge.stt_failed.emitted == []
    assert app._stt is None


def test_a_load_that_really_failed_still_says_so(app, monkeypatch):
    """The other half of the same branch: nothing about #114 makes a broken
    model quieter."""
    monkeypatch.setattr(app_mod, "make_engine", no_model_on_disk)
    monkeypatch.setattr(stt, "prefetch", lambda *_a: True)

    assert app._load_stt() == app_mod.FAILED
    assert app.bridge.stt_failed.emitted != []


def test_a_prefetch_that_cannot_run_leaves_the_download_to_the_engine(app, monkeypatch):
    """An unknown repo, or a metadata call that fails where the download would
    have worked. Losing the progress bar is a far smaller failure than refusing
    to set the machine up, so the engine's own silent download still runs."""
    attempts = []

    def loads_once_the_weights_arrive(*_args, **kwargs):
        attempts.append(kwargs.get("local_files_only", False))
        if kwargs.get("local_files_only"):
            raise OSError("model is not cached locally")
        return types.SimpleNamespace(device="cpu")

    def unavailable(*_args):
        raise RuntimeError("could not reach the hub")

    monkeypatch.setattr(app_mod, "make_engine", loads_once_the_weights_arrive)
    monkeypatch.setattr(stt, "prefetch", unavailable)

    assert app._load_stt() == app_mod.LOADED
    assert app._stt is not None
    assert attempts == [True, False]


# ---- the wizard's Cancel reaches the right download -------------------------


def test_the_download_stays_cancellable_right_through_the_load(app, monkeypatch):
    """The engine's own loader picks up anything the prefetch missed, and it
    hardcodes its progress bar off, so that stretch cannot be watched or
    stopped. It still has to be *registered*: an empty register is what would
    let the wizard's Cancel report a cancellation nobody performed, which is
    the whole of #114."""
    registered = []

    def note_the_register(*_args, **kwargs):
        registered.append(app_mod.SPEECH_MODEL in app._downloads)
        if kwargs.get("local_files_only"):
            raise OSError("model is not cached locally")
        return types.SimpleNamespace(device="cpu")

    monkeypatch.setattr(app_mod, "make_engine", note_the_register)
    monkeypatch.setattr(stt, "prefetch", lambda *_a: True)

    app._load_stt()

    # [the local-only probe before any download, the load after the fetch]
    assert registered == [False, True]


def test_a_cancel_during_the_load_is_not_reported_as_a_cancellation(app, monkeypatch):
    """The bytes were already spent and the model is on disk. Saying "loaded"
    is the truth; saying "cancelled" while handing over a working model is the
    contradiction #114 objects to."""
    def load_after_a_cancel(*_args, **kwargs):
        if kwargs.get("local_files_only"):
            raise OSError("model is not cached locally")
        app._cancel_wizard_download()      # lands while the engine is loading
        return types.SimpleNamespace(device="cpu")

    app.wizard = FakeWizard()
    monkeypatch.setattr(app_mod, "make_engine", load_after_a_cancel)
    monkeypatch.setattr(stt, "prefetch", lambda *_a: True)

    assert app._load_stt() == app_mod.LOADED
    assert app.wizard.cancelled == 0


def test_the_wizards_cancel_stops_the_speech_download(app):
    app.wizard = FakeWizard()
    with app._watching(app_mod.SPEECH_MODEL) as download:
        app._cancel_wizard_download()
        assert download.cancelled is True
    # It asks the fetch to stop; it does not tell the page it already has.
    assert app.wizard.cancelled == 0


def test_the_wizards_cancel_leaves_a_cleanup_download_alone(app):
    """The wizard has one bar, for the one download it started. Cancelling
    whatever happens to be running is a different bug from the one #114 fixed.
    """
    app.wizard = FakeWizard()
    with app._watching(app_mod.CLEANUP_MODEL) as cleanup:
        app._cancel_wizard_download()
        assert cleanup.cancelled is False


def test_a_cancel_with_nothing_left_to_stop_still_answers_the_wizard(app):
    """The fetch can finish between the click and the slot. Without this the
    page sits on "Cancelling…" waiting for a confirmation that never comes."""
    app.wizard = FakeWizard()

    app._cancel_wizard_download()

    assert app.wizard.cancelled == 1


# ---- what the surfaces are told (#115) --------------------------------------


def test_a_watched_download_reports_to_the_bridge_and_then_stops(app):
    with app._watching(app_mod.SPEECH_MODEL) as download:
        download.expect(100)
        download.credit(50)

    assert app.bridge.download_progress.emitted == \
        [(app_mod.SPEECH_MODEL, Progress(50, 100))]
    assert app.bridge.download_finished.emitted == [(app_mod.SPEECH_MODEL,)]
    assert app._downloads == {}


def test_a_watched_download_lets_go_even_when_it_throws(app):
    with pytest.raises(RuntimeError), app._watching(app_mod.SPEECH_MODEL):
        raise RuntimeError("the hub went away")

    assert app._downloads == {}
    assert app.bridge.download_finished.emitted == [(app_mod.SPEECH_MODEL,)]


def test_the_tray_names_the_download_and_then_goes_quiet(app):
    app._on_download_progress(app_mod.SPEECH_MODEL, Progress(1, 4))
    app._on_download_finished(app_mod.SPEECH_MODEL)

    assert app.tray.shown == \
        [(app_mod.SPEECH_MODEL, Progress(1, 4)), (None, None)]


def test_only_the_speech_download_paints_the_wizards_bar(app):
    app.wizard = FakeWizard()

    app._on_download_progress(app_mod.CLEANUP_MODEL, Progress(1, 4))
    app._on_download_progress(app_mod.SPEECH_MODEL, Progress(2, 4))

    assert app.wizard.readings == [Progress(2, 4)]


def test_a_cancelled_load_tells_the_wizard_so_rather_than_that_it_failed(app):
    app.wizard = FakeWizard()

    app._on_wizard_download_done(app_mod.CANCELLED, "")

    assert app.wizard.cancelled == 1
    assert app.wizard.finished == []
