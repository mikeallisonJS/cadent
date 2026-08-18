# Cadent

Local, offline push-to-talk dictation for Windows and macOS: hotkey → record →
transcribe → (optionally clean up) → inject into the focused app. Per-OS
behaviour lives behind the platform seam (`cadent/platform/`, ADR 0005). Linux
is planned and has no adapter yet, so it resolves to the inert fallback.

## Language

**App override**:
A per-app entry in `config.json`'s `app_overrides` that picks the injection
strategy (and its knobs) for one executable, matched case-insensitively.
The typing strategy is spelled `type` since #142; `sendinput`, its pre-seam
name, stays a permanent read alias so old files keep loading.
_Avoid_: app rule, injection profile

**Learned override**:
An app override Cadent wrote itself — marked `learned: true` — after a
detectable typing failure where the clipboard fallback succeeded. A safe
base configuration, tunable like any other row in Settings ▸ App overrides.
The mark is provenance — how the row was born — not a claim about its
contents, so it survives editing; deleting one is "forget it", and the app
may learn it again.
_Avoid_: auto-override, dynamic override

**Auto-learn**:
Persisting a learned override the first time an app detectably rejects
synthetic typing and the paste works: one failure teaches, immediately,
announced by a one-time tray toast — never a prompt. Any existing entry for
the process (hand-authored or learned) blocks it. A platform fact
(`Capabilities.auto_learn_overrides`): only where typing failure is
detectable at all.
_Avoid_: adaptive injection, self-healing overrides

**Speech engine**:
Which recogniser transcribes — `faster-whisper` (the default; the only one
that runs well on any PC) or `parakeet`. Nobody picks one: since #111 it is
**derived from the speech model** and written alongside it, because model
names are unique across engines and a user choosing both is a user keeping two
lists in agreement by hand. It still decides which **runtimes** mean anything.
Distinct from the *runtime*, which is where that engine executes.
_Avoid_: STT backend, ASR provider, model family

**Speech model**:
Which recogniser weights transcribe, and the only speech choice offered —
picking one picks its **speech engine**. Six are listed, each as a tier, a
googleable name, a blurb and a download size (`cadent/models.py`); older
Whisper sizes stay loadable for a config.json that names them but earn no row.
_Avoid_: model size, checkpoint, STT model

**Cleanup**:
The local LLM pass that removes filler words and fixes punctuation between
transcription and injection — off by default, toggled from the tray, from
Settings, or by tapping a secondary chord. Never blocks dictation: every
failure path silently inserts the raw transcript. The config keys are still
spelled `cleanup_mode` / `cleanup_hotkey`, and the history table still stores
`"flow"` / `"raw"`, because renaming a persisted name buys nothing anyone can
see (#113).
_Avoid_: Flow mode, AI mode, polish, post-processing

**Recommended**:
A chip drawn on the model row this machine should take — the hardware
suggestion for speech, the heaviest rung that clears the memory and core
thresholds for cleanup. Applied at render and never stored, so it can differ
per PC and never contradicts what the app would have chosen. At most one chip
per row, and a warning always beats it.
_Avoid_: default, best, suggested tier

**Runtime**:
Where a model executes. Speech has one (`stt_device`: `auto`, `cpu`, `cuda`,
or `directml` on the Parakeet path — the key predates the second engine) and
cleanup has its own (`llm_runtime`: `auto` or `cpu` — one accelerator, so
"use the GPU if it works" and "use the GPU" would be the same thing). `auto` is a
ladder, not a claim — each engine tries its best option, does real work to
prove the accelerator is there, and drops a rung when it isn't. Committing on
a successful *load* is the bug (#38): ctranslate2 loads cuBLAS lazily, ONNX
Runtime loads cuDNN lazily, and llama.cpp builds its graph and compiles its
GPU shaders at the first generation, so on all three paths a broken runtime
first shows itself at the first real use. Only an encode (speech) or a
generation (cleanup) counts. On the Parakeet path the probed session is then
discarded, because ONNX Runtime plans a session around its first inference and
a probe-shaped one is slow forever; cleanup keeps the model it warmed, because
the warm-up is also what pays llama.cpp's one-off shader compile. Cleanup asks
one question before all of that, because it has a failure llama.cpp does not
raise: with no GPU present, a full offload is accepted, lands every layer on
the CPU and succeeds, so the runtime has to be *enumerated* and not merely
attempted. ONNX Runtime has the same failure in
the other direction: a session asked for DirectML on a platform without it
warns and constructs on the CPU EP, so even a successful encode proves the
wrong thing — where a Parakeet session *landed* is read off
`session.get_providers()`, never inferred from which rung didn't throw (#137,
#146). Cleanup's landed rung obeys the same honesty rule by naming the
processor and not the API: it is `gpu` or `cpu`, never `vulkan` — one
accelerator per build, Vulkan on Windows and Metal on macOS, and a ladder that
cannot tell them apart must not claim to (#155). `cpu` exists to route around
a driver that crashes on contact, and so never queries the GPU at all. Which
runtimes exist at all is a platform fact (`Capabilities.stt_runtimes`): on
macOS both speech engines have one rung (`auto`, `cpu` — ADR 0003) and stray
GPU values sanitize back to `auto`.
_Avoid_: device, execution provider, backend

**Model download**:
A fetch of one model's weights from Hugging Face, run in front of the engine
that needs them rather than inside it, so it can be **watched and stopped**
(`cadent/downloads.py`). Always disclosed first and never silent. Reports
bytes to the tray header and tooltip, and to the wizard as a bar; the wizard's
Cancel really stops it. Goes over plain HTTP, never Xet, because Xet reports
one lump at the end and swallows the cancel. Sizes are quoted in decimal MB —
every file the fetch actually takes — so the picker row and the bar under it
count the same thing.
_Avoid_: model install, weights sync, prefetch (that is the mechanism, not the
concept)

**Cancelled download**:
A model download the user stopped. **Not a failure**: no amber, no failure
toast, no "speech model failed" — the app was doing what it was told. Distinct
from a *failed* download, which is a fault and says so. Hugging Face discards
the partial, so a cancelled download starts over — which is why Escape goes
inert on the wizard's model page while one is running.
_Avoid_: aborted, interrupted, errored

**Support tier**:
Which promise Cadent makes about the core loop on Linux, decided once at
startup from the session type and desktop and carried as a platform fact — one
build, three tiers. **Whole** (X11 on any desktop) is Windows parity: the
shipped chord, type-first with paste fallback, auto-learn, the windowed
overlay, per-app overrides, no permission. **Portal** (Wayland on Plasma,
wlroots, SteamOS desktop) is the loop intact but portal-shaped: a
keysym-bearing default chord, paste-first with no auto-learn, overrides keyed
on the desktop-file id, a portal grant as the permission preflight — and no
overlay in v1 (ADR 0014; a layer-shell pill is a follow-on effort). **Reduced**
(GNOME Wayland, run natively — never forced under XWayland) is the same
paste-first portal ladder — the Clipboard portal rides the RemoteDesktop
session that already types — with no overlay and no per-app overrides
(ADR 0007, ADR 0009). Portal and Reduced differ only in per-app overrides.
Tiers carry the promises; distros never do. Raw evdev/uinput input would be an
opt-in *mode* overlaying a Wayland tier rather than a tier of its own — v1
does not ship it at all, and never as a silent fallback (ADR 0008). A tier's
name is not a promise its bus can keep: where the desktop places a session in
a tier whose portal is missing, the affected rung is dropped for that run and
the copy says so — never a fourth tier. The user reads the tier in one
Settings ▸ General row (`support_tier_summary`, ADR 0012).
_Avoid_: compatibility level, X11 mode / Wayland mode, degraded mode

**Permission preflight**:
The one OS grant Cadent cannot work without — Accessibility on darwin, the
portal grant on Linux's Wayland tiers, none on Windows or Linux X11
(`Capabilities.permission`, which carries both the name and the words each
surface renders; ADR 0012). Surfaced three times since #148, never as a
prompt: a wizard step, a persistent Settings banner that clears itself the
moment the grant lands, and the `permission-needed` tray fault for when
neither window is open. None of them gates anything — the wizard's Next stays
enabled, because a managed Mac that cannot grant must still finish setup.
**Where the grant is given differs, so the verb does**: darwin deep-links to
System Settings, Linux raises the dialog by making the portal request — both
through `DesktopEnv.request_permission()`, which never blocks and never
retries on its own.
_Avoid_: TCC nag, permission dialog, onboarding gate, "open permission
settings" (there are none to open on Linux)

**App picker**:
The overrides pane's add affordance where `Capabilities.app_picker` is true
(darwin, #148; Linux, #21): the apps a user could target, rendered
"Display Name — identity" and storing the identity — nobody knows their
terminal's bundle identifier or desktop-file id, and a mistyped one is a
silent no-op forever. Darwin lists the running regular-activation-policy
apps; Linux lists the *installed* applications (their `.desktop` files),
which every support tier can read. Free text stays accepted; override and
history rows render display names — by live lookup against what is running
on darwin, by desktop-file name on Linux — raw identity otherwise.
_Avoid_: app dropdown, bundle browser

**GPU support pack**:
The CUDA runtime libraries the *current speech engine* needs to reach the
GPU, downloaded once — disclosed, user-initiated from the tray, never
silent — into the app's data dir. One surface everywhere it exists; what it
fetches is an **edition** keyed by engine: on Windows the two cuBLAS DLLs
(`cublas64_12.dll`, `cublasLt64_12.dll`) for faster-whisper, prepended to
`PATH`; on Linux the same cuBLAS pair for faster-whisper *and* the CUDA-13
stack for Parakeet, preloaded at startup (ADR 0010). Offered only when an
NVIDIA driver that can use it is present but the speech engine fell back to
CPU. Deleting the directory reverts to CPU.
_Avoid_: CUDA runtime install, GPU flavor
