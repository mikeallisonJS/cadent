"""The Settings surface (spec §5, §8.3).

Instant apply means every assertion here is "click it and look at the file" —
there is no OK to press and no draft to inspect.
"""

import json
from pathlib import Path

import pytest
from conftest import LONG_DEVICE_NAMES
from PySide6.QtCore import Qt

from cadent import a11y, hardware, jsonstore, models, settings, snippets, vocabulary
from cadent.config import AppOverride, Config
from cadent.config_store import ConfigStore
from cadent.downloads import Progress
from cadent.settings_ui import SettingsWindow, speech
from cadent.settings_ui.model_picker import CHIP_ROLE, SUBTITLE_ROLE
from cadent.settings_ui.window import STRUCTURE
from cadent.theme.tokens import tokens


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    """The panes read `platform.current()` for their facts (seed rules, the
    keycode table, labels). These tests describe the pane *logic* over the
    win32 column, so pin it — the darwin column (#144-#148) is exercised by
    building darwin-shaped capabilities explicitly, not by the host OS."""


@pytest.fixture(autouse=True)
def _a_model_is_on_disk(monkeypatch):
    """Pin what the Speech pane's download button reads.

    It asks the same global question the wizard does — "is *a* speech model on
    disk?" — against the app's real models directory, so left alone these tests
    would show or hide a button depending on whether the developer has ever run
    Cadent. The common state is the pinned one; the tests about the button say
    so themselves."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: True)


@pytest.fixture
def paths(tmp_path):
    vocabulary.ensure_example(tmp_path / "vocabulary.json")
    snippets.ensure_example(tmp_path / "snippets.json")
    return tmp_path


# `styled` — the app-wide stylesheet fixture — lives in conftest.py now that
# the overlay's geometry tests need it too.


@pytest.fixture
def window(qt_app, paths):
    store = ConfigStore(paths / "config.json")
    win = SettingsWindow(store, tokens=tokens("dark"),
                         devices=["Rode NT-USB Mini", "Realtek HD Audio"])
    win.ctx.vocab_path = paths / "vocabulary.json"
    win.ctx.snippets_path = paths / "snippets.json"
    win.ctx.config_path = paths / "config.json"
    win.vocabulary.reload()
    yield win
    win.close()
    win.deleteLater()


def written(window):
    return json.loads(window.store.path.read_text(encoding="utf-8"))


def shown(widget):
    """Would this widget be visible once its pane is on screen?

    These tests never show the windows, and only one stacked pane is ever the
    current one — so plain isVisible() reports False for everything and says
    nothing about whether the pane decided to display it. Measured against the
    containing pane instead, which is the decision under test.
    """
    ancestor = widget.parentWidget()
    while (ancestor is not None and ancestor.objectName() != "Pane"
           and ancestor.parentWidget() is not None):
        ancestor = ancestor.parentWidget()
    return widget.isVisibleTo(ancestor or widget.window())


def pane_flow(pane):
    """The pane's own widgets, top to bottom.

    findChildren() answers in parenting order, which is construction order —
    so it says nothing about what the pane put where. The layout is the only
    thing that knows the reading order these tests are about.
    """
    layout = pane.layout()
    items = (layout.itemAt(i) for i in range(layout.count()))
    return [item.widget() for item in items if item.widget() is not None]


def ancestor_named(widget, name):
    """The nearest ancestor with `objectName() == name`, or None.

    The panes express structure through object names — "Card", "Row", "Pane" —
    so "which card did this control end up in?" and "which row is this label
    in?" are the same walk asked about a different name.
    """
    ancestor = widget.parentWidget()
    while ancestor is not None and ancestor.objectName() != name:
        ancestor = ancestor.parentWidget()
    return ancestor


def card_of(widget):
    """The section card a control ended up in."""
    return ancestor_named(widget, "Card")


# ---- structure (§5.1) ------------------------------------------------------

def test_six_panes_under_three_groups(window):
    assert window.stack.count() == 6
    assert [group for group, _ in STRUCTURE] == ["Workspace", "Text", "Privacy"]


def test_group_headings_are_visible_but_cannot_be_landed_on(window):
    """A NoItemFlags heading reports selectable=0 while staying visible and
    enabled, so it reads as a heading (§8.3)."""
    headings = [window.sidebar.item(i) for i in range(window.sidebar.count())
                if i not in window._pane_rows]
    assert [h.text() for h in headings] == ["WORKSPACE", "TEXT", "PRIVACY"]
    for heading in headings:
        assert not (heading.flags() & Qt.ItemFlag.ItemIsSelectable)


def test_the_sidebar_is_one_tab_stop_not_six(window):
    """The alternative — six checkable buttons — costs six tab stops, no
    arrow movement, and a screen reader announcing "button" with no
    position."""
    stops = [w for w in a11y.focusables(window) if w is window.sidebar]
    assert len(stops) == 1
    assert window.sidebar.accessibleName()


def test_the_sidebar_width_is_a_minimum_not_a_fixed_size(window):
    """setFixedWidth(220) clips outright at 150% text scaling (§8.5)."""
    assert window.sidebar.minimumWidth() == int(tokens("dark")["sidebar_min_w"])
    assert window.sidebar.maximumWidth() > window.sidebar.minimumWidth()


def test_selecting_a_pane_shows_it(window):
    window.show_pane("overrides")
    assert window.stack.currentWidget().widget() is window.overrides


def test_there_is_no_ok_or_cancel(window):
    """Closing the window is done."""
    from PySide6.QtWidgets import QDialogButtonBox

    assert window.findChildren(QDialogButtonBox) == []


# ---- the microphone meter --------------------------------------------------
# The same feedback the wizard's microphone page gives (#105), on the pane
# where a device is picked for the rest of the app's life. Picking a
# microphone is the same question in both places, so it gets the same answer.
#
# The lifecycle is not the same, though, and that is the whole difficulty: a
# wizard page is passed through in seconds, where Settings can sit open on a
# second monitor all afternoon. A meter that holds the input stream for that
# long keeps the Windows mic-in-use indicator lit, which is a bad look for an
# app whose pitch is that nothing leaves the machine.


@pytest.fixture
def metered(qt_app, paths, mic):
    store = ConfigStore(paths / "config.json")
    win = SettingsWindow(store, tokens=tokens("dark"),
                         devices=["Rode NT-USB Mini", "Realtek HD Audio"],
                         mic_monitor=mic)
    yield win
    win.close()
    win.deleteLater()


def test_the_general_pane_listens_while_it_is_showing(metered, mic):
    metered.show()
    assert metered.stack.currentWidget().widget() is metered.general
    assert mic.opened == [None]
    assert metered.general.level.level_source() > 0


def test_leaving_the_pane_lets_go_of_the_microphone(metered, mic):
    metered.show()
    metered.show_pane("hotkeys")
    assert mic.stops >= 1
    assert mic.level == 0.0


def test_coming_back_to_the_pane_listens_again(metered, mic):
    metered.show()
    metered.show_pane("hotkeys")
    metered.show_pane("general")
    assert mic.opened == [None, None]


def test_closing_the_window_lets_go_of_the_microphone(metered, mic):
    metered.show()
    metered.close()
    assert mic.stops >= 1


def test_hiding_the_window_lets_go_of_the_microphone(metered, mic):
    """Settings is a long-lived window; hidden is the common resting state."""
    metered.show()
    metered.hide()
    assert mic.stops >= 1


def test_switching_device_moves_the_meter_to_it(metered, mic):
    """The combo is the row above the meter — a meter still listening to the
    old device would answer the wrong question."""
    metered.show()
    metered.general.mic.setCurrentIndex(
        metered.general.mic.findData("Rode NT-USB Mini"))
    assert mic.opened == [None, "Rode NT-USB Mini"]


def test_the_meter_only_repaints_while_the_pane_is_showing(metered):
    metered.show()
    assert metered.general._meter_timer.isActive()
    metered.show_pane("history")
    assert metered.general._meter_timer.isActive() is False


def test_the_pane_builds_without_a_monitor_at_all(window):
    """The default: tests and any caller that does not pass one."""
    window.show()
    assert window.general.level.level_source is None
    assert window.general._meter_timer.isActive() is False


# ---- accessible name coverage (§8.3) --------------------------------------
# The walk itself lives in test_a11y_coverage.py, which runs it over every
# window rather than only the one this file happens to build. What stays here
# is the part that is about *this* surface.


def test_a_text_less_toggle_is_named_by_its_row_label(window):
    # The row label is the platform's autostart copy ("Start at login" on
    # darwin), so assert against the capability, not one OS's literal.
    from cadent import platform

    assert window.general.autostart.accessibleName() == \
        platform.current().capabilities.autostart_label
    assert window.general.show_overlay.accessibleName() == \
        "Show overlay while dictating"


# ---- instant apply (§5.2) --------------------------------------------------

def test_a_live_field_applies_silently_and_immediately(window):
    window.general.autostart.setChecked(True)
    assert written(window)["autostart"] is True
    assert window.store.config.autostart is True


def test_a_heavy_field_applies_on_change_and_names_its_engine(window):
    applied = []
    window.applied.connect(applied.append)
    window.speech.stt.setCurrentIndex(
        window.speech.stt.findData("distil-medium.en"))
    assert written(window)["stt_model"] == "distil-medium.en"
    assert "stt_model" in applied


def test_a_heavy_field_carries_its_restart_hint_before_it_is_touched(window):
    assert "restarts" in window.speech.stt.accessibleDescription()
    assert "~2 s" in window.speech.stt.accessibleDescription()


def test_a_live_field_carries_no_restart_badge(window):
    """Immediate is the default; badging the common case would imply the
    others are somehow more immediate."""
    assert "restarts" not in window.general.autostart.accessibleDescription()


def test_a_hotkey_is_written_on_commit_not_per_keystroke(window):
    window.hotkeys.hotkey.setText("<ctrl>+<alt>")
    assert written(window)["hotkey"] == Config().hotkey      # nothing yet
    window.hotkeys.hotkey.editingFinished.emit()
    assert written(window)["hotkey"] == "<ctrl>+<alt>"


def test_an_invalid_chord_is_flagged_inline_and_never_written(window):
    """An unparseable chord has no meaning to preserve, and writing it would
    leave the user with no way to dictate."""
    window.hotkeys.hotkey.setText("<nonsense>")
    assert shown(window.hotkeys.error)
    window.hotkeys.hotkey.editingFinished.emit()
    assert written(window)["hotkey"] == Config().hotkey


def test_the_repeating_spinbox_coalesces_and_flushes_on_close(window):
    """min_hold_ms is a spinbox *and* a hotkeys engine field, so the coalesce
    gates the engine restart (§7.3)."""
    window.hotkeys.min_hold.setValue(350)
    assert window.store.config.min_hold_ms == 350
    window.store.flush()
    assert written(window)["min_hold_ms"] == 350


# ---- the engine chain: engine -> model, runtime (#72) ----------------------

@pytest.fixture
def gpu_box(machine):
    """A machine DirectML can run on, whatever the developer's box has."""
    machine(dx12=True)


@pytest.fixture
def cpu_box(machine):
    machine(dx12=False)


@pytest.fixture
def speech_pane(qt_app, tmp_path):
    """Build a window over a config with the given values, and tear it down.

    A factory rather than a plain helper because every window these tests
    build has to be destroyed: a leaked widget stays live for the rest of the
    session and app.setStyleSheet() re-polishes it on every later theme test.
    """
    built = []

    def build(**config_values):
        store = ConfigStore(tmp_path / "config.json")
        for key, value in config_values.items():
            store.set(key, value)
        win = SettingsWindow(store, tokens=tokens("dark"))
        built.append(win)
        return win

    yield build
    for win in built:
        win.close()
        win.deleteLater()


def offered(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def pick(combo, value):
    combo.setCurrentIndex(combo.findData(value))


def item_enabled(combo, value):
    return combo.model().item(combo.findData(value)).isEnabled()


def test_one_list_carries_both_engines(speech_pane, gpu_box):
    """Engine is an implementation detail of the model — `distil-small.en`
    could only ever be faster-whisper's — so there is one control, not two."""
    win = speech_pane()
    assert offered(win.speech.stt) == [m.id for m in models.listed_speech_models()]
    assert not hasattr(win.speech, "engine")


def test_picking_a_parakeet_model_writes_the_engine_with_it(speech_pane, gpu_box):
    win = speech_pane()
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    assert win.store.config.stt_engine == "parakeet"
    assert win.store.config.stt_model == "parakeet-tdt-0.6b-v2"


def test_picking_a_parakeet_model_swaps_the_runtime_list_with_it(speech_pane, gpu_box):
    """`directml` is meaningless to ctranslate2, so the runtimes still follow
    the engine even though nobody picks one."""
    win = speech_pane()
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    assert offered(win.speech.runtime) == list(settings.runtime_choices("parakeet"))


def test_a_runtime_the_new_engine_cannot_use_falls_back_to_automatic(speech_pane, gpu_box):
    win = speech_pane(stt_engine="parakeet",
                      stt_model="parakeet-tdt-0.6b-v3", stt_device="directml")
    pick(win.speech.stt, "distil-small.en")
    assert win.store.config.stt_device == "auto"


def test_a_runtime_both_engines_share_survives_the_switch(speech_pane, gpu_box):
    win = speech_pane(stt_device="cuda")
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    assert win.store.config.stt_device == "cuda"


def test_one_pick_restarts_the_speech_engine_exactly_once(speech_pane, gpu_box):
    """Engine and runtime are corollaries of the model write, not three
    separate reloads — and `stt_model` is the field that announces (#111)."""
    win = speech_pane()
    applied = []
    win.applied.connect(applied.append)
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    assert applied == ["stt_model"]


def test_a_same_engine_pick_announces_once_too(speech_pane, gpu_box):
    win = speech_pane()
    applied = []
    win.applied.connect(applied.append)
    pick(win.speech.stt, "distil-large-v3")
    assert applied == ["stt_model"]


def test_the_whole_change_reaches_the_file(speech_pane, gpu_box):
    win = speech_pane()
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    win.store.flush()
    raw = json.loads(win.store.path.read_text(encoding="utf-8"))
    assert raw["stt_engine"] == "parakeet"
    assert raw["stt_model"] == "parakeet-tdt-0.6b-v2"


def test_parakeet_rows_are_shown_but_unpickable_without_a_gpu(speech_pane, cpu_box):
    """Hidden explains nothing; disabled with a reason explains everything —
    and keeps the engine discoverable to anyone who later adds a card (#111)."""
    win = speech_pane()
    for name in ("parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3"):
        assert item_enabled(win.speech.stt, name) is False
        index = win.speech.stt.findData(name)
        assert win.speech.stt.itemData(index, CHIP_ROLE) == models.NEEDS_A_GPU


def test_a_configured_parakeet_stays_pickable_even_if_the_gpu_probe_says_no(speech_pane, cpu_box):
    """The probe is best-effort. It must never strand a user on a setting they
    already have — including on the sibling checkpoint they might prefer."""
    win = speech_pane(stt_engine="parakeet",
                      stt_model="parakeet-tdt-0.6b-v3")
    assert item_enabled(win.speech.stt, "parakeet-tdt-0.6b-v3") is True
    assert item_enabled(win.speech.stt, "parakeet-tdt-0.6b-v2") is True


def test_the_recommended_chip_follows_the_machine(speech_pane, gpu_box, monkeypatch):
    """A hardcoded tier would label Whisper Small "Recommended" on a GPU box
    the suggestion table sends to Parakeet (#111)."""
    from cadent import hardware

    monkeypatch.setattr(hardware, "suggest_for_this_machine",
                        lambda: hardware.Suggestion("parakeet-tdt-0.6b-v2", "why"))
    win = speech_pane()
    chips = {name: win.speech.stt.itemData(win.speech.stt.findData(name), CHIP_ROLE)
             for name in offered(win.speech.stt)}
    assert chips["parakeet-tdt-0.6b-v2"] == models.RECOMMENDED
    assert chips["distil-small.en"] == ""


def test_a_suggestion_the_list_does_not_show_chips_its_listed_neighbour(
        speech_pane, cpu_box, monkeypatch):
    from cadent import hardware

    monkeypatch.setattr(hardware, "suggest_for_this_machine",
                        lambda: hardware.Suggestion("base.en", "low on RAM"))
    win = speech_pane()
    index = win.speech.stt.findData("tiny.en")
    assert win.speech.stt.itemData(index, CHIP_ROLE) == models.RECOMMENDED


def test_picking_parakeet_says_out_loud_that_biasing_is_not_running(speech_pane, gpu_box):
    """§5.4's Vocabulary pane would otherwise imply biasing is active."""
    win = speech_pane()
    assert win.speech.biasing.text() == ""
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    assert "corrected afterwards" in win.speech.biasing.text()


def test_the_vocabulary_pane_stops_claiming_order_is_priority_under_parakeet(speech_pane, gpu_box):
    """"Terms higher in the list are hinted first" is a claim about a
    mechanism Parakeet doesn't have."""
    win = speech_pane()
    assert "hinted to the speech model" in win.vocabulary.order_note.text()
    pick(win.speech.stt, "parakeet-tdt-0.6b-v2")
    assert "order doesn't matter" in win.vocabulary.order_note.text()


def test_a_parakeet_config_opens_with_the_right_vocabulary_note(speech_pane, gpu_box):
    win = speech_pane(stt_engine="parakeet",
                      stt_model="parakeet-tdt-0.6b-v3")
    assert "order doesn't matter" in win.vocabulary.order_note.text()


def test_a_hand_edited_model_joins_the_bottom_of_the_list(speech_pane, gpu_box):
    """It is still what is running, so it belongs in the list — and it says
    where it came from rather than pretending to be a curated rung."""
    win = speech_pane(stt_model="large-v3-turbo")
    assert offered(win.speech.stt)[-1] == "large-v3-turbo"
    assert win.speech.stt.currentData() == "large-v3-turbo"
    index = win.speech.stt.findData("large-v3-turbo")
    assert win.speech.stt.itemData(index, SUBTITLE_ROLE) == models.SET_IN_CONFIG


def test_a_known_but_unlisted_model_still_discloses_its_size(speech_pane, gpu_box):
    win = speech_pane(stt_model="large-v3")
    index = win.speech.stt.findData("large-v3")
    assert "3.1 GB" in win.speech.stt.itemData(index, SUBTITLE_ROLE)


# ---- the runtime, behind Advanced (#111) -----------------------------------

def test_the_runtime_starts_hidden_behind_a_disclosure(speech_pane, gpu_box):
    """The one speech setting whose wrong answer fixes itself: `auto` probes
    and drops a rung."""
    win = speech_pane()
    assert win.speech.advanced_card.isVisibleTo(win.speech) is False
    win.speech.advanced.setChecked(True)
    assert win.speech.advanced_card.isVisibleTo(win.speech) is True
    assert "Hide" in win.speech.advanced.text()


def test_the_runtime_row_writes_the_value_not_its_label(speech_pane, gpu_box):
    win = speech_pane()
    win.speech.advanced.setChecked(True)
    pick(win.speech.runtime, "cpu")
    assert win.store.config.stt_device == "cpu"


def test_both_runtimes_share_the_one_disclosure(speech_pane, gpu_box):
    """Cleanup's runtime is the same kind of setting as speech's — a
    self-correcting `auto` ladder — so it belongs behind the same door rather
    than behind a second one (#116)."""
    win = speech_pane()
    assert offered(win.speech.llm_runtime) == list(settings.cleanup_runtime_choices())
    assert win.speech.llm_runtime.isVisibleTo(win.speech) is False
    win.speech.advanced.setChecked(True)
    assert win.speech.llm_runtime.isVisibleTo(win.speech) is True


def test_the_cleanup_runtime_row_writes_the_value_not_its_label(speech_pane, gpu_box):
    win = speech_pane()
    win.speech.advanced.setChecked(True)
    pick(win.speech.llm_runtime, "cpu")
    assert win.store.config.llm_runtime == "cpu"


# ---- the speech download, watched and stoppable here too (#114, #115) ------

@pytest.fixture
def nothing_on_disk(monkeypatch):
    """The state a skipped wizard leaves: no speech model anywhere."""
    monkeypatch.setattr(hardware, "any_speech_model_downloaded", lambda _d: False)


def test_a_download_started_elsewhere_shows_up_here(speech_pane, gpu_box):
    """Picking a model sets a fetch off several layers down, and until #115 the
    pane that made the change said nothing about it for up to 3.1 GB."""
    win = speech_pane()
    pane = win.speech
    pane.mark_download_started()
    pane.report_progress(Progress(130_000_000, 792_723_456))

    assert pane.download_status.text() == "Downloading… 130 MB of 793 MB"
    assert pane.download_bar.value() == 16
    assert shown(pane.download_bar)


def test_there_is_no_bar_before_there_is_anything_to_report(speech_pane, gpu_box):
    win = speech_pane()
    assert shown(win.speech.download_bar) is False
    assert shown(win.speech.download_status) is False


def test_the_button_becomes_the_cancel_for_the_download_it_started(speech_pane,
                                                                  gpu_box):
    cancelled = []
    win = speech_pane()
    win.model_download_cancel_requested.connect(lambda: cancelled.append(1))
    win.speech.mark_download_started()

    assert win.speech.download_button.text() == speech.CANCEL_DOWNLOAD
    win.speech.download_button.click()
    assert cancelled == [1]


def test_cancel_says_it_is_cancelling_rather_than_that_it_cancelled(speech_pane,
                                                                   gpu_box):
    """The same claim-waits-for-the-app rule the wizard's button follows: only
    the app knows when a fetch has actually unwound (#114)."""
    win = speech_pane()
    win.speech.mark_download_started()
    win.speech.download_button.click()

    assert win.speech.download_status.text() == speech.CANCELLING
    assert win.speech.download_status.text() != speech.CANCELLED
    assert win.speech.download_button.isEnabled() is False


def test_asking_twice_only_cancels_once(speech_pane, gpu_box):
    cancelled = []
    win = speech_pane()
    win.model_download_cancel_requested.connect(lambda: cancelled.append(1))
    win.speech.mark_download_started()
    win.speech._on_download_clicked()
    win.speech._on_download_clicked()

    assert cancelled == [1]


def test_a_reading_that_arrives_after_the_download_is_ignored(speech_pane, gpu_box):
    """hub's last chunk can land after the cancel was confirmed; a bar that
    reappears reads as a cancel that didn't take."""
    win = speech_pane()
    win.speech.mark_download_started()
    win.speech.mark_download_cancelled()
    win.speech.report_progress(Progress(3, 4))

    assert shown(win.speech.download_bar) is False
    assert win.speech.download_status.text() == speech.CANCELLED


def test_a_model_on_disk_needs_no_download_button(speech_pane, gpu_box):
    """Choosing from the list is what downloads one. A second control for the
    same act is a second thing to explain."""
    win = speech_pane()
    assert shown(win.speech.download_button) is False


def test_a_skipped_setup_leaves_a_way_to_get_a_model(speech_pane, gpu_box,
                                                     nothing_on_disk):
    """The wizard's model page can now be skipped, and this is where its
    docstring sends you. Without the button there is nothing to click: the
    combo already sits on the configured model, so re-picking it commits
    nothing and starts nothing."""
    asked = []
    win = speech_pane()
    win.model_download_requested.connect(lambda: asked.append(1))

    assert shown(win.speech.download_button) is True
    assert win.speech.download_button.text() == speech.DOWNLOAD
    win.speech.download_button.click()
    assert asked == [1]


def test_a_failed_download_leaves_a_way_to_try_again(speech_pane, gpu_box):
    win = speech_pane()
    win.speech.mark_download_started()
    win.speech.mark_download_finished(False, "the hub went away")

    assert "the hub went away" in win.speech.download_status.text()
    assert shown(win.speech.download_button) is True
    assert win.speech.download_button.text() == speech.DOWNLOAD


def test_a_cancelled_download_leaves_a_way_to_try_again(speech_pane, gpu_box):
    """Stopping a download is not the same as not wanting the model."""
    win = speech_pane()
    win.speech.mark_download_started()
    win.speech.mark_download_cancelled()

    assert shown(win.speech.download_button) is True


def test_a_finished_download_puts_the_pane_back_where_it_started(speech_pane,
                                                                 gpu_box):
    win = speech_pane()
    win.speech.mark_download_started()
    win.speech.mark_download_finished(True)

    assert win.speech.download_status.text() == speech.READY
    assert shown(win.speech.download_bar) is False
    assert shown(win.speech.download_button) is False


def test_a_new_pick_clears_the_last_attempts_verdict(speech_pane, gpu_box):
    """Otherwise choosing a model that turns out to be cached already leaves
    "Download failed: …" sitting under a model that loaded fine."""
    win = speech_pane()
    win.speech.mark_download_started()
    win.speech.mark_download_finished(False, "the hub went away")
    pick(win.speech.stt, "distil-medium.en")

    assert win.speech.download_status.text() == ""
    assert shown(win.speech.download_status) is False


# ---- the cleanup rungs (#112) ----------------------------------------------

@pytest.fixture
def roomy_box(monkeypatch):
    """A machine every rung fits on, whatever the developer's box has."""
    from cadent import hardware

    monkeypatch.setattr(hardware, "detect_safely",
                        lambda: hardware.Hardware(ram_gb=32.0, physical_cores=16))


@pytest.fixture
def small_box(monkeypatch):
    from cadent import hardware

    monkeypatch.setattr(hardware, "detect_safely",
                        lambda: hardware.Hardware(ram_gb=8.0, physical_cores=4))


def test_four_rungs_and_a_way_to_bring_your_own(speech_pane, gpu_box, roomy_box):
    win = speech_pane()
    assert offered(win.speech.llm) == \
        [m.id for m in models.CLEANUP_MODELS] + [speech.CHOOSE_A_FILE]


def test_the_cleanup_combo_is_never_editable(speech_pane, gpu_box, roomy_box):
    """A paste-a-path combo looks like a dropdown and behaves like a text
    field; the file picker is what replaced it."""
    win = speech_pane()
    assert win.speech.llm.isEditable() is False


def test_a_roomy_machine_is_recommended_the_top_rung(speech_pane, gpu_box, roomy_box):
    win = speech_pane()
    chips = [win.speech.llm.itemData(i, CHIP_ROLE)
             for i in range(win.speech.llm.count())]
    assert chips[:4] == ["", "", "", models.RECOMMENDED]


def test_a_small_machine_is_warned_off_the_heavy_rungs(speech_pane, gpu_box, small_box):
    win = speech_pane()
    chips = [win.speech.llm.itemData(i, CHIP_ROLE)
             for i in range(win.speech.llm.count())]
    assert chips[:4] == ["", models.RECOMMENDED,
                         models.NOT_ENOUGH_MEMORY, models.NOT_ENOUGH_MEMORY]


def test_a_metal_box_is_never_warned_slow_whatever_its_cores(
        speech_pane, gpu_box, monkeypatch):
    """ADR 0003 (#146): the pane hands the Metal fact through, so a four-core
    Apple Silicon box — which the win32 core gate would warn off the heavy
    rungs — wears the top-rung Recommended chip and no warning."""
    from cadent import hardware

    monkeypatch.setattr(hardware, "detect_safely",
                        lambda: hardware.Hardware(ram_gb=32.0, physical_cores=4,
                                                  metal_gpu=True))
    win = speech_pane()
    chips = [win.speech.llm.itemData(i, CHIP_ROLE)
             for i in range(win.speech.llm.count())]
    assert chips[:4] == ["", "", "", models.RECOMMENDED]


def test_picking_a_rung_writes_the_path_it_downloads_to(speech_pane, gpu_box, roomy_box):
    win = speech_pane()
    pick(win.speech.llm, "Llama-3.2-1B-Instruct-Q4_K_M.gguf")
    win.speech.llm.activated.emit(win.speech.llm.currentIndex())
    written_path = win.store.config.llm_model_path
    assert written_path.endswith("Llama-3.2-1B-Instruct-Q4_K_M.gguf")
    assert "llm" in written_path


def test_a_bring_your_own_file_keeps_a_row_of_its_own(speech_pane, gpu_box, roomy_box):
    win = speech_pane(llm_model_path="D:/models/custom-finetune.gguf")
    assert win.speech.llm.currentData() == "D:/models/custom-finetune.gguf"
    assert win.speech.llm.currentText() == "custom-finetune.gguf"


def test_cancelling_the_file_picker_puts_the_selection_back(speech_pane, gpu_box,
                                                            roomy_box, monkeypatch):
    """"Choose a file…" is an action, not a model; leaving it selected would
    show it as the cleanup model in use."""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    win = speech_pane()
    before = win.store.config.llm_model_path
    pick(win.speech.llm, speech.CHOOSE_A_FILE)
    win.speech.llm.activated.emit(win.speech.llm.currentIndex())
    assert win.store.config.llm_model_path == before
    assert win.speech.llm.currentData() == models.CLEANUP_MODELS[-1].id


def test_choosing_a_file_writes_it_and_gives_it_a_row(speech_pane, gpu_box,
                                                      roomy_box, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("D:/mine/tuned.gguf", "")))
    win = speech_pane()
    pick(win.speech.llm, speech.CHOOSE_A_FILE)
    win.speech.llm.activated.emit(win.speech.llm.currentIndex())
    assert win.store.config.llm_model_path == "D:/mine/tuned.gguf"
    assert win.speech.llm.currentData() == "D:/mine/tuned.gguf"

# ---- Appearance (§5.3) -----------------------------------------------------

def test_the_theme_row_says_which_windows_setting_it_follows(window):
    """Windows has two theme settings and Qt reports the *app* one, so
    without the line a light Cadent on a dark taskbar reads as a bug. The
    module pins the win32 column; the darwin one is covered below."""
    assert "Windows app colour mode" in window.general.theme.accessibleDescription()


def test_the_theme_row_names_the_mac_setting_on_the_darwin_column(qt_app, paths,
                                                                  monkeypatch):
    """Copy that names an OS setting must name *this* OS's: a Mac told to
    follow "your Windows app colour mode" is the app claiming a platform it
    isn't on."""
    from conftest import pin_darwin_ui_platform

    pin_darwin_ui_platform(monkeypatch)
    win = SettingsWindow(ConfigStore(paths / "config.json"), tokens=tokens("dark"),
                         devices=[])
    try:
        described = win.general.theme.accessibleDescription()
        assert "Appearance" in described
        assert "Windows" not in described
    finally:
        win.close()
        win.deleteLater()


def test_choosing_a_theme_writes_it_and_asks_the_app_to_apply_it(window):
    requested = []
    window.theme_requested.connect(requested.append)
    window.general.theme.setCurrentIndex(window.general.theme.findData("dark"))
    assert written(window)["theme"] == "dark"
    assert requested == ["dark"]


def test_the_theme_control_stays_enabled_under_a_contrast_theme(qt_app, paths):
    """Qt drops disabled widgets from the tab order, so disabling it would put
    the explanation out of reach of exactly the user who needs it (§1.3)."""
    win = SettingsWindow(ConfigStore(paths / "config.json"),
                         tokens=tokens("dark"), high_contrast=True)
    assert win.general.theme.isEnabled() is True
    assert "contrast theme" in win.general.theme.accessibleDescription()
    assert "contrast theme" in win.general.theme.toolTip()
    win.close()


# ---- a combo never takes the row's label column (#106) ---------------------
# A combo sizing itself to its widest *item*, handed that width by row(). The
# device names come from conftest, where the note on why they have to be real
# lengths lives. §8.5's other half — that the cap survives 225% text scale — is
# asserted in test_a11y_coverage.py, next to the rest of the scaling checks.

LONG_DEVICES = LONG_DEVICE_NAMES


@pytest.fixture
def general_pane(styled, paths):
    # These are geometry assertions, so they run under `styled`: without the
    # app stylesheet the rows are measured in the platform's default font,
    # and on macOS that font is wide enough to wrap the one-line hint.
    built = []

    def build(devices, show=False):
        win = SettingsWindow(ConfigStore(paths / "config.json"),
                             tokens=tokens("dark"), devices=devices)
        built.append(win)
        if show:
            win.resize(940, 680)
            win.show()
            styled.processEvents()
        return win

    yield build
    for win in built:
        win.close()
        win.deleteLater()


def row_labels(widget, name):
    """The `name` labels in the card row a control sits in."""
    from PySide6.QtWidgets import QLabel

    row = ancestor_named(widget, "Row")
    return [w for w in row.findChildren(QLabel) if w.objectName() == name]


def lines(label):
    return round(label.height() / label.fontMetrics().lineSpacing())


def test_a_long_device_name_does_not_widen_the_microphone_combo(general_pane):
    """The cap is the fix, and it holds whatever is in the list."""
    short = general_pane(["Rode NT-USB Mini"]).general.mic
    long = general_pane(LONG_DEVICES).general.mic
    assert long.sizeHint().width() == short.sizeHint().width()


def test_the_label_column_is_the_same_width_whatever_the_devices_are_called(
        general_pane):
    """The property, rather than a fraction someone picked: how much room the
    label column gets must not depend on how long the device names are."""
    short = general_pane(["Rode NT-USB Mini"], show=True).general.mic
    long = general_pane(LONG_DEVICES, show=True).general.mic
    assert (row_labels(long, "RowTitle")[0].width()
            == row_labels(short, "RowTitle")[0].width())


def test_a_long_device_name_leaves_the_restart_hint_on_one_line(general_pane):
    """The hint — *"Capture retargets to the new device on the next
    dictation"* — is the thing telling the user their change is not lost.
    Wrapped down the side of the row it reads as damage, not information."""
    mic = general_pane(LONG_DEVICES, show=True).general.mic
    assert lines(row_labels(mic, "RowHint")[0]) == 1


def test_a_device_name_too_long_to_show_stays_readable_as_a_tooltip(general_pane):
    """A truncated device name is still recognisable where a nine-line label is
    not — but the whole string has to stay reachable somewhere."""
    mic = general_pane(LONG_DEVICES, show=True).general.mic
    mic.setCurrentIndex(mic.findData(LONG_DEVICES[0]))
    assert mic.toolTip() == LONG_DEVICES[0]


def test_a_device_name_that_fits_carries_no_tooltip(general_pane):
    """A tooltip repeating text you can already read is noise."""
    mic = general_pane(["Rode NT-USB Mini"], show=True).general.mic
    mic.setCurrentIndex(mic.findData("Rode NT-USB Mini"))
    assert mic.toolTip() == ""


def test_the_cap_is_on_the_control_not_on_the_names(general_pane):
    """The point of eliding is that the whole string is still one click away,
    so the cap must not reach the model: the names go in untouched and the
    popup can show what the row cannot.

    Asserted on the model rather than on the popup's width — an unshown
    QListView's `sizeHintForColumn` is not measured yet, and how wide Qt opens
    a popup is Qt's business, not this cap's."""
    mic = general_pane(LONG_DEVICES, show=True).general.mic
    assert [mic.itemText(i + 1) for i in range(len(LONG_DEVICES))] == LONG_DEVICES
    assert mic.findData(LONG_DEVICES[0]) > 0


def test_the_cleanup_model_combo_is_capped_the_same_way(speech_pane, gpu_box):
    """#106 is a rule about how a row divides space, not a patch to one row: a
    bring-your-own GGUF filename is every bit as long as a device name, and it
    is listed by that filename (#112)."""
    own_file = "C:/models/a-community-finetune-of-something-Q4_K_M.gguf"
    rungs = speech_pane().speech.llm
    own = speech_pane(llm_model_path=own_file).speech.llm
    assert own.findData(own_file) >= 0
    assert own.sizeHint().width() == rungs.sizeHint().width()


# ---- Setup lives in Settings only (#110) -----------------------------------

def test_general_names_every_group_and_opens_on_setup(window):
    """With the tray item gone this is the only way back into the wizard, so it
    takes the first card — and once Setup holds the bare-first-card slot every
    other group needs its own heading or Basics is left orphaned (#110)."""
    flow = [w.objectName() if w.objectName() != "Caps" else w.text()
            for w in pane_flow(window.general)
            if w.objectName() in ("Caps", "Card")]
    assert flow == ["SETUP", "Card", "BASICS", "Card", "OVERLAY", "Card"]


def test_the_wizard_button_is_in_the_setup_card(window):
    """Named groups are only worth having if the button is under the right
    one — the heading order alone would pass with Setup's card empty."""
    cards = [w for w in pane_flow(window.general) if w.objectName() == "Card"]
    assert card_of(window.general.wizard_button) is cards[0]


# ---- Vocabulary & snippets (§5.4) -----------------------------------------

def test_writing_the_vocabulary_file_is_applying_it(window, paths):
    """Re-read at the start of every dictation, so there is no engine to
    restart and no badge on this pane."""
    jsonstore.upsert_term(paths / "vocabulary.json", "Cadent", ["local flow"])
    window.vocabulary.reload()
    assert window.vocabulary.table.item(0, 0).text() == "Cadent"
    assert window.vocabulary.table.item(0, 1).text() == "local flow"


def test_editing_a_term_writes_only_that_row(window, paths):
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "Kubernetes")
    window.vocabulary.reload()
    window.vocabulary.table.item(0, 1).setText("cooper netties")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["terms"][0] == {"term": "Kubernetes", "soundsLike": ["cooper netties"]}
    assert "_comment" in raw                       # the prose round-trips


def test_a_blank_row_is_never_written(window, paths):
    window.vocabulary._add_term()
    raw = json.loads((paths / "vocabulary.json").read_text(encoding="utf-8"))
    assert raw["terms"] == []


def test_deleting_a_term_offers_undo_with_no_confirm_dialog(window, paths):
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "Kubernetes", ["cooper netties"])
    window.vocabulary.reload()
    window.vocabulary.table.setCurrentCell(0, 0)
    window.vocabulary._remove_term()
    assert json.loads(path.read_text(encoding="utf-8"))["terms"] == []
    assert shown(window.vocabulary.undo)

    window.vocabulary.undo._fire()
    terms, _warning = vocabulary.load(path)
    assert terms == [vocabulary.Term("Kubernetes", ("cooper netties",))]


def test_moving_a_term_rewrites_priority_order(window, paths):
    """Order is the hint budget's priority — pack_hotwords packs in file
    order and drops the overflow."""
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "First")
    jsonstore.upsert_term(path, "Second")
    window.vocabulary.reload()
    window.vocabulary.table.setCurrentCell(1, 0)
    window.vocabulary._move(-1)
    terms, _warning = vocabulary.load(path)
    assert [t.term for t in terms] == ["Second", "First"]


def test_an_unparseable_vocabulary_file_disables_editing(window, paths):
    """So the UI can never overwrite a file it failed to read."""
    (paths / "vocabulary.json").write_text('{"terms": [', encoding="utf-8")
    window.vocabulary.reload()
    assert shown(window.vocabulary.error)
    assert window.vocabulary.table.isEnabled() is False
    assert window.vocabulary.add_term.isEnabled() is False


def test_the_budget_note_is_silent_until_it_actually_bit(window):
    assert shown(window.vocabulary.budget_note) is False
    window.ctx.dropped_hotwords = [f"term{i}" for i in range(12)]
    window.vocabulary.refresh_budget_note()
    assert shown(window.vocabulary.budget_note)
    assert "12 terms" in window.vocabulary.budget_note.text()


def test_the_vocabulary_empty_state_teaches_what_sounds_like_is_for(window):
    """The state every new user meets, and load-bearing by design: §5.4 dropped
    the seeded sample entries on purpose, *"the pane's empty state teaches by
    example instead"* — and then the empty state was never built (#102).

    "Sounds like" is the non-obvious half. A user who does not know it means
    what the recogniser *mishears* has no way to guess it from an empty grid
    and an `Add term` button."""
    assert shown(window.vocabulary.empty)
    assert "mishear" in window.vocabulary.empty.text().lower()


def test_the_snippets_empty_state_teaches_what_a_trigger_is(window):
    """The detail box had its placeholder all along; the list above it — which
    is what a new user looks at first — had nothing."""
    assert shown(window.vocabulary.snippet_empty)
    assert "trigger" in window.vocabulary.snippet_empty.text().lower()


def test_both_empty_states_read_in_the_register_the_pane_next_door_uses(window):
    """§5.5's App overrides line is the model: name what is not there, then
    teach the concept in one more clause. Same shape, so the two panes do not
    read as two products."""
    for empty in (window.vocabulary.empty, window.vocabulary.snippet_empty):
        assert empty.text().startswith("No ")
        assert empty.objectName() == window.overrides.empty.objectName()


def test_the_empty_states_go_away_once_there_is_something_there(window, paths):
    """The file is the source, so a reload is the ordinary way back."""
    jsonstore.upsert_term(paths / "vocabulary.json", "Kubernetes",
                          ["cooper netties"])
    jsonstore.upsert_snippet(paths / "snippets.json", "my sig", "Best,\nMe")
    window.vocabulary.reload()
    assert shown(window.vocabulary.empty) is False
    assert shown(window.vocabulary.snippet_empty) is False


def test_add_term_takes_the_empty_state_off_the_screen(window):
    """`Add term` inserts a row and opens its editor without reloading — so
    the empty state has to be told, or "No terms" sits under the row the user
    is typing into for the rest of the session.

    This is the exact flow #102 describes: a fresh install is two empty boxes
    and an `Add term` button, so the first thing anyone does here is press it.
    """
    window.vocabulary._add_term()
    assert window.vocabulary.table.rowCount() == 1
    assert shown(window.vocabulary.empty) is False
    # The same row count that hides one note reveals the other, and order is
    # the hint budget's priority — a list of one still has a first place.
    assert shown(window.vocabulary.order_note)


def test_add_snippet_does_the_same(window):
    window.vocabulary._add_snippet()
    assert window.vocabulary.triggers.count() == 1
    assert shown(window.vocabulary.snippet_empty) is False


def test_an_unreadable_file_shows_its_error_and_not_an_empty_state(window, paths):
    """"Nothing here yet" and "this file is broken" are different facts, and
    the wrong one invites the user to start typing into a pane whose editing is
    disabled."""
    (paths / "vocabulary.json").write_text('{"terms": [', encoding="utf-8")
    (paths / "snippets.json").write_text("{", encoding="utf-8")
    window.vocabulary.reload()
    assert shown(window.vocabulary.error)
    assert shown(window.vocabulary.empty) is False
    assert shown(window.vocabulary.snippet_error)
    assert shown(window.vocabulary.snippet_empty) is False


def test_a_multi_line_snippet_round_trips_through_the_detail_box(window, paths):
    path = paths / "snippets.json"
    jsonstore.upsert_snippet(path, "my sig", "Best,\nMe")
    window.vocabulary.reload()
    window.vocabulary.triggers.setCurrentRow(0)
    assert window.vocabulary.replacement.toPlainText() == "Best,\nMe"
    window.vocabulary.replacement.setPlainText("Regards,\nMe\nCadent")
    window.vocabulary._commit_snippet()
    table, _warning = snippets.load(path)
    assert table[snippets.normalize("my sig")] == "Regards,\nMe\nCadent"


# ---- the vocabulary pane's file watcher (#119) -----------------------------

def test_the_watcher_is_destroyed_with_the_pane_that_wanted_it(qt_app, tmp_path):
    """A watcher that outlives its pane is a live `ReadDirectoryChangesW`
    thread reporting to nobody — the ownership defect behind #119."""
    from PySide6.QtCore import QEvent
    from shiboken6 import isValid

    win = SettingsWindow(ConfigStore(tmp_path / "config.json"), tokens=tokens("dark"))
    watcher = win.vocabulary.watcher
    assert isValid(watcher)
    win.close()
    win.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(watcher), (
        "the pane is gone and its watcher is still watching")


def test_no_pane_reads_or_watches_the_developers_own_files(qt_app, tmp_path):
    """A pane built without explicit paths must not reach the developer's own
    `%LOCALAPPDATA%/Cadent` files — reading them makes an assertion depend on
    whose machine it runs on, and watching them is what #119 burned.

    The real directory is recomputed here rather than read from `config`, so
    the fixture that redirects it cannot make this test agree with itself.
    """
    from platformdirs import user_data_dir

    real = [Path(user_data_dir("Cadent", appauthor=False))]
    win = SettingsWindow(ConfigStore(tmp_path / "config.json"), tokens=tokens("dark"))
    try:
        for path in (win.ctx.vocab_path, win.ctx.snippets_path, win.ctx.config_path):
            assert not [d for d in real if d in path.parents], path
        watched = win.vocabulary.watcher.files() + win.vocabulary.watcher.directories()
        assert [p for p in watched
                if [d for d in real if d in Path(p).parents]] == []
    finally:
        win.close()
        win.deleteLater()


# ---- App overrides (§5.5) --------------------------------------------------

def test_the_pane_lists_every_shipped_rule(window):
    """The 10 rows first run writes are not noise — they are the answer to
    "why does dictation paste into my terminal?"."""
    listed = {window.overrides.table.item(i, 0).text()
              for i in range(window.overrides.table.rowCount())}
    assert "notepad.exe" in listed
    assert "windowsterminal.exe" in listed


def test_rows_sort_alphabetically_in_the_view_and_file_order_stays_put(window):
    shown = [window.overrides.table.item(i, 0).text()
             for i in range(window.overrides.table.rowCount())]
    assert shown == sorted(shown)
    on_disk = [o["process"] for o in written(window)["app_overrides"]]
    assert on_disk != shown or len(on_disk) <= 1


def test_strategies_read_in_plain_language(window):
    """Never the raw enum — and editable in the row, where you are looking
    when you decide a rule is wrong (§5.5)."""
    strategies = {window.overrides.table.cellWidget(i, 1).currentText()
                  for i in range(window.overrides.table.rowCount())}
    assert strategies <= {"Type it", "Paste it", "Don't insert"}


def test_the_pane_mutates_the_injector_s_list_in_place(window):
    """Rebinding leaves the injector holding the old list, every edit becomes
    a no-op until restart, and the symptom is indistinguishable from a typo'd
    executable name."""
    live = window.store.config.app_overrides
    window.overrides.add_combo.setCurrentText("slack.exe")
    window.overrides._add()
    assert window.store.config.app_overrides is live
    assert any(o.process == "slack.exe" for o in live)
    assert "slack.exe" in [o["process"] for o in written(window)["app_overrides"]]


def test_a_duplicate_add_selects_the_existing_row(window):
    """Instead of creating a second, dead one."""
    before = window.overrides.table.rowCount()
    window.overrides.add_combo.setCurrentText("notepad.exe")
    window.overrides._add()
    assert window.overrides.table.rowCount() == before
    assert window.overrides._current().process == "notepad.exe"


def test_deleting_a_shipped_rule_explains_why_it_existed(window):
    """Most shipped rules cannot be re-learned: Notepad's failure is
    scrambled-yet-successful input that reports fine."""
    window.overrides.select("notepad.exe")
    window.overrides._remove()
    assert "scrambles typed text" in window.overrides.undo.message.text()


def test_deleting_a_learned_rule_says_it_may_come_back(window):
    window.store.config.app_overrides.append(
        AppOverride(process="slack.exe", strategy="clipboard", learned=True))
    window.overrides.reload()
    window.overrides.select("slack.exe")
    window.overrides._remove()
    assert "may re-learn it" in window.overrides.undo.message.text()


def test_restore_built_in_rules_re_adds_only_what_is_missing(window):
    """Undo expires on the next edit, so there has to be a durable way back."""
    window.overrides.select("notepad.exe")
    window.overrides._remove()
    window.overrides.select("mstsc.exe")
    window.overrides._remove()
    before = window.overrides.table.rowCount()
    window.overrides._restore_defaults()
    assert window.overrides.table.rowCount() == before + 2
    assert len({o.process for o in window.store.config.app_overrides}) == \
        len(window.store.config.app_overrides)


def test_a_learned_row_keeps_its_chip_when_edited(window):
    """It is provenance — how the row was born — not a claim about its
    contents."""
    window.store.config.app_overrides.append(
        AppOverride(process="slack.exe", strategy="clipboard", learned=True))
    window.overrides.reload()
    window.overrides.select("slack.exe")
    window.overrides.settle_delay.setValue(400)
    window.overrides._commit_knobs()
    row = next(i for i in range(window.overrides.table.rowCount())
               if window.overrides.table.item(i, 0).text() == "slack.exe")
    assert window.overrides.table.item(row, 2).text() == "learned"
    assert "typing failed" in window.overrides.table.item(row, 2).toolTip()


def test_the_knobs_disclose_by_strategy(window):
    window.overrides.select("notepad.exe")
    assert shown(window.overrides.paste_rows)
    assert shown(window.overrides.type_rows) is False
    window.overrides.strategy.setCurrentIndex(
        window.overrides.strategy.findData("type"))
    window.overrides._commit_strategy()
    assert shown(window.overrides.type_rows)


def test_switching_strategy_preserves_the_other_strategy_s_values(window):
    """So a hand-tuned row survives a round trip through the dropdown."""
    window.overrides.select("notepad.exe")
    window.overrides.strategy.setCurrentIndex(
        window.overrides.strategy.findData("type"))
    window.overrides._commit_strategy()
    window.overrides.select("notepad.exe")
    window.overrides.strategy.setCurrentIndex(
        window.overrides.strategy.findData("clipboard"))
    window.overrides._commit_strategy()
    override = next(o for o in window.store.config.app_overrides
                    if o.process == "notepad.exe")
    assert override.settle_delay_ms == 500


def test_notepads_mystery_number_carries_its_explanation(window):
    window.overrides.select("notepad.exe")
    assert shown(window.overrides.settle_note)
    assert "150 ms" in window.overrides.settle_note.text()


def test_an_unrecognised_paste_chord_warns_inline_and_is_written_anyway(window):
    """_parse_paste_chord silently drops unknown parts and falls back to
    ctrl+v, so `cmd+v` yields a working-looking row that pastes wrong."""
    window.overrides.select("notepad.exe")
    window.overrides.paste_chord.setText("cmd+v")
    window.overrides._commit_knobs()
    assert shown(window.overrides.chord_warning)
    assert "cmd" in window.overrides.chord_warning.text()
    override = next(o for o in window.store.config.app_overrides
                    if o.process == "notepad.exe")
    assert override.paste_chord == "cmd+v"


def test_the_empty_state_teaches_auto_learn(window):
    for override in list(window.store.config.app_overrides):
        window.store.config.app_overrides.remove(override)
    window.overrides.reload()
    assert shown(window.overrides.empty)
    assert "one automatically if typing fails" in window.overrides.empty.text()


def test_no_row_widget_paints_over_the_column_beside_it(styled, paths):
    """Qt widens an editor narrower than its own minimum size hint *rightward*,
    so a 100px Strategy column did not clip the combo — it moved it, over the
    next column (#101). The render was the only thing that showed this, which
    is why an assertion about geometry earns its keep here."""
    win = _overrides_window(styled, paths)
    table = win.overrides.table
    for column in range(table.columnCount()):
        edge = table.columnViewportPosition(column) + table.columnWidth(column)
        for i in range(table.rowCount()):
            widget = table.cellWidget(i, column)
            if widget is not None:
                assert widget.geometry().right() <= edge, (i, column, widget.geometry())
    win.close()


def test_the_learned_chip_is_not_covered_by_the_strategy_combo(styled, paths):
    """§5.5 makes the chip load-bearing: it is the only durable explanation of
    auto-learn anywhere in the app, since the toast is transient and fires
    once. Covered, it explains nothing — and its tooltip is unreachable
    besides, because the combo owns the mouse over that area."""
    win = _overrides_window(styled, paths, learned="slack.exe")
    table = win.overrides.table
    i = next(r for r in range(table.rowCount())
             if table.item(r, 0).text() == "slack.exe")
    chip = table.item(i, 2)
    assert chip.text() == "learned"
    assert chip.toolTip()
    assert table.visualItemRect(chip).width() > 0
    assert not table.cellWidget(i, 1).geometry().intersects(table.visualItemRect(chip))
    win.close()


def _overrides_window(app, paths, learned: str = ""):
    """The pane shown at the size #101 measured it at, laid out for real.

    Geometry is the whole subject here, and a table that was never shown has
    none: Qt places cell widgets from `updateEditorGeometries`, which runs on
    layout.
    """
    store = ConfigStore(paths / "config.json")
    if learned:
        store.config.app_overrides.append(
            AppOverride(process=learned, strategy="clipboard", learned=True))
    # The store is mutated before the window is built, so the pane's own reload
    # already sees the row. A second one would leave the cell widgets it
    # replaced alive at their old geometry — setCellWidget only `deleteLater`s
    # them, and nothing drains that without an event loop.
    win = SettingsWindow(store, tokens=tokens("dark"))
    win.resize(940, 680)
    win.show_pane("overrides")
    win.show()
    app.processEvents()
    return win


def test_no_drag_handle_is_offered_because_order_is_inert(window):
    """resolve_override is first-match-wins and duplicates are prevented, so
    reordering would be theater."""
    assert not hasattr(window.overrides, "move_up")


# ---- History (§5.6) --------------------------------------------------------

def test_retention_is_a_dropdown_of_plausible_choices(window):
    captions = [window.history.retention.itemText(i)
                for i in range(window.history.retention.count())]
    assert captions[:2] == ["Keep forever", "7 days"]


def test_choosing_a_retention_writes_it(window):
    index = window.history.retention.findData(30)
    window.history.retention.setCurrentIndex(index)
    assert written(window)["history_retention_days"] == 30


def test_a_hand_edited_retention_is_shown_rather_than_snapped(qt_app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"history_retention_days": 137}), encoding="utf-8")
    win = SettingsWindow(ConfigStore(path), tokens=tokens("dark"))
    assert win.history.retention.currentData() == 137
    assert win.history.retention.currentText() == "137 days"
    win.close()

# ---- config.json states (§7.2, §7.4, §7.5) --------------------------------

def test_an_unreadable_config_shows_a_persistent_banner(qt_app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"hotkey":', encoding="utf-8")
    win = SettingsWindow(ConfigStore(path), tokens=tokens("dark"))
    assert shown(win.banner)
    assert "won't survive a restart" in win.banner.message.text()
    win.close()


def test_backing_up_and_starting_fresh_clears_the_banner(qt_app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"hotkey":', encoding="utf-8")
    win = SettingsWindow(ConfigStore(path), tokens=tokens("dark"))
    win._start_fresh()
    assert shown(win.banner) is False
    assert (tmp_path / "config.json.broken").exists()
    win.close()


def test_an_external_edit_is_shown_quietly_with_no_reload_button(qt_app, tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    win = SettingsWindow(store, tokens=tokens("dark"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["stt_model"] = "large-v3"
    path.write_text(json.dumps(raw), encoding="utf-8")
    win.refresh_notices()
    assert shown(win.divergence)
    assert "will apply next time you start" in win.divergence.message.text()
    from PySide6.QtWidgets import QPushButton

    assert not any(b.text() == "Reload" for b in win.divergence.findChildren(QPushButton))
    win.close()


def test_a_sanitized_field_is_reported_in_the_pane(qt_app, tmp_path):
    """Under delta writes nothing ever writes the correction back, so the bad
    value would be silently overridden every start."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_mode": "hodl"}), encoding="utf-8")
    win = SettingsWindow(ConfigStore(path), tokens=tokens("dark"))
    assert shown(win.divergence)
    assert "isn't valid" in win.divergence.message.text()
    win.close()


def test_closing_flushes_anything_pending(window):
    window.hotkeys.min_hold.setValue(275)
    window.close()
    assert written(window)["min_hold_ms"] == 275


# ---- table keyboard shortcuts (§8.3) ---------------------------------------

def test_delete_removes_the_selected_row(window, paths):
    """Stock table semantics plus Delete: the Remove button is not the only
    way out."""
    from PySide6.QtGui import QKeySequence, QShortcut

    jsonstore.upsert_term(paths / "vocabulary.json", "Kubernetes")
    window.vocabulary.reload()
    window.vocabulary.table.setCurrentCell(0, 0)
    shortcuts = [s for s in window.vocabulary.table.findChildren(QShortcut)
                 if s.key() == QKeySequence(Qt.Key.Key_Delete)]
    assert shortcuts
    shortcuts[0].activated.emit()
    assert json.loads((paths / "vocabulary.json").read_text(
        encoding="utf-8"))["terms"] == []


def test_ctrl_z_is_the_real_mechanism_the_strip_makes_visible(window, paths):
    """A strip that disappears on a timer is unusable if reaching it costs
    several Tab presses."""
    from PySide6.QtGui import QKeySequence, QShortcut

    jsonstore.upsert_term(paths / "vocabulary.json", "Kubernetes")
    window.vocabulary.reload()
    window.vocabulary.table.setCurrentCell(0, 0)
    window.vocabulary._remove_term()

    undo = [s for s in window.vocabulary.findChildren(QShortcut)
            if s.key() == QKeySequence(QKeySequence.StandardKey.Undo)]
    assert undo
    undo[0].activated.emit()
    terms, _warning = vocabulary.load(paths / "vocabulary.json")
    assert [t.term for t in terms] == ["Kubernetes"]


def test_the_undo_strip_never_takes_focus(window, paths):
    jsonstore.upsert_term(paths / "vocabulary.json", "Kubernetes")
    window.vocabulary.reload()
    window.vocabulary.table.setCurrentCell(0, 0)
    window.vocabulary._remove_term()
    assert window.vocabulary.undo.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_the_overrides_table_gets_the_same_two_shortcuts(window):
    from PySide6.QtGui import QKeySequence, QShortcut

    keys = {s.key() for s in window.overrides.findChildren(QShortcut)}
    assert QKeySequence(Qt.Key.Key_Delete) in keys
    assert QKeySequence(QKeySequence.StandardKey.Undo) in keys


# ---- the vocabulary view vs the file (§5.4) --------------------------------

def test_a_filter_narrows_the_view_without_touching_order(window, paths):
    """Order is the hint budget's priority, so filtering must never rewrite
    it."""
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "Kubernetes")
    jsonstore.upsert_term(path, "Postgres")
    window.vocabulary.reload()
    before = path.read_text(encoding="utf-8")

    window.vocabulary.filter.setText("post")
    assert window.vocabulary.table.isRowHidden(0) is True
    assert window.vocabulary.table.isRowHidden(1) is False
    assert path.read_text(encoding="utf-8") == before

    window.vocabulary.filter.setText("")
    assert window.vocabulary.table.isRowHidden(0) is False


def test_a_filter_matches_aliases_too(window, paths):
    jsonstore.upsert_term(paths / "vocabulary.json", "Kubernetes",
                          ["cooper netties"])
    window.vocabulary.reload()
    window.vocabulary.filter.setText("cooper")
    assert window.vocabulary.table.isRowHidden(0) is False


def test_header_click_sorts_the_view_only(window, paths):
    """Tidying can never silently demote terms out of the hint budget."""
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "Zeta")
    jsonstore.upsert_term(path, "Alpha")
    window.vocabulary.reload()
    assert [t.term for t in vocabulary.load(path)[0]] == ["Zeta", "Alpha"]

    window.vocabulary._on_sorted(0)
    assert window.vocabulary.table.item(0, 0).text() == "Alpha"
    # The file is untouched, and reordering is off while the view is sorted.
    assert [t.term for t in vocabulary.load(path)[0]] == ["Zeta", "Alpha"]
    assert window.vocabulary.move_up.isEnabled() is False
    assert "unchanged" in window.vocabulary.order_note.text()


def test_reloading_restores_file_order_and_re_enables_reordering(window, paths):
    jsonstore.upsert_term(paths / "vocabulary.json", "Zeta")
    jsonstore.upsert_term(paths / "vocabulary.json", "Alpha")
    window.vocabulary.reload()
    window.vocabulary._on_sorted(0)
    window.vocabulary.reload()
    assert window.vocabulary.table.item(0, 0).text() == "Zeta"
    assert window.vocabulary.move_up.isEnabled() is True


def test_renaming_a_term_replaces_it_rather_than_adding_a_second(window, paths):
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "Kubernetes", ["cooper netties"])
    window.vocabulary.reload()
    window.vocabulary.table.item(0, 0).setText("K8s")
    terms, _warning = vocabulary.load(path)
    assert [t.term for t in terms] == ["K8s"]
    assert terms[0].sounds_like == ("cooper netties",)


def test_undo_puts_a_term_back_where_it_was(window, paths):
    """Appending it would silently demote it, because order is priority."""
    path = paths / "vocabulary.json"
    for term in ("First", "Second", "Third"):
        jsonstore.upsert_term(path, term)
    window.vocabulary.reload()
    window.vocabulary.table.setCurrentCell(1, 0)
    window.vocabulary._remove_term()
    window.vocabulary.undo.undo()
    assert [t.term for t in vocabulary.load(path)[0]] == \
        ["First", "Second", "Third"]


def test_a_colliding_term_warns_inline_and_is_still_written(window, paths):
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "My Term")
    jsonstore.upsert_term(path, "my term!")
    window.vocabulary.reload()
    assert shown(window.vocabulary.duplicate_note)
    assert "only one will win" in window.vocabulary.duplicate_note.text()
    assert len(vocabulary.load(path)[0]) == 2


def test_the_duplicate_warning_clears_when_the_collision_goes(window, paths):
    path = paths / "vocabulary.json"
    jsonstore.upsert_term(path, "My Term")
    jsonstore.upsert_term(path, "my term!")
    window.vocabulary.reload()
    jsonstore.remove_term(path, "my term!")
    window.vocabulary.reload()
    assert shown(window.vocabulary.duplicate_note) is False


# ---- app overrides: the row itself (§5.5) ---------------------------------

def test_the_strategy_dropdown_in_the_row_writes(window):
    window.overrides.select("notepad.exe")
    row = next(i for i in range(window.overrides.table.rowCount())
               if window.overrides.table.item(i, 0).text() == "notepad.exe")
    combo = window.overrides.table.cellWidget(row, 1)
    combo.setCurrentIndex(combo.findData("type"))
    combo.activated.emit(combo.currentIndex())
    override = next(o for o in window.store.config.app_overrides
                    if o.process == "notepad.exe")
    assert override.strategy == "type"


def test_re_picking_the_same_strategy_writes_nothing(window, paths):
    """`activated` fires even when the user re-picks what was selected, and
    that must not rewrite a hand-authored `clipboard-no-restore`."""
    override = next(o for o in window.store.config.app_overrides
                    if o.process == "notepad.exe")
    override.strategy = "clipboard-no-restore"
    window.overrides.reload()
    window.overrides.select("notepad.exe")
    window.overrides.strategy.setCurrentIndex(
        window.overrides.strategy.findData("clipboard"))
    window.overrides._commit_strategy()
    assert override.strategy == "clipboard-no-restore"


def test_a_duplicate_add_flashes_the_row_it_found(window):
    window.overrides.add_combo.setCurrentText("notepad.exe")
    window.overrides._add()
    row = next(i for i in range(window.overrides.table.rowCount())
               if window.overrides.table.item(i, 0).text() == "notepad.exe")
    assert window.overrides.table.currentRow() == row
    assert window.overrides.table.item(row, 0).background().color().alpha() > 0


# ---- notices land in the pane that owns the field (§7.4) ------------------

def test_a_divergence_is_shown_in_the_pane_that_owns_the_field(qt_app, tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    win = SettingsWindow(store, tokens=tokens("dark"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["stt_model"] = "large-v3"
    path.write_text(json.dumps(raw), encoding="utf-8")
    win.refresh_notices()
    assert "stt_model" in win.speech.notice.message.text()
    assert win.general.notice.message.text() == ""
    win.close()


def test_a_sanitize_report_lands_in_its_own_pane(qt_app, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_mode": "hodl"}), encoding="utf-8")
    win = SettingsWindow(ConfigStore(path), tokens=tokens("dark"))
    assert "hotkey_mode" in win.hotkeys.notice.message.text()
    assert win.speech.notice.message.text() == ""
    win.close()

# ---- Appearance under a contrast theme (§1.3) -----------------------------

def test_the_contrast_reason_is_carried_visibly_not_just_as_a_tooltip(qt_app,
                                                                     paths):
    win = SettingsWindow(ConfigStore(paths / "config.json"),
                         tokens=tokens("dark"), high_contrast=True)
    assert win.general.contrast_reason.isVisibleTo(win.general)
    assert "contrast theme" in win.general.contrast_reason.text()
    win.close()


def test_the_reason_is_hidden_when_there_is_no_contrast_theme(window):
    assert shown(window.general.contrast_reason) is False


# ---- the nav rail's shape (§5.1, the winning design direction) ------------

def test_nav_rows_have_the_design_s_vertical_rhythm(styled, paths):
    """The rail is the surface the design was chosen on: ~38px rows, not a
    bare line of text each.

    Guarded because the way this broke was *silent*: a fresh
    QListWidgetItem's sizeHint is QSize(-1, -1) — invalid — so building on it
    yields QSize(-1, h), which Qt discards without complaint. Row height comes
    from the stylesheet's padding now.
    """
    win = SettingsWindow(ConfigStore(paths / "config.json"), tokens=tokens("dark"))
    win.show()
    styled.processEvents()
    heights = [win.sidebar.visualItemRect(win.sidebar.item(row)).height()
               for row in win._pane_rows]
    assert all(32 <= h <= 44 for h in heights), heights
    win.close()


def test_group_headings_carry_air_above_the_groups_after_the_first(styled, paths):
    win = SettingsWindow(ConfigStore(paths / "config.json"), tokens=tokens("dark"))
    win.show()
    styled.processEvents()
    headings = [win.sidebar.item(i) for i in range(win.sidebar.count())
                if i not in win._pane_rows]
    first, *rest = [win.sidebar.visualItemRect(h).height() for h in headings]
    assert all(h > first for h in rest), [first, *rest]
    win.close()


def test_every_heading_size_hint_is_valid(qt_app, paths):
    """QSize(-1, h) is silently discarded; QSize(0, h) is honoured."""
    win = SettingsWindow(ConfigStore(paths / "config.json"), tokens=tokens("dark"))
    for i in range(win.sidebar.count()):
        if i not in win._pane_rows:
            assert win.sidebar.item(i).sizeHint().isValid()
    win.close()


def test_the_rail_is_inset_from_the_window_edge(styled, paths):
    """The selected pill is a rounded pill, so it cannot touch the border."""
    win = SettingsWindow(ConfigStore(paths / "config.json"), tokens=tokens("dark"))
    win.show()
    styled.processEvents()
    assert win.sidebar.viewport().x() >= int(tokens("dark")["sp_3"])
    win.close()


def test_each_pane_is_titled_with_its_own_name_not_its_group(window):
    """The sidebar already says which group you are in; repeating it in the
    page reads as the nav rail leaking into the content."""
    from PySide6.QtWidgets import QLabel

    for attribute, expected in (("general", "General"), ("hotkeys", "Hotkeys"),
                                ("speech", "Speech & cleanup"),
                                ("vocabulary", "Vocabulary & snippets"),
                                ("overrides", "App overrides"),
                                ("history", "History")):
        pane = getattr(window, attribute)
        titles = [w.text() for w in pane.findChildren(QLabel)
                  if w.objectName() == "PageTitle"]
        assert titles == [expected], (attribute, titles)


def test_the_sidebar_grows_past_its_minimum_for_the_longest_pane_name(window):
    """A minimum, not a fixed width — which is also what keeps 150% text
    scaling from clipping it (§8.5)."""
    assert window.sidebar.sizeHint().width() >= \
        int(tokens("dark")["sidebar_min_w"])


# ---- the darwin column (spec §7, #148) --------------------------------------
# Built by pinning darwin-shaped capabilities, never by the host OS: the
# autouse win32 pin above is overridden per-test.

from conftest import pin_darwin_ui_platform  # noqa: E402


def darwin_window(paths, monkeypatch, **facts):
    plat = pin_darwin_ui_platform(monkeypatch, **facts)
    win = SettingsWindow(ConfigStore(paths / "config.json"),
                         tokens=tokens("dark"), devices=[])
    return win, plat


def test_the_accessibility_banner_shows_while_the_grant_is_missing(
        qt_app, paths, monkeypatch):
    win, _plat = darwin_window(paths, monkeypatch, granted=False)
    assert win.permission_banner.isVisibleTo(win) is True
    win.close()


def test_no_accessibility_banner_when_granted_or_off_darwin(
        qt_app, paths, monkeypatch, window):
    darwin, _plat = darwin_window(paths, monkeypatch, granted=True)
    assert darwin.permission_banner.isVisibleTo(darwin) is False
    darwin.close()
    # The win32 window (the `window` fixture) never shows one at all.
    assert window.permission_banner.isVisibleTo(window) is False


def test_the_banner_clears_the_moment_the_grant_lands(qt_app, paths, monkeypatch):
    win, plat = darwin_window(paths, monkeypatch, granted=False)
    plat.focused_app.granted = True
    win._refresh_permission()
    assert win.permission_banner.isVisibleTo(win) is False
    win.close()


def test_the_banner_deep_links_to_system_settings(qt_app, paths, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    win, plat = darwin_window(paths, monkeypatch, granted=False)
    button = win.permission_banner.findChild(QPushButton)
    button.click()
    assert plat.desktop.permission_settings_opens == 1
    win.close()


def test_the_speech_runtime_combo_is_hidden_on_darwin(qt_app, paths, monkeypatch):
    """One choice offered twice is no choice (ADR 0003): the row never joins
    the advanced card. Cleanup keeps its combo — auto and cpu genuinely
    differ there."""
    win, _plat = darwin_window(paths, monkeypatch)
    assert win.speech.runtime.parentWidget() is None
    assert win.speech.llm_runtime.parentWidget() is not None
    win.close()


def test_no_model_row_is_disabled_on_darwin(qt_app, paths, monkeypatch):
    """`gpu_only_engines` is empty, so "Needs a graphics card" never renders
    and nothing is greyed out — Parakeet runs on the CPU there (#146)."""
    win, _plat = darwin_window(paths, monkeypatch)
    combo = win.speech.stt
    for i in range(combo.count()):
        assert combo.model().item(i).isEnabled()
        assert combo.itemData(i, CHIP_ROLE) != models.NEEDS_A_GPU
    win.close()


def test_the_autostart_row_reads_start_at_login_on_darwin(
        qt_app, paths, monkeypatch):
    from PySide6.QtWidgets import QLabel

    win, _plat = darwin_window(paths, monkeypatch)
    texts = [w.text() for w in win.general.findChildren(QLabel)]
    assert "Start at login" in texts
    assert "Start with Windows" not in texts
    win.close()


def test_the_darwin_add_affordance_lists_running_apps(qt_app, paths, monkeypatch):
    """§5.2: a picker of running regular-activation-policy apps, rendered
    "Display Name — bundle.id", storing the id."""
    win, _plat = darwin_window(
        paths, monkeypatch,
        running=[("Safari", "com.apple.Safari"),
                 ("Terminal", "com.apple.Terminal")])
    combo = win.overrides.add_combo
    rows = [(combo.itemText(i), combo.itemData(i))
            for i in range(combo.count())]
    assert ("Safari — com.apple.Safari", "com.apple.Safari") in rows
    assert ("Terminal — com.apple.Terminal", "com.apple.Terminal") in rows
    win.close()


def test_picking_an_app_stores_the_bundle_id(qt_app, paths, monkeypatch):
    win, _plat = darwin_window(
        paths, monkeypatch, running=[("Safari", "com.apple.Safari")])
    combo = win.overrides.add_combo
    combo.setCurrentIndex(combo.findData("com.apple.Safari"))
    win.overrides._add()
    assert any(o.process == "com.apple.Safari"
               for o in win.overrides.overrides)
    win.close()


def test_free_text_is_still_accepted_beside_the_picker(qt_app, paths, monkeypatch):
    """An app that isn't running right now must still be addable."""
    win, _plat = darwin_window(
        paths, monkeypatch, running=[("Safari", "com.apple.Safari")])
    win.overrides.add_combo.setCurrentText("com.example.notrunning")
    win.overrides._add()
    assert any(o.process == "com.example.notrunning"
               for o in win.overrides.overrides)
    win.close()


def test_override_rows_render_display_names_when_the_lookup_resolves(
        qt_app, paths, monkeypatch):
    """§5.2: the display name when a live lookup resolves, the raw identity
    otherwise — and the raw identity stays the row's key either way."""
    win, _plat = darwin_window(
        paths, monkeypatch, names={"com.apple.terminal": "Terminal"})
    win.overrides.overrides.extend([
        AppOverride(process="com.apple.Terminal", strategy="clipboard"),
        AppOverride(process="com.gone.app", strategy="clipboard"),
    ])
    win.overrides.reload()
    texts = {win.overrides.table.item(i, 0).text()
             for i in range(win.overrides.table.rowCount())}
    assert "Terminal" in texts
    assert "com.gone.app" in texts
    # Selection still works on the raw identity the row is keyed by.
    win.overrides.select("com.apple.terminal")
    row_now = win.overrides.table.currentRow()
    assert win.overrides.table.item(row_now, 0).text() == "Terminal"
    win.close()


def test_history_rows_render_display_names_too(qt_app, paths, monkeypatch):
    class FakeHistory:
        def search(self, _query):
            return [{"ts": 1_700_000_000, "app_name": "com.apple.Terminal",
                     "raw_text": "hello", "cleaned_text": None}]

    plat = pin_darwin_ui_platform(monkeypatch,
                                  names={"com.apple.terminal": "Terminal"})
    assert plat is not None
    win = SettingsWindow(ConfigStore(paths / "config.json"),
                         tokens=tokens("dark"), devices=[],
                         history=FakeHistory())
    assert win.history.table.item(0, 1).text() == "Terminal"
    win.close()


def test_the_picker_relists_running_apps_on_every_visit(qt_app, paths, monkeypatch):
    """Settings is long-lived: a picker frozen at first open would offer
    "running apps" from hours ago, and the app launched since would only be
    addable as free text — the mistyped-id trap the picker exists to prevent."""
    win, plat = darwin_window(
        paths, monkeypatch, running=[("Safari", "com.apple.Safari")])
    plat.focused_app.running = [("Safari", "com.apple.Safari"),
                                ("Ghostty", "com.mitchellh.ghostty")]
    win.overrides.showEvent(None)
    combo = win.overrides.add_combo
    assert combo.findData("com.mitchellh.ghostty") >= 0
    # Relisted, not re-appended: the row count follows the running set.
    assert combo.count() == 2
    win.close()
