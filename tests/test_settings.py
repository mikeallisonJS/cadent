"""Apply semantics for Settings (M4 §5.2).

Instant apply removes the draft, and with it the two-config diff. What used
to be `restarts_needed(old, new)` over `ENGINE_FIELDS` is now a lookup keyed
on the single field that was written.
"""

from dataclasses import fields

from cadent import config, models, settings, stt
from cadent.config import Config


def test_a_heavy_field_names_its_engine():
    assert settings.engine_for("stt_model") == "stt"
    assert settings.engine_for("input_device") == "mic"
    assert settings.engine_for("hotkey") == "hotkeys"
    assert settings.engine_for("cleanup_hotkey") == "hotkeys"
    assert settings.engine_for("hotkey_mode") == "hotkeys"
    assert settings.engine_for("min_hold_ms") == "hotkeys"
    assert settings.engine_for("llm_model_path") == "llm"


def test_a_live_field_names_no_engine():
    """Immediate is the default, so live fields carry no badge — badging the
    common case would imply the others are somehow more immediate."""
    for field in ("cleanup_mode", "autostart", "history_retention_days", "theme",
                  "paused", "show_overlay", "app_overrides"):
        assert settings.engine_for(field) is None
        assert settings.restart_hint(field) == ""
        assert settings.restart_announcement(field) == ""


def test_every_engine_field_is_a_real_config_field():
    known = {f.name for f in fields(Config)}
    assert set(settings.FIELD_ENGINES) <= known


def test_every_engine_has_a_hint_and_a_label():
    for engine in set(settings.FIELD_ENGINES.values()):
        assert settings.RESTART_HINTS[engine]
        assert settings.ENGINE_LABELS[engine]


def test_a_heavy_field_says_what_will_happen_and_how_long_it_takes():
    """A two-second pause with no explanation is a mystery."""
    hint = settings.restart_hint("stt_model")
    assert "restarts" in hint
    assert "~2 s" in hint


def test_a_heavy_field_announces_the_restart_on_commit():
    """Announced as an Alert, so a screen-reader user learns what changed.

    "the speech engine", not "Whisper": since #72 there are two of them, and
    naming the wrong one is worse than naming neither.
    """
    assert settings.restart_announcement("stt_model") == \
        "Setting changed — restarting the speech engine."


def test_strategies_read_in_plain_language_never_the_raw_enum():
    assert settings.strategy_label("sendinput") == "Type it"
    assert settings.strategy_label("clipboard") == "Paste it"
    assert settings.strategy_label("notify-only") == "Don't insert"


def test_clipboard_no_restore_is_a_spelling_of_paste_not_a_fourth_strategy():
    """It and `restore_clipboard: false` are two spellings of one behaviour,
    so the dropdown offers three and restore is a checkbox under Paste it."""
    assert settings.strategy_label("clipboard-no-restore") == "Paste it"
    assert [key for key, _ in settings.STRATEGY_LABELS] == \
        ["type", "clipboard", "notify-only"]


def test_retention_offers_plausible_choices_including_keep_forever():
    values = [days for days, _ in settings.RETENTION_CHOICES]
    assert values[0] == 0                      # PRD 5.6 default
    assert settings.retention_label(0) == "Keep forever"
    assert settings.retention_label(30) == "30 days"


def test_a_hand_edited_retention_still_reads_as_something():
    assert settings.retention_label(137) == "137 days"


def test_the_default_stt_model_is_offered():
    assert Config().stt_model in settings.STT_MODEL_CHOICES


# ---- engine-scoped choices (#72) ------------------------------------------

def test_model_choices_are_scoped_to_the_engine():
    """A flat list of faster-whisper names can't describe two engines."""
    assert "distil-small.en" in settings.model_choices("faster-whisper")
    assert "distil-small.en" not in settings.model_choices("parakeet")
    assert settings.model_choices("parakeet") == (
        "parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3")


def test_both_parakeet_checkpoints_are_offered():
    for model in settings.model_choices("parakeet"):
        assert model in stt.PARAKEET_REPOS


def test_every_engine_has_a_default_model_it_actually_offers():
    for engine in config.STT_ENGINES:
        assert settings.default_model(engine) in settings.model_choices(engine)


def test_runtime_choices_are_scoped_to_the_engine(pinned_win32_facts):
    """DirectML is meaningless to ctranslate2; it is the Parakeet path only.
    Pinned to the win32 column: darwin offers one rung to both engines
    (#146), which has its own test in test_platform."""
    assert "directml" not in settings.runtime_choices("faster-whisper")
    assert "directml" in settings.runtime_choices("parakeet")
    assert settings.runtime_choices("faster-whisper")[0] == "auto"
    assert settings.runtime_choices("parakeet")[0] == "auto"


def test_every_runtime_reads_in_plain_language():
    for engine in config.STT_ENGINES:
        for runtime in settings.runtime_choices(engine):
            assert settings.runtime_label(runtime) != runtime
    for runtime in settings.cleanup_runtime_choices():
        assert settings.runtime_label(runtime) != runtime


def test_the_cleanup_runtime_offers_what_config_accepts():
    """One list of runtimes, not two: an option the pane offers and the parse
    rejects is a setting that resets itself the next time the app starts."""
    assert settings.cleanup_runtime_choices() == config.LLM_RUNTIMES
    assert settings.cleanup_runtime_choices()[0] == "auto"


def test_changing_the_cleanup_runtime_reloads_the_cleanup_model():
    """It is a load-time argument to llama.cpp, so nothing changes until the
    model is rebuilt — and the user is told so before they touch it (#116)."""
    assert settings.engine_for("llm_runtime") == "llm"
    assert settings.restart_hint("llm_runtime") == settings.restart_hint("llm_model_path")


def test_the_model_list_offers_exactly_the_engines_config_accepts():
    """Two lists of engines is one too many; this is what keeps them honest.

    Since #111 the check is on the merged picker's own source rather than on an
    engine dropdown, because there isn't one.
    """
    assert {m.engine for m in models.SPEECH_MODELS} == set(config.STT_ENGINES)


def test_the_engine_dropdown_and_its_captions_are_gone():
    """Captioning an engine picker that no longer exists is a lie waiting to
    be read as a spec (#111)."""
    assert not hasattr(settings, "STT_ENGINE_CHOICES")
    assert not hasattr(settings, "ENGINE_CAPTIONS")


def test_an_unknown_engine_degrades_to_the_default_rather_than_raising():
    """A hand-edited config.json can name anything; the panes must still build."""
    assert settings.model_choices("kaldi") == settings.model_choices("faster-whisper")
    assert settings.runtime_choices("kaldi") == settings.runtime_choices("faster-whisper")


def test_parakeet_says_out_loud_that_biasing_does_not_apply():
    """`hotwords=` has no Parakeet equivalent — so the UI must not let the user
    think vocabulary biasing is running (#72)."""
    assert settings.biasing_note("parakeet")
    assert settings.biasing_note("faster-whisper") == ""
    assert settings.supports_biasing("parakeet") is False
    assert settings.supports_biasing("faster-whisper") is True


def test_the_engine_registry_covers_exactly_the_engines_config_accepts():
    """`supports_biasing` reads capabilities off these classes, so a missing
    entry would be an AttributeError in a Settings pane."""
    assert set(stt.ENGINES) == set(config.STT_ENGINES)


def test_the_draft_model_is_gone():
    """There is no OK, so there is no draft to diff (#76's correction)."""
    assert not hasattr(settings, "restarts_needed")
    assert not hasattr(settings, "ENGINE_FIELDS")
