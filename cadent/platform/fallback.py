"""The stand-in platform for any OS without a real adapter module.

Any OS that is neither win32 nor darwin gets this — and `darwin.py` borrows
these stand-ins for the seams its tickets (now just #146's hardware probes)
have not yet filled:
inert adapters that preserve the old modules' dev-mode behaviour —
injection prints instead of typing, the hotkey tap hears nothing, probes
describe no hardware — and a Capabilities table that mirrors win32's portable
values so behaviour off Windows is byte-for-byte what it was before the seam.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from .. import config
from .base import Capabilities, Platform
from .gpu_packs import WIN32_EDITIONS
from .keycodes import WIN32_KEYCODES

log = logging.getLogger(__name__)


class NullKeyboard:
    def send_text_units(self, units: list[int]) -> bool:
        raw = b"".join(u.to_bytes(2, "little") for u in units)
        text = raw.decode("utf-16-le", errors="replace")
        print(f"[inject] (dev mode) would type: {text!r}")
        return True

    def send_chord(self, keys: list[int]) -> None:
        pass

    def send_mask_key(self) -> None:
        pass

    def modifiers_down(self) -> bool:
        return False


class MemoryClipboard:
    """A process-local clipboard: keeps the paste rung harmless in dev mode
    and gives app-logic tests a real sequence number to assert against."""

    def __init__(self) -> None:
        self._text: str | None = None
        self._seq = 0

    def get_text(self) -> str | None:
        return self._text

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        self._text = text
        self._seq += 1

    def sequence_number(self) -> int:
        return self._seq


class NullFocusedApp:
    def name(self) -> str:
        return "dev"

    def window_rect(self) -> tuple[int, int, int, int] | None:
        return None

    def injection_blocked(self) -> str | None:
        return None

    def permission_granted(self) -> bool:
        return True     # no preflight here, so nothing can be missing

    def running_apps(self) -> list[tuple[str, str]]:
        return []

    def display_name(self, identity: str) -> str | None:
        return None


class NullHotkeyTap:
    """Hears nothing. Off Windows the old pynput listener's win32_event_filter
    never fired either, so a silent tap is the same behaviour without the
    thread (#129: the listener lies — `running == True` while deaf)."""

    def start(self, on_event, chords=()) -> None:
        pass

    def stop(self) -> None:
        pass

    def bound_shortcuts(self):
        return None    # the hook sees the whole keyboard; nobody else binds

    def available(self) -> bool:
        return True


class NullHardware:
    def cuda_total_memory(self) -> float | None:
        return None

    def dx12_gpu_present(self) -> bool:
        return False

    def processor_name(self) -> str:
        return ""

    def nvidia_driver_present(self) -> bool:
        return False

    def metal_gpu_present(self) -> bool:
        return False

    def cuda_driver_version(self) -> int | None:
        return None


class NullAutostart:
    def run_command(self) -> str:
        return ""

    def set_enabled(self, enabled: bool) -> None:
        log.debug("autostart has no adapter on this platform; ignoring set(%s)",
                  enabled)

    def is_enabled(self) -> bool:
        return False



class NullSingleInstance:
    def acquire(self) -> bool:
        return True

    def notify_running(self) -> bool:
        return False    # a tray icon is always there to click

    def watch(self, on_second_launch) -> None:
        pass


class NullDesktop:
    def open_path(self, path: Path) -> None:
        try:                             # pragma: no cover - dev convenience
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def request_permission(self) -> None:
        pass    # no permission preflight, nothing to ask for

    def text_scale_factor(self) -> float:
        return 1.0

    def high_contrast(self) -> bool:
        return False

    def animations_enabled(self) -> bool:
        return True

    def tray_ink(self) -> str:
        """White, because the panels this placeholder meets are usually dark
        and an invisible tray icon is worse than a slightly wrong one. There
        is no portable way to ask, so this is a guess and stays one until a
        real Linux column exists."""
        return "#ffffff"

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        pass    # nothing portable to watch

    def stop_watching_tray_ink(self) -> None:
        pass


# Mirrors win32's values on purpose: these are the portable facts the app ran
# on everywhere before the seam existed. The darwin column of spec §1.3 arrives
# with #144-#146, not with this placeholder.
CAPABILITIES = Capabilities(
    keycode_table=WIN32_KEYCODES,
    default_injection_strategy="type",
    injection_rungs=("type", "paste"),
    paste_chord="ctrl+v",
    default_overrides=tuple(config._default_overrides()),
    default_override_reasons=config.DEFAULT_OVERRIDE_REASONS,
    auto_learn_overrides=True,
    stt_runtimes=config.STT_RUNTIMES,
    gpu_only_engines=frozenset({"parakeet"}),
    show_runtime_combo=True,
    gpu_pack_available=True,
    gpu_pack_editions=WIN32_EDITIONS,
    parakeet_cpu_floor=None,
    permission=None,
    autostart_label="Start with Windows",
    app_identity_placeholder="app.exe",
    app_picker=False,
    mic_permission_hint=None,
    tray_click_toggles_pause=True,
    modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                       "option": "Alt", "win": "Win", "cmd": "Win"},
    tray_icon_painted_by_os=False,
    # The exception to mirroring win32: copy that *names an OS setting* has no
    # portable Windows answer — a Linux user reading "your Windows app colour
    # mode" is the app claiming a platform it isn't on. Neutral until an
    # adapter arrives with the real names.
    theme_subtitle="Follows your system colour mode",
    high_contrast_reason=("Your system is using a contrast theme, so Cadent "
                          "is following its colours"),
    overlay="windowed",
    per_app_overrides=True,
    support_tier=None,
    support_tier_summary=None,
    default_combo="<ctrl>+<cmd>",
    default_cleanup_combo="<ctrl>+<shift>+<alt>",
)


def create() -> Platform:
    return Platform(
        capabilities=CAPABILITIES,
        keyboard=NullKeyboard(),
        clipboard=MemoryClipboard(),
        focused_app=NullFocusedApp(),
        hotkey_tap=NullHotkeyTap(),
        hardware=NullHardware(),
        autostart=NullAutostart(),
        single_instance=NullSingleInstance(),
        desktop=NullDesktop(),
    )
