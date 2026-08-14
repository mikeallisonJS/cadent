"""Insert text into the focused window — the injection ladder, once, portably.

Implements the resolved "Text injection strategy" decisions:
- the platform's default rung order is data (`Capabilities.injection_rungs`):
  on Windows, batched unicode typing sent only after the hotkey modifiers are
  physically released, falling through to the paste rung
- a blocked foreground (elevated window under UIPI) → notify-only; failure
  there looks like success, and a synthetic paste chord is equally blocked
- paste rung: exclusion-format clipboard write, paste chord, settle delay,
  sequence-checked text-only restore
- total failure → transcript left on the clipboard WITHOUT the exclusion
  format (last resort, PRD §6) so the user can paste it manually

Every OS-touching primitive lives behind the platform seam (ADR 0005); this
module holds only policy and runs anywhere.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import INJECTION_STRATEGIES, AppOverride, canonical_strategy

if TYPE_CHECKING:
    from .platform import KeycodeTable, Platform

log = logging.getLogger(__name__)


@dataclass
class InjectionResult:
    outcome: str              # "inserted" | "fallback" | "notify-only" | "failed"
    on_clipboard: bool = False
    detail: str = ""
    # True only when a *detectable* typing failure (short send or exception)
    # forced the fallback — the auto-learn signal (#45). The modifier-timeout
    # path into fallback is the user's hand, not the app, and stays False.
    typing_failed: bool = False


def resolve_override(app_name: str | None, overrides: list[AppOverride],
                     default_strategy: str = "type") -> AppOverride:
    """Pick the override for an app (case-insensitive first match), else the default."""
    name = (app_name or "").lower()
    for o in overrides:
        if o.process.lower() == name and canonical_strategy(o.strategy) in INJECTION_STRATEGIES:
            return o
    default_strategy = canonical_strategy(default_strategy)
    if default_strategy not in INJECTION_STRATEGIES:
        default_strategy = "type"
    return AppOverride(process=name or "*", strategy=default_strategy,
                       restore_clipboard=True)


def learn_override(app_name: str | None, overrides: list[AppOverride]) -> AppOverride | None:
    """Auto-learn (#45): after a genuine typing failure where the clipboard
    fallback succeeded, remember clipboard for that app. Any existing entry for
    the process — hand-authored or learned — blocks learning, so hand-authored
    precedence is automatic. Returns the appended override, or None."""
    name = (app_name or "").lower()
    if not name or name == "unknown":  # the platform couldn't identify the app
        return None
    if any(o.process.lower() == name for o in overrides):
        return None
    # Defaults are byte-for-byte the fallback configuration that just succeeded.
    override = AppOverride(process=name, strategy="clipboard", learned=True)
    overrides.append(override)
    return override


class Injector:
    def __init__(self, overrides: list[AppOverride],
                 default_strategy: str = "type",
                 platform: Platform | None = None) -> None:
        if platform is None:
            from . import platform as platform_pkg

            platform = platform_pkg.current()
        self.overrides = overrides
        self.default_strategy = default_strategy
        self._platform = platform

    # ---- public API ------------------------------------------------------

    def focused_app_name(self) -> str:
        """The focused app's identity — executable name on Windows."""
        return self._platform.focused_app.name()

    def insert(self, text: str, app_name: str | None = None) -> InjectionResult:
        """Insert text at the cursor of the focused app."""
        if not text:
            return InjectionResult("inserted")

        override = resolve_override(app_name, self.overrides, self.default_strategy)
        strategy = canonical_strategy(override.strategy)
        log.debug("inject: app=%s strategy=%s chars=%d", app_name, strategy, len(text))
        if strategy == "notify-only":
            return InjectionResult("notify-only", detail="app is configured notify-only")
        blocked = self._platform.focused_app.injection_blocked()
        if blocked:
            return InjectionResult("notify-only", detail=blocked)

        released = self._wait_modifiers_released()

        if strategy == "type":
            # The fall-through chain is platform data (§1.2): on Windows a
            # detectable typing failure degrades to the paste rung. A
            # still-held modifier corrupts unicode typing while a synthetic
            # paste survives it (extra Ctrl is harmless to Ctrl+V), so a
            # modifier timeout degrades too — without the auto-learn flag.
            fall_through = "paste" in self._platform.capabilities.injection_rungs[1:]
            typing_failed = False
            if not released:
                log.debug("inject: modifiers still held at timeout; using clipboard")
            else:
                try:
                    if self._type_text(text, override):
                        return InjectionResult("inserted")
                    typing_failed = True
                    log.debug("inject: short send; falling back to clipboard")
                except Exception:
                    typing_failed = True
                    log.debug("inject: typing raised", exc_info=True)
            if not fall_through:
                return self.last_resort(text)
            try:
                self._clipboard_paste(text, override)
                return InjectionResult("fallback", typing_failed=typing_failed)
            except Exception:
                log.debug("inject: clipboard fallback raised", exc_info=True)
                return self.last_resort(text)

        # clipboard / clipboard-no-restore
        try:
            self._clipboard_paste(text, override)
            return InjectionResult("inserted")
        except Exception:
            log.debug("inject: clipboard strategy raised", exc_info=True)
            return self.last_resort(text)

    def last_resort(self, text: str) -> InjectionResult:
        """Leave the transcript on the clipboard (no exclusion format) so it's pasteable."""
        try:
            self._platform.clipboard.set_text(text, exclude_from_history=False)
            return InjectionResult("failed", on_clipboard=True,
                                   detail="insertion failed; transcript on clipboard")
        except Exception:
            return InjectionResult("failed", on_clipboard=False,
                                   detail="insertion and clipboard both failed")

    # ---- the rungs ---------------------------------------------------------

    def _wait_modifiers_released(self, timeout_s: float = 2.0) -> bool:
        """A physically-held Ctrl corrupts unicode typing; wait for release.
        Returns False when the modifiers are still down at timeout."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self._platform.keyboard.modifiers_down():
                return True
            time.sleep(0.01)
        return False

    def _type_text(self, text: str, override: AppOverride) -> bool:
        """Batched unicode typing. Returns False on a detectable short send."""
        units = _utf16_units(text)
        chunk = override.chunk_size or len(units)
        for i in range(0, len(units), chunk):
            if not self._platform.keyboard.send_text_units(units[i:i + chunk]):
                return False
            if override.chunk_delay_ms:
                time.sleep(override.chunk_delay_ms / 1000)
        return True

    def _clipboard_paste(self, text: str, override: AppOverride) -> None:
        clipboard = self._platform.clipboard
        restore = (override.restore_clipboard
                   and canonical_strategy(override.strategy) != "clipboard-no-restore")
        saved = clipboard.get_text() if restore else None

        clipboard.set_text(text, exclude_from_history=True)
        seq_after_set = clipboard.sequence_number()

        table = self._platform.capabilities.keycode_table
        chord = override.paste_chord or self._platform.capabilities.paste_chord
        keys = (parse_paste_chord(chord, table)
                or parse_paste_chord(self._platform.capabilities.paste_chord, table))
        self._platform.keyboard.send_chord(keys)
        time.sleep(max(override.settle_delay_ms, 0) / 1000)

        if (restore and saved is not None
                and clipboard.sequence_number() == seq_after_set):
            # Text formats only; restoring delayed-render formats is unsafe.
            clipboard.set_text(saved, exclude_from_history=False)


# ---- module-level helpers --------------------------------------------------

def _utf16_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


def utf16_chunks(units: list[int], max_units: int) -> list[list[int]]:
    """Split UTF-16 units into events of at most `max_units` (CGEvent unicode
    typing truncates at 20 per event, spec §2), backing off one unit rather
    than splitting a surrogate pair across events. A lone high surrogate at
    the end of the input (or with no room to back off) passes through as-is —
    mojibake in, mojibake out, never a dropped unit."""
    chunks: list[list[int]] = []
    start = 0
    while start < len(units):
        end = min(start + max_units, len(units))
        if (end < len(units) and end - start > 1
                and 0xD800 <= units[end - 1] <= 0xDBFF):
            end -= 1
        chunks.append(units[start:end])
        start = end
    return chunks


def parse_paste_chord(chord: str, table: KeycodeTable | None = None) -> list[int]:
    """Parse "ctrl+v" into the keycodes a synthetic chord presses, per the
    platform's table. Unrecognized parts are dropped so a typo can never send
    a key nobody named. An entirely unparseable chord returns [] — the caller
    falls back to `Capabilities.paste_chord`, the platform's own spelling."""
    if table is None:
        from . import platform

        table = platform.current().capabilities.keycode_table
    keys: list[int] = []
    for part in chord.lower().split("+"):
        part = part.strip()
        if part in table.modifiers:
            keys.append(table.modifiers[part])
        elif part in table.chars:
            keys.append(table.chars[part])
        elif table.ord_fallback and len(part) == 1:
            keys.append(ord(part.upper()))
    return keys
