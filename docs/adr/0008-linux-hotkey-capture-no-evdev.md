# Linux hotkey capture follows the support tier, and Cadent never reads evdev

The capture mechanism is the **support tier**'s, like the injection ladder
before it (ADR 0007): **Whole** taps XRecord through pynput — press and release,
sided modifiers, no permission at all — and **Portal** and **Reduced** bind
`org.freedesktop.portal.GlobalShortcuts` and listen for `Activated` /
`Deactivated`. Raw `/dev/input` capture, which would fill every seam on every
compositor, is **not shipped in v1 and not hidden as a fallback**. Decided
in #19, on the Linux porting map (#11). Mirrors ADR 0002, which settled the same
question — what one grant do we ask for, and what is the honest health check —
for macOS.

- **`permission_preflight` is `None` on Whole and `"portal"` on the Wayland
  tiers.** One value, up to two grants: the shortcut binding always, and the
  RemoteDesktop input session **when the mechanisms ADR 0007 selected need
  it** — KWin, SteamOS and GNOME type and paste through that session; a
  wlroots compositor typing via `zwp_virtual_keyboard_v1` and pasting via
  `ext-data-control-v1` never asks for it (Hyprland's portal ships
  GlobalShortcuts and no RemoteDesktop backend, and must not fault forever
  on a session it cannot open). `permission_granted()` is true only when
  every grant the run's mechanisms need is live, because a user holding one
  without another has a dead loop and has earned the banner.
- **The honest health check is never the listener's own flag** — ADR 0002's
  rule, transplanted. On Whole there is no grant to poll and the X connection
  fails loudly, with an exception. On the Wayland tiers a user who unbinds
  Cadent in the compositor's own dialog produces *silence*, so health is
  `ListShortcuts` reporting our shortcut plus a live input session.
- **The tier's name is not a promise its bus can keep.** Session type and
  desktop fix the tier at startup (#17), then the Wayland tiers probe for the
  GlobalShortcuts interface. Stock Sway has none — xdg-desktop-portal-wlr ships
  Screenshot and ScreenCast only — so Cadent starts there with the hotkey
  disarmed and says so. A named failure inside Portal, not a fourth tier, in
  the shape ADR 0007 already used for a missing Clipboard portal.

## Considered options

**evdev + uinput as the Wayland capture path** was the real alternative, and it
is genuinely better on two rows: it is the only route to the shipped
modifier-only Ctrl+Super chord under Wayland, and it filters our own injected
events by device identity instead of by the state gate ADR 0007 settled for. It
was rejected on price. Reading `/dev/input/event*` requires joining the `input`
group — a grant that lets any process in it keylog the whole session, which is
a heavier ask than anything Cadent makes on Windows or macOS, and heavier than
the portal dialogs it would replace. The udev rule that would make it civil
cannot be installed by the tarball or AppImage that #15 chose to ship first.
Typing through uinput is layout-blind, so it buys nothing for the ladder. And
it is a second capture path to validate on every Wayland target. What it buys
back is a chord shape — which a keysym-bearing default already replaces, at the
cost of two keys of muscle memory. The spec names evdev as a deliberate
non-goal with this price attached, so the question does not get re-litigated
from scratch.

**Two preflight values**, one per grant, was rejected for ADR 0002's reason: no
useful configuration of the app holds one and not the other, so a second
wizard line would double the friction to unlock zero states.

**Forcing XWayland to keep the shipped chord on Wayland** was already ruled out
by #17 and is not reopened here.

## Consequences

**The shipped chord is not the Linux default everywhere.** The freedesktop
shortcuts grammar requires a keysym, so `<ctrl>+<cmd>` and the cleanup tap
`<ctrl>+<shift>+<alt>` are unbindable on the Wayland tiers. They default there
to `<ctrl>+<cmd>+space` and `<ctrl>+<cmd>+c`, carried as platform facts rather
than as `Config` literals. A `config.json` written in an X11 session and opened
in a Wayland one is **not rewritten** — one file serves both sessions — the
tier default binds for that run and the copy says why.

**On Wayland the compositor owns the binding.** `preferred_trigger` is a
suggestion the user may override in the bind dialog, so what fires is whatever
`ListShortcuts` reports, and Settings shows that rather than pretending the
text field is authoritative.

**The seam learns which chords to watch.** `HotkeyTap.start` gains the chords to
bind; X11, Windows and macOS ignore it and keep tapping everything, while the
portal adapter binds both chords in one call and synthesizes the parsed
keysym events its shortcut ids stand for — so `chord.py` and `hotkey.py` stay
unchanged, hold, toggle, `min_hold_ms` and the cleanup tap included. One
behaviour cannot cross: "any non-chord keydown mid-hold cancels" needs a view
of the whole keyboard, which the Wayland tiers structurally lack.

**A dead portal session is recovered once, then admitted.** `xdg-desktop-portal`
restarting closes the session and silences the hotkey with no error, so the
adapter re-creates it on `Closed` — silent, on the persisted restore token —
and on failure stops and lets `permission_granted()` go false. A silent retry
loop is the one outcome worse than an honest dead state.

**One item rides on real hardware**, in ADR 0002's spirit: bare Super opens
GNOME's Activities and Plasma's launcher, and whether holding Ctrl first
suppresses that is not answerable by reading. `send_mask_key()` is a no-op on
Linux for v1; if a launcher pops on Plasma or GNOME under X11, the fix is
XTEST-injecting a harmless keysym mid-chord — the Windows trick in a different
key namespace, not a different design.
