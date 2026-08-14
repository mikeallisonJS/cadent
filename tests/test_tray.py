"""The tray's colour ledger, status header and menu (spec §3)."""

import pytest
from conftest import pin_darwin_ui_platform
from PySide6.QtWidgets import QSystemTrayIcon

from cadent.downloads import Progress
from cadent.tray import ATTENTION, PAUSED, READY, Tray, TrayLedger


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    """The menu reads `platform.current()` for `gpu_pack_available` (M5 §7).
    These tests describe the win32 menu, so pin that column — the darwin menu
    is tested by pinning darwin-shaped capabilities explicitly below."""


@pytest.fixture
def ledger():
    return TrayLedger()


# ---- the icon signals availability only ------------------------------------

def test_a_clean_app_is_ready(ledger):
    assert ledger.state(paused=False) == READY


def test_a_fault_turns_the_icon_amber(ledger):
    ledger.set_fault("model-failed")
    assert ledger.state(paused=False) == ATTENTION


def test_paused_outranks_amber(ledger):
    """While paused nothing is going to run anyway, so a fault is not yet
    actionable, and the icon should reflect the state you deliberately chose."""
    ledger.set_fault("model-failed")
    assert ledger.state(paused=True) == PAUSED


def test_amber_reappears_the_instant_you_resume(ledger):
    ledger.set_fault("model-failed")
    assert ledger.state(paused=True) == PAUSED
    assert ledger.state(paused=False) == ATTENTION


def test_a_fixed_fault_clears_the_colour(ledger):
    ledger.set_fault("cleanup-failed")
    ledger.set_fault("cleanup-failed", False)
    assert ledger.state(paused=False) == READY


def test_an_unknown_ledger_entry_is_refused(ledger):
    with pytest.raises(KeyError):
        ledger.set_fault("vibes")
    with pytest.raises(KeyError):
        ledger.raise_offer("vibes")


# ---- offers clear on view, faults clear on fix (§3.4) ----------------------

def test_an_offer_drops_the_colour_once_the_menu_has_been_opened(ledger):
    """Otherwise an NVIDIA user who never installs the pack sits permanently
    amber, which trains them to ignore the colour."""
    ledger.raise_offer("gpu-pack")
    assert ledger.state(paused=False) == ATTENTION
    ledger.menu_opened()
    assert ledger.state(paused=False) == READY


def test_opening_the_menu_never_clears_a_fault(ledger):
    """Faults clear on fix, and only on fix."""
    ledger.set_fault("model-failed")
    ledger.menu_opened()
    assert ledger.state(paused=False) == ATTENTION


# ---- the status header is what makes a colour-only icon safe (§3.2) --------

def test_the_header_names_the_mode_the_icon_no_longer_carries(ledger):
    """On/off rather than two named modes: it is one toggle (#113)."""
    assert ledger.status_line(paused=False, cleanup=True) == \
        "Cadent — ready · cleanup on"
    assert ledger.status_line(paused=False, cleanup=False) == \
        "Cadent — ready · cleanup off"


def test_the_header_states_the_combination_the_colour_cannot(ledger):
    """The colour carries one state; the header carries the combination."""
    ledger.set_fault("model-failed")
    assert ledger.status_line(paused=True, cleanup=False) == \
        "Cadent — paused · cleanup off · speech model failed"


def test_the_header_lists_every_concern(ledger):
    ledger.set_fault("cleanup-failed")
    ledger.set_fault("config-unreadable")
    line = ledger.status_line(paused=False, cleanup=True)
    assert "cleanup unavailable" in line
    assert "config.json couldn't be read" in line


def test_an_unseen_offer_is_named_in_the_header_too(ledger):
    ledger.raise_offer("gpu-pack")
    assert "GPU support pack available" in ledger.status_line(False, False)


def test_unfinished_setup_replaces_the_header_entirely(ledger):
    ledger.set_fault("setup-unfinished")
    assert ledger.status_line(paused=False, cleanup=True) == \
        "Cadent — setup unfinished"


# ---- a download in flight (#115) -------------------------------------------

def test_a_running_download_is_named_rather_than_left_silent(ledger):
    """One balloon at the start and then silence, for anything up to 3.1 GB,
    was the whole of #115."""
    ledger.set_activity("downloading the speech model, 16%")
    assert ledger.status_line(paused=False, cleanup=False) == \
        "Cadent — ready · cleanup off · downloading the speech model, 16%"


def test_a_download_is_not_a_reason_to_go_amber(ledger):
    """Amber means "unseen, or wrong". Work in progress is neither, and a
    colour that fires on routine activity is a colour people learn to ignore."""
    ledger.set_activity("downloading the speech model, 16%")
    assert ledger.state(paused=False) == READY
    assert ledger.concerns() == []


def test_the_header_goes_quiet_again_once_the_download_ends(ledger):
    ledger.set_activity("downloading the speech model, 16%")
    ledger.set_activity(None)
    assert ledger.status_line(paused=False, cleanup=False) == \
        "Cadent — ready · cleanup off"


def test_a_download_is_named_beside_a_fault_not_instead_of_it(ledger):
    ledger.set_fault("config-unreadable")
    ledger.set_activity("downloading the cleanup model, 60%")
    line = ledger.status_line(paused=False, cleanup=False)
    assert "downloading the cleanup model, 60%" in line
    assert "config.json couldn't be read" in line


# ---- the menu --------------------------------------------------------------

@pytest.fixture
def tray(qt_app):
    calls = {"cleanup": [], "pause": [], "settings": 0, "history": 0,
             "wizard": 0, "gpu": 0, "quit": 0}

    def bump(key):
        def fn():
            calls[key] += 1
        return fn

    widget = Tray(on_toggle_cleanup=calls["cleanup"].append,
                  on_toggle_pause=calls["pause"].append,
                  on_settings=bump("settings"), on_history=bump("history"),
                  on_wizard=bump("wizard"), on_gpu_download=bump("gpu"),
                  on_quit=bump("quit"))
    widget.calls = calls
    yield widget
    widget.icon.hide()


def test_the_header_is_disabled_so_it_reads_as_a_label(tray):
    assert tray.status_action.isEnabled() is False


def test_the_menu_carries_the_sections_in_order(tray):
    """No "Run setup wizard" — it duplicated the button in Settings ▸ General
    and sat under History…, where an accidental click opened a full-screen
    wizard over whatever you were doing (#110)."""
    labels = [a.text() for a in tray.menu.actions() if not a.isSeparator()]
    assert labels == ["Cadent — ready · cleanup off", "Finish setup…",
                      "Clean up transcripts (AI)", "Pause dictation",
                      "Settings…", "History…",
                      tray.gpu_action.text(), "Quit"]


def test_settings_and_history_never_move_when_the_gpu_item_appears(tray):
    """Separator-grouped sections keep conditional items from shuffling the
    action list under the cursor: the GPU item lives below them, in its own
    section, so it can only ever appear after everything it must not move."""
    actions = tray.menu.actions()
    before = (actions.index(tray.settings_action),
              actions.index(tray.history_action))
    tray.offer_gpu_pack()
    actions = tray.menu.actions()
    assert (actions.index(tray.settings_action),
            actions.index(tray.history_action)) == before
    assert actions.index(tray.gpu_action) > actions.index(tray.history_action)
    assert tray.gpu_action.isVisible() is True


def test_the_gpu_item_stays_available_after_the_colour_clears(tray):
    """The menu item remains forever; only the colour stops."""
    tray.offer_gpu_pack()
    tray._on_menu_about_to_show()
    assert tray.ledger.state(paused=False) == READY
    assert tray.gpu_action.isVisible() is True


def test_the_gpu_section_carries_no_separator_until_it_has_something_in_it(tray):
    """The GPU item is the only thing left in that section now, so the
    separator grouping it has to appear with it — otherwise the menu every
    non-NVIDIA user sees renders History… ⎯ ⎯ Quit (#110)."""
    assert tray.gpu_separator.isSeparator()
    assert tray.gpu_separator.isVisible() is False
    tray.offer_gpu_pack()
    assert tray.gpu_separator.isVisible() is True


def test_the_menu_never_draws_two_rules_in_a_row(tray):
    """What the conditional separator is for, stated as the thing anyone
    actually sees. Checked in both directions: the offer arriving must not
    leave a gap behind it, and installing the pack must not leave its rule."""
    def rules_are_adjacent():
        visible = [a for a in tray.menu.actions() if a.isVisible()]
        return any(a.isSeparator() and b.isSeparator()
                   for a, b in zip(visible, visible[1:], strict=False))

    assert rules_are_adjacent() is False
    tray.offer_gpu_pack()
    assert rules_are_adjacent() is False
    tray.withdraw_gpu_offer()
    assert rules_are_adjacent() is False


def test_the_gpu_separator_sits_between_history_and_the_offer(tray):
    """It has to group the offer, not float loose at the bottom: showing it
    below the GPU item would put a rule directly above Quit's own."""
    tray.offer_gpu_pack()
    actions = tray.menu.actions()
    assert (actions.index(tray.history_action)
            < actions.index(tray.gpu_separator)
            < actions.index(tray.gpu_action))


def test_finish_setup_is_still_the_way_back_into_the_wizard(tray):
    """The one wizard entry point the tray keeps: not a duplicate, but the
    recovery path §6.4 was written around (#110)."""
    tray.set_fault("setup-unfinished")
    tray.finish_setup_action.trigger()
    assert tray.calls["wizard"] == 1


def test_unfinished_setup_bolds_a_way_back_and_disables_dictation(tray):
    assert tray.finish_setup_action.isVisible() is False
    tray.set_fault("setup-unfinished")
    assert tray.finish_setup_action.isVisible() is True
    assert tray.finish_setup_action.font().bold() is True
    assert tray.cleanup_action.isEnabled() is False
    assert tray.pause_action.isEnabled() is False


# ---- left-click toggles pause (§3.3) ---------------------------------------

def test_left_click_flips_pause(tray):
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert tray.calls["pause"] == [True]


def test_double_click_is_the_same_action_not_two(tray):
    """Qt emits Trigger on the first click and DoubleClick on the second."""
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert tray.calls["pause"] == [True]


def test_a_context_menu_open_is_not_a_pause(tray):
    tray._on_activated(QSystemTrayIcon.ActivationReason.Context)
    assert tray.calls["pause"] == []


def test_clicking_while_setup_is_unfinished_does_nothing(tray):
    tray.set_fault("setup-unfinished")
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert tray.calls["pause"] == []


def test_the_click_is_confirmed_by_the_icon_and_tooltip_alone(tray):
    """No toast: a tray click happens with your eye already on the icon."""
    tray.set_paused(True)
    assert "paused" in tray.icon.toolTip()
    tray.set_paused(False)
    assert "hold the hotkey" in tray.icon.toolTip()


def test_the_tooltip_carries_the_mode_the_icon_dropped(tray):
    tray.set_cleanup(True)
    assert "cleanup on" in tray.icon.toolTip()
    tray.set_cleanup(False)
    assert "cleanup off" in tray.icon.toolTip()


def test_the_tooltip_names_the_concern_when_amber(tray):
    tray.set_fault("model-failed")
    assert "speech model failed" in tray.icon.toolTip()


def test_the_tooltip_answers_how_far_the_download_has_got(tray):
    """The header only exists while the menu is open. Hovering is how you ask
    "is this getting anywhere?" without clicking, and during a download that
    question beats "hold the hotkey to dictate" — which is not yet true."""
    tray.set_download("speech model", Progress(130_000_000, 792_723_456))

    assert tray.icon.toolTip() == \
        "Cadent (cleanup off) — downloading the speech model, 16%"
    assert "downloading the speech model, 16%" in tray.status_action.text()


def test_a_finished_download_leaves_the_tray_exactly_as_it_found_it(tray):
    before = tray.icon.toolTip()
    tray.set_download("speech model", Progress(1, 2))
    tray.set_download(None)

    assert tray.icon.toolTip() == before


def test_a_download_never_shouts_over_a_fault_in_the_tooltip(tray):
    """Amber is the more urgent fact, and the header still carries both."""
    tray.set_fault("model-failed")
    tray.set_download("speech model", Progress(1, 2))

    assert "speech model failed" in tray.icon.toolTip()


def test_the_icon_changes_with_the_state(tray):
    ready = tray.icon.icon().cacheKey()
    tray.set_paused(True)
    assert tray.icon.icon().cacheKey() != ready


# ---- the darwin menu (spec §7, #148) ----------------------------------------

def test_no_gpu_pack_items_where_no_pack_exists(qt_app, monkeypatch):
    """Metal ships in the build, so there is nothing to download: the GPU
    section never joins the darwin menu, and an offer raised anyway (a bug
    upstream) must not resurrect it."""
    pin_darwin_ui_platform(monkeypatch)
    widget = Tray(on_toggle_cleanup=lambda _on: None,
                  on_toggle_pause=lambda _on: None,
                  on_settings=lambda: None, on_history=lambda: None,
                  on_wizard=lambda: None, on_gpu_download=lambda: None,
                  on_quit=lambda: None)
    try:
        labels = [a.text() for a in widget.menu.actions() if not a.isSeparator()]
        assert not any("GPU" in text for text in labels)
        widget.offer_gpu_pack()
        labels = [a.text() for a in widget.menu.actions() if not a.isSeparator()]
        assert not any("GPU" in text for text in labels)
    finally:
        widget.icon.hide()


def test_a_darwin_icon_click_is_the_menu_not_a_pause(qt_app, monkeypatch):
    """On macOS the status-item click *opens the menu*, and Qt emits
    activated(Trigger) for that same click — flipping pause there would pause
    dictation on every look at the menu (#160). The click is only a spare
    gesture where the menu lives on right-click; the fact table says whose
    the click is."""
    pin_darwin_ui_platform(monkeypatch)
    calls = []
    widget = Tray(on_toggle_cleanup=lambda _on: None,
                  on_toggle_pause=calls.append,
                  on_settings=lambda: None, on_history=lambda: None,
                  on_wizard=lambda: None, on_gpu_download=lambda: None,
                  on_quit=lambda: None)
    try:
        widget._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
        widget._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        assert calls == []
    finally:
        widget.icon.hide()
