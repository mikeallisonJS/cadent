# Spec: Cadent M6 — Linux port (x86_64, X11 and Wayland)

Status: ready-for-agent
Source map: [#11](https://github.com/mikeallisonJS/cadent/issues/11) · ADRs: [`docs/adr/0007`](../adr/0007-linux-injection-follows-the-support-tier.md)–[`0014`](../adr/0014-linux-desktop-fills-tray-overlay-portal-settings.md) · Research: [`linux-input`](../research/linux-input.md), [`linux-desktop-integration`](../research/linux-desktop-integration.md), [`linux-gpu-runtimes`](../research/linux-gpu-runtimes.md), [`linux-packaging`](../research/linux-packaging.md), [`linux-audio`](../research/linux-audio.md), [`linux-parakeet-cpu-bench`](../research/linux-parakeet-cpu-bench.md)

> Every section is the settlement of a closed wayfinder ticket on map #11. The ticket and its ADR are the reasoning; this spec is the instruction. Section headings carry their sources. The template is [`m5-macos-port-spec.md`](./m5-macos-port-spec.md); the seam it fills is ADR 0005's, and `cadent/platform/fallback.py` is the inert adapter a Linux package replaces.

---

## Scope

Cadent runs on x86_64 Linux with the core loop intact — hotkey → record → transcribe → cleanup → inject — plus tray, Settings and wizard, on both session types, from a shipped tarball, AppImage or AUR package. What the user gets is a **support tier** (§1) decided once at startup: **Whole** on X11, **Portal** on Wayland under Plasma / wlroots / SteamOS desktop, **Reduced** on GNOME Wayland. Every feature beyond the loop is an explicit `Capabilities` decision (§2.4).

**Validation targets**: Arch, CachyOS, SteamOS (desktop mode), Ubuntu GNOME, KDE Plasma — x86_64, both session types where a target offers them. Only tiers carry promises; distros never do.

**Not in scope** (map #11's Out-of-scope list): non-x86_64 Linux; SteamOS **gaming mode** (Gamescope has no portal backend and no tray — no tier exists there); a layer-shell overlay on Plasma/SteamOS Wayland (compiled `cadent-overlay` helper — a follow-on; `Capabilities.overlay = "anchored"` is reserved for it); the Parakeet CUDA pack edition on Windows; a Flatpak (deferred until portal-backed input adapters exist — ADR 0011); raw evdev/uinput capture (a named non-goal for v1, never a silent fallback — ADR 0008); running the Wayland tiers under XWayland (`QT_QPA_PLATFORM=xcb`) to recover anything.

Ground truth while implementing: `CONTEXT.md` (glossary: *Support tier*, *Permission preflight*, *GPU support pack*, *App picker* carry the Linux wording), ADR 0005 (the seam), `cadent/platform/{base,win32,darwin,fallback}.py`, `scripts/build.py` (`LOAD_BEARING`).

---

# 1. The support tier model (#17, amended by #18/#20 · glossary `Support tier`)

## 1.1 Three tiers, decided at startup, one build

`current()` on Linux probes `XDG_SESSION_TYPE` + `XDG_CURRENT_DESKTOP` **once** and fills the same frozen `Capabilities`; nothing is re-probed after start. A new fact **`support_tier`** names the tier (`None` on win32/darwin) and **`support_tier_summary`** (§9.4) is the finished sentence Settings shows.

| Tier | Where | Core-loop promise (as amended) |
| --- | --- | --- |
| **Whole** | X11, any desktop | Windows parity: shipped Ctrl+Super chord, type-first with paste fallback, auto-learn, windowed overlay, per-app overrides, no permission preflight |
| **Portal** | Wayland on Plasma / wlroots / SteamOS desktop | Loop intact, portal-shaped: keysym-bearing default chord, paste-first, no auto-learn, overrides keyed on desktop-file id, permission preflight = portal grant, **no overlay in v1** (ADR 0014) |
| **Reduced** | GNOME Wayland, run natively | The same paste-first portal ladder (Clipboard portal rides the RemoteDesktop session — ADR 0007), no overlay, no per-app overrides (`FocusedApp.name()` is `"unknown"`), permission preflight = portal grant |

**Portal and Reduced now differ only in per-app overrides.**

## 1.2 Target × session → tier (the validation matrix)

| Target | X11 session | Wayland session |
| --- | --- | --- |
| Arch / CachyOS (any DE the dev installs) | Whole | Portal (Plasma, Sway/Hyprland) or Reduced (GNOME) |
| SteamOS desktop mode (Plasma) | Whole (developer toggle) | Portal |
| Ubuntu GNOME | Whole (where still offered) | Reduced (default) |
| KDE Plasma | Whole | Portal |

## 1.3 A tier's name is not a promise its bus can keep

Session type and desktop fix the tier; then the Wayland tiers probe the portals. Where an interface is missing, the affected rung is **dropped for that run and the copy says so** — never a fourth tier, never a forced XWayland fallback:

- No `org.freedesktop.portal.GlobalShortcuts` (stock Sway — xdg-desktop-portal-wlr ships Screenshot/ScreenCast only): Cadent starts with the hotkey **disarmed** and raises the `hotkey-unavailable` tray fault (§9.3).
- No Clipboard portal (older backend) on Reduced: rungs collapse to `("type",)` for the run.
- A wlroots compositor with neither `zwp_virtual_keyboard_v1` nor a RemoteDesktop backend is unsupported.

---

# 2. The platform seam on Linux (#27 · ADR 0013, over ADR 0005)

## 2.1 Package shape — a subpackage from day one

`cadent/platform/linux/` (ADR 0005 allowed the split "only if one bloats"; Linux carries X11, three portals, XDG entries and the tray door):

- `portal.py` — the **one jeepney connection**, its `cadent-portal` receive thread (`threading.DBusRouter`), the `Request`/`Session` handle plumbing (subscribe to `Response` on `/org/freedesktop/portal/desktop/request/<unique-name-mangled>/<token>` *before* sending; check the returned handle), hand-built messages with explicit signatures for the four interfaces (GlobalShortcuts, RemoteDesktop, Clipboard, Settings). No generated proxies.
- Seam adapters take the connection **in their constructors** — tests drive them against a fake portal with no bus; jeepney's I/O-free core lets message building be tested purely.
- `__init__.py` — tier detection (§1.1) and assembly of the eight adapters.

**Transport is `jeepney`**, pure Python, `sys_platform == 'linux'` marker in `pyproject.toml`; no native library, no `LOAD_BEARING` row. Maintenance risk (one maintainer, slow cadence) is accepted knowingly; the slice used is small enough to vendor.

**New rule, enforced: `cadent/platform/` never imports the GUI toolkit.** QtDBus is out (PYSIDE-2547 drops `a{sv}` signals — every portal `Response` is one; needs a Qt loop; loads host libdbus). The import-safety test grows an assertion that `PySide6` is absent from `cadent/platform/`; win32/darwin change nothing but are now bound by it.

## 2.2 Threading and timeouts

Seams **stay synchronous**; the adapter blocks the calling thread (typing/paste already run on the `_process` worker). Signals fan out to seam callbacks **from the portal thread** — the same "hook's own thread; callers marshal" contract `HotkeyTap.start` and `watch_tray_ink` document. Two timeout classes, named in the adapter:

- **bounded** — every D-Bus method reply (`CreateSession`, `ListShortcuts`, `NotifyKeyboardKeysym`, `SetSelection`, `ReadAll`) waits ≤ 5 s; on timeout the rung fails and dictation falls through to inserting the raw transcript.
- **consent** — `BindShortcuts` and `RemoteDesktop.Start` raise a human-answered dialog; **never awaited**, fired from `request_permission()` (fire-and-forget), outcome on the `Response` signal.

A blocking call from the GUI thread is a bug — **logged, not asserted** (an assert can kill the tray). `xdg-desktop-portal` restarting closes sessions silently: the adapter re-creates them **once** on `Closed` using the persisted restore token, and on failure stops and lets `permission_granted()` go false — no silent retry loop.

## 2.3 The eight seams — Linux fills by tier

| Seam | Whole (X11) | Portal (Wayland: Plasma / wlroots / SteamOS) | Reduced (GNOME Wayland) |
| --- | --- | --- | --- |
| **KeyboardOutput** | XTEST typing with a scratch keycode temporarily remapped for characters the layout lacks; XTEST chord for Ctrl+V; `send_mask_key()` no-op | Probe `zwp_virtual_keyboard_v1` (wlroots: client keymap, full unicode, no dialog) else RemoteDesktop `NotifyKeyboardKeysym` on a restore-token session; the paste chord rides the same mechanism | RemoteDesktop keysym path |
| **Clipboard** | X selection ownership; XFixes `SelectionNotify` counter as `sequence_number()` | `ext-data-control-v1` where offered (KWin, wlroots), else Clipboard portal on the RemoteDesktop session (`RequestClipboard` before `Start`, `SetSelection`, `SelectionOwnerChanged` as the counter) | Clipboard portal on the RemoteDesktop session; absent → paste rung dropped for the run |
| **FocusedApp** | identity via `WM_CLASS` → desktop-file id (§5); rect from X11; `injection_blocked()` → `None`; `permission_granted()` → `True` | identity via plasma-window-management (KDE-only, accepted); `injection_blocked()` → `None`; `permission_granted()` = shortcut bound **and** input session live | `name()` → `"unknown"`; `permission_granted()` as Portal |
| **HotkeyTap** | pynput XRecord, press+release, sided modifiers, no grant; `start()` ignores the chords argument | GlobalShortcuts portal: bind both chords in one call, synthesize parsed keysym events from `Activated`/`Deactivated` | as Portal |
| **HardwareProbe** | `libcuda.so.1` loadable, driver-API VRAM read, `/proc/cpuinfo`, **new driver-CUDA-version fill** (`cuDriverGetVersion()`); `dx12_gpu_present`/`metal_gpu_present` False; no Vulkan probe | same | same |
| **Autostart** | XDG autostart entry, `$APPIMAGE`-aware `Exec=`/`TryExec=`, in-place path healing (§8.4) | same | same |
| **SingleInstance** | `fcntl.flock` on `user_data_dir("Cadent")/cadent.lock` — darwin's, **duplicated, not shared** (one file per platform); second launch opens Settings when tray-less (§10.2) | same | same |
| **DesktopEnv** | one Settings-portal `ReadAll` snapshot + `SettingChanged` watch (§10.4); `request_permission()` no-op; `open_path` = `xdg-open` | `request_permission()` issues `CreateSession`/`BindShortcuts` + `RemoteDesktop.Start` | same |

**Seam signature changes** (all platforms):

- `Capabilities.permission_preflight: str | None` → **`Capabilities.permission: PermissionPreflight | None`** — a frozen dataclass: `name` + `banner`, `wizard_body`, `action_label`, `waiting`, `granted` (ADR 0012). Gating stays a truthiness check at the same four call sites; the darwin copy in `settings_ui/window.py` (`NEEDS_PERMISSION`) and `wizard.py` moves into the darwin adapter.
- `DesktopEnv.open_permission_settings()` → **`request_permission()`** — darwin deep-links, Linux makes the portal request; fire-and-forget on both.
- **`HotkeyTap.start(on_event, chords=...)`** learns which chords to bind; X11/win32/darwin ignore it. `chord.py` and `hotkey.py` are unchanged.
- **`FocusedApp.running_apps()` / `display_name()`** semantics on Linux are installed-apps / desktop-file `Name=` (§5.2) — the docstrings widen, the shape does not.

## 2.4 The Capabilities table — Linux columns

`Capabilities` remains the only thing UI, settings and wizard code read. Fields shared by all three tiers are written once; tier-specific ones are split.

| Field | Whole (X11) | Portal | Reduced | Source |
| --- | --- | --- | --- | --- |
| `keycode_table` | one keysym `LINUX_KEYCODES` table (`ord_fallback=False`) | same | same | ADR 0008 |
| `default_injection_strategy` | `"type"` | `"clipboard"` (paste) | `"clipboard"` | ADR 0007 |
| `injection_rungs` | `("type", "paste")` | `("paste", "type")`, no auto fall-through | `("paste", "type")`; `("type",)` when the Clipboard portal is absent | ADR 0007 |
| `paste_chord` | `ctrl+v` | `ctrl+v` | `ctrl+v` | ADR 0007 |
| `default_overrides` / reasons | empty (Restore-defaults inert) | empty | empty | ADR 0007 |
| `auto_learn_overrides` | `True` (XTEST errors are detectable) | `False` | `False` | ADR 0007 |
| **`per_app_overrides`** (new; True on win32/darwin) | `True` | `True` | `False` (pane stays editable, with a note) | ADR 0009 |
| `stt_runtimes` | `("auto", "cuda", "cpu")` for **both** engines | same | same | ADR 0010 |
| `gpu_only_engines` | empty | empty | empty | ADR 0010, #28 |
| `show_runtime_combo` | `True` | `True` | `True` | ADR 0010 |
| `gpu_pack_available` | `True` — one surface, two editions (§6.2) | same | same | ADR 0010 |
| **`permission`** (was `permission_preflight`) | `None` | `PermissionPreflight(name="portal", …)` — one value covering both grants | same as Portal | ADR 0008/0012 |
| `autostart_label` | `"Start at login"` | same | same | ADR 0011 |
| `app_identity_placeholder` | `org.mozilla.firefox` | same | same | ADR 0009 |
| `app_picker` | `True` — installed `.desktop` apps | same | same | ADR 0009 |
| `mic_permission_hint` | `None` | `None` | `None` | #24 |
| `tray_click_toggles_pause` | `False` | `False` | `False` | ADR 0014 |
| `modifier_captions` | `"<cmd>"` → "Super" | same | same | ADR 0008 |
| `tray_icon_painted_by_os` | `False` | `False` | `False` | ADR 0014 |
| `theme_subtitle` | "Follows your desktop's colour scheme" | same | same | ADR 0014 |
| `high_contrast_reason` | "Your desktop is set to higher contrast, so Cadent is following your system colours" | same | same | ADR 0014 |
| **`overlay`** (new three-valued; `"windowed"` on win32/darwin) | `"windowed"` | `None` | `None` (`"anchored"` reserved) | ADR 0014 |
| **`support_tier`** (new; `None` off-Linux) | `"whole"` | `"portal"` | `"reduced"` | #17 |
| **`support_tier_summary`** (new) | adapter-built sentence (§9.4) | same | same | ADR 0012 |
| **`default_combo` / `default_cleanup_combo`** (new platform facts) | `<ctrl>+<cmd>` / `<ctrl>+<shift>+<alt>` (shipped) | `<ctrl>+<cmd>+space` / `<ctrl>+<cmd>+c` | same as Portal | ADR 0008 |

(Exact names are the implementer's to bikeshed; the *set* is fixed. Any new platform fact goes here, never in an `if sys.platform` branch.)

## 2.5 Import rule and CI

ADR 0005's rule now covers `Xlib`, `jeepney`, and anything reading `/dev` or the portals: only inside `cadent/platform/linux/`. **`test.yml` grows an `ubuntu-24.04` leg with `QT_QPA_PLATFORM=offscreen`** — imports, keycode table, pure logic, fakes at the seams, jeepney message building. No bus, no portal, no compositor: every portal/X11 behaviour is HITL on the Arch/CachyOS boxes (§12).

---

# 3. Text injection on Linux (#18 · ADR 0007)

The ladder follows the tier, mirroring ADR 0001's Windows-vs-macOS split. Rung order/fall-through/auto-learn stay in `inject.py`; the tier supplies data.

- **Whole types.** `("type", "paste")`, `ctrl+v`. Typing = XTEST with a scratch keycode remapped for out-of-layout characters; an XTEST request error or an exhausted scratch keycode **raises** — a detectable failure, so `auto_learn_overrides=True`. Paste = own the CLIPBOARD selection, post Ctrl+V, restore guarded by the XFixes `SelectionNotify` count.
- **Portal and Reduced paste.** `("paste", "type")`, **no automatic paste→type fall-through** (setting a selection cannot meaningfully fail — darwin's shape), `auto_learn_overrides=False` (portal typing reports nothing about consumption). "Type it" is an explicit per-app override, never reached automatically. Mechanism chosen per compositor at startup *inside* the tier (§2.3).
- **Suppression is a state gate, not a per-event flag.** XTEST fakes arrive with `send_event` false; the tap drops/flags events while `KeyboardOutput` is mid-send. Same gate on the Wayland tiers even though portal input does not loop back.
- **No secure-input analogue**: `injection_blocked()` → `None` on every tier. Grant presence is `permission_granted()`'s job.
- **Empty default override table.** Mainstream terminals bind paste to Ctrl+Shift+V, so overrides are coming — but no seeded rule without a measured sweep (§12).

---

# 4. Hotkey capture and the Linux keycode table (#19 · ADR 0008)

- **Whole**: pynput XRecord, listen-only, press+release, sided modifiers, no grant. Health: the X connection fails loudly with an exception — nothing to poll.
- **Portal/Reduced**: `org.freedesktop.portal.GlobalShortcuts` — `CreateSession`, `BindShortcuts` (consent), `Activated`/`Deactivated`. **`permission = "portal"` covers both grants** (shortcut binding + RemoteDesktop input session); `permission_granted()` is true only when both are live. **Health check = `ListShortcuts` reporting our shortcut plus a live input session** — never the listener's own flag (ADR 0002's rule). Missing interface → hotkey disarmed + `hotkey-unavailable` fault (§1.3).
- **One keysym `LINUX_KEYCODES` table** for all three tiers (no evdev table); `ord_fallback=False`; captions say "Super". `chord.parse_combo(combo, table=...)` as today.
- **Wayland defaults differ**: the freedesktop shortcuts grammar needs a keysym, so `<ctrl>+<cmd>` and `<ctrl>+<shift>+<alt>` are unbindable there; the Wayland tiers default to `<ctrl>+<cmd>+space` / `<ctrl>+<cmd>+c`, carried as platform facts, not `Config` literals. A `config.json` written under X11 and opened under Wayland is **not rewritten** — the tier default binds for that run and the copy says why.
- **The compositor owns the binding.** `preferred_trigger` is a suggestion; Settings shows what `ListShortcuts` reports rather than pretending the text field is authoritative (Hotkeys pane note: "your desktop owns this shortcut").
- **`HotkeyTap.start` learns the chords**; the portal adapter binds both in one call and synthesizes the parsed keysym events its shortcut ids stand for — hold, toggle, `min_hold_ms`, cleanup tap all work unchanged. One behaviour cannot cross: "any non-chord keydown mid-hold cancels" needs a whole-keyboard view the Wayland tiers lack.
- **`send_mask_key()` is a no-op on Linux for v1**; whether bare Super pops a launcher mid-chord is a hardware item (§12).
- **evdev/uinput is a named non-goal** with its price recorded (ADR 0008: `input` group = session-wide keylog grant, udev rule uninstallable from a tarball/AppImage, layout-blind typing, second capture path to validate).

---

# 5. App identity and overrides on Linux (#21 · ADR 0009)

## 5.1 Identity — the desktop-file id, on every tier

`AppOverride.process` / History `app_name` hold the **freedesktop desktop-file id**: xdg-shell `app_id` on Wayland; on X11 resolved from `WM_CLASS` (a `.desktop` whose filename stem or `StartupWMClass=` matches, case-insensitive, across `$XDG_DATA_DIRS/applications` and Flatpak exports). Fallback: executable basename (`_NET_WM_PID` on X11, toplevel pid on Plasma Wayland) → `"unknown"`. One key so overrides survive the X11↔Wayland flip on the same machine. `FocusedApp.name()` per tier as in §2.3.

## 5.2 The pane bridges to humans

- **`app_picker=True` on every tier, listing installed apps**: `running_apps()` returns `(Name, id)` for every `Type=Application` `.desktop` file that is neither `NoDisplay` nor `Hidden` — pure XDG lookup, identical on all tiers. Free text stays accepted.
- **`display_name(identity)`** resolves the localized `.desktop` `Name=` whether or not the app runs — a history row for a closed app still reads "Firefox".
- **`per_app_overrides=False` on Reduced**: the pane stays **editable** (config is per-machine; a Plasma-X11 session uses the same rows) with a one-line note that overrides and auto-learn do not apply in this session (copy per §9).

---

# 6. Speech & cleanup runtimes on Linux (#22 · ADR 0010, numbers from #28)

## 6.1 Speech: three rungs, both engines

`stt_runtimes = ("auto", "cuda", "cpu")` for faster-whisper **and** Parakeet; `show_runtime_combo=True`; `gpu_only_engines` **empty** (confirmed by data — §6.3). `landed_on` derives from `session.get_providers()` (#137, both platforms). No ROCm rung (the EP is gone upstream); AMD/Intel get their acceleration in cleanup only.

## 6.2 The GPU support pack — one surface, two editions

Same wizard page and tray items, engine-keyed; `should_offer` keeps its engine test as a real selector. Extracted from PyPI `nvidia-*` wheels into `~/.local/share/cadent/cuda` (XDG data dir), disclosed, user-initiated:

- **faster-whisper edition**: `libcublas.so.12` + `libcublasLt.so.12` from `nvidia-cublas-cu12` (~600 MB; ctranslate2 ≥ 4.6.3 needs no cuDNN).
- **Parakeet edition**: the CUDA-13 userspace ORT 1.28's CUDA provider dlopens (`libcudart.so.13`, `libcublas/Lt.so.13`, `libnvrtc.so.13`, `libcufft.so.12` + `libnvJitLink.so.13`, `libcurand.so.10`, cuDNN 9 lib dir; ~1.3 GB compressed) — requires **R580+ driver** (`cuDriverGetVersion() >= 13000` off `libcuda.so.1`). The Linux build ships **`onnxruntime-gpu`** (250 MB) instead of `onnxruntime`. Pre-580 driver → edition not offered; the row says to update the NVIDIA driver to 580+.
- **Activation is a `ctypes.CDLL` preload with the default `RTLD_LOCAL`** before the engine first touches CUDA — not a PATH prepend (`LD_LIBRARY_PATH` is fixed at exec), **not `RTLD_GLOBAL`** (a GLOBAL cuBLAS 12 preload lets unversioned symbols shadow cuBLAS 13's — this corrects the research doc).

## 6.3 Cleanup and the measured constants

- Cleanup introduces nothing Linux-specific: widen the pinned Vulkan-index `llama-cpp-python` source marker to Linux (manylinux x86_64 wheel exists at the Windows version); the distro Vulkan loader is the only system need (missing `libvulkan.so.1` → import `OSError` → ladder lands `cpu`, silently); the landed rung stays `gpu`/`cpu`.
- **Recommended chip**: Parakeet v2 on NVIDIA ≥ 4 GB with a CUDA-13-capable driver; **on the non-NVIDIA branch Parakeet v2 at `physical_cores >= 4 and ram_gb >= 8`** (bench: 0.66 s median on a 1.4–57 s set, ~0.4 s for a 10 s dictation, 0.84 s at 4 emulated cores, 1.14 s at 2 — faster than `distil-small.en` at every length, saturates at 8 threads; x86 proxy on the Zen 5 dev box, Linux confirmation checklist in `docs/research/linux-parakeet-cpu-bench.md`, script `scripts/bench_cpu_stt.py`); else today's Whisper VRAM/CPU rows. **distil-medium.en is never a Linux CPU default.**

---

# 7. Audio capture on Linux (#24, research #16)

**Ports unchanged.** sounddevice → PortAudio's ALSA host API → `pipewire-alsa` `default` PCM → PipeWire on every target; `device=None` default; `Recorder.start`/`LevelMonitor._listen`'s open-failure fallback as-is (WirePlumber moves streams live on unplug — better than Windows). `mic_permission_hint=None` (failures raise, never fake silence); no new copy or capability. Packaging deltas only (§8.5).

---

# 8. Packaging and distribution (#23 · ADR 0011, plus #24's deltas)

## 8.1 Formats

`.tar.zst` + **AppImage** from one `scripts/build_linux.py`, plus a hand-published **`cadent-bin` AUR PKGBUILD** (`packaging/aur/PKGBUILD`, repacking the released tarball). **No Flatpak** (sandbox forecloses uinput and goes deaf on Wayland; its update channel is a capability Cadent has on no OS). SteamOS: an AppImage in `/home` survives rootfs updates; not in Discover — accepted.

## 8.2 Build workflow and glibc floor

`.github/workflows/build-installer-linux.yml`, **`ubuntu-24.04` pinned by name** (like `macos-14`) → **glibc floor 2.39** ("Ubuntu 24.04 LTS or newer, any rolling distro"). Steps: checkout → setup-uv → `apt-get install -y libportaudio2` → `uv sync --all-extras` → `scripts/build.py --skip-sync` → `scripts/build_linux.py` (emits `Cadent-<version>-x86_64.AppImage` and `cadent-<version>-x86_64.tar.zst`) → upload → attach to release. No signing.

## 8.3 Desktop entry and identity

Nothing installs a desktop entry, so on first run Cadent writes `~/.local/share/applications/com.mikeallisonjs.cadent.desktop` + hicolor 48/128/256 PNGs, idempotently, **skipped where a system-wide entry exists** (AUR stays authoritative). `com.mikeallisonjs.cadent` is the `.desktop` basename, `Icon=`, `StartupWMClass`, and `QGuiApplication::setDesktopFileName()` — the only thing Qt derives the Wayland `app_id` from.

## 8.4 Autostart and SingleInstance

XDG autostart entry, label "Start at login"; `Exec=`/`TryExec=` = `$APPIMAGE` where set else `sys.executable` (an AppImage's `sys.executable` is an ephemeral `/tmp/.mount_*`); `TryExec` disables a deleted tarball/moved AppImage at login; a stale `Exec=` is **rewritten in place** by the adapter. SingleInstance = darwin's `flock`, duplicated (§2.3).

## 8.5 `LOAD_BEARING["linux"]` and the excluded-on-purpose twin

Eleven glob rows: `Cadent`, `*/libctranslate2*.so*`, `*/llama_cpp/lib/libllama.so`, `*/llama_cpp/lib/libggml-vulkan.so`, `*/faster_whisper/assets/silero_vad_v6.onnx`, `*/onnx_asr/preprocessors/data/nemo128.onnx`, `*/onnxruntime/capi/libonnxruntime_providers_cuda.so` (the GPU wheel, not the CPU one), `*/platforms/libqxcb.so`, `*/platforms/libqwayland-generic.so` (either missing = a dead session type), `*/wayland-shell-integration/libxdg-shell.so` (without it: Wayland, but no window), `*/libportaudio.so*` (staged by the build). `libvulkan.so.1` stays out (distro's). jeepney needs no row.

**Excluded on purpose** (record beside `LOAD_BEARING`, reason inline, so a refactor cannot re-add it): **`libasound.so.2`** — alsa-lib dlopens host plugins (incl. `pcm_pipewire`) against the host config; a bundled copy breaks routing silently. `libjack.so.0` **rides along** (inert without a JACK server; excluding it makes `libportaudio` fail to load). Host requirement: `libasound2`. AUR: `depends=(alsa-lib)`, `optdepends=('pipewire-alsa: route the microphone through PipeWire')`.

## 8.6 CI test leg

`test.yml` gains `ubuntu-24.04` (§2.5) — this milestone.

---

# 9. Permission surface and support-tier copy (#26 · ADR 0012)

## 9.1 The grant is the dialog

Linux's Wayland tiers have no settings page: `request_permission()` issues `CreateSession`/`BindShortcuts` + `RemoteDesktop.Start`; the outcome arrives on the portal signal path. Never blocks; **no auto-retry after a denial** — the button stays enabled, the user asks again.

## 9.2 Surfaces

- **One wizard step for both portals** (Whole has none, like Windows), fixed at construction, never a gate, live re-check.
- **Settings banner** while `permission_granted()` is false.
- **The grant poll moves to `app.py`**: one 2 s timer where `caps.permission is not None`, owning the fault; banner and wizard page read it. (Deliberately changes darwin: today granting Accessibility with no window open leaves the tray green while the loop is dead.)
- One Wayland wording for Portal and Reduced: says "your desktop" (never GNOME/Plasma/portal), "the prompts" without a count.

## 9.3 Two new tray faults, mutually exclusive

`hotkey-unavailable` (interface absent — stock Sway) and `permission-needed` (interface present, grant missing; darwin gains this too). Never both. `status_line()`'s `setup-unfinished` short-circuit subsumes both during first run.

## 9.4 Restore token and the tier line

- Restore token in **`DATA_DIR/portal-tokens.json`**, not `config.json` (an opaque credential in a hand-edited file; "Back it up and start fresh" would revoke the grant). Lost/rejected token re-asks silently; only a denial raises the banner.
- **Settings ▸ General row on every Linux session** (absent where `support_tier` is None), built by the adapter as `support_tier_summary` — never assembled by cross-platform UI. Tier word followed by its consequence:
  - Whole — "X11 session on KDE Plasma — Whole support: everything Cadent does works here."
  - Portal — "Wayland session on KDE Plasma — Portal support: your desktop owns the hotkey, Cadent won't learn on its own which apps need pasting, and there's no overlay."
  - Reduced — "Wayland session on GNOME — Reduced support: no overlay, no per-app overrides."
  Desktop name from a known-name map; on no match the desktop clause is dropped (never raw `XDG_CURRENT_DESKTOP`). The wizard's Done page shows the same line **only on Portal/Reduced**. Detail lives in local pane notes (Hotkeys: "your desktop owns this shortcut"; Overrides: "not applied in this session" / "pasting isn't available in this session").

---

# 10. Tray, overlay, and DesktopEnv fills (#20 · ADR 0014, research #13)

## 10.1 Tray ink

`tray_icon_painted_by_os=False`; `tray_ink()` keyed desktop-then-scheme from the Settings portal `org.freedesktop.appearance color-scheme`: **GNOME → `#ffffff` always**; else dark (1) → `#ffffff`, light (2) → `#000000`, no-preference (0) → `#ffffff`. Portal `contrast` never changes the ink. `watch_tray_ink` = the one `SettingChanged` subscription on the shared jeepney connection, callback on the portal thread. Themed panels are wrong unobservably; monochrome per-state icons in hicolor for Plasma's `IconName` recolouring is named future work.

## 10.2 A tray-less desktop gets a second door

Stock GNOME hosts no SNI until the AppIndicator extension is installed. The icon stays registered; while `isSystemTrayAvailable()` is false, the Settings window carries **Pause and Quit** in a footer, a second launch opens Settings, and a General row says: *"No system tray found, so Pause and Quit live here. On GNOME, the AppIndicator and KStatusNotifierItem Support extension adds one."* **Runtime state, not a `Capabilities` fact** (a host can arrive mid-session).

## 10.3 Overlay and failure feedback

`Capabilities.overlay`: `"windowed"` on Whole (today's `overlay.py`, Move mode included); **`None` on Portal and Reduced** (LayerShellQt from PySide6 foreclosed upstream; compiled helper is out of scope). Move-overlay button and overlay position rows gate on `overlay == "windowed"`. `app.py` constructs the overlay only where the fact says so and otherwise routes `show_failure(...)` to **`tray.message()`** — Qt's D-Bus backend sends `showMessage` to `org.freedesktop.Notifications` unconditionally, so it lands as a desktop notification even on stock GNOME. Failures only, no start/stop chatter, no new `notify` seam; recording-in-progress state on those tiers is the tray icon alone.

## 10.4 One portal snapshot for everything else

Every other `DesktopEnv` read is a field of one Settings-portal `ReadAll` snapshot kept fresh by the same `SettingChanged` subscription: `text_scale_factor` ← `org.gnome.desktop.interface text-scaling-factor` where proxied, else 1.0; `high_contrast` ← `org.freedesktop.appearance contrast == 1`, fallback `org.gnome.desktop.a11y.interface high-contrast`; `animations_enabled` ← `reduced-motion` if present, else GNOME `enable-animations`, else `kdeglobals [KDE] AnimationDurationFactor == 0` → False; `open_path` ← `xdg-open`. No portal → the fallback adapter's answers, no fault. Copy is desktop-independent (§2.4). `tray_click_toggles_pause=False` on every tier (SNI `Activate` opens the menu under GNOME's extension while Qt still emits `Trigger`).

---

# 11. Platform-conditional UI surface (driven by Capabilities)

All read `Capabilities`, never `sys.platform`:

1. **Wizard**: one portal permission step where `permission` is set (Portal/Reduced); GPU-pack page renders when `gpu_pack_available` and discloses the edition/size for the current engine; Done page carries the tier line only on Portal/Reduced.
2. **Settings banner**: permission banner from `permission.banner` while `permission_granted()` is false; polled by `app.py`.
3. **General pane**: `support_tier_summary` row on every Linux session; autostart checkbox from `autostart_label`; the tray-less row when no SNI host is present.
4. **Hotkeys pane**: on the Wayland tiers shows the compositor's bound shortcut from `ListShortcuts`, with the "your desktop owns this shortcut" note; Wayland defaults from the new default-combo facts; captions say "Super".
5. **Speech pane**: runtime combo shown; both engines offer `cuda`; no disabled rows; Recommended chip per §6.3; pack row says "update the NVIDIA driver to 580 or newer" when applicable.
6. **Tray**: pack items present, edition-keyed; two new faults; `message()` carries failures on the overlay-less tiers; click never toggles pause.
7. **Overrides pane**: installed-apps picker + `.desktop` display names, `org.mozilla.firefox` placeholder; on Reduced editable with the "not applied in this session" note; on the Wayland tiers the "pasting isn't available in this session" note when the paste rung was dropped.
8. **Overlay**: constructed only when `overlay == "windowed"`; Move-overlay and position rows gated the same way.
9. **Settings footer**: Pause/Quit while tray-less.
10. **First run**: `.desktop` + hicolor icons written (skipped if a system entry exists).

---

# 12. Real-hardware verification pass (Arch / CachyOS / SteamOS / Ubuntu GNOME / Plasma)

Manual/HITL after the port lands; every portal, X11 and compositor behaviour lives here because the CI leg is headless. Tier × target per §1.2; each item names its tiers.

1. **Whole — bare Super and launchers.** Hold Ctrl then Super on Plasma-X11 and GNOME-X11: does a launcher pop? If so, XTEST-inject a harmless keysym mid-chord (the Windows mask-key trick in another namespace) — not a design change.
2. **Whole — XTEST scratch-keycode typing** across layouts (US, a non-Latin layout, characters outside the layout) and the state-gate suppression during an actual Ctrl+V paste (the posted chord must not re-enter the chord machine).
3. **Portal — GlobalShortcuts on Plasma, SteamOS desktop, Sway/Hyprland**: bind dialog, `Activated`/`Deactivated` press+release, `ListShortcuts` health, unbind-in-compositor → banner; `xdg-desktop-portal` restart → one silent recovery on the restore token, then an honest dead state.
4. **Portal — typing mechanism per compositor**: `zwp_virtual_keyboard_v1` on wlroots (unicode, no dialog), RemoteDesktop keysym on KWin/SteamOS; paste via `ext-data-control` (KWin, wlroots).
5. **Reduced — GNOME Wayland**: RemoteDesktop keysym typing; Clipboard portal riding the same session (`RequestClipboard` before `Start`); an older backend without it → `("type",)` and the copy says so; `FocusedApp.name()` = `"unknown"`; tray-less footer + AppIndicator row, then install the extension and confirm the icon appears mid-session.
6. **Portal-grant flows** (Portal + Reduced): first-run wizard step raises the dialogs (one or two, compositor-dependent), denial leaves the button enabled with no retry, `permission-needed` vs `hotkey-unavailable` never both (test on stock Sway for the latter), token survives "Back it up and start fresh".
7. **The known suspects for overrides** — terminals binding Ctrl+Shift+V (Konsole, GNOME Terminal, Alacritty, kitty, foot), Electron apps, browsers: sweep before authoring any seed rows; earned overrides get hand-written reasons.
8. **IME / international text input** (fog on map #11 that never sharpened into a decision): with ibus/fcitx5 active, confirm XTEST typing (Whole) and portal keysym / virtual-keyboard typing (Portal/Reduced) reach the app rather than the input method, and that composed-character layouts type correctly. Any finding that changes the ladder returns as a fresh ticket, not an in-place edit.
9. **GPU pack** on an NVIDIA box (real hardware — a VM cannot validate CUDA): both editions extract to `~/.local/share/cadent/cuda`, `RTLD_LOCAL` preload lets cuBLAS 12 and 13 coexist in one process, `landed_on` reads `CUDAExecutionProvider`/`cuda`; pre-580 driver hides the Parakeet edition with the driver-update row; Vulkan cleanup lands `gpu` on AMD/Intel and NVIDIA.
10. **Parakeet CPU confirmation on Linux** per the checklist in `docs/research/linux-parakeet-cpu-bench.md` (`scripts/bench_cpu_stt.py`) — the Recommended-chip gate `physical_cores >= 4 and ram_gb >= 8` holds.
11. **Audio**: `default` PCM through `pipewire-alsa` on every target; unplug mid-capture; a host without `pipewire-alsa` lands on the flat meter, not a crash.
12. **Packaging**: AppImage and tarball on each target (glibc ≥ 2.39), `.desktop`/icons written once and skipped when the AUR package is installed, `app_id` = `com.mikeallisonjs.cadent` under Wayland, autostart with `$APPIMAGE` and path healing after moving the AppImage, `flock` second-launch behaviour, `libasound.so.2` truly absent from the bundle, `LOAD_BEARING` check passing.
13. **Tray ink and DesktopEnv snapshot**: dark/light scheme flip on Plasma repaints the mark; GNOME stays white under both; contrast/reduced-motion/text-scale reads on GNOME and KDE; failure notification via `tray.message()` on stock GNOME with no host.
14. **SteamOS desktop mode**: everything above for Portal, from an AppImage in `/home`, surviving a SteamOS update.

---

## Ticket index

| # | Ticket | Spec sections |
| --- | --- | --- |
| T1 | `cadent/platform/linux/` subpackage: tier detection, `portal.py` (jeepney connection + thread + Request plumbing), Qt-free import assertion, `Capabilities` growth (`permission`, `per_app_overrides`, `overlay`, `support_tier`, `support_tier_summary`, default combos) with win32/darwin fills | §1, §2 |
| T2 | CI: `ubuntu-24.04` test leg (offscreen) + import-walk for a third OS | §2.5, §8.6 |
| T3 | Whole (X11) adapters: XTEST KeyboardOutput, X-selection Clipboard, X11 FocusedApp + `WM_CLASS`→desktop-id, XRecord HotkeyTap, state-gate suppression | §3, §4, §5 |
| T4 | Wayland adapters: GlobalShortcuts HotkeyTap (+ `start(chords)`), RemoteDesktop/virtual-keyboard KeyboardOutput, ext-data-control/Clipboard-portal Clipboard, plasma-window-management FocusedApp, `permission_granted()`, restore tokens, `Closed` recovery | §2.2, §3, §4, §5 |
| T5 | Permission seam rename (`PermissionPreflight`, `request_permission`), grant poll in `app.py`, two tray faults, tier row + Done-page line, pane notes | §9 |
| T6 | Runtimes: `("auto","cuda","cpu")` both engines, `onnxruntime-gpu` on Linux, two-edition pack (extract + `RTLD_LOCAL` preload), driver-version probe, Vulkan marker widening, Recommended-chip branch | §6 |
| T7 | Small seams: Autostart (XDG, `$APPIMAGE`, healing), SingleInstance (`flock`), DesktopEnv (portal `ReadAll` snapshot, ink watcher, `xdg-open`), tray-less footer/row, `overlay` gating + `tray.message()` routing | §8.4, §10 |
| T8 | App identity: installed-apps picker, `.desktop` `Name=` display names, `per_app_overrides` gating | §5 |
| T9 | Packaging: `scripts/build_linux.py` (tarball + AppImage), `build-installer-linux.yml`, `LOAD_BEARING["linux"]` + excluded-on-purpose, `libportaudio` staging, first-run `.desktop`/icons, `setDesktopFileName`, `packaging/aur/PKGBUILD` | §8 |
| T10 | Platform-conditional UI pass | §11 |
| T11 | Real-hardware verification pass | §12 |
