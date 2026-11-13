# Changelog

Curated per-release notes. Entries accumulate as fragments in `changelog.d/`
and are rendered here by the release PR — see `docs/agents/releases.md`.

<!-- towncrier release notes start -->

## v0.8.0 — 2026-11-13

### Features

- Cadent now starts on Linux. It detects whether the session is X11, Wayland on Plasma and similar desktops, or Wayland on GNOME, and Settings ▸ General says which level of support that session gets. ([#33](https://github.com/mikeallisonJS/cadent/issues/33))
- Cadent now dictates on X11: hold Ctrl+Super, speak, release, and the text types into the focused app, falling back to pasting where typing fails and remembering which apps need it. Per-app overrides and History identify apps by their desktop-file id. ([#34](https://github.com/mikeallisonJS/cadent/issues/34))
- Cadent now dictates on KDE Plasma Wayland (and SteamOS desktop mode): the first-run step asks your desktop for the shortcut and typing permissions, and the hotkey you set in your desktop's shortcut settings types the transcript. On a desktop with no global-shortcut support, Cadent says so instead of asking for a permission it cannot use. ([#35](https://github.com/mikeallisonJS/cadent/issues/35))
- On Wayland, Cadent now pastes into the focused app on KDE, Sway, Hyprland and GNOME, putting your clipboard back afterwards, and types with full unicode and no permission prompt on Sway and Hyprland. Where a session has no way to paste, the App overrides pane says so and Cadent types instead. ([#36](https://github.com/mikeallisonJS/cadent/issues/36))
- The app overrides pane on Linux now lists your installed apps (including Flatpak ones) by name, shows each app's desktop name in override and history rows even when it's closed, and notes on Reduced-support sessions that overrides don't apply there. ([#37](https://github.com/mikeallisonJS/cadent/issues/37))

### Fixes

- A restart hint in Settings no longer wraps beside the microphone or model list when the window is narrow or the system font is wide: the list shortens its own text first, and the full name stays in its tooltip. ([#linux-restart-hint-wrap](https://github.com/mikeallisonJS/cadent/issues/linux-restart-hint-wrap))
- The Mac app now says it needs macOS 15 or later. It used to claim macOS 12, but parts of it were already built for macOS 15, so the claim was not one it could keep. ([#macos-minimum-version](https://github.com/mikeallisonJS/cadent/issues/macos-minimum-version))
- Picking a different speech or cleanup model while the previous one is still loading now switches to the new model, instead of sometimes keeping the old one in memory until Cadent restarts. The tray also no longer keeps a "model failed" warning once a working speech model has loaded. ([#model-swap-race](https://github.com/mikeallisonJS/cadent/issues/model-swap-race))
- The Accessibility permission is now watched by the app itself, so on a Mac the tray recovers the moment it's granted even with no window open. ([#31](https://github.com/mikeallisonJS/cadent/issues/31))
- macOS: the push-to-talk hotkey now starts working as soon as the Accessibility permission is granted, instead of staying silent until Cadent was quit and relaunched. ([#60](https://github.com/mikeallisonJS/cadent/issues/60))


## v0.7.0 — 2026-09-26

### Features

- Hotkeys are recorded, not typed: in Settings ▸ Hotkeys, click the chord, press the keys you want, and let go. Nobody has to know that Ctrl+Win is spelled `<ctrl>+<cmd>` any more. A switch records the side you press (Right Ctrl rather than either Ctrl), and keys that cannot be a hotkey, such as a letter on its own, are explained inline rather than accepted. ([#63](https://github.com/mikeallisonJS/cadent/issues/63))
- A hotkey can name one side of a modifier: `<rctrl>` or `<rcmd>` alone makes a push-to-talk key that collides with nothing, and `<lshift>`, `<ralt>` and the rest work the same way on Windows and macOS (`<ctrl_r>`-style spellings are accepted too). Plain `<ctrl>` still means either side. ([#64](https://github.com/mikeallisonJS/cadent/issues/64))

### Fixes

- Cadent starts even when the hotkey in config.json is one it cannot read. Previously a hand-edited chord the app did not recognise (a `<ctrl_r>`, say) crashed it on launch with an exception dialog; now it falls back to the default chord and Settings ▸ Hotkeys says what the file asked for and what is in use instead. ([#68](https://github.com/mikeallisonJS/cadent/issues/68))


## v0.6.0 — 2026-09-11

### Features

- The Windows installer can now be Authenticode-signed, which removes the
  SmartScreen warning on first launch and is what the Microsoft Store requires
  before it will accept a build. Signing is off unless credentials are
  configured, so unsigned builds work exactly as before. ([#51](https://github.com/mikeallisonJS/cadent/issues/51))
- A new "Tap or hold" hotkey mode: tap the dictation chord to start and tap
  again to stop, or hold it and release to dictate exactly as in hold mode.
  It is now the default; "Hold to dictate" and "Tap to start / tap to stop" remain in Settings ▸ Hotkeys. Configs that already store a valid mode keep it. ([#52](https://github.com/mikeallisonJS/cadent/issues/52))


## v0.5.0 — 2026-08-15

### Features

- Cadent's tray icon is now the same mark on Windows and macOS — previously each
  platform drew a different one. The three states are told apart by shape rather
  than colour everywhere: flow lines when dictation is ready, two bars when it is
  paused, an exclamation when something needs you. On Windows the icon follows
  your taskbar's light or dark setting, and switches with it while the app is
  running, so it stays visible whichever you use. ([#7](https://github.com/mikeallisonJS/cadent/issues/7))
- Cadent has a proper app icon: the mark knocked out of a rounded purple tile,
  on the installer, the Start menu, the taskbar, the Alt-Tab entry, the macOS app
  bundle and the disk image the Mac version arrives in. Previously those all
  carried the bare tray glyph, which looked unfinished at the sizes Finder and
  the Start menu use. The tray icon itself is unchanged. ([#10](https://github.com/mikeallisonJS/cadent/issues/10))


## v0.4.0 — 2026-08-14

### Features

- Setup no longer strands you without a connection: the speech model step can
  be skipped, and Cadent waits in the tray with dictation off until you download
  one. Settings ▸ Speech & cleanup now shows the download too — how far it has
  got, a button to stop it, and a way to start it again after a skip, a
  cancellation, or a failure. Stopping a download now takes effect however early
  you ask, and picking a different model while one is running switches to it
  instead of waiting the old one out. ([#173](https://github.com/mikeallisonJS/cadent/issues/173))

### Fixes

- Parakeet works again in the installed Windows app. Choosing it used to fail
  immediately — the installer left out a file the speech engine reads about
  itself on startup — so dictation fell back to Whisper. ([#172](https://github.com/mikeallisonJS/cadent/issues/172))


## v0.3.0 — 2026-08-14

### Features

- Cadent now ships a macOS installer: a drag-to-Applications disk image for Apple silicon, built and attached to every release alongside the Windows installer. The app has no Apple developer certificate yet, so the first launch is a right-click ▸ Open, and macOS asks for Accessibility again after an update. ([#171](https://github.com/mikeallisonJS/cadent/issues/171))

### Fixes

- Cadent no longer describes itself as a Windows-only app now that macOS is
  supported: the README and package metadata name both platforms, and the
  Appearance row in Settings names the setting your own OS has — a Mac is told
  Cadent follows its Appearance setting, not a "Windows app colour mode". ([#1](https://github.com/mikeallisonJS/cadent/issues/1))
- Opening the setup wizard could hang the app: it probed your hardware on a
  background thread while it was still building the page, and the two could
  deadlock. It now builds the page first. Settings also stops watching your
  vocabulary and snippets files once the window showing them is gone. ([#119](https://github.com/mikeallisonJS/cadent/issues/119))
