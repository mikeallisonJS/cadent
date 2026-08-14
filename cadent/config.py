"""Configuration: the shape of config.json and the tolerant parse into it.

`Config` is a plain dataclass and the app's running truth — subsystems hold
references *into* it (the injector shares the very same `app_overrides` list).
The file is its projection, and `config_store.ConfigStore` owns every byte of
I/O: this module never reads or writes a file. That split is what lets a write
be a per-key delta instead of a whole-file rewrite that erases hand edits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Cadent"
DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))
CONFIG_PATH = DATA_DIR / "config.json"
VOCAB_PATH = DATA_DIR / "vocabulary.json"
SNIPPETS_PATH = DATA_DIR / "snippets.json"
DB_PATH = DATA_DIR / "history.db"
MODELS_DIR = DATA_DIR / "models"
CUDA_DIR = DATA_DIR / "cuda"        # optional GPU support pack (cuBLAS DLLs)

INJECTION_STRATEGIES = {"type", "clipboard", "clipboard-no-restore", "notify-only"}

# Permanent read alias (spec §2): configs written before the macOS port say
# "sendinput" for the strategy now called "type". Old files keep loading
# forever; anything we write says "type".
_STRATEGY_ALIASES = {"sendinput": "type"}


def canonical_strategy(strategy: str) -> str:
    """The current spelling of an injection strategy, aliases resolved."""
    return _STRATEGY_ALIASES.get(strategy, strategy)

# The speech engines, and the runtimes each can actually be pointed at (#72).
# Validity lives here with INJECTION_STRATEGIES; settings.py layers the
# curation and the plain-language labels on top. `directml` is Parakeet-only
# and `cuda` means two different stacks — ctranslate2's cuBLAS on the Whisper
# path, ONNX Runtime's CUDA provider on the Parakeet one.
STT_ENGINES = ("faster-whisper", "parakeet")
STT_RUNTIMES: dict[str, tuple[str, ...]] = {
    "faster-whisper": ("auto", "cuda", "cpu"),
    "parakeet": ("auto", "directml", "cuda", "cpu"),
}

# Where cleanup can run (#116). One engine, so one flat tuple rather than the
# per-engine mapping above — and two values, not three: `auto` reaches for the
# GPU and drops to the processor when it can't, so a third "GPU please" would
# be the same ladder under another name. See cleanup.RUNTIME_LADDERS.
LLM_RUNTIMES = ("auto", "cpu")


@dataclass
class AppOverride:
    """Per-app injection override (see docs/agents + M1 spec)."""

    process: str                        # executable name, matched case-insensitively
    strategy: str = "clipboard"         # one of INJECTION_STRATEGIES
    # Chord sent for clipboard strategies; "" means the platform's own
    # (Capabilities.paste_chord: Ctrl+V on Windows, Cmd+V on macOS). A stored
    # literal is a user's explicit choice and is sent verbatim.
    paste_chord: str = ""
    chunk_size: int = 0                 # 0 = whole utterance in one SendInput batch
    chunk_delay_ms: int = 0
    settle_delay_ms: int = 150          # wait after paste before restoring clipboard
    restore_clipboard: bool = True
    learned: bool = False               # auto-learned (#45); informational marker


_TERMINALS = (
    "windowsterminal.exe", "wt.exe", "conhost.exe", "openconsole.exe",
    "mintty.exe", "alacritty.exe", "wezterm-gui.exe",
)


def _capabilities():
    """The platform fact table, imported lazily: platform modules import this
    module at load, so a top-level import here would be a cycle. Only default
    factories call this, and only after both modules exist."""
    from . import platform

    return platform.current().capabilities


def _seed_overrides() -> list[AppOverride]:
    """Fresh copies of the platform's seed rules. Capabilities holds one
    shared tuple of instances; a config's list is live and mutated in place
    by the pane, so handing out the originals would let one config's edits
    bleed into every later fresh config (and into Restore defaults)."""
    return [replace(o) for o in _capabilities().default_overrides]


def _default_overrides() -> list[AppOverride]:
    overrides = [
        AppOverride(process=p, strategy="clipboard", paste_chord="ctrl+shift+v")
        for p in _TERMINALS
    ]
    overrides.append(AppOverride(process="mstsc.exe", strategy="clipboard"))
    # Win11 Notepad's rewritten text stack scrambles fast synthetic unicode
    # input (batched: 0/3 clean in the ticket-17 sweep; even 20 ms/char
    # reordered once). Clipboard paste was 3/3 clean and instant — but a cold
    # Notepad can take >150 ms to process the paste, and restoring the old
    # clipboard before then pastes the wrong thing; hence the long settle.
    overrides.append(AppOverride(process="notepad.exe", strategy="clipboard",
                                 settle_delay_ms=500))
    return overrides


# Why each shipped rule exists, in the user's words. The App overrides pane
# shows these when one is deleted (M4 §5.5) — needed because most of them
# cannot be re-learned: auto-learn fires only on a *detectable* SendInput
# failure, and Notepad's failure is scrambled-yet-successful input that
# reports fine. Recognising these process names needs no stored flag, because
# _default_overrides() is a code constant.
DEFAULT_OVERRIDE_REASONS: dict[str, str] = {
    **{p: "Cadent ships this rule because terminals don't bind Ctrl+V."
       for p in _TERMINALS},
    "mstsc.exe": "Cadent ships this rule because Remote Desktop drops "
                 "synthetic keystrokes.",
    "notepad.exe": "Cadent ships this rule because Notepad scrambles "
                   "typed text.",
}

# Seeded into a *new* config.json only (M4 §7.6). vocabulary.json and
# snippets.json carry the same pair; config's says the opposite about when
# edits apply, which is exactly why it belongs in the file. Existing files are
# never backfilled — re-adding a comment the user deleted needs a marker key,
# which is worse than the gap.
SEED_COMMENTS: dict[str, str] = {
    "_comment": (
        "Cadent settings. Every value here is also reachable from the "
        "Settings window. Keys starting with an underscore are ignored."
    ),
    "_editing": (
        "Edit this file freely. Cadent preserves your edits and never "
        "rewrites keys it didn't change. Unlike vocabulary.json, changes "
        "here apply the next time Cadent starts."
    ),
}


@dataclass
class Config:
    # Hotkey. Modifier-only chords supported; see chord.py for parsing.
    hotkey: str = "<ctrl>+<cmd>"          # Ctrl + Win
    hotkey_mode: str = "hold"             # "hold" | "toggle"
    min_hold_ms: int = 200                # hold shorter than this discards the utterance
    paused: bool = False                  # persisted pause state

    # Audio
    input_device: str | None = None       # None = system default
    sample_rate: int = 16_000
    max_utterance_seconds: int = 120

    # STT
    stt_engine: str = "faster-whisper"
    stt_model: str = "distil-small.en"    # ticket-03 switch, taken 2026-07-28: real-dictation latency straddled 2 s on small.en
    stt_device: str = "auto"              # "auto" | "cuda" | "cpu"

    # Vocabulary correction (M2 ticket 04): fuzzy-rewrite similarity gates,
    # length-scaled. Restart-to-apply, like the rest of config.json.
    vocab_fuzzy_threshold: float = 0.85         # spans of >=5 chars
    vocab_fuzzy_threshold_short: float = 0.90   # 4-char spans (<=3 never fuzzy)

    # Cleanup. The model is a config setting; the default is the M2 ticket-02
    # benchmark winner. A tap chord toggles it (see TapChord).
    cleanup_mode: bool = False
    cleanup_hotkey: str = "<ctrl>+<shift>+<alt>"
    llm_model_path: str = str(MODELS_DIR / "llm" / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf")
    llm_max_tokens: int = 1024
    # Not `llm_device`, for all that it would pair with `stt_device`:
    # CONTEXT.md's Runtime term says to avoid "device", and `stt_device`'s
    # spelling is a compatibility debt rather than a pattern to copy.
    llm_runtime: str = "auto"             # "auto" | "cpu" (#116)

    # Injection. Both defaults are the platform's Capabilities column (ADR
    # 0001): Windows types with seed rules, macOS pastes with none.
    injection_method: str = field(default_factory=lambda: _capabilities()
                                  .default_injection_strategy)
    app_overrides: list[AppOverride] = field(
        default_factory=lambda: _seed_overrides())

    # History
    history_enabled: bool = True
    history_retention_days: int = 0       # 0 = keep forever (PRD 5.6)

    # Shell
    autostart: bool = False               # run at login; off by default (M3 charter)
    setup_complete: bool = False          # the first-run wizard finished (M4 §6.4)

    # Appearance (M4 §1.2). "system" follows the Windows *app* colour mode.
    theme: str = "system"                 # "system" | "light" | "dark"

    # Overlay pill (M4 §4.6/§4.8). The toggle governs the *activity* states
    # only — failure states appear regardless, so turning off the indicator
    # never turns off the alarm.
    show_overlay: bool = True
    # The anchor is normalized — a fraction of available geometry, not
    # absolute pixels — so a custom position survives a monitor being
    # unplugged and still follows the focused window's screen.
    overlay_position_custom: bool = False
    overlay_anchor_x: float = 0.5
    overlay_anchor_y: float = 1.0
    overlay_snap: bool = True             # soft-snap to centre and bottom margin

    def to_raw(self) -> dict:
        """The dataclass as plain JSON types. Used to seed a *new* file only —
        an existing file is only ever delta-written (see config_store)."""
        return asdict(self)


@dataclass(frozen=True)
class SanitizeIssue:
    """A field config.json set to something unusable, and what we used instead.

    Under wholesale save the next save quietly repaired these. Under delta
    writes it never does, so the bad value would sit in the file and be
    silently overridden every start — hence a report the settings pane shows
    (M4 §7.5).
    """

    field: str
    file_value: object
    used: object


def parse(raw: dict) -> tuple[Config, list[SanitizeIssue]]:
    """Build a Config from a raw JSON dict, tolerantly.

    Unknown keys and `_comment`s are ignored here and preserved on disk by the
    store. Wrong-typed fields fall back to their defaults — a typo can never
    brick the app — and each one is reported rather than silently corrected.
    """
    known = {f.name for f in fields(Config)}
    kwargs = {k: v for k, v in raw.items() if k in known}
    supplied = dict(kwargs)
    kwargs["app_overrides"] = _parse_overrides(kwargs.get("app_overrides"))
    return _sanitize(Config(**kwargs), supplied)


def _sanitize(cfg: Config, supplied: dict) -> tuple[Config, list[SanitizeIssue]]:
    defaults = Config()
    issues: list[SanitizeIssue] = []

    def reset(name: str, default: object) -> None:
        if name in supplied:
            issues.append(SanitizeIssue(name, supplied[name], default))
        setattr(cfg, name, default)

    for f in fields(cfg):
        if f.name in ("app_overrides", "input_device"):
            continue
        default = getattr(defaults, f.name)
        value = getattr(cfg, f.name)
        # bool is a subclass of int, so an isinstance check alone lets
        # `"paused": 1` through as True on an int-typed neighbour's coattails.
        if isinstance(default, bool) and not isinstance(value, bool):
            reset(f.name, default)
        elif isinstance(default, float) and isinstance(value, int) \
                and not isinstance(value, bool):
            setattr(cfg, f.name, float(value))     # 1 for 1.0 is not a typo
        elif not isinstance(value, type(default)) or (
                isinstance(value, bool) and not isinstance(default, bool)):
            reset(f.name, default)

    if cfg.input_device is not None and not isinstance(cfg.input_device, str):
        reset("input_device", None)
    if cfg.hotkey_mode not in ("hold", "toggle"):
        reset("hotkey_mode", defaults.hotkey_mode)
    if cfg.theme not in ("system", "light", "dark"):
        reset("theme", defaults.theme)
    # Engine before runtime: the runtime is only meaningful relative to the
    # engine that will actually run, so a bad engine must not condemn a
    # perfectly good runtime along with it.
    if cfg.stt_engine not in STT_ENGINES:
        reset("stt_engine", defaults.stt_engine)
    # Judged against the *platform's* runtime column, not the win32 table
    # above: on darwin's one-rung ladder (ADR 0003) a carried-over "cuda" or
    # "directml" sanitizes back to `auto` rather than quietly meaning nothing.
    if cfg.stt_device not in _capabilities().stt_runtimes[cfg.stt_engine]:
        reset("stt_device", defaults.stt_device)
    if cfg.llm_runtime not in LLM_RUNTIMES:
        reset("llm_runtime", defaults.llm_runtime)
    # The read alias, not a sanitize issue: "sendinput" was the documented
    # spelling for years, so it is honoured silently rather than reported.
    cfg.injection_method = canonical_strategy(cfg.injection_method)
    return cfg, issues


def _parse_overrides(raw: object) -> list[AppOverride]:
    """Tolerant parse: skip malformed entries, ignore unknown keys."""
    if not isinstance(raw, list):
        return _seed_overrides()
    known = {f.name for f in fields(AppOverride)}
    result: list[AppOverride] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("process"):
            continue
        override = AppOverride(**{k: v for k, v in entry.items() if k in known})
        if isinstance(override.strategy, str):
            override.strategy = canonical_strategy(override.strategy)
        result.append(override)
    return result
