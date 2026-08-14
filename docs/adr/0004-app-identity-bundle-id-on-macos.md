# App identity: the `process` key holds the bundle identifier on macOS

`AppOverride.process` is a persisted config.json key that names an app. What it
holds is a platform fact, not a schema change — the #130 alias precedent applied
to identity: executable name on Windows (`notepad.exe`), **bundle identifier**
on macOS (`com.apple.Terminal`), falling back to the executable basename from
`executableURL` for the rare bundle-less process, then the shared `"unknown"`
sentinel. Bundle id wins because it is the stable machine identity — display
names localize and rename, executable names collide — and readability is the
pane's job, not the store's. History's `app_name` stores the same identity, so
overrides, history rows, and pane suggestions stay join-compatible; matching
stays case-insensitive everywhere.

Detection is `NSWorkspace.frontmostApplication()` — no TCC grant, and its
app-level granularity is exactly override granularity (overrides are per-app,
never per-window), so the AX API and `CGWindowListCopyWindowInfo` (Screen
Recording TCC) were rejected. The Settings pane bridges identity to humans: the
add affordance becomes a picker of running regular-activation-policy apps
rendered "Display Name — bundle.id" storing the id, free text still accepted;
table rows render the display name when a live lookup resolves it and the raw
identity otherwise. Darwin's shipped override list is empty, so Restore
defaults and the default-override reasons stay platform-neutral and naturally
inert. The known-suspect list (Remote Desktop, Citrix, Parallels, VMware
Fusion, UTM) is a later verification task, not seed rows — untested seeds are
superstition.
