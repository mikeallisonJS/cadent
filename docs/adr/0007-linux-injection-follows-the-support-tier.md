# Linux injection follows the support tier: X11 types, Wayland pastes

On Linux the injection ladder is decided by the **support tier**, not the distro
and not a Linux-wide rule. There is no one Linux answer because there is no one
Linux input model: X11 has no permission model and lets any client fake keys and
own selections; Wayland lets a background app do neither without a compositor
extension or a portal. Decided in #18, on the Linux porting map (#11), on top of
the tier partition from #17. Mirrors ADR 0001, which made the same call for
Windows versus macOS.

- **Whole (X11)** is Windows parity: rungs `("type", "paste")`, `paste_chord`
  `ctrl+v`. Typing is XTEST with a scratch keycode temporarily remapped for
  characters the active layout lacks; paste is X selection ownership with an
  XFixes `SelectionNotify` counter standing in for the sequence number.
  `auto_learn_overrides=True` — an XTEST request error or an exhausted scratch
  keycode raises, which is a *detectable* typing failure in ADR 0001's sense.
- **Portal (Wayland on Plasma, wlroots, SteamOS desktop)** and **Reduced (GNOME
  Wayland)** share one ladder: rungs `("paste", "type")`, `paste_chord` `ctrl+v`,
  **no automatic paste→type fall-through** (setting a selection cannot
  meaningfully fail — darwin's shape), `auto_learn_overrides=False` (portal
  typing reports nothing about whether the app consumed it). "Type it" remains
  an explicit per-app override, never reached automatically.
- The **mechanism** under each Wayland rung is chosen per compositor at
  startup, inside the tier. Typing: probe for `zwp_virtual_keyboard_v1` first
  (wlroots family — client-supplied keymap, full unicode, no dialog), else the
  RemoteDesktop portal's `NotifyKeyboardKeysym` with a persisted restore token
  (KWin, SteamOS, GNOME). Paste: `ext-data-control-v1` where the compositor
  offers it (KWin, wlroots), else the **Clipboard portal riding the same
  RemoteDesktop session** (`RequestClipboard` before `Start`; `SetSelection`;
  `SelectionOwnerChanged` as the counter) — which is what gives GNOME its paste
  rung. Ctrl+V and "Type it" ride the same typing mechanism.
- Where no paste mechanism exists at runtime — no `ext-data-control-v1` and
  no Clipboard portal (an older portal backend) — **either Wayland tier drops
  the paste rung for that run**: rungs `("type",)`, and the copy says why.
  Typing is then the run's only rung, not a fall-through: the no-automatic
  paste→type rule governs a ladder that *has* a paste rung, and "Type it"
  stays the explicit override where paste exists. Same tier, one fewer rung;
  not a fourth tier and never a forced XWayland fallback. Reduced is the tier
  most likely to hit this (GNOME has no `ext-data-control`); Portal hits it
  only on a compositor offering neither.
- A wlroots compositor with neither the virtual-keyboard global nor a
  RemoteDesktop backend is unsupported.

## Considered options

Paste-first on X11 too, for one Linux story, was rejected: XTEST typing is as
reliable as `SendInput`, permissionless, and Ctrl+V is not paste in mainstream
Linux terminals — the exact reason Windows types. A single RemoteDesktop-only
typing path on Wayland was rejected because the wlroots family ships no
RemoteDesktop backend; the virtual-keyboard probe is what keeps Sway and
Hyprland in the Portal tier. Keeping Reduced type-only in v1 (the tier
decision's original row) was rejected once it was clear the Clipboard portal
costs nothing extra: the RemoteDesktop session is already held for typing.

## Consequences

**Injected-event suppression is a state gate, not a per-event flag.** XTEST fakes
arrive with `send_event` false, indistinguishable from the user's keys, so the
X11 tap drops or flags events while `KeyboardOutput` is mid-send rather than
trusting the `injected` bit; the same gate covers the paste chord. Portal input
does not loop back into a GlobalShortcuts session, but the gate stays on
everywhere. If the opt-in evdev/uinput mode ships, it filters by its own uinput
device identity.

**No secure-input analogue.** `injection_blocked()` returns `None` on every tier;
Linux has nothing like UIPI or `IsSecureEventInputEnabled` to name. Whether the
portal grant is present is `permission_granted()`'s job.

**Linux ships an empty default override table.** Mainstream terminals bind paste
to Ctrl+Shift+V, so overrides are coming — but ADR 0001's rule holds: no seeded
rule without a measured sweep, and that sweep is a later verification pass.

**This amends the tier decision (#17).** Reduced now differs from Portal only in
overlay and per-app overrides, not in the ladder; the glossary's `Support tier`
entry says so.
