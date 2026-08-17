# Linux: both speech engines reach CUDA through a pack, cleanup rides the Vulkan wheel

On Linux the speech ladders are `("auto", "cuda", "cpu")` for **both** engines,
and each CUDA rung is served by its own edition of the **GPU support pack** —
disclosed, user-initiated, extracted from PyPI `nvidia-*` wheels into the XDG
data dir (`~/.local/share/cadent/cuda`), never silent. Decided in #22, on the
Linux porting map (#11); research in `docs/research/linux-gpu-runtimes.md` (#14).

- **faster-whisper** takes the same two files the Windows pack ships —
  `libcublas.so.12` + `libcublasLt.so.12` from `nvidia-cublas-cu12` (~600 MB;
  ctranslate2 ≥ 4.6.3 needs no cuDNN, verified in the wheels).
- **Parakeet** takes the CUDA-13 userspace ONNX Runtime 1.28's CUDA provider
  dlopens — `libcudart.so.13`, `libcublas.so.13` + `libcublasLt.so.13`,
  `libnvrtc.so.13`, `libcufft.so.12` + `libnvJitLink.so.13`, `libcurand.so.10`,
  and the cuDNN 9 lib dir — from `nvidia-cuda-runtime`, `nvidia-cublas`,
  `nvidia-cufft`, `nvidia-curand`, `nvidia-cuda-nvrtc`, `nvidia-nvjitlink`,
  `nvidia-cudnn-cu13` (~1.3 GB compressed; less if only the needed `.so`s are
  extracted). It needs an **R580+ driver**, checked as
  `cuDriverGetVersion() >= 13000` off `libcuda.so.1`. The Linux build ships
  `onnxruntime-gpu` (250 MB) in place of `onnxruntime` (19 MB) so the provider
  is present; with the libs absent it constructs on the CPU EP, and the landed
  rung is read off `session.get_providers()` as always (#137).

The pack is **one surface, engine-keyed**: one wizard page, one set of tray
items, still called the GPU support pack; which edition it fetches — and the
size it discloses — follows the speech engine in use, and `should_offer` keeps
its engine test as a selector rather than the placebo guard it is on Windows
(#72). Activation is **not** the Windows PATH prepend — `LD_LIBRARY_PATH` is
fixed at exec — but a `ctypes.CDLL` **preload with the default `RTLD_LOCAL`**,
before the engine first touches CUDA. Not `RTLD_GLOBAL`: cuBLAS 12 and 13 can
share a process by soname, but a GLOBAL preload of one lets its unversioned
symbols shadow the other's lookups (this corrects the research doc's
recommendation). When the driver is present but pre-580, the Parakeet edition
is not offered and the row says to update the NVIDIA driver to 580 or newer —
the one thing the user can act on. This adds a driver-CUDA-version fill to
`HardwareProbe`; the other fills port one-for-one (`libcuda.so.1` loadable,
the same driver-API VRAM read, `/proc/cpuinfo`), `dx12_gpu_present` and
`metal_gpu_present` are False, and no Vulkan probe is added — cleanup already
enumerates its own accelerator.

Cleanup introduces nothing Linux-specific: the pinned Vulkan index already
serves the manylinux x86_64 `llama-cpp-python` at the Windows version, so the
uv source marker widens to Linux, the only system need is the distro Vulkan
loader (a missing `libvulkan.so.1` is an import-time `OSError` the ladder
already lands as `cpu`, silently — no user-facing hint), and the landed rung
stays `gpu`/`cpu`, never `vulkan` (#155 holds: one accelerator per build).
AMD and Intel GPUs get their acceleration here and only here — the ROCm EP is
gone from ONNX Runtime upstream. `Capabilities` on Linux: `gpu_pack_available`
True, `show_runtime_combo` True, `gpu_only_engines` empty (Parakeet is offered
CPU-only on non-NVIDIA boxes until an x86 CPU bench says otherwise). The
Recommended chip names Parakeet on NVIDIA ≥ 4 GB with a CUDA-13-capable
driver, else today's Whisper VRAM/CPU rows.

Rejected: Parakeet CPU-only on Linux as on macOS (the researched default —
turned down for the GPU rung's accuracy on the machines that can drive it,
at the cost of a second, bigger pack tier); a `cadent[cuda]` extra instead of
a pack (only fits pip distribution, which packaging (#15) does not lead with);
one merged pack (two CUDA majors, two driver floors, and a 2 GB download for a
Whisper user who reads 600 MB of it); a ROCm rung (uninstallable from any
index we ship from). Whether Windows also grows the Parakeet CUDA edition is
out of this map's scope.
