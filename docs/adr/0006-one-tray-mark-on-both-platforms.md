# One tray mark on both platforms: shape carries the state, the surface picks the ink

Cadent shipped **two different marks**. macOS drew a mic with three flow lines
beside it; Windows drew a mic in a cradle with no flow lines at all. That was
deliberate — a macOS menu-bar icon is a *template image*, so the OS throws the
colour away and repaints the alpha, which left silhouette as the only channel
the states could differ on. Windows had colour available, so it kept one
silhouette and recoloured it: violet Ready, grey Paused, orange Needs
attention. Two platforms, two compositions, two SVG families to keep in step.

**This supersedes that split.** There is now one mark. The cradle sits on the
box centre at 0.84 of the master scale, which frees a 4.8-unit zone at
x 18.6–23.4 for the state element: flow lines for Ready, two bars for Paused,
an exclamation for Needs attention. The three states differ **by shape on both
platforms**, and colour says nothing about state anywhere.

Cross-platform recognisability beat menu-bar-native composition. The earlier
decision optimised each platform's tray in isolation and produced an app whose
identity changed when you switched machines — and the Windows mark was the one
users met most, on the exe, the installer, Add/Remove Programs, the taskbar
and Alt-Tab, none of which had ever seen the flow lines. Anyone reading the
old rationale and regenerating the cradle-only set would be reintroducing the
bug this records fixing.

## What follows from it

**One raster set, alpha only.** The shipped PNGs carry a silhouette and no
colour: 18 files, three states by six sizes, down from 36. Where the OS
repaints a mask to suit the surface it sits on — the macOS menu bar — the mark
ships as a mask and the OS picks the ink, including for vibrancy and Reduce
Transparency, which no value we chose could track. Where it does not, we paint
the silhouette ourselves through the alpha. The states cannot drift apart
between platforms because there is nothing left to drift.

**The fact and the live answer are separate.** `Capabilities` carries
`tray_icon_painted_by_os` — a static fact about who paints. `DesktopEnv`
carries `tray_ink()` — a live question, because the answer changes while the
app runs. This is ADR 0005's division applied unchanged: the fact gates, the
adapter answers.

**Windows reads the taskbar's colour mode, not the app's.** The spec used to
say tracking it was not worth it for a 16px glyph. It is worth it once the
glyph has no colour of its own: `SystemUsesLightTheme` under
`HKCU\…\Themes\Personalize` is the taskbar's setting, and `AppsUseLightTheme`
beside it is the one Qt reports through `colorScheme()`. Dark taskbar with
light apps is a common configuration, and reading the wrong value there paints
black on black. Cadent's own Light/Dark preference must never reach the tray
either — a theme setting that can make your own tray icon invisible is not a
setting anyone intends. Under a Windows contrast theme neither black nor white
is safe, so the ink comes from `COLOR_WINDOWTEXT`, which is what that theme
writes text in.

**The watch is `RegNotifyChangeKeyValue` on a thread, not a Qt event filter.**
A `WM_SETTINGCHANGE` filter would need a window and a Qt event loop, and this
package stays Qt-free (ADR 0005). The callback lands on the watcher's own
thread exactly as `HotkeyTap.start`'s does, and the caller marshals.

## What it costs

**Needs attention loses its orange.** That was the one state where an
at-a-glance colour cue earned its keep, and shape now carries it alone, with
the tooltip carrying the words. Accepted knowingly: shape-coded states are
what macOS already had, they survive a colour-vision deficiency, and the
alternative was keeping two marks.

**Small sizes lean harder on the silhouette.** At a true 16px the state
element has three pixel columns to work in. Two things keep that honest —
macOS renders the menu bar at 2×, so the mask is really a 36–44px raster, and
the hand-corrected 16-unit grid is allowed to *simplify* the composition
rather than merely pixel-tune it. The 16 grid does not reproduce the 0.84
cradle for the same reason: below a pixel of difference, shrinking costs more
legibility than the clearance buys.
