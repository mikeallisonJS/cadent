"""The config *shape* and its tolerant parse.

File I/O lives in ConfigStore and is tested in test_config_store.py; what is
left here is the pure translation between a raw JSON dict and the dataclass
the whole app holds references into.
"""

import pytest

from cadent.config import AppOverride, Config, parse


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    """Config defaults now come from the platform column (#144); these tests
    describe the win32 shape (seed rules, "type" default), so pin it."""


def loaded(raw: dict) -> Config:
    return parse(raw)[0]


def issues(raw: dict) -> dict:
    return {i.field: (i.file_value, i.used) for i in parse(raw)[1]}


def test_known_fields_are_read_through():
    cfg = loaded({"hotkey": "<ctrl>+<alt>", "stt_model": "base.en"})
    assert cfg.hotkey == "<ctrl>+<alt>"
    assert cfg.stt_model == "base.en"


def test_an_empty_dict_is_all_defaults():
    cfg = loaded({})
    assert cfg.hotkey_mode == "hold"
    assert cfg.paused is False


def test_unknown_keys_are_ignored():
    """They are ignored *here*; the store preserves them on disk."""
    assert loaded({"hotkey": "<ctrl>", "some_future_field": 1}).hotkey == "<ctrl>"


def test_default_overrides_include_terminals():
    terminals = [o for o in loaded({}).app_overrides
                 if o.process == "windowsterminal.exe"]
    assert terminals and terminals[0].strategy == "clipboard"
    assert terminals[0].paste_chord == "ctrl+shift+v"


def test_default_overrides_route_notepad_to_clipboard():
    """Win11 Notepad scrambles fast synthetic unicode input; clipboard paste
    was the only fast 3/3-clean strategy in the ticket-17 sweep."""
    notepad = [o for o in loaded({}).app_overrides if o.process == "notepad.exe"]
    assert notepad and notepad[0].strategy == "clipboard"
    # "" = the platform's own chord (Ctrl+V here); only the terminals need an
    # explicit ctrl+shift+v.
    assert notepad[0].paste_chord == ""
    assert notepad[0].settle_delay_ms >= 500  # cold Notepad pastes late


def test_overrides_round_trip_through_to_raw():
    cfg = Config(app_overrides=[AppOverride(process="foo.exe",
                                            strategy="notify-only",
                                            settle_delay_ms=300)])
    assert loaded(cfg.to_raw()).app_overrides == cfg.app_overrides


def test_learned_override_round_trips():
    """Auto-learn (#45): a learned entry survives a reload."""
    cfg = Config(app_overrides=[AppOverride(process="slack.exe",
                                            strategy="clipboard", learned=True)])
    assert loaded(cfg.to_raw()).app_overrides[0].learned is True


def test_hand_authored_override_defaults_unlearned():
    raw = {"app_overrides": [{"process": "vim.exe", "strategy": "clipboard"}]}
    assert loaded(raw).app_overrides[0].learned is False


def test_malformed_overrides_are_skipped():
    raw = {"app_overrides": [
        {"process": "ok.exe", "strategy": "clipboard", "future_key": 1},
        "not-a-dict", {"strategy": "clipboard"}]}
    assert [o.process for o in loaded(raw).app_overrides] == ["ok.exe"]


def test_a_scaffold_era_config_still_loads():
    raw = {"hotkey": "<ctrl>+<cmd>", "clipboard_fallback_apps": ["mstsc.exe"],
           "cleanup_mode": True}
    cfg = loaded(raw)
    assert cfg.hotkey == "<ctrl>+<cmd>"
    assert cfg.paused is False


# ---- sanitizing: a typo can't brick the app, and it is reported ------------

def test_wrong_typed_fields_reset_to_defaults():
    cfg = loaded({"hotkey": 5, "hotkey_mode": "bogus",
                  "max_utterance_seconds": "x", "input_device": 3})
    assert cfg.hotkey == "<ctrl>+<cmd>"
    assert cfg.hotkey_mode == "hold"
    assert cfg.max_utterance_seconds == 120
    assert cfg.input_device is None


def test_every_reset_is_reported_with_what_the_file_said():
    """Under wholesale save the next save quietly repaired these; under delta
    writes it never does, so the pane has to say so (§7.5)."""
    reported = issues({"hotkey_mode": "hodl", "autostart": "yes"})
    assert reported["hotkey_mode"] == ("hodl", "hold")
    assert reported["autostart"] == ("yes", False)


def test_a_field_the_file_never_set_is_not_reported():
    assert issues({}) == {}


def test_an_int_where_a_float_belongs_is_not_a_typo():
    cfg, reported = parse({"vocab_fuzzy_threshold": 1})
    assert cfg.vocab_fuzzy_threshold == 1.0
    assert reported == []


def test_a_bool_never_passes_for_an_int():
    """bool subclasses int, so an isinstance check alone lets `true` through
    as 1 on an int-typed field."""
    assert loaded({"max_utterance_seconds": True}).max_utterance_seconds == 120


def test_vocab_thresholds_default_and_are_tunable():
    cfg = loaded({})
    assert cfg.vocab_fuzzy_threshold == 0.85       # ticket-04 decided values
    assert cfg.vocab_fuzzy_threshold_short == 0.90
    assert loaded({"vocab_fuzzy_threshold": 0.95}).vocab_fuzzy_threshold == 0.95


def test_wrong_typed_vocab_threshold_resets():
    assert loaded({"vocab_fuzzy_threshold": "high"}).vocab_fuzzy_threshold == 0.85


def test_wrong_typed_shell_settings_reset():
    cfg = loaded({"autostart": "yes", "history_retention_days": "forever"})
    assert cfg.autostart is False
    assert cfg.history_retention_days == 0


# ---- defaults the charter and PRD pinned -----------------------------------

def test_cleanup_mode_defaults():
    cfg = loaded({})
    assert cfg.cleanup_mode is False
    assert cfg.cleanup_hotkey == "<ctrl>+<shift>+<alt>"
    # ticket-02 benchmark winner is the default cleanup model
    assert cfg.llm_model_path.endswith("Qwen3-4B-Instruct-2507-Q4_K_M.gguf")


def test_shell_settings_default_off_and_keep_forever():
    cfg = loaded({})
    assert cfg.autostart is False                 # charter: autostart off by default
    assert cfg.history_retention_days == 0        # PRD 5.6: default keep forever
    assert cfg.setup_complete is False


def test_paused_and_cleanup_mode_round_trip():
    cfg = Config(paused=True, cleanup_mode=True, cleanup_hotkey="<ctrl>+<alt>+f9")
    reloaded = loaded(cfg.to_raw())
    assert reloaded.paused is True
    assert reloaded.cleanup_mode is True
    assert reloaded.cleanup_hotkey == "<ctrl>+<alt>+f9"


def test_shell_settings_round_trip():
    reloaded = loaded(Config(autostart=True, history_retention_days=30).to_raw())
    assert reloaded.autostart is True
    assert reloaded.history_retention_days == 30


# ---- M4 additions ----------------------------------------------------------

def test_theme_defaults_to_following_windows():
    assert loaded({}).theme == "system"
    assert loaded({"theme": "dark"}).theme == "dark"


def test_an_unrecognised_theme_falls_back_and_is_reported():
    cfg, reported = parse({"theme": "midnight"})
    assert cfg.theme == "system"
    assert reported[0].field == "theme"


# ---- a second speech engine (#72) ------------------------------------------

def test_the_default_engine_is_still_whisper_on_the_cpu_safe_path():
    cfg = loaded({})
    assert cfg.stt_engine == "faster-whisper"
    assert cfg.stt_device == "auto"


def test_parakeet_is_a_recognised_engine():
    cfg = loaded({"stt_engine": "parakeet", "stt_model": "parakeet-tdt-0.6b-v3",
                  "stt_device": "directml"})
    assert cfg.stt_engine == "parakeet"
    assert cfg.stt_device == "directml"


def test_an_unrecognised_engine_falls_back_and_is_reported():
    cfg, reported = parse({"stt_engine": "kaldi"})
    assert cfg.stt_engine == "faster-whisper"
    assert [i.field for i in reported] == ["stt_engine"]


def test_a_runtime_the_chosen_engine_cannot_use_falls_back_and_is_reported():
    """`directml` is a Parakeet-only runtime; ctranslate2 would silently land
    on the CPU instead, which is the kind of quiet override §7.5 exists to
    surface."""
    cfg, reported = parse({"stt_engine": "faster-whisper", "stt_device": "directml"})
    assert cfg.stt_device == "auto"
    assert [i.field for i in reported] == ["stt_device"]


def test_stray_gpu_runtimes_sanitize_to_auto_on_a_one_rung_platform(monkeypatch):
    """ADR 0003 (#146): the valid runtimes are a platform fact, not a global.
    On darwin's one-rung column a config carried over from a Windows install
    ("cuda", "directml") sanitizes back to `auto` — and is reported, because
    the engine would otherwise quietly run somewhere the file doesn't say."""
    from conftest import pin_one_rung_platform

    pin_one_rung_platform(monkeypatch)
    for engine, device in (("parakeet", "directml"), ("parakeet", "cuda"),
                           ("faster-whisper", "cuda")):
        cfg, reported = parse({"stt_engine": engine, "stt_device": device})
        assert cfg.stt_device == "auto"
        assert "stt_device" in [i.field for i in reported]
    cfg, reported = parse({"stt_engine": "parakeet", "stt_device": "cpu"})
    assert cfg.stt_device == "cpu"
    assert reported == []


def test_a_bad_engine_does_not_take_a_good_runtime_down_with_it():
    """The engine resets first, so the runtime is judged against the engine
    that will actually run."""
    cfg, _ = parse({"stt_engine": "kaldi", "stt_device": "cuda"})
    assert cfg.stt_device == "cuda"


# ---- the cleanup runtime (#116) --------------------------------------------

def test_cleanup_reaches_for_the_gpu_by_default():
    assert loaded({}).llm_runtime == "auto"


def test_a_pinned_cleanup_runtime_is_kept():
    assert loaded({"llm_runtime": "cpu"}).llm_runtime == "cpu"


@pytest.mark.parametrize("hand_edited", ["vulkan", "gpu"])
def test_neither_the_backend_nor_the_rung_is_a_cleanup_runtime(hand_edited):
    """Neither is a value: the ladder proves the GPU itself and walks down by
    itself, so the only choice worth persisting is "don't try". Naming the
    backend would date the file, and `gpu` — the rung `auto` lands on (#155) —
    would be `auto` under another name."""
    cfg, reported = parse({"llm_runtime": hand_edited})
    assert cfg.llm_runtime == "auto"
    assert [i.field for i in reported] == ["llm_runtime"]


def test_overlay_defaults_show_activity_at_bottom_centre():
    cfg = loaded({})
    assert cfg.show_overlay is True
    assert cfg.overlay_position_custom is False
    assert cfg.overlay_anchor_x == 0.5
    assert cfg.overlay_snap is True


def test_a_moved_overlay_round_trips_as_a_fraction_not_pixels():
    """A normalized anchor survives a monitor being unplugged (§4.6)."""
    cfg = Config(overlay_position_custom=True, overlay_anchor_x=0.18,
                 overlay_anchor_y=0.42)
    reloaded = loaded(cfg.to_raw())
    assert reloaded.overlay_position_custom is True
    assert (reloaded.overlay_anchor_x, reloaded.overlay_anchor_y) == (0.18, 0.42)
