"""What this PC can run, and which speech model to suggest for it (spec §6.2).

The OS-touching probes live on the platform seam (`HardwareProbe`); this
module owns the portable parts — psutil for RAM and physical cores, the
session cache, and the suggestion table.

The probe's failure mode *is* the right answer: `cuInit` failing means "don't
suggest GPU models", and any exception at all falls back to `distil-small.en`,
today's default. So detection is allowed to be best-effort.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .platform import HardwareProbe

log = logging.getLogger(__name__)

# The catch-all. Also what a thrown probe returns, so a detection failure
# degrades to the status quo rather than to a guess.
FALLBACK_MODEL = "distil-small.en"


@dataclass(frozen=True)
class Hardware:
    cpu_name: str = ""
    ram_gb: float = 0.0
    physical_cores: int = 1
    gpu_present: bool = False
    vram_gb: float | None = None    # None = no usable NVIDIA GPU
    dx12_gpu: bool = False          # a Direct3D 12 device — the DirectML gate (#72)
    nvidia_driver: bool = False     # nvcuda loadable — the GPU-pack gate (#55)
    metal_gpu: bool = False         # Apple Silicon — the darwin suggestion gate (#146)
    cuda_driver_version: int | None = None   # cuDriverGetVersion(): 13000 = CUDA 13 (ADR 0010)


@dataclass(frozen=True)
class Suggestion:
    model: str
    reason: str                     # one line, shown under the pre-selected row
    # Model only — no engine and no runtime. Since #72 the model name says
    # which engine owns it (`settings.engine_for_model`), and `auto` is always
    # the right runtime to start on: each engine's ladder already walks down
    # to the CPU on a failed probe, so anything more specific could only be
    # wrong.


# ---- detection -------------------------------------------------------------

def detect(probe: HardwareProbe | None = None) -> Hardware:
    """Probe this machine. Call off the UI thread — `cuInit` can block."""
    import psutil

    if probe is None:
        from . import platform

        probe = platform.current().hardware
    vram = probe.cuda_total_memory()
    return Hardware(
        cpu_name=probe.processor_name(),
        ram_gb=psutil.virtual_memory().total / (1024 ** 3),
        physical_cores=psutil.cpu_count(logical=False) or 1,
        gpu_present=vram is not None,
        vram_gb=vram,
        dx12_gpu=probe.dx12_gpu_present(),
        nvidia_driver=probe.nvidia_driver_present(),
        metal_gpu=probe.metal_gpu_present(),
        cuda_driver_version=probe.cuda_driver_version(),
    )


_cached: Hardware | None = None


def detect_cached() -> Hardware:
    """Probe once per session. The wizard's model page and the settings pane
    both want this and neither should pay for it twice."""
    global _cached
    if _cached is None:
        _cached = detect()
    return _cached


def detect_safely() -> Hardware:
    """`detect_cached()` for surfaces that have to build regardless.

    A probe that raises must not stop a Settings pane from opening. The
    zero-valued default reads as *unknown*, which the model rules treat as
    "claim nothing about this machine" rather than as a very small one.
    """
    try:
        return detect_cached()
    except Exception:
        log.warning("hardware detection failed; describing no machine",
                    exc_info=True)
        return Hardware()


def reset_cache() -> None:
    global _cached
    _cached = None


# ---- the suggestion table --------------------------------------------------

def suggest_model(vram_gb: float | None, ram_gb: float, physical_cores: int,
                  dx12_gpu: bool = False, metal_gpu: bool = False,
                  parakeet_cuda: bool = False,
                  parakeet_cpu_floor: tuple[int, float] | None = None) -> Suggestion:
    """Top-down, first match wins.

    `parakeet_cuda` says this machine can run Parakeet on CUDA (Linux: the
    platform offers the rung and the driver is CUDA-13-capable, ADR 0010);
    `parakeet_cpu_floor` is the platform's (cores, RAM GB) floor for
    recommending Parakeet on the CPU with no usable NVIDIA GPU — Linux's
    (4, 8.0) from the bench, None where that branch was never measured.

    Distil models are disproportionately good for push-to-talk: full-size
    encoder, 2-layer decoder, so the decode step `beam_size=5` multiplies is
    exactly what shrinks. `large-v3` is therefore never auto-suggested — it
    stays a manual "accuracy over latency" pick for >= 6 GB GPUs.
    """
    gpu = f"your {_gpu_label(vram_gb)}" if vram_gb else "your GPU"
    # The Apple Silicon branch first (spec §4.3, measured on the M1 Max):
    # speech runs on the CPU there, so the NVIDIA rows below can never match.
    # Parakeet v2 earns the chip at 16 GB of unified memory — varied-length
    # median 0.30 s against the ~1 s bar — and distil-small.en is the
    # sub-second Whisper fallback (0.78 s); distil-medium/large sit above the
    # 1 s line (1.61 s / 2.79 s) and are not defensible darwin defaults, no
    # matter the core count.
    if metal_gpu:
        if ram_gb >= 16:
            return Suggestion("parakeet-tdt-0.6b-v2",
                              "Half the mistakes, a third of a second a "
                              "dictation — and it punctuates as it types.")
        return Suggestion(FALLBACK_MODEL,
                          "The most accurate model that stays under a second "
                          "on this Mac.")
    # Parakeet sits above every Whisper row on hardware that can run it: half
    # the word error rate of any of them (2.87% against distil-small's 6.26%
    # and distil-large-v3's 5.30%), and punctuated without being asked. It is
    # *not* the fastest row — ~0.4 s an utterance against distil-small-on-CUDA's
    # ~0.07 s — but both are far inside the dictation budget, and accuracy is
    # what the user notices (#72).
    #
    # The gate is deliberately narrow: a Direct3D 12 device *and* measurable
    # VRAM. `cuda_total_memory` only measures NVIDIA, so an AMD or Intel GPU
    # falls through to Whisper — Parakeet stays one dropdown away, but a
    # 660 MB download is a poor thing to recommend onto an integrated GPU we
    # cannot size.
    if (dx12_gpu or parakeet_cuda) and vram_gb is not None and vram_gb >= 4:
        return Suggestion("parakeet-tdt-0.6b-v2",
                          f"Half the mistakes on {gpu}, and it punctuates as "
                          "it types.")
    # The Linux CPU branch (spec M6 §6.3): with no usable NVIDIA GPU, Parakeet
    # v2 at ≥ 4 physical cores and ≥ 8 GB — 0.66 s median on the bench set,
    # faster than distil-small.en at every length — and distil-medium.en is
    # never a Linux CPU default.
    if parakeet_cpu_floor is not None and (vram_gb is None or vram_gb < 4):
        cores_floor, ram_floor = parakeet_cpu_floor
        if physical_cores >= cores_floor and ram_gb >= ram_floor:
            return Suggestion("parakeet-tdt-0.6b-v2",
                              f"Half the mistakes, and {physical_cores} cores keep "
                              "it under a second — it punctuates as it types.")
        if ram_gb < 6:
            return Suggestion("tiny.en",
                              f"Smallest model — this PC has {ram_gb:.0f} GB of RAM.")
        if ram_gb < 8:
            return Suggestion("base.en",
                              f"Light on memory — this PC has {ram_gb:.0f} GB of RAM.")
        return Suggestion(FALLBACK_MODEL, "Fast and accurate enough on any CPU.")
    if vram_gb is not None and vram_gb >= 6:
        return Suggestion("distil-large-v3",
                          f"Most accurate model {gpu} can run at speed.")
    if vram_gb is not None and vram_gb >= 4:
        return Suggestion("distil-medium.en",
                          f"Fits {gpu} with room for the runtime.")
    # Under 4 GB the GPU tier can't hold fp16 weights plus ~1.4 GB of runtime
    # VRAM and compositor headroom, so we fall through to the CPU rows.
    if ram_gb < 6:
        return Suggestion("tiny.en",
                          f"Smallest model — this PC has {ram_gb:.0f} GB of RAM.")
    if ram_gb < 8:
        return Suggestion("base.en",
                          f"Light on memory — this PC has {ram_gb:.0f} GB of RAM.")
    if ram_gb >= 16 and physical_cores >= 8:
        return Suggestion("distil-medium.en",
                          f"More accurate, and {physical_cores} cores can keep up.")
    return Suggestion(FALLBACK_MODEL, "Fast and accurate enough on any CPU.")


def _gpu_label(vram_gb: float | None) -> str:
    return f"{vram_gb:.0f} GB GPU" if vram_gb else "GPU"


def suggest_for_this_machine() -> Suggestion:
    """The suggestion plus its reason, never raising."""
    try:
        hw = detect_cached()
    except Exception:
        log.warning("hardware detection failed; suggesting the default model",
                    exc_info=True)
        return Suggestion(FALLBACK_MODEL, "Fast and accurate enough on any CPU.")
    from . import gpu_pack, platform

    caps = platform.current().capabilities
    # Parakeet on CUDA is a real option only where a pack edition serves it
    # and the driver can run that edition (Linux, R580+ — ADR 0010).
    edition = caps.gpu_pack_editions.get("parakeet")
    parakeet_cuda = (edition is not None
                     and gpu_pack.driver_supports(edition, hw.cuda_driver_version))
    return suggest_model(hw.vram_gb, hw.ram_gb, hw.physical_cores, hw.dx12_gpu,
                         hw.metal_gpu, parakeet_cuda=parakeet_cuda,
                         parakeet_cpu_floor=caps.parakeet_cpu_floor)


def should_offer_gpu_page(driver_present: bool, vram_gb: float | None,
                          pack_installed: bool, suggested_engine: str = "faster-whisper",
                          edition_exists: bool | None = None,
                          driver_ok: bool = True) -> bool:
    """Whether the wizard shows the GPU support pack page.

    It comes **before** the model page, because accepting it changes the
    recommendation. The post-hoc `gpu_pack.should_offer` path stays as a
    safety net for everyone who skips it.

    Which is exactly why the *suggested engine* has to be part of the gate
    (#72): on a machine we are about to recommend Parakeet to, the pack
    changes nothing — those are ctranslate2's cuBLAS DLLs and Parakeet runs on
    ONNX Runtime's DirectML provider. Asking for 550 MB before recommending
    the model that won't read a byte of it is the placebo `gpu_pack.
    should_offer` already refuses to serve, one page earlier.
    """
    if edition_exists is None:
        # Where an edition exists for the suggested engine (Windows: only
        # faster-whisper's; Linux: both), the page has something to offer.
        from . import gpu_pack

        edition_exists = gpu_pack.edition_for(suggested_engine) is not None
    return (driver_present and not pack_installed and edition_exists and driver_ok
            and vram_gb is not None and vram_gb >= 4)


def any_speech_model_downloaded(models_dir: Path) -> bool:
    """Is *a* speech model already on disk?

    The signal that an existing user has a working setup and must never see
    the wizard (§6.4). Deliberately not "is the configured model present":
    faster-whisper stores models under Hugging Face's own cache naming
    (`models--Systran--faster-distil-whisper-small.en` for `distil-small.en`),
    so matching a config value against a directory name is a guess. The
    presence of a weights file is a fact — `model.bin` for ctranslate2,
    `encoder-model*.onnx` for Parakeet (#72).
    """
    if not models_dir.exists():
        return False
    return any(child.is_dir() and any(next(child.rglob(pattern), None) is not None
                                      for pattern in ("model.bin", "encoder-model*.onnx"))
               for child in models_dir.iterdir())
