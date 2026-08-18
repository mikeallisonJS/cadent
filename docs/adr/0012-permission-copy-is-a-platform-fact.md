# Permission copy is a platform fact, and Linux grants by asking

macOS's one grant lives in System Settings, so Cadent's permission surfaces
deep-link to it and poll until it lands. Linux's Wayland tiers have no settings
page: the grant *is* the portal dialog, and Cadent raises it by making the
request. Porting the darwin surfaces verbatim would have shipped
"Privacy & Security → Accessibility" to GNOME users and a button that opens
nothing, so the seam changes shape rather than growing a platform branch.

- **`Capabilities.permission_preflight: str | None` becomes
  `Capabilities.permission: PermissionPreflight | None`** — a frozen dataclass
  carrying `name` plus the five strings the surfaces render: `banner`,
  `wizard_body`, `action_label`, `waiting`, `granted`. Gating stays a
  truthiness check at the same four call sites. The copy in
  `settings_ui/window.py` (`NEEDS_PERMISSION`) and `wizard.py`
  (`PERMISSION_WAITING`, `PERMISSION_GRANTED`, the page body, the button
  label) moves into the adapters, joining `autostart_label`,
  `theme_subtitle` and `high_contrast_reason` as copy the platform owns.
- **`DesktopEnv.open_permission_settings()` becomes `request_permission()`** —
  darwin implements it by deep-linking, Linux by issuing `CreateSession` /
  `BindShortcuts` and `RemoteDesktop.Start`. Fire-and-forget on both, per
  `DesktopEnv`'s never-blocks-never-raises contract; the outcome arrives on
  the portal signal path. The old name would have been a lie on Linux.
- **One Wayland wording for Portal and Reduced.** Both tiers ask for the same
  two portals for the same reason; what differs between them is nothing the
  prompt talks about. The copy says "your desktop" rather than naming
  GNOME/Plasma/the portal, and says "the prompts" without a count, because
  whether one click yields one dialog or two is compositor-dependent.
- **Two new tray faults, mutually exclusive**: `hotkey-unavailable` (the
  GlobalShortcuts interface is absent — stock Sway) and `permission-needed`
  (the interface is there and the grant is missing). Never both: telling a
  Sway user to grant a permission no dialog will offer them is worse than
  silence. `status_line()`'s existing `setup-unfinished` short-circuit
  already subsumes both during first run.
- **The grant poll moves to `app.py`** — one 2s timer where
  `caps.permission is not None`, owning the fault; the Settings banner and
  wizard page read it instead of running their own timers. This changes
  darwin behaviour deliberately: today, granting Accessibility with no window
  open leaves the tray green while the loop is dead, and leaves it stale after.
- **The restore token lives in `DATA_DIR / "portal-tokens.json"`, not
  `config.json`** — an opaque credential has no place in a hand-edited file,
  and "Back it up and start fresh" would otherwise revoke the grant as a side
  effect of fixing an unrelated problem. A lost or rejected token re-asks
  silently; only a denial raises the banner.
- **The support tier is named to the user**, in a Settings ▸ General row
  present on every Linux session (absent where `support_tier` is None), built
  by the adapter as one `support_tier_summary` fact — never assembled from
  parts by cross-platform UI code. The tier word is followed immediately by
  its consequence: "Wayland session on GNOME — Reduced support: no overlay, no
  per-app overrides." The wizard's Done page shows the same line only on
  Portal and Reduced: the General row is where the fact is looked up, so it
  must always answer; the wizard is a flow passed through once, where
  "everything works here" answers nothing.

## Considered options

- **Flat copy fields on `Capabilities` beside `permission_preflight`** —
  rejected: five parallel fields plus a both-or-neither invariant that can
  drift. One fact cannot fall out of sync with itself.
- **Per-tier permission copy** — rejected: two strings to keep in step that
  never legitimately diverge.
- **A permanent banner for the tier** — rejected: banners are for actionable
  things that clear, and a permanent one trains users to ignore banners.
- **A central tier line enumerating every missing capability** — rejected in
  favour of short summaries plus local notes at the point of confusion (the
  Hotkeys pane's "your desktop owns this shortcut", the overrides pane's
  "not applied in this session" and "pasting isn't available in this
  session"). A central list must be re-edited whenever a tier changes.
- **Printing `XDG_CURRENT_DESKTOP` raw** — rejected: `ubuntu:GNOME` in a
  settings row reads as a bug. A known-name map, and on no match the desktop
  clause is dropped; the tier and session type are known with certainty and
  carry the line alone.
- **Blocking `request_permission()`** — rejected: it would freeze the wizard
  behind the very dialog the wizard is telling the user to go answer.
- **Auto-retry after a denial** — rejected, per ADR 0008's rule that a silent
  retry loop is worse than an honest dead state. The button stays enabled; the
  user asks again.
