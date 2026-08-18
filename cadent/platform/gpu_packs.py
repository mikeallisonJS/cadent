"""The GPU support pack editions as data (ADR 0010), importable on any OS.

Like the keycode tables: an edition is one OS's fact, but a tuple of file
names and PyPI coordinates has no OS imports, so the definitions live here
and each adapter picks its column. `gpu_pack.py` does the downloading,
extracting and activating.
"""

from __future__ import annotations

from .base import GpuPackEdition

# Windows: ctranslate2 delay-loads cuBLAS through the DLL search order, so
# the pair goes into CUDA_DIR itself and PATH is prepended (M3 #55, #40).
CUBLAS12_WIN = GpuPackEdition(
    key="cublas12",
    engine="faster-whisper",
    files=("cublas64_12.dll", "cublasLt64_12.dll"),
    sources=(("nvidia-cublas-cu12", "12.9.2.10"),),
    wheel_tag="win_amd64",
    size="~550 MB",
    subdir="",
    activation="path",
)

# Linux, faster-whisper: the same cuBLAS-12 pair from the manylinux wheel;
# ctranslate2 ≥ 4.6.3 needs no cuDNN. Preloaded with RTLD_LOCAL — never
# GLOBAL, or cuBLAS 12's unversioned symbols shadow cuBLAS 13's when both
# editions share a process (ADR 0010).
CUBLAS12_LINUX = GpuPackEdition(
    key="cublas12",
    engine="faster-whisper",
    files=("libcublasLt.so.12", "libcublas.so.12"),
    sources=(("nvidia-cublas-cu12", "12.9.2.10"),),
    wheel_tag="manylinux",
    size="~600 MB",
    subdir="cublas12",
    activation="preload",
)

# Linux, Parakeet: the CUDA-13 userspace ORT 1.28's CUDA provider dlopens.
# Preload order matters for RTLD_LOCAL: dependencies before dependents.
# Package versions are the newest release under each major on PyPI — pinning
# to exact builds is a hardware-verification item (spec §12.9), where the
# set that actually loads together gets recorded.
CUDA13_LINUX = GpuPackEdition(
    key="cuda13",
    engine="parakeet",
    files=("libcudart.so.13", "libnvJitLink.so.13", "libnvrtc.so.13",
           "libnvrtc-builtins.so.13*", "libcublasLt.so.13", "libcublas.so.13",
           "libcufft.so.12", "libcurand.so.10", "libcudnn*.so.9*"),
    sources=(("nvidia-cuda-runtime", "13."), ("nvidia-nvjitlink", "13."),
             ("nvidia-cuda-nvrtc", "13."), ("nvidia-cublas", "13."),
             ("nvidia-cufft", "12."), ("nvidia-curand", "10."),
             ("nvidia-cudnn-cu13", "9.")),
    wheel_tag="manylinux",
    size="~1.3 GB",
    subdir="cuda13",
    activation="preload",
    min_driver_cuda=13000,
    driver_hint=("This edition needs NVIDIA driver 580 or newer — update the "
                 "NVIDIA driver to 580 or newer, then come back."),
)

WIN32_EDITIONS = {"faster-whisper": CUBLAS12_WIN}
LINUX_EDITIONS = {"faster-whisper": CUBLAS12_LINUX, "parakeet": CUDA13_LINUX}
