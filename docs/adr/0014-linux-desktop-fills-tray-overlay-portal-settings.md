# Linux paints its own tray mark from the Settings portal, ships no overlay on the Wayland tiers, and reads every desktop fact from one portal snapshot

The last seam of the Linux port to be decided was `DesktopEnv` and the two
surfaces it feeds — the tray mark and the overlay pill. Both are places where
Linux's five targets disagree with each other more than they disagree with
Windows or macOS, so each answer below is deliberately the one that holds on
every target rather than the best one available on any single desktop.
Decided in #20 on the Linux porting map (#11), against the desktop-integration
research (#13) and three upstream fact-checks (LayerShellQt, PySide6's native
interfaces, Qt's D-Bus tray backend).

- **`tray_icon_painted_by_os = False`; the ink is a watched two-colour
  approximation.** No Linux desktop repaints a pixmap StatusNotifierItem
  icon, and none exposes the panel's actual colour. `tray_ink()` is keyed on
  the desktop first, then the Settings portal's
  `org.freedesktop.appearance color-scheme`: **GNOME → `#ffffff` always**
  (the stock shell keeps its top bar dark under both schemes); everyone else
  dark (1) → `#ffffff`, light (2) → `#000000`, no preference (0) →
  `#ffffff` — the fallback adapter's "invisible is worse than slightly
  wrong". Portal `contrast` does not change the ink: unlike Windows'
  `COLOR_WINDOWTEXT` there is no palette to read. `watch_tray_ink` is the
  one `SettingChanged` subscription on the shared jeepney connection
  (ADR 0013), callback on the portal thread. Themed panels are wrong
  unobservably and stay wrong; shipping monochrome per-state icons into
  hicolor so Plasma recolours them by `IconName` is named future work.
- **A tray-less desktop gets a second door, not a fault.** Stock GNOME hosts
  no StatusNotifierItem until the AppIndicator extension is installed. The
  icon stays registered — Qt shows it the moment a host appears — and while
  `isSystemTrayAvailable()` is false the Settings window carries **Pause and
  Quit** in a footer, a second launch (`SingleInstance`) opens Settings, and
  a General row says: *"No system tray found, so Pause and Quit live here.
  On GNOME, the AppIndicator and KStatusNotifierItem Support extension adds
  one."* This is a **live runtime state**, not a tier fact — a host can
  arrive mid-session — so it lives beside the tray, not in `Capabilities`.
  Naming GNOME here is fine: ADR 0012's "your desktop" rule covers the
  permission dialog, which is theirs; this names a thing to install.
- **A three-valued `Capabilities.overlay` fact — and the Wayland tiers ship
  `None` in v1.** `"windowed"` on Whole (X11: today's `overlay.py` as-is,
  Move mode included); `None` on Portal *and* Reduced; `"anchored"` is
  reserved for a layer-shell pill and unused. The support-tier promise for
  Portal is amended: **no overlay**. The Move-overlay button and the overlay
  position rows gate on `overlay == "windowed"`. Portal now differs from
  Reduced only in per-app overrides.
- **Failure feedback with no overlay goes to the tray's `message()`.** Qt's
  D-Bus tray backend sends `showMessage` to `org.freedesktop.Notifications`
  unconditionally — watcher or not — and Wayland has no XEmbed fallback to
  swallow it, so on Portal and Reduced `show_failure(...)` lands as a desktop
  notification even on stock GNOME. Failures only; no start/stop chatter,
  and no new `notify` seam. Recording-in-progress state on those tiers is
  the tray icon alone, and the tier line already says so.
- **Every other `DesktopEnv` read is a field of one portal `ReadAll`
  snapshot** kept fresh by the same `SettingChanged` subscription — no
  round-trips, no second watcher: `text_scale_factor` ←
  `org.gnome.desktop.interface text-scaling-factor` where a backend proxies
  it, else 1.0 (KDE's font DPI already flows through Qt scaling);
  `high_contrast` ← `org.freedesktop.appearance contrast == 1`, fallback
  `org.gnome.desktop.a11y.interface high-contrast`; `animations_enabled` ←
  `reduced-motion` if present, else GNOME `enable-animations`, else
  `kdeglobals [KDE] AnimationDurationFactor == 0` → False; `open_path` ←
  `xdg-open`. No portal → the fallback adapter's answers, no fault.
- **Copy is desktop-independent.** `theme_subtitle` = *"Follows your
  desktop's colour scheme"*; `high_contrast_reason` = *"Your desktop is set
  to higher contrast, so Cadent is following your system colours"*. Per-DE
  maps were rejected: `XDG_CURRENT_DESKTOP` is a list (`ubuntu:GNOME`), and
  a wrong name in a settings row reads as a bug.
- **`tray_click_toggles_pause = False` on every tier.** SNI `Activate` is a
  spare gesture on Plasma but opens the menu under GNOME's AppIndicator
  extension while Qt still emits `Trigger` — darwin's #160 double action.
  One value safe on every host beats a per-host guess.

## Considered options

- **Layer-shell overlay on Portal, in-process** — the research's route.
  Foreclosed by upstream source: LayerShellQt's shell integration turns
  *every* toplevel into a layer surface (no xdg-shell fallback for windows
  never handed to `Window::get`), so Settings and the wizard would become
  full-output Top-layer surfaces with keyboard-on-demand; it exposes no
  input-region API for click-through; and PySide6 exposes no `wl_surface`
  (only `QWaylandApplication`), so neither raw `zwlr_layer_shell_v1` nor
  `wl_surface_set_input_region` is reachable from Python.
- **Layer-shell overlay via a compiled helper process** (`cadent-overlay`, a
  small Qt/LayerShellQt binary taking state over a pipe, `QWindow::setMask`
  for click-through) — the only remaining working route. Rejected for v1: a
  second binary in a pure-Python app, built for one tier, with its own
  `LOAD_BEARING` row and AUR wiring, is a hard-to-reverse cost that deserves
  its own effort. The `"anchored"` value is reserved for it.
- **Running the Wayland tiers under XWayland (`QT_QPA_PLATFORM=xcb`) to
  recover the overlay** — already ruled out by #17.
- **Pure scheme→ink map on GNOME** — wrong on the stock theme, which keeps
  the panel dark under the light scheme.
- **A `DesktopEnv.notify` seam** — unnecessary once Qt's `showMessage` is
  known to reach the notification daemon without a tray host.
- **Naming the tray-less state on the wizard's Done page** — the Done page
  already carries the tier line; the General row is where the fact is looked
  up.

## Consequences

**The Portal tier's promise shrinks by one line.** The `Support tier` glossary
entry and ADR 0012's Portal summary line pick up "no overlay" when the branch
chains are folded together at spec assembly (#25): *"Wayland session on KDE
Plasma — Portal support: your desktop owns the hotkey, Cadent won't learn on
its own which apps need pasting, and there's no overlay."*

**`Capabilities` gains `overlay`, and win32/darwin fill it `"windowed"`.**
The overlay code itself does not change; `app.py` learns to construct it only
where the fact says so, and to route `show_failure` to the tray otherwise.

**A layer-shell overlay for Plasma/SteamOS Wayland is a follow-on effort**,
out of this map's scope, with its mechanism (compiled helper) already known.
