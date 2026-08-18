# Linux GPU and runtime research: speech CUDA, cleanup Vulkan, hardware probes (research, 2026-08-16)

Ticket: #14 (wayfinder research, part of #11). Ground rules inherited from CONTEXT.md ▸ Runtime:

> **Research snapshot.** Findings as of the date above; the decisions that
> followed supersede this doc where they differ — see ADR 0010 (Parakeet *does* get a CUDA rung via a second pack edition; the preload is `RTLD_LOCAL`, not `RTLD_GLOBAL`). Read the ADRs
> and `docs/specs/m6-linux-port-spec.md` for what ships.
construction proves nothing (#38), landed rungs are read off the runtime and never inferred
(#137), and a ladder that cannot tell accelerators apart must not name one (#155). Everything
below was checked against primary sources on 2026-08-16; wheel contents were inspected by
downloading and unzipping the actual artifacts, not by reading READMEs. What could not be
executed here (this is a Windows dev box) is marked **unverified on Linux** — the app's own
encode/generation probes remain the final arbiter on real hardware.

## 1. faster-whisper / ctranslate2 CUDA on Linux

### Wheels and what they dlopen

ctranslate2 ships manylinux x86_64 wheels on PyPI with GPU support compiled in; current is
4.8.1 (`manylinux_2_27/_2_28`, cp311+). Inspected `ctranslate2-4.8.1-...manylinux_2_28_x86_64.whl`
directly: `ctranslate2.libs/` vendors only `libctranslate2` and `libgomp`, and a full string
scan of `libctranslate2.so.4.8.1` shows exactly two CUDA sonames referenced —
**`libcuda.so.1`** (the driver) and **`libcublas.so.12`**. No `libcudnn`, no `libcudart`
(statically linked), no `libcublasLt` by name — cuBLAS's own dependency chain pulls
`libcublasLt.so.12` in, which is why NVIDIA ships both `.so` files in one wheel.

- PyPI: https://pypi.org/project/ctranslate2/
- Wheel inspection: local, 2026-08-16 (string scan of the `.so` inside the wheel)

cuDNN stopped being a hard dependency at **4.6.3** — "Conv1d pure CUDA implementation (#1949),
makes cuDNN an optional dependency"
(https://github.com/OpenNMT/CTranslate2/blob/master/CHANGELOG.md). The evidence is physical,
not just textual: the 4.6.2 manylinux wheel still **bundles** `libcudnn-*.so.9.1.0` in
`ctranslate2.libs/`, and the 4.8.1 wheel does not carry or reference cuDNN at all (both wheels
inspected). This matches the Windows finding recorded in `cadent/gpu_pack.py` ("cuDNN is not
needed at ctranslate2 >= 4.6.3", #40): **the Linux runtime need is the same two cuBLAS shared
objects the Windows pack ships, plus the driver's `libcuda.so.1`.**

The faster-whisper README's GPU section still lists "cuBLAS for CUDA 12" *and* "cuDNN 9 for
CUDA 12" and recommends `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*` with
`LD_LIBRARY_PATH` (https://github.com/SYSTRAN/faster-whisper) — the cuDNN half is stale advice
for ct2 ≥ 4.6.3, kept there for older pins. CTranslate2's own install docs say only "Install
CUDA 12.x to use the GPU" (https://opennmt.net/CTranslate2/installation.html).

### Is a Linux GPU-support-pack analogue viable?

**Yes, with one mechanical change.** The payload is the same: `nvidia-cublas-cu12` publishes a
manylinux x86_64 wheel (12.9.2.10 — the exact version the Windows pack pins — 581 MB;
https://pypi.org/project/nvidia-cublas-cu12/) containing `libcublas.so.12` and
`libcublasLt.so.12`; extract the two files into the app data dir, exactly as `gpu_pack.py`
does with the win_amd64 wheel.

The delivery cannot be a path prepend, though. The Windows pack works by prepending
`%LOCALAPPDATA%\Cadent\cuda` to `PATH`, which the Windows loader consults on every delay-load.
The Linux equivalent does not exist: per dlopen(3), the loader searches `LD_LIBRARY_PATH` "if,
at the time that the program was started, the environment variable ... was defined" — the
variable is captured at exec, so an `os.environ` write after startup changes nothing
(https://man7.org/linux/man-pages/man3/dlopen.3.html). The working mechanism is
**preloading**: `ctypes.CDLL("<pack dir>/libcublasLt.so.12", mode=os.RTLD_GLOBAL)` then the
same for `libcublas.so.12`, before ctranslate2 first touches CUDA; glibc then satisfies the
later dlopen-by-soname from the already-loaded objects. This is the mechanism the NVIDIA pip
ecosystem itself leans on (ONNX Runtime's `preload_dlls` does the same job on Windows, and
finds the `nvidia_*` site-packages the same way —
https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html). **Unverified on
Linux** in this repo's hands; the #38 encode probe already catches a preload that didn't take,
so the failure mode is a clean CPU fall-back, not a lie.

Does the system suffice without a pack? The driver alone, never: the driver installs
`libcuda.so.1` but not cuBLAS — cuBLAS is toolkit userspace
(https://docs.nvidia.com/cuda/cuda-installation-guide-linux/). A user with the full CUDA
toolkit installed and on `LD_LIBRARY_PATH` needs nothing from us; everyone else (the common
case) needs the two `.so` files from somewhere. One Linux-only alternative worth naming: if
Linux distribution ends up pip-based rather than PyInstaller-packaged, an extra
(`cadent[cuda]` → `nvidia-cublas-cu12`) plus the same preload gets the files without any
download UI at all. The disclosed-download pack and the extra are the same bytes; which to
ship is a packaging decision, not a runtime one.

## 2. Parakeet: onnxruntime CUDA EP on Linux, and ROCm

Plain `onnxruntime` (CPU EP) already resolves on Linux per the pyproject override — the
Parakeet CPU rung costs nothing. The CUDA EP means the **`onnxruntime-gpu`** distribution
instead (same import package, same clobbering hazard the pyproject comment records for
DirectML), and its requirements moved under us: per the CUDA EP requirements table, ORT
**1.27.x–1.29.x on PyPI are built against CUDA 13.0** + cuDNN 9.x; the CUDA 12.8 builds
stopped at 1.26.x (https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html;
`onnxruntime-gpu` 1.28.0 on PyPI, manylinux x86_64, 2026-07-25 —
https://pypi.org/project/onnxruntime-gpu/). Since the pyproject floor is `onnxruntime>=1.28`,
a Parakeet CUDA rung on Linux implies the CUDA 13 userspace stack (cudart, cublas, cufft,
curand, cudnn — the `onnxruntime-gpu[cuda,cudnn]` extras exist precisely to pull it) and a
**CUDA-13-capable driver (R580+)** per NVIDIA's compatibility table
(https://docs.nvidia.com/deploy/cuda-compatibility/). That is the ~2 GB second pack tier the
Windows research already declined (docs/research/parakeet-runtime.md §3), now with a higher
driver floor than the speech engine's cuBLAS-12 pack (R525+) and a second CUDA major living in
the same process. Same verdict as Windows, for stronger reasons: **no CUDA rung for Parakeet
on Linux; CPU is the rung.** There is no DirectML on Linux and no Vulkan EP to substitute.

**ROCm is not worth a rung.** The ROCm EP was removed from ONNX Runtime as of 1.23; AMD's
guidance is to migrate to the MIGraphX EP, and both were only ever distributed as AMD-built
wheels on repo.radeon.com, never PyPI
(https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html,
https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html). A rung we
cannot install from the index we ship from is not a rung; AMD GPUs get their acceleration on
the cleanup side, where the Vulkan build is vendor-neutral.

## 3. Cleanup: the Vulkan manylinux wheel

**Current: yes.** The abetlen Vulkan index serves
`llama_cpp_python-0.3.34-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` — the same
0.3.34 the Windows pin holds, with manylinux builds back through 0.3.26
(https://abetlen.github.io/llama-cpp-python/whl/vulkan/llama-cpp-python/). So the existing
`[[tool.uv.index]]` covers Linux by widening the `tool.uv.sources` marker from
`sys_platform == 'win32'` to include linux; no new index.

**Loadable: inspected, not executed.** The 130 MB wheel was downloaded and unzipped
(2026-08-16): it is auditwheel-repaired (vendored, hash-mangled copies under
`llama_cpp_python.libs/`), carries `libggml-vulkan.so` (ggml 0.16.0, ~49 MB) **and**
`libggml-cpu.so` — so `llm_runtime: cpu` stays reachable from the same wheel, as on Windows —
and its external sonames are only glibc/libstdc++/libgcc and **`libvulkan.so.1`**. The Vulkan
loader is *not* bundled: it must come from the distro (`libvulkan1` / `vulkan-loader`), which
then discovers driver ICDs — Mesa RADV/ANV or NVIDIA — via `/usr/share/vulkan/icd.d`
(https://github.com/KhronosGroup/Vulkan-Loader). That is the Linux edition of "vulkan-1.dll
ships with every GPU driver": near-universal on desktop Linux, but a package, not a certainty.
An actual construct-and-generate on Linux is **unverified**; `cleanup.py`'s
`llama_supports_gpu_offload()` enumeration and the warm-up generation are exactly the checks
that make that safe to defer, and the missing-loader case is an import-time `OSError` the
existing ladder already converts to CPU. The one-time shader-compile cost and whether every
vendor's driver caches pipelines (llama-runtime.md §6) remain unmeasured on Linux GPUs.

## 4. HardwareProbe on Linux

Every `Win32Hardware` fill has a direct analogue; none needs a new dependency.

| Probe | Windows | Linux |
|---|---|---|
| `nvidia_driver_present` | `ctypes.WinDLL("nvcuda.dll")` loadable | `ctypes.CDLL("libcuda.so.1")` loadable — `libcuda.so.1` ships with every NVIDIA driver, not the toolkit (https://docs.nvidia.com/cuda/cuda-installation-guide-linux/) |
| `cuda_total_memory` | `cuInit` / `cuDeviceGet` / `cuDeviceTotalMem_v2` off nvcuda | identical calls off `libcuda.so.1` — same driver API, same ABI (https://docs.nvidia.com/cuda/cuda-driver-api/) |
| `directml_supported` (D3D12 device probe) | `D3D12CreateDevice` | Vulkan-presence probe: `ctypes.CDLL("libvulkan.so.1")`, `vkCreateInstance`, `vkEnumeratePhysicalDevices` count > 0 — loader present *and* at least one ICD behind it, the honest gate for the cleanup GPU rung |
| `processor_name` | registry `ProcessorNameString` | first `model name` line of `/proc/cpuinfo` (proc(5) — https://man7.org/linux/man-pages/man5/proc.5.html) |

The "offer the GPU pack" moment then reads exactly as `gpu_pack.should_offer` does today:
engine is faster-whisper, configured `auto`/`cuda`, landed `cpu`, pack absent,
`libcuda.so.1` loadable. `/proc/driver/nvidia/version` exists as a secondary driver tell
(kernel-module-provided, so visible in any procfs mount), but the loadability probe is the one
that mirrors Windows and the one the pack actually depends on.

## 5. What survives a Flatpak sandbox

Should Linux distribution go Flatpak, per-probe and per-mechanism:

- **Vulkan: survives.** The freedesktop runtime carries the loader; drivers arrive through the
  `org.freedesktop.Platform.GL` extension point — Mesa in `GL.default`, NVIDIA via the
  `org.freedesktop.Platform.GL.nvidia-<version>` extension that repacks the host driver's
  userspace at the *matching* version (https://github.com/flathub/org.freedesktop.Platform.GL.nvidia).
- **libcuda probes: survive on NVIDIA.** The GL.nvidia extension's extractor installs the
  driver libraries and explicitly creates the `libcuda.so.1` and `libnvidia-ml.so.1` links
  (`nvidia-extractor/nvidia-extract.c` in that repo), and the standard `--device=dri`
  permission exposes `/dev/nvidiactl`, `/dev/nvidia*`, and `/dev/nvidia-uvm` inside the
  sandbox (https://github.com/flatpak/flatpak/issues/2266). So `nvidia_driver_present`,
  `cuda_total_memory`, and ctranslate2's CUDA path all have what they need.
- **The cuBLAS pack: survives.** The app's data dir maps to `~/.var/app/<app-id>/data`,
  writable from inside the sandbox (https://docs.flatpak.org/en/latest/sandbox-permissions.html),
  and the ctypes preload needs no search-path or ld.so.conf edits — which would be exactly the
  things a sandbox forbids.
- **`/proc/cpuinfo`: survives** (procfs is kernel-global; bwrap mounts a fresh procfs but CPU
  and `/proc/driver/nvidia` nodes are not namespaced).
- **What does not survive:** host `/usr` libraries (a toolkit the user installed system-wide
  is invisible — the pack or the extension are the only sources), `nvidia-smi` (not in the
  runtime), and any `LD_LIBRARY_PATH` scheme (set before flatpak's exec or not at all). The
  known operational sharp edge: the GL.nvidia extension version must equal the host driver
  version, so a host driver update before `flatpak update` leaves every GPU probe honestly
  reporting no-GPU until the extension catches up
  (https://github.com/flatpak/flatpak/issues/6512). The ladder handles that by design — it is
  a CPU day, not a crash.

## Bottom line

1. Speech CUDA on Linux is the **same pack, different delivery**: `libcublas.so.12` +
   `libcublasLt.so.12` out of the manylinux `nvidia-cublas-cu12` wheel, ctypes-preloaded
   (RTLD_GLOBAL) instead of PATH-prepended — ct2 ≥ 4.6.3 needs no cuDNN, verified in the
   wheels themselves. System driver alone never suffices; full-toolkit users need nothing.
2. Parakeet on Linux is **CPU-only**: ORT ≥ 1.27 on PyPI means CUDA 13 (~2 GB tier, R580+
   driver floor), and the ROCm EP is removed upstream. No new rung.
3. Cleanup's Vulkan wheel is **already published for Linux** at the pinned version — widen the
   uv source marker; the only system requirement is the distro Vulkan loader. Load test on
   real Linux GPUs is the open verification, gated by the existing enumeration + warm-up.
4. HardwareProbe ports one-for-one (`libcuda.so.1`, Vulkan instance probe, `/proc/cpuinfo`),
   and everything above — probes, pack, Vulkan — survives Flatpak given `--device=dri` and the
   GL.nvidia extension, with the driver/extension version-lock as the one honest degradation.
