"""TEXT ▸ Speech & cleanup: the two model choices (spec §5.1).

Since #111 each choice is **one** control. The speech half used to be three —
engine, model, runtime — chained so that changing the engine rewrote the other
two. But the engine is an implementation detail of the model: `distil-small.en`
could only ever be faster-whisper's, so picking a model picks its engine, and
asking the user to pick both is asking them to keep two lists in agreement.

What is left is a list of models that read as choices rather than filenames,
and a runtime that goes behind an Advanced disclosure — it is the setting that
matters least and the one whose wrong answer is self-correcting, because every
engine's `auto` ladder walks itself down to the CPU.

Picking a speech model here *downloads* one, which until now was a tray balloon
at the start and silence afterwards — for up to 3.1 GB, on the pane where the
choice was made. The same bar, caption and Cancel the wizard grew in #114/#115
live here too, driven by the same app-side signals, because a download you can
watch and stop in setup and cannot in Settings is one feature with two answers.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import a11y, hardware, models, platform, settings
from ..config import MODELS_DIR
from ..downloads import Progress
from .context import PaneContext
from .model_picker import add_model_row, fill_speech_models, model_combo
from .widgets import Notice, caps, card, label, page_title, row

_LLM_DIR = MODELS_DIR / "llm"

# The item that opens a file picker, and the only way to bring your own GGUF
# now that the combo is read-only (#112). Not a path, so it can never collide
# with one.
CHOOSE_A_FILE = "__choose__"
CHOOSE_A_FILE_TITLE = "Choose a file…"
CHOOSE_A_FILE_BLURB = "Use your own GGUF model"
OWN_FILE_BLURB = "Your own file"

ADVANCED_SHOW = "Show advanced"
ADVANCED_HIDE = "Hide advanced"

# The download controls, worded exactly as the wizard words them (§6.5): one
# button that is the verb it will perform, and a claim that waits for the app
# rather than announcing a cancellation it has only asked for.
DOWNLOAD = "Download model"
CANCEL_DOWNLOAD = "Cancel download"
CANCELLING = "Cancelling the download…"
CANCELLED = "Download cancelled."
READY = "Speech model ready."


class SpeechPane(QWidget):
    # The app owns the download; the pane only asks. Both signals travel out
    # through SettingsWindow, which is the only thing app.py holds.
    download_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, ctx: PaneContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        # The download, as this pane knows it. A fetch can start before this
        # window exists and outlive it, so none of this is authoritative — it
        # is the last thing the app said, which is what the pane can honestly
        # paint.
        self._downloading = False
        self._cancelling = False
        self._failed = False
        self._progress = Progress(0, 0)
        self._outcome = ""
        # Set while a model change repopulates the runtime combo, so its own
        # change signal doesn't write the value we are mid-way through setting.
        self._repopulating = False
        t = ctx.tokens
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_3"]))

        engine = settings.engine_for_model(ctx.config.stt_model)
        self.stt = model_combo(t)
        self._fill_speech_models()
        self.stt.currentIndexChanged.connect(self._commit_model)

        self.runtime = QComboBox()
        self._fill_runtimes(engine, ctx.config.stt_device)
        self.runtime.currentIndexChanged.connect(self._commit_runtime)

        # What layer-1 vocabulary biasing means for the chosen engine. Empty
        # for faster-whisper, which simply does it.
        self.biasing = label("", "RowHint")

        # A quiet line for anything config.json disagrees with us
        # about in this pane's own fields (§7.4, §7.5).
        self.notice = Notice(t, "", [])
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        layout.addWidget(page_title("Speech & cleanup"))
        layout.addWidget(caps("Speech recognition", t))
        layout.addWidget(card([
            row(t, "Speech model", self.stt,
                desc="Everything runs on this PC. Nothing is uploaded.",
                hint=settings.restart_hint("stt_model")),
        ]))

        # Directly under the row that starts it, so the answer to "what is it
        # doing?" is where the question was asked.
        self.download_button = QPushButton(DOWNLOAD)
        self.download_button.setObjectName("Accent")
        self.download_button.clicked.connect(self._on_download_clicked)
        layout.addWidget(self.download_button)
        # Determinate, for the reason the wizard's is: the numbers exist, and
        # the question during 3.1 GB is "how much longer".
        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 100)
        self.download_bar.setTextVisible(False)
        self.download_bar.setAccessibleName("Download progress")
        layout.addWidget(self.download_bar)
        self.download_status = label("", "RowHint")
        layout.addWidget(self.download_status)
        self._show_download_state()

        layout.addWidget(self.biasing)
        self._show_biasing_note(engine)

        self.cleanup = QCheckBox()
        self.cleanup.setChecked(ctx.config.cleanup_mode)
        self.cleanup.toggled.connect(lambda on: ctx.set("cleanup_mode", on))

        self.llm_runtime = QComboBox()
        for name in settings.cleanup_runtime_choices():
            self.llm_runtime.addItem(settings.runtime_label(name), name)
        self.llm_runtime.setCurrentIndex(
            max(self.llm_runtime.findData(ctx.config.llm_runtime), 0))
        self.llm_runtime.currentIndexChanged.connect(self._commit_llm_runtime)

        self.llm = model_combo(t)
        self._fill_cleanup_models()
        # `activated` rather than `currentIndexChanged`: only a *user* pick may
        # open a file dialog, and the list is rebuilt under itself when a
        # chosen file joins it.
        self.llm.activated.connect(self._commit_llm)

        layout.addSpacing(int(t["sp_2"]))
        layout.addWidget(caps("AI cleanup", t))
        layout.addWidget(card([
            # Not "Flow mode": the product name was doing duty as a feature
            # name, inside a card that already said what the feature was (#113).
            row(t, "Clean up transcripts before inserting them", self.cleanup,
                desc="A local LLM removes filler words and fixes punctuation"),
            row(t, "Cleanup model", self.llm,
                desc="Bigger models clean up better and take longer",
                hint=settings.restart_hint("llm_model_path")),
        ]))

        # Both runtimes, behind one door, at the foot of the page. Runtime is
        # the setting on each half whose wrong answer fixes itself — every
        # `auto` ladder probes and drops a rung — so it is the one that can be
        # a disclosure rather than a row (#111). Cleanup joined it in #116;
        # they sit together because the reason they are hidden is the same one.
        self.advanced = QPushButton(ADVANCED_SHOW)
        self.advanced.setObjectName("Link")
        self.advanced.setCheckable(True)
        self.advanced.toggled.connect(self._toggle_advanced)
        layout.addSpacing(int(t["sp_2"]))
        layout.addWidget(self.advanced)
        # The speech row only where the ladder has more than one real rung:
        # on darwin `auto` and `cpu` both mean the CPU, and one choice
        # offered twice is no choice (M5 §7). Cleanup keeps its combo
        # everywhere — auto and cpu genuinely differ there.
        rows = []
        if platform.current().capabilities.show_runtime_combo:
            rows.append(row(t, "Speech runtime", self.runtime,
                            desc="Automatic uses the graphics card and falls "
                                 "back to the processor if it can't.",
                            hint=settings.restart_hint("stt_device")))
        rows.append(row(t, "Cleanup runtime", self.llm_runtime,
                        desc="Cleanup is much faster on a graphics card.",
                        hint=settings.restart_hint("llm_runtime")))
        self.advanced_card = card(rows)
        self.advanced_card.setVisible(False)
        layout.addWidget(self.advanced_card)
        # The GPU-pack driver row (ADR 0010): where the running engine's pack
        # edition needs a newer NVIDIA driver than this machine has, the CUDA
        # rung is not on offer and the line says what to do about it. Reads
        # the cached probe (never blocks) and the platform's edition table.
        self.pack_hint = label("", "RowHint")
        self.pack_hint.setVisible(False)
        layout.addWidget(self.pack_hint)
        self.refresh_pack_hint()
        layout.addStretch()

    def refresh_pack_hint(self) -> None:
        from .. import gpu_pack

        detected = hardware.detect_safely()
        hint = None
        if detected.nvidia_driver:
            hint = gpu_pack.driver_hint(self.ctx.config.stt_engine,
                                        detected.cuda_driver_version)
        self.pack_hint.setText(hint or "")
        self.pack_hint.setVisible(bool(hint))

    # ---- the speech list ---------------------------------------------------

    def _fill_speech_models(self) -> None:
        """The shared list, opened on what is actually running."""
        self._repopulating = True
        try:
            configured = self.ctx.config.stt_model
            fill_speech_models(self.stt, configured=configured,
                               carve_out_engine=self.ctx.config.stt_engine)
            self.stt.setCurrentIndex(max(self.stt.findData(configured), 0))
        finally:
            self._repopulating = False

    def _fill_runtimes(self, engine: str, runtime: str) -> None:
        """Repopulate the runtime combo for `engine`.

        Still engine-scoped even though the engine is no longer picked
        directly: `directml` is meaningless to ctranslate2.
        """
        self._repopulating = True
        try:
            self.runtime.clear()
            for name in settings.runtime_choices(engine):
                self.runtime.addItem(settings.runtime_label(name), name)
            self.runtime.setCurrentIndex(max(self.runtime.findData(runtime), 0))
        finally:
            self._repopulating = False

    def _commit_model(self) -> None:
        """One pick, one restart notice — cross-engine or not.

        The engine and runtime are corollaries, so they go straight to the
        store: `stt_model` is the announcing write, and the single reload that
        follows it reads all three.
        """
        model = self.stt.currentData()
        if self._repopulating or not model or model == self.ctx.config.stt_model:
            return
        engine = settings.engine_for_model(model)
        if engine != self.ctx.config.stt_engine:
            runtime = (self.ctx.config.stt_device
                       if self.ctx.config.stt_device in settings.runtime_choices(engine)
                       else "auto")
            self.ctx.store.set("stt_engine", engine)
            self.ctx.store.set("stt_device", runtime)
            self._fill_runtimes(engine, runtime)
            self._show_biasing_note(engine)
        self.ctx.set("stt_model", model)
        # A new pick is a new attempt, so the last one's verdict goes. Without
        # this, choosing a model that turns out to be cached already leaves
        # "Download failed: …" sitting under a model that loaded fine.
        if not self._downloading:
            self._failed = False
            self._outcome = ""
            self._show_download_state()

    def _commit_runtime(self) -> None:
        runtime = self.runtime.currentData()
        if not self._repopulating and runtime:
            self.ctx.set("stt_device", runtime)

    def _commit_llm_runtime(self) -> None:
        """No `_repopulating` guard: this list is fixed at construction — one
        cleanup engine, so nothing can rewrite it under the user."""
        runtime = self.llm_runtime.currentData()
        if runtime:
            self.ctx.set("llm_runtime", runtime)

    def _toggle_advanced(self, shown: bool) -> None:
        self.advanced_card.setVisible(shown)
        self.advanced.setText(ADVANCED_HIDE if shown else ADVANCED_SHOW)

    def _show_biasing_note(self, engine: str) -> None:
        note = settings.biasing_note(engine)
        self.biasing.setText(note)
        self.biasing.setVisible(bool(note))

    # ---- the download: watched, stoppable, and never a dead end ------------

    def _on_download_clicked(self) -> None:
        """One button, two verbs — the wizard's shape (§6.5).

        The cancel half **asks; it does not announce.** Only the app knows when
        a fetch has actually unwound, so the line says what has been asked for
        and waits for `mark_download_cancelled` (#114).
        """
        if self._downloading:
            if self._cancelling:
                return
            self._cancelling = True
            self.cancel_requested.emit()
            self._show_download_state()
            a11y.announce(self.download_status, CANCELLING)
            return
        self.mark_download_started()
        self.download_requested.emit()

    def mark_download_started(self) -> None:
        """A fetch is under way — clicked here, or begun before this window
        opened and adopted by it."""
        self._downloading = True
        self._cancelling = False
        self._progress = Progress(0, 0)
        self._outcome = ""
        self._show_download_state()

    def report_progress(self, reading: Progress) -> None:
        """One reading, on the UI thread. Not announced: a screen reader
        reciting a byte count every ten megabytes is noise. The bar carries it,
        and both ends of the download are announced."""
        if not self._downloading:
            return      # a last chunk landing after the cancel was confirmed
        self._progress = reading
        self._show_download_state()

    def mark_download_finished(self, ok: bool, detail: str = "") -> None:
        self._settle(ok, READY if ok else f"Download failed: {detail}")

    def mark_download_cancelled(self) -> None:
        self._settle(False, CANCELLED)

    def _settle(self, ok: bool, outcome: str) -> None:
        self._downloading = False
        self._cancelling = False
        self._failed = not ok
        self._outcome = outcome
        self._show_download_state()
        a11y.announce(self.download_status, outcome)

    def _needs_a_download(self) -> bool:
        """Is there a dead end here for the button to be the way out of?

        Two of them, and neither is reachable any other way from this pane.
        A wizard whose model step was skipped leaves the app with no weights at
        all; a fetch that failed or was cancelled leaves the user looking at
        the model they were denied — and the combo is already sitting on it, so
        re-picking it writes nothing and starts nothing.

        The rest of the time the button stays away: choosing a model from the
        list is what downloads one, and a second control for the same act is a
        second thing to explain.
        """
        return self._failed or not hardware.any_speech_model_downloaded(MODELS_DIR)

    def _show_download_state(self) -> None:
        """Paint the download from the state. The one place that decides what
        the button says, whether it is there at all, and what the line reads."""
        running = self._downloading
        self.download_bar.setVisible(running)
        self.download_bar.setValue(self._progress.percent)
        self.download_button.setText(CANCEL_DOWNLOAD if running else DOWNLOAD)
        self.download_button.setEnabled(not self._cancelling)
        self.download_button.setVisible(running or self._needs_a_download())
        if self._cancelling:
            text = CANCELLING
        elif running:
            text = f"Downloading… {self._progress.caption}"
        else:
            text = self._outcome
        self.download_status.setText(text)
        self.download_status.setVisible(bool(text))

    # ---- the cleanup list --------------------------------------------------

    def _fill_cleanup_models(self) -> None:
        """Four rungs, chipped by what this machine can hold and how fast.

        No `_repopulating` guard here, unlike the speech list: the only handler
        on this combo is wired to `activated`, which Qt fires for a *user* pick
        and never for a programmatic one.
        """
        self.llm.clear()
        configured = self.ctx.config.llm_model_path
        hw = hardware.detect_safely()
        recommended = models.recommended_cleanup(hw.ram_gb, hw.physical_cores,
                                                 hw.metal_gpu)
        for model in models.CLEANUP_MODELS:
            add_model_row(self.llm, model.id, model.title, model.subtitle,
                          badge=(models.RECOMMENDED
                                 if model.id == recommended else ""),
                          warning=models.cleanup_warning(model, hw.ram_gb,
                                                         hw.physical_cores,
                                                         hw.metal_gpu))
        if models.cleanup_model(configured) is None and configured:
            # Bring-your-own-file: what is running keeps a row of its own.
            add_model_row(self.llm, configured, Path(configured).name,
                          OWN_FILE_BLURB)
        add_model_row(self.llm, CHOOSE_A_FILE, CHOOSE_A_FILE_TITLE,
                      CHOOSE_A_FILE_BLURB)
        self._select_configured()

    def _commit_llm(self, index: int) -> None:
        value = self.llm.itemData(index)
        if value == CHOOSE_A_FILE:
            self._choose_llm_file()
            return
        path = str(_LLM_DIR / value) if models.cleanup_model(value) else value
        if path != self.ctx.config.llm_model_path:
            self.ctx.set("llm_model_path", path)

    def _choose_llm_file(self) -> None:
        """The escape hatch, as a file picker rather than a paste-a-path combo.

        Cancelling has to put the selection back: the item the user clicked is
        an action, not a model, and leaving it selected would show "Choose a
        file…" as the cleanup model in use (#112).
        """
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a cleanup model", str(_LLM_DIR),
            "GGUF models (*.gguf);;All files (*)")
        if not path:
            self._select_configured()
            return
        self.ctx.set("llm_model_path", path)
        self._fill_cleanup_models()

    def _select_configured(self) -> None:
        """Point the combo at the model actually in use.

        A rung is stored as a full path and listed by its filename, so what to
        look for is not simply what config.json holds.
        """
        configured = self.ctx.config.llm_model_path
        rung = models.cleanup_model(configured)
        wanted = rung.id if rung is not None else configured
        self.llm.setCurrentIndex(max(self.llm.findData(wanted), 0))
