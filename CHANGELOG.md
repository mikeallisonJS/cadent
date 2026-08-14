# Changelog

Curated per-release notes. Entries accumulate as fragments in `changelog.d/`
and are rendered here by the release PR — see `docs/agents/releases.md`.

<!-- towncrier release notes start -->

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
