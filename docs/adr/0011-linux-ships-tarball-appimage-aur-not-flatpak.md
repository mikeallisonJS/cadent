# Linux ships a tarball, an AppImage and an AUR package — not a Flatpak

Every other desktop Linux app of this shape ships a Flatpak, and on the one
target where the choice is loudest — SteamOS, whose root filesystem is
read-only and replaced wholesale on update — Valve's own guidance funnels
desktop-mode software to Flatpak and Discover. Cadent ships **`.tar.zst` +
AppImage** from one workflow, with an `cadent-bin` AUR package repacking the
released tarball, and **no Flatpak**. The reason is that Cadent's whole
product is synthetic input, and the Flatpak sandbox is the one format that
stands between the app and the input stack.

The permission vocabulary is the evidence. `--device=input` exposes
`/dev/input` but confers no `input` group, and no grant at all exposes
`/dev/uinput` — so the evdev/uinput backend is unreachable from inside a
sandbox, not merely awkward. `--socket=x11` does hand over the whole display
including XTEST, so a Flatpak would work perfectly on the Whole tier and go
deaf the moment the user logged into a Wayland session — and Flathub review
would rightly refuse the static grants that paper over that. The portal path
(ADR 0007, ADR 0008) is the honest Wayland answer, and it is worth building
for *every* format, not just for a Flatpak; once it exists a Flatpak becomes
possible, which is why this is a "not yet", not a "never". What it is not is
a reason to hold the port.

Flatpak's other draw is an update channel on every target. Cadent has no
in-app updater on Windows or macOS, so that is a capability the app declines
everywhere, not one Linux uniquely gives up. What the tarball and AppImage do
give up is SteamOS's *supported* install path — but only the rootfs is
replaced on update, so a file in `/home` survives exactly as well as a
Flatpak does, and pacman-into-the-rootfs is the option that does not.

## What follows from it

**The glibc floor is a stated support fact, not an accident of the runner.**
Builds run on **`ubuntu-24.04` pinned by name**, mirroring the `macos-14`
pinning, which puts the floor at glibc 2.39 — "Ubuntu 24.04 LTS or newer, any
rolling distro." The AppImage project's build-on-the-oldest-supported rule
argues for a `manylinux_2_28` container instead, reaching back to the Ubuntu
20.04 era; it was rejected because every named validation target — Arch,
CachyOS, SteamOS 3, Plasma — is rolling and far above 2.39, and the container
trades one line of YAML for a Qt and portal build surface with no desktop
stack to debug it against. The research's `ubuntu-22.04` recommendation was
overtaken by events: those runners begin deprecation on 2026-09-17.

**Nothing installs a desktop entry, so Cadent installs its own.** A tarball
and an AppImage have no install step, which means no launcher entry, no menu
icon, and — on Wayland — a window whose `app_id` matches nothing installed.
On first run Cadent writes
`$XDG_DATA_HOME/applications/com.mikeallisonjs.cadent.desktop` (default
`~/.local/share/applications`; `$XDG_DATA_DIRS` is a search path, never a
write target) and the
hicolor 48/128/256 PNGs, idempotently, and **skips it where a system-wide
entry already exists** so the AUR package stays authoritative. The same
reverse-DNS id is the `.desktop` basename, the `Icon=` name,
`StartupWMClass`, and `QGuiApplication::setDesktopFileName()` — which is the
only thing Qt derives a Wayland `app_id` from, and the identity ADR 0009
already picked for every *other* app applied to ourselves.

**Autostart heals its own path.** The XDG entry's `Exec=` is the
**absolute path read from the `APPIMAGE` environment variable at write time**
where set and `sys.executable` otherwise — written resolved, because desktop
entries expand no variables — since inside an AppImage `sys.executable` is an
ephemeral `/tmp/.mount_*` path that dies with the process. `TryExec=` carries
the same path, so the autostart spec's own
rule disables a deleted tarball or a moved AppImage at login instead of
erroring; and where the entry exists with a stale `Exec=`, the adapter
rewrites it in place — the checkbox said on, so it stays on and points at the
binary actually running.

**The build stages `libportaudio` itself.** This is the one load-bearing file
with no Windows or macOS twin: no manylinux `sounddevice` wheel bundles
PortAudio, so on Linux the library is an apt dependency of the *build*, copied
into the bundle, rather than something the wheel brought along.

## What it costs

**Two artefacts and a manual publish.** The AUR package is checked in as
`packaging/aur/PKGBUILD` but pushed to `aur.archlinux.org` by hand — no
workflow of ours can own a repo we don't control — so an Arch user's update
path is exactly as prompt as someone remembers to make it.

**No update channel anywhere on Linux**, which is the same amount of update
channel Windows and macOS have.

**The Deck is served by the pragmatic path, not the supported one.** An
AppImage in `/home` survives SteamOS updates, but it is not what Discover
lists, and a user looking for Cadent in the store will not find it.
