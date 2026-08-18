# App identity: the `process` key holds the desktop-file id on Linux

`AppOverride.process` names an app; what it holds is a platform fact (ADR
0004). On Linux it holds **one identity on every support tier: the
freedesktop desktop-file id** — the xdg-shell `app_id` on Wayland
(`org.mozilla.firefox`, `org.kde.konsole`), and on X11 the same id resolved
from `WM_CLASS` (a `.desktop` file whose filename stem or `StartupWMClass=`
matches the class, case-insensitively, across `$XDG_DATA_HOME/applications`
— default `~/.local/share/applications` — then each `$XDG_DATA_DIRS`
entry's `applications/`, then the Flatpak exports; first match by desktop-file
id wins, so a user-installed entry shadows the system one, and the picker
deduplicates by id the same way). When no `.desktop` file resolves, the
executable basename (via `_NET_WM_PID` on X11, the toplevel's pid on Wayland
where the protocol carries one);
then the shared `"unknown"` sentinel. History's `app_name` stores the same
identity, matching stays case-insensitive, and the Linux shipped override list
is empty (ADR 0007). Decided in #21, on the Linux porting map (#11).

Why one key rather than "exe name on X11, app_id on Wayland": the same Plasma
or SteamOS machine flips between session types — Wayland by default, X11 a
developer toggle away — and an override written under one must match under
the other. Exe names also collide where desktop ids don't (`electron`,
`python3`), and Flatpak `app_id`s already *are* desktop ids. The X11
resolution chain is what every taskbar does to map windows to launchers, so it
is well-trodden, not invention.

Readability is the pane's job, not the store's, so:

- **`app_picker` is True on every tier and lists installed applications**, not
  running ones — `running_apps()` returns `(Name, id)` for every
  `Type=Application` `.desktop` file that is neither `NoDisplay` nor `Hidden`.
  A pure XDG lookup, identical on all three tiers, no compositor protocol
  needed; and nobody knows their terminal's desktop id any more than its
  bundle id. Free text stays accepted.
- **`display_name(identity)` resolves the `.desktop` `Name=`** (localized),
  whether or not the app is running — strictly better than darwin's live-only
  lookup: a history row for a closed app still reads "Firefox". Raw identity
  otherwise.
- **`app_identity_placeholder` is `org.mozilla.firefox`.**
- **`FocusedApp.name()` follows the tier**: Whole reads it from X11; Portal
  from the compositor's own toplevel protocol — `plasma-window-management`
  on KWin/SteamOS, `wlr-foreign-toplevel-management-unstable-v1` (`app_id`
  + `activated`) on the wlroots family — probed at startup; Reduced returns
  `"unknown"` — GNOME Wayland exposes no focused-window identity to third
  parties. A Portal compositor offering neither protocol returns `"unknown"`
  too, and the run carries `per_app_overrides=False` with the same note as
  Reduced: the tier's name is not a promise its compositor can keep.
- **A new `Capabilities.per_app_overrides: bool`** carries the tier's promise
  as a platform fact — False on Reduced (and on a Portal run with no toplevel
  protocol), True elsewhere, True on Windows and macOS. The overrides pane stays **editable** where it is False (config is
  per-machine; a Plasma-X11 session or a future shell extension uses the same
  rows) and shows a one-line note that overrides and auto-learn do not apply
  in this session; that copy belongs to the support-tier wording ticket
  (#26). Auto-learn keys on the same identity and is already off on the
  Wayland tiers (ADR 0007).

Rejected: exe basename everywhere (collides, and doesn't survive the
session-type flip); `WM_CLASS` as the stored key (X11-only vocabulary, and
Wayland has no `WM_CLASS`); a picker sourced from running windows (needs
`_NET_CLIENT_LIST` or a KDE-only protocol and is empty on Reduced, for no gain
over the installed list); hiding the overrides pane on Reduced (throws away
rows the same machine may use next session).
