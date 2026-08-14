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
from pathlib import Path

from .. import config
from .base import Capabilities, Platform
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

    def start(self, on_event) -> None:
        pass

    def stop(self) -> None:
        pass


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


class NullDesktop:
    def open_path(self, path: Path) -> None:
        try:                             # pragma: no cover - dev convenience
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def open_permission_settings(self) -> None:
        pass    # no permission_preflight, nowhere to link

    def text_scale_factor(self) -> float:
        return 1.0

    def high_contrast(self) -> bool:
        return False

    def animations_enabled(self) -> bool:
        return True


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
    permission_preflight=None,
    autostart_label="Start with Windows",
    app_identity_placeholder="app.exe",
    app_picker=False,
    mic_permission_hint=None,
    tray_click_toggles_pause=True,
    modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                       "option": "Alt", "win": "Win", "cmd": "Win"},
    tray_icon_is_template=False,
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
