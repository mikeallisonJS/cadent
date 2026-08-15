# Spec: Cadent M4 — UI modernization

Status: ready-for-agent
Source map: [#58](https://github.com/mikeallisonJS/cadent/issues/58) · Source PRD: [../../PRD.md](../../PRD.md) · Domain: [../../CONTEXT.md](../../CONTEXT.md)

> Every section below is the settlement of a closed wayfinder ticket. The ticket is the reasoning; this spec is the instruction. Where the two disagree, the ticket's *later* corrections win — they are already folded in here. Section headings carry their source ticket; the full index is at the end.

---

## Problem Statement

Cadent works and looks like a scaffold. It has **no icon at all** — `QIcon.fromTheme("audio-input-microphone")` returns null on Windows, so the tray is a grey circle drawn in code and the exe, the installer, every window title bar and the Alt-Tab entry are on PyInstaller's default. The overlay pill is 99 lines of hardcoded `QColor` literals speaking a different visual language from the settings window, which is itself stock Fusion. There is no first-run experience: a new user meets a tray icon and a config file. There is no accessibility code anywhere in `cadent/*.py` — no accessible names, no tab order, no focus treatment. Dark mode is whatever Qt happens to do.

Underneath the surface problem are three real defects the redesign has to fix rather than repaint:

- The pill resolves its screen from `self.screen()` on a never-shown widget, which is always the **primary** screen — dictate into a window on a second monitor and the pill appears where you aren't looking.
- Three events produce **no feedback whatsoever**: a mid-hold cancel, the max-utterance cutoff, and a hotkey press while paused. The first is indistinguishable from a crash.
- `Config.save()` writes the whole file from a dataclass that dropped every unknown key at load, so a hand-authored `_comment` in `config.json` is erased by the next save — and `load()` promises to leave a malformed file "untouched for hand-repair" while `save()` destroys it.

## Solution

M4 gives Cadent one visual language, one identity mark, a first-run wizard, and a settings surface a person can actually configure the app from — without changing the app's shape. **The tray-first shell stays**; there is no main window.

The design language is **soft dark SaaS**: near-black violet-tinted surfaces, `#9678FF` accent, rounded section cards, grouped sidebar. It ships in **both** dark and light, follows the Windows app colour mode by default, and gets out of the way entirely under a Windows contrast theme. Everything is QWidget + QSS rendered from a single Python token dict — no QML.

One mic-derived mark becomes the app's whole identity: tray, exe, installer, title bars. In the tray it signals **availability only** — Ready / Paused / Needs attention — in three colours proven against both taskbar polarities.

The overlay pill stops being decorative and becomes the app's **guaranteed** feedback channel, because Windows Do Not Disturb silently suppresses toasts. Pill = terse failure; toast = the detail.

Settings is rebuilt as six panes under a grouped sidebar with **instant apply and no OK/Cancel**, including new Vocabulary & snippets and App overrides panes. `config.json` moves to per-key atomic delta writes that never destroy a hand edit and never overwrite an unreadable file.

A six-page first-run wizard consents the model download with a hardware-based suggestion, offers the GPU support pack, and teaches the hotkey with the **real pill**.

## User Stories

**Look and theme**
1. As a user, I want Cadent to look like one designed app across the tray, pill, wizard, settings and history, so that it doesn't read as a collection of scaffolds.
2. As a user, I want Cadent to follow my Windows app colour mode automatically, so that it fits my desktop without configuration.
3. As a user, I want to override the theme to Light or Dark regardless of Windows, so that I can keep the app dark on a light desktop.
4. As a user running a Windows contrast theme, I want Cadent to use my system colours instead of its own, so that I can read it at all.
5. As a user with Windows Text size above 100%, I want Cadent's text to scale with it, so that nothing clips.

**Identity and tray**
6. As a user, I want a real Cadent icon in the tray, the taskbar, Alt-Tab, the installer and the Start menu, so that the app is recognisable and doesn't look unfinished.
7. As a user, I want one glance at the tray icon to tell me whether pressing the hotkey will produce text, so that I don't discover a paused or broken app by dictating into nothing.
8. As a user, I want to left-click the tray icon to pause and resume, so that the most reversible action costs one click.
9. As a user, I want the tray menu to tell me in words what the icon says in colour, so that a colour-only signal is never the only signal.
10. As a user, I want a degraded state (model failed, cleanup failed, setup unfinished, GPU pack available) to show on the icon until I've seen it, so that nothing important is delivered only by a 10-second toast.

**Overlay pill**
11. As a user, I want the pill to appear on the screen holding the window I'm dictating into, so that it isn't on another monitor.
12. As a user, I want the pill to tell me which mode I'm in *before* I speak, so that I don't find out by whether cleanup happened.
13. As a user, I want a level meter that visibly responds to how loudly I'm speaking and goes flat when it hears nothing, so that a wrong input device is obvious immediately.
14. As a user, I want a cancelled dictation, a max-length cutoff and a hotkey press while paused to each say something, so that none of them reads as a crash.
15. As a user, I want failures to reach me on the pill even when Windows Do Not Disturb is suppressing toasts, so that a failed dictation is never silent.
16. As a user, I want to move the pill and have it stay where I put it across monitor changes, so that it isn't over the thing I'm looking at.
17. As a user, I want to hide the overlay's activity states but still be told about failures, so that turning off the indicator doesn't turn off the alarm.

**Settings**
18. As a user, I want settings organised into named groups with one topic per pane, so that I can find a setting without hunting.
19. As a user, I want changes to apply as I make them with no OK button, so that closing the window is never a question.
20. As a user, I want to be told inline when a change restarts an engine and roughly how long it takes, so that a two-second pause isn't a mystery.
21. As a user, I want to edit vocabulary and snippets in a pane instead of a JSON file, while my hand-edited file keeps working, so that I get an editor without losing the file.
22. As a user, I want to see and edit every per-app injection rule — including the ones Cadent ships and the ones it learned — in plain language, so that "why does dictation paste into my terminal?" has an answer.
23. As a user, I want to be able to restore the built-in app rules after deleting one, so that a routine delete isn't permanent damage.
24. As a user, I want my recent dictations listed in settings with the retention control that governs them, so that the setting and its subject are in one place.

**Config file**
25. As a user who hand-edits `config.json`, I want my comments, unknown keys and untouched edits preserved, so that using the settings window doesn't erase my file.
26. As a user, I want an unreadable `config.json` to be left alone rather than overwritten, and to be told the app is running on defaults, so that a typo doesn't silently cost me my configuration.
27. As a user, I want to be told when the file and the running app disagree, so that a surprise doesn't land on a restart weeks later.

**First run**
28. As a new user, I want a guided first run that ends with a working dictation setup, so that I don't have to find the config file.
29. As a new user, I want the speech model download disclosed, sized, sourced and initiated by me, so that the privacy claim is visible on day one.
30. As a new user on an NVIDIA machine, I want the GPU support pack offered before I pick a model, so that my choice reflects the hardware I'll actually have.
31. As a new user, I want a model suggested for my hardware with a one-line reason, and the full list still available, so that I don't have to research Whisper variants.
32. As a new user, I want to try the hotkey once inside the wizard and see the real pill, so that I learn both in one motion.
33. As a user who cancels setup, I want the app to stay in the tray with an obvious way back, so that quitting the wizard doesn't strand me.
34. As an existing user, I want to never see the wizard, so that an update doesn't re-onboard me.

**Accessibility**
35. As a keyboard-only user, I want every function reachable and operable by keyboard with a visible focus ring, so that I can use the app without a mouse.
36. As a screen-reader user, I want every control to announce a correct name and role, so that the app is navigable.
37. As a screen-reader user, I want wizard page changes, engine restarts and try-it results announced, so that I learn what changed without seeing it.

---

# 1. Foundations

## 1.1 Toolkit: QWidget + QSS everywhere ([#59](https://github.com/mikeallisonJS/cadent/issues/59))

**Build every surface on QWidget + QSS, including the overlay pill. Do not adopt QML/Qt Quick.**

Packaging is the decisive axis: widgets-only builds cost zero delta because PyInstaller's stock hooks already skip QML. Adopting QML adds a measured **~35–45 MB** through an import-blind `qmldir` sweep with no supported exclusion knob, plus a 204 MB `Qt6WebEngineCore.dll` hazard reachable via `qml/QtWebEngine` that would need hand-maintained spec surgery. `QQuickWidget` also disables the threaded render loop and cannot stack translucently over widgets — i.e. it fails on exactly the pill, the one surface QML was attractive for.

Rules that fall out and must be honoured in implementation:

- **One token module rendered into one app-wide QSS**, applied with `app.setStyleSheet()`. QSS has no variables; the token dict + `string.Template` substitution is the variable mechanism (§1.4).
- **Repolish after any dynamic-property change used in a selector** (`style().unpolish(w); style().polish(w)`). The focus-visible rule in §8.2 depends on this.
- **Never partially style `QComboBox` or `QScrollBar`** — QSS styling of these is all-or-nothing; a partial rule yields a broken control.
- Animation is hand-coded `QPropertyAnimation` / `QTimer`, not declarative. This is the only real concession and it is within budget for these surfaces.

## 1.2 Theme: System / Light / Dark, on Fusion ([#65](https://github.com/mikeallisonJS/cadent/issues/65), [#60](https://github.com/mikeallisonJS/cadent/issues/60))

**Cadent follows the Windows *app* colour mode by default; the user can override it. Both colour sets ship.**

- New `Config` field: `theme: str = "system"` — `"system" | "light" | "dark"`.
- Mechanism: `unsetColorScheme()` for System; `setColorScheme(Qt.ColorScheme.Light|Dark)` for the overrides. A `colorSchemeChanged` handler re-renders the QSS template and re-applies it.
- **Decide off the signal's `scheme` argument, never `QGuiApplication.palette()`** — the old palette is still in effect when the signal fires.
- **The branch is written `Light → light tokens, else → dark tokens`** — see §1.3; the naive `Dark → dark, else → light` form is a live bug.
- **Floor bump**: `setColorScheme`/`unsetColorScheme` is **Qt 6.8**. `pyproject.toml` currently declares `PySide6>=6.7`; raise it to `>=6.8`. The lock is already 6.11.1, so this is a one-line change with no upgrade cost.

**`app.setStyle("fusion")` unconditionally, on every Windows version.** It is what the winning direction was proven on; the Win11 default `windows11` style draws its own control assets and is the least cooperative base for the heavy restyling in §1.5; and it collapses the Windows 10 divergence, where the default `windowsvista` style cannot render dark at all. Fusion costs nothing under a contrast theme (§1.3).

**Window chrome**: keep **native frames** on settings, history and the wizard. Frameless loses Aero snap and snap layouts (`startSystemMove/Resize` does not restore them — QTBUG-84466), and restoring them needs community-maintained `WM_NCHITTEST`/`WM_NCCALCSIZE` handling. Windows 11 rounds those windows' corners itself; `DWM_WINDOW_CORNER_PREFERENCE` (attr 33) is only worth an explicit call if a window needs to opt out or force a radius. **Dark title bars are automatic** when the app palette is dark — no `DWMWA_USE_IMMERSIVE_DARK_MODE` call in the normal path.

> `DWMWCP_ROUND` is a **no-op on the pill** and must not be used there. The pill is frameless and translucent and paints/renders its own corners; there is no frame for DWM to round. (#60's original line said the opposite; corrected by #68.)

**Residual limits to honour:**
- Native file dialogs are OS-drawn and follow the Windows setting regardless of our override. Any path-browsing dialog (a GGUF model is the plausible case) must pass `QFileDialog.DontUseNativeDialog`.
- `QMessageBox` is a Qt widget and follows the palette; no special handling once `setColorScheme` is in place.

## 1.3 High Contrast: drop the sheet ([#74](https://github.com/mikeallisonJS/cadent/issues/74), [#75](https://github.com/mikeallisonJS/cadent/issues/75))

Under a Windows contrast theme, **nothing happens to a QSS-styled window, and the failure is total** — a contrast theme changes the palette, not our stylesheet, and a QSS declaration with a hard-coded colour beats the palette on every widget it matches (verified: palette `Window`/`Base` forced to `#ff0000` + `background-color: #2a1d4a` in QSS renders `#2a1d4a`).

**Posture: drop the stylesheet.** `setStyleSheet("")` while a contrast theme is active, restore on the way out. One branch at one call site. Fusion then draws from the system palette, which under a contrast theme already *is* the `GetSysColor` contrast palette (`QApplicationPrivate::basePalette()` prefers the platform theme's palette; `QFusionStyle::polish(QPalette&)` is a no-op). We lose the visual identity; the user gets a readable app. Never wrong.

**Detection**, in order of preference:
1. `QStyleHints::accessibility()->contrastPreference() == Qt.ContrastPreference.HighContrast`, plus `contrastPreferenceChanged`. Added in **Qt 6.10**; the lock is 6.11.1.
2. Below 6.10 — which the `>=6.8` floor permits — a ctypes `SystemParametersInfo(SPI_GETHIGHCONTRAST)` / `HCF_HIGHCONTRASTON` probe, same shape as the `SPI_GETCLIENTAREAANIMATION` probe in §4.6. Implement both; select on `hasattr`.
   > *Synthesis note*: #65 set the floor at `>=6.8` and #75 assumed 6.10 detection. Keeping the 6.8 floor with a ctypes fallback satisfies both; raising the floor to `>=6.10` instead is a defensible simplification if the maintainer prefers it.

**PySide6 binding gotcha, reproducible 5/5**: `app.styleHints().accessibility().contrastPreference()` raises `RuntimeError: Internal C++ object (QAccessibilityHints) already deleted`. **Hold a Python reference to `styleHints()` first.**

**High Contrast outranks the user's Light/Dark override** — the sheet is dropped either way, so the override has nothing to act on. It is detected by `contrastPreference()`, **never** by `colorScheme()`.

**The `Unknown` trap.** `colorScheme()` returns `Qt.ColorScheme.Unknown` under High Contrast on Windows (Qt's own `windows11` style uses exactly that as its HC test). Two consequences:
- `colorSchemeChanged` **already fires** on a contrast toggle, so §1.2's handler runs at the right moment for free. It is a trigger, not a discriminator.
- Any `Dark → dark, else → light` branch silently serves **light** tokens under a dark contrast theme. Writing it `Light → light, else → dark` kills the bug by construction and lands the overloaded `Unknown` on dark — the design's home column.

**Live changes are free.** Qt runs a hidden top-level handling `WM_THEMECHANGED` / `WM_SYSCOLORCHANGE` / `WM_SETTINGCHANGE("ImmersiveColorSet")` and rebuilds palettes synchronously. No message loop of ours.

**Two things that survive the sheet drop:**
- **The mic level meter** is custom-painted. Under HC it takes colours through a single accessor limited to **`window` / `window-text` / `button` / `button-text`** — the roles verified as unconditional `GetSysColor` reads. **Never `highlight`, never `accent`**: `Highlight` may be the WinRT personalisation accent rather than `COLOR_HIGHLIGHT`, and `Accent` is a derived `accent.darker(120)`.
- **The wizard's embedded real pill keeps its real violet.** It is a preview of a surface that itself opts out of High Contrast (§4.6), so a system-coloured imitation would teach the user a picture they will never see on their desktop.

**The Appearance control stays enabled** under a contrast theme. It still saves and takes effect the moment HC goes off, and it carries its reason — *"Windows is using a contrast theme, so Cadent is following your system colours"* — both visibly and as its `accessibleDescription`. Disabling it would be the accessibility smell: Qt drops disabled widgets from the tab order, so the explanation would be unreachable by exactly the user who needs it.

**Also note**: `palette(<role>)` in QSS is real and all 21 roles resolve, but the resolved brush is cached per widget and nothing invalidates it on `ApplicationPaletteChange` — a palette change alone leaves existing widgets stale while new ones update (measured as a half-repainted window). Re-applying the sheet is required regardless.

## 1.4 The token set ([#71](https://github.com/mikeallisonJS/cadent/issues/71))

One Python dict in three parts: theme-independent **BASE**, plus **DARK** and **LIGHT** colour columns with **identical key names**. Prototype: [`prototype/design-tokens`](https://github.com/mikeallisonJS/cadent/tree/prototype/design-tokens) → `scripts/prototype_design_tokens.py` (`T` flips theme live, `--screenshots <dir>` renders all six surfaces).

### Colour columns

| token | dark | light |
|---|---|---|
| `bg` | `#14111a` | `#f1eff7` |
| `bg_sidebar` | `#191521` | `#f7f6fb` |
| `sidebar_border` | `#241e30` | `#e4e0ee` |
| `surface` | `#1c1726` | `#ffffff` |
| `surface_hover` | `#221c2e` | `#f4f2f9` |
| `field` | `#221c2e` | `#f5f3fa` |
| `field_border` | `#322a44` | `#d8d3e6` |
| `selected` | `#2a2140` | `#ebe4fe` |
| `border` | `#302943` | `#e6e2f0` |
| `border_strong` | `#3a3350` | `#c9c2dc` |
| `text` | `#edeaf6` | `#1b1826` |
| `text_dim` | `#a79fc0` | `#57506e` |
| `text_faint` | `#8b83a6` | `#6d6685` |
| `accent_fill_a` / `accent_fill_b` | `#9678ff` / `#7a5cff` | *same* |
| `accent_fill_hover_a` / `_b` | `#a488ff` / `#8a6eff` | `#8a6bf5` / `#6d4df5` |
| `accent_text` | `#9678ff` | `#6f47e0` |
| `accent_soft_bg` | `#221a38` | `#f4efff` |
| `accent_soft_border` | `#9678ff` | *same* |
| `on_accent` | `#ffffff` | *same* |
| `danger` | `#ff6b6b` | `#c62f2f` |
| `warning` | `#f0a94c` | `#9a5c07` |
| `success` | `#4fd18b` | `#12754a` |
| `scrollbar` | `#322a44` | `#d8d3e6` |
| `focus_ring` | `#9678ff` | `#6f47e0` |
| `focus_ring_on_accent` | near-white | near-white |

Three naming rules the light column forced out, which implementations must not undo:

1. **`accent` is split, permanently.** `#9678FF` on white is ~3:1 — fine as a filled gradient pill with white text, not fine as a violet link, label or caps heading. Any violet **fill** uses `accent_fill_a`/`_b` (identical in both themes — the brand mark doesn't move); any violet **text, icon or caps heading** uses `accent_text` (`#6f47e0` in light: 5.75:1 on white, 5.04:1 on `bg`).
2. **`field` is not "one step raised".** Dark elevation runs `bg` < `bg_sidebar` < `surface` < `field`. Light does **not** mirror it: the card is white, so the field sits *below* it. The name means "the input's own surface", not "lighter than the card".
3. **State colours are not one hue with two tints.** Dark `danger` `#ff6b6b` is 2.9:1 on white. Light needs genuinely darker, more saturated values, not the dark column dimmed.

**Light neutrals keep the violet cast** rather than going neutral grey — a neutral-grey light column reads as a different app's theme. Cards stay pure white so they lift off the tinted window.

### BASE

- **Type**: `"Segoe UI"`, sizes in pt — `fs_display` 22 · `fs_title` 18 · `fs_row` 10.5 · `fs_body` 10 · `fs_desc` 9 · `fs_caps` 8.5. Weights `fw_regular` 400 · `fw_medium` 600 · `fw_bold` 700. `tracking_caps` 2.0px at `fs_caps`.
- **Spacing** (4-based): `sp_1` 4 · `sp_2` 8 · `sp_3` 12 · `sp_4` 18 · `sp_5` 28 · `sp_6` 36.
- **Radii**: `r_field` / `r_nav` 9 · `r_row` 12 · `r_card` 14 · `r_btn` 17.
- **Controls**: toggle 38×20 with a 14px knob · radio 16 · combo arrow well 26 with a 12×8 chevron · row padding 18/12 · sidebar **minimum** width 220 (see §8.5 — not a fixed width).
- **Focus** (§8.2): ring width 2, offset 2, `outline-radius` tracking each control's own radius.
- **Pill geometry and motion**: §4.7.
- **Tray colours**: §3.1.

### The contrast audit is part of the deliverable

The prototype prints a startup audit — 17 fg/bg pairs × 2 themes, plus every tray colour against both taskbar polarities, each with ratio, target and pass/fail. **Port it into the repo as a test or a script the render pass runs**, and extend it to cover the focus ring against every surface it can land on (§8.2). It already caught a real bug: dark `border` at `#2a2438` sat at 1.17:1 against `surface` — an invisible card edge — and was bumped to `#302943` (1.27:1). Eyeballing a dark mockup does not catch that.

## 1.5 Two QSS facts that change the implementation ([#71](https://github.com/mikeallisonJS/cadent/issues/71))

**Qt QSS has no `letter-spacing`.** The winning mockup's tracked capitals (sidebar group labels, card headings) were being **silently ignored** — that look never actually shipped in the prototype that won. Tracking must be applied in code: `QFont.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking_caps)`. The spec requires a **helper that builds tracked-caps labels**; it cannot come from the stylesheet.

*(Contrast with `outline`, which §8.2 measured as genuinely honoured — the two are not the same story.)*

**Three indicators need generated pixmaps, not QSS**: the combo **chevron**, the toggle **knob** (QSS can draw the track, not a knob offset inside it), and the **ring radio** (the border-width trick renders as a crescent). All three are drawn from the token dict into `QPixmap`s at startup and referenced as `image: url(...)` — **derived from** the tokens, never a parallel hardcoded palette. Six pixmaps per theme: chevron, toggle off/on, radio off/on.

> **Trap**: `QSS image:` draws a pixmap at its natural pixel size and **clips** to the indicator box — it does not scale. A 2×-DPR pixmap into a 38×20 indicator renders as a clipped smear. **Generate at the window's live `devicePixelRatio` and regenerate on DPI change.** Do not bake in 2×. The cache key must also include the contrast state (§1.3) since the pixmaps vanish with the sheet.

---

# 2. The mark ([#73](https://github.com/mikeallisonJS/cadent/issues/73), [#69](https://github.com/mikeallisonJS/cadent/issues/69))

**One mic-derived mark is Cadent's entire visual identity** — the tray on both platforms, and, knocked out of a tile, the exe, the installer, the `.app` bundle and every window's title-bar icon. Splitting identity from function buys nothing and doubles the drawing; it also makes the Alt-Tab entry, the taskbar and the tray agree, where today three of the four are PyInstaller's default.

**The tray shows the mark bare; everywhere else shows it on a tile.** Same mark, two jobs. A 2px-stroke silhouette on transparency is right in a tray full of monochrome glyphs and reads as a placeholder at 512px in a Dock full of filled shapes. The tile is a rounded square — `rx 224` on the 1024 master, 21.9%, between Apple's continuous corner and Windows 11's near-square, which is the point of having one tile instead of one per platform — carrying a 135° gradient from `#8f78ff` to `#3f22b5` with the Ready mark knocked out of it in white at **62.5%** of the canvas.

The gradient is the only one in the product. An app icon is conventionally read as a lit surface; nothing else here is, and the tray mark in particular stays flat because the OS picks its ink.

**The tile is composited, never authored holding a copy of the mark.** `app-tile.svg` is the tile alone; `build_icons.py` renders the mark over it. A tile file containing its own copy of the geometry is precisely the parallel-copy drift [ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md) exists to undo — and the copy nobody would notice going stale, since that file is opened only when the app icon is being changed. A test asserts the tile source draws nothing but the tile.

**Below 32px the tile trades margin for glyph** — `0.80` of the canvas rather than `0.625`. Proportional scaling gives a 16px entry ten pixels for the whole mark and the mic stops being a mic. Every platform's own icon set does this; the discontinuity is invisible because nothing shows 24 and 32 side by side.

> **Superseded in part by [ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md).** Paused and Needs-attention are no longer recolourings: the three states differ **by shape, on both platforms**, and colour says nothing about state anywhere. The paragraphs below are updated to the mark as it ships; the tray's colour table in §3.1 is kept as the record of what it replaced.

**The geometry: a solid capsule in an open cradle, plus a state element.** 24-unit master — the cradle scaled 0.84 about the box centre so its ink spans x 5.7–18.3, which frees the zone at **x 18.6–23.4** for the state. The scale is baked into the coordinates rather than carried as a transform, so every number in the SVG is the number that rasterises: capsule `rect(9.48, 4.02, 5.04, 8.82, r2.52)`; cradle `M6.54 10.74 a5.46 5.46 0 0 0 10.92 0` stroked 1.68 with round caps; stem `rect(11.16, 15.78, 1.68, 2.94)`; base `rect(8.22, 18.38, 7.56, 1.85, r0.92)`.

**The three state elements**, all inside that zone:

| State | Element |
|---|---|
| Ready | three flow lines, `rect(18.6, {7.5, 12, 16.5}, {4.8, 3.6, 2.4}, 2, r1)` — a taper running out of the mic |
| Paused | two bars, `rect({18.8, 21.6}, 8.5, 1.6, 7, r0.8)`, centred on 21 |
| Needs attention | an exclamation — `rect(19.8, 7.5, 2.4, 6.5, r1.2)` over `circle(21, 17.2, r1.5)` |

**Draw a single un-outlined silhouette.** A 1px dark keyline is the obvious escape from the tray colour band (§3.1) and it **fails at 16px** — it eats the glyph and reads heavy and smudged on the light-taskbar row, and it adds a second element to the SVG pipeline for nothing.

**Two grids, and the seam is deliberate.** Authored as SVG per state and rasterised at build time from:
- a **24-unit master** for 24px and up, and
- a **hand-corrected 16-unit source** for 16 and 20 — every edge on a whole pixel, cradle thickened from 1.5 to 2 so it lands cleanly on rows 10–12, base dropped to a single pixel row.

At 16px the master scaled down puts the cradle across three rows at half alpha and it reads as a grey smudge. **The seam sits between 20 and 24; a discontinuity in the coverage table there is the correction, not a bug.**

**The 16 grid may simplify, not merely pixel-tune.** It does *not* reproduce the master's 0.84 cradle — at that size the difference is under a pixel and shrinking costs more legibility than the clearance buys, so the cradle stays full size on the box centre (ink 3–13) and the state gets the three columns at 13–15. Within them the flow lines taper 3/2/1, the pause bars are two single columns with a single-column gap, and the exclamation is a one-column stroke over a one-pixel dot. Divergence at this grid is the point of having it.

## Deliverables (originally lifted from [`prototype/mark`](https://github.com/mikeallisonJS/cadent/tree/prototype/mark); the geometry and the raster set were redrawn by [ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md))

- `packaging/icons/mark-{ready,paused,attention}.svg` — 24-unit masters
- `packaging/icons/mark-{ready,paused,attention}-16.svg` — hand-corrected 16-unit sources
- `packaging/icons/png/mark-<state>-<size>.png` — 18 rasters at **16 / 20 / 24 / 32 / 48 / 256**, **alpha only**: the silhouette is the asset and the ink is a runtime decision (ADR 0006)
- `packaging/icons/app-tile.svg` — the app icon's tile, and nothing else; the mark is composited over it at build time
- `packaging/icons/cadent.ico` — multi-size, the tile with the Ready mark knocked out in white. Also what `icons.app_icon()` loads at runtime: on Windows a *running* app's taskbar button takes its icon from `setWindowIcon`, not from the exe, so pointing it anywhere else makes the button disagree with the Start-menu entry that launched it
- `packaging/icons/cadent.icns` — the same tile for the `.app` bundle and the DMG's volume icon ([#171](https://github.com/mikeallisonJS/cadent/issues/171))
- `scripts/build_icons.py` — regenerates all of it

Loaded via **`QIcon.addFile()`** on the PNG set so Windows picks the exact size it asks for (16 @100% DPI, 20 @125%, 24 @150%, 32 @200%) and **never scales**. This also avoids shipping Qt's SVG image-format plugin — a classic PyInstaller silent failure where the icon works in dev and is blank in the built exe.

> **Implementation fact: Qt's ICO handler is read-only** — `QImage.save(..., "ICO")` is not available. `build_icons.py` assembles the container by hand: 6-byte header, a 16-byte directory entry per image, **PNG payloads** (what Windows has read since Vista, and what keeps the 256px entry from ballooning the file the way a BMP entry would). **The directory's single-byte size fields use 0 to mean 256.** Verified: Qt reads all six sizes back at exact dimensions with no scaling, and `System.Drawing.Icon` parses the file.

**Wiring is this spec's slice, not the prototype's**: point `app.py`'s tray icon, `packaging/cadent.spec` and `packaging/cadent.iss` at these files, and set the app-wide window icon.

**The pill's three glyphs** (mic, flow-mic, warning) are drawn by the same hand at `pill_glyph` 18px, in `hud_text` / `hud_danger`. They only ever sit on `hud_surface`, which is dark in **both** themes, so the both-polarity constraint does not apply to them. **The flow variant needs a silhouette difference readable at a glance** — the placeholder's diagonal cut through the capsule was invisible at 18px. At that size a subtle mark is no mark.

The mic sits at the mark's 0.84 cradle on the box centre in **both** pill variants, and the flow lines occupy the same 18.6–23.4 zone they do in the tray. The flow variant used to shift the mic left to make room; it no longer moves or resizes, because a glyph that changes size when cleanup toggles reads as a rendering fault rather than as information. The warning triangle stays its own glyph and is deliberately *not* unified with the tray's exclamation — the pill is a transient HUD and the triangle is the stronger transient signal.

---

# 3. Tray icon and menu ([#69](https://github.com/mikeallisonJS/cadent/issues/69))

## 3.1 The icon signals availability only

**Three states: Ready / Paused / Needs attention.** Three silhouettes; the mic never changes.

| State | Element | Meaning |
|---|---|---|
| Ready | flow lines | the hotkey will produce text |
| Paused | pause bars | you turned dictation off |
| Needs attention | exclamation | something is degraded or unseen |

> **Superseded by [ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md).** This used to read "one silhouette, three colours", with `tray_ready` `#7a5cff`, `tray_paused` `#8b849e` and `tray_attention` `#cc5f14`. Colour left the tray when the mark unified across platforms: macOS repaints a mask and throws any colour away, so a state signal that only worked on Windows was a second mark in disguise. The luminance analysis kept below is the record of choosing those three values, not a live constraint on the tray — **Needs attention losing its orange is the known cost**, accepted because shape survives a colour-vision deficiency and the alternative was maintaining two marks. The tooltip carries the words.

Raw-vs-flow **mode comes off the icon entirely** and lives in the tooltip and the menu checkbox. The pill now answers "which mode am I in?" positively at the only moment it matters — the moment you speak — and a 16px icon Windows frequently collapses into the overflow flyout is the wrong surface for a mode you check *while dictating*. What the tray is uniquely good at is the question the pill cannot answer because it isn't on screen: **"is this going to work if I press the key?"** Activity states (recording, transcribing) are excluded — they would be a third indicator of the same fact, after the pill and Windows' own mic-in-use indicator, animating in a place most users cannot see.

**The ink comes from the surface the mark sits on** ([ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md)). This paragraph used to say the opposite — *"theme-agnostic, tracking nothing … reading `SystemUsesLightTheme` from the registry and watching it for a 16px glyph is not worth it"* — and that held only while the glyph had a colour of its own to be legible in. It does not any more.

macOS ships the mark as a mask and the menu bar paints it. Windows has no such mechanism, so Cadent paints it: `SystemUsesLightTheme` under `HKCU\…\Themes\Personalize` is the **taskbar's** setting, while `AppsUseLightTheme` beside it is the one Qt reports through `colorScheme()` — dark taskbar with light apps is a common configuration, and reading the wrong one paints black on black. It is watched live with `RegNotifyChangeKeyValue` on a thread, because the package stays Qt-free (ADR 0005). Cadent's own Light/Dark preference never reaches the tray; under a contrast theme the ink is `COLOR_WINDOWTEXT`.

**Why these three values, so they don't look arbitrary later.** For a colour to clear 3:1 against **both** `#1f1f1f` and `#f3f3f3`, its relative luminance must land in `[0.143, 0.263]` — a band barely 1.8× wide. Measured: ready 3.77 / 3.94 (L 0.190) · paused 4.62 / 3.21 (L 0.245) · attention 4.09 / 3.63 (L 0.211). Rejected candidates, kept as the record: a "safe band" triad whose amber `#b8700c` read as brown rather than alarm; a vivid triad whose paused (2.65) and attention (2.42) **failed** on a light taskbar; and that vivid triad rescued by a dark keyline, which fails at 16px (§2).

> **Honest limitation, and why §3.2's header matters more than it looks**: the band caps luminance separation at ~1.3× between any two of these. Luminance ordering exists but **hue and chroma do the work** — saturated violet, desaturated true grey, saturated orange. That is a consequence of the both-polarity requirement, not a compromise on it.

Colourblind legibility survives the ladder (violet / grey / amber read as blue / grey / yellow under common red-green deficiencies), but the background is the unresolvable variable.

*Rejected: a slash through the mic for Paused* — a shape difference would have followed §4.4's rule, but a single silhouette that never changes geometry won.

## 3.2 Menu

```
  Cadent — ready · cleanup off     (disabled)
  ─────────────────────────────────
  ✓ Clean up transcripts (AI)
    Pause dictation
  ─────────────────────────────────
    Settings…
    History…
  ─────────────────────────────────
    Download GPU support pack (…)    (conditional — separator and all)
  ─────────────────────────────────
    Quit
```

When setup is unfinished (§6.4):

```
  Cadent — setup unfinished       (disabled)
  ─────────────────────────────────
    Finish setup…                    (bold)
  ─────────────────────────────────
    …rest, dictation toggles disabled
```

- **The disabled status header is what makes a glyph-only icon safe.** One click always resolves ready-vs-paused, it names the mode the icon no longer carries, and it is the only surface that can state a **combination** in words — `Paused · speech model failed`. (This read "colour-only" while the states were colours; [ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md) made them shapes, and the header's job is unchanged either way.)
- **Separator-grouped sections keep conditional items from shuffling the action list.** The GPU item is conditional and lives in a section of its own, so Settings and History never move under the cursor when it appears. Since [#110](https://github.com/mikeallisonJS/cadent/issues/110) removed "Run setup wizard" it is the *only* thing in that section, which makes the separator conditional too — **it is hidden and shown with the item it groups**, or the menu every non-NVIDIA user sees renders `History… ⎯ ⎯ Quit`. Folding the item in with Settings and History instead would drop the no-shuffling guarantee, so the section stays.
- The menu is a `QMenu` — a real widget — **restyled by the app-level QSS and following the app theme** like every other surface, on Fusion. The icon does not follow the *app* theme, and the menu does — but the icon is not theme-blind either: it takes its ink from the tray surface it sits on, which on Windows is the taskbar's own colour mode and on macOS is whatever the menu bar paints a mask ([ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md)). Two different settings, deliberately.

## 3.3 Left-click toggles pause

`activated` is never connected today — left-clicking the icon is inert. Connect it: **left-click flips pause**, making the most reversible state change a single click. **Double-click is the same action, not two.**

**Confirmed by the icon and tooltip alone — no toast.** A tray click happens with your eye already on the icon, so the mark changing to the pause bars lands exactly where you are looking; if the icon is in the overflow flyout, that flyout stays open showing it. A toast for something you did while watching it change is noise. (`_on_cleanup_hotkey`'s toast exists precisely because a hotkey press has *no* co-located feedback.) The accidental-click failure — pausing without noticing, then finding the hotkey does nothing — is caught downstream by §4.3's **Paused** pill.

## 3.4 Needs attention: anything degraded, but offers clear on view

> This section said **amber** throughout while the state was a colour. [ADR 0006](../adr/0006-one-tray-mark-on-both-platforms.md) made it the exclamation mark instead; everything below is about *when the state fires and when it clears*, which the change did not touch.

**Needs attention fires whenever Cadent is not running at full capability**, which is broader than "broken": the speech model failed to load, flow-mode cleanup failed, the engine crashed and is rebuilding on CPU, setup is unfinished (§6.4), **a pending GPU-pack offer**, and **an unreadable `config.json`** (§7.2).

Surfacing the GPU offer beyond a single toast is deliberate — `_offer_gpu_pack` relies today on one 10-second toast plus a menu item the user has no reason to look for. But that breadth creates a live trap: the menu item, once shown, stays visible forever and the eligibility check re-runs every launch, so an NVIDIA user who never installs the pack would sit **permanently in the attention state**, which trains them to ignore it. The clearing rule therefore splits by kind:

- **Offers clear on view.** The GPU-pack offer drives the attention mark only until the user has seen it — it drops the first time the tray menu is opened after the offer fires. **The menu item remains available forever**; only the mark stops.
- **Faults clear on fix.** Every other condition holds the attention mark until it actually resolves.

The attention state therefore means *"there is something here you have not seen yet, or something is genuinely wrong"*, and can never become permanent scenery.

## 3.5 Precedence: paused > attention > ready

**Paused outranks attention.** While paused nothing is going to run anyway, so a fault is not yet actionable, and the icon should reflect the state you deliberately chose; the attention mark reappears the instant you resume. The cost — a real problem off the icon until you resume — is absorbed by the status header, which **names both facts**. The mark carries one state; the header carries the combination.

---

# 4. Overlay pill ([#68](https://github.com/mikeallisonJS/cadent/issues/68), [#71](https://github.com/mikeallisonJS/cadent/issues/71))

The pill keeps its shape and its job — a small, click-through, bottom-centre HUD — but becomes the app's **guaranteed** feedback channel and stops speaking a different visual language from the rest of the app.

## 4.1 No live transcription preview — settled, not deferred

Ruled out. The architecture is record-then-transcribe (`Recorder.stop()` returns the whole buffer; `Pipeline.process(audio)` runs once); a preview means chunking and re-transcribing a growing window **during** recording, competing for CPU with the real transcription on a machine where the audio stack already loads the CPU — spending the ≤2 s / ≤3.5 s budget to preview it. Partials are also pre-vocabulary, pre-snippet, pre-cleanup, so in flow mode the user would read text that isn't what gets inserted. And a 200×44 click-through glanceable HUD would become a growing, wrapping, reading-demanding text surface. (Reinforcing detail: text on a translucent window loses subpixel antialiasing.)

The reassurance a preview would have given is delivered by the meter rework (§4.5) instead. **No streaming-partials research is needed.**

## 4.2 Pill = terse failure. Toast = detail.

The two channels overlapped unevenly. They are now split by rule:

- **The pill carries the terse failure**, three words or fewer, always. It is the **primary** signal, because **Windows 11 Do Not Disturb silently suppresses tray toasts** and turns itself on during presentations and full-screen games — exactly when you'd most notice text not appearing. Nothing suppresses our own always-on-top window.
- **The toast carries the detail** — why, and what to do about it ("the transcript is in History", the learned-override notice). Best-effort delivery.

Two corrections fall out:
- **`not-ready` gains a toast** — *"The speech model is still loading — try again in a moment"*. It was the one outcome with no detail channel.
- **`notify-only` stops hiding the pill silently.** It gets the same terse treatment (*"Not inserted"*); a wordless success-hide teaches the user their words vanished for no reason.

## 4.3 States

| State | Renders | Note |
|---|---|---|
| **Recording** | level meter + mode glyph | |
| **Transcribing** | indeterminate + mode glyph | today's "processing" |
| **Cleaning up** | indeterminate + label | flow only |
| **Cancelled** | terse "Cancelled", ~1 s | mid-hold cancel only |
| **Failure** | warning glyph + terse label | |
| **Paused** | terse "Paused", ~1.5 s | on a hotkey press while paused |
| *hidden* | — | success stays silent |

Three events with **no feedback at all** today are now covered:

- **Mid-hold cancel** — you can talk for eight seconds, hit a key, and the pill just vanishes, indistinguishable from a crash. Now shows **Cancelled**. A **sub-min-hold release stays silent** — that's "that wasn't a dictation", and flashing on every accidental brush of the hotkey would be noise.
- **Max-utterance cutoff** — recording is cut off while you're still holding the key and still talking, and the pill flips to processing with no explanation. It is **not** a pill state (the pill's job at that moment is to show work is happening); it gains a **toast**: *"Stopped at 60 s — transcribing what was captured"*.
- **Hotkey pressed while paused** — currently inert, which reads as broken. Now shows **Paused**.

**Processing splits into Transcribing / Cleaning up.** Flow's ≤3.5 s vs raw's ≤2 s is entirely the LLM; naming the second phase explains the wait rather than leaving an undifferentiated longer spinner. **Cost, and it is outside `overlay.py`: one new signal from the pipeline at the STT→cleanup boundary.**

## 4.4 Shape carries mode, colour carries state

Violet becomes the accent for anything active, which collides with its current job as the flow-mode dot. Beyond the collision, the existing indicator is unreadable on its own terms: **flow mode is signalled by a dot that is either there or not there**, so with nothing to compare against you can only tell "there is a dot", never "flow is on".

- **Leading glyph = mode, always positively rendered.** Mic glyph in raw mode, a distinct flow glyph in flow mode. The pill answers "which mode am I in?" *before* you speak.
- **Colour = state; violet = activity.** Meter and indeterminate indicator both draw from the accent gradient; failure is `hud_danger`; Cancelled and Paused are `hud_muted` — they're non-events, not alarms.

Four hues drop to two plus neutral, which is what makes the pill read as the soft-dark design rather than as a status LED. *(The traffic-light alternative is more instantly legible but visibly belongs to a different app than the settings window.)*

> This kills "violet badge = flow" for the tray too — hence §3.1.

## 4.5 Level meter

Today's single green bar maps `min(1.0, level * 12)` — an unexplained gain constant on a **linear** RMS→width map, so normal speech pins it near full while quiet speech barely moves it. It reads as "on", not as "hearing you", which is the reassurance the meter exists to give.

- **Symmetric multi-bar equalizer** — bars mirrored around a centre line, filled with the accent gradient. Reads as *voice* at a glance.
- **Log/dB mapping**, ranged so conversational speech lands mid-scale with headroom above.
- **Fast attack, slow decay** (~200 ms fall). Without decay the bars flicker at 30 fps; with it the meter looks alive between syllables.
- **A visible floor** — bars never fully collapse; a meter at literal zero is indistinguishable from a frozen one.
- **Silence cue**: if the level holds at the floor for ~2 s while recording, the bars go muted (`hud_meter_silent`). Free diagnosis for the most common real failure — the wrong input device — which the current meter hides. In the render pass it reads unmistakably as *flat*, not as *stopped*, which is the point.

## 4.6 Position, motion, construction

**Position: bottom-centre of the screen holding the focused window**, resolved at `show_recording()` time from the foreground window's rect, falling back to the cursor's screen, then primary. **This fixes a live multi-monitor bug**: `self.screen()` on a never-shown widget is the *primary* screen. Bottom-centre, not caret-following — caret tracking needs per-app UIA, fails in many apps, and jitters; a fixed place is one your eye learns.

**User-movable, but only from settings.** The pill stays `WA_TransparentForMouseEvents` at **all** times during dictation — PRD §5.7's click-through property is preserved and there is never a dead zone that eats clicks or a stray drag that relocates the overlay. Settings gets a **"Move overlay…"** mode showing a mouse-enabled *dummy* pill with **Drag / Done / Reset**, plus a persisted **"Snap to guides"** toggle (soft-snap to centre and to the bottom margin) — snapping is configurable, not always-on. The anchor commits on **Done**: one write per gesture (§7.3).

**Stored as a normalized anchor** — edge/corner plus a fractional offset within available geometry, not absolute pixels — so a custom position survives a monitor being unplugged and still follows the focused window's screen. Reset returns to bottom-centre at `pill_margin_bottom`.

**Construction: hybrid.**
- **Chrome is QSS.** A child `QFrame#overlayPill` inside the translucent top-level, zero margins: background, `border-radius`, hairline border and padding all render from the same app-wide stylesheet as every other surface, and the terse label is a `QLabel` inside it. The HUD token becomes literally one rule — `#overlayPill { background: {hud_surface}; }` — so the pill can never drift from the token set.
- **Indicators are custom-painted child widgets reading the token dict directly**, not parsed back out of a stylesheet. The meter and the indeterminate animation are frame-by-frame drawing; QSS has no vocabulary for it, and faking the meter with a stretched `QProgressBar` fights the shape and gains nothing.

**Motion.** There is no animation today, and `show_processing()` doesn't even re-place or re-show — it mutates `self.state` and rides the recording timer. State-driven geometry means every state change re-places and re-sizes, so this is rebuilt regardless.

- **Recording snaps in at full opacity. Zero fade.** The pill is the "I'm listening" signal and nothing may delay it.
- **More important than the fade — a requirement, not an optimization:** `Overlay()` is constructed at startup but never shown until the first dictation, so Qt doesn't create the native window until the first `show()`, and **the first pill of every session pays window-creation and first-paint cost no later one does**. The window must be **realized at startup** — created and positioned, held hidden.
- Reactive states (failure / cancelled / paused) fade in `fade_in_ms`; **all exits fade `fade_out_ms`**, including the silent success exit — an instant disappearance reads as a glitch, a fade reads as completion.
- **The meter morphs, it doesn't swap**: recording's bars stop tracking your voice and become an indeterminate sweep in the same geometry, so recording→transcribing feels like one continuous act.
- **Width changes tween** (`width_tween_ms`) so a failure label doesn't snap the pill wider under your eye.
- **Minimum on-screen time `min_visible_ms`.** A two-word dictation on GPU can go recording→transcribing→hidden in under 200 ms; the floor stops it strobing.
- **Stay at 30 fps.** The meter repaints *while the audio stack is capturing*, on a CPU-loaded machine; doubling the paint rate buys nothing for a bar that's smooth at 30.
- **Honour `SPI_GETCLIENTAREAANIMATION`.** When Windows animations are off, fades and tweens are skipped and states snap — **but the meter keeps animating**: it's information, not decoration, and stopping it removes the only proof the mic is live.

**High Contrast: the pill opts out.** It is a HUD over other apps with its own non-inverting surface; forcing it into the system contrast palette would put a bright slab over the user's document. Screen-reader-wise it is an always-on-top tool window with no focus and no interaction and should be **marked as such rather than announced** (§8.4).

## 4.7 Geometry and motion tokens

`pill_h` **40** · `pill_r` **20** (= h/2, a true pill at any width) · `pill_min_w` **132** · `pill_max_w` **280** · `pill_margin_bottom` **80** · `pill_shadow_margin` **28** · `pill_pad_h` 14 · `pill_glyph` 18 · `pill_gap` 10.

- **Fixed height, content-sized width**, growing symmetrically about the anchor, clamped to min/max then clamped to available geometry so a pill dragged near an edge can't grow off-screen.
- **`pill_max_w` is load-bearing, not cosmetic.** It came down from a first pass at 320: *"Speech model still loading"* — the longest label anyone has proposed — measures ~215px with its glyph, and 280 leaves headroom for one more word and **nothing else**. If a failure label doesn't fit, **the copy is wrong, not the pill**. (Today's error text draws into ~154 px, so that string clips.)
- **The leading glyph slot is state-aware**: mic / flow-mic while active, warning glyph on failure, and **nothing** for Cancelled and Paused — those collapse to a short label in a small neutral pill, so the size difference is itself a signal.
- **`pill_shadow_margin` is a geometric consequence**: the shadow needs room, so the translucent top-level is **96px tall for a 40px pill** (40 + 2×28), and **`_place()` must offset by it** or the pill floats 28px above its bottom margin.

**HUD surface, per theme, never inverting:**

| token | dark | light |
|---|---|---|
| `hud_surface` | `#15121c` | `#262130` |
| `hud_alpha` | 248 | 250 |
| `hud_border` | `rgba(255,255,255,0.14)` | `rgba(255,255,255,0.14)` |
| `hud_text` | `#f3f0fa` | `#f6f4fb` |
| `hud_text_dim` | `#b6adce` | `#c2bad6` |
| `hud_danger` | `#ff7a7a` | `#ff8a8a` |
| `hud_muted` | `#8d84a6` | `#9a92b2` |
| `hud_shadow` | `rgba(0,0,0,0.59)` blur 26 dy 8 | `rgba(31,26,45,0.35)` blur 30 dy 10 |

**The pill is themed but its contrast direction is fixed.** It is always the deep, high-contrast element in *both* themes — light mode gives it **a different dark** (`#262130`, a touch lighter and cooler), never a light pill. Its background is foreign content (the user's document), so inverting it would make it vanish over a white page in exactly the case light mode exists to serve. **Near-opaque**, not today's 230/255: the pill sits over white pages, video and code; legibility beats translucency, and there's no subpixel crispness left to trade away.

> **Rule from the over-real-backgrounds render pass**: the weak case is a **dark pill on a dark code editor**, where the near-opaque surface has almost nothing to separate from and the shadow does nothing. **The hairline is the entire separation there** — which is why dark `hud_border` went from 0.10 to 0.14 alpha to match light's. *The hairline is load-bearing on dark content, the shadow on light content, and neither alone is sufficient.*

**Meter**: `meter_bars` 14 · `meter_bar_w` 3 · `meter_gap` 3 · `meter_bar_r` 1.5 · `meter_floor_h` 3 · `meter_span` 22 (mirrored, ±11) — 81px wide overall. Gradient `hud_meter_a` → `hud_meter_b` (`#9678ff`→`#7a5cff` dark, `#a88fff`→`#8b6dff` light, lifted because the light pill's surface is lighter). `hud_meter_silent` `#56506b` / `#6b6484`.

**Motion**: `fps` 30 · `fade_in_ms` 100 · `fade_out_ms` 180 · `width_tween_ms` 150 · `min_visible_ms` 400 · `transient_ms` 1000 (Cancelled) · `paused_ms` 1500 · `meter_decay_ms` 200 · `meter_silence_ms` 2000.

## 4.8 Wizard try-it and the hide toggle

- **The wizard's hotkey try-it step uses the real pill, unmodified** — it teaches the hotkey and the pill in one motion. **Constraint on the wizard's layout**: the pill is always-on-top and the wizard window is not, so the try-it page must leave the bottom-centre clear (or reposition itself) rather than have the pill cover the instructions being read.
- **The overlay is hideable from settings — but the toggle governs the *activity* states only.** "Show overlay while dictating" hides Recording, Transcribing, Cleaning up, Cancelled and Paused; **failure states appear regardless.** This keeps the setting honest — it turns off the ambient indicator, not the alarm — and avoids a configuration (overlay off + Do Not Disturb on) where dictations fail into total silence and the app reads as broken.

---

# 5. Settings ([#62](https://github.com/mikeallisonJS/cadent/issues/62), [#63](https://github.com/mikeallisonJS/cadent/issues/63))

## 5.1 Structure

**Grouped sidebar, six panes of stacked section cards.**

| Group | Pane | Cards |
|---|---|---|
| **WORKSPACE** | General | **Setup** (re-run the wizard, §6.4 — first card since [#110](https://github.com/mikeallisonJS/cadent/issues/110)) · Basics (autostart · microphone · **Appearance** §5.3) · Overlay (§4.6/§4.8 controls) |
| | Hotkeys | one card: dictation hotkey, mode, flow-toggle, with inline validity |
| **TEXT** | Speech & cleanup | Speech recognition (model) · AI cleanup (flow mode, cleanup model) |
| | Vocabulary & snippets | two cards, §5.4 |
| | App overrides | per-app injection table, §5.5 |
| **PRIVACY** | History | Retention card + **embedded recent-transcript list**, §5.6 |

Visual language: near-black violet-tinted surfaces, rounded section cards with hairline row separators, generous padding, muted-lavender secondary text, gradient pill CTAs for primary actions, **tracked-caps** group labels and card headings (via the §1.5 helper, not QSS).

**Stock Qt controls do not pass** — every indicator is QSS-restyled: toggle-pill checkboxes, ring radios, chevron-icon combo arrows with a styled popup list. Three of these are generated pixmaps (§1.5).

**The sidebar is a `QListWidget`** with `Qt.ItemFlag.NoItemFlags` group headings — see §8.3. This picks the widget that delivers the chosen look; it does not re-open the structure decision.

## 5.2 Apply semantics: instant apply, no OK/Cancel

**Closing the window is done.** There is no OK, no Cancel, no draft.

- **Live fields apply silently.** Immediate is the default, so they carry **no badge** — badging the common case would imply the others are somehow more immediate.
- **Heavy fields apply on change and restart their engine in-process**, reading `applies on change · restarts <engine>` with an inline hint, e.g. *"⟳ Speech engine restarts when you pick a model (~2 s)"*.
- Writes fire on **field commit** — focus-out, Enter, add, delete, reorder — **never per keystroke**.

**This replaces the buffered draft/OK model in `cadent/settings_ui.py`, and the replacement goes further than it first looked** (correction from #76): with no OK there is no draft to diff, so **`restarts_needed(old, new)` and `_on_settings_committed` go away**, and `ENGINE_FIELDS` **inverts** into a field→engine lookup keyed on the single field that was written. The live/heavy split itself is untouched — it just keys off the written field rather than a two-config diff.

## 5.3 Appearance card ([#65](https://github.com/mikeallisonJS/cadent/issues/65))

One row in the General pane: a combo labelled **Theme** — System / Light / Dark. A combo rather than a segmented control because chevron combos with a styled popup are already proven in QSS; a segmented control would be a fourth control recipe to design and prove for a single setting. It is a **live field** — applies silently, no restart badge.

**The row needs a subtitle**, e.g. *"Follows your Windows app colour mode"*. Windows has two theme settings and Qt's `colorScheme` reports the **app** one, so a user running a dark taskbar with light apps who picks System will correctly get a light Cadent. Without the line that reads as a bug.

Under a contrast theme the control **stays enabled** and carries its reason visibly and as `accessibleDescription` (§1.3).

## 5.4 Vocabulary & snippets pane ([#66](https://github.com/mikeallisonJS/cadent/issues/66))

**The pane is how people work; the file stays valid, hand-editable and re-read per dictation.** Everything below follows from that stance.

**Vocabulary card** — two-column table, `Term | Sounds like`, both cells inline-editable, aliases entered comma-separated. *(The comma is safe: `soundsLike` aliases are matched through `normalize()`, which strips all punctuation, so a comma inside an alias never carried meaning.)* Rows render in **file order, which is biasing priority** — `pack_hotwords()` packs in file order and drops the overflow. New rows append; a drag handle reorders and says it rewrites order; a filter box narrows without reordering; **header-click sorts the view only**, so tidying can never silently demote terms out of the hint budget. Footer: `N terms` + an `Open vocabulary.json` link.

**Snippets card** — master–detail, because replacements are multi-line. A trigger list with one-line replacement previews on top; selecting a row opens `When you say [trigger]` / `Cadent types [multi-line box]` beneath. Same footer treatment.

**Apply semantics: writing the file *is* applying.** Both files are re-read at the start of *every* dictation, so **no `restarts <engine>` badge belongs on this pane** — there is no engine to restart. Writes fire on **field commit**, so a half-typed term is never live if a dictation starts mid-edit; blank rows are never written. Each write is a **read-modify-write against current disk contents applying only that row's delta**, so an external edit to a different entry is never stomped and `_comment` / `_editing` / unknown keys / key order round-trip **by construction** rather than by the serializer remembering. A `QFileSystemWatcher` refreshes the table when the file changes and nothing is being typed.

> Note the contrast with `config.json` (§7): same delta-write mechanics, **opposite apply contract**. That difference is deliberate and is stated in each file.

**Errors:**
- **File unparseable on open** — the card replaces its table with the loader's existing message (*"vocabulary.json is not valid JSON — vocabulary is off for this dictation"*) plus `[Open file]` and `[Reload]`. **Editing is disabled**, so the UI can never overwrite a file it failed to read.
- **User input** — inline, non-blocking warning on the row, and **the edit is written anyway**. Writing two colliding keys to JSON is lossless; the collapse only happens at load. Covers duplicate terms, triggers that normalize identically (*"my email signature"* vs *"My Email Sig!"* — "only one will win"), pure-punctuation triggers, empty replacements. **Nothing is refused or reverted under the user's hands.**
- **Hotword budget overflow** — today log-only by deliberate choice. Now surfaced **only when it actually bit**: the pipeline hands its `dropped` list to settings and the card adds a line — *"12 terms past the model's hint budget — still corrected in the transcript, just not hinted. Drag them up to prioritize."* Exact token counts, not an in-pane estimate (the budget needs the model's tokenizer). Silent for the majority who never approach it; reads stale until the first dictation of the session.

**Deletion** — `✕` writes immediately with an **Undo strip** on the card (*"Removed 'my email signature'. [Undo]"*), lasting until the next edit or pane change. **No confirm dialog on the pane's most routine action.**

**Seeded examples** — `ensure_example()` keeps writing the `_comment`/`_editing` prose (the only thing that ever taught hand-editors the JSON shape, and it round-trips untouched) but ships **no live sample entries**. The seeded `my email signature` snippet was live enough to inject placeholder contact details; the pane's empty state teaches by example instead. **No migration** — existing installs keep what they have.

**Toast** — the existing malformed-file notice becomes **clickable**, opening Settings on this pane where the error state and `[Open file]` are waiting. Still a toast, still never a prompt; it just leads somewhere now. *(No `messageClicked` wiring exists today — this is new.)*

## 5.5 App overrides pane ([#67](https://github.com/mikeallisonJS/cadent/issues/67))

**The pane *is* `app_overrides` — flat, complete, and the only place per-app injection is ever explained.**

**The list.** One table, **no stored provenance flag**: shipped defaults, learned rows and hand-authored rows all render alike, because any flag we invented would become a lie the first time someone hand-edits a shipped row. The 10 rows `_default_overrides()` writes on first run are not noise — they are the answer to *"why does dictation paste into my terminal?"*. **Sorted alphabetically in the view, file order untouched on disk**; new rows appended. Unlike vocabulary, order here is semantically inert — `resolve_override` is first-match-wins and duplicates are prevented, so no row can shadow another, and **no drag handle is offered because reordering would be theater.**

Columns: `App | Strategy` + a `learned` chip + `✕` + a disclosure chevron. **Strategy reads in plain language — Type it / Paste it / Don't insert — never the raw enum.** No filter box at ~12 rows. Footer: `N app rules` + `Open config.json`.

**Editing depth: full.** Strategy in the row; the five knobs behind a **strategy-scoped** disclosure mirroring the branch in `inject.py` — chunk size / chunk delay under *Type it*; paste chord, settle delay, restore-clipboard under *Paste it*; *Don't insert* shows only a line noting the transcript still lands in History. **Values for the other strategy are preserved on disk, not wiped**, so a hand-tuned row survives a round trip through the dropdown.

`clipboard-no-restore` and `restore_clipboard: false` are two spellings of one behavior, so **the dropdown offers three strategies** and restore is a checkbox under *Paste it* — and **the raw `strategy` string is only rewritten when the dropdown is actually changed**, so a hand-written `clipboard-no-restore` round-trips untouched. This depth is what makes the pane able to express the rows it displays: without the knobs it shows Notepad as plain "Paste" and hides the 500 ms that makes it work.

**Paste chord is a free-text field, not a key-capture widget** — it describes the *target app's* shortcut rather than one being pressed, and capture would fight Cadent's global listeners. It **echoes what it parsed and warns inline on unrecognized parts**, because `_parse_paste_chord` **silently drops** them and falls back to `ctrl+v` — so `cmd+v` today yields a working-looking row that pastes with the wrong chord. Written anyway, per §5.4's rule. Numeric fields carry units and bounds; **Notepad's 500 ms carries an inline note explaining it** rather than reading as a mystery number.

**Adding.** `+ Add app` opens a **typeable combo** suggesting *apps you've dictated into* (distinct `app_name` from `history.dictations`, recent first) then other running processes, with free text always accepted. This defends the only real failure mode: an executable name that never matches is a silent no-op forever. Degrades cleanly to running-processes-only when history is off or pruned. A **duplicate add — including an app a shipped default already covers — selects and flashes the existing row** instead of creating a second, dead one.

**Learned rows.** The `learned` chip is tooltip-bearing (*"Added automatically after typing failed in this app."*) — the only durable explanation of auto-learn anywhere in the app, since the toast is transient and fires once. **That makes the chip's column width load-bearing too** ([#101](https://github.com/mikeallisonJS/cadent/issues/101)): Qt widens a cell widget narrower than its own minimum size hint *rightward* rather than clipping it, so the Strategy column has to be sized from the combo that goes in it — left at a default the combo moved over the chip and painted it out entirely, tooltip and all. `ResizeToContents` cannot do the sizing, because it asks the delegate and the delegate does not know a cell widget is there. **Editing a learned row keeps the chip**: it is provenance — how the row was born — not a claim about its contents. **Deleting is "forget it", and re-learning is correct**: no tombstone, no `no_learn` flag, no undeletable rows. `learn_override` blocks on *existence*, not on the flag, so an app that failed in March and was fixed by an update deserves "delete and see"; if it fails again it re-learns and toasts again. A tombstone would invent a third state — *not in the list, but also not learnable* — that a hand-editor cannot see.

**Deletion.** `✕` with an Undo strip, no confirm dialog. For **learned** rows the strip says what will happen: *"Removed `slack.exe` — Cadent will try typing there again, and may re-learn it. [Undo]"*.

For rows whose process is in the **shipped set**, the strip explains why the rule exists — *"Removed `notepad.exe` — Cadent ships this rule because Notepad scrambles typed text. [Undo]"* — from a code-side table of reasons promoting `config.py`'s comments to user-facing text. **This needs no stored flag**: `_default_overrides()` is a code constant, so recognizing those process names is a known fact rather than invented state.

It matters because **the Notepad row cannot be re-learned**: auto-learn fires only on a *detectable* SendInput failure, and Notepad's failure is scrambled-yet-successful input that reports fine. Delete it and dictation there quietly produces jumbled text forever, with no toast and no path back; likewise deleting a terminal row leaves `ctrl+v` pasting nothing into a shell that doesn't bind it. Since Undo expires on the next edit, a footer **`Restore built-in rules`** re-adds any missing shipped defaults (touched rows left alone).

**Apply semantics: live and unbadged.** `Injector` holds the config's list *object* and `resolve_override` walks it fresh on every insert, so an edit is live on the next dictation with no engine to restart.

> **Hard implementation constraint.** The pane **must mutate the existing list in place** (`append` / `remove` / mutate the dataclass) and **must never rebind `config.app_overrides` to a fresh list** — rebinding leaves the injector holding the old list, every edit becomes a no-op until restart, and the symptom ("my override does nothing") is indistinguishable from a typo'd executable name. Either mutate in place, or re-point `injector.overrides` after each write. (§7.7 carries this into the store's seam.)

**Toasts.** The auto-learn toast becomes **clickable**, opening Settings ▸ App overrides with that row scrolled into view and flashed — same mechanism as §5.4's. Right after "typing failed in this app" is the only moment anyone cares about per-app injection. Text stays a statement, never a prompt.

**Two neighbouring toasts stay inert**, named here so the spec is explicit:
- **notify-only** fires for two causes — a user-configured *Don't insert* row, and an **elevated foreground window**. The elevated case is unfixable by any override (UIPI blocks the paste chord too), so linking it would be a dead end implying a fix exists.
- **insertion failed** — the transcript is on the clipboard and the user's next move is Ctrl+V, not settings archaeology.

**Empty state** (reachable only by deleting every row): *"No app rules. Cadent types into every app, and adds a rule automatically if typing fails."* — true, and it quietly teaches auto-learn to someone who never caught the toast.

> **Domain-doc follow-up**: `CONTEXT.md`'s **Learned override** entry says *"later tuning is a hand edit"*. That is accurate today and becomes **false when this pane ships** — amend it in the implementation slice that lands the pane.

## 5.6 History pane

**Retention card** — a **dropdown** of plausible choices (Keep forever / 7 / 30 / 90 days / 1 year), not a spinbox — **plus the embedded recent-transcript list** (rows with app, age, snippet, Copy). Putting the list next to the retention control keeps the setting and its subject in one place.

*(The standalone History window is untouched by this spec beyond inheriting the theme; whether it is eventually folded in is a separate call.)*

---

# 6. First-run wizard ([#64](https://github.com/mikeallisonJS/cadent/issues/64), [#61](https://github.com/mikeallisonJS/cadent/issues/61))

## 6.1 Pages, in order

Linear Back/Next with progress dots.

1. **Welcome** — value line + "Set up Cadent" / "Not now".
2. **Microphone & basics** — input device picker with a live level meter, "start with Windows" checkbox.
3. **GPU support pack** *(only when eligible)* — offered **before** model choice, because accepting it changes the recommendation. Radio yes/no, ~550 MB disclosure. **Skippable.**
4. **Speech model** — hardware-suggested list (§6.2), explicit consented download with size + Hugging Face disclosure. **This page gates progress** — Next enables only once a model is downloaded.
5. **Hotkey** — teach hold-`Ctrl+Win`-speak-release with a **try-it field using the real pill** (§4.8); "Change hotkey…" escape hatch. **Skippable.**
6. **Done** — summary of what's configured.

## 6.2 Hardware detection and model suggestion ([#61](https://github.com/mikeallisonJS/cadent/issues/61))

**Detect with psutil (RAM / physical cores — already a dependency) plus a ctypes CUDA driver probe: `nvcuda.dll` → `cuInit(0)` → `cuDeviceTotalMem_v2`.** This is a strict extension of what `gpu_pack.py` already does — same DLL, same ctypes style, no subprocess flash — and **its failure mode is the right answer**: `cuInit` failing means "don't suggest GPU models". CPU name comes from the `ProcessorNameString` registry value. **Probe once, off the UI thread, cached.** NVML and `nvidia-smi` were compared and rejected. No new dependencies.

Suggestion table — **top-down, first match wins**:

| # | Condition | Suggest |
|---|---|---|
| 1 | NVIDIA GPU usable, VRAM ≥ 6 GB | `distil-large-v3` |
| 2 | NVIDIA GPU usable, VRAM ≥ 4 GB | `distil-medium.en` |
| 3 | GPU < 4 GB VRAM | *fall through to CPU rows* |
| 4 | RAM < 8 GB | `base.en` (`tiny.en` if < 6 GB) |
| 5 | RAM ≥ 16 GB **and** ≥ 8 physical cores | `distil-medium.en` |
| 6 | everything else | `distil-small.en` (current default) |

**`distil-small.en` is also the catch-all fallback whenever detection throws.**

Why it looks like this:
- **Distil models are disproportionately good for push-to-talk** — full-size encoder, 2-layer decoder, so the decode step that `beam_size=5` multiplies is what shrinks. `distil-large-v3` is ~6× lower latency than `large-v3` at +1.3 short-form WER, so **`large-v3` is never auto-suggested** (it stays a manual "accuracy over latency" pick for ≥ 6 GB GPUs).
- **Footprint math**: the GPU tier needs `model.bin` (fp16 weights) + ~1.4 GB runtime VRAM + compositor headroom; the CPU tier needs int8 weights (≈ `model.bin`/2) + ~1.2 GB runtime + headroom for the cleanup LLM and the user's own apps.
- **Honest caveat**: `cuDeviceTotalMem` is total VRAM, not free. Acceptable because `stt.py`'s encode probe already falls back to CPU int8 at load time.

**Presentation**: run detection on entering the model step. **Pre-select the suggested model with a one-line "why"** (GPU/CPU name, RAM); the user can override from the full 8-model list. If the driver is present, VRAM ≥ 4 GB, and the pack isn't installed, **surface the GPU-pack page first**. **Keep the post-hoc `gpu_pack.should_offer` path as a safety net.**

## 6.3 Skip policy

**Only the GPU pack and Hotkey pages carry "Skip for now".** The model page has no skip — Finish is unreachable without a model, so **a completed wizard always leaves a dictation-capable app**.

## 6.4 Cancel, re-run, upgrade

- **Cancel / "Not now"**: the app stays resident in the tray with **dictation disabled**; the tray menu gains a bold **"Finish setup…"** first item (§3.2), and pressing the hotkey pops a toast pointing at it. *Toast, never a prompt.* Unfinished setup also drives the **Needs attention** mark (§3.4).
- **Re-run**: **Settings ▸ General**, where Setup is the first card ([#110](https://github.com/mikeallisonJS/cadent/issues/110)). The tray carried a duplicate of the same button directly under History…, where an accidental click opened a full-screen wizard over whatever you were doing; it is gone. Re-running setup is a rare, deliberate act, and Settings is where deliberate acts live.
- **Upgrade**: existing users **never** see the wizard — a model already exists. GPU-pack eligibility keeps being handled by the existing tray toast.

## 6.5 Wizard keys ([#75](https://github.com/mikeallisonJS/cadent/issues/75))

- **Enter activates the primary button uniformly on every page**, including the Model page's disclosed download — a focused, labelled default button is user-initiated, and exempting one page of six is hard to discover and reads as a broken wizard.
- **Escape cancels to tray-only, but goes inert while a download is running**, so a stray key cannot bin 750 MB. That page carries its own **Cancel download** control. No confirm prompt.
- Focus containment needs no work — the wizard is a normal top-level window and Qt already cycles Tab within the active window.
- **Page changes move focus to the new page's first interactive control and announce it** (§8.4).

---

# 7. The `config.json` write model ([#76](https://github.com/mikeallisonJS/cadent/issues/76))

## 7.1 The contract, and why it differs from `vocabulary.json`

**Per-key delta read-modify-write, atomic, no watcher, restart-to-apply.**

This is deliberately **not** §5.4's contract. `vocabulary.json` is re-read at the start of every dictation, which is *why* writing it **is** applying. `config.json` is read **once at startup** into a live `Config` object that subsystems hold references *into* — the injector shares the very same `app_overrides` list. For config, **memory is the running truth and the file is its projection.** Copying §5.4 would mean a `QFileSystemWatcher` plus mid-session engine restarts plus reconciling an open settings window — all rejected.

The contract is stated **in the file itself**:

> Edit `config.json` freely. Cadent preserves your edits and never rewrites keys it didn't change. Unlike `vocabulary.json`, changes here apply **the next time Cadent starts**.

## 7.2 Delta writes, atomically; an unreadable file is never overwritten

**Every write names its key(s), reads the file, merges, writes back.** Unknown keys, `_comment`s, and hand-edits to keys this write didn't touch all survive. Instant apply has already reduced every write to a single-field event, so the delta is free: `self.config.save()` becomes `store.set("paused", True)`.

This closes a live gap: `Config.load()` keeps only known field names, so today a `_comment` in `config.json` survives *load* and is erased by the next *save* — unlike its siblings, where comments are a documented round-tripping feature.

**Writes are atomic — temp file + `os.replace`.** `save()` is a bare `write_text` today; a crash mid-write truncates the file, and instant apply multiplies the exposure.

**An unreadable file is never overwritten.** `load()` deliberately leaves a malformed file "untouched for hand-repair" — **and then the next `save()` destroys it anyway.** Today that needs a pause toggle or a settings OK; under instant apply a single stray click does it. Fixed by **refusing to write**:

- Changes apply to the **session only**, never persisted.
- **The tray's Needs-attention mark** (§3.4) carries it with no settings window open — it is a *fault*, so it clears on fix and can't become permanent scenery. This also fixes a second gap: startup on a broken config is **silent** today, so the app runs on nothing the user chose and never says so.
- Settings shows a **persistent inline banner**: *"config.json couldn't be read, so Cadent started with default settings. Changes you make now won't survive a restart."* with **[Open the file]** and **[Back it up and start fresh]**. The rename-aside is the **only** thing allowed to displace the file, and only because the user clicked it. Not a prompt — an inline error state, mirroring §5.4.

*(This is whole-file unreadability. Valid JSON with a bad value goes through `_sanitized()` per-field — §7.5.)*

## 7.3 Pacing: immediate, except repeating controls

Discrete acts — combo, checkbox, toggle, tray pause, the overlay's **Done** — **write immediately**. Only **auto-repeating controls coalesce** (~400 ms trailing, flushed on focus-out, window close and quit).

**The coalesce exists to gate the engine restart, not to spare the disk.** `min_hold_ms` is a spinbox *and* a `hotkeys` engine field, so under instant apply holding the arrow would tear down and rebuild the hotkey listener on every auto-repeat tick. `retention_spin` already emits `valueChanged` live, so this is a real path, not a hypothetical. A blanket debounce was rejected: it makes a discrete act like `paused` losable if the app is killed inside the window.

> **There is no continuous writer anywhere in the app.** The overlay anchor is not one — the drag lives inside "Move overlay…" and commits on **Done**, one write per gesture (§4.6). The repeating one is the spinbox.

## 7.4 Divergence is shown, not reconciled

The one sharp edge of restart-to-apply is that file and memory can disagree silently, and the surprise lands on a restart weeks later.

Detection is free — a delta write already reads the file. **It must compare against the raw dict captured at startup, not against current memory**: `_sanitized()` type-corrects in memory and, under delta writes, never writes the correction back, so a memory-vs-file comparison would false-positive forever on any typo'd field.

Surfaced as a **quiet informational line in the affected pane** — *"config.json was edited outside Cadent. `stt_model` there is `large-v3` and will apply next time you start."* **No toast; no Needs-attention mark** (that state is reserved for degraded, and a pending edit is not a fault); **no Reload button**, which would drag back everything the watcher was rejected for.

## 7.5 A bad value uses the same surface

Under wholesale save, the next save quietly repaired a sanitized field. **Under delta writes it never does** — the bad value would sit in the file and be silently overridden **every start**, invisible to §7.4 because the file hasn't changed.

`_sanitized()` already knows which fields it reset, so **the same inline component reports it**: *"config.json sets `hotkey_mode` to `"hodl"`, which isn't valid. Using `"hold"`."* The file is left alone; the notice clears when the user fixes the file, or sets the field in the pane (which writes it).

## 7.6 The file documents itself

New `config.json` files are seeded with `_comment` / `_editing`, mirroring `vocabulary.py` and `snippets.py` — whose `_editing` states that file's apply semantics verbatim. **Config's says the opposite, which is exactly why it belongs in the file.** **Existing files are never backfilled**: re-adding a comment the user deleted needs a marker key, which is worse than the gap.

## 7.7 The seam

**A `ConfigStore` owns the file; `Config` stays a plain dataclass and remains the running truth.** The new duties — startup raw dict, invalid-file state, atomic delta write, coalesce timer, sanitize report — are too much for a dataclass method, and `settings.py` is already the precedent for a pure-logic sibling.

**`app_overrides` stays a shared reference**: row edits and `learn_override` mutate the list **in place** (§5.5's constraint — otherwise the injector goes stale), then ask the store to write that one key. **Key-granularity means a hand-edit anywhere in `app_overrides` is lost when the app writes that key**; per-row diffing is machinery out of all proportion.

---

# 8. Accessibility ([#75](https://github.com/mikeallisonJS/cadent/issues/75))

**The bar: keyboard-complete, correctly named, High-Contrast-safe.** Every function reachable and operable by keyboard with visible focus; every control carrying a correct accessible name and role; a contrast theme never leaving the user staring at violet. **Screen-reader support is correct-by-construction from stock Qt widgets, verified by spot-check — not a conformance claim.**

That construction argument is measured, not hoped for: every restyled control still reports its true role — `QCheckBox` → `CheckBox`, `QComboBox` → `ComboBox`, `QLineEdit` → `EditableText`, `QRadioButton` → `RadioButton`. The generated pixmaps repaint the indicator; they do not touch the semantics underneath.

**This is greenfield, not a repair.** There is no accessibility code at all today — no `setAccessibleName`, `setTabOrder`, `setBuddy`, `setFocusPolicy`, no mnemonics anywhere in `cadent/*.py`.

## 8.1 High Contrast

Covered in §1.3. Summary: drop the sheet; detect via `contrastPreference()`, never `colorScheme()`; HC outranks the Light/Dark override; the meter reads only `window`/`window-text`/`button`/`button-text`; **the wizard's embedded pill keeps its real violet**; the Appearance control stays enabled with its reason as `accessibleDescription`.

## 8.2 Focus: one rule, focus-visible

**Measured first**, because §1.5 was already burned once by `letter-spacing` being silently ignored: **`outline` is real in Qt 6 QSS** — including `outline-offset` and `outline-radius` — and it renders at widget level **over** an `image:` pixmap indicator (160px changed) and on a borderless `#NavItem` (648px changed).

> **So the §1.5 generated-pixmap set does not have to double with focused variants.** One `outline` rule per focusable class covers every control.

**Focus-visible semantics** via a small app-level event filter reading `QFocusEvent.reason()`, setting a `kbdFocus` dynamic property and repolishing (§1.1): **ring on Tab, no ring on click.** QSS has no `:focus-visible`, so a plain `:focus` rule would ring on mouse clicks — **which is not what Fusion does**. Measured: a focused `QPushButton` changes 284px and a `QCheckBox` 832px under keyboard focus, and **0px under mouse focus**. Matching that keeps focus behaviour identical whether or not the sheet is present — which matters precisely because §1.3 removes it.

**Two new tokens per column**, plus theme-independent geometry:
- **`focus_ring`** — dark `#9678ff`; light `#6f47e0`, reusing §1.4's own fix for violet-on-white being ~3:1.
- **`focus_ring_on_accent`** — a near-white value applied by a scoped rule on accent-filled controls. **Without it the ring is invisible on the violet gradient CTA** — the button the wizard's whole flow depends on.
- Geometry (BASE): 2px width, 2px offset, `outline-radius` tracking each control's own radius.

§1.4's printed contrast audit **extends to cover the ring against every surface it can land on.**

## 8.3 Keyboard

**Names come from `label.setBuddy(control)`** in the one card-row factory. Verified: it works **without a mnemonic**, so it costs nothing visually, and it strips the `&` when one is present. The decisive property is that **it cannot drift from the visible label, because it *is* the label** — no second copy of the string. Subtitles go on via `setAccessibleDescription`.

This is not theoretical: today a bare `QComboBox`, a bare `QLineEdit`, **and the toggle** all report `name=''` — the toggle because it is a text-less `QCheckBox` with its label in a separate `QLabel`.

**Enforcement is a test** that walks every window and asserts each focusable control reports a non-empty accessible name — the thing that catches the control built outside the factory, which is exactly how the text-less toggle got in.

**The sidebar becomes a `QListWidget`** with `Qt.ItemFlag.NoItemFlags` group headings: **one tab stop instead of six**, arrow keys with selection following focus (the Windows Settings model), and each pane announced as a list item with its position-in-set. Verified: a `NoItemFlags` heading reports `selectable=0` while staying visible and enabled, so it reads as a heading and cannot be landed on. *(The alternative — checkable `QPushButton#NavItem`s — costs six tab stops, no arrow movement, and a screen reader announcing "button" with no position.)*

**Tables** (§5.4, §5.5) keep stock `QTableView` semantics — arrows / Home / End, F2 or Enter to edit, Tab to commit and move on (already §5.4's commit-writes-the-file model) — plus **Delete to remove a row and a real Ctrl+Z**. **The Undo strip becomes the *visible face* of that shortcut** rather than the only way back: a strip that disappears on a timer is unusable if reaching it costs several Tab presses. It fires an Alert when it appears and **never takes focus**.

**Enter = primary everywhere** (§6.5).

Keyboard-only users reach the tray through Windows' own `Win+B` notification-area path, and the tray reaches everything else — so **no shortcut of our own is required**. What that argument does *not* license is leaving anything reachable from the tray alone: since [#110](https://github.com/mikeallisonJS/cadent/issues/110) the wizard is reached by `Win+B` ▸ **Settings…** ▸ General, where it is the first card, and the extra hop is the same one a mouse user takes. The cancelled-setup case still resolves in the tray itself, where §3.2's bold "Finish setup…" sits in a plain `QMenu` with native keyboard navigation.

## 8.4 Announcements

All on one primitive — `QAccessibleEvent(widget, QAccessible.Event.Alert)` + `updateAccessibility`, verified to dispatch. **Free when no AT is attached**, since `QAccessible.isActive()` is false and Qt no-ops it.

- **Instant apply mirrors §5.2's own split** rather than inventing a second policy. **Light fields stay silent in both channels.** Heavy fields carry the `restarts <engine>` text as the control's `accessibleDescription` — announced **before** the change, which is what a change-of-context warning has to do — and fire a one-line Alert **on commit**: *"Speech model changed — restarting Whisper."*
- **Wizard page changes** move focus to the new page's first interactive control and announce it (*"Step 3 of 6 — GPU support pack"*). Without this, focus is stranded on a button that no longer exists and a screen reader never learns the content changed.
- **The try-it step mirrors pill state into an announced status line on the page** — "Recording", "Transcribing", "Inserted 7 words". §4 stays intact: the desktop pill remains a **non-announced tool window**, and normal operation is covered by toasts, which Windows announces itself. Only the one step whose entire purpose is *confirmation* gets a non-visual channel.

## 8.5 Text scaling

Windows' accessibility **Text size** setting (100–225%) is honoured by **no Qt mechanism at all** — verified: `styleHints()` exposes no text-scale hint, and `HKCU\SOFTWARE\Microsoft\Accessibility\TextScaleFactor` is the only route (**absent means 100%**).

**Read it once at startup, multiply §1.4's type-scale tokens by it, and replace fixed sizes with minimum widths and layout-driven sizing — starting with the sidebar's `setFixedWidth(220)`, which clips outright at 150%.** Applied on next launch; **no registry watcher**.

The reason to take it now is timing: every one of these surfaces is being rebuilt in this effort, so "don't hard-code a width" is free today and a six-pane retrofit later.

---

# 9. Module map

The seams the decisions actually require, marked accordingly. Organizational placement is a suggestion; the load-bearing items are not.

| Module | Status | Notes |
|---|---|---|
| `cadent/theme/tokens.py` | new | **Load-bearing** (§1.1, §1.4): BASE + DARK + LIGHT, identical key names. Single source for QSS *and* the custom painters. |
| `cadent/theme/qss.py` | new | `string.Template` QSS + render. Never partially style `QComboBox`/`QScrollBar`. |
| `cadent/theme/pixmaps.py` | new | **Load-bearing** (§1.5): chevron / toggle / radio at live DPR; cache key includes theme **and** contrast state. |
| `cadent/theme/manager.py` | new | Owns `colorSchemeChanged` + `contrastPreferenceChanged`, the `Light → light, else → dark` branch (§1.3), sheet-drop under HC, repolish, tracked-caps label helper. |
| `cadent/config_store.py` | new | **Load-bearing** (§7.7): `ConfigStore` owns the file; `Config` stays a plain dataclass. |
| `cadent/hardware.py` | new | psutil + ctypes CUDA probe + the §6.2 table. Probe once, off the UI thread, cached. |
| `cadent/wizard.py` | new | Six pages, §6. |
| `cadent/tray.py` | new (extracted) | §3 icon states, status header, grouped menu, left-click pause, fault ledger. |
| `cadent/a11y.py` | new | Focus-visible event filter, Alert helper, `TextScaleFactor` read. |
| `cadent/settings_ui.py` | rebuilt | Becomes a package: sidebar `QListWidget` + six panes. **Drop the draft/OK model.** |
| `cadent/settings.py` | changed | **`restarts_needed()` and `ENGINE_FIELDS`-as-diff go away**; invert to a field→engine lookup (§5.2). |
| `cadent/overlay.py` | rebuilt | §4: `QFrame#overlayPill` + custom-painted indicators, seven states, realize at startup, focused-window screen. |
| `cadent/config.py` | changed | `theme`, overlay anchor + snap, "show overlay" toggle; `save()`/`load()` duties move to the store. |
| `cadent/pipeline.py` | changed | **One new signal at the STT→cleanup boundary** (§4.3); hand the `dropped` hotword list to settings (§5.4). |
| `cadent/app.py` | changed | Icon wiring, clickable toasts (`messageClicked` — none exists today), the two new toasts (§4.2), wizard launch, fault ledger. |
| `packaging/icons/**`, `scripts/build_icons.py` | new (lift from `prototype/mark`) | §2. |
| `packaging/cadent.spec`, `cadent.iss` | changed | Point at `cadent.ico`. |
| `pyproject.toml` | changed | `PySide6>=6.8` (§1.2). |
| `CONTEXT.md` | changed | Amend **Learned override** when §5.5 lands. |

---

# 10. Testing Decisions

Good tests exercise external behavior at the seams — never implementation details. Extending the prior art in `tests/test_config.py` / `tests/test_history.py` (pytest, temp dirs, no mocking frameworks).

**Automated:**
- **`ConfigStore`** (§7) is the richest pure seam and gets the most coverage: delta write preserves unknown keys / `_comment`s / untouched hand-edits; atomic replace; **an unreadable file is never written to**; divergence detected against the **startup raw dict**, not memory; sanitize report; coalesce flushes on focus-out / close / quit; `app_overrides` written by key with the list mutated in place.
- **Token set**: every key present in **both** colour columns (the identical-key-names rule is what makes §1.2's swap safe).
- **Contrast audit as a test** (§1.4): all 17 pairs × 2 themes, the tray triad against both taskbar polarities, and the focus ring against every surface it lands on (§8.2). This is the mechanism that caught the invisible dark card border; it must not stay a prototype script.
- **Accessible-name coverage** (§8.3): walk every window, assert each focusable control reports a non-empty name.
- **Hardware suggestion table** (§6.2): pure function over `(vram, ram, cores)` including the throws-→-`distil-small.en` fallback.
- **Wizard page gating** (§6.3): Finish unreachable without a model; skip permitted only on GPU and Hotkey.
- **Pill state machine** (§4.3) as pure logic — state → glyph/label/timeout — with the min-visible floor, driven without a screen.
- **Icon build** (§2): `build_icons.py` produces all six sizes; Qt reads each back at exact dimensions; the `.ico` directory encodes 256 as `0`.

**Manual — the Win32 and visual edges, which are not unit-tested:**
- One keyboard-only walkthrough per surface, and one **Narrator** pass (it ships with Windows, so the check is always runnable).
- One pass under a **dark contrast theme — Night sky specifically**, because that is the theme that exposes the `Unknown`-serves-light bug if it ever regresses.
- Pill over **real backgrounds** — a white document, a dark code editor, video-bright content — in both themes. "Near-opaque + shadow" is a claim about separation from arbitrary content and is only checkable that way.
- Tray triad at 16/24/32 on a near-black **and** near-white taskbar.
- Multi-monitor: dictate into a window on a secondary display and confirm the pill lands there.
- DPI change with the settings window open (pixmap regeneration, §1.5).

> **The four-built-in-contrast-theme sweep is not needed.** Route (a) drops the sheet, and the surviving painters are restricted to roles already verified as unconditional `GetSysColor` reads.

---

# 11. Out of Scope

- **A shell rethink** — a unified main window rather than a tray-first one. Ruled out at charting: this effort matures the look of the tray-first shell, not the app's structure.
- **Pinning the overlay pill to one specific monitor.** Following the focused window's screen is right for the single-user case; a per-monitor pin is a distinct feature.
- **A High Contrast token column computed from the live `QPalette`.** Route (a) is already correct and complete, so this buys visual identity under a contrast theme, not accessibility — and it isn't specifiable yet, since which palette roles are contrast-safe is unresolved (`Highlight` may be the WinRT personalisation accent; `Accent` is a derived `accent.darker(120)`), which needs the four-theme sweep first.
- **Live reload of `config.json` while running.** A `QFileSystemWatcher` would force mid-session engine restarts and reconciliation of an open settings window for a power-user convenience; restart-to-apply plus never stomping the file delivers the safety without the machinery.
- **Live transcription preview in the pill** (§4.1) — settled against, not deferred.
- **Import/export and a snippet "test it" affordance** (§5.4); **a "this executable was never seen" marker** on override rows (§5.5).
- Streaming insertion, Parakeet, voice commands, per-app tone presets, macOS/Linux — still v2 per the PRD.

---

# 12. Further Notes

**Suggested implementation slicing.** The dependency order is roughly the spec's own: §1 (tokens + theme manager + pixmaps) and §2 (the mark, lifted from `prototype/mark`) are the foundation everything else renders on and can land first and independently. §7 (`ConfigStore`) is next and is a prerequisite for §5's instant apply. §3 (tray) and §4 (pill) are then independent of each other. §5's panes slice per pane. §6 (wizard) needs §1, §2, §4 and `hardware.py`. §8 is **not** a trailing slice — the buddy factory, the sidebar `QListWidget`, the focus filter and the name-coverage test are cheapest built into each surface as it lands, and the whole reason §8.5 is in scope now is that these surfaces are being rebuilt anyway.

**Prototypes to lift from, not rebuild.** `prototype/design-direction` (the winning language), `prototype/settings-structure` (pane organization), `prototype/wizard-flow` (the paged flow), `prototype/design-tokens` (the token dict, the QSS recipes, and the contrast audit), `prototype/mark` (SVGs, rasters, `.ico`, `build_icons.py`). Research write-ups live on `research/ui-foundation`, `research/dark-mode-chrome`, `research/model-auto-suggestion`, `research/high-contrast-qss`.

**No ADR and no new `CONTEXT.md` terms** are created by this spec. The state ladders and apply semantics are spec detail, not domain vocabulary — the glossary's terms are behavioural concepts. The one glossary change is amending **Learned override** (§5.5).

**PRD alignment.** This spec is M4, after the PRD's M3 (settings UI, history search, per-app overrides, installer, autostart). It honours PRD §5.7's click-through pill (§4.6), §5.6's retention setting (§5.6), §6's latency budget (§4.1 declines to spend it, §4.6 keeps the paint rate at 30 fps), and §6's disclosure rule (§6.1 page 4, §6.2's GPU pack). `CONTEXT.md`'s "toast, never a prompt" holds throughout: §5.4, §5.5 and §7.2 all use inline error states with actions rather than modals, and the wizard is the one consented, user-initiated exception.

---

## Decision index

| § | Ticket |
|---|---|
| 1.1 | [#59 — How should the new UI be built: QWidget+QSS or QML?](https://github.com/mikeallisonJS/cadent/issues/59) |
| 1.2 | [#60 — Following Windows dark/light mode and modern window chrome in PySide6](https://github.com/mikeallisonJS/cadent/issues/60) · [#65 — Dark/light mode policy](https://github.com/mikeallisonJS/cadent/issues/65) |
| 1.3, 8.1 | [#74 — What Windows High Contrast does to a QSS-styled Qt 6 window](https://github.com/mikeallisonJS/cadent/issues/74) · [#75 — Accessibility posture for the windowed surfaces](https://github.com/mikeallisonJS/cadent/issues/75) |
| 1.4, 1.5 | [#71 — Design tokens for the soft-dark direction](https://github.com/mikeallisonJS/cadent/issues/71) |
| 2 | [#73 — The Cadent mark: mic glyph at 16px and up](https://github.com/mikeallisonJS/cadent/issues/73) |
| 3 | [#69 — Tray icon and menu redesign](https://github.com/mikeallisonJS/cadent/issues/69) |
| 4 | [#68 — Overlay pill: behavior and content](https://github.com/mikeallisonJS/cadent/issues/68) |
| 5.1, 5.2 | [#62 — Visual direction: contrasting design mockups](https://github.com/mikeallisonJS/cadent/issues/62) · [#63 — Settings surface structure: sections and panes](https://github.com/mikeallisonJS/cadent/issues/63) |
| 5.4 | [#66 — Vocabulary and snippets editor UX](https://github.com/mikeallisonJS/cadent/issues/66) |
| 5.5 | [#67 — App overrides pane UX](https://github.com/mikeallisonJS/cadent/issues/67) |
| 6 | [#64 — First-run wizard flow, skip and re-run policy](https://github.com/mikeallisonJS/cadent/issues/64) · [#61 — Hardware detection for STT model auto-suggestion](https://github.com/mikeallisonJS/cadent/issues/61) |
| 7 | [#76 — The config.json write model under instant apply](https://github.com/mikeallisonJS/cadent/issues/76) |
| 8 | [#75 — Accessibility posture for the windowed surfaces](https://github.com/mikeallisonJS/cadent/issues/75) |
