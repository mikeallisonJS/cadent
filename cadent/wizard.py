"""The first-run wizard (spec §6).

Six pages, linear Back/Next with progress dots, ending in a working dictation
setup. The one consented, user-initiated exception to CONTEXT.md's "toast,
never a prompt": the user asked for setup, so setup gets a window.

Two policies do most of the work here:

- **Finish is unreachable without a model, but the page is not.** Next still
  gates on a downloaded model, so a *completed* wizard always leaves a
  dictation-capable app. The model page carries "Skip for now" alongside it,
  because the download is the one step that needs the network and an offline
  machine was otherwise walled in on page five with no way forward and no way
  out but Escape. Skipping is not completing: it lands in exactly the state a
  Cancel does.
- **Cancel leaves the app resident in the tray with dictation disabled**, a
  bold "Finish setup…" first in the tray menu, and amber on the icon. Quitting
  the wizard must never strand anyone.

Existing users never see this: a model already exists on disk (§6.4).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import a11y, gpu_pack, hardware, models, pill, platform, settings
from .chord import describe_combo
from .config import MODELS_DIR
from .config_store import ConfigStore
from .downloads import Progress
from .overlay import Meter
from .settings_ui.model_picker import fill_speech_models, model_combo
from .settings_ui.widgets import CappedCombo, card, label, row

log = logging.getLogger(__name__)

WELCOME, BASICS, PERMISSION, GPU, MODEL, HOTKEY, DONE = range(7)

PAGE_TITLES = (
    "Welcome to Cadent",
    "Microphone & basics",
    "Permission",
    "GPU support pack",
    "Speech model",
    "Your dictation hotkey",
    "You're set up",
)

# The three skippable steps (§6.3). The permission step needs no Skip: Next is
# never gated on the grant — the wizard teaches, the Settings banner nags — so
# a managed Mac that can't grant still finishes setup. MODEL is skippable for
# the opposite reason: it *is* gated, on the one thing a machine with no
# network cannot produce.
SKIPPABLE = frozenset({GPU, MODEL, HOTKEY})

# What the model page says about its own Skip. Shown only while there is no
# model, so a wizard reopened over a working install doesn't offer a way out
# of a page nobody is stuck on.
SKIP_MODEL_HINT = (
    "No connection right now? Skip this for now — Cadent stays in the tray "
    "with dictation off, and you can download a model later from "
    "Settings ▸ Speech & cleanup.")

# The last page when the model step was skipped. It is not "You're set up",
# and the title says so too (SKIPPED_TITLE): dictation does not work yet, and
# a summary page that congratulates you anyway is the wizard lying about the
# one thing it exists to establish.
SKIPPED_TITLE = "Almost set up"
NO_MODEL_YET = ("Dictation stays off until a speech model is downloaded. "
                "Open Settings ▸ Speech & cleanup when you're online, or pick "
                "\"Finish setup…\" from the tray menu to come back here.")




@dataclass
class WizardState:
    """What the wizard has established so far.

    Kept separate from the widgets so the page-gating rules — the part with
    actual consequences — are testable without a screen.
    """

    gpu_eligible: bool = False
    # Capabilities.permission names a grant and it is missing (M5 §7, ADR
    # 0012) — one step for darwin and the Linux portals. Fixed at construction: a
    # page that vanished mid-wizard the moment the grant landed would shuffle
    # the step count under the user.
    permission_needed: bool = False
    model_downloaded: bool = False
    downloading: bool = False
    # Asked to stop, not yet stopped. Still `downloading` as far as Escape is
    # concerned: the fetch keeps running until it unwinds, and the page must
    # not claim otherwise (#114).
    cancelling: bool = False

    def pages(self) -> list[int]:
        """The page sequence: the GPU page joins only when eligible, the
        permission step only where a grant is missing (M5 §7)."""
        return [p for p in (WELCOME, BASICS, PERMISSION, GPU, MODEL, HOTKEY,
                            DONE)
                if (p != GPU or self.gpu_eligible)
                and (p != PERMISSION or self.permission_needed)]

    def can_advance(self, page: int) -> bool:
        """**The model page gates progress.** Next enables only once a model
        is downloaded, which is what makes a completed wizard always leave a
        dictation-capable app."""
        if page == MODEL:
            return self.model_downloaded
        return True

    def can_skip(self, page: int) -> bool:
        """Skip withdraws while the model page is downloading, for the reason
        Escape goes inert there: walking off a running 3.1 GB fetch by pressing
        the footer button next to it is the same lost download."""
        if page == MODEL and self.downloading:
            return False
        return page in SKIPPABLE

    def can_cancel(self) -> bool:
        """Escape cancels to tray-only, but goes inert while a download is
        running, so a stray key cannot bin 750 MB. That page carries its own
        Cancel download control instead."""
        return not self.downloading

    def is_complete(self, page: int) -> bool:
        return page == DONE and self.model_downloaded


class SetupWizard(QDialog):
    """The window. Page logic lives in WizardState; this renders it."""

    finished_setup = Signal(bool)       # True when setup completed
    download_requested = Signal(str)    # model name; the app owns the download
    cancel_requested = Signal()
    change_hotkey_requested = Signal()  # the escape hatch on the Hotkey page

    def __init__(self, store: ConfigStore, overlay=None, *,
                 devices: list[str] | None = None, tokens: dict | None = None,
                 mic_monitor=None, install_gpu_pack=None,
                 parent=None) -> None:
        super().__init__(parent)
        from .theme.tokens import tokens as default_tokens

        self.setObjectName("Root")
        self.setWindowTitle("Cadent — Setup")
        self.resize(720, 560)
        self.store = store
        self.overlay = overlay
        self._t = tokens or default_tokens("dark")
        self.state = WizardState()
        self.state.model_downloaded = hardware.any_speech_model_downloaded(MODELS_DIR)
        # The permission step (M5 §7, ADR 0012): joins when the platform
        # names a preflight and the grant is missing right now. The app's
        # grant poll calls `refresh_permission()` while the page is showing,
        # so granting outside Cadent and coming back needs no button.
        caps = platform.current().capabilities
        self.state.permission_needed = bool(caps.permission) and \
            not platform.current().focused_app.permission_granted()
        # The last reading from the app's downloader. On the state rather than
        # on the bar, because the page is rebuilt on every visit.
        self._progress = Progress(0, 0)
        self._completed = False
        self._devices = devices or []
        # The microphone page's own capture (#105). Not the dictation
        # recorder: that only has a level while it is capturing a dictation,
        # so on a page where nobody is holding the hotkey it reads 0.0 and the
        # meter paints its floor forever — which is exactly what a wrong input
        # device looks like, on the one page built to catch one.
        self._mic = mic_monitor
        self._install_gpu_pack = install_gpu_pack
        # Drives the microphone page's live meter; both the timer and the
        # capture stop whenever that page is not showing.
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(round(1000 / int(self._t["fps"])))
        self._meter_connected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(self._t["sp_6"]), int(self._t["sp_5"]),
                                  int(self._t["sp_6"]), int(self._t["sp_4"]))
        layout.setSpacing(int(self._t["sp_3"]))

        self.title = label("", "Display", wrap=False)
        self.dots = label("", "RowHint", wrap=False)
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.dots)
        layout.addLayout(header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(int(self._t["sp_3"]))
        layout.addWidget(self.body, 1)

        # The try-it step mirrors pill state into an announced status line —
        # the one step whose entire purpose is *confirmation* (§8.4).
        self.status = label("", "RowDesc")
        layout.addWidget(self.status)

        footer = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back)
        footer.addWidget(self.back_button)
        footer.addStretch()
        self.skip_button = QPushButton("Skip for now")
        self.skip_button.clicked.connect(self.skip)
        footer.addWidget(self.skip_button)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("Accent")
        # Enter activates the primary button uniformly on every page,
        # including the Model page's disclosed download: a focused, labelled
        # default button is user-initiated, and exempting one page of six is
        # hard to discover and reads as a broken wizard (§6.5).
        self.next_button.setDefault(True)
        self.next_button.setAutoDefault(True)
        self.next_button.clicked.connect(self.advance)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)

        self._index = 0
        # Render before probing, never the other way round (#119). Building a
        # widget takes Qt's signal/slot connection mutex while the builder
        # holds the GIL; a garbage collection on the probe thread destroys
        # some other QObject, which takes that same mutex and then asks
        # shiboken for the GIL back. Started first, the probe overlapped this
        # constructor's own page build every single time — a deadlock waiting
        # for the collector to land in the window. Welcome also has nothing to
        # say about the hardware, so there is nothing to wait for.
        self._render()
        self._probe_hardware()

    # ---- hardware ----------------------------------------------------------

    def _probe_hardware(self) -> None:
        """Probe once, off the UI thread, cached.

        `cuInit` can block, so the wizard opens on Welcome immediately and the
        GPU page joins the sequence when the answer arrives — which is always
        well before anyone clicks past page one.

        The answer comes back through a plain list polled by a QTimer, not a
        signal emitted from the thread (#119). A wizard closed while cuInit
        still blocks can be *destroyed* before the probe returns, and a
        cross-thread emit walks the sender's C++ connection list — racing
        that against destruction is heap corruption that surfaces as an
        access violation wherever the next allocation lands. A thread that
        only appends to a Python list has nothing to race: the timer dies
        with the wizard, and the list outlives the C++ side harmlessly.
        """
        answer: list[bool] = []

        def probe() -> None:
            try:
                # The capability gates before the hardware gets a vote: where
                # no pack exists to download (darwin — Metal ships in the
                # build), no machine earns the page (M5 §7).
                if not platform.current().capabilities.gpu_pack_available:
                    answer.append(False)
                    return
                detected = hardware.detect_cached()
                engine = settings.engine_for_model(
                    hardware.suggest_for_this_machine().model)
                edition = gpu_pack.edition_for(engine)
                eligible = hardware.should_offer_gpu_page(
                    detected.nvidia_driver, detected.vram_gb,
                    gpu_pack.pack_installed(engine=engine), engine,
                    edition_exists=edition is not None,
                    # A pre-580 driver cannot run the CUDA-13 edition: no
                    # page — the Speech pane's row says to update instead.
                    driver_ok=edition is not None and gpu_pack.driver_supports(
                        edition, detected.cuda_driver_version))
            except Exception:
                log.debug("hardware probe failed", exc_info=True)
                eligible = False
            answer.append(eligible)

        self._probe_answer = answer
        self._probe_thread = threading.Thread(target=probe, daemon=True)
        self._probe_thread.start()
        self._probe_timer = QTimer(self)
        self._probe_timer.timeout.connect(self._poll_probe)
        self._probe_timer.start(50)

    def _poll_probe(self) -> None:
        if self._probe_answer:
            self._probe_timer.stop()
            self._on_hardware_probed(self._probe_answer[0])

    def _on_hardware_probed(self, eligible: bool) -> None:
        if eligible == self.state.gpu_eligible:
            return
        current = self.page
        self.state.gpu_eligible = eligible
        # Keep the user on the page they are looking at, whatever its index
        # became.
        pages = self.state.pages()
        self._index = pages.index(current) if current in pages else 0
        self._render()

    # ---- navigation --------------------------------------------------------

    @property
    def page(self) -> int:
        return self.state.pages()[self._index]

    def advance(self) -> None:
        if not self.state.can_advance(self.page):
            return
        self._go_forward()

    def back(self) -> None:
        self._index = max(self._index - 1, 0)
        self._render()

    def skip(self) -> None:
        """**Skip is not Next.** It moves past a page the gate would otherwise
        hold you on, which is its entire job now that the model page — the only
        gated one — carries a Skip. Routing it through `advance` made the two
        buttons agree on the model page and do nothing, forever.
        """
        if not self.state.can_skip(self.page):
            return
        self._go_forward()

    def _go_forward(self) -> None:
        """The move itself, once Next or Skip has decided it may happen."""
        if self.page == GPU:
            self._accept_gpu_choice()
        if self.page == DONE:
            self._finish()
            return
        self._index = min(self._index + 1, len(self.state.pages()) - 1)
        self._render()

    def _finish(self) -> None:
        self._completed = self.state.is_complete(DONE)
        self.finished_setup.emit(self._completed)
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key.Key_Escape and not self.state.can_cancel():
            event.ignore()      # a stray key must not bin 750 MB
            return
        super().keyPressEvent(event)

    def _stop_monitoring(self) -> None:
        """Let go of the microphone and stop repainting the meter.

        Holding an input stream open behind five other pages would keep the
        Windows mic-in-use indicator lit for the rest of setup, which is a bad
        look for an app whose pitch is that nothing leaves the machine.
        """
        self._meter_timer.stop()
        if self._meter_connected:
            self._meter_timer.timeout.disconnect()
            self._meter_connected = False
        if self._mic is not None:
            self._mic.stop()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing from the microphone page is the one exit that does not go
        # through a page change.
        self._stop_monitoring()
        if not self._completed:
            # Cancel: the app stays resident in the tray with dictation
            # disabled and an obvious way back (§6.4).
            self.finished_setup.emit(False)
        super().closeEvent(event)

    # ---- rendering ---------------------------------------------------------

    def _render(self) -> None:
        page = self.page
        pages = self.state.pages()
        title = self._title_for(page)
        self.title.setText(title)
        self.dots.setText(f"Step {self._index + 1} of {len(pages)}")

        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent *now*, not on the next event loop turn: taking a
                # widget out of a layout leaves it parented, so it keeps
                # painting at its old geometry and the previous page shows
                # through the new one.
                widget.setParent(None)
                widget.deleteLater()

        self._stop_monitoring()

        builder = {
            WELCOME: self._build_welcome, BASICS: self._build_basics,
            PERMISSION: self._build_permission,
            GPU: self._build_gpu, MODEL: self._build_model,
            HOTKEY: self._build_hotkey, DONE: self._build_done,
        }[page]
        first_control = builder()

        self.back_button.setEnabled(self._index > 0)
        self.next_button.setText("Finish" if page == DONE else "Next")
        self._refresh_footer()

        # Page changes move focus to the new page's first interactive control
        # and announce it. Without this, focus is stranded on a button that no
        # longer exists and a screen reader never learns the content changed.
        target = first_control or self.next_button
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        a11y.announce(self.title,
                      f"Step {self._index + 1} of {len(pages)} — {title}")
        self.title.setAccessibleName(title)

    def _title_for(self, page: int) -> str:
        if page == DONE and not self.state.model_downloaded:
            return SKIPPED_TITLE
        return PAGE_TITLES[page]

    def _refresh_footer(self) -> None:
        """The two footer controls the state can change under a standing page.

        Both answers move when a download starts or settles, which is a thing
        that happens *between* renders — the model page is the only one whose
        footer is not fixed for as long as you are looking at it.
        """
        page = self.page
        self.skip_button.setVisible(self.state.can_skip(page))
        self.next_button.setEnabled(self.state.can_advance(page))

    def _add(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    # ---- pages -------------------------------------------------------------

    def _build_welcome(self) -> QWidget | None:
        self._add(label(
            "Hold a key, speak, release — Cadent types what you said. "
            "Everything runs on this PC; nothing you dictate is uploaded.",
            "RowDesc"))
        self.body_layout.addStretch()
        return None

    def _build_basics(self) -> QWidget | None:
        # The same row as Settings ▸ General, so it gets the same answer to the
        # same 44-character device names (#106) — picking a microphone is one
        # question asked in two places (#105).
        self.device = CappedCombo()
        self.device.addItem("System default", None)
        for name in self._devices:
            self.device.addItem(name, name)
        self.device.setCurrentIndex(
            max(self.device.findData(self.store.config.input_device), 0))
        self.device.currentIndexChanged.connect(self._on_device_changed)

        self.autostart = QCheckBox()
        self.autostart.setChecked(self.store.config.autostart)
        self.autostart.toggled.connect(lambda on: self.store.set("autostart", on))

        # A live meter, so "is this the right microphone?" is answered by
        # speaking rather than by finishing setup and finding out (§6.1).
        self.meter = Meter(self._t)
        self.meter.level_source = (lambda: self._mic.level) if self._mic else None
        self.meter.mode = pill.VOICE
        self._meter_timer.timeout.connect(self.meter.tick)
        self._meter_connected = True

        self._add(card([
            row(self._t, "Microphone", self.device,
                desc="Which input device dictation records from"),
            row(self._t, "Say something to check it", self.meter,
                desc="The bars follow your voice"),
            row(self._t, platform.current().capabilities.autostart_label,
                self.autostart, desc="Run Cadent when you sign in"),
        ]))
        self.body_layout.addStretch()
        if self._mic is not None:
            self._mic.start(self.store.config.input_device)
            self._meter_timer.start()
        return self.device

    def _on_device_changed(self, _index: int) -> None:
        """The combo sits in the row above the meter, so the meter has to
        follow it — otherwise it keeps answering about the old device."""
        device = self.device.currentData()
        self.store.set("input_device", device)
        if self._mic is not None:
            self._mic.start(device)

    def _build_permission(self) -> QWidget | None:
        """The permission step (M5 §7, ADR 0012): the platform's words, its
        request action, and a live re-check driven by the app's grant poll.
        Never a gate — see SKIPPABLE's comment."""
        plat = platform.current()
        permission = plat.capabilities.permission
        self._add(label(permission.wizard_body, "RowDesc"))
        self.open_settings = QPushButton(permission.action_label)
        self.open_settings.clicked.connect(plat.desktop.request_permission)
        self._add(self.open_settings)
        self.permission_status = label("", "RowHint")
        self._add(self.permission_status)
        self.body_layout.addStretch()
        self.refresh_permission()
        return self.open_settings

    def refresh_permission(self) -> None:
        """Re-read the grant while the permission page is showing; called by
        the app's poll (and once on build). Safe on any other page."""
        if self.page != PERMISSION or not hasattr(self, "permission_status"):
            return
        plat = platform.current()
        permission = plat.capabilities.permission
        granted = plat.focused_app.permission_granted()
        was = self.permission_status.text()
        self.permission_status.setText(
            permission.granted if granted else permission.waiting)
        if granted and was == permission.waiting:
            # Announce the arrival once — the one state change on a page
            # whose whole point is confirmation.
            a11y.announce(self.permission_status, permission.granted)

    def _build_gpu(self) -> QWidget | None:
        """Offered **before** model choice, because accepting it changes the
        recommendation. The page discloses the edition and size for the
        engine the recommendation is about to name (ADR 0010)."""
        engine = settings.engine_for_model(hardware.suggest_for_this_machine().model)
        edition = gpu_pack.edition_for(engine)
        what = ("CUDA libraries" if edition is not None and edition.key != "cublas12"
                else "cuBLAS libraries")
        self._add(label(
            f"This PC has an NVIDIA GPU. Cadent can download NVIDIA's "
            f"{what} ({gpu_pack.download_size(engine)}, one-time, from PyPI) "
            "to transcribe on the GPU instead of the CPU. This is the only "
            "network activity Cadent performs besides the speech model.",
            "RowDesc"))
        self.gpu_yes = QRadioButton("Download the GPU support pack")
        self.gpu_no = QRadioButton("Stay on CPU")
        self.gpu_no.setChecked(True)
        self._add(self.gpu_yes)
        self._add(self.gpu_no)
        self.body_layout.addStretch()
        return self.gpu_yes

    def _accept_gpu_choice(self) -> None:
        """Leaving the GPU page is what acts on it — accepting the pack before
        the model page is the whole reason the page comes first."""
        wanted = getattr(self, "gpu_yes", None)
        if wanted is not None and wanted.isChecked() \
                and self._install_gpu_pack is not None:
            self._install_gpu_pack()

    def _build_model(self) -> QWidget | None:
        """Explicit, consented, sized and sourced — the privacy claim made
        visible on day one."""
        suggestion = hardware.suggest_for_this_machine()
        self._add(label(
            "Cadent downloads one speech model from Hugging Face. After "
            "that it never touches the network to transcribe.", "RowDesc"))

        # One flat list across both engines rather than an engine picker the
        # wizard would then have to explain: the model names say which is
        # which, and picking one picks its engine (#72). Since #111 it is the
        # *same* list Settings shows, built by the same call — a model that
        # reads one way during setup and another way afterwards is two lists
        # to trust. Parakeet's rows are shown and disabled where they cannot
        # run, reversing this page's first cut ("a 670 MB download with
        # nothing to run it on is not a choice, it is a trap"): a disabled row
        # carrying its own reason is not a trap, and hiding it makes the
        # engine undiscoverable to anyone who later adds a graphics card.
        self.model = model_combo(self._t)
        recommended = fill_speech_models(self.model)
        self.model.setCurrentIndex(max(self.model.findData(recommended), 0))
        self._add(card([
            row(self._t, "Model", self.model, desc=suggestion.reason),
        ]))

        self.download_button = QPushButton("Download model")
        self.download_button.setObjectName("Accent")
        self.download_button.clicked.connect(self._start_download)
        self._add(self.download_button)

        # A determinate bar rather than a spinner: the numbers exist, and the
        # question during a 3.1 GB download is "how much longer", which a
        # spinner cannot answer. Hidden until there is something to report.
        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 100)
        self.download_bar.setTextVisible(False)
        self.download_bar.setAccessibleName("Download progress")
        self._add(self.download_bar)

        self.download_status = label("", "RowHint")
        self._add(self.download_status)

        # Names the footer's Skip and what taking it costs. The button alone
        # says "Skip for now" on a page whose whole subject is a download, and
        # the thing anyone offline needs to know is where the model comes from
        # afterwards.
        self.skip_hint = label(SKIP_MODEL_HINT, "RowHint")
        self._add(self.skip_hint)
        self.body_layout.addStretch()
        # Every page is rebuilt on every visit, so a download you walked away
        # from has to be read back off the state rather than remembered by
        # widgets that no longer exist.
        self._show_download_state()
        return self.model

    def _build_hotkey(self) -> QWidget | None:
        self._add(label(
            f"Hold {describe_combo(self.store.config.hotkey)}, say something, "
            "then let go. Cadent types it wherever your cursor is.",
            "RowDesc"))
        # The pill is always-on-top and this window is not, so the try-it page
        # leaves the bottom-centre clear rather than having the pill cover the
        # instructions being read (§4.8).
        self.body_layout.addStretch()
        self.change_hotkey = QPushButton("Change hotkey…")
        self.change_hotkey.clicked.connect(self.change_hotkey_requested.emit)
        self._add(self.change_hotkey)
        self.body_layout.addSpacing(int(self._t["pill_margin_bottom"]) + 60)
        return self.change_hotkey

    def _build_done(self) -> QWidget | None:
        if self.state.model_downloaded:
            # By the name it was picked under, not by its filename: `distil-
            # small.en` on the last page of setup is the thing #111 removed
            # from the page before it.
            picked = models.speech_model(self.store.config.stt_model)
            model = picked.title if picked is not None else self.store.config.stt_model
        else:
            # The model page was skipped. `config.stt_model` still names the
            # default, and printing it here would have the summary page — the
            # one page whose entire job is to say what was set up — name a
            # model that is not on disk.
            model = "none yet"
        device = self.store.config.input_device or "System default"
        self._add(label(
            f"Microphone: {device}\nSpeech model: {model}\n"
            f"Hotkey: hold {describe_combo(self.store.config.hotkey)} "
            "and speak.", "RowDesc"))
        if not self.state.model_downloaded:
            self._add(label(NO_MODEL_YET, "RowHint"))
        # The support-tier line (M6 §9.4), only where the platform names a
        # permission preflight — the Wayland tiers, whose promise is not the
        # whole loop. Whole (like Windows) says nothing here.
        caps = platform.current().capabilities
        if caps.permission is not None and caps.support_tier_summary:
            self.tier_line = label(caps.support_tier_summary, "RowHint")
            self._add(self.tier_line)
        self.body_layout.addStretch()
        return None

    # ---- the model download ------------------------------------------------

    def _start_download(self) -> None:
        if self.state.downloading:
            # The page carries its own Cancel download control, because Escape
            # goes inert while one is running (§6.5). **It asks; it does not
            # announce.** This button used to emit a signal nobody consumed,
            # write "Download cancelled." and leave the download running — a
            # button reporting success while doing nothing is worse than no
            # button at all (#114). The page now says what it has asked for and
            # waits for `mark_download_cancelled`.
            if self.state.cancelling:
                return
            self.state.cancelling = True
            self.cancel_requested.emit()
            self._show_download_state()
            a11y.announce(self.download_status, self.download_status.text())
            return
        self.state.downloading = True
        self.state.cancelling = False
        self._progress = Progress(0, 0)
        self._show_download_state()
        self._refresh_footer()      # Skip withdraws for as long as this runs
        a11y.announce(self.download_status, "Downloading the speech model.")
        self.download_requested.emit(self.model.currentData())

    def report_progress(self, progress: Progress) -> None:
        """One reading from the app's downloader, on the UI thread.

        Not announced: a screen reader reciting a byte count every ten
        megabytes is noise over the top of whatever the user is actually doing.
        The bar carries it, and the start and the end are both announced.
        """
        if not self.state.downloading:
            return      # a last chunk landing after the cancel was confirmed
        self._progress = progress
        self._show_download_state()

    def mark_download_finished(self, ok: bool, detail: str = "") -> None:
        """Called by the app once the model is on disk (or failed)."""
        self.state.model_downloaded = ok
        self._settle_download(
            "Speech model ready." if ok else f"Download failed: {detail}")

    def mark_download_cancelled(self) -> None:
        """Called by the app once the fetch has actually stopped.

        Stopping a download is not the same as having a model, so the page goes
        back to exactly where it started — Next included.
        """
        self._settle_download("Download cancelled.")

    def _settle_download(self, outcome: str) -> None:
        self.state.downloading = False
        self.state.cancelling = False
        # The page may have been walked away from and torn down while the
        # download ran; the state outlives its widgets, and the gate below is
        # what the user can still see.
        if self.page == MODEL:
            self._show_download_state(outcome)
            a11y.announce(self.download_status, outcome)
        self._refresh_footer()

    def _show_download_state(self, outcome: str | None = None) -> None:
        """Paint the model page from the state. The one place that decides what
        the button says, whether the bar is there, and what the line reads."""
        if self.page != MODEL:
            return      # the page has been torn down; the state is what lasts
        running = self.state.downloading
        self.download_bar.setVisible(running)
        self.download_bar.setValue(self._progress.percent)
        self.download_button.setText(
            "Cancel download" if running else "Download model")
        self.download_button.setEnabled(not self.state.cancelling)
        # Nobody with a model is stuck, and nobody mid-download may leave.
        self.skip_hint.setVisible(not running and not self.state.model_downloaded)
        if outcome is not None:
            self.download_status.setText(outcome)
        elif self.state.cancelling:
            self.download_status.setText("Cancelling the download…")
        elif running:
            self.download_status.setText(f"Downloading… {self._progress.caption}")
        elif self.state.model_downloaded:
            self.download_status.setText("A speech model is already installed.")

    # ---- try-it ------------------------------------------------------------

    def report_pill_state(self, text: str) -> None:
        """Mirror the pill into an announced status line.

        §4 stays intact: the desktop pill remains a non-announced tool window.
        Only the one step whose entire purpose is confirmation gets a
        non-visual channel.
        """
        self.status.setText(text)
        a11y.announce(self.status, text)
