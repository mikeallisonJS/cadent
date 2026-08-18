"""The platform seam: eight small protocols, a table of facts, one bundle.

Policy stays portable; platforms supply primitives and facts (ADR 0005).
Everything the rest of the app may know about an operating system is either
a method on one of these protocols or a field on `Capabilities` — UI,
settings, and wizard code decide what to *show* from the table alone, and no
`sys.platform` branch exists outside this package. A surface the table has
switched on may then ask the adapters live questions the table cannot hold
(#148: is the permission granted, what is running, what is this app called)
— the fact gates, the adapter answers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..config import AppOverride


@dataclass(frozen=True)
class KeycodeTable:
    """One OS's key namespace as plain data (#131): VK ints on Windows,
    Carbon/CGEvent ints on macOS — the two never mix in one table.

    `chord.parse_combo` and `inject.parse_paste_chord` consume it, so either
    OS's chord logic tests anywhere by passing the other table explicitly.
    """

    groups: Mapping[str, frozenset[int]]    # "<ctrl>" → every keycode that satisfies it
    modifiers: Mapping[str, int]            # "ctrl" → the code a synthetic chord presses
    chars: Mapping[str, int]                # "a" → keycode
    function_keys: Mapping[str, int]        # "f1" → keycode
    # Windows VKs for A-Z/0-9 are the ASCII uppercase codes, and today's parser
    # accepts *any* single character through ord(); keep that contract where it
    # was true rather than silently narrowing what a hand-edited config may say.
    ord_fallback: bool = False

    def group_for(self, part: str) -> frozenset[int] | None:
        """The keycode group a combo part names, or None if unrecognized."""
        if part in self.groups:
            return self.groups[part]
        if part in self.chars:
            return frozenset({self.chars[part]})
        if part in self.function_keys:
            return frozenset({self.function_keys[part]})
        if self.ord_fallback and len(part) == 1:
            return frozenset({ord(part.upper())})
        return None


@dataclass(frozen=True)
class PermissionPreflight:
    """The one OS grant Cadent cannot work without, as the platform describes
    it (ADR 0012): Accessibility on darwin, the portal grant on Linux's
    Wayland tiers. Carries the words every surface renders, so no permission
    copy lives in cross-platform UI modules — a surface gates on
    `Capabilities.permission` being set and reads the strings from here."""

    name: str            # "accessibility" / "portal"
    banner: str          # the Settings banner while the grant is missing
    wizard_body: str     # the wizard step's explanation
    action_label: str    # the button that calls `DesktopEnv.request_permission`
    waiting: str         # wizard status while the grant is missing
    granted: str         # wizard status once it lands


@dataclass(frozen=True)
class GpuPackEdition:
    """One downloadable GPU support pack (ADR 0010): the CUDA userspace an
    engine dlopens, extracted from PyPI `nvidia-*` wheels into the data dir.
    One surface (wizard page, tray item), engine-keyed editions — Windows
    ships the cuBLAS-12 pair for faster-whisper; Linux adds the CUDA-13
    stack for Parakeet."""

    key: str                                    # "cublas12" / "cuda13"
    engine: str                                 # the engine it serves
    files: tuple[str, ...]                      # basenames (globs allowed) to extract
    sources: tuple[tuple[str, str], ...]        # (PyPI package, version or "13." prefix)
    wheel_tag: str                              # "win_amd64" / "manylinux"
    size: str                                   # disclosed download size
    subdir: str                                 # under CUDA_DIR ("" = the dir itself)
    activation: str                             # "path" (PATH prepend) / "preload" (RTLD_LOCAL)
    min_driver_cuda: int | None = None          # cuDriverGetVersion() floor, e.g. 13000
    driver_hint: str | None = None              # the row when the driver is too old


@dataclass(frozen=True)
class Capabilities:
    """Platform *facts* (spec §1.3). If a surface needs a platform fact not
    listed here, it goes in this table, not in an `if sys.platform` branch.
    Facts decide what renders; live answers (a grant's presence, the running
    apps) come from the adapters the fact switched on."""

    keycode_table: KeycodeTable
    # "type" on win32; "clipboard" on darwin — pastes by default (ADR 0001).
    # T3's naming call (#144): the strategy enum keeps "clipboard"; "paste"
    # stays the *rung* name in `injection_rungs` and in copy.
    default_injection_strategy: str
    # The automatic fall-through chain the typing path walks, in order (§1.2):
    # ("type", "paste") on win32; darwin pastes with no automatic fall-through.
    injection_rungs: tuple[str, ...]
    paste_chord: str                         # "ctrl+v" on win32, "cmd+v" on darwin
    default_overrides: tuple[AppOverride, ...]
    default_override_reasons: Mapping[str, str]
    auto_learn_overrides: bool               # paste has no detectable failure on darwin
    stt_runtimes: Mapping[str, tuple[str, ...]]   # per-engine runtime ladder choices
    gpu_only_engines: frozenset[str]
    show_runtime_combo: bool
    gpu_pack_available: bool                 # wizard page + tray items exist at all
    # The editions this platform can download, by engine (ADR 0010); empty
    # where the build ships its acceleration (darwin). `gpu_pack_available`
    # is the same fact as "this is non-empty", kept for the surfaces.
    gpu_pack_editions: Mapping[str, GpuPackEdition]
    # The CPU floor at which Parakeet v2 is recommended over the Whisper rows
    # on a machine with no usable NVIDIA GPU: (physical cores, RAM GB), or
    # None where that branch was never benched (win32 today). Linux carries
    # (4, 8.0) from docs/research/linux-parakeet-cpu-bench.md.
    parakeet_cpu_floor: tuple[int, float] | None
    # None, or the grant this OS needs (Accessibility on darwin, the portal
    # grant on Linux's Wayland tiers) with the words its surfaces render.
    # Gating stays a truthiness check; the app polls `permission_granted()`.
    permission: PermissionPreflight | None
    autostart_label: str
    app_identity_placeholder: str            # "app.exe" / "com.example.app"
    app_picker: bool                         # running-apps picker vs free text only
    # What a capture of pure zeros means here, as the words to surface — or
    # None where silence is just silence. On darwin a missing Microphone TCC
    # grant raises no exception and records zeros (§7.7).
    mic_permission_hint: str | None
    # Left-clicking the tray icon is a spare gesture on win32 (the menu lives
    # on right-click), so it flips pause (§3.3). On darwin the same click
    # opens the menu — and Qt still emits activated(Trigger) for it — so the
    # click belongs to the menu, never to a toggle (#160).
    tray_click_toggles_pause: bool
    # What a stored chord part is *called* here: "<cmd>" is the Win key on
    # win32 and the Cmd key on darwin, "<alt>" is Option there. Copy that
    # names a chord renders through this (chord.describe_combo, #166).
    modifier_captions: Mapping[str, str]
    # Shape carries the tray state everywhere — flow lines, pause bars, the
    # exclamation — and colour says nothing about it on either OS. What
    # differs is who paints the silhouette: darwin adapts a mask to the menu
    # bar itself, so it wants one shipped as a mask and picks the ink. Where
    # this is false nobody paints it for us and `DesktopEnv.tray_ink` says
    # what to use (supersedes #164, which made this a darwin-only story).
    tray_icon_painted_by_os: bool
    # What "Follow system" actually follows, named the way the host OS names
    # it: Windows has two colour-mode settings and Qt reports the *app* one,
    # while macOS has one Appearance control. Copy that names an OS setting
    # is a platform fact, not a string constant.
    theme_subtitle: str
    # Why the theme control appears inert while a contrast setting is on —
    # same reason, two OS vocabularies (contrast theme / increased contrast).
    high_contrast_reason: str
    # ---- the tier-shaped facts (ADR 0013/0014, spec M6 §2.4) ---------------
    # What kind of overlay this platform can show: "windowed" (today's
    # overlay.py, Move mode included) on win32/darwin and Linux X11; None on
    # the Wayland tiers, where failure feedback goes to `tray.message()`
    # instead; "anchored" is reserved for a layer-shell helper. Overlay
    # construction, the Move-overlay button and the position rows all gate on
    # `overlay == "windowed"`.
    overlay: str | None
    # Whether per-app overrides and auto-learn apply in this session — False
    # only where the focused app cannot be named (GNOME Wayland). The pane
    # stays editable regardless; this only decides the note it carries.
    per_app_overrides: bool
    # Linux's support tier ("whole" / "portal" / "reduced"), None elsewhere;
    # `support_tier_summary` is the adapter-built sentence Settings ▸ General
    # and the wizard's Done page show — never assembled by UI code.
    support_tier: str | None
    support_tier_summary: str | None
    # The chords this platform ships as defaults for the dictation hotkey and
    # the cleanup tap. Platform facts rather than `Config` literals because
    # the freedesktop shortcuts grammar needs a keysym: a modifier-only chord
    # is unbindable on Wayland, so those tiers default differently.
    default_combo: str
    default_cleanup_combo: str


class KeyboardOutput(Protocol):
    """Synthetic keyboard primitives; the injection ladder stays in inject.py."""

    def send_text_units(self, units: list[int]) -> bool:
        """Type UTF-16 code units. False on a detectable short send."""
        ...

    def send_chord(self, keys: list[int]) -> None:
        """Press and release keycodes as one chord (the paste chord)."""
        ...

    def send_mask_key(self) -> None:
        """The Start-menu MenuMaskKey trick; a no-op where menus don't pop."""
        ...

    def modifiers_down(self) -> bool:
        """Is any modifier key physically held right now?"""
        ...


class Clipboard(Protocol):
    def get_text(self) -> str | None: ...

    def set_text(self, text: str, exclude_from_history: bool) -> None: ...

    def sequence_number(self) -> int:
        """A counter that moves whenever anyone writes the clipboard — the
        guard for restoring only what nobody else touched."""
        ...


class FocusedApp(Protocol):
    def name(self) -> str:
        """The focused app's identity — executable name on Windows, bundle id
        on macOS — or the shared "unknown" sentinel."""
        ...

    def window_rect(self) -> tuple[int, int, int, int] | None:
        """(left, top, right, bottom) of the focused window, or None."""
        ...

    def injection_blocked(self) -> str | None:
        """A human-readable reason synthetic input cannot land (elevated
        window under UIPI, secure event input), or None when clear."""
        ...

    def permission_granted(self) -> bool:
        """Is `Capabilities.permission`'s grant present? True where
        there is no preflight, and on a *failed* probe — a broken check must
        nag nobody."""
        ...

    def running_apps(self) -> list[tuple[str, str]]:
        """(display name, identity) of the apps a user could target — the
        running regular-activation-policy apps on macOS (§5.2), the
        *installed* applications (`.desktop` files, ADR 0009) on Linux, where
        every tier can read them and nobody knows a desktop-file id by heart.
        Empty where the overrides pane lists process names instead
        (`app_picker` False)."""
        ...

    def display_name(self, identity: str) -> str | None:
        """The human name behind a stored identity — resolved against what is
        running right now on macOS, against the installed `.desktop` entries
        on Linux (a closed app's history row still reads "Firefox") — or
        None, and rows render the raw identity."""
        ...


class HotkeyTap(Protocol):
    """The OS event hook. Feeds raw key events; chord.py stays pure."""

    def start(self, on_event: Callable[[int, bool, bool], None],
              chords: tuple[str, ...] = ()) -> None:
        """Begin listening; on_event(keycode, is_down, injected) is called on
        the hook's own thread and must stay fast. `chords` are the combos the
        caller will recognize (dictation, cleanup tap): a whole-keyboard hook
        (win32, darwin, X11) ignores them; a portal that binds named
        shortcuts (Linux Wayland) registers exactly these and synthesizes
        the parsed keysym events they stand for."""
        ...

    def stop(self) -> None: ...

    def bound_shortcuts(self) -> Mapping[str, str] | None:
        """Where the *desktop* owns the binding (Linux's Wayland tiers), the
        chords it actually bound — {"dictate": "Ctrl+Super+Space", ...} as
        the compositor describes them — so the Hotkeys pane can show them
        instead of pretending the text field is authoritative (ADR 0008).
        None wherever the hook sees the raw keyboard (win32, darwin, X11)."""
        ...

    def available(self) -> bool:
        """Can a hotkey be armed on this desktop at all? False only where
        the interface that would carry it is missing (a Wayland compositor
        whose portal ships no GlobalShortcuts) — the app then shows the
        `hotkey-unavailable` fault. True everywhere else."""
        ...


class HardwareProbe(Protocol):
    def cuda_total_memory(self) -> float | None: ...

    def dx12_gpu_present(self) -> bool: ...

    def processor_name(self) -> str: ...

    def nvidia_driver_present(self) -> bool: ...

    def metal_gpu_present(self) -> bool:
        """A Metal GPU the llama.cpp build can offload to — true on every
        Apple Silicon Mac, false everywhere else (#146)."""
        ...

    def cuda_driver_version(self) -> int | None:
        """`cuDriverGetVersion()` off the NVIDIA driver (13000 = CUDA 13.0),
        or None with no driver. Gates the CUDA-13 pack edition (ADR 0010:
        R580+ drivers only)."""
        ...


class Autostart(Protocol):
    def run_command(self) -> str: ...

    def set_enabled(self, enabled: bool) -> None: ...

    def is_enabled(self) -> bool: ...



class SingleInstance(Protocol):
    def acquire(self) -> bool:
        """True when we are the only Cadent running. The claim must die
        with the process — no stale state to clean up."""
        ...

    def notify_running(self) -> bool:
        """The *second* launch's move after `acquire()` failed: tell the
        running instance to show itself. True if delivered. A no-op (False)
        where a tray icon is always there to click (win32, darwin); Linux
        signals the holder of the lock, because a tray-less desktop has no
        other door (spec M6 §10.2)."""
        ...

    def watch(self, on_second_launch: Callable[[], None]) -> None:
        """The *first* instance's side: `on_second_launch` fires when another
        launch called `notify_running()`. On the watcher's own thread; the
        app marshals. A no-op where nothing notifies."""
        ...


class DesktopEnv(Protocol):
    def open_path(self, path: Path) -> None:
        """Open a file in the user's associated app; never blocks, never raises."""
        ...

    def request_permission(self) -> None:
        """Ask for `Capabilities.permission`'s grant: deep-link to System
        Settings ▸ Accessibility on darwin, issue the portal requests on
        Linux. Fire-and-forget — never blocks, never raises; the outcome is
        read back through `FocusedApp.permission_granted()`. A no-op where
        there is no preflight."""
        ...

    def text_scale_factor(self) -> float: ...

    def high_contrast(self) -> bool: ...

    def animations_enabled(self) -> bool: ...

    def tray_ink(self) -> str:
        """The colour the tray mark must be painted to read against this OS's
        tray surface, as "#rrggbb".

        Deliberately *not* the app's colour mode. The tray sits on the
        taskbar, which Windows themes with its own setting, and Cadent's own
        Light/Dark preference must never reach it — a user could otherwise
        make their own tray icon invisible by picking a theme. Never called
        where `Capabilities.tray_icon_painted_by_os` is true."""
        ...

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        """Begin watching for changes to what `tray_ink()` would return.

        Like `HotkeyTap.start`, `on_change` arrives on the watcher's own
        thread and must stay fast — the tray lives on the GUI thread, so
        callers marshal. A no-op where the OS paints the mark."""
        ...

    def stop_watching_tray_ink(self) -> None: ...


@dataclass(frozen=True)
class Platform:
    """One OS, assembled: the eight adapters plus the fact table."""

    capabilities: Capabilities
    keyboard: KeyboardOutput
    clipboard: Clipboard
    focused_app: FocusedApp
    hotkey_tap: HotkeyTap
    hardware: HardwareProbe
    autostart: Autostart
    single_instance: SingleInstance
    desktop: DesktopEnv
