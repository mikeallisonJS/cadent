"""The config.json write model (spec §7).

The contract this file pins down, in one sentence: **memory is the running
truth and the file is its projection**, so every write names its key, merges
it into whatever is on disk right now, and lands atomically — and an
unreadable file is never written to at all.

This is deliberately *not* vocabulary.json's contract. That file is re-read at
the start of every dictation, which is why writing it is applying it. This one
is read once at startup into a live Config that subsystems hold references
into, so changes here apply at the next start.
"""

import json

import pytest

from cadent.config import AppOverride, Config
from cadent.config_store import ConfigStore


class ManualTimer:
    """Stands in for the coalesce timer so tests never sleep."""

    def __init__(self):
        self.on_timeout = None
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def fire(self):
        self.running = False
        if self.on_timeout is not None:
            self.on_timeout()


@pytest.fixture
def store(tmp_path):
    return ConfigStore(tmp_path / "config.json")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---- seeding a new file ----------------------------------------------------

def test_a_missing_file_is_seeded_with_defaults_and_its_own_documentation(tmp_path):
    """config's `_editing` states the *opposite* apply semantics to its
    siblings', which is exactly why it belongs in the file (§7.6)."""
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    raw = read(path)
    assert raw["hotkey"] == Config().hotkey
    assert "next time Cadent starts" in raw["_editing"]
    assert store.readable is True


def test_an_existing_file_is_never_backfilled_with_comments(tmp_path):
    """Re-adding a comment the user deleted needs a marker key, which is worse
    than the gap (§7.6)."""
    path = tmp_path / "config.json"
    path.write_text('{"hotkey": "<ctrl>"}', encoding="utf-8")
    ConfigStore(path).set("paused", True)
    assert "_comment" not in read(path)


# ---- delta writes ----------------------------------------------------------

def test_a_write_names_its_key_and_leaves_the_rest_of_the_file_alone(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "_comment": "hand-written note",
        "some_future_field": 1,
        "hotkey": "<ctrl>+<alt>",
        "stt_model": "base.en",
    }), encoding="utf-8")
    store = ConfigStore(path)
    store.set("paused", True)

    raw = read(path)
    assert raw["paused"] is True
    assert raw["_comment"] == "hand-written note"       # comments round-trip
    assert raw["some_future_field"] == 1                # unknown keys survive
    assert raw["hotkey"] == "<ctrl>+<alt>"              # untouched keys survive


def test_a_hand_edit_to_another_key_between_writes_is_not_stomped(tmp_path):
    """The merge reads current disk contents, not a snapshot — otherwise
    settings would quietly revert an edit made while it was open."""
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.set("paused", True)

    raw = read(path)
    raw["stt_model"] = "large-v3"
    path.write_text(json.dumps(raw), encoding="utf-8")

    store.set("autostart", True)
    assert read(path)["stt_model"] == "large-v3"


def test_the_write_updates_memory_too(store):
    store.set("paused", True)
    assert store.config.paused is True


def test_writes_are_atomic_and_leave_no_temp_behind(tmp_path):
    """save() was a bare write_text; a crash mid-write truncated the file, and
    instant apply multiplies the exposure."""
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.set("paused", True)
    assert list(p.name for p in tmp_path.iterdir()) == ["config.json"]
    assert read(path)["paused"] is True


def test_app_overrides_are_written_by_key_from_the_live_list(tmp_path):
    """The injector holds the config's list *object*, so the pane mutates it
    in place and then asks the store to write that one key (§7.7)."""
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    live = store.config.app_overrides
    live.append(AppOverride(process="slack.exe", strategy="clipboard", learned=True))
    store.set("app_overrides")

    assert store.config.app_overrides is live          # never rebound
    written = read(path)["app_overrides"]
    assert written[-1] == {"process": "slack.exe", "strategy": "clipboard",
                           "paste_chord": "", "chunk_size": 0,
                           "chunk_delay_ms": 0, "settle_delay_ms": 150,
                           "restore_clipboard": True, "learned": True}


def test_setting_an_unknown_field_is_refused(store):
    with pytest.raises(KeyError):
        store.set("not_a_config_field", 1)


# ---- an unreadable file is never overwritten -------------------------------

def test_a_malformed_file_starts_the_app_on_defaults_and_says_so(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"hotkey": "<ctrl>"', encoding="utf-8")   # truncated JSON
    store = ConfigStore(path)
    assert store.readable is False
    assert store.config.hotkey == Config().hotkey


def test_a_malformed_file_is_never_written_to(tmp_path):
    """load() already left a malformed file 'untouched for hand-repair' — and
    then the next save() destroyed it anyway. Under instant apply a single
    stray click would do it."""
    path = tmp_path / "config.json"
    original = '{"hotkey": "<ctrl>"'
    path.write_text(original, encoding="utf-8")
    store = ConfigStore(path)
    store.set("paused", True)

    assert path.read_text(encoding="utf-8") == original
    assert store.config.paused is True          # session only, never persisted


def test_a_file_that_breaks_while_running_is_not_written_to(tmp_path):
    """Each write re-checks: the file was fine at startup and isn't now."""
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    broken = '{"hotkey":'
    path.write_text(broken, encoding="utf-8")
    store.set("paused", True)
    assert path.read_text(encoding="utf-8") == broken


def test_a_json_file_that_is_not_an_object_counts_as_unreadable(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = ConfigStore(path)
    assert store.readable is False
    store.set("paused", True)
    assert path.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_backing_up_and_starting_fresh_is_the_only_thing_that_displaces_it(tmp_path):
    """The rename-aside is user-initiated, from an inline error state — never
    a prompt, and never automatic."""
    path = tmp_path / "config.json"
    path.write_text('{"hotkey": "<ctrl>"', encoding="utf-8")
    store = ConfigStore(path)
    backup = store.back_up_and_start_fresh()

    assert backup.exists() and backup != path
    assert backup.read_text(encoding="utf-8") == '{"hotkey": "<ctrl>"'
    assert store.readable is True
    assert read(path)["hotkey"] == Config().hotkey
    store.set("paused", True)
    assert read(path)["paused"] is True


# ---- divergence (§7.4) -----------------------------------------------------

def test_divergence_is_measured_against_the_startup_raw_dict(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"stt_model": "base.en"}), encoding="utf-8")
    store = ConfigStore(path)
    assert store.divergence() == {}

    raw = read(path)
    raw["stt_model"] = "large-v3"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert store.divergence() == {"stt_model": "large-v3"}


def test_a_sanitized_field_never_reads_as_divergence(tmp_path):
    """_sanitized() type-corrects in memory and, under delta writes, never
    writes the correction back — so a memory-vs-file comparison would
    false-positive forever on any typo'd field."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_mode": "hodl"}), encoding="utf-8")
    store = ConfigStore(path)
    assert store.config.hotkey_mode == "hold"
    assert store.divergence() == {}


def test_our_own_writes_never_read_as_divergence(store):
    store.set("paused", True)
    assert store.divergence() == {}


def test_divergence_ignores_keys_that_are_not_config_fields(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    raw = read(path)
    raw["_comment"] = "changed my note"
    raw["some_future_field"] = 2
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert store.divergence() == {}


def test_an_unreadable_file_reports_no_divergence(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    path.write_text("{", encoding="utf-8")
    assert store.divergence() == {}


# ---- the sanitize report (§7.5) --------------------------------------------

def test_a_bad_value_is_reported_rather_than_silently_repaired(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_mode": "hodl",
                                "max_utterance_seconds": "x"}), encoding="utf-8")
    store = ConfigStore(path)
    reported = {i.field: (i.file_value, i.used) for i in store.sanitized}
    assert reported["hotkey_mode"] == ("hodl", "hold")
    assert reported["max_utterance_seconds"] == ("x", 120)
    assert path.read_text(encoding="utf-8")      # the file is left alone


def test_setting_the_field_in_the_pane_clears_its_report(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey_mode": "hodl"}), encoding="utf-8")
    store = ConfigStore(path)
    assert [i.field for i in store.sanitized] == ["hotkey_mode"]
    store.set("hotkey_mode", "toggle")
    assert store.sanitized == []
    assert read(path)["hotkey_mode"] == "toggle"


def test_a_valid_file_reports_nothing(store):
    assert store.sanitized == []


# ---- coalescing (§7.3) -----------------------------------------------------

def test_discrete_acts_write_immediately(tmp_path):
    """A blanket debounce makes a discrete act like `paused` losable if the
    app is killed inside the window."""
    path = tmp_path / "config.json"
    timer = ManualTimer()
    store = ConfigStore(path, timer=timer)
    store.set("paused", True)
    assert read(path)["paused"] is True
    assert timer.running is False


def test_a_repeating_control_coalesces_until_the_timer_fires(tmp_path):
    """min_hold_ms is a spinbox *and* a hotkeys engine field, so under instant
    apply holding the arrow would rebuild the hotkey listener on every
    auto-repeat tick. The coalesce gates the engine restart, not the disk."""
    path = tmp_path / "config.json"
    timer = ManualTimer()
    store = ConfigStore(path, timer=timer)
    baseline = read(path)["min_hold_ms"]

    for value in (210, 220, 230):
        store.set("min_hold_ms", value, coalesce=True)
    assert read(path)["min_hold_ms"] == baseline    # nothing on disk yet
    assert store.config.min_hold_ms == 230          # memory is already current
    assert timer.running is True

    timer.fire()
    assert read(path)["min_hold_ms"] == 230


def test_flush_writes_pending_values_on_focus_out_close_or_quit(tmp_path):
    path = tmp_path / "config.json"
    timer = ManualTimer()
    store = ConfigStore(path, timer=timer)
    store.set("history_retention_days", 30, coalesce=True)
    store.flush()
    assert read(path)["history_retention_days"] == 30
    assert timer.running is False


def test_flushing_with_nothing_pending_is_a_no_op(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path, timer=ManualTimer())
    before = path.read_bytes()
    store.flush()
    assert path.read_bytes() == before


def test_a_discrete_write_flushes_whatever_was_pending(tmp_path):
    """Otherwise a pending spinbox value could land *after* a later discrete
    write and reorder the two on disk."""
    path = tmp_path / "config.json"
    timer = ManualTimer()
    store = ConfigStore(path, timer=timer)
    store.set("min_hold_ms", 250, coalesce=True)
    store.set("paused", True)
    assert read(path)["min_hold_ms"] == 250
    assert timer.running is False


def test_a_pending_write_is_dropped_when_the_file_is_unreadable(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    timer = ManualTimer()
    store = ConfigStore(path, timer=timer)
    store.set("min_hold_ms", 250, coalesce=True)
    store.flush()
    assert path.read_text(encoding="utf-8") == "{"
    assert store.config.min_hold_ms == 250


# ---- one write per gesture (§4.6) ------------------------------------------

def test_set_many_lands_several_fields_in_one_write(tmp_path):
    """The overlay anchor is three fields settled by one gesture, so it is one
    atomic merge rather than three files hitting the disk in a row."""
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.set_many({"overlay_position_custom": True,
                    "overlay_anchor_x": 0.25,
                    "overlay_anchor_y": 0.75})
    raw = read(path)
    assert raw["overlay_position_custom"] is True
    assert (raw["overlay_anchor_x"], raw["overlay_anchor_y"]) == (0.25, 0.75)
    assert store.config.overlay_anchor_x == 0.25


def test_set_many_preserves_the_rest_of_the_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"_comment": "note", "hotkey": "<ctrl>+<alt>"}),
                    encoding="utf-8")
    ConfigStore(path).set_many({"overlay_snap": False})
    raw = read(path)
    assert raw["_comment"] == "note"
    assert raw["hotkey"] == "<ctrl>+<alt>"


def test_set_many_refuses_an_unknown_field(store):
    with pytest.raises(KeyError):
        store.set_many({"overlay_snap": False, "not_a_field": 1})


def test_set_many_never_writes_to_an_unreadable_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    store = ConfigStore(path)
    store.set_many({"overlay_snap": False})
    assert path.read_text(encoding="utf-8") == "{"
    assert store.config.overlay_snap is False
