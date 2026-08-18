# Research: Linux audio — PortAudio over PipeWire, PulseAudio, ALSA

Ticket: #16 (part of #11) · Date: 2026-08-16

Question: does sounddevice/PortAudio (16 kHz mono float32 capture, `cadent/audio.py`)
behave across the Linux targets, where PipeWire is now the norm? Which host API does
it land on, what do default-device and device-disappearance look like, is there any
mic permission to hint about, and what should `Capabilities.mic_permission_hint` say?

## 1. There is no wheel-bundled PortAudio on Linux — that premise is Windows/macOS only

The sounddevice wheels bundle a PortAudio binary **only on Windows and macOS**. The
install docs are explicit: on other platforms "you might have to install PortAudio
with your package manager (the package might be called `libportaudio2` or similar)"
([sounddevice installation docs](https://python-sounddevice.readthedocs.io/en/latest/installation.html)).
PyPI confirms it: sounddevice 0.5.5 ships win/mac binary wheels plus one
`py3-none-any` wheel and an sdist — **no manylinux wheel exists**
([PyPI file list](https://pypi.org/pypi/sounddevice/json)), and the
[portaudio-binaries build workflow](https://github.com/spatialaudio/portaudio-binaries/blob/master/.github/workflows/build-libs.yml)
that produces the bundled binaries (PortAudio v19.7.0) has macOS and Windows jobs only.

At import time `sounddevice.py` tries `ctypes.util.find_library('portaudio')` first and
falls back to the bundled `_sounddevice_data/portaudio-binaries` copy only on darwin/win32;
on Linux the fallback **re-raises** `OSError("PortAudio library not found")`
([sounddevice source](https://github.com/spatialaudio/python-sounddevice/blob/master/src/sounddevice.py)).

**Packaging consequence.** `packaging/cadent.spec` gets PortAudio for free today because
the stock sounddevice hook collects `_sounddevice_data` — which is empty on Linux. A Linux
build must either declare a distro dependency (`libportaudio2` on Debian/Ubuntu,
`portaudio` on Arch — both universally packaged) or add a self-built `libportaudio.so.2`
to `binaries=`. If bundling, do **not** let PyInstaller drag in `libasound.so.2` alongside
it: alsa-lib dlopens host-side plugin modules (including PipeWire's `pcm_pipewire.c`
plugin, §2) against the host's ALSA config, and a bundled libasound of a different
version breaks that plugin loading. Preferring the distro's libportaudio sidesteps the
whole class of problem.

## 2. Which host API PortAudio lands on: ALSA, then PipeWire's ALSA plugin routes it

Every distro binary in the field is a v19.6/19.7 build with **ALSA + JACK** compiled in:

- Arch `portaudio 1:19.7.0-4` depends on `alsa-lib` and `libjack.so`
  ([Arch package](https://archlinux.org/packages/extra/x86_64/portaudio/)).
- Ubuntu 24.04 `libportaudio2 19.6.0-1.2build3` depends on `libasound2t64` and
  `libjack-jackd2-0`, and **not** libpulse
  ([Ubuntu package](https://packages.ubuntu.com/noble/libportaudio2)).

PortAudio *has* grown a native PulseAudio host API, but only on master — the README
[lists PulseAudio among host APIs](https://github.com/PortAudio/portaudio), while the
[last tagged release is v19.7.0 (April 2021)](https://github.com/PortAudio/portaudio/releases),
which predates it (its notes mention only JACK fixes "that allows use on Linux systems
with PipeWire"). So in practice PortAudio opens its **ALSA host API**, and its default
device is the ALSA PCM literally named `default` (§3).

On PipeWire-default distros that `default` PCM is not hardware: the **pipewire-alsa**
plugin ("for ALSA applications to output via PipeWire",
[Ubuntu package](https://packages.ubuntu.com/noble/pipewire-alsa);
[pcm_pipewire.c](https://github.com/PipeWire/pipewire/blob/master/pipewire-alsa/alsa-plugins/pcm_pipewire.c))
installs an ALSA config (`99-pipewire-default.conf`) that makes `default` route into the
PipeWire graph. PulseAudio-built clients are covered the same way by pipewire-pulse,
"a complete PulseAudio server on top of PipeWire"
([PipeWire docs](https://docs.pipewire.org/page_module_protocol_pulse.html)).

The targets are all in this shape: Ubuntu made PipeWire the default audio server in
22.10 ([Phoronix](https://www.phoronix.com/news/Ubuntu-22.10-PipeWire),
[OMG! Ubuntu](https://www.omgubuntu.co.uk/2022/05/ubuntu-22-10-makes-pipewire-default)),
SteamOS 3 uses PipeWire with WirePlumber
([Steam Deck audio discussion](https://steamcommunity.com/app/1675200/discussions/1/4357873056145016996/)),
and Arch/CachyOS installs ship pipewire + pipewire-alsa as the standard stack
([Arch Wiki: PipeWire](https://wiki.archlinux.org/title/PipeWire)).

**Conclusion: the path is PortAudio → ALSA host API → pipewire-alsa `default` PCM →
PipeWire.** Capture works unmodified; 16 kHz mono float32 is fine because the plugin
(like dmix/pulse before it) resamples/remixes to the graph rate.

## 3. Default-device semantics and device disappearance

`sd.InputStream(device=None)` asks `Pa_GetDefaultInputDevice()`
([sounddevice source](https://github.com/spatialaudio/python-sounddevice/blob/master/src/sounddevice.py)),
and PortAudio's ALSA backend marks the PCM named `default` as the default device —
`BuildDeviceList()` makes a device default "if there isn't already one or it is the
ALSA 'default' device"
([pa_linux_alsa.c](https://github.com/PortAudio/portaudio/blob/master/src/hostapi/alsa/pa_linux_alsa.c)).
Under pipewire-alsa that PCM is a virtual device whose real capture target is
WirePlumber's **default source** — chosen by priority, stored when the user changes it
in pavucontrol/settings, restored across replug, and streams targeting "default" are
**moved live** when the default changes
([WirePlumber default-nodes scripts](https://pipewire.pages.freedesktop.org/wireplumber/scripting/existing_scripts/default_nodes.html),
[WirePlumber settings](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/settings.html)).

Three decision-relevant consequences:

1. **`device=None` is the robust choice on Linux.** It tracks the user's system-wide
   mic selection, and a mid-capture unplug does not kill the stream — the server
   reroutes to the new default source. The `Recorder`'s existing behavior (configured
   device by name, fall back to default on open failure) ports as-is.
2. **The device list is frozen.** PortAudio's list "is not automatically updated when
   hardware devices are connected or disconnected"; `Pa_RefreshDeviceList()` exists only
   on the unmerged hotplug branch
   ([PortAudio HotPlug wiki](https://github.com/PortAudio/portaudio/wiki/HotPlug)).
   So `Recorder.list_devices()` (the wizard's mic picker) shows a snapshot from process
   start; refreshing requires sounddevice's private `_terminate()`/`_initialize()`
   dance. Mitigating this matters less under PipeWire because the enumerated ALSA
   view is mostly virtual PCMs — the *stable* row is `default`.
3. **A vanished named device fails at open, which the code already catches.** A stale
   name no longer in the frozen list raises from sounddevice's device-ID lookup; a
   listed-but-gone device fails in `Pa_OpenStream` with an ALSA error
   (`paUnanticipatedHostError` / `paDeviceUnavailable`,
   [pa_linux_alsa.c](https://github.com/PortAudio/portaudio/blob/master/src/hostapi/alsa/pa_linux_alsa.c)).
   Both land in the `except Exception` fallback in `Recorder.start` / `LevelMonitor._listen`.

## 4. Mic permission reality: none on bare desktop; Flatpak is static grant, no prompt

**Bare desktop: no permission exists.** Any process in the user's session may open the
default source; there is no TCC-like gate and, crucially, no mode where a denied grant
silently records zeros. Failures are *exceptions* (no libportaudio, no server socket),
never fake silence.

**Flatpak:** microphone access is the static manifest permission `--socket=pulseaudio` —
"Access to PulseAudio. It includes sound input (mic), sound output/playback, MIDI and
ALSA sound devices in /dev/snd" — granted at install, with **no runtime prompt**
([Flatpak sandbox permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html)).
The portal that could gate it is not usable by apps: `org.freedesktop.portal.Device.AccessDevice`
("microphone", "speakers", "camera") "is not directly accessible to applications inside
the sandbox"
([org.freedesktop.portal.Device.xml](https://sources.debian.org/src/xdg-desktop-portal/1.8.1-1/data/org.freedesktop.portal.Device.xml/)),
and a real Audio portal is still a design discussion, which itself notes that today
"most apps [have] the pipewire socket always enabled as a static permission"
([xdg-desktop-portal#1129](https://github.com/flatpak/xdg-desktop-portal/issues/1129),
[discussion #1142](https://github.com/flatpak/xdg-desktop-portal/discussions/1142)).
A Flatpak *without* the socket fails to connect to the server — again an exception at
stream open, not zeros.

## 5. What `Capabilities.mic_permission_hint` should say on Linux: `None`

The field exists for exactly one failure shape (base.py §7.7): a permission whose denial
**raises nothing and records zeros** — macOS TCC. Linux has no such permission (§4). An
all-zero capture on Linux means what it means on Windows: a muted or wrong source —
silence is just silence. A hint blaming permissions would send users hunting for a
settings pane that does not exist.

`cadent/platform/fallback.py` already ships `mic_permission_hint=None` and
`tests/test_platform.py` asserts it — this research confirms that value rather than
changing it. If Cadent ever ships as a Flatpak, the story still holds: the socket
grant is install-time, and its absence is an open-failure, so the zero-buffer
heuristic never fires for permission reasons.

## 6. Recommendations

1. **Keep `mic_permission_hint=None`** in the Linux capability table (§5).
2. **Keep `device=None` as the default** and the existing open-failure fallback; under
   PipeWire it follows the user's mic choice and survives unplug mid-capture (§3).
3. **Packaging:** depend on the distro PortAudio (`libportaudio2` / `portaudio`) rather
   than bundling; if bundling ever becomes necessary, exclude `libasound.so.2` from the
   collect so the host's pipewire-alsa plugin still loads (§1).
4. **Non-goal:** a native PulseAudio/PipeWire host API is not reachable through any
   released PortAudio; do not condition on it. The ALSA path *is* the PipeWire path (§2).
5. If a Flatpak target is ever added, request `--socket=pulseaudio` in the manifest and
   expect no prompt; revisit only if the Audio portal proposal ships (§4).

## Sources

- sounddevice installation (Linux needs system PortAudio) — https://python-sounddevice.readthedocs.io/en/latest/installation.html
- sounddevice PyPI file list (no manylinux wheel) — https://pypi.org/pypi/sounddevice/json
- sounddevice source (library lookup, device=None, name matching) — https://github.com/spatialaudio/python-sounddevice/blob/master/src/sounddevice.py
- portaudio-binaries build workflow (v19.7.0; mac/win only) — https://github.com/spatialaudio/portaudio-binaries/blob/master/.github/workflows/build-libs.yml
- PortAudio README (host API list incl. PulseAudio on master) — https://github.com/PortAudio/portaudio
- PortAudio releases (v19.7.0 latest tagged) — https://github.com/PortAudio/portaudio/releases
- pa_linux_alsa.c ("default" PCM as default device; open errors) — https://github.com/PortAudio/portaudio/blob/master/src/hostapi/alsa/pa_linux_alsa.c
- PortAudio HotPlug wiki (frozen device list) — https://github.com/PortAudio/portaudio/wiki/HotPlug
- PipeWire pulse server module — https://docs.pipewire.org/page_module_protocol_pulse.html
- pipewire-alsa plugin source — https://github.com/PipeWire/pipewire/blob/master/pipewire-alsa/alsa-plugins/pcm_pipewire.c
- pipewire-alsa package (Ubuntu noble) — https://packages.ubuntu.com/noble/pipewire-alsa
- libportaudio2 package (Ubuntu noble; ALSA+JACK deps) — https://packages.ubuntu.com/noble/libportaudio2
- portaudio package (Arch; ALSA+JACK deps) — https://archlinux.org/packages/extra/x86_64/portaudio/
- WirePlumber default-nodes scripts — https://pipewire.pages.freedesktop.org/wireplumber/scripting/existing_scripts/default_nodes.html
- WirePlumber well-known settings (stream moving/restoring) — https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/settings.html
- Ubuntu 22.10 PipeWire default (Phoronix) — https://www.phoronix.com/news/Ubuntu-22.10-PipeWire
- Arch Wiki: PipeWire — https://wiki.archlinux.org/title/PipeWire
- Flatpak sandbox permissions (--socket=pulseaudio) — https://docs.flatpak.org/en/latest/sandbox-permissions.html
- org.freedesktop.portal.Device (not app-accessible) — https://sources.debian.org/src/xdg-desktop-portal/1.8.1-1/data/org.freedesktop.portal.Device.xml/
- Audio portal proposal — https://github.com/flatpak/xdg-desktop-portal/issues/1129 and https://github.com/flatpak/xdg-desktop-portal/discussions/1142
