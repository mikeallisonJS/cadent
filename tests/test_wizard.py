"""The first-run wizard (spec §6).

The gating rules are in WizardState precisely so the part with consequences —
"a completed wizard always leaves a dictation-capable app" — is testable
without a screen.
"""

import threading

import pytest
from conftest import pin_darwin_ui_platform
from PySide6.QtCore import Qt

from cadent import a11y, hardware, models
from cadent.config_store import ConfigStore
from cadent.downloads import Progress
from cadent.settings_ui.model_picker import CHIP_ROLE, SUBTITLE_ROLE
from cadent.wizard import (
    BASICS,
    DONE,
    GPU,
    HOTKEY,
    MODEL,
    PAGE_TITLES,
    PERMISSION,
    WELCOME,
    SetupWizard,
    WizardState,
)

# ---- page order and gating (§6.1, §6.3) ------------------------------------

def test_the_pages_run_in_order():
    state = WizardState(gpu_eligible=True)
    assert state.pages() == [WELCOME, BASICS, GPU, MODEL, HOTKEY, DONE]


def test_the_gpu_page_appears_only_when_eligible():
    """Offered *before* model choice, because accepting it changes the
    recommendation."""
    assert GPU not in WizardState(gpu_eligible=False).pages()
    assert WizardState(gpu_eligible=False).pages() == \
        [WELCOME, BASICS, MODEL, HOTKEY, DONE]


def test_the_permission_step_joins_only_when_a_grant_is_missing():
    """Spec §7 (#148): the Accessibility step is a darwin-only page, and only
    while the grant is missing — a granted machine's wizard looks exactly
    like Windows' minus the GPU page."""
    assert PERMISSION not in WizardState().pages()
    state = WizardState(permission_needed=True)
    assert state.pages() == [WELCOME, BASICS, PERMISSION, MODEL, HOTKEY, DONE]


def test_the_permission_step_sits_before_the_gpu_page():
    """Both conditional pages at once — the win32 shape with a grant missing
    can't happen live, but the ordering rule should hold anyway."""
    state = WizardState(gpu_eligible=True, permission_needed=True)
    assert state.pages() == [WELCOME, BASICS, PERMISSION, GPU, MODEL,
                             HOTKEY, DONE]


def test_the_model_page_gates_progress():
    """Next enables only once a model is downloaded."""
    state = WizardState()
    assert state.can_advance(MODEL) is False
    state.model_downloaded = True
    assert state.can_advance(MODEL) is True


def test_every_other_page_advances_freely():
    state = WizardState(gpu_eligible=True, permission_needed=True)
    for page in (WELCOME, BASICS, PERMISSION, GPU, HOTKEY, DONE):
        assert state.can_advance(page) is True


def test_the_gpu_model_and_hotkey_pages_can_be_skipped():
    state = WizardState(gpu_eligible=True, permission_needed=True)
    assert state.can_skip(GPU) is True
    assert state.can_skip(HOTKEY) is True
    # The one gated page is also the one that needs the network, which is why
    # it is skippable: an offline machine was otherwise walled in on page five.
    assert state.can_skip(MODEL) is True
    # The permission step needs no Skip: Next is never gated on the grant —
    # the Settings banner carries the nag, the wizard only teaches.
    for page in (WELCOME, BASICS, PERMISSION, DONE):
        assert state.can_skip(page) is False


def test_skip_withdraws_from_the_model_page_while_a_download_runs():
    """Same reason Escape goes inert there: walking off a running 3.1 GB fetch
    by clicking the footer button next to it is the same lost download."""
    state = WizardState()
    assert state.can_skip(MODEL) is True
    state.downloading = True
    assert state.can_skip(MODEL) is False
    # The other two are unaffected — no download of theirs is at stake.
    assert WizardState(gpu_eligible=True, downloading=True).can_skip(GPU) is True


def test_skipping_the_model_is_not_a_completed_setup():
    """Skipping lands exactly where Cancel does: resident in the tray with
    dictation off and a bold "Finish setup…" to come back through."""
    state = WizardState()
    state.pages()                       # skip does not change the sequence
    assert state.can_skip(MODEL) is True
    assert state.is_complete(DONE) is False


def test_finishing_without_a_model_is_not_a_completed_setup():
    assert WizardState().is_complete(DONE) is False
    assert WizardState(model_downloaded=True).is_complete(DONE) is True


def test_escape_goes_inert_while_a_download_is_running():
    """A stray key must not bin 750 MB. That page carries its own Cancel
    download control instead (§6.5)."""
    state = WizardState()
    assert state.can_cancel() is True
    state.downloading = True
    assert state.can_cancel() is False


# ---- the window ------------------------------------------------------------

@pytest.fixture
def wizard(qt_app, tmp_path, monkeypatch, pinned_win32_facts, machine):
    # Pinned to a machine with nothing for DirectML to run on, so the model
    # page's contents don't depend on the developer's graphics card (#72) —
    # and to the win32 facts, so the gpu-only chips these pages describe
    # don't depend on the developer's OS (#146). The darwin wizard surfaces
    # are #148's. The GPU box has its own fixture below.
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    machine(dx12=False)
    store = ConfigStore(tmp_path / "config.json")
    win = SetupWizard(store, devices=["Rode NT-USB Mini"])
    yield win
    win._completed = True       # don't emit a cancel on teardown
    win.close()
    win.deleteLater()


@pytest.fixture
def gpu_wizard(qt_app, tmp_path, monkeypatch, pinned_win32_facts, machine):
    """A box with a Direct3D 12 device and measurable VRAM — where Parakeet is
    both offered and recommended."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    machine(dx12=True, vram=24.0)
    store = ConfigStore(tmp_path / "config.json")
    win = SetupWizard(store, devices=["Rode NT-USB Mini"])
    yield win
    win._completed = True
    win.close()
    win.deleteLater()


def test_it_opens_on_welcome_with_no_back(wizard):
    assert wizard.page == WELCOME
    assert wizard.back_button.isEnabled() is False


def test_walking_forward_reaches_done(wizard):
    wizard.state.model_downloaded = True
    seen = []
    for _ in range(len(wizard.state.pages())):
        seen.append(wizard.page)
        wizard.advance()
    assert seen[0] == WELCOME
    assert DONE in seen


def test_next_is_disabled_on_the_model_page_until_a_model_exists(wizard):
    while wizard.page != MODEL:
        wizard.advance()
    assert wizard.next_button.isEnabled() is False
    wizard.mark_download_finished(True)
    assert wizard.next_button.isEnabled() is True


def test_the_skip_button_only_appears_on_skippable_pages(wizard):
    wizard.state.model_downloaded = True
    while wizard.page != DONE:
        assert wizard.skip_button.isVisibleTo(wizard) == \
            wizard.state.can_skip(wizard.page)
        wizard.advance()


def test_enter_activates_the_primary_button_on_every_page(wizard):
    """Exempting one page of six is hard to discover and reads as a broken
    wizard (§6.5)."""
    wizard.state.model_downloaded = True
    while True:
        assert wizard.next_button.isDefault()
        if wizard.page == DONE:
            break
        wizard.advance()


def test_the_done_page_names_the_model_the_way_it_was_picked(wizard):
    """`distil-small.en` on the last page of setup is the filename #111 took
    off the page before it."""
    wizard.state.model_downloaded = True
    wizard.store.set("stt_model", "distil-small.en")
    while wizard.page != DONE:
        wizard.advance()
    body = " ".join(child.text() for child in wizard.findChildren(type(wizard.title))
                    if child.text())
    assert "Fast — Whisper Small" in body
    assert "distil-small.en" not in body


def test_the_done_page_still_names_a_model_only_config_json_knows(wizard):
    wizard.state.model_downloaded = True
    wizard.store.set("stt_model", "large-v3-turbo")
    while wizard.page != DONE:
        wizard.advance()
    body = " ".join(child.text() for child in wizard.findChildren(type(wizard.title))
                    if child.text())
    assert "large-v3-turbo" in body


def test_the_last_page_says_finish(wizard):
    wizard.state.model_downloaded = True
    while wizard.page != DONE:
        wizard.advance()
    assert wizard.next_button.text() == "Finish"


def test_finishing_reports_completion(wizard):
    results = []
    wizard.finished_setup.connect(results.append)
    wizard.state.model_downloaded = True
    while wizard.page != DONE:
        wizard.advance()
    wizard.advance()
    assert results == [True]


def test_cancelling_reports_incompletion(qt_app, tmp_path, monkeypatch):
    """The app stays resident in the tray with dictation disabled and an
    obvious way back — quitting the wizard must never strand anyone (§6.4)."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"))
    results = []
    win.finished_setup.connect(results.append)
    win.close()
    assert results == [False]


# ---- the pages themselves --------------------------------------------------

def test_no_page_restates_its_title_as_a_card_heading(wizard):
    """Tracked caps name a card inside a page (§1.5); a heading that repeats
    the page title one line above it names nothing. f0d1937 swept this out of
    the settings panes and #103 found the Speech model page still doing it."""
    from PySide6.QtWidgets import QLabel

    wizard.state.model_downloaded = True
    for _ in range(len(wizard.state.pages())):
        title = PAGE_TITLES[wizard.page].upper()
        headings = [w.text() for w in wizard.findChildren(QLabel)
                    if w.objectName() == "Caps"]
        assert title not in headings, \
            f"page {wizard.page} repeats its own title as a card heading"
        wizard.advance()


def test_the_basics_page_writes_its_choices_immediately(wizard):
    wizard.advance()
    assert wizard.page == BASICS
    wizard.autostart.setChecked(True)
    assert wizard.store.config.autostart is True


def subtitle(combo, index):
    return combo.itemData(index, SUBTITLE_ROLE)


def enabled(combo, index):
    return combo.model().item(index).isEnabled()


def test_the_model_page_preselects_a_suggestion_with_a_reason(wizard):
    """Pre-select the suggested model with a one-line "why"; the user can
    override from the full list (§6.2)."""
    while wizard.page != MODEL:
        wizard.advance()
    assert wizard.model.currentData() in [m.id for m in models.listed_speech_models()]
    assert wizard.model.accessibleDescription()


def test_the_model_page_discloses_size_and_source(wizard):
    while wizard.page != MODEL:
        wizard.advance()
    lines = [subtitle(wizard.model, i) for i in range(wizard.model.count())]
    assert all("MB" in line or "GB" in line for line in lines)
    body = " ".join(child.text() for child in wizard.findChildren(type(wizard.title))
                    if child.text())
    assert "Hugging Face" in body


def test_the_rows_read_as_choices_rather_than_filenames(wizard):
    """`distil-medium.en` is a Hugging Face repo name; "More accurate —
    Whisper Medium · 792 MB" is a choice (#111)."""
    while wizard.page != MODEL:
        wizard.advance()
    captions = [wizard.model.itemText(i) for i in range(wizard.model.count())]
    assert "More accurate — Whisper Medium" in captions
    assert not any("distil" in caption for caption in captions)


def test_the_curated_list_is_what_the_wizard_offers(wizard):
    """Six rows, not ten: `small.en` and friends stay loadable for a config
    that names them, but a first-run list of near-duplicates is not a choice."""
    while wizard.page != MODEL:
        wizard.advance()
    assert [wizard.model.itemData(i) for i in range(wizard.model.count())] == \
        [m.id for m in models.listed_speech_models()]


# ---- the darwin wizard (spec §7, #148) -------------------------------------

@pytest.fixture
def darwin_wizard(qt_app, tmp_path, monkeypatch, machine):
    """A darwin-shaped wizard: Accessibility not yet granted, no GPU pack —
    on hardware that *would* earn the GPU page under the win32 column, so the
    absence below is the capability's doing, never the probe's."""
    plat = pin_darwin_ui_platform(monkeypatch, granted=False)
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    machine(dx12=True, vram=24.0, driver=True)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      devices=["MacBook Pro Microphone"])
    yield win, plat
    win._completed = True
    win.close()
    win.deleteLater()


def at_permission_page(win):
    while win.page != PERMISSION:
        win.advance()
    return win


def test_the_darwin_wizard_swaps_the_gpu_page_for_the_permission_step(darwin_wizard):
    """The GPU page renders only when `gpu_pack_available` — Metal ships in
    the build, so there is nothing to download even on a big machine."""
    win, _plat = darwin_wizard
    win._probe_thread.join(timeout=10)
    win._poll_probe()
    assert PERMISSION in win.state.pages()
    assert GPU not in win.state.pages()


def test_a_granted_machine_sees_no_permission_step(qt_app, tmp_path,
                                                   monkeypatch, machine):
    pin_darwin_ui_platform(monkeypatch, granted=True)
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    machine()
    win = SetupWizard(ConfigStore(tmp_path / "config.json"), devices=[])
    try:
        assert PERMISSION not in win.state.pages()
    finally:
        win._completed = True
        win.close()
        win.deleteLater()


def test_the_permission_page_deep_links_to_system_settings(darwin_wizard):
    win, plat = darwin_wizard
    at_permission_page(win)
    win.open_settings.click()
    assert plat.desktop.permission_requests == 1


def test_the_permission_page_notices_the_grant_arriving(darwin_wizard):
    """The page re-checks on its own — granting in System Settings and coming
    back must not require finding a button."""
    win, plat = darwin_wizard
    at_permission_page(win)
    assert win.permission_status.text() == plat.capabilities.permission.waiting
    plat.focused_app.granted = True
    win.refresh_permission()
    assert win.permission_status.text() == plat.capabilities.permission.granted


def test_next_is_never_gated_on_the_grant(darwin_wizard):
    """The wizard teaches; the Settings banner nags. A managed Mac that can't
    grant must still finish setup."""
    win, _plat = darwin_wizard
    at_permission_page(win)
    assert win.next_button.isEnabled() is True


# ---- Parakeet on the model page (#72, #111) --------------------------------

def to_model_page(wizard):
    while wizard.page != MODEL:
        wizard.advance()
    return [wizard.model.itemData(i) for i in range(wizard.model.count())]


def test_a_gpu_box_is_offered_parakeet_alongside_the_whisper_models(gpu_wizard):
    """One flat list across both engines rather than an engine picker the
    wizard would have to explain."""
    offered = to_model_page(gpu_wizard)
    assert "parakeet-tdt-0.6b-v2" in offered
    assert "distil-small.en" in offered
    assert all(enabled(gpu_wizard.model, i) for i in range(len(offered)))


def test_a_gpu_box_has_parakeet_preselected_with_a_reason(gpu_wizard):
    to_model_page(gpu_wizard)
    assert gpu_wizard.model.currentData() == "parakeet-tdt-0.6b-v2"
    assert gpu_wizard.model.accessibleDescription()


def test_the_recommended_chip_follows_the_machine(gpu_wizard):
    """A static tag would label Whisper Small "Recommended" on a GPU box the
    suggestion table sends to Parakeet — the same widget disagreeing with
    itself (#111)."""
    offered = to_model_page(gpu_wizard)
    chips = {gpu_wizard.model.itemData(i, CHIP_ROLE) for i in range(len(offered))}
    assert models.RECOMMENDED in chips
    index = offered.index("parakeet-tdt-0.6b-v2")
    assert gpu_wizard.model.itemData(index, CHIP_ROLE) == models.RECOMMENDED


def test_a_box_with_no_direct3d_12_sees_parakeet_disabled_not_hidden(wizard):
    """Deliberately reverses this page's first cut. A disabled row carrying
    its own reason is not a trap, and hiding it makes the engine
    undiscoverable to anyone who later adds a graphics card (#111)."""
    offered = to_model_page(wizard)
    parakeet = [i for i, m in enumerate(offered) if str(m).startswith("parakeet")]
    assert len(parakeet) == 2
    for index in parakeet:
        assert not enabled(wizard.model, index)
        assert wizard.model.itemData(index, CHIP_ROLE) == models.NEEDS_A_GPU


def test_a_disabled_row_still_says_what_it_would_cost(wizard):
    offered = to_model_page(wizard)
    for index, name in enumerate(offered):
        if str(name).startswith("parakeet"):
            assert "MB" in subtitle(wizard.model, index)


def test_every_row_speaks_its_chip_as_well_as_its_two_lines(wizard):
    """One string carrying everything the eye gets (§8.4)."""
    offered = to_model_page(wizard)
    index = offered.index("parakeet-tdt-0.6b-v2")
    spoken = wizard.model.itemData(index, Qt.ItemDataRole.AccessibleTextRole)
    assert spoken.startswith(models.NEEDS_A_GPU)
    assert wizard.model.itemText(index) in spoken
    assert subtitle(wizard.model, index) in spoken


def test_the_try_it_page_leaves_the_bottom_clear_for_the_real_pill(wizard):
    """The pill is always-on-top and the wizard is not, so the try-it page
    must not have the pill cover the instructions being read (§4.8)."""
    wizard.state.model_downloaded = True
    while wizard.page != HOTKEY:
        wizard.advance()
    spacing = wizard.body_layout.itemAt(wizard.body_layout.count() - 1)
    assert spacing.spacerItem() is not None


# ---- accessibility (§8.4) --------------------------------------------------

def test_a_page_change_moves_focus_and_announces_the_step(wizard):
    """Without this, focus is stranded on a button that no longer exists and a
    screen reader never learns the content changed."""
    wizard.advance()
    assert wizard.device.hasFocus() or wizard.focusWidget() is wizard.device
    assert wizard.title.accessibleName() == "Microphone & basics"


def test_every_focusable_control_reports_a_name(wizard):
    wizard.state.model_downloaded = True
    while True:
        offenders = [(type(w).__name__, w.text() if hasattr(w, "text") else "")
                     for w in a11y.unnamed(wizard)]
        assert offenders == [], f"page {wizard.page}: {offenders}"
        if wizard.page == DONE:
            break
        wizard.advance()


def test_the_try_it_result_is_announced(wizard):
    """The one step whose entire purpose is confirmation gets a non-visual
    channel; the desktop pill stays a non-announced tool window."""
    wizard.report_pill_state("Inserted 7 words")
    assert wizard.status.text() == "Inserted 7 words"
    assert wizard.status.accessibleName() == "Inserted 7 words"


def test_starting_a_download_announces_it_and_blocks_escape(wizard):
    while wizard.page != MODEL:
        wizard.advance()
    wizard._start_download()
    assert wizard.state.can_cancel() is False
    assert wizard.download_button.text() == "Cancel download"


# ---- the pages actually do something (§6.1, §6.2) --------------------------

def test_the_microphone_page_carries_a_live_meter(qt_app, tmp_path, monkeypatch, mic):
    """"Is this the right microphone?" answered by speaking, rather than by
    finishing setup and finding out."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      devices=["Rode NT-USB Mini"], mic_monitor=mic)
    win.advance()
    assert win.page == BASICS
    assert win.meter.mode == "voice"
    assert win._meter_timer.isActive()
    win.meter.tick()
    assert any(bar > 0 for bar in win.meter._bars)
    win._completed = True
    win.close()


def test_the_page_opens_the_microphone_it_is_asking_about(
        qt_app, tmp_path, monkeypatch, mic):
    """The bug behind #105. The meter used to read `Recorder.level`, which is
    written only while a dictation is being captured — so on a page where
    nobody is holding the hotkey it was always 0.0, and the meter painted its
    floor forever. Nothing rendered wrong; there was simply no audio.

    So the assertion is not "the meter has a source" but "something is
    listening": a source that is never fed is what the bug *was*.
    """
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      devices=["Rode NT-USB Mini"], mic_monitor=mic)
    win.advance()
    assert win.page == BASICS
    assert mic.opened == [None]
    assert win.meter.level_source() > 0
    win._completed = True
    win.close()


def test_switching_device_moves_the_meter_to_it(qt_app, tmp_path, monkeypatch, mic):
    """The combo sits in the row above the meter; a meter that kept listening
    to the old device would answer the wrong question."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      devices=["Rode NT-USB Mini"], mic_monitor=mic)
    win.advance()
    win.device.setCurrentIndex(win.device.findData("Rode NT-USB Mini"))
    assert mic.opened == [None, "Rode NT-USB Mini"]
    win._completed = True
    win.close()


def test_the_meter_only_ticks_on_its_own_page(qt_app, tmp_path, monkeypatch, mic):
    """Nothing else in the wizard needs the microphone open."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"), mic_monitor=mic)
    win.advance()
    assert win._meter_timer.isActive()
    win.advance()
    assert win._meter_timer.isActive() is False
    win._completed = True
    win.close()


def test_leaving_the_page_closes_the_microphone(qt_app, tmp_path, monkeypatch, mic):
    """Holding an input stream open behind five other pages would keep the
    Windows mic-in-use indicator lit for the rest of setup."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"), mic_monitor=mic)
    win.advance()
    assert mic.opened == [None]
    win.advance()
    assert mic.stops >= 1
    assert mic.level == 0.0
    win._completed = True
    win.close()


def test_closing_the_wizard_on_the_page_closes_the_microphone(
        qt_app, tmp_path, monkeypatch, mic):
    """Cancelling from the microphone page is the one exit that does not go
    through a page change."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"), mic_monitor=mic)
    win.advance()
    win._completed = True
    win.close()
    assert mic.stops >= 1


def test_the_page_opens_without_a_monitor_at_all(qt_app, tmp_path, monkeypatch):
    """Tests and headless runs pass none; the page must still build."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"))
    win.advance()
    assert win.page == BASICS
    assert win._meter_timer.isActive() is False
    win._completed = True
    win.close()


def test_accepting_the_gpu_pack_actually_installs_it(qt_app, tmp_path, monkeypatch):
    """Offered *before* the model page precisely so accepting it changes the
    recommendation — which means it has to happen on the way past."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    installs = []
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      install_gpu_pack=lambda: installs.append(1))
    win.state.gpu_eligible = True
    win._index = win.state.pages().index(GPU)
    win._render()
    win.gpu_yes.setChecked(True)
    win.advance()
    assert installs == [1]
    win._completed = True
    win.close()


def test_declining_the_gpu_pack_installs_nothing(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    installs = []
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      install_gpu_pack=lambda: installs.append(1))
    win.state.gpu_eligible = True
    win._index = win.state.pages().index(GPU)
    win._render()
    win.advance()
    assert installs == []
    win._completed = True
    win.close()


def test_change_hotkey_leads_somewhere(wizard):
    """An escape hatch that does nothing is worse than no escape hatch."""
    asked = []
    wizard.change_hotkey_requested.connect(lambda: asked.append(1))
    wizard.state.model_downloaded = True
    while wizard.page != HOTKEY:
        wizard.advance()
    wizard.change_hotkey.click()
    assert asked == [1]


def test_the_download_button_cancels_a_running_download(wizard):
    """That page carries its own Cancel download control, because Escape goes
    inert while one is running (§6.5)."""
    cancelled = []
    wizard.cancel_requested.connect(lambda: cancelled.append(1))
    while wizard.page != MODEL:
        wizard.advance()
    wizard._start_download()
    wizard._start_download()
    assert cancelled == [1]


def test_the_download_is_requested_by_name(wizard):
    requested = []
    wizard.download_requested.connect(requested.append)
    while wizard.page != MODEL:
        wizard.advance()
    wizard.model.setCurrentIndex(wizard.model.findData("distil-medium.en"))
    wizard._start_download()
    assert requested == ["distil-medium.en"]


def test_a_late_hardware_probe_inserts_the_gpu_page_without_losing_your_place(wizard):
    """cuInit can block, so the wizard opens immediately and the page joins
    the sequence when the answer arrives."""
    wizard.advance()
    assert wizard.page == BASICS
    wizard._on_hardware_probed(True)
    assert wizard.page == BASICS          # still where the user was
    assert GPU in wizard.state.pages()


def test_destroying_the_wizard_mid_probe_is_safe(qt_app, tmp_path, monkeypatch):
    """The probe thread must survive the wizard it was probing for (#119).

    A wizard can be closed and destroyed while cuInit still blocks, so the
    thread must never touch a Qt object on its way out — `_probe_hardware`'s
    docstring says why. This drives that exact interleaving: destruction
    completes while the probe is still blocked. It pins the interleaving as
    exercised, not as impossible — the defect it guards against was a
    probabilistic heap race no assertion can observe directly.
    """
    from PySide6.QtCore import QEvent

    release = threading.Event()
    real_detect = hardware.detect_cached

    def detect_when_released():
        release.wait(timeout=10)
        return real_detect()

    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)
    monkeypatch.setattr(hardware, "detect_cached", detect_when_released)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      devices=["Rode NT-USB Mini"])
    probe = win._probe_thread
    win._completed = True
    win.close()
    win.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # C++ side gone
    release.set()
    probe.join(timeout=10)
    assert not probe.is_alive()


def test_the_probe_does_not_start_until_the_wizard_has_built_its_widgets(
        qt_app, tmp_path, monkeypatch):
    """The probe thread and widget construction must not overlap (#119).

    They deadlock. Building a widget takes Qt's signal/slot connection mutex
    while the builder holds the GIL; a garbage collection on the probe thread
    destroys some other QObject, which takes that same mutex and *then* asks
    shiboken for the GIL. Neither side can finish. Observed as a hung test
    run with both threads idle, main in `QLabel::QLabel` →
    `QObject::connectImpl` → `QBasicMutex::lockInternal`, the probe in
    `QObject::disconnect` → `PyGILState_Ensure`.

    Constructing the wizard used to guarantee that overlap: it started the
    thread and then built a page. Rendering first does not make the collision
    impossible — any thread can trigger a collection at any time — but it
    stops the wizard from arranging one every time it opens.
    """
    rendered_with = []
    original = SetupWizard._render

    def spy(self):
        rendered_with.append(getattr(self, "_probe_thread", None))
        return original(self)

    monkeypatch.setattr(SetupWizard, "_render", spy)
    win = SetupWizard(ConfigStore(tmp_path / "config.json"),
                      devices=["Rode NT-USB Mini"])
    try:
        assert rendered_with, "the wizard never rendered"
        assert rendered_with[0] is None, (
            "the wizard built its first page with a probe thread already running")
    finally:
        win._completed = True
        win.close()
        win.deleteLater()


# ---- the download: watched, and stoppable (#114, #115) ---------------------


def _at_the_model_page(wizard):
    while wizard.page != MODEL:
        wizard.advance()
    return wizard


def shown_text(wizard):
    """Every label the current page is showing, as one string."""
    from PySide6.QtWidgets import QLabel

    return " ".join(lbl.text() for lbl in wizard.findChildren(QLabel)
                    if lbl.isVisibleTo(wizard))


def test_a_running_download_says_how_far_it_has_got(wizard):
    """Anything from 78 MB to 3.1 GB behind the word "Downloading…" and nothing
    else was the whole of #115."""
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard.report_progress(Progress(130_000_000, 792_723_456))

    assert wizard.download_status.text() == "Downloading… 130 MB of 793 MB"
    assert wizard.download_bar.value() == 16
    assert wizard.download_bar.isVisibleTo(wizard) is True


def test_there_is_no_bar_before_there_is_anything_to_report(wizard):
    _at_the_model_page(wizard)
    assert wizard.download_bar.isVisibleTo(wizard) is False


def test_cancel_says_it_is_cancelling_rather_than_that_it_cancelled(wizard):
    """The button used to emit a signal nobody consumed, write "Download
    cancelled." and let the download carry on (#114). Reporting success it
    cannot deliver is the defect; the claim now waits for the app."""
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard._start_download()

    assert "Cancelling" in wizard.download_status.text()
    assert wizard.download_status.text() != "Download cancelled."
    # The fetch is still winding down, so a stray Escape still must not land.
    assert wizard.state.can_cancel() is False


def test_asking_twice_only_cancels_once(wizard):
    cancelled = []
    wizard.cancel_requested.connect(lambda: cancelled.append(1))
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard._start_download()
    wizard._start_download()

    assert cancelled == [1]


def test_a_confirmed_cancel_puts_the_page_back_where_it_started(wizard):
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard._start_download()
    wizard.mark_download_cancelled()

    assert wizard.download_button.text() == "Download model"
    assert wizard.download_button.isEnabled() is True
    assert wizard.download_status.text() == "Download cancelled."
    assert wizard.download_bar.isVisibleTo(wizard) is False
    assert wizard.state.can_cancel() is True


def test_a_cancelled_download_does_not_unlock_the_page(wizard):
    """Next still gates on a model being on disk — stopping the download is
    not the same as having one."""
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard.mark_download_cancelled()

    assert wizard.state.can_advance(MODEL) is False
    assert wizard.next_button.isEnabled() is False


def test_a_download_survives_walking_off_the_page_and_back(wizard):
    """Every page is rebuilt on every visit, so the model page has to read the
    download off the state rather than remember its own widgets."""
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard.report_progress(Progress(1, 4))
    wizard.back()
    _at_the_model_page(wizard)

    assert wizard.download_button.text() == "Cancel download"
    assert wizard.download_bar.isVisibleTo(wizard) is True
    assert wizard.download_bar.value() == 25


def test_the_model_page_offers_a_way_past_it_when_there_is_no_network(wizard):
    """The download is the one step that needs the network. Without a Skip an
    offline machine reaches page five and can go neither forward nor back out
    except by pressing Escape."""
    _at_the_model_page(wizard)

    assert wizard.next_button.isEnabled() is False       # still gated
    assert wizard.skip_button.isVisibleTo(wizard) is True
    assert shown_text(wizard).count("Settings") == 1     # says where to go later


def test_the_skip_goes_away_while_the_download_it_would_abandon_runs(wizard):
    _at_the_model_page(wizard)
    wizard._start_download()

    assert wizard.skip_button.isVisibleTo(wizard) is False
    wizard.mark_download_cancelled()
    assert wizard.skip_button.isVisibleTo(wizard) is True


def test_nobody_with_a_model_is_offered_a_way_out_of_the_page(wizard):
    """A wizard reopened over a working install lands on this page with the
    download already done. There is nothing to be stuck on, so nothing to
    explain."""
    _at_the_model_page(wizard)
    wizard.mark_download_finished(True)

    assert wizard.skip_hint.isVisibleTo(wizard) is False


def test_skipping_the_model_reaches_a_last_page_that_does_not_claim_one(wizard):
    """`config.stt_model` still names the default, so the summary page would
    otherwise report a model that is not on disk — on the one page whose whole
    job is to say what was set up."""
    _at_the_model_page(wizard)
    while wizard.page != DONE:
        wizard.skip()

    text = shown_text(wizard)
    assert "none yet" in text
    assert wizard.store.config.stt_model not in text
    assert wizard.title.text() == "Almost set up"
    assert wizard.state.is_complete(DONE) is False


def test_finishing_a_skipped_setup_reports_it_as_unfinished(wizard):
    """Which is what leaves the tray amber with "Finish setup…" on it — a
    skipped setup must be as recoverable as a cancelled one."""
    completed = []
    wizard.finished_setup.connect(completed.append)
    _at_the_model_page(wizard)
    while wizard.page != DONE:
        wizard.skip()
    wizard.advance()        # Finish

    assert completed == [False]


def test_a_reading_that_arrives_after_the_download_is_ignored(wizard):
    """hub's last chunk can land after the cancel has already been confirmed;
    a bar that reappears after the page went quiet reads as a cancel that
    didn't take."""
    _at_the_model_page(wizard)
    wizard._start_download()
    wizard.mark_download_cancelled()
    wizard.report_progress(Progress(3, 4))

    assert wizard.download_bar.isVisibleTo(wizard) is False
    assert wizard.download_status.text() == "Download cancelled."


def test_the_hotkey_copy_speaks_the_platforms_words(wizard):
    """The try-it page names the actual chord in the platform's words, and
    the done page never leaks the stored token syntax (#166)."""
    from PySide6.QtWidgets import QLabel

    wizard.state.model_downloaded = True
    while wizard.page != HOTKEY:
        wizard.advance()
    texts = " ".join(lbl.text() for lbl in wizard.findChildren(QLabel))
    assert "Ctrl+Win" in texts

    while wizard.page != DONE:
        wizard.advance()
    texts = " ".join(lbl.text() for lbl in wizard.findChildren(QLabel))
    assert "Ctrl+Win" in texts
    assert "<ctrl>" not in texts


def test_the_hotkey_copy_says_cmd_on_darwin(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: True)
    pin_darwin_ui_platform(monkeypatch)
    from PySide6.QtWidgets import QLabel

    win = SetupWizard(ConfigStore(tmp_path / "config.json"))
    win.state.model_downloaded = True
    while win.page != HOTKEY:
        win.advance()
    texts = " ".join(lbl.text() for lbl in win.findChildren(QLabel))
    assert "Ctrl+Cmd" in texts
    assert "Win" not in texts
    win._completed = True
    win.close()
