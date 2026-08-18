# Research: Linux desktop integration — tray, theme ink, overlay, focused app

Ticket: #13 (part of #11) · Date: 2026-08-16

> **Research snapshot.** Findings as of the date above; the decisions that
> followed supersede this doc where they differ — see ADR 0009 (`app_picker` is True — installed apps), ADR 0012 (Wayland *does* have a permission surface: `request_permission()`), ADR 0013 (transport is jeepney, not `QStyleHints`), ADR 0014 (no LayerShellQt overlay and no XWayland fallback in v1). Read the ADRs
> and `docs/specs/m6-linux-port-spec.md` for what ships.

Scope: GNOME (Wayland and X11 sessions), KDE Plasma (Wayland and X11), and
SteamOS desktop mode. SteamOS desktop mode *is* KDE Plasma ([Steam Deck
FAQ](https://www.steamdeck.com/en/faq)), and since SteamOS 3.8.10 the desktop
session defaults to Plasma on Wayland with X11 demoted to a developer option
([OpenSourceFeed on 3.8.10](https://www.opensourcefeed.org/steamos-3-8-10-release/),
[ValveSoftware/SteamOS#2081](https://github.com/ValveSoftware/SteamOS/issues/2081)) —
so every "Plasma Wayland" verdict below is also the SteamOS verdict, and the
"Plasma X11" column is its fallback. Verdicts are stated as **possible**,
**conditional**, or **impossible** per DE and session type.

## 1. Tray: QSystemTrayIcon → StatusNotifierItem

**Mechanics.** On Linux, `QSystemTrayIcon` speaks the freedesktop
StatusNotifierItem D-Bus protocol (`org.kde.StatusNotifierItem`, menu via
`com.canonical.dbusmenu`) when a `org.kde.StatusNotifierWatcher` is present,
and falls back to legacy XEmbed embedding otherwise ([QSystemTrayIcon
docs](https://doc.qt.io/qt-6/qsystemtrayicon.html), [Qt's original SNI
commit](https://code.qt.io/cgit/qt/qtbase.git/commit/?h=v5.2.1&id=38abd653774aa0b3c5cdfd9a8b78619605230726)).
The SNI spec's watcher exposes `IsStatusNotifierHostRegistered` and emits
`StatusNotifierHostRegistered` when a host (a panel) appears, which is the
protocol-level answer to "is there a tray at all"
([StatusNotifierWatcher spec](https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/StatusNotifierWatcher/),
[StatusNotifierItem spec](https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/)).
Qt surfaces the same answer as `QSystemTrayIcon.isSystemTrayAvailable()`.

Cadent paints its state icons as pixmaps (`cadent/icons.py`, set in
`cadent/tray.py`), so Qt transmits them via the SNI `IconPixmap` property
(ARGB32); only a `QIcon.fromTheme` name would go out as `IconName`
([qdbustrayicon.cpp](https://github.com/qt/qtbase/blob/dev/src/gui/platform/unix/dbustray/qdbustrayicon.cpp)).
Consequence: **no Linux DE repaints Cadent's mark** — pixmap SNI icons render
as sent — so `Capabilities.tray_icon_painted_by_os` is False on Linux and
`DesktopEnv.tray_ink` is load-bearing (§2).

**Per-DE availability.**

| Environment | Verdict | Detail |
| --- | --- | --- |
| KDE Plasma (X11 + Wayland) | **Possible** | Plasma's system tray is a native SNI host; the spec's interfaces are literally `org.kde.*`. Works out of the box. |
| SteamOS desktop mode | **Possible** | It is Plasma (above). |
| GNOME (X11 + Wayland) | **Conditional** | GNOME Shell hosts no SNI natively and removed the legacy XEmbed tray in 3.26 ([OMG! Ubuntu](https://www.omgubuntu.co.uk/2017/09/will-you-miss-gnome-legacy-tray), [gnome-shell behaviour report](https://github.com/pithos/pithos/issues/491)). A shell extension supplies the host: [AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/), which **Ubuntu preinstalls and enables** ([ubuntu/gnome-shell-extension-appindicator](https://github.com/ubuntu/gnome-shell-extension-appindicator)); stock Fedora/upstream GNOME has no tray until the user installs one. |

**When no SNI host exists** (stock GNOME): `isSystemTrayAvailable()` is
false, `show()` displays nothing, and the menu is unreachable. What Cadent
should do:

- Never gate startup or dictation on the tray — the hotkey path is the app.
- Register the icon anyway and leave it registered: Qt's SNI backend tracks
  the watcher's D-Bus ownership, so the icon appears the moment a host
  registers (`StatusNotifierHostRegistered`,
  [watcher spec](https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/StatusNotifierWatcher/))
  — e.g. right after the user installs the extension, no restart.
- The tray's irreplaceable duties (pause, settings, quit) need a second
  door: `SingleInstance` already implies "second launch focuses the app" —
  on Linux that second launch should open Settings, and Settings should
  carry pause and quit when `isSystemTrayAvailable()` is false.
- Tray *toasts* go through SNI/notifications too; the overlay pill (§3)
  remains the guaranteed channel, same reasoning as Windows DND.

## 2. Panel ink: reading and watching `tray_ink` per DE

**No desktop exposes "the panel's actual pixel colour" through any API.**
The exact ink is **impossible** everywhere; a scheme-based approximation is
**possible** everywhere, through one portal:

- The XDG Settings portal standardizes namespace `org.freedesktop.appearance`
  with `color-scheme` (0 = no preference, 1 = prefer dark, 2 = prefer light),
  `contrast`, `accent-color` and `reduced-motion`, readable via `ReadOne` /
  `ReadAll` and **watchable via the `SettingChanged` signal** on the session
  bus ([org.freedesktop.portal.Settings docs](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Settings.html)).
  GNOME, KDE and other backends all implement it; it works for non-sandboxed
  apps too. This is the natural engine for `watch_tray_ink`: one D-Bus
  subscription, callback on the D-Bus thread — the same threading contract
  the protocol already documents.
- Qt ≥ 6.5 surfaces the same value in-process as
  `QStyleHints::colorScheme` + `colorSchemeChanged`, read from the portal on
  Linux ([QStyleHints docs](https://doc.qt.io/qt-6/qstylehints.html#colorScheme-prop),
  [qgenericunixthemes.cpp](https://github.com/qt/qtbase/blob/dev/src/gui/platform/unix/qgenericunixthemes.cpp)) —
  simpler than hand-rolled D-Bus if PySide6's Qt is ≥ 6.5 (Cadent's is).

Per-DE mapping from scheme to ink:

- **GNOME**: the stock shell theme keeps the top bar **dark in both colour
  schemes** — the light preference restyles apps, not the panel ([gnome-shell
  theme sources](https://gitlab.gnome.org/GNOME/gnome-shell/-/tree/main/data/theme)).
  So on stock GNOME a constant light ink is actually correct; user shell
  themes can invert it, unobservably. Verdict: **conditional** (correct on
  stock theme; approximate under user themes).
- **KDE Plasma / SteamOS**: the panel is painted by the Plasma desktop theme,
  whose default (Breeze) follows the global light/dark scheme — but the
  panel theme is user-selectable independently of the app colours
  ([Plasma/SystemTray, KDE UserBase](https://userbase.kde.org/Plasma/SystemTray)).
  Scheme → ink (dark scheme → light ink) is right for stock Breeze;
  **conditional** otherwise. Plasma *does* recolor monochrome icons to match
  the panel — but only icons resolved by name from the icon theme, never
  `IconPixmap` payloads, so the only route to a truly always-correct mark on
  Plasma is shipping per-state monochrome icons into hicolor and switching
  `IconName`s — a larger change, noted as future work, not required.

Recommendation: Linux `tray_ink()` returns one of two inks keyed off the
portal `color-scheme`, and `watch_tray_ink` subscribes to
`SettingChanged` (or `colorSchemeChanged`). The hardcoded guess becomes a
watched approximation; the honest limit — themed panels — is unobservable by
design.

## 3. Overlay: always-on-top, translucent, click-through under Wayland

**The core constraint.** Wayland's application shell, xdg-shell, gives
clients no global coordinates and no z-order control: `QWindow::setPosition`
/ `move()` are documented dead letters
([wayland-devel thread](https://lists.freedesktop.org/archives/wayland-devel/2014-August/016472.html)),
and `Qt::WindowStaysOnTopHint` is not honoured — keep-above is a
compositor-internal decision
([KDE Discuss on WindowStaysOnTopHint](https://discuss.kde.org/t/kde-plasma-support-for-qt-windowstaysontophint-flag-in-wayland/3106),
[Qt forum](https://forum.qt.io/topic/143381/how-to-get-window-stays-on-top-in-plasma-wayland),
[Wayland and Qt](https://doc.qt.io/qt-6/wayland-and-qt.html)).
`overlay.py`'s current recipe (frameless + stays-on-top + `setGeometry`)
therefore does not survive a native Wayland session as written.

**The escape hatch: wlr-layer-shell.** The `zwlr_layer_shell_v1` protocol
exists exactly for HUD-class surfaces: an **overlay** layer above normal
windows, anchoring to screen edges with margins, an exclusive-zone opt-out,
`keyboard_interactivity: none`, and click-through by setting an **empty
input region** on the surface — the protocol text says verbatim "If you do
not want to receive them, set the input region on your surface to an empty
region" ([wlr-layer-shell protocol](https://wayland.app/protocols/wlr-layer-shell-unstable-v1)).

Compositor support, checked against the compositors' own sources:

- **KWin implements it** (`src/wayland/layershell_v1.cpp` in
  [KDE/kwin](https://github.com/KDE/kwin/tree/master/src/wayland)) — so
  Plasma Wayland and SteamOS desktop mode can host the pill properly.
- **Mutter does not**: the request is a still-open 2019 issue with GNOME
  explicitly unenthusiastic about third-party use
  ([mutter#973](https://gitlab.gnome.org/GNOME/mutter/-/issues/973),
  [gnome-shell#1141](https://gitlab.gnome.org/GNOME/gnome-shell/-/issues/1141));
  Mutter's `src/wayland/` contains no layer-shell implementation
  ([GNOME/mutter](https://github.com/GNOME/mutter/tree/main/src/wayland)).
- wlroots compositors (Sway, Hyprland, etc.) and COSMIC support it —
  relevant when "Linux" widens past the three targets.

**Qt route to layer-shell.** KDE ships
[LayerShellQt](https://invent.kde.org/plasma/layer-shell-qt), a Qt Wayland
shell-integration plugin (selectable per-process via
`QT_WAYLAND_SHELL_INTEGRATION=layer-shell`) plus a C++ API for layer, anchors
and margins. There are no official Python bindings, so from PySide6 Cadent
would either generate a thin binding or accept the integration's defaults —
a real but bounded engineering cost.

**Click-through caveat.** Qt does not currently translate
`WA_TransparentForMouseEvents` / `Qt::WindowTransparentForInput` into an
empty `wl_surface.set_input_region` on Wayland — reported broken on the Qt
forum ([WindowTransparentForInput not worked on wayland](https://forum.qt.io/topic/154266/windowtransparentforinput-not-worked-on-wayland));
the input region is a first-class Wayland concept
([Surface regions, The Wayland Book](https://wayland-book.com/surfaces-in-depth/surface-regions.html))
but needs to be set explicitly (LayerShellQt or raw `wl_surface` via the
native interface). Translucency itself is fine — Wayland surfaces are ARGB
and always composited, so `WA_TranslucentBackground` costs nothing.

**Placement.** Even with layer-shell there is no "position over the focused
window" — only edge anchoring on a chosen output. Cadent's default
bottom-centre placement maps cleanly onto `anchor: bottom` + margin; the
multi-monitor "follow the dictation target" refinement additionally needs
§4's window rect, which native Wayland refuses, and `QCursor.pos()` is only
meaningful over the app's own surfaces on Wayland. Bottom-centre of the
primary (or compositor-chosen) output is the honest Wayland behaviour.

**Verdicts.**

| Environment | Verdict | Route |
| --- | --- | --- |
| GNOME X11 / Plasma X11 (incl. SteamOS X11 fallback) | **Possible** | Current code as-is: EWMH `_NET_WM_STATE_ABOVE`, real `setGeometry`, X11 input shaping all work. |
| Plasma Wayland / SteamOS | **Conditional** | Layer-shell via LayerShellQt (binding work + explicit empty input region). |
| GNOME Wayland | **Impossible natively** | Conditional fallback: run the whole app on XWayland (`QT_QPA_PLATFORM=xcb`), which restores X11 semantics; stacking above native Wayland windows is then compositor-managed and not guaranteed. |

## 4. Focused-app identity and window rect

**X11 sessions (GNOME Xorg, Plasma X11): possible, fully.** EWMH gives the
active window (`_NET_ACTIVE_WINDOW`), its process (`_NET_WM_PID` →
`/proc/<pid>/exe` name) and `WM_CLASS`, and Xlib gives the frame geometry
([EWMH spec](https://specifications.freedesktop.org/wm-spec/latest/)).
`FocusedApp.name()` and `window_rect()` both fill in.

**Wayland: isolation is the design.** A client cannot observe other clients'
windows through core protocols. What exists:

- `ext-foreign-toplevel-list-v1` (staging) lists toplevels with **title,
  app_id and a stable identifier only — no geometry, no focused state**;
  extension protocols are expected for the rest
  ([protocol page](https://wayland.app/protocols/ext-foreign-toplevel-list-v1)).
  Implemented by wlroots-family compositors; **not by Mutter** (absent from
  [GNOME/mutter src/wayland](https://github.com/GNOME/mutter/tree/main/src/wayland))
  and **not by KWin**, which closed the request as NOT A BUG in favour of
  purpose-built APIs ([KDE bug 483227](https://bugs.kde.org/show_bug.cgi?id=483227)).
- **KDE-specific: possible.** KWin implements its own
  `plasma-window-management` protocol
  (`src/wayland/plasmawindowmanagement.cpp` in
  [KDE/kwin](https://github.com/KDE/kwin/tree/master/src/wayland),
  [protocol page](https://wayland.app/protocols/plasma-window-management)) —
  the protocol Plasma's own taskbar consumes — exposing per-window title,
  app id, pid, **geometry and the active flag**. A Plasma-Wayland
  `FocusedApp` can be whole (name + rect), at the cost of a KDE-only
  protocol dependency.
- **GNOME Wayland: impossible without an extension.** GNOME Shell's
  `org.gnome.Shell.Introspect` D-Bus API (windows + focus, consumed by
  xdg-desktop-portal-gnome) is restricted to allowlisted callers; a
  user-installed shell extension re-exporting the focused window over D-Bus
  is the only third-party route. Absent that, `FocusedApp.name()` returns
  the "unknown" sentinel and `window_rect()` returns None — which
  `overlay.py` already tolerates.

**Identity vocabulary.** On Wayland the natural identity is the xdg-shell
`app_id`, which the spec ties to the `.desktop` file id
([xdg-shell, set_app_id](https://wayland.app/protocols/xdg-shell)); on X11
it is `WM_CLASS` or the exe name via pid. Recommendation: Linux
`FocusedApp.name()` returns the app_id / desktop id where a Wayland source
exists and the exe name on X11, matched case-insensitively like today;
`Capabilities.app_identity_placeholder` becomes e.g. `org.mozilla.firefox`,
and `app_picker` stays False for v1 (no portable running-apps enumeration).
Note the knock-on: on GNOME Wayland, per-app overrides and auto-learn have
no key to match on — overrides degrade to the default strategy there.

## 5. DesktopEnv fills

- **`open_path`** — **possible everywhere**: `QDesktopServices.openUrl`
  (→ `xdg-open`, or the [OpenURI portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.OpenURI.html)
  when sandboxed) ([QDesktopServices docs](https://doc.qt.io/qt-6/qdesktopservices.html)).
- **`open_permission_settings`** — no-op: Linux has no Accessibility-style
  preflight grant (`permission_preflight = None`), matching win32.
- **`text_scale_factor`** — **conditional.** GNOME exposes
  `org.gnome.desktop.interface text-scaling-factor` (GSettings), proxied
  through the Settings portal by the GTK backend
  ([xdg-desktop-portal-gtk settings.c](https://github.com/flatpak/xdg-desktop-portal-gtk/blob/master/src/settings.c)).
  KDE has no separate runtime text factor — its "force font DPI" feeds Qt's
  own high-DPI scaling, which Qt applies before widgets ever measure
  ([Qt High DPI](https://doc.qt.io/qt-6/highdpi.html)) — so return the GNOME
  key where present, else 1.0.
- **`high_contrast`** — **conditional-possible everywhere**: the portal's
  `org.freedesktop.appearance` `contrast` key (0 normal / 1 higher,
  interface version 2;
  [Settings portal docs](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Settings.html));
  GNOME fallback `org.gnome.desktop.a11y.interface high-contrast`.
- **`animations_enabled`** — **conditional.** Newest portals standardize
  `reduced-motion` in `org.freedesktop.appearance` (same doc; probe with
  `ReadAll` and treat absence as "no preference"). Fallbacks:
  GNOME `org.gnome.desktop.interface enable-animations` (via the same portal
  passthrough), KDE `kdeglobals` `[KDE] AnimationDurationFactor` = 0
  meaning off.
- All of these change-notify through the **same** portal `SettingChanged`
  signal as §2 — one D-Bus watcher serves `watch_tray_ink` and any future
  watcher.

## 6. Shape of a Linux adapter (summary)

- **Tray**: SNI via Qt everywhere; whole on Plasma/SteamOS, extension-gated
  on GNOME. Add the no-host door (Settings carries pause/quit); keep the
  icon registered for late hosts. `tray_icon_painted_by_os = False`.
- **Ink**: portal `color-scheme` → two-ink map, watched via `SettingChanged`
  / `colorSchemeChanged`; exact panel ink is unobservable by design.
- **Overlay**: works today on X11 sessions; layer-shell (LayerShellQt +
  explicit empty input region) on Plasma/SteamOS Wayland; on GNOME Wayland
  either run on `xcb` or accept no overlay — a candidate `Capabilities`
  fact per session type rather than a runtime surprise.
- **FocusedApp**: whole on X11; whole-ish on Plasma Wayland via
  plasma-window-management; sentinel + None on GNOME Wayland, where
  overrides/auto-learn consequently cannot key.
- **DesktopEnv fills**: one portal (Settings) supplies scheme, contrast and
  motion with change signals; `xdg-open` covers open-path; text scale is a
  GNOME-only key with a 1.0 default.
- Session type (X11 vs Wayland, `XDG_SESSION_TYPE`) matters as much as the
  DE: several rows flip on it, which suggests the Linux adapter probes both
  once at `current()` time and fills `Capabilities` accordingly.

## Sources

- StatusNotifierItem spec — https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/
- StatusNotifierWatcher spec — https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/StatusNotifierWatcher/
- QSystemTrayIcon — https://doc.qt.io/qt-6/qsystemtrayicon.html
- Qt SNI implementation — https://github.com/qt/qtbase/blob/dev/src/gui/platform/unix/dbustray/qdbustrayicon.cpp and https://code.qt.io/cgit/qt/qtbase.git/commit/?h=v5.2.1&id=38abd653774aa0b3c5cdfd9a8b78619605230726
- GNOME 3.26 legacy tray removal — https://www.omgubuntu.co.uk/2017/09/will-you-miss-gnome-legacy-tray and https://github.com/pithos/pithos/issues/491
- AppIndicator/KStatusNotifierItem extension — https://extensions.gnome.org/extension/615/appindicator-support/ and https://github.com/ubuntu/gnome-shell-extension-appindicator
- XDG Settings portal (color-scheme, contrast, reduced-motion, SettingChanged) — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Settings.html
- QStyleHints::colorScheme — https://doc.qt.io/qt-6/qstylehints.html#colorScheme-prop
- Qt Unix theme portal reader — https://github.com/qt/qtbase/blob/dev/src/gui/platform/unix/qgenericunixthemes.cpp
- gnome-shell theme sources (dark panel) — https://gitlab.gnome.org/GNOME/gnome-shell/-/tree/main/data/theme
- Plasma system tray — https://userbase.kde.org/Plasma/SystemTray
- wlr-layer-shell protocol — https://wayland.app/protocols/wlr-layer-shell-unstable-v1
- KWin Wayland sources (layershell_v1, plasmawindowmanagement) — https://github.com/KDE/kwin/tree/master/src/wayland
- Mutter Wayland sources (no layer-shell / foreign-toplevel) — https://github.com/GNOME/mutter/tree/main/src/wayland
- Mutter layer-shell issue — https://gitlab.gnome.org/GNOME/mutter/-/issues/973 and https://gitlab.gnome.org/GNOME/gnome-shell/-/issues/1141
- LayerShellQt — https://invent.kde.org/plasma/layer-shell-qt
- Wayland and Qt — https://doc.qt.io/qt-6/wayland-and-qt.html
- Wayland positioning limits (wayland-devel) — https://lists.freedesktop.org/archives/wayland-devel/2014-August/016472.html
- WindowStaysOnTopHint under Plasma Wayland — https://discuss.kde.org/t/kde-plasma-support-for-qt-windowstaysontophint-flag-in-wayland/3106 and https://forum.qt.io/topic/143381/how-to-get-window-stays-on-top-in-plasma-wayland
- Qt WindowTransparentForInput on Wayland — https://forum.qt.io/topic/154266/windowtransparentforinput-not-worked-on-wayland
- Wayland input regions — https://wayland-book.com/surfaces-in-depth/surface-regions.html
- ext-foreign-toplevel-list protocol — https://wayland.app/protocols/ext-foreign-toplevel-list-v1
- KWin declines ext-foreign-toplevel-list — https://bugs.kde.org/show_bug.cgi?id=483227
- plasma-window-management protocol — https://wayland.app/protocols/plasma-window-management
- xdg-shell (app_id ↔ .desktop id) — https://wayland.app/protocols/xdg-shell
- EWMH spec — https://specifications.freedesktop.org/wm-spec/latest/
- OpenURI portal — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.OpenURI.html
- QDesktopServices — https://doc.qt.io/qt-6/qdesktopservices.html
- xdg-desktop-portal-gtk settings backend — https://github.com/flatpak/xdg-desktop-portal-gtk/blob/master/src/settings.c
- Qt High DPI — https://doc.qt.io/qt-6/highdpi.html
- Steam Deck FAQ (desktop is KDE Plasma) — https://www.steamdeck.com/en/faq
- SteamOS 3.8.10 Wayland default — https://www.opensourcefeed.org/steamos-3-8-10-release/ and https://github.com/ValveSoftware/SteamOS/issues/2081
