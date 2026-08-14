"""Application orchestrator: tray icon + dictation pipeline wiring.

M1 raw mode: hotkey -> record -> STT -> inject, transcript-safe via history.
M2 cleanup: the toggle and LLM residency live here (tray item + secondary tap
chord; load-on-toggle, unload-on-off/pause); the pipeline runs the cleanup
stage under the charter safety contract, with vocabulary biasing and
post-correction on every dictation.
"""

from __future__ import annotations

import logging
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import a11y, downloads, gpu_pack, hardware, icons, snippets, stt, vocabulary
from . import config as cfg
from . import platform as platform_pkg
from . import settings as settings_logic
from .audio import LevelMonitor, Recorder
from .chord import parse_combo
from .cleanup import Cleaner, CleanerLifecycle, hf_repo_for
from .config_store import ConfigStore
from .history import History
from .history_ui import HistoryWindow
from .hotkey import PushToTalk
from .inject import Injector, learn_override
from .overlay import Overlay
from .pipeline import DictationReport, Pipeline
from .settings_ui import SettingsWindow
from .stt import make_engine
from .theme.manager import ThemeManager
from .tray import Tray

log = logging.getLogger(__name__)

# What a download is called wherever it is reported — the tray header, the
# tooltip, and the decision about which surface a given fetch belongs to.
SPEECH_MODEL, CLEANUP_MODEL = "speech model", "cleanup model"

# How a model load ended. Cancelled is deliberately not a failure: nothing is
# faulted, nothing is toasted, and the wizard says so in its own words (#114).
# Not `READY`, which next door in `tray.py` means a green icon — a different
# claim about a different thing.
LOADED, FAILED, CANCELLED = "loaded", "failed", "cancelled"


class _Bridge(QObject):
    """Marshals events from the hotkey/worker threads onto the Qt main thread."""
    started = Signal()
    processing = Signal()
    discarded = Signal()
    reported = Signal(object)   # DictationReport
    stt_failed = Signal(str)
    stt_loaded = Signal()       # the engine came up; clears the amber fault
    cleaning = Signal()         # STT->cleanup boundary (§4.3)
    paused_press = Signal()     # hotkey pressed while paused (§4.3)
    max_utterance = Signal()    # recording cut off at the cap (§4.3)
    cleanup_hotkey = Signal()   # secondary tap chord fired (hotkey worker thread)
    llm_ready = Signal()
    llm_failed = Signal(str)
    gpu_offer = Signal()        # STT landed on CPU with an NVIDIA GPU idle (#55)
    gpu_installed = Signal(str) # support pack install finished; arg = error or ""
    wizard_download_done = Signal(str, str)    # outcome, detail (#114)
    download_progress = Signal(str, object)    # what, Progress (#115)
    download_finished = Signal(str)            # what — however it ended


class CadentApp:
    def __init__(self, platform: platform_pkg.Platform | None = None) -> None:
        # The one DI point (ADR 0005): everything below that needs an OS
        # primitive or fact is handed this object or a piece of it.
        self.platform = platform or platform_pkg.current()
        self.store = ConfigStore()
        self.config = self.store.config
        self.history = History(cfg.DB_PATH)
        self.history.prune(self.config.history_retention_days)
        self.recorder = Recorder(self.config.sample_rate, self.config.input_device,
                                 self.config.max_utterance_seconds)
        # One monitor shared by the wizard's microphone page and Settings ▸
        # General, so the two can never both hold the device. Each surface
        # starts and stops it; dictation suspends it (#105).
        self.mic_monitor = LevelMonitor(self.config.sample_rate)
        self.injector = Injector(self.config.app_overrides, self.config.injection_method,
                                 platform=self.platform)

        self._stt = None
        self._stt_lock = threading.Lock()
        self._recording = False
        self._max_timer: threading.Timer | None = None
        snippets.ensure_example(cfg.SNIPPETS_PATH)
        vocabulary.ensure_example(cfg.VOCAB_PATH)
        self.cleaner = Cleaner(self.config.llm_model_path, self.config.llm_max_tokens,
                               self.config.llm_runtime)
        self.pipeline = Pipeline(self._get_stt, self.injector, self.history,
                                 self.config.sample_rate, self.config.history_enabled,
                                 snippets_path=cfg.SNIPPETS_PATH,
                                 cleaner=self.cleaner,
                                 cleanup_supplier=lambda: self.config.cleanup_mode,
                                 vocab_path=cfg.VOCAB_PATH,
                                 vocab_threshold=self.config.vocab_fuzzy_threshold,
                                 vocab_threshold_short=self.config.vocab_fuzzy_threshold_short,
                                 on_cleanup_start=lambda: self.bridge.cleaning.emit(),
                                 mic_zero_hint=self.platform.capabilities
                                 .mic_permission_hint)

        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        # One mark for every window, the taskbar and Alt-Tab (#73).
        self.qt.setWindowIcon(icons.app_icon())
        # The design language, before any window exists. Windows' Text size
        # setting is read once here and applied on this launch; no Qt
        # mechanism honours it and there is no watcher (M4 8.5).
        self.theme = ThemeManager(self.qt, self.config.theme,
                                  text_scale=self.platform.desktop.text_scale_factor())
        self.theme.changed.connect(self._on_theme_changed)
        # Focus-visible: ring on Tab, nothing on click. Held on self because
        # an event filter that gets collected silently stops filtering.
        self.focus_filter = a11y.install(self.qt)
        self.overlay = Overlay(self.config, self.theme.tokens, platform=self.platform)
        self.overlay.level_source = lambda: self.recorder.level
        # Realized at startup rather than on the first show: otherwise the
        # first pill of every session pays window-creation and first-paint
        # cost no later one does (§4.6).
        self.overlay.realize()
        self.history_window: HistoryWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self.wizard = None
        # Where a clicked toast should land. Set right before each toast that
        # leads somewhere; cleared by the ones that deliberately do not.
        self._toast_target: tuple[str, str] | None = None
        # Terms the last dictation's hotwords budget dropped, handed to the
        # Vocabulary pane so it can say so only when it actually bit (5.4).
        self._dropped_hotwords: list[str] = []
        # The fetches in flight, by name. Keyed rather than singular because
        # the wizard's Cancel may only stop the download the wizard is actually
        # watching, never whatever happens to be running (#114).
        self._downloads: dict[str, downloads.Download] = {}

        self.bridge = _Bridge()
        self.bridge.started.connect(self.overlay.show_recording)
        self.bridge.processing.connect(self.overlay.show_transcribing)
        # A mid-hold cancel used to make the pill simply vanish, which is
        # indistinguishable from a crash.
        self.bridge.discarded.connect(self.overlay.show_cancelled)
        self.bridge.cleaning.connect(self.overlay.show_cleaning)
        self.bridge.paused_press.connect(self._on_paused_press)
        self.bridge.max_utterance.connect(self._on_max_utterance_toast)
        self.bridge.reported.connect(self._handle_report)
        self.bridge.stt_failed.connect(self._handle_stt_failure)
        self.bridge.stt_loaded.connect(self._handle_stt_loaded)
        self.bridge.cleanup_hotkey.connect(self._on_cleanup_hotkey)
        self.bridge.llm_ready.connect(self._handle_llm_ready)
        self.bridge.llm_failed.connect(self._handle_llm_failure)
        self.bridge.gpu_offer.connect(self._on_gpu_offer)
        self.bridge.gpu_installed.connect(self._on_gpu_installed)
        self.bridge.wizard_download_done.connect(self._on_wizard_download_done)
        self.bridge.download_progress.connect(self._on_download_progress)
        self.bridge.download_finished.connect(self._on_download_finished)
        # The try-it step is the one place the pill's state also needs a
        # non-visual channel (§8.4). Connected once, here: re-connecting on
        # every wizard open would stack duplicate slots.
        self.bridge.started.connect(lambda: self._tell_wizard("Recording"))
        self.bridge.processing.connect(lambda: self._tell_wizard("Transcribing"))

        self.llm_lifecycle = CleanerLifecycle(self.cleaner,
                                              prepare=self._ensure_llm_model,
                                              on_ready=self.bridge.llm_ready.emit,
                                              on_error=self.bridge.llm_failed.emit)

        self._setup_tray()
        self.overlay.set_cleanup(self.config.cleanup_mode)
        self.ptt = self._make_ptt()

        if self._needs_setup():
            # Dictation is disabled until setup finishes, and the tray carries
            # the way back: a bold "Finish setup…" plus amber (§6.4).
            self.tray.set_fault("setup-unfinished")
            self._run_wizard()
            return

        # Preload STT in the background so the first dictation isn't slow;
        # shows the explicit download disclosure on a model-less first run.
        threading.Thread(target=self._load_stt, daemon=True).start()
        # Warm the hardware probe off the UI thread. Settings' model picker
        # chips read it synchronously, and `cuInit` can block — the wizard
        # already pays this cost up front, and everyone who never sees the
        # wizard should too (#111, #112).
        threading.Thread(target=hardware.detect_safely, daemon=True).start()
        # Same for the cleanup LLM when the app starts with cleanup on.
        if self.config.cleanup_mode and not self.config.paused:
            self.llm_lifecycle.set_wanted(True)

    # ---- STT lifecycle ---------------------------------------------------

    def _get_stt(self):
        return self._stt

    def _load_stt(self) -> str:
        """Bring the speech engine up, downloading the weights if they are not
        there yet. Returns how it ended — the wizard needs to tell a cancel
        apart from a failure, and only this call knows which it was."""
        with self._stt_lock:
            if self._stt is not None:
                return LOADED
            try:
                self._stt = make_engine(self.config.stt_engine, self.config.stt_model,
                                        self.config.stt_device, local_files_only=True)
            except Exception:
                # model not on disk yet → explicit, disclosed download
                outcome = self._download_and_load()
                if outcome != LOADED:
                    return outcome
        if gpu_pack.should_offer(self.config.stt_device,
                                 getattr(self._stt, "device", "cpu"),
                                 stt_engine=self.config.stt_engine,
                                 driver_present=hardware.detect_safely().nvidia_driver):
            self.bridge.gpu_offer.emit()
        return LOADED

    def _download_and_load(self) -> str:
        """The disclosed download, pulled in front of the load rather than left
        buried inside it — which is what lets the tray report it and the wizard
        stop it (#114, #115).

        A prefetch that cannot run is not an error. `make_engine` still
        downloads whatever is missing, silently, exactly as it did before this
        existed; losing the progress bar is a smaller failure than refusing to
        set the machine up.

        **The handle stays registered across that load, not just the fetch.**
        Only the fetch can actually be stopped — the engine's own loader is the
        one that hardcodes its progress bar off. But a Cancel arriving in the
        load window must still find a live download to talk to: an empty
        register is what would let the wizard report a cancellation nobody
        performed, which is the exact defect #114 is about.
        """
        self.tray.message(
            "Cadent — one-time model download",
            f"Downloading speech model '{self.config.stt_model}' from Hugging Face. "
            "This is the only network activity Cadent performs. "
            "Dictation is disabled until it finishes.",
            QSystemTrayIcon.MessageIcon.Information, 10_000)
        with self._watching(SPEECH_MODEL) as download:
            try:
                stt.prefetch(self.config.stt_model, download)
            except downloads.Cancelled:
                log.info("speech model download stopped by the user")
                return CANCELLED
            except Exception:
                log.warning("could not prefetch %r; the engine will download it "
                            "without progress", self.config.stt_model, exc_info=True)
            try:
                self._stt = make_engine(self.config.stt_engine, self.config.stt_model,
                                        self.config.stt_device)
            except Exception as exc:
                self.bridge.stt_failed.emit(str(exc))
                return FAILED
        self.bridge.stt_loaded.emit()
        self.tray.message("Cadent", "Speech model ready — dictate away.",
                          QSystemTrayIcon.MessageIcon.Information, 4_000)
        return LOADED

    @contextmanager
    def _watching(self, what: str):
        """Run a download where every surface can see it.

        Readings cross to the UI thread through the bridge, because `fetch`
        reports from whichever thread is doing the downloading. Keyed by name
        rather than held as one handle: a speech model and a cleanup model can
        be fetched at once — two threads, started independently — and a cancel
        has to reach the right one of them.
        """
        download = downloads.Download(
            lambda reading: self.bridge.download_progress.emit(what, reading))
        self._downloads[what] = download
        try:
            yield download
        finally:
            self._downloads.pop(what, None)
            self.bridge.download_finished.emit(what)

    def _on_download_progress(self, what: str, reading: downloads.Progress) -> None:
        self.tray.set_download(what, reading)
        # The wizard has one bar, for the one download it started. A cleanup
        # model arriving in the background must not paint over it.
        if what == SPEECH_MODEL and self.wizard is not None \
                and self.wizard.isVisible():
            self.wizard.report_progress(reading)

    def _on_download_finished(self, _what: str) -> None:
        """The tray carries one line, so it simply goes quiet. Anything still
        running repaints it within a chunk — self-correcting, and cheaper than
        a second place that has to agree about which download is showing."""
        self.tray.set_download(None)

    def _rebuild_stt_on_cpu(self) -> None:
        """#38 recovery: a transcribe-time crash leaves the engine wedged (a
        broken CUDA runtime does). The caller already detached the wedged
        engine; rebuild on CPU — the model is on disk, it just transcribed.
        Dictations meanwhile report not-ready."""
        with self._stt_lock:
            self._stt = None
            try:
                self._stt = make_engine(self.config.stt_engine, self.config.stt_model,
                                        "cpu", local_files_only=True)
            except Exception as exc:
                self.bridge.stt_failed.emit(str(exc))
                return
        # Same thread-precedent as _load_stt's download toasts.
        self.tray.message("Cadent", "Speech engine restarted on CPU — dictate away.",
                              QSystemTrayIcon.MessageIcon.Information, 4_000)

    # ---- GPU support pack (#55) ------------------------------------------

    def _on_gpu_offer(self) -> None:
        """Disclose, never silently download, never a blocking prompt: the
        toast points at a tray menu item, so a missed toast still leaves the
        offer reachable. The offer also drives amber — but only until the menu
        has been opened, so it can never become permanent scenery (§3.4)."""
        self.tray.offer_gpu_pack()
        self.tray.message(
            "Cadent — GPU detected",
            "Dictation is running on CPU, but this PC has an NVIDIA GPU. "
            f"Pick 'Download GPU support pack' ({gpu_pack.DOWNLOAD_SIZE}, one-time) "
            "from the tray menu to speed up transcription.",
            QSystemTrayIcon.MessageIcon.Information, 10_000)

    def _on_gpu_download_clicked(self) -> None:
        self.tray.gpu_action.setEnabled(False)
        self.tray.message(
            "Cadent — one-time GPU support pack download",
            f"Downloading NVIDIA cuBLAS ({gpu_pack.DOWNLOAD_SIZE}) from PyPI. "
            "This is the only network activity Cadent performs. "
            "Dictation keeps working on CPU until it finishes.",
            QSystemTrayIcon.MessageIcon.Information, 10_000)
        threading.Thread(target=self._install_gpu_pack, daemon=True).start()

    def _install_gpu_pack(self) -> None:
        try:
            gpu_pack.install_pack()
        except Exception as exc:
            log.exception("GPU support pack install failed")
            self.bridge.gpu_installed.emit(str(exc))
            return
        self.bridge.gpu_installed.emit("")

    def _on_gpu_installed(self, detail: str) -> None:
        if detail:
            self.tray.gpu_action.setEnabled(True)
            self.tray.message(
                "Cadent — GPU support pack failed",
                f"Download failed: {detail[:160]} "
                "Pick it from the tray menu to retry.",
                QSystemTrayIcon.MessageIcon.Warning, 8_000)
            return
        self.tray.withdraw_gpu_offer()
        self.tray.message(
            "Cadent", "GPU support pack ready — restarting the speech engine on GPU.",
            QSystemTrayIcon.MessageIcon.Information, 4_000)
        # install_pack prepended the pack dir to PATH, so the reload's CUDA
        # probe can find cuBLAS now. Detach-then-reload, #38 pattern.
        self._stt = None
        threading.Thread(target=self._load_stt, daemon=True).start()

    def _make_ptt(self) -> PushToTalk:
        return PushToTalk(self.config.hotkey, self.config.hotkey_mode,
                          self._on_start, self._on_stop, self._on_discard,
                          self.config.min_hold_ms / 1000,
                          cleanup_combo=self._valid_cleanup_combo(),
                          on_cleanup_toggle=self.bridge.cleanup_hotkey.emit,
                          platform=self.platform)

    # ---- cleanup / LLM lifecycle -----------------------------------------

    def _valid_cleanup_combo(self) -> str | None:
        """The tap chord, or None if config.json names an unparseable one.

        The config key stays `cleanup_hotkey`: renaming it would rewrite a file
        the user may have hand-edited, to no benefit anyone can see (#113).
        """
        try:
            parse_combo(self.config.cleanup_hotkey)
            return self.config.cleanup_hotkey
        except ValueError:
            log.warning("invalid cleanup_hotkey %r; toggle via tray only",
                        self.config.cleanup_hotkey)
            return None

    def _ensure_llm_model(self) -> None:
        """Download the cleanup model on first use — disclosed, M1 pattern.

        Runs from the lifecycle's `prepare` hook, which under instant apply
        fires the moment a model is picked in Settings with cleanup on — so
        this *is* selection time for the case that matters, and the raise
        below reaches the user as a named toast rather than as a bare
        FileNotFoundError from `Cleaner.load` several steps later (#112).
        """
        path = Path(self.config.llm_model_path)
        if path.exists():
            return
        repo = hf_repo_for(str(path))
        if repo is None:
            # Say it here, where the filename is still in hand. Returning
            # quietly left `Cleaner.load` to raise a bare "cleanup model not
            # found" several steps later, which reads as a bug rather than as
            # a setting pointing at nothing (#112).
            raise FileNotFoundError(
                f"'{path.name}' isn't a model Cadent can download, and "
                f"there's no file at {path}. Pick a cleanup model in "
                "Settings, or choose your own file.")
        self.tray.message(
            "Cadent — one-time model download",
            f"Downloading cleanup model '{path.name}' from Hugging Face. "
            "This is the only network activity Cadent performs. "
            "Dictation stays raw until it finishes.",
            QSystemTrayIcon.MessageIcon.Information, 10_000)
        # 808 MB to 2.5 GB, and #112 made switching between them a browsable
        # choice — so this path is walked far more often than it used to be,
        # and it does not get to be silent either (#115).
        with self._watching(CLEANUP_MODEL) as download:
            downloads.fetch(repo, [path.name], download, local_dir=path.parent)

    def _apply_cleanup(self, on: bool) -> None:
        """Single entry point for the toggle (tray click, hotkey, startup)."""
        self.store.set("cleanup_mode", on)
        self.overlay.set_cleanup(on)
        self.tray.set_cleanup(on)
        self.llm_lifecycle.set_wanted(on and not self.config.paused)

    def _on_cleanup_hotkey(self) -> None:
        on = not self.config.cleanup_mode
        self._apply_cleanup(on)
        # The tray checkbox isn't visible from the hotkey; confirm the flip.
        self.tray.message("Cadent",
                              "Cleanup on — transcripts are polished." if on
                              else "Cleanup off — raw transcripts.",
                              QSystemTrayIcon.MessageIcon.Information, 3_000)

    def _handle_llm_ready(self) -> None:
        self.tray.set_fault("cleanup-failed", False)
        self.tray.message("Cadent", "Cleanup ready — it's running now.",
                              QSystemTrayIcon.MessageIcon.Information, 3_000)

    def _handle_llm_failure(self, detail: str) -> None:
        self.tray.set_fault("cleanup-failed")
        # The mode choice stays persisted (retry on next toggle or restart);
        # only the residency attempt failed. Dictation continues raw meanwhile.
        self.tray.message("Cadent — cleanup unavailable",
                              f"Cleanup model failed to load: {detail[:160]} "
                              "Dictation continues raw; toggle cleanup to retry.",
                              QSystemTrayIcon.MessageIcon.Warning, 8_000)

    # ---- pipeline --------------------------------------------------------

    def _on_start(self) -> None:
        if self.tray.ledger.setup_unfinished and not self._wizard_wants_hotkey():
            # Toast, never a prompt — pointing at the way back (§6.4).
            self.tray.message(
                "Cadent — setup isn't finished",
                "Pick 'Finish setup…' from the tray menu to choose a speech "
                "model. Dictation is off until then.",
                QSystemTrayIcon.MessageIcon.Information, 6_000)
            return
        if self.config.paused:
            # The hook stays armed while paused precisely so this is not
            # inert — a dead hotkey reads as broken (§4.3).
            self.bridge.paused_press.emit()
            return
        log.debug("dictation start")
        # Before the recorder opens the device, not in response to `started`:
        # this runs on the hotkey worker thread and the signal would arrive
        # after the contention it exists to prevent (#105).
        self.mic_monitor.suspend()
        try:
            self.recorder.start()
        except Exception:
            log.exception("recorder failed to start")
            self.mic_monitor.resume()      # never took the device after all
            self.bridge.reported.emit(DictationReport(
                outcome="failed", detail="microphone unavailable"))
            return
        self._recording = True
        self._max_timer = threading.Timer(self.config.max_utterance_seconds,
                                          self._on_max_utterance)
        self._max_timer.daemon = True
        self._max_timer.start()
        self.bridge.started.emit()

    def _on_stop(self) -> None:
        if not self._recording:
            # Paused, or the max-utterance cutoff already stopped us — the key
            # release must not process a second, empty dictation.
            return
        self._end_recording()
        audio = self.recorder.stop()
        self.mic_monitor.resume()   # back to whatever meter was using it
        log.debug("dictation stop: %.1fs of audio", len(audio) / self.config.sample_rate)
        self.bridge.processing.emit()
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _on_discard(self) -> None:
        if not self._recording:
            return
        log.debug("dictation discarded")
        self._end_recording()
        self.recorder.stop()
        self.mic_monitor.resume()
        self.bridge.discarded.emit()

    def _end_recording(self) -> None:
        self._recording = False
        if self._max_timer is not None:
            self._max_timer.cancel()
            self._max_timer = None

    def _on_max_utterance(self) -> None:
        """Graceful cutoff: a stuck hotkey can't record forever — transcribe
        what we have and release the microphone."""
        if self._recording:
            log.debug("max utterance length reached; cutting off")
            self.bridge.max_utterance.emit()
            self._on_stop()

    def _process(self, audio) -> None:
        try:
            report = self.pipeline.process(audio)
        except Exception as exc:
            log.exception("pipeline crashed")
            report = DictationReport(outcome="failed", detail=str(exc))
        log.debug("dictation outcome=%s detail=%s", report.outcome, report.detail)
        self.bridge.reported.emit(report)

    def _handle_report(self, report: DictationReport) -> None:
        self._dropped_hotwords = report.dropped_hotwords
        if self.settings_window is not None:
            self.settings_window.ctx.dropped_hotwords = self._dropped_hotwords
            self.settings_window.vocabulary.refresh_budget_note()
        for notice in report.notices:   # e.g. malformed snippets.json
            # Still a toast, still never a prompt; it just leads somewhere now.
            self._toast_target = ("vocabulary", "")
            self.tray.message("Cadent", notice,
                                  QSystemTrayIcon.MessageIcon.Warning, 6_000)
        if (report.outcome == "fallback" and report.typing_failed
                and self.platform.capabilities.auto_learn_overrides):
            # Auto-learn (#45): typing detectably failed here and the paste
            # just worked, so remember clipboard for this app. The injector
            # shares the config's override list, so the entry is live at once;
            # inform with a one-time toast, never a prompt (#44). Once learned,
            # the app routes to clipboard and this path can't fire again.
            learned = learn_override(report.app_name, self.config.app_overrides)
            if learned is not None:
                # learn_override mutated the list the injector holds; write
                # that one key rather than the whole file (§7.7).
                self.store.set("app_overrides")
                self._toast_target = ("overrides", learned.process)
                self.tray.message(
                    "Cadent — learned",
                    f"Pasting into {learned.process} from now on "
                    "(typing failed there).",
                    QSystemTrayIcon.MessageIcon.Information, 6_000)
        if report.outcome in ("empty", "inserted", "fallback"):
            self.overlay.hide_overlay()   # success is silent
            words = len(report.text.split())
            self._tell_wizard(f"Inserted {words} word{'' if words == 1 else 's'}"
                              if words else "Nothing heard — try again")
            return
        if report.outcome == "not-ready":
            # The one outcome that had no detail channel at all (§4.2).
            self.overlay.show_failure("Model loading")
            self.tray.message(
                "Cadent — not ready yet",
                "The speech model is still loading — try again in a moment.",
                QSystemTrayIcon.MessageIcon.Information, 5_000)
            return
        if report.outcome == "notify-only":
            # A wordless success-hide teaches the user their words vanished
            # for no reason (§4.2).
            self.overlay.show_failure("Not inserted")
            # Deliberately inert: notify-only fires for a configured "Don't
            # insert" row *and* for an elevated foreground window, and the
            # elevated case is unfixable by any override — UIPI blocks the
            # paste chord too — so linking it would imply a fix exists.
            self._toast_target = None
            self.tray.message(
                "Cadent — nothing inserted",
                (report.detail or "This app can't receive synthetic input.")
                + " The transcript is in History.",
                QSystemTrayIcon.MessageIcon.Warning, 6_000)
            return
        # failed
        if report.stt_crashed:
            # Engine-fatal (#38): the engine may be wedged — rebuild it on CPU.
            # Detach it right here, before the rebuild thread even starts, so
            # a dictation racing the rebuild reports not-ready instead of
            # reaching the wedged engine.
            self._stt = None
            self.tray.set_fault("engine-on-cpu")
            self.overlay.show_failure("Dictation failed")
            self.tray.message(
                "Cadent — speech engine crashed",
                "Nothing was transcribed. Restarting the speech engine on CPU…",
                QSystemTrayIcon.MessageIcon.Warning, 8_000)
            threading.Thread(target=self._rebuild_stt_on_cpu, daemon=True).start()
            return
        if not report.text:
            # Failed before a transcript existed (mic unavailable, pipeline
            # crash) — don't claim one is in History (#38).
            self.overlay.show_failure("Dictation failed")
            self.tray.message("Cadent — dictation failed",
                                  (report.detail or "unexpected error")[:200],
                                  QSystemTrayIcon.MessageIcon.Warning, 6_000)
            return
        self.overlay.show_failure("Insertion failed")
        where = "on your clipboard and in History" if report.on_clipboard else "in History"
        self.tray.message("Cadent — insertion failed",
                              f"The transcript is {where}.",
                              QSystemTrayIcon.MessageIcon.Warning, 6_000)

    def _on_paused_press(self) -> None:
        self.overlay.show_paused()

    def _on_max_utterance_toast(self) -> None:
        """Not a pill state: the pill's job at that moment is to show that
        work is happening. The explanation goes to the toast (§4.3)."""
        self.tray.message(
            "Cadent", f"Stopped at {self.config.max_utterance_seconds} s — "
            "transcribing what was captured.",
            QSystemTrayIcon.MessageIcon.Information, 5_000)

    def _needs_setup(self) -> bool:
        """Existing users never see the wizard.

        A model already on disk is the fact that says so — more reliable than
        the flag, which a fresh config.json on an old install would not carry
        (M4 6.4).
        """
        if self.config.setup_complete:
            return False
        return not hardware.any_speech_model_downloaded(cfg.MODELS_DIR)

    def _on_toast_clicked(self) -> None:
        """A toast that leads somewhere. Right after "typing failed in this
        app" is the only moment anyone cares about per-app injection, and the
        malformed-vocabulary notice should land on the pane where the error
        state and [Open file] are already waiting (M4 5.4, 5.5)."""
        target, self._toast_target = self._toast_target, None
        if target is None:
            return
        pane, process = target
        self._show_settings(pane)
        if pane == "overrides" and process and self.settings_window is not None:
            self.settings_window.overrides.select(process)

    def _handle_stt_failure(self, detail: str) -> None:
        self.tray.set_fault("model-failed")
        self.tray.message("Cadent — speech model failed to load",
                              detail[:200], QSystemTrayIcon.MessageIcon.Critical, 10_000)

    def _handle_stt_loaded(self) -> None:
        self.tray.set_fault("model-failed", False)
        self.tray.set_fault("engine-on-cpu", False)

    # ---- shell -----------------------------------------------------------

    def _setup_tray(self) -> None:
        self.tray = Tray(on_toggle_cleanup=self._apply_cleanup,
                         on_toggle_pause=self._toggle_pause,
                         on_settings=self._show_settings,
                         on_history=self._show_history,
                         on_wizard=self._run_wizard,
                         on_gpu_download=self._on_gpu_download_clicked,
                         on_quit=self._quit,
                         on_message_clicked=self._on_toast_clicked)
        self.tray.set_cleanup(self.config.cleanup_mode)
        self.tray.set_paused(self.config.paused)
        # Startup on a broken config was silent: the app ran on nothing the
        # user chose and never said so (§7.2).
        self.tray.set_fault("config-unreadable", not self.store.readable)
        self.tray.show()

    def _toggle_pause(self, checked: bool) -> None:
        self.store.set("paused", checked)
        if checked:
            # The mic is released, but the hook stays armed so a hotkey press
            # can answer with the Paused pill instead of nothing (§4.3).
            self.recorder.stop()
            self._end_recording()
            self.overlay.hide_overlay()
        # Charter: the LLM never sits resident while paused.
        self.llm_lifecycle.set_wanted(self.config.cleanup_mode and not checked)
        self.tray.set_paused(checked)

    def _run_wizard(self) -> None:
        """Settings ▸ General, and the tray's "Finish setup…" when setup was
        cancelled. Always available, so a cancelled setup is never a dead end
        (§6.4)."""
        from .wizard import SetupWizard

        if self.wizard is not None and self.wizard.isVisible():
            self.wizard.raise_()
            self.wizard.activateWindow()
            return
        # Its own capture, not the recorder's: the recorder only has a level
        # while it is capturing a dictation, and nobody is holding the hotkey
        # on the microphone page (#105). The wizard opens and closes it.
        self.wizard = SetupWizard(self.store, self.overlay,
                                  devices=self._input_devices(),
                                  tokens=self.theme.tokens,
                                  mic_monitor=self.mic_monitor,
                                  install_gpu_pack=self._on_gpu_download_clicked)
        self.wizard.finished_setup.connect(self._on_wizard_finished)
        self.wizard.download_requested.connect(self._download_model_for_wizard)
        self.wizard.cancel_requested.connect(self._cancel_wizard_download)
        self.wizard.show()

    def _wizard_wants_hotkey(self) -> bool:
        """The try-it step teaches the hotkey and the pill in one motion, so
        the hotkey has to actually fire there — even though setup is still
        unfinished everywhere else (§6.1)."""
        from .wizard import HOTKEY

        return (self.wizard is not None and self.wizard.isVisible()
                and self.wizard.page == HOTKEY)

    def _tell_wizard(self, state: str) -> None:
        if self.wizard is not None and self.wizard.isVisible():
            self.wizard.report_pill_state(state)

    def _download_model_for_wizard(self, model: str) -> None:
        """The wizard consents; the app downloads. Same disclosed path as the
        normal first run, just initiated by a button instead of a startup.

        The wizard offers one flat list across both engines, so the engine
        comes from the model that was picked (#72).
        """
        self.store.set("stt_engine", settings_logic.engine_for_model(model))
        self.store.set("stt_model", model)
        self._stt = None
        threading.Thread(target=self._load_stt_for_wizard, daemon=True).start()

    def _cancel_wizard_download(self) -> None:
        """The wizard's Cancel, connected to the fetch it is actually watching.

        Before #114 this signal had no consumer at all. It now stops one
        specific download — not whatever happens to be running — and answers
        immediately when there is nothing left to stop, so the page can never
        sit on "Cancelling…" waiting for a confirmation that will not come.
        """
        download = self._downloads.get(SPEECH_MODEL)
        if download is not None:
            download.cancel()
            return
        if self.wizard is not None:
            self.wizard.mark_download_cancelled()

    def _load_stt_for_wizard(self) -> None:
        outcome = self._load_stt()
        detail = "" if outcome == LOADED else "see the tray notification"
        # Marshalled through the bridge: _load_stt runs off the UI thread.
        self.bridge.wizard_download_done.emit(outcome, detail)

    def _on_wizard_download_done(self, outcome: str, detail: str) -> None:
        if self.wizard is None:
            return
        if outcome == CANCELLED:
            self.wizard.mark_download_cancelled()
        else:
            self.wizard.mark_download_finished(outcome == LOADED, detail)

    def _on_wizard_finished(self, completed: bool) -> None:
        self.tray.set_fault("setup-unfinished", not completed)
        if not completed:
            return
        self.store.set("setup_complete", True)
        if self._stt is None:
            threading.Thread(target=self._load_stt, daemon=True).start()
        if self.config.cleanup_mode and not self.config.paused:
            self.llm_lifecycle.set_wanted(True)

    def _input_devices(self) -> list[str]:
        try:
            return sorted({d["name"] for d in Recorder.list_devices()})
        except Exception:
            return []

    def _show_history(self) -> None:
        self.history_window = HistoryWindow(self.history)
        self.history_window.show()

    # ---- settings ----------------------------------------------------------

    def _show_settings(self, pane: str | None = None) -> None:
        if self.settings_window is None or not self.settings_window.isVisible():
            self.settings_window = SettingsWindow(
                self.store, tokens=self.theme.tokens,
                devices=self._input_devices(), history=self.history,
                high_contrast=self.theme.high_contrast,
                mic_monitor=self.mic_monitor)
            self.settings_window.applied.connect(self._on_setting_applied)
            self.settings_window.theme_requested.connect(self._on_theme_preference)
            self.settings_window.wizard_requested.connect(self._run_wizard)
            self.settings_window.move_overlay_requested.connect(self._move_overlay)
            self.settings_window.ctx.dropped_hotwords = self._dropped_hotwords
            self.settings_window.show()
        else:
            self.settings_window.raise_()
            self.settings_window.activateWindow()
        if pane is not None:
            self.settings_window.show_pane(pane)

    def _on_setting_applied(self, field: str) -> None:
        """Instant apply: the store already wrote the field, so all that is
        left is restarting exactly the engine it touched — in-process, never
        the whole app (M4 5.2)."""
        if field == "cleanup_mode":
            self._apply_cleanup(self.config.cleanup_mode)
        elif field == "history_retention_days":
            self.history.prune(self.config.history_retention_days)
        elif field == "autostart":
            self.platform.autostart.set_enabled(self.config.autostart)
        elif field == "show_overlay":
            self.overlay.set_show_activity(self.config.show_overlay)

        engine = settings_logic.engine_for(field)
        if engine == "mic":
            # The input stream opens per dictation, so retargeting the device
            # is the whole restart.
            self.recorder.device = self.config.input_device
        elif engine == "hotkeys":
            self.ptt.stop()
            self.ptt = self._make_ptt()
            self.ptt.start()
        elif engine == "stt":
            # Detach first so dictations report not-ready instead of using the
            # old model (same pattern as the #38 crash recovery).
            self._stt = None
            threading.Thread(target=self._load_stt, daemon=True).start()
        elif engine == "llm":
            self.cleaner.model_path = self.config.llm_model_path
            self.cleaner.max_tokens = self.config.llm_max_tokens
            self.cleaner.wanted_runtime = self.config.llm_runtime
            self.cleaner.unload()
            self.llm_lifecycle.set_wanted(self.config.cleanup_mode
                                          and not self.config.paused)
        self.tray.refresh()

    def _on_theme_preference(self, preference: str) -> None:
        self.theme.set_preference(preference)
        self._on_theme_changed()

    def _on_theme_changed(self) -> None:
        """A live theme change re-renders the sheet; the custom painters read
        the token dict directly and have to be told."""
        self.overlay.set_tokens(self.theme.tokens)
        if self.settings_window is not None:
            self.settings_window.general.set_high_contrast(
                self.theme.high_contrast)

    def _move_overlay(self) -> None:
        """Settings "Move overlay..." The real pill stays click-through at all
        times, so the drag happens on a mouse-enabled stand-in and the anchor
        commits on Done - one write per gesture (M4 4.6)."""
        from .overlay_move import MoveOverlayDialog

        MoveOverlayDialog(self.store, self.theme.tokens,
                          parent=self.settings_window).exec()

    def _quit(self) -> None:
        """Coalesced writes flush on focus-out, window close **and quit** — a
        spinbox left mid-repeat must not lose its value to a tray Quit."""
        self.store.flush()
        self.qt.quit()

    def run(self) -> int:
        self.ptt.start()
        try:
            return self.qt.exec()
        finally:
            self.store.flush()
            self.ptt.stop()
