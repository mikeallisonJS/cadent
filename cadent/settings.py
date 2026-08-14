"""Settings apply semantics (M4 §5.2, replacing M3 ticket 42's draft model).

**Instant apply, no OK/Cancel.** Closing the window is done — there is no
draft, so there is nothing to diff. `restarts_needed(old, new)` and the whole
buffered-commit path are gone; what is left is a field -> engine lookup, keyed
on the single field that was just written.

The live/heavy split itself is untouched. Live fields apply silently and carry
**no badge** — immediate is the default, and badging the common case would
imply the others are somehow more immediate. Heavy fields apply on change and
restart their engine in-process, and say so before they do it.
"""

from __future__ import annotations

from . import config as config_module
from . import models
from .config import LLM_RUNTIMES, STT_ENGINES
from .stt import ENGINES

# The inversion of M3's ENGINE_FIELDS: one field, one engine. Everything not
# listed here is a live field.
FIELD_ENGINES: dict[str, str] = {
    "stt_engine": "stt",
    "stt_model": "stt",
    "stt_device": "stt",
    "input_device": "mic",
    "hotkey": "hotkeys",
    "hotkey_mode": "hotkeys",
    "cleanup_hotkey": "hotkeys",
    "min_hold_ms": "hotkeys",
    "llm_model_path": "llm",
    "llm_max_tokens": "llm",
    "llm_runtime": "llm",
}

ENGINE_LABELS: dict[str, str] = {
    # Not "Whisper": since #72 there are two speech engines, and announcing a
    # restart of the one that isn't running is worse than naming neither.
    "stt": "the speech engine",
    "mic": "microphone capture",
    "hotkeys": "the hotkey listener",
    "llm": "the cleanup model",
}

# Inline hints, shown next to the control *before* it is touched. They carry a
# rough duration because a two-second pause with no explanation is a mystery.
RESTART_HINTS: dict[str, str] = {
    "stt": "⟳ Speech engine restarts when you pick a model (~2 s)",
    "mic": "⟳ Capture retargets to the new device on the next dictation",
    "hotkeys": "⟳ The hotkey listener restarts when you change this",
    "llm": "⟳ The cleanup model reloads when you change this (~3 s)",
}

# Every faster-whisper model that loads, small-to-large — a superset of the
# four the picker lists (#111). Kept as a name because `Config().stt_model`
# has to be one of them, and that is worth a test.
STT_MODEL_CHOICES = tuple(m.id for m in models.speech_models("faster-whisper"))

# Parakeet checkpoints (#72). Both are int8 and CC-BY-4.0 — attribution
# required, unlike the Whisper models — and differ only in the decoder: v2 is
# English-only at 661 MB, v3 covers 25 languages at 670 MB.
PARAKEET_MODEL_CHOICES = tuple(m.id for m in models.speech_models("parakeet"))

# There is no engine dropdown to caption since #111: the engine is derived
# from the model, so what each engine is for is said by the models it owns —
# `models.SPEECH_MODELS` carries the prose now.

# Whisper stays the default: it is the only one that runs well on any PC.
DEFAULT_STT_ENGINE = "faster-whisper"

# Which engines are only worth offering where there is a GPU to run them on
# is a platform fact now (spec §1.3): `Capabilities.gpu_only_engines`. The
# Settings pane and the wizard still gate through `requires_gpu()` rather
# than each testing for "parakeet" in their own words (#72).

# Model choices are engine-scoped: a flat list of *ids* cannot describe two
# engines whose names have nothing in common (#72). The picker the user sees is
# flat again since #111 — but only because every row carries its engine with
# it, which is a property of `models.SPEECH_MODELS`, not of these tuples.
ENGINE_MODEL_CHOICES: dict[str, tuple[str, ...]] = {
    engine: tuple(m.id for m in models.speech_models(engine))
    for engine in STT_ENGINES
}

DEFAULT_MODELS: dict[str, str] = {
    "faster-whisper": "distil-small.en",   # the ticket-03 latency winner
    # v2, not the newer v3: measured 2.87% WER against v3's 4.35% on
    # LibriSpeech clean, and slightly faster. v3 buys 24 other languages,
    # which an app that transcribes with language="en" cannot spend.
    "parakeet": "parakeet-tdt-0.6b-v2",
}

# So are the runtimes. `directml` is meaningless to ctranslate2, and `cuda` on
# the Parakeet path is a deliberate manual choice — it measured slower than
# DirectML and would need a ~1.9 GB support pack that does not exist
# (docs/research/parakeet-runtime.md).
#
# One table across speech and cleanup (#116). `auto` and `cpu` mean the same
# sentence on both, and a user reading two runtime rows in one disclosure
# should not have to work out whether two spellings of one thing differ.
# No entry for the cleanup GPU rung, because nothing offers one: that ladder
# reaches for the GPU without ever asking the user to name it — nor to name the
# API behind it, which is the build's, not the user's (see
# cleanup.RUNTIME_LADDERS).
RUNTIME_LABELS: dict[str, str] = {
    "auto": "Automatic — use the GPU if it works",
    "cuda": "NVIDIA GPU (CUDA)",
    "directml": "Graphics card (DirectML)",
    "cpu": "Processor only",
}

# Said in the Speech pane whenever the engine has no hotwords equivalent, so
# nobody concludes from a populated Vocabulary pane that biasing is running.
BIASING_NOTES: dict[str, str] = {
    "parakeet": "Parakeet can't be primed with your vocabulary while it "
                "listens — your terms are still corrected afterwards.",
}

# A dropdown of plausible choices rather than a spinbox: nobody wants 137 days
# of history, and the spinbox's auto-repeat was the only continuous writer in
# the app (M4 §5.6, §7.3).
RETENTION_CHOICES: tuple[tuple[int, str], ...] = (
    (0, "Keep forever"),
    (7, "7 days"),
    (30, "30 days"),
    (90, "90 days"),
    (365, "1 year"),
)

# Plain language, never the raw enum (M4 §5.5). `clipboard-no-restore` is not
# a fourth strategy: it and `restore_clipboard: false` are two spellings of one
# behaviour, so the dropdown offers three and restore is a checkbox.
STRATEGY_LABELS: tuple[tuple[str, str], ...] = (
    ("type", "Type it"),
    ("clipboard", "Paste it"),
    ("notify-only", "Don't insert"),
)


def engine_for(field: str) -> str | None:
    """The engine a write to this field restarts, or None if it is live."""
    return FIELD_ENGINES.get(field)


def restart_hint(field: str) -> str:
    """The inline hint for a control, or "" for a live field."""
    engine = engine_for(field)
    return RESTART_HINTS.get(engine, "") if engine else ""


def restart_announcement(field: str) -> str:
    """The one-line Alert fired on commit, for a screen reader (M4 §8.4).

    Light fields stay silent in both channels; this mirrors §5.2's split
    rather than inventing a second policy.
    """
    engine = engine_for(field)
    if engine is None:
        return ""
    return f"Setting changed — restarting {ENGINE_LABELS[engine]}."


def model_choices(engine: str) -> tuple[str, ...]:
    """The models this engine offers.

    An unknown engine — config.json is hand-editable — degrades to the default
    engine's list rather than raising, so a typo can't stop a pane building.
    """
    return ENGINE_MODEL_CHOICES.get(engine, ENGINE_MODEL_CHOICES[DEFAULT_STT_ENGINE])


def runtime_choices(engine: str) -> tuple[str, ...]:
    """The runtimes this speech engine can be pointed at on this platform,
    best-guess first (spec §1.3: a platform fact, read from Capabilities)."""
    from . import platform

    runtimes = platform.current().capabilities.stt_runtimes
    return runtimes.get(engine, runtimes[DEFAULT_STT_ENGINE])


def cleanup_runtime_choices() -> tuple[str, ...]:
    """The runtimes cleanup can be pointed at (#116).

    Not engine-scoped, unlike the speech list: there is one cleanup engine.
    Read off `config.LLM_RUNTIMES` rather than restated, so the pane can never
    offer a value the parse would reset on the next start.
    """
    return LLM_RUNTIMES


def engine_for_model(model: str) -> str:
    """Which engine owns this model name.

    Kept as a wrapper over the registry so the call sites that predate #111
    don't move; `models.engine_for_model` is where the answer lives.
    """
    return models.engine_for_model(model)


def default_model(engine: str) -> str:
    return DEFAULT_MODELS.get(engine, DEFAULT_MODELS[DEFAULT_STT_ENGINE])


def runtime_label(runtime: str) -> str:
    return RUNTIME_LABELS.get(runtime, runtime)


def requires_gpu(engine: str) -> bool:
    """Is this engine only worth offering where there is a GPU to run it on?"""
    from . import platform

    return engine in platform.current().capabilities.gpu_only_engines


def app_display_name(identity: str) -> str:
    """What an override or history row calls an app (M5 §5.2): the display
    name when a live lookup resolves — bundle ids are for machines — and the
    raw identity otherwise. On Windows the lookup never resolves, and the
    executable name *is* the display name."""
    from . import platform

    if not identity:
        return identity
    return platform.current().focused_app.display_name(identity) or identity


def supports_biasing(engine: str) -> bool:
    """Can this engine be primed with the vocabulary *while it listens*?

    The UI question behind `SttEngine.supports_hotwords`, asked of an engine
    *name* — the panes have to describe the setting before anything is loaded.
    Read off the engine class rather than restated here, so deleting a note
    can never silently grant an engine a capability it doesn't have.
    """
    return getattr(ENGINES[engine], "supports_hotwords", True)


def biasing_note(engine: str) -> str:
    """What layer-1 vocabulary biasing means for this engine, or "" when it
    just works. Only Parakeet has something to explain (#72)."""
    return BIASING_NOTES.get(engine, "")


def strategy_label(strategy: str) -> str:
    """Plain language for an injection strategy, including the spellings the
    dropdown doesn't offer ("clipboard-no-restore", the "sendinput" alias)."""
    strategy = config_module.canonical_strategy(strategy)
    if strategy == "clipboard-no-restore":
        return "Paste it"
    return dict(STRATEGY_LABELS).get(strategy, strategy)


def retention_label(days: int) -> str:
    return dict(RETENTION_CHOICES).get(days, f"{days} days")
