# Research: Linux input — hotkey capture, synthetic typing, clipboard on X11 and Wayland

Ticket: #12 (Linux porting map #11 — input research)
Date: 2026-08-16

> **Research snapshot.** Findings as of the date above; the decisions that
> followed supersede this doc where they differ — see ADR 0007 (injection ladder), ADR 0008 (hotkey capture; the Hyprland / stock-Sway permission split), ADR 0009 (identity), ADR 0012 (permission surface). Read the ADRs
> and `docs/specs/m6-linux-port-spec.md` for what ships.

## TL;DR

X11 fills all three seams today with no permission model at all: **XRecord** for a
listen-only tap (press *and* release, modifier-only chords included), **XTEST** for
typing (with temporary keycode remapping for off-layout unicode), and X selections
guarded by **XFixes** selection-notify events standing in for a sequence number. The
costs are two: XTEST events carry no injected flag (suppression must be a state gate,
not a per-event check), and clipboard contents die with their owning client.

Wayland splits by compositor. The one *cross-desktop* press-and-release hotkey
mechanism is the **GlobalShortcuts portal** (GNOME 48+, KDE, Hyprland — not
wlroots/Sway), and its trigger grammar **cannot express a modifiers-only chord**:
Ctrl+Win-as-shipped is impossible there, so Linux needs a keysym-bearing default
chord or the evdev route. The one cross-desktop unicode typing path is the
**RemoteDesktop portal's `NotifyKeyboardKeysym`** (consent dialog once, then a restore
token); wlroots compositors additionally offer `zwp_virtual_keyboard_v1` with a
client-supplied keymap (full unicode, no dialog), which **neither Mutter nor KWin
implements**. Background clipboard set/restore works on KDE and wlroots via
**ext-data-control** and is **impossible natively on GNOME** — conditional routes are
running under XWayland (Mutter bridges X selections) or the Clipboard portal riding a
RemoteDesktop session. **evdev + uinput** fills every seam on every compositor at the
price of an `input`-group / udev-rule permission story and layout-blind keycodes (no
reliable unicode). Recommendation at the end: ship X11 first; on Wayland, paste-first
(ADR 0001's shape) over portal typing, with per-compositor capability rows.

---

## 1. The lay of the land

X11 has no permission model: any client may snoop input, inject input, and own
selections. Wayland inverts this — a client sees only its own input and may only set
the selection while it holds focus — so every privileged operation goes through
either a **compositor-specific protocol extension** or an **XDG desktop portal**
(D-Bus, user-consent dialogs). Three compositor families matter and they differ on
almost every row:

- **GNOME (Mutter)** implements the fewest privileged Wayland protocols and leans
  entirely on portals. Its portal backend ships `globalshortcuts.c`, `remotedesktop.c`
  and `clipboard.c` ([xdg-desktop-portal-gnome `src/`][xdpg-src]); Mutter's tree
  contains **no** virtual-keyboard and **no** data-control implementation (repo
  search over [GNOME/mutter][mutter-wayland] returns nothing for either), but does
  contain a full EIS server (`src/backends/meta-eis*.c`) for emulated input.
- **KDE (KWin)** implements `ext-data-control-v1` and KDE's privileged `fake-input`
  protocol ([`src/wayland/CMakeLists.txt`][kwin-cmake]), ships an EIS plugin including
  an Xwayland EIS context and InputCapture support ([`src/plugins/eis/`][kwin-eis]),
  and its portal backend implements GlobalShortcuts, RemoteDesktop and Clipboard
  ([xdg-desktop-portal-kde `src/`][xdpk-src]). KWin does **not** implement
  `zwp_virtual_keyboard_v1` (absent from [its protocol list][kwin-cmake]).
- **wlroots (Sway, and cousins like Hyprland, river, Wayfire, niri)** goes the other
  way: rich privileged protocols — `zwp_virtual_keyboard_v1` and data-control are
  supported across the family ([wayland.app support matrix][wl-vk],
  [ext-data-control][wl-edc]) — but a minimal portal backend:
  xdg-desktop-portal-wlr implements *only* Screenshot and ScreenCast, "meant to
  offload the missing portals to other implementations" ([README][xdpw-readme]).
  Hyprland's own backend adds GlobalShortcuts and InputCapture
  ([xdg-desktop-portal-hyprland `src/portals/`][xdph-portals]).

Both major desktops have converged on **libei/EIS** ("a library for Emulated Input,
primarily aimed at the Wayland stack", [libei docs][libei]) as the sanctioned
injection transport, reached via the RemoteDesktop portal's `ConnectToEIS`.

## 2. Hotkey capture (HotkeyTap)

### X11 — POSSIBLE, no permission, no injected flag

pynput's Xorg listener is an **XRecord** context over all clients
([`_util/xorg.py`][pynput-xorg-util] — `record_create_context(...,
[Xlib.ext.record.AllClients])`), delivering every KeyPress/KeyRelease including
modifier-only activity: the Windows hook's shape, minus suppression (XRecord is
strictly listen-only; swallowing would need `XGrabKey`, which cannot express "these
two modifiers, any order, no ordinary key" as one grab). The Ctrl+Win default chord
and its release edge both work as-is.

**Injected-event suppression is the gap.** pynput's `injected` argument on X is
simply the X11 `send_event` flag ([`_util/xorg.py` line 493][pynput-xorg-util]),
which marks `XSendEvent` traffic — XTEST fakes go through normal device-event
processing ([XTEST spec][xtest]) and arrive with `send_event` False, i.e. **our own
typing is indistinguishable from the user's**. The seam's `on_event(..., injected)`
will lie on X11. Mitigation is structural, and Cadent already has it: injection only
runs after chord release, so the adapter can gate the tap (drop or flag events while
`KeyboardOutput` is mid-send) instead of trusting a per-event bit. The same gate
covers the paste chord.

### Wayland — CONDITIONAL, three routes

**GlobalShortcuts portal** (`org.freedesktop.portal.GlobalShortcuts`, version 2):
`CreateSession` → `BindShortcuts` (a user-facing configuration dialog; one bind per
session) → **`Activated` and `Deactivated` signals**, each carrying the shortcut id
and a timestamp ([portal docs][gs-portal]) — press *and* release edges, so
push-to-talk is expressible. Grants persist: `ListShortcuts` returns "shortcuts that
were successfully bound in a previous session by this application". Implemented by
GNOME 48+ ("apps can now create their own system-wide shortcuts",
[GNOME 48 release notes][gnome48]; `globalshortcuts.c` in [the backend][xdpg-src]),
KDE ([`globalshortcuts.cpp`][xdpk-src]) and Hyprland ([portals list][xdph-portals]);
**not** by xdg-desktop-portal-wlr ([README][xdpw-readme]), so stock Sway has no
portal hotkey at all (its users bind keys in the compositor config instead).

**The trigger grammar kills the shipped default chord.** Triggers use the
freedesktop shortcuts spec: `[MODIFIER+]...[MODIFIER+]KEYSYM` — "a set of modifiers
... together with a key identifier" ([shortcuts spec][shortcuts-spec]). A keysym is
mandatory; **a modifiers-only chord (Ctrl+Super) is not expressible**. A Linux
portal-based Cadent needs a different default (e.g. `CTRL+SUPER+space`), or the
evdev route below. Note also the compositor, not Cadent, owns the actual binding —
the user can rebind it in the dialog, so `preferred_trigger` is a suggestion.

**InputCapture portal + libei** is *not* a fit: it captures whole input devices and
is armed by pointer-barrier crossings (built for input-leap-style server hopping,
[portal docs][ic-portal]) — the wrong shape for an always-on hotkey.

**evdev** (`/dev/input/event*`) — POSSIBLE everywhere, including modifier-only
chords and release edges, below the compositor entirely. Permission: device nodes
are `root:input 0660` by systemd's default udev rule (`SUBSYSTEM=="input",
GROUP="input"` — [50-udev-default.rules.in][udev-rules]), so the user must join the
`input` group (a real grant: that group can keylog the whole session). Reading does
not consume events — the chord still reaches the focused app and the compositor
(a bare Super tap opens GNOME's Activities; the Windows MenuMaskKey trick has a
uinput analogue but needs uinput access too). `EVIOCGRAB` would suppress, but it
grabs the *entire device* exclusively — unusable. Suppressing our own injections is
clean here, though: uinput events arrive on a separate device node, filterable by
device identity.

## 3. Synthetic unicode typing (KeyboardOutput)

### X11 — POSSIBLE, reliable, permissionless

**XTEST** (`XTestFakeKeyEvent`, [spec][xtest]) is the standard; xdotool and pynput
both ride it. It is keycode-based, so characters absent from the active layout need
a scratch keycode temporarily remapped — xdotool: "In cases where your keyboard
doesn't actually have the key you want to type, xdotool will automatically find an
unused keycode and use that to type the key" ([man page][xdotool-man]). That remap
is globally visible for its duration (a rare but real race against other clients
reading the keymap), and physically-held modifiers combine with fakes — xdotool grew
`--clearmodifiers` for exactly this; the seam's `modifiers_down()` maps to a keymap
query. No permission, no consent dialog, works in every X session.

### Wayland — CONDITIONAL per compositor

- **`zwp_virtual_keyboard_v1`** (wlroots family only — [support matrix][wl-vk];
  absent from Mutter [source][mutter-wayland] and KWin [source][kwin-cmake]): the
  client uploads **its own xkb keymap** before sending keys ([protocol][wl-vk]), so
  arbitrary unicode is expressible by crafting a keymap — this is wtype ("xdotool
  type for wayland", [README][wtype]). No dialog, no permission beyond the
  compositor exposing the global.
- **RemoteDesktop portal** (version 2, [docs][rd-portal]): the one cross-desktop
  path. `NotifyKeyboardKeycode` sends evdev codes (layout-blind), but
  **`NotifyKeyboardKeysym` sends keysyms** — and xkb defines a unicode keysym for
  every codepoint (`0x01000000 + codepoint`, [xkbcommon][xkbcommon-keysym]) — so
  full unicode typing is POSSIBLE on GNOME and KDE. Cost: a consent dialog on
  `Start`; version 2 adds `persist_mode`/`restore_token` (persist "until revoked",
  single-use tokens reissued each session) so the dialog is once, not per-dictation.
  Not available on Sway (no RemoteDesktop backend in [xdg-desktop-portal-wlr][xdpw-readme]).
- **libei via `ConnectToEIS`** (same portal session): the modern transport — once
  connected, "input events must be sent exclusively via the EIS connection"
  ([docs][rd-portal]). Note the EIS server describes devices (and their keymaps) to
  the client, not the reverse ([libei][libei]), so keysym-level typing through the
  portal methods is the safer unicode route than raw EI keycodes.
- **uinput / ydotool** — POSSIBLE everywhere, unicode UNRELIABLE: kernel-level
  emulation ("emulate input devices from userspace", [kernel docs][uinput]) writes
  keycodes with no idea of the user's layout — ydotool: "ydotool does not recognize
  if the user is using a custom keyboard layout" ([README][ydotool]) — and
  `/dev/uinput` "usually requires root permissions" (ditto; packaged fixes are a
  udev rule + group, or ydotoold running as root). Fine for the *paste chord*
  (Ctrl+V is layout-stable), wrong for transcript typing.
- **KWin `fake-input`**: KDE's privileged keycode-level protocol (still built,
  [CMakeLists][kwin-cmake]); same layout blindness, KDE-only, authorization at the
  compositor's discretion. Not a portable rung.
- **XWayland XTEST bridge** — CONDITIONAL: Xwayland ≥ 23.1 can forward XTEST into
  the compositor through libei/the input-emulation portal (`-enable-ei-portal`,
  "Enable support for the XDG portal for input emulation", [Xwayland man][xwayland-man]);
  Mutter (`meta-eis*.c`) and KWin (`xwaylandeiscontext.cpp`, [eis plugin][kwin-eis])
  both host it. So xdotool-style X clients *can* reach Wayland-native apps — but
  whether a dialog appears, and whether the bridge is compiled in, is distro and
  compositor policy. A diagnostic fallback, not a rung to ship on.

**Consequence for the ladder:** on Wayland, typing is the fragile half — exactly
macOS's shape, for different reasons. ADR 0001's paste-first order, not Windows'
type-first, is the natural Linux default; and as on darwin, portal typing reports
nothing usable about whether the focused app consumed it, so `auto_learn_overrides`
stays false (no detectable typing failure).

## 4. Clipboard set/restore, change detection, paste chord (Clipboard)

### X11 — POSSIBLE, with two caveats

Selections are lazy rendezvous: the owner announces ownership and renders data on
request, so **contents evaporate when the owner exits** — the reason clipboard
managers exist. Cadent holds its process alive, so set → paste → restore works; but
"restore" means *re-owning with the saved text*, and any clipboard manager that took
ownership meanwhile must be out-raced or tolerated. There is no
`GetClipboardSequenceNumber`; the **XFixes** extension provides the event instead:
`SelectSelectionInput` delivers a `SelectionNotify` ("an event delivered whenever
the selection ownership changes", with owner and timestamps — [fixesproto][xfixes]).
The adapter keeps its own monotonic counter, bumped per notify — the seam's
`sequence_number()` contract holds exactly.

### Wayland — split down the middle

- **KDE and wlroots — POSSIBLE**: `ext-data-control-v1` (successor of
  wlr-data-control) lets "a privileged client ... manage the current selection and
  take the role of a clipboard manager" **without focus**: `set_selection` sets,
  the `selection` event fires on every clipboard change ([protocol][wl-edc]) — a
  ready-made sequence-number source. KWin implements it ([source][kwin-cmake]);
  the wlroots family does ([matrix][wl-edc]).
- **GNOME — IMPOSSIBLE natively**: Mutter implements no data-control ([repo
  search][mutter-wayland]), and core `wl_data_device.set_selection` demands the
  "serial number of the event that triggered this request" ([wayland core][wl-core])
  — a background tray app has no such serial. Two conditional routes: (1) **run the
  Qt app as an X client** (`QT_QPA_PLATFORM=xcb`): Mutter bridges X selections to
  the Wayland clipboard (`src/x11/meta-x11-selection*.c`, [mutter tree][mutter-x11]),
  so the X11 adapter's clipboard works on GNOME today; (2) the **Clipboard portal**,
  which only rides an existing RemoteDesktop session (`RequestClipboard` before
  `Start`; `SetSelection`; `SelectionOwnerChanged` for change detection —
  [portal docs][clip-portal]) — sensible only if the typing seam already holds such
  a session.
- **Manager interference / history exclusion**: KDE's Klipper honours the
  `x-kde-passwordManagerHint` mime type with value `secret`
  ([klipper `historymodel.cpp`][klipper-hint]) — the
  `ExcludeClipboardContentFromMonitorProcessing` / `org.nspasteboard.TransientType`
  analogue. No GNOME-wide equivalent exists (GNOME ships no default clipboard
  history); third-party managers vary.
- **Secure-input analogue**: none on either display protocol. Wayland's answer is
  architectural (apps cannot snoop each other), not a queryable flag; there is no
  `IsSecureEventInputEnabled` to preflight, and nothing to name in
  `injection_blocked()` beyond the permission grants themselves.

**Paste chord**: delivering Ctrl+V is the §3 problem again (uinput, portal keysym,
virtual-keyboard, or XTEST — all fine for one chord). One Linux-specific override
fact mirrors Windows: mainstream terminals bind paste to **Ctrl+Shift+V**, so
Linux's default-override table wants its own measured sweep before seeding
(ADR 0001's "we will not seed a rule we have not tested" applies).

## 5. The verdict table

| Need | X11 | GNOME Wayland | KDE Wayland | wlroots/Sway |
| --- | --- | --- | --- | --- |
| Global chord, press+release | POSSIBLE (XRecord) | POSSIBLE (GlobalShortcuts, GNOME 48+) | POSSIBLE (GlobalShortcuts) | Hyprland: portal; Sway: evdev or compositor config only |
| Modifier-only chord (Ctrl+Super) | POSSIBLE | IMPOSSIBLE via portal (grammar); evdev only | same | same |
| Ignore own injected events | No flag (send_event lies for XTEST) — gate by state | Portal events don't loop back to the session; gate anyway | same | evdev: filter by uinput device — clean |
| Unicode typing | POSSIBLE (XTEST + remap) | CONDITIONAL (RemoteDesktop keysym / EIS; XWayland bridge) | CONDITIONAL (same) | POSSIBLE (virtual-keyboard, own keymap) |
| Background clipboard set/restore | POSSIBLE | IMPOSSIBLE native; CONDITIONAL (XWayland bridge, or Clipboard portal in a RemoteDesktop session) | POSSIBLE (ext-data-control) | POSSIBLE (ext-data-control) |
| Clipboard change detection | XFixes notify → own counter | portal `SelectionOwnerChanged` (in-session only) | `selection` event → own counter | same |
| Permission story | none | portal dialogs; restore tokens persist | same | none for privileged protocols; `input` group for evdev |
| uinput/evdev fallback | works | works (below compositor) | works | works |

## 6. Shape of a Linux adapter (for the map, not decided here)

1. **X11 first.** Every seam fills 1:1 with no permission UX: XRecord tap, XTEST +
   remap typing, XFixes-counted selections. `permission_preflight=None`. This also
   covers GNOME/KDE users who still run X sessions, and — via `QT_QPA_PLATFORM=xcb`
   — degrades gracefully under XWayland for clipboard on GNOME.
2. **Wayland second, portal-shaped**: GlobalShortcuts for the tap (which forces a
   keysym-bearing default chord on Linux — a Capabilities fact and a settings-copy
   fact), RemoteDesktop+Clipboard session or ext-data-control for injection
   depending on compositor. Paste-first rungs (ADR 0001 parity),
   `auto_learn_overrides=False` (no detectable typing failure), and
   `permission_preflight` grows a portal-grant value with the restore-token dance
   standing where `AXIsProcessTrusted` stands on darwin.
3. **evdev/uinput as an explicit opt-in**, never a default: it is the only route to
   the shipped modifier-only chord on Wayland, and its price (input group ≈
   keylogger grant, layout-blind typing) belongs in the user's hands, not a silent
   fallback.

## Sources

- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html
- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Clipboard.html
- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html
- https://specifications.freedesktop.org/shortcuts/latest/
- https://release.gnome.org/48/
- https://wayland.app/protocols/virtual-keyboard-unstable-v1
- https://wayland.app/protocols/ext-data-control-v1
- https://wayland.app/protocols/wayland#wl_data_device:request:set_selection
- https://gitlab.gnome.org/GNOME/mutter/-/tree/main/src/wayland (and `src/backends` for `meta-eis*.c`, `src/x11` for `meta-x11-selection*.c`)
- https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/tree/main/src
- https://invent.kde.org/plasma/kwin/-/blob/master/src/wayland/CMakeLists.txt
- https://invent.kde.org/plasma/kwin/-/tree/master/src/plugins/eis
- https://invent.kde.org/plasma/xdg-desktop-portal-kde/-/tree/master/src
- https://invent.kde.org/plasma/plasma-workspace/-/blob/master/klipper/historymodel.cpp
- https://github.com/emersion/xdg-desktop-portal-wlr/blob/master/README.md
- https://github.com/hyprwm/xdg-desktop-portal-hyprland/tree/main/src/portals
- https://libinput.pages.freedesktop.org/libei/
- https://www.mankier.com/1/Xwayland (`-enable-ei-portal`)
- https://xorg.freedesktop.org/archive/X11R7.7/doc/fixesproto/fixesproto.txt
- https://www.x.org/releases/X11R7.7/doc/xextproto/xtest.html
- https://man.archlinux.org/man/xdotool.1.en
- https://github.com/moses-palmer/pynput/blob/master/lib/pynput/_util/xorg.py (XRecord context; `injected = event.send_event`)
- https://pynput.readthedocs.io/en/latest/limitations.html
- https://github.com/moses-palmer/pynput/blob/master/CHANGES.rst (1.8.x `injected` argument)
- https://docs.kernel.org/input/uinput.html
- https://github.com/ReimuNotMoe/ydotool/blob/master/README.md
- https://github.com/atx/wtype/blob/master/README.md
- https://github.com/systemd/systemd/blob/main/rules.d/50-udev-default.rules.in (`SUBSYSTEM=="input", GROUP="input"`)
- https://xkbcommon.org/doc/current/group__keysyms.html (unicode keysyms `0x01000000 + codepoint`)

[gs-portal]: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
[rd-portal]: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html
[clip-portal]: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Clipboard.html
[ic-portal]: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html
[shortcuts-spec]: https://specifications.freedesktop.org/shortcuts/latest/
[gnome48]: https://release.gnome.org/48/
[wl-vk]: https://wayland.app/protocols/virtual-keyboard-unstable-v1
[wl-edc]: https://wayland.app/protocols/ext-data-control-v1
[wl-core]: https://wayland.app/protocols/wayland#wl_data_device:request:set_selection
[mutter-wayland]: https://gitlab.gnome.org/GNOME/mutter/-/tree/main/src/wayland
[mutter-x11]: https://gitlab.gnome.org/GNOME/mutter/-/tree/main/src/x11
[xdpg-src]: https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/tree/main/src
[kwin-cmake]: https://invent.kde.org/plasma/kwin/-/blob/master/src/wayland/CMakeLists.txt
[kwin-eis]: https://invent.kde.org/plasma/kwin/-/tree/master/src/plugins/eis
[xdpk-src]: https://invent.kde.org/plasma/xdg-desktop-portal-kde/-/tree/master/src
[klipper-hint]: https://invent.kde.org/plasma/plasma-workspace/-/blob/master/klipper/historymodel.cpp
[xdpw-readme]: https://github.com/emersion/xdg-desktop-portal-wlr/blob/master/README.md
[xdph-portals]: https://github.com/hyprwm/xdg-desktop-portal-hyprland/tree/main/src/portals
[libei]: https://libinput.pages.freedesktop.org/libei/
[xwayland-man]: https://www.mankier.com/1/Xwayland
[xfixes]: https://xorg.freedesktop.org/archive/X11R7.7/doc/fixesproto/fixesproto.txt
[xtest]: https://www.x.org/releases/X11R7.7/doc/xextproto/xtest.html
[xdotool-man]: https://man.archlinux.org/man/xdotool.1.en
[pynput-xorg-util]: https://github.com/moses-palmer/pynput/blob/master/lib/pynput/_util/xorg.py
[uinput]: https://docs.kernel.org/input/uinput.html
[ydotool]: https://github.com/ReimuNotMoe/ydotool/blob/master/README.md
[wtype]: https://github.com/atx/wtype/blob/master/README.md
[udev-rules]: https://github.com/systemd/systemd/blob/main/rules.d/50-udev-default.rules.in
[xkbcommon-keysym]: https://xkbcommon.org/doc/current/group__keysyms.html
