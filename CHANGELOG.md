# Changelog

Curated per-release notes. Entries accumulate as fragments in `changelog.d/`
and are rendered here by the release PR — see `docs/agents/releases.md`.

<!-- towncrier release notes start -->

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
