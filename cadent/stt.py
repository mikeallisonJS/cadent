"""Speech-to-text engines behind a pluggable interface.

Two engines, one protocol. **faster-whisper** (CTranslate2) is the default and
the only one that runs well on any PC. **Parakeet** (NVIDIA's TDT model, as
ONNX, through ONNX Runtime's DirectML provider) is the GPU option: faster, and
punctuated and capitalised without being asked — see
docs/research/parakeet-runtime.md for why DirectML and not CUDA.

Both share the same hard-won load rule. A model that *constructs* on an
accelerator has proved nothing: ctranslate2 loads cuBLAS lazily and ONNX
Runtime's CUDA provider loads cuDNN lazily, so on both paths a broken runtime
first shows itself at the first dictation (#38). Each engine therefore commits
to a device only after a **real encode**, and drops a rung when that fails.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from . import downloads, models
from .config import MODELS_DIR

log = logging.getLogger(__name__)


class SttEngine(Protocol):
    supports_hotwords: bool

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   hotwords: str | None = None) -> str: ...


class FasterWhisperEngine:
    device: str  # where the model actually landed; gates the GPU-pack offer (#55)
    supports_hotwords = True

    def __init__(self, model_name: str = "distil-small.en", device: str = "auto",
                 local_files_only: bool = False) -> None:
        from faster_whisper import WhisperModel  # lazy import: heavy

        def load(dev: str, compute: str):
            # download_root keeps models under %LOCALAPPDATA%/Cadent with
            # the rest of the app's data, not the Hugging Face default cache.
            return WhisperModel(model_name, device=dev, compute_type=compute,
                                download_root=str(MODELS_DIR),
                                local_files_only=local_files_only)

        # On a one-rung platform (darwin, ADR 0003) `auto` goes straight to
        # the CPU: ctranslate2 ships no Metal backend, so the CUDA
        # construction below could only fail and log.
        if device == "auto" and "cuda" in _allowed_runtimes("faster-whisper"):
            try:
                model = load("cuda", "float16")
                # CTranslate2 loads cuBLAS/cuDNN lazily, so constructing on
                # CUDA succeeds even when the runtime can't load — the failure
                # would surface at the first dictation (#38). Commit to CUDA
                # only after a real encode; the generator must be consumed,
                # transcription is lazy too.
                probe = np.zeros(1600, dtype=np.float32)   # 0.1 s of silence at 16 kHz
                segments, _ = model.transcribe(probe, language="en", beam_size=1)
                for _ in segments:
                    pass
                self.model = model
                self.device = "cuda"
                return
            except Exception:
                log.warning("CUDA unusable; falling back to CPU", exc_info=True)
        self.model = load("cpu", "float16" if device == "cuda" else "int8")
        self.device = "cpu"

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   hotwords: str | None = None) -> str:
        if audio.size == 0:
            return ""
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            hotwords=hotwords,  # vocabulary biasing (per-window, ticket 04)
            vad_filter=True,
            beam_size=5,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def count_tokens(self, text: str) -> int:
        """Token count under the model's own tokenizer — what the hotwords
        prompt budget is actually measured in."""
        return len(self.model.hf_tokenizer.encode(text, add_special_tokens=False).ids)


# ---- Parakeet (#72) --------------------------------------------------------

# The ONNX re-exports of NVIDIA's checkpoints. Config carries the bare
# checkpoint name, the way it carries `distil-small.en` rather than
# `Systran/faster-distil-whisper-small.en`, so the mapping has to live
# somewhere — and that somewhere is the one registry, read off here rather
# than restated (#115), the same rule `cleanup.GGUF_REPOS` follows.
PARAKEET_REPOS = {m.id: m.hf_repo for m in models.speech_models("parakeet")}

# int8 only. The fp32 export is ~2.5 GB against ~670 MB for a quality
# difference a dictation microphone cannot show (research §5).
_WEIGHT_PATTERNS = ["*.int8.onnx", "nemo128.onnx", "config.json", "vocab.txt"]

# Which ONNX Runtime providers to try, in order, for each configured device.
# DirectML leads `auto` because it measured ~3x faster than CUDA on int8 and
# costs nothing to ship; CUDA is reachable only on an explicit ask, because
# choosing it means a ~1.9 GB support pack that does not exist yet (research
# §3). Every list ends on CPU except the one that asked for CPU.
_PROVIDER_LADDERS: dict[str, tuple[str, ...]] = {
    "auto": ("DmlExecutionProvider", "CPUExecutionProvider"),
    "directml": ("DmlExecutionProvider", "CPUExecutionProvider"),
    "cuda": ("CUDAExecutionProvider", "CPUExecutionProvider"),
    "cpu": ("CPUExecutionProvider",),
}

_RUNTIME_NAMES = {
    "DmlExecutionProvider": "directml",
    "CUDAExecutionProvider": "cuda",
    "CPUExecutionProvider": "cpu",
}


def _allowed_runtimes(engine: str) -> tuple[str, ...]:
    """The runtimes this platform can point an engine at — Capabilities'
    stt_runtimes column (spec §4.1). Imported lazily for the same reason
    config.py does it: the platform package imports this module's siblings."""
    from . import platform

    runtimes = platform.current().capabilities.stt_runtimes
    return runtimes.get(engine, ("auto", "cpu"))


def _provider_ladder(device: str, runtimes: tuple[str, ...]) -> tuple[str, ...]:
    """The providers to try for a configured device, filtered to the runtimes
    the platform actually offers.

    The filter is load-bearing on darwin, not cosmetic (spec §4.1): ORT there
    accepts a DmlExecutionProvider request, warns, and silently constructs
    the session on the CPU — the probe "succeeds" and lies — so a rung the
    platform doesn't name must never be asked for at all. There is no CoreML
    rung either: that EP crashes on Parakeet (microsoft/onnxruntime#26355).
    CPU always closes the ladder."""
    ladder = _PROVIDER_LADDERS.get(device, _PROVIDER_LADDERS["auto"])
    return tuple(p for p in ladder
                 if p == "CPUExecutionProvider" or _RUNTIME_NAMES[p] in runtimes)


def _landed_runtime(model, requested: str) -> str:
    """Where the session actually landed, read off ORT itself (#137).

    `session.get_providers()` is the only honest source — on both platforms:
    a session can construct "successfully" on a provider ORT then quietly
    replaces with the CPU EP, so which ladder entry didn't throw proves
    nothing. The adapter's sessions are found by duck-typing rather than by a
    pinned private name; only when none is visible does the requested rung
    stand in."""
    asr = getattr(model, "asr", model)
    for value in vars(asr).values():
        if hasattr(value, "get_providers"):
            return _RUNTIME_NAMES.get(value.get_providers()[0], "cpu")
    # Loud, not debug: reporting the requested rung is exactly the inference
    # #137 forbids, tolerated only because the alternative is failing a load
    # that works. If this ever fires, onnx_asr restructured and the walk
    # above needs re-pointing.
    log.warning("no ORT session visible on %s; reporting the requested "
                "provider unverified", type(model).__name__)
    return _RUNTIME_NAMES[requested]


class ParakeetEngine:
    device: str  # where the model actually landed: directml | cuda | cpu
    # No Parakeet equivalent of faster-whisper's `hotwords`, and inventing one
    # is out of scope. Vocabulary biasing degrades to the layer-2
    # post-correction pass in vocabulary.py, and the Settings UI says so
    # rather than letting the user believe biasing is running.
    supports_hotwords = False

    def __init__(self, model_name: str = "parakeet-tdt-0.6b-v2",
                 device: str = "auto", local_files_only: bool = False) -> None:
        import onnx_asr  # lazy import: drags ONNX Runtime in

        repo = PARAKEET_REPOS.get(model_name)
        if repo is None:
            raise ValueError(f"Unknown Parakeet model: {model_name}")
        weights = _fetch_weights(repo, model_name, local_files_only)

        def session(provider: str):
            return onnx_asr.load_model(f"nemo-{model_name}", path=str(weights),
                                       quantization="int8", providers=[provider])

        ladder = _provider_ladder(device, _allowed_runtimes("parakeet"))
        last: Exception | None = None
        for provider in ladder:
            try:
                # The #38 rule: an ONNX session builds on a provider whose
                # kernels are missing, and only the first encode finds out.
                # The catch is that the probe has to run on a session we then
                # throw away. ONNX Runtime derives a session's execution plan
                # from the first inference it ever does, and a probe-shaped
                # one leaves it ~3.5x slower for every real dictation after
                # (0.44 s vs 0.12 s measured — see the research doc §4).
                # Proving a provider works and keeping the session that proved
                # it turn out to be two different things; the second load is
                # what that costs, once, on a background thread.
                probe = session(provider)
                probe.recognize(np.zeros(1600, dtype=np.float32), sample_rate=16_000)
                del probe
                model = session(provider)
            except Exception as exc:
                last = exc
                log.warning("Parakeet unusable on %s; trying the next provider",
                            provider, exc_info=True)
                continue
            self.model = model
            self.device = _landed_runtime(model, provider)
            return
        raise RuntimeError(f"Parakeet could not start on any provider: {last}") from last

    def transcribe(self, audio: np.ndarray, sample_rate: int,
                   hotwords: str | None = None) -> str:
        # hotwords is accepted and dropped: see supports_hotwords. Rejecting it
        # would make the pipeline engine-aware for no gain.
        if audio.size == 0:
            return ""
        return self.model.recognize(audio, sample_rate=sample_rate).strip()


# What faster-whisper asks its own `snapshot_download` for. Mirrored rather
# than imported because `download_model` hardcodes them inside its body — and
# mirrored *safely* because a pattern we miss is a file the engine's own load
# then downloads, silently, exactly as it did before the prefetch existed.
_WHISPER_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin",
                     "tokenizer.json", "vocabulary.*"]


def _parakeet_dir(model_name: str) -> Path:
    return MODELS_DIR / "parakeet" / model_name


def prefetch(model_name: str, download: downloads.Download) -> bool:
    """Pull a speech model's weights down where the fetch can be watched and
    stopped, into the very place the engine will look for them.

    Both engines download on construction, and neither can be watched doing it:
    faster-whisper passes `tqdm_class=disabled_tqdm` and Parakeet's fetch is one
    opaque `snapshot_download`. Prefetching moves that work in front of the
    load, where a progress reading and a Cancel button can reach it (#114,
    #115); the load that follows then finds everything on disk.

    Returns False for a model the registry has no repo for — config.json is
    hand-editable, and the answer to a name we don't recognise is to let the
    engine download it the old silent way, not to fail the load.
    """
    model = models.speech_model(model_name)
    if model is None:
        log.info("no repo for %r; leaving the download to the engine", model_name)
        return False
    if model.engine == "parakeet":
        downloads.fetch(model.hf_repo, _WEIGHT_PATTERNS, download,
                        local_dir=_parakeet_dir(model_name))
    else:
        downloads.fetch(model.hf_repo, _WHISPER_PATTERNS, download,
                        cache_dir=MODELS_DIR)
    return True


def _fetch_weights(repo: str, model_name: str, local_files_only: bool) -> Path:
    """The int8 files, under MODELS_DIR rather than the Hugging Face cache.

    Raises when `local_files_only` and they aren't there yet — which is the
    signal app._load_stt turns into the disclosed-download toast.
    """
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(
        repo_id=repo,
        allow_patterns=_WEIGHT_PATTERNS,
        local_dir=str(_parakeet_dir(model_name)),
        local_files_only=local_files_only,
    ))


# The registry, so dispatch and "what is this engine capable of" cannot drift
# apart. settings.py reads the classes' own `supports_hotwords` off it rather
# than keeping a second table of the same fact.
ENGINES: dict[str, type] = {
    "faster-whisper": FasterWhisperEngine,
    "parakeet": ParakeetEngine,
}


def make_engine(name: str, model: str, device: str,
                local_files_only: bool = False) -> SttEngine:
    engine = ENGINES.get(name)
    if engine is None:
        raise ValueError(f"Unknown STT engine: {name}")
    return engine(model, device, local_files_only=local_files_only)
