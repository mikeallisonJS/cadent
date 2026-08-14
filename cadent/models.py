"""The one registry of downloadable models — speech and cleanup (#111, #112).

Settings, the first-run wizard and the downloader all read this table. Before
it there were three: `settings.STT_MODEL_CHOICES` had the ids, `wizard.
MODEL_SIZES` had the sizes, `cleanup.GGUF_REPOS` had the repos, and nothing
had the prose. A user picking a model was choosing between `distil-medium.en`
and `parakeet-tdt-0.6b-v3`, which is a filename, not a choice.

So every row here carries the four things a choice needs: a **tier** that says
where it sits on the ladder, a **name** that is googleable, a **blurb** that
says what you get, and a **size** that says what it costs.

Two rules the surfaces share rather than each inventing:

- **"Recommended" is applied at render, never stored.** It follows the machine
  — `hardware.suggest_for_this_machine()` for speech, the thresholds below for
  cleanup — so the same row is not recommended on every PC.
- **At most one chip per row, and a warning beats a badge.** A model worth
  warning about is by definition not the model we recommend.

The cleanup thresholds are **unmeasured**. No benchmark backs either number;
they are starting points, and they are constants rather than inline literals
so the next person can find and move them. The one exception is darwin's
(#146): the core gate is retired there on a real M1 Max measurement, not on
a guess — the numbers sit at the gate, below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The chips, in the words the user reads. Named because three surfaces assert
# on them and a fourth announces them to a screen reader.
RECOMMENDED = "Recommended"
NEEDS_A_GPU = "Needs a graphics card"
NOT_ENOUGH_MEMORY = "Not enough memory"
MAY_BE_SLOW = "May be slow on this PC"

# What a row says when config.json named a model this table doesn't list.
SET_IN_CONFIG = "Set in config.json"

# Whisper is the only engine that runs well on any PC, so it is what an
# unrecognised model name degrades to. `settings.DEFAULT_STT_ENGINE` is this
# same fact wearing the name the panes ask for it by.
DEFAULT_ENGINE = "faster-whisper"


class _Choice:
    """How any listed model reads: two lines, tier-first.

    A plain mixin rather than a shared dataclass base, so both tables keep
    their own field order and the entries below stay readable as positional
    rows.
    """

    tier: str
    name: str
    blurb: str
    size: str

    @property
    def title(self) -> str:
        """Line one: where it sits on the ladder, then what it is called."""
        return f"{self.tier} — {self.name}" if self.tier else self.name

    @property
    def subtitle(self) -> str:
        """Line two: what you get, then what it costs."""
        return "  ·  ".join(part for part in (self.blurb, self.size) if part)


@dataclass(frozen=True)
class SpeechModel(_Choice):
    """One row of the speech picker — and, for the unlisted ones, one model
    that still has to load when a hand-edited config.json names it."""

    id: str             # what config.json holds; also the engine's own name for it
    engine: str         # derived from the pick, never chosen separately (#111)
    # Where the weights come from. Here rather than in `stt.py` for the same
    # reason `CleanupModel.hf_repo` is here: the download, the picker and the
    # size disclosure are three views of one row, and a repo table kept beside
    # the engine is a second table to keep in agreement (#115).
    hf_repo: str
    tier: str
    name: str
    blurb: str
    size: str
    listed: bool = True


@dataclass(frozen=True)
class CleanupModel(_Choice):
    id: str             # the GGUF filename, which is also the download's name
    hf_repo: str
    tier: str
    name: str
    blurb: str
    size: str
    params_b: float     # billions — drives the "may be slow" rule
    # Resident cost, not parameter count: llama.cpp holds the whole quantised
    # file in memory, so this is what the free-RAM rule has to reason about.
    size_gb: float
    # Hybrid-thinking models emit a `<think>` block unless told not to, and we
    # pay for every token of it. Only Qwen3 1.7B is one (#112).
    no_think: bool = False


# Order is small-to-large within each engine, and load-bearing: the picker
# renders the listed rows in exactly this sequence, and `settings.
# model_choices` hands the ids out unchanged.
#
# The Whisper repos mirror faster-whisper's own `_MODELS` map, which is private
# and so mirrored rather than imported; `test_models` pins the two together so
# a version bump that moved one can never leave the other quietly wrong.
#
# **Sizes are every file the download actually fetches, in decimal MB** — the
# same bytes, in the same units, that `downloads.Progress` counts as it fetches
# them (#115), so a row and the bar under it read the same. Measured, not
# quoted: the three `distil-` rows previously understated themselves by up to
# 90% (`distil-large-v3` said 756 MB against a real 1.5 GB), which is a
# disclosure claiming the download is half what it is.
#
# Unlike the repo names below, these are **not pinned by a test** — checking
# them means asking Hugging Face, and no other test in this suite needs a
# network. They are a disclosure, re-measured by hand whenever a row's repo or
# patterns change, and that is the whole of the guarantee.
SPEECH_MODELS: tuple[SpeechModel, ...] = (
    SpeechModel("tiny.en", "faster-whisper", "Systran/faster-whisper-tiny.en",
                "Fastest", "Whisper Tiny",
                "Least accurate. For older or slower PCs.", "78 MB"),
    SpeechModel("base.en", "faster-whisper", "Systran/faster-whisper-base.en",
                "", "base.en", "", "148 MB", listed=False),
    # Display names drop the `distil-` prefix: it is a Hugging Face repo
    # naming convention, and it made three of the four Whisper rows read as
    # variants of something the list never showed.
    SpeechModel("distil-small.en", "faster-whisper",
                "Systran/faster-distil-whisper-small.en",
                "Fast", "Whisper Small",
                "Accurate enough for everyday dictation, and quick.", "336 MB"),
    SpeechModel("small.en", "faster-whisper", "Systran/faster-whisper-small.en",
                "", "small.en", "", "486 MB", listed=False),
    SpeechModel("distil-medium.en", "faster-whisper",
                "Systran/faster-distil-whisper-medium.en", "More accurate",
                "Whisper Medium", "Noticeably better on unclear speech. Slower.",
                "792 MB"),
    SpeechModel("medium.en", "faster-whisper", "Systran/faster-whisper-medium.en",
                "", "medium.en", "", "1.5 GB", listed=False),
    SpeechModel("distil-large-v3", "faster-whisper",
                "Systran/faster-distil-whisper-large-v3", "Most accurate",
                "Whisper Large", "Best quality. Slowest, and a large download.",
                "1.5 GB"),
    SpeechModel("large-v3", "faster-whisper", "Systran/faster-whisper-large-v3",
                "", "large-v3", "", "3.1 GB", listed=False),
    # int8 ONNX exports (#72); the encoder is the same 652 MB in both.
    SpeechModel("parakeet-tdt-0.6b-v2", "parakeet",
                "istupakov/parakeet-tdt-0.6b-v2-onnx", "Fast and accurate",
                "Parakeet v2", "Faster than Whisper at similar accuracy.",
                "661 MB"),
    # Said in half the width of the honest long version, which elided to
    # nothing once a chip shared the row.
    SpeechModel("parakeet-tdt-0.6b-v3", "parakeet",
                "istupakov/parakeet-tdt-0.6b-v3-onnx", "Multilingual",
                "Parakeet v3", "25 languages. Less accurate than v2 at English, "
                "which is all Cadent dictates.", "671 MB"),
)

# What wears the "Recommended" chip when `hardware.suggest_for_this_machine()`
# names a model the list doesn't show. Each unlisted rung points at the listed
# one nearest it *downwards*: the suggestion table steps down for a reason, and
# rounding up would spend RAM it has just decided the machine hasn't got.
LISTED_STANDIN: dict[str, str] = {
    "base.en": "tiny.en",
    "small.en": "distil-small.en",
    "medium.en": "distil-medium.en",
    "large-v3": "distil-large-v3",
}

# Order is lightest-first, and load-bearing: `recommended_cleanup` walks down
# it to the heaviest rung this machine clears.
#
# Repos and sizes from docs/research/llama-runtime.md. Adding the two Llama
# rungs takes on the Llama 3.2 Community License's "Built with Llama"
# attribution and license passthrough — a deliberate reversal of that doc's
# "Qwen3 rungs are license-simplest" recommendation, made because the ladder
# needs a 1B floor and a 3B middle that Qwen3 has no GGUF for (#112).
CLEANUP_MODELS: tuple[CleanupModel, ...] = (
    CleanupModel("Llama-3.2-1B-Instruct-Q4_K_M.gguf",
                 "bartowski/Llama-3.2-1B-Instruct-GGUF",
                 "Fastest", "Llama 3.2 1B",
                 "Light cleanup with almost no delay. Best on slower PCs.",
                 "808 MB", 1.0, 0.81),
    CleanupModel("Qwen3-1.7B-Q4_K_M.gguf", "unsloth/Qwen3-1.7B-GGUF",
                 "Fast", "Qwen3 1.7B",
                 "Better punctuation than the fastest option.",
                 "1.1 GB", 1.7, 1.11, no_think=True),
    CleanupModel("Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                 "bartowski/Llama-3.2-3B-Instruct-GGUF",
                 "Balanced", "Llama 3.2 3B",
                 "Handles messier, more rambling speech well.",
                 "2.0 GB", 3.0, 2.02),
    CleanupModel("Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
                 "unsloth/Qwen3-4B-Instruct-2507-GGUF",
                 "Best quality", "Qwen3 4B",
                 "Best cleanup quality. Adds the most delay.",
                 "2.5 GB", 4.0, 2.5),
)

# ---- the cleanup hardware rules --------------------------------------------
# Both thresholds are unmeasured. They ship as named constants precisely so
# that stays visible.

SLOW_CPU_CORES = 6          # fewer physical cores than this and heavy rungs drag
HEAVY_MODEL_B = 3.0         # "heavy" starts here, in billions of parameters

# The exception: on a Metal machine the core gate is retired outright, and
# that call is measured, not hedged (docs/research/macos-bench-m1max.md,
# #146) — the heaviest rung warm-cleans a short/medium/long transcript in
# 0.15/0.42/1.3 s on M1 Max full offload, so "may be slow" would be a false
# claim on any core count.
# Not "model size + a bit": llama.cpp holds the whole thing resident and
# Windows plus a browser wants most of what is left. Expressed as
# free-RAM-after-load because that is what decides whether the box swaps — a
# naive "model + 2 GB" rule only ever fired at 4 GB.
FREE_RAM_NEEDED_GB = 6.0


# ---- lookups ---------------------------------------------------------------

def speech_models(engine: str) -> tuple[SpeechModel, ...]:
    """Every model this engine can load, listed or not."""
    return tuple(m for m in SPEECH_MODELS if m.engine == engine)


def listed_speech_models() -> tuple[SpeechModel, ...]:
    """The rows the picker shows, in ladder order."""
    return tuple(m for m in SPEECH_MODELS if m.listed)


def speech_model(model_id: str) -> SpeechModel | None:
    return next((m for m in SPEECH_MODELS if m.id == model_id), None)


def cleanup_model(model_path: str) -> CleanupModel | None:
    """The rung this path names, or None for a bring-your-own-file path.

    Takes a path rather than an id because that is what `llm_model_path`
    holds, and every caller would otherwise write the same `Path(...).name`.
    """
    name = Path(model_path).name
    return next((m for m in CLEANUP_MODELS if m.id == name), None)


def engine_for_model(model_id: str) -> str:
    """Which engine owns this model name.

    Model names are unique across engines — `distil-small.en` could only ever
    be faster-whisper's — which is what lets one flat list of models pick an
    engine as a side effect (#111). An unknown name degrades to the default
    engine rather than raising: config.json is hand-editable.
    """
    model = speech_model(model_id)
    return model.engine if model is not None else DEFAULT_ENGINE


def recommended_speech_model(suggested: str) -> str:
    """The listed row that wears the chip for a hardware suggestion."""
    return LISTED_STANDIN.get(suggested, suggested)


# ---- how a row reads --------------------------------------------------------
# `title` and `subtitle` live on the models themselves; only the row for a
# model we have *no* entry for needs a function.

def unlisted_subtitle(model_id: str) -> str:
    """Line two for a model only config.json knows about.

    A known-but-unlisted size still has a size to disclose; a name we have
    never seen has only its provenance.
    """
    model = speech_model(model_id)
    size = model.size if model is not None else ""
    return "  ·  ".join(part for part in (SET_IN_CONFIG, size) if part)


# ---- what this machine should be offered ------------------------------------

def cleanup_warning(model: CleanupModel, ram_gb: float,
                    physical_cores: int, metal_gpu: bool = False) -> str:
    """The one chip this rung wears on this machine, or "".

    Memory outranks speed: "slow" is a complaint, "not enough memory" is the
    machine swapping mid-dictation. On a Metal machine the core gate is
    dropped — it mispredicts once the GPU does the work (ADR 0003) — while
    the RAM test stays: unified memory makes it more honest, not less,
    because the GPU can't page its way out of a too-big model.
    """
    if ram_gb <= 0:
        # The probe failed or hasn't run. A machine we know nothing about is
        # not a machine we get to warn about — every rung would wear "Not
        # enough memory", which is a claim, not a shrug.
        return ""
    if ram_gb - model.size_gb < FREE_RAM_NEEDED_GB:
        return NOT_ENOUGH_MEMORY
    if (not metal_gpu and physical_cores < SLOW_CPU_CORES
            and model.params_b >= HEAVY_MODEL_B):
        return MAY_BE_SLOW
    return ""


def recommended_cleanup(ram_gb: float, physical_cores: int,
                        metal_gpu: bool = False) -> str:
    """The heaviest rung that clears both thresholds, as an id.

    Falls back to the smallest rung rather than nothing: a dropdown with no
    recommendation is a dropdown that has given up on the user with the
    weakest machine, who needs the steer most. Does **not** change the stored
    default — this is a chip, applied at render.
    """
    fits = [m for m in CLEANUP_MODELS
            if not cleanup_warning(m, ram_gb, physical_cores, metal_gpu)]
    return (fits[-1] if fits else CLEANUP_MODELS[0]).id
