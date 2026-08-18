"""The tray icon and menu (spec §3).

**The icon signals availability only** — Ready / Paused / Needs attention, one
silhouette in three colours. Whether cleanup is on comes off the icon entirely
and lives in the tooltip and the menu checkbox: the pill now answers "which
mode am I in?" at the only moment it matters, and a 16px icon Windows frequently
collapses into the overflow flyout is the wrong surface for a mode you check
*while dictating*. Activity states are excluded too — they would be a third
indicator of the same fact, after the pill and Windows' own mic-in-use
indicator, animating in a place most users cannot see.

What the tray is uniquely good at is the question the pill cannot answer,
because it isn't on screen: **"is this going to work if I press the key?"**
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import gpu_pack, icons, platform
from .downloads import Progress

READY, PAUSED, ATTENTION = "ready", "paused", "attention"

# Faults clear when they are fixed. Each maps to the words the status header
# uses, because a colour-only signal must never be the only signal.
FAULTS: dict[str, str] = {
    "setup-unfinished": "setup unfinished",
    "model-failed": "speech model failed",
    "cleanup-failed": "cleanup unavailable",
    "engine-on-cpu": "speech engine rebuilding on CPU",
    "config-unreadable": "config.json couldn't be read",
    # The platform's one grant is missing (ADR 0012): Accessibility on
    # darwin, the portal grant on Linux's Wayland tiers. Set by the app's
    # grant poll, so it clears with no window open.
    "permission-needed": "permission needed",
}

# Offers clear when they are *seen*. Without that split an NVIDIA user who
# never installs the pack would sit permanently amber, which trains them to
# ignore the colour.
OFFERS: dict[str, str] = {
    "gpu-pack": "GPU support pack available",
}


class TrayLedger:
    """What the icon's colour means, and when it stops meaning it.

    Amber fires whenever Cadent is not running at full capability, which is
    broader than "broken" — but breadth is what creates the permanent-scenery
    trap, so the clearing rule splits by kind: **offers clear on view, faults
    clear on fix.** Amber therefore means *"there is something here you have
    not seen yet, or something is genuinely wrong"*.
    """

    def __init__(self) -> None:
        self._faults: set[str] = set()
        self._offers: set[str] = set()
        # Work in progress, in the words the header uses. Deliberately outside
        # both sets: an activity is neither unseen nor wrong, and a colour that
        # fires on routine work is a colour people learn to ignore (§3.4).
        self._activity: str | None = None

    # ---- raising and clearing ---------------------------------------------

    def set_fault(self, kind: str, active: bool = True) -> None:
        if kind not in FAULTS:
            raise KeyError(f"unknown fault {kind!r}")
        if active:
            self._faults.add(kind)
        else:
            self._faults.discard(kind)

    def raise_offer(self, kind: str) -> None:
        if kind not in OFFERS:
            raise KeyError(f"unknown offer {kind!r}")
        self._offers.add(kind)

    def menu_opened(self) -> None:
        """The menu item stays available forever; only the colour stops."""
        self._offers.clear()

    def set_activity(self, activity: str | None) -> None:
        """What Cadent is busy doing, or None when it is busy with nothing.

        Model downloads used to be one tray balloon at the start and then
        silence, for anything from 78 MB to 3.1 GB (#115). This is where that
        silence ends.
        """
        self._activity = activity

    # ---- what it adds up to ------------------------------------------------

    def state(self, paused: bool) -> str:
        """Precedence: **paused > attention > ready**.

        While paused nothing is going to run anyway, so a fault is not yet
        actionable, and the icon should reflect the state you deliberately
        chose; amber reappears the instant you resume. The cost — a real
        problem off the icon until you resume — is absorbed by the status
        header, which names both facts.
        """
        if paused:
            return PAUSED
        if self._faults or self._offers:
            return ATTENTION
        return READY

    def concerns(self) -> list[str]:
        """Every unresolved thing, in the words the header uses."""
        return ([FAULTS[k] for k in FAULTS if k in self._faults]
                + [OFFERS[k] for k in OFFERS if k in self._offers])

    def status_line(self, paused: bool, cleanup: bool) -> str:
        """The disabled header — what makes a colour-only icon safe.

        One click always resolves ready-vs-paused, it names the mode the icon
        no longer carries, and it is the only surface that can state a
        *combination* in words: `Paused · speech model failed`.
        """
        if "setup-unfinished" in self._faults:
            return "Cadent — setup unfinished"
        availability = "paused" if paused else "ready"
        parts = [availability, "cleanup on" if cleanup else "cleanup off"]
        parts += self.concerns()
        if self._activity:
            parts.append(self._activity)
        return "Cadent — " + " · ".join(parts)

    @property
    def activity(self) -> str | None:
        return self._activity

    @property
    def setup_unfinished(self) -> bool:
        return "setup-unfinished" in self._faults


class Tray:
    """The QSystemTrayIcon, its menu, and the ledger that colours it."""

    def __init__(self, *, on_toggle_cleanup: Callable[[bool], None],
                 on_toggle_pause: Callable[[bool], None],
                 on_settings: Callable[[], None],
                 on_history: Callable[[], None],
                 on_wizard: Callable[[], None],
                 on_gpu_download: Callable[[], None],
                 on_quit: Callable[[], None],
                 on_message_clicked: Callable[[], None] | None = None) -> None:
        self.ledger = TrayLedger()
        self._cleanup = False
        self._paused = False
        self._on_toggle_pause = on_toggle_pause
        self._on_message_clicked = on_message_clicked

        self.icon = QSystemTrayIcon()
        self.menu = QMenu()

        self.status_action = QAction("Cadent")
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        # Bold, and first, when setup was cancelled: the way back has to be
        # more prominent than the thing that is currently disabled (§6.4).
        self.finish_setup_action = QAction("Finish setup…")
        font = QFont(self.finish_setup_action.font())
        font.setBold(True)
        self.finish_setup_action.setFont(font)
        self.finish_setup_action.setVisible(False)
        self.finish_setup_action.triggered.connect(on_wizard)
        self.menu.addAction(self.finish_setup_action)

        self.cleanup_action = QAction("Clean up transcripts (AI)")
        self.cleanup_action.setCheckable(True)
        self.cleanup_action.triggered.connect(on_toggle_cleanup)
        self.menu.addAction(self.cleanup_action)
        self.pause_action = QAction("Pause dictation")
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(on_toggle_pause)
        self.menu.addAction(self.pause_action)

        self.menu.addSeparator()
        self.settings_action = QAction("Settings…")
        self.settings_action.triggered.connect(on_settings)
        self.menu.addAction(self.settings_action)
        self.history_action = QAction("History…")
        self.history_action.triggered.connect(on_history)
        self.menu.addAction(self.history_action)

        # Separator-grouped sections keep conditional items from shuffling the
        # action list: the GPU item appearing must never move Settings and
        # History under the cursor. Folding it in with them would be cheaper
        # now that it is alone in here (#110) — and would give that guarantee
        # up, so the section stays and the separator becomes as conditional as
        # the item it groups. Where no pack exists at all (M5 §7: Metal ships
        # in the build), the whole section stays out of the menu — the
        # actions still exist so callers hold one shape everywhere.
        self._gpu_pack_available = \
            platform.current().capabilities.gpu_pack_available
        self._click_toggles_pause = \
            platform.current().capabilities.tray_click_toggles_pause
        self.gpu_separator = self.menu.addSeparator() \
            if self._gpu_pack_available else QAction()
        self.gpu_separator.setVisible(False)
        self.gpu_action = QAction(
            f"Download GPU support pack ({gpu_pack.DOWNLOAD_SIZE})")
        self.gpu_action.setVisible(False)
        self.gpu_action.triggered.connect(on_gpu_download)
        if self._gpu_pack_available:
            self.menu.addAction(self.gpu_action)

        self.menu.addSeparator()
        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(on_quit)
        self.menu.addAction(self.quit_action)

        self.icon.setContextMenu(self.menu)
        self.menu.aboutToShow.connect(self._on_menu_about_to_show)
        # `activated` was never connected: left-clicking the icon was inert.
        self.icon.activated.connect(self._on_activated)
        if on_message_clicked is not None:
            self.icon.messageClicked.connect(self._on_message_clicked_slot)
        self.refresh()

    # ---- state -------------------------------------------------------------

    def set_cleanup(self, on: bool) -> None:
        self._cleanup = on
        self.cleanup_action.setChecked(on)
        self.refresh()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_action.setChecked(paused)
        self.refresh()

    def set_fault(self, kind: str, active: bool = True) -> None:
        self.ledger.set_fault(kind, active)
        self.refresh()

    def set_download(self, what: str | None,
                     progress: Progress | None = None) -> None:
        """Name the download in flight, or clear it with None.

        A percentage rather than the byte caption: the header and the tooltip
        are single lines read at a glance, and "16%" answers "is this getting
        anywhere?" in three characters. The wizard, where there is room and
        someone is watching, shows the megabytes.
        """
        if what is None:
            self.ledger.set_activity(None)
        else:
            percent = progress.percent if progress is not None else 0
            self.ledger.set_activity(f"downloading the {what}, {percent}%")
        self.refresh()

    def offer_gpu_pack(self) -> None:
        if not self._gpu_pack_available:
            return      # nothing to download here; never amber for it either
        self.ledger.raise_offer("gpu-pack")
        self._show_gpu_offer(True)
        self.refresh()

    def withdraw_gpu_offer(self) -> None:
        """The pack is installed — there is nothing left to offer."""
        self._show_gpu_offer(False)

    def _show_gpu_offer(self, shown: bool) -> None:
        """The item and the separator grouping it move together.

        Nothing else lives in that section since #110, so a visible separator
        with a hidden item is a double rule above Quit. Callers get one lever
        rather than two they have to remember to pull in step.
        """
        self.gpu_separator.setVisible(shown)
        self.gpu_action.setVisible(shown)

    def refresh(self) -> None:
        state = self.ledger.state(self._paused)
        self.icon.setIcon(icons.state_icon(state))
        self.status_action.setText(
            self.ledger.status_line(self._paused, self._cleanup))
        self.icon.setToolTip(self._tooltip(state))

        unfinished = self.ledger.setup_unfinished
        self.finish_setup_action.setVisible(unfinished)
        # Dictation toggles are disabled while setup is unfinished: there is
        # nothing for them to turn on yet.
        self.cleanup_action.setEnabled(not unfinished)
        self.pause_action.setEnabled(not unfinished)

    def _tooltip(self, state: str) -> str:
        mode = "cleanup on" if self._cleanup else "cleanup off"
        if self.ledger.setup_unfinished:
            return "Cadent — setup unfinished"
        if state == PAUSED:
            return f"Cadent ({mode}) — paused"
        if state == ATTENTION:
            return (f"Cadent ({mode}) — "
                    + "; ".join(self.ledger.concerns()))
        # Only ever displaces the ready tail: a fault and a pause are both more
        # urgent than a download, and the header still names all of them.
        if self.ledger.activity:
            return f"Cadent ({mode}) — {self.ledger.activity}"
        return f"Cadent ({mode}) — hold the hotkey to dictate"

    # ---- interaction -------------------------------------------------------

    def _on_menu_about_to_show(self) -> None:
        self.ledger.menu_opened()
        self.refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Left-click flips pause — the most reversible state change costs one
        click. **Double-click is the same action, not two**, so only Trigger
        is handled. Only where the fact table says the click is spare: on
        darwin the OS spends it opening the menu (#160).

        Confirmed by the icon and tooltip alone, no toast: a tray click happens
        with your eye already on the icon, so the colour change lands exactly
        where you are looking. The accidental-click failure — pausing without
        noticing, then finding the hotkey does nothing — is caught downstream
        by the Paused pill.
        """
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return
        if not self._click_toggles_pause:
            # Where the OS opens the menu on this same click (darwin), Qt
            # still emits Trigger — pausing here would flip state on every
            # look at the menu (#160).
            return
        if self.ledger.setup_unfinished:
            return
        self._on_toggle_pause(not self._paused)

    def _on_message_clicked_slot(self) -> None:
        if self._on_message_clicked is not None:
            self._on_message_clicked()

    # ---- passthrough -------------------------------------------------------

    def show(self) -> None:
        self.icon.show()

    def message(self, title: str, body: str,
                kind: QSystemTrayIcon.MessageIcon =
                QSystemTrayIcon.MessageIcon.Information,
                timeout_ms: int = 5_000) -> None:
        self.icon.showMessage(title, body, kind, timeout_ms)
