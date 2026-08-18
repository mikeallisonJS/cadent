# Research: Linux packaging — format vs sandbox vs SteamOS

Ticket: #15 · Date: 2026-08-16

Question: for the PyInstaller-onedir Qt tray app (global input capture, synthetic
typing, mic access, optional GPU), how do AppImage, Flatpak, AUR/native and a
plain tarball fit Arch, CachyOS, SteamOS desktop mode and Ubuntu — and what
would a Linux leg of scripts/build.py and CI need?

## 1. The input mechanisms decide the format, not the other way round

The formats differ mostly in *how much they stand between the app and the input
stack*, so the input stack has to be pinned down first.

- **X11 sessions.** Synthetic input is XTEST: "limited synthesis of input
  device events, almost as if a cooperative user had moved the pointing device
  or pressed a key or button" — events "cause event propagation, passive grab
  activation, and so on, just as if the corresponding input device action had
  occurred," and any client on the display can do it, no privilege involved
  ([XTEST 2.2 spec](https://www.x.org/releases/X11R7.7/doc/xextproto/xtest.html)).
  Capture is equally open (XRecord / global grabs). This is the path pynput's
  default Linux backend uses: "An X server must be running" and "the
  environment variable `$DISPLAY` must be set"
  ([pynput limitations](https://pynput.readthedocs.io/en/latest/limitations.html)).
- **Wayland sessions.** No XTEST, no global grabs; pynput under Wayland rides
  XWayland and "you will only receive input events from applications running
  under this emulator" ([pynput limitations](https://pynput.readthedocs.io/en/latest/limitations.html)).
  The kernel-level bypass is evdev for capture (`/dev/input`, conventionally
  gated by the `input` group) and uinput for injection — "a kernel module that
  makes it possible to emulate input devices from userspace. By writing to
  /dev/uinput … a process can create a virtual input device"
  ([kernel uinput docs](https://www.kernel.org/doc/html/latest/input/uinput.html)),
  which pynput only reaches as root: "You must run your script as root, so that
  it has the required permissions for uinput"
  ([pynput limitations](https://pynput.readthedocs.io/en/latest/limitations.html)).
- **The portal path (Wayland-native, sandbox-compatible).** Three portals
  divide the territory. [GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)
  registers named shortcuts via `BindShortcuts()` (a consent dialog "showing
  the shortcuts and allowing users to configure" them) and emits `Activated` /
  `Deactivated` signals — a press/release pair, which is exactly the
  push-to-talk contract; the app "cannot capture arbitrary keys — only
  pre-registered shortcuts." [RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
  is the injection side: `NotifyKeyboardKeycode` / `NotifyKeyboardKeysym`
  behind a `CreateSession` → `SelectDevices` consent dialog, with
  `persist_mode` 2 ("persist until explicitly revoked") plus `restore_token`
  so the dialog is not per-launch. [InputCapture](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html)
  is *not* the hotkey answer despite the name: capture triggers on pointer
  barriers and "there is currently no way for an application to activate
  immediate input capture."

So the Linux input seam has three backends in falling order of reach: XTEST
(X11 only, zero friction), portals (Wayland-native, consent dialogs, needs a
portal backend that implements GlobalShortcuts/RemoteDesktop), evdev/uinput
(display-server-agnostic, permission-gated, sees keys even in
grab-everything games). None of this is packaging work, but §2 shows one
format forecloses two of the three.

## 2. Format fit

### Plain tarball of the onedir

The zero-machinery baseline: tar up `dist/Cadent/` the way the .app is the
macOS artefact. Runs with full user permissions — every input backend in §1 is
available exactly as the host allows. The costs are all integration: no
.desktop file, no icon, no update channel, and the glibc floor is whatever the
build runner has, which is why AppImage's own guidance is to build "on the
oldest still-supported" distribution so the binary runs on everything newer
([AppImage concepts](https://docs.appimage.org/introduction/concepts.html)) —
advice that applies verbatim to a bare tarball.

### AppImage

"Every AppImage is a regular file, and every AppImage contains exactly one app
with all its dependencies" ([AppImage concepts](https://docs.appimage.org/introduction/concepts.html))
— an AppDir (which a PyInstaller onedir nearly already is) plus a runtime,
made executable. No installation, no root, and crucially **no sandbox**: the
process runs like any user binary, so XTEST, evdev, uinput and
`~/.config/autostart` all behave as on the host. Two known taxes:

- **FUSE.** Type-2 AppImages mount via libfuse2, which modern distros no
  longer ship by default — Ubuntu 22.04+ ships only fuse3, so users install
  `libfuse2` or fall back to `--appimage-extract-and-run`
  ([AppImage FUSE troubleshooting](https://docs.appimage.org/user-guide/troubleshooting/fuse.html)).
- **Desktop integration is not automatic.** The .desktop file and icon live
  *inside* the image; menus/autostart see them only via a helper (appimaged)
  or by the app writing its own entries pointing at its current path — which
  breaks if the user moves the file (§4).

### Flatpak

The only format with an update channel on every target and the only one that
is first-class on SteamOS (§3) — and the only one whose sandbox collides with
§1. The static permission vocabulary has `--device=input` ("input devices as
exposed in /dev/input"), `--device=all`, `--socket=x11`, `--socket=wayland`
([sandbox permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html)),
but no grant exposes `/dev/uinput`, and DAC still applies inside the sandbox —
`--device=input` does not confer the `input` group. On an X11 session,
`--socket=x11` hands over the whole display, XTEST included (X11 "does not
have window isolation", which is why the docs steer Wayland-native apps to
`fallback-x11`); on Wayland the portals of §1 are the *only* road. And Flathub
review points the same way: "static permissions must be kept to an absolute
minimum" and portals are mandatory where they exist
([Flathub requirements](https://docs.flathub.org/docs/for-app-authors/requirements)).

Net: shipping today's pynput-based capture in a Flatpak is shipping an app
that works on X11 sessions and goes deaf on Wayland ones. A Flatpak becomes
honest only after the platform seam grows a portal-backed HotkeyTap
(GlobalShortcuts) and KeyboardOutput (RemoteDesktop) — both of which then also
depend on the user's desktop implementing those portals. GPU is the easy part
(`--device=dri` "necessary for GL") and mic is `--socket=pulseaudio` or the
Device portal.

### AUR / native package

Covers Arch and, by descent, CachyOS (an Arch-based rolling distro using
pacman/AUR). A package repacking the released tarball must carry the `-bin`
suffix: "packages that use prebuilt deliverables, when the sources are
available, must use the -bin suffix"
([AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines)).
`cadent-bin`: install the onedir under `/opt/cadent/`, the .desktop to
`/usr/share/applications/`, icons per §5. Full native permissions, a real
package manager for updates, and the PKGBUILD is ~40 lines against release
assets that already exist. It does nothing for Ubuntu, and on SteamOS pacman
installs sit on the doomed side of the read-only line (§3).

## 3. SteamOS desktop mode: what survives

SteamOS 3 keeps the root filesystem read-only and updates it as an image; the
`steamos-readonly` tool can disable protection for pacman work, but anything
installed into the rootfs that way is liable to be wiped by the next SteamOS
update — only the home partition persists — which is why Valve's guidance
funnels users to Flatpak/Discover for desktop-mode software
([Steam Deck FAQ](https://help.steampowered.com/en/faqs/view/1B71-EDF2-EB6D-2BB3),
[steamos-readonly](https://linuxcommandlibrary.com/man/steamos-readonly),
[GamingOnLinux on dev mode](https://www.gamingonlinux.com/2022/04/steam-deck-developer-mode-does-not-turn-off-the-read-only-filesystem/)).
The practical ranking for Cadent on a Deck:

1. **Flatpak** — survives updates, updates via Discover, but inherits every
   sandbox constraint of §2 (and desktop mode is a Plasma Wayland-family
   session, so the portal work is a prerequisite, not a nicety).
2. **AppImage or tarball in `/home`** — survives updates too (only the rootfs
   is replaced), full permissions, no update channel; the user manages the
   file. This is the pragmatic near-term answer for the Deck.
3. **pacman/AUR after `steamos-readonly disable`** — works until the next
   update; not a supported target, only a documented caveat.

The `input`-group / uinput permission question is per-device configuration on
SteamOS as anywhere; udev rules live in `/etc/udev/rules.d` which sits on the
writable `/etc` overlay, but documenting "add a udev rule" as a install step
is a support burden to weigh against the portal path.

## 4. .desktop and autostart per format

The autostart mechanism is the freedesktop one everywhere except inside a
sandbox: drop a desktop entry in `$XDG_CONFIG_HOME/autostart` (default
`~/.config/autostart/`), system-wide in `/etc/xdg/autostart/`; `Hidden=true`
means the entry "MUST be ignored", and an entry with `TryExec` "MUST NOT be
autostarted" if the named executable is missing
([autostart spec](https://specifications.freedesktop.org/autostart-spec/latest/)).
That `TryExec` clause is a free robustness win for the Autostart adapter — a
deleted tarball or moved AppImage disables itself instead of erroring at
login. A systemd user unit (`~/.config/systemd/user/`, wanted by the session
target) is the other idiom; it adds restart-on-crash but ties Cadent to
systemd-managed sessions, and the XDG entry is what desktop Settings panels
display and toggle — so the XDG entry is the right default, mirroring the HKCU
Run value and LaunchAgent the other adapters manage.

Per format: **tarball/AUR** write the autostart entry directly (`Exec=` the
installed path). **AppImage** writes an entry pointing at its own
`$APPIMAGE` path — correct until the user renames or moves the file, so the
adapter should verify the path on every settings read (the `TryExec` guard
catches the stale case at login). **Flatpak** cannot write the host's
`~/.config/autostart`; the sandboxed idiom is the Background portal's
`RequestBackground` with `autostart: true`, where "the `Exec` key from the
desktop file will be used" and the response reports whether autostart was
granted ([Background portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Background.html))
— a third Autostart adapter shape, consent-based rather than declarative.

Conventions the entry itself must follow: reverse-DNS file name matching the
app id (`com.mikeallisonjs.cadent.desktop` — Flathub enforces the id format
outright, "reverse-DNS … maximum 255 characters", domain controlled by the
author, [Flathub requirements](https://docs.flathub.org/docs/for-app-authors/requirements)),
installed under `$XDG_DATA_DIRS/applications`, `Icon=` a bare name resolved
through the icon theme machinery (§5), and `StartupWMClass`/app-id matching
the Qt application name so the window (settings, overlay) associates with the
right launcher entry.

## 5. Icons

Third-party apps install into **hicolor**: "in order to have a place for third
party applications to install their icons there should always exist a theme
called 'hicolor' … implementations are required to look in the 'hicolor'
theme if an icon was not found in the current theme," minimally "a PNG file in
$prefix/share/icons/hicolor/48x48/apps"
([icon theme spec](https://specifications.freedesktop.org/icon-theme-spec/latest/)).
The rasterised PNG set that #73 already generates for the tray covers this —
install the 48/128/256 PNGs as
`icons/hicolor/{size}x{size}/apps/com.mikeallisonjs.cadent.png`, named after
the desktop entry's `Icon=` value. No .ico/.icns sibling needed; Linux is the
one platform whose icon container is the loose PNGs themselves.

## 6. SingleInstance

Outside a sandbox the boring answer works: a lock file in `$XDG_RUNTIME_DIR`
(per-user, tmpfs, cleared at logout) — the direct analogue of the named mutex
/ file lock pair ADR 0005 already names. Inside Flatpak the conventional
mechanism is D-Bus name ownership — the manifest grants it explicitly
(`--own-name`: "allow the application to own the well known name NAME on the
session bus", [flatpak command reference](https://docs.flatpak.org/en/latest/flatpak-command-reference.html))
— because a second `flatpak run` happily starts a second sandbox; Flatpak
itself deduplicates nothing. Since the lock-file mechanism also works inside
the sandbox (the lock just lives in the sandbox-visible runtime dir), the
adapter can stay file-lock-only until a Flatpak actually ships, at which point
owning `com.mikeallisonjs.cadent` on the session bus does double duty as
activate-the-running-instance IPC.

## 7. The third LOAD_BEARING key and a Linux workflow

The `linux` list in `scripts/build.py` is the same silent-breakage catalogue
with `.so` spellings, plus one platform plugin that is genuinely new — Qt
needs *both* display plugins because the session decides at runtime:

- `Cadent` (the executable)
- `_internal/ctranslate2*/*libctranslate2*.so*` — custom hook: STT engine
- `_internal/llama_cpp/lib/libllama.so` — custom hook: ctypes-loaded
- `_internal/llama_cpp/lib/libggml-vulkan.so` — the Vulkan backend, same
  scan-the-lib-dir silent-CPU-fallback failure mode as both existing legs
  (Vulkan is also the right Linux GPU story for the Deck's RDNA2)
- `_internal/faster_whisper/assets/silero_vad_v6.onnx`
- `_internal/onnx_asr/preprocessors/data/nemo128.onnx`
- `_internal/PySide6/Qt/plugins/platforms/libqxcb.so` **and**
  `.../platforms/libqwayland-generic.so` — either missing means Qt cannot
  start on that session type
- `_internal/_sounddevice_data/portaudio-binaries/libportaudio*.so*`

(No DirectML analogue: on Linux the onnxruntime GPU flavour question becomes
CPU vs CUDA/ROCm and is a follow-on decision, not a packaging fact.)

Workflow shape, cloned from `build-installer.yml`: `ubuntu-22.04` runner (the
oldest supported LTS, per the build-on-old rule in
[AppImage concepts](https://docs.appimage.org/introduction/concepts.html) —
this sets the glibc floor for tarball and AppImage alike), `uv sync
--all-extras`, `scripts/build.py --skip-sync`, then a `scripts/build_appimage.py`
counterpart of `build_dmg.py` that stages the onedir as an AppDir (add the
.desktop, hicolor PNGs, AppRun symlink), runs `appimagetool`, and also emits
the plain `.tar.zst` the AUR `-bin` PKGBUILD consumes. `test.yml` grows an
`ubuntu-22.04` leg with `qpa: offscreen`, which also makes the ADR 0005
import-walk rule real for a third OS.

## 8. Recommendation

1. **Ship the tarball + AppImage from one workflow first.** Full input-stack
   freedom on every target including SteamOS `/home`, one new build script,
   and the same artefact feeds step 2.
2. **AUR `cadent-bin` second** — Arch and CachyOS covered for ~40 lines
   against released assets ([AUR guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines)).
3. **Flatpak only after the portal backends exist.** GlobalShortcuts for
   HotkeyTap, RemoteDesktop (with `persist_mode`/`restore_token`) for
   KeyboardOutput; until then a Flatpak is an app that cannot hear its own
   hotkey on a Wayland desktop, and Flathub review would rightly balk at the
   permission grants that paper over it. The portal work is desirable anyway —
   it is the only Wayland-native path for *every* format, not just Flatpak.
4. **Autostart adapter:** XDG autostart entry with `TryExec`, Background
   portal variant reserved for the eventual Flatpak. **SingleInstance:** file
   lock in `$XDG_RUNTIME_DIR`.

## Sources

- XTEST extension spec — https://www.x.org/releases/X11R7.7/doc/xextproto/xtest.html
- pynput platform limitations — https://pynput.readthedocs.io/en/latest/limitations.html
- Kernel uinput docs — https://www.kernel.org/doc/html/latest/input/uinput.html
- GlobalShortcuts portal — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
- RemoteDesktop portal — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html
- InputCapture portal — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html
- Background portal (sandboxed autostart) — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Background.html
- Flatpak sandbox permissions — https://docs.flatpak.org/en/latest/sandbox-permissions.html
- Flatpak command reference (--own-name) — https://docs.flatpak.org/en/latest/flatpak-command-reference.html
- Flathub app requirements — https://docs.flathub.org/docs/for-app-authors/requirements
- AppImage concepts — https://docs.appimage.org/introduction/concepts.html
- AppImage FUSE troubleshooting — https://docs.appimage.org/user-guide/troubleshooting/fuse.html
- AUR submission guidelines — https://wiki.archlinux.org/title/AUR_submission_guidelines
- XDG autostart spec — https://specifications.freedesktop.org/autostart-spec/latest/
- XDG icon theme spec — https://specifications.freedesktop.org/icon-theme-spec/latest/
- Steam Deck FAQ (read-only rootfs) — https://help.steampowered.com/en/faqs/view/1B71-EDF2-EB6D-2BB3
- steamos-readonly — https://linuxcommandlibrary.com/man/steamos-readonly
- Steam Deck dev mode and the read-only filesystem — https://www.gamingonlinux.com/2022/04/steam-deck-developer-mode-does-not-turn-off-the-read-only-filesystem/
