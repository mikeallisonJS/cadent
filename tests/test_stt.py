"""STT engine construction: models must live under the app's own data dir."""

import sys
import types

import pytest
from conftest import pin_one_rung_platform

from cadent import config as cfg
from cadent import downloads, stt
from cadent.stt import make_engine


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    """The provider ladder is filtered to the platform's runtime column
    (#146); these tests describe the win32 shape (DML leads `auto`), so pin
    it. Darwin-column behavior pins one-rung capabilities explicitly."""


class FakeWhisperModel:
    calls: list[dict] = []
    fail_devices: set[str] = set()
    lazy_fail_devices: set[str] = set()   # fail at first encode, like a broken CUDA runtime

    def __init__(self, model_name, **kwargs):
        FakeWhisperModel.calls.append({"model_name": model_name, **kwargs})
        self.device = kwargs.get("device")
        if self.device in FakeWhisperModel.fail_devices:
            raise RuntimeError(f"no {self.device}")

    def transcribe(self, audio, **kwargs):
        def segments():
            if self.device in FakeWhisperModel.lazy_fail_devices:
                raise RuntimeError("Library cublas64_12.dll is not found")
            yield from ()

        return segments(), None


@pytest.fixture
def fake_whisper(monkeypatch):
    FakeWhisperModel.calls = []
    FakeWhisperModel.fail_devices = set()
    FakeWhisperModel.lazy_fail_devices = set()
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return FakeWhisperModel


def test_models_download_into_app_data_dir(fake_whisper):
    make_engine("faster-whisper", "small.en", "cpu")
    (call,) = fake_whisper.calls
    assert call["download_root"] == str(cfg.MODELS_DIR)


def test_auto_falls_back_when_cuda_fails_at_first_encode(fake_whisper):
    """#38: WhisperModel(device="cuda") can construct fine on a machine with no
    loadable CUDA runtime — CTranslate2 loads cuBLAS lazily, so the failure
    only surfaces at the first encode. `auto` must probe with a real encode
    before committing to CUDA."""
    fake_whisper.lazy_fail_devices = {"cuda"}
    make_engine("faster-whisper", "small.en", "auto")
    assert [c["device"] for c in fake_whisper.calls] == ["cuda", "cpu"]


def test_download_root_passed_on_cuda_fallback_too(fake_whisper):
    fake_whisper.fail_devices = {"cuda"}
    make_engine("faster-whisper", "small.en", "auto")
    assert [c["device"] for c in fake_whisper.calls] == ["cuda", "cpu"]
    assert all(c["download_root"] == str(cfg.MODELS_DIR) for c in fake_whisper.calls)


def test_local_files_only_passed_through(fake_whisper):
    make_engine("faster-whisper", "small.en", "cpu", local_files_only=True)
    (call,) = fake_whisper.calls
    assert call["local_files_only"] is True


def test_engine_reports_cuda_when_probe_succeeds(fake_whisper):
    """The GPU support-pack offer (#55) keys off where the engine landed."""
    engine = make_engine("faster-whisper", "small.en", "auto")
    assert engine.device == "cuda"


def test_engine_reports_cpu_after_cuda_fallback(fake_whisper):
    fake_whisper.lazy_fail_devices = {"cuda"}
    engine = make_engine("faster-whisper", "small.en", "auto")
    assert engine.device == "cpu"


def test_unknown_engine_rejected():
    with pytest.raises(ValueError):
        make_engine("kaldi", "x", "cpu")


# ---- vocabulary biasing (M2 ticket 09) ------------------------------------

class FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        return FakeEncoding(list(range(len(text.split()))))


def test_transcribe_passes_hotwords_through(fake_whisper):
    import numpy as np

    fake_whisper.transcribe_calls = []

    def transcribe(self, audio, **kwargs):
        FakeWhisperModel.transcribe_calls.append(kwargs)
        return [], None

    fake_whisper.transcribe = transcribe
    engine = make_engine("faster-whisper", "small.en", "cpu")
    engine.transcribe(np.ones(16, dtype=np.float32), 16_000, hotwords="Kubernetes, Allison")
    (call,) = fake_whisper.transcribe_calls
    assert call["hotwords"] == "Kubernetes, Allison"


def test_count_tokens_uses_model_tokenizer(fake_whisper):
    fake_whisper.hf_tokenizer = FakeTokenizer()
    engine = make_engine("faster-whisper", "small.en", "cpu")
    assert engine.count_tokens("alpha beta gamma") == 3


def test_faster_whisper_declares_hotword_support(fake_whisper):
    assert make_engine("faster-whisper", "small.en", "cpu").supports_hotwords is True


# ---- Parakeet (#72) -------------------------------------------------------

class FakeOrtSession:
    """Just enough of onnxruntime.InferenceSession: the provider list the
    session actually built with, which is the only honest `landed_on` source
    (#137 — a session can construct "successfully" on a provider ORT then
    quietly replaces with the CPU EP)."""

    def __init__(self, providers):
        self._providers = providers

    def get_providers(self):
        return self._providers


class FakeAsrModel:
    def __init__(self, providers):
        self.providers = providers
        self.probed = False
        # The real load_model returns an adapter holding the model, which
        # holds its ORT sessions; ORT appends the CPU EP to every session it
        # builds unless the lie knob says otherwise.
        landed = FakeOnnxAsr.session_lands_on.get(
            providers[0], providers + ["CPUExecutionProvider"]
            if "CPUExecutionProvider" not in providers else providers)
        self.asr = types.SimpleNamespace(_encoder=FakeOrtSession(landed))

    def recognize(self, audio, sample_rate=16_000):
        provider = self.providers[0]
        if provider in FakeOnnxAsr.probe_fail_providers:
            raise RuntimeError(f"{provider} is unusable")
        if audio.size <= 1600:
            self.probed = True
        return "  Hello there.  "


class FakeOnnxAsr:
    """Stands in for onnx_asr + huggingface_hub. CI has no GPU and no weights."""

    calls: list[dict] = []
    sessions: list = []
    downloads: list[dict] = []
    probe_fail_providers: set[str] = set()   # fail at first encode, not at load
    load_fail_providers: set[str] = set()    # fail at session construction
    missing_locally: bool = False            # weights not on disk yet
    # requested provider -> what the session reports it actually built with;
    # the macOS DML lie is {"DmlExecutionProvider": ["CPUExecutionProvider"]}.
    session_lands_on: dict[str, list[str]] = {}

    @staticmethod
    def load_model(name, path=None, *, quantization=None, providers=None, **kw):
        FakeOnnxAsr.calls.append({"name": name, "path": path,
                                  "quantization": quantization,
                                  "providers": list(providers or [])})
        if providers and providers[0] in FakeOnnxAsr.load_fail_providers:
            raise RuntimeError(f"no {providers[0]}")
        model = FakeAsrModel(list(providers or []))
        FakeOnnxAsr.sessions.append(model)
        return model

    @staticmethod
    def snapshot_download(repo_id, **kw):
        FakeOnnxAsr.downloads.append({"repo_id": repo_id, **kw})
        if FakeOnnxAsr.missing_locally and kw.get("local_files_only"):
            raise OSError("weights are not cached locally")
        return str(kw.get("local_dir") or "/models/parakeet")


@pytest.fixture
def fake_parakeet(monkeypatch):
    FakeOnnxAsr.calls = []
    FakeOnnxAsr.sessions = []
    FakeOnnxAsr.downloads = []
    FakeOnnxAsr.probe_fail_providers = set()
    FakeOnnxAsr.load_fail_providers = set()
    FakeOnnxAsr.missing_locally = False
    FakeOnnxAsr.session_lands_on = {}
    onnx_asr = types.ModuleType("onnx_asr")
    onnx_asr.load_model = FakeOnnxAsr.load_model
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = FakeOnnxAsr.snapshot_download
    monkeypatch.setitem(sys.modules, "onnx_asr", onnx_asr)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return FakeOnnxAsr


def _providers(fake):
    """The providers tried, in order. A successful rung builds two sessions —
    one to probe and throw away, one to keep — so consecutive repeats collapse.
    """
    tried = [c["providers"][0] for c in fake.calls]
    return [p for i, p in enumerate(tried) if i == 0 or tried[i - 1] != p]


def test_parakeet_weights_land_under_the_app_data_dir(fake_parakeet):
    """Never the Hugging Face default cache — the same rule the whisper models
    follow via download_root."""
    make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    (download,) = fake_parakeet.downloads
    assert download["repo_id"] == "istupakov/parakeet-tdt-0.6b-v3-onnx"
    assert str(download["local_dir"]).startswith(str(cfg.MODELS_DIR))


def test_parakeet_auto_prefers_directml(fake_parakeet):
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "auto")
    assert _providers(fake_parakeet) == ["DmlExecutionProvider"]
    assert engine.device == "directml"


def test_parakeet_auto_falls_back_when_the_gpu_fails_at_first_encode(fake_parakeet):
    """#38 again, one library down: an ONNX session builds fine on a provider
    whose kernels only fail at the first encode. Commit after a real encode."""
    fake_parakeet.probe_fail_providers = {"DmlExecutionProvider"}
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "auto")
    assert _providers(fake_parakeet) == ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert engine.device == "cpu"


def test_parakeet_auto_falls_back_when_the_session_wont_build(fake_parakeet):
    fake_parakeet.load_fail_providers = {"DmlExecutionProvider"}
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "auto")
    assert _providers(fake_parakeet) == ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert engine.device == "cpu"


def test_parakeet_falls_back_to_its_own_cpu_provider_not_to_whisper(fake_parakeet):
    """The ticket assumed Parakeet had no CPU story; the measured 0.74 s says
    otherwise, so a failed probe keeps the engine the user chose."""
    fake_parakeet.probe_fail_providers = {"DmlExecutionProvider"}
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "auto")
    assert type(engine).__name__ == "ParakeetEngine"


def test_parakeet_cpu_never_touches_a_gpu_provider(fake_parakeet):
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    assert _providers(fake_parakeet) == ["CPUExecutionProvider"]
    assert engine.device == "cpu"


def test_parakeet_cuda_is_selectable_but_never_automatic(fake_parakeet):
    """CUDA measured slower than DirectML and would cost a ~1.9 GB pack tier,
    so it is offered only on an explicit ask."""
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cuda")
    assert _providers(fake_parakeet) == ["CUDAExecutionProvider"]
    assert engine.device == "cuda"


def test_parakeet_gives_up_when_even_the_cpu_provider_fails(fake_parakeet):
    fake_parakeet.probe_fail_providers = {"DmlExecutionProvider", "CPUExecutionProvider"}
    with pytest.raises(RuntimeError):
        make_engine("parakeet", "parakeet-tdt-0.6b-v3", "auto")


def test_parakeet_local_files_only_passed_to_the_download(fake_parakeet):
    make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu", local_files_only=True)
    (download,) = fake_parakeet.downloads
    assert download["local_files_only"] is True


def test_parakeet_missing_weights_raise_so_the_app_can_disclose_the_download(fake_parakeet):
    """app._load_stt tries local_files_only first and shows the disclosure
    toast on the failure — so the failure has to actually happen."""
    fake_parakeet.missing_locally = True
    with pytest.raises(OSError):
        make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu", local_files_only=True)


def test_parakeet_never_dictates_through_the_session_it_probed(fake_parakeet):
    """ONNX Runtime derives a session's execution plan from the first
    inference it ever runs, and a 0.1 s probe leaves it ~3.5x slower for every
    real dictation after (0.44 s vs 0.12 s measured). Prove the provider on a
    session, then throw that session away."""
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    probed, kept = fake_parakeet.sessions
    assert probed.probed is True
    assert kept.probed is False
    assert engine.model is kept


def test_parakeet_one_rung_platform_never_asks_for_dml(fake_parakeet, monkeypatch):
    """Spec §4.1, load-bearing: ORT on macOS accepts a DmlExecutionProvider
    request, warns, and silently constructs the session on the CPU — the
    probe "succeeds" and lies. On a one-rung platform the DML rung must never
    be asked for at all."""
    pin_one_rung_platform(monkeypatch)
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v2", "auto")
    assert _providers(fake_parakeet) == ["CPUExecutionProvider"]
    assert engine.device == "cpu"


def test_parakeet_carried_over_gpu_runtimes_land_on_cpu_on_one_rung(
        fake_parakeet, monkeypatch):
    """A config value the sanitize pass hasn't seen yet ("directml", "cuda")
    still cannot reach a GPU provider on a one-rung platform."""
    pin_one_rung_platform(monkeypatch)
    for stray in ("directml", "cuda"):
        fake_parakeet.calls = []
        engine = make_engine("parakeet", "parakeet-tdt-0.6b-v2", stray)
        assert _providers(fake_parakeet) == ["CPUExecutionProvider"]
        assert engine.device == "cpu"


def test_parakeet_landed_on_comes_from_the_session_not_the_ladder(fake_parakeet):
    """#137's spec-bound finding, on both platforms: `landed_on` derives from
    `session.get_providers()`, never from which ladder entry didn't throw —
    a session that "succeeds" on a DML request while ORT quietly builds it on
    the CPU EP must report cpu."""
    fake_parakeet.session_lands_on = {
        "DmlExecutionProvider": ["CPUExecutionProvider"]}
    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v2", "auto")
    assert _providers(fake_parakeet) == ["DmlExecutionProvider"]
    assert engine.device == "cpu"


def test_whisper_auto_skips_the_cuda_probe_on_a_one_rung_platform(
        fake_whisper, monkeypatch):
    """ctranslate2 ships no Metal backend (ADR 0003): `auto` on a one-rung
    platform goes straight to the CPU instead of paying for a CUDA
    construction that can only fail."""
    pin_one_rung_platform(monkeypatch)
    engine = make_engine("faster-whisper", "distil-small.en", "auto")
    assert [c["device"] for c in fake_whisper.calls] == ["cpu"]
    assert engine.device == "cpu"


def test_parakeet_ships_int8_weights(fake_parakeet):
    """fp32 is ~2.5 GB for a difference a dictation microphone cannot show."""
    make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    assert fake_parakeet.calls[0]["quantization"] == "int8"


def test_parakeet_transcribes_and_strips(fake_parakeet):
    import numpy as np

    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    assert engine.transcribe(np.ones(16, dtype=np.float32), 16_000) == "Hello there."


def test_parakeet_ignores_hotwords_rather_than_failing_on_them(fake_parakeet):
    """No Parakeet equivalent of faster-whisper's hotwords; biasing falls to
    the layer-2 post-correction pass, and the engine says so."""
    import numpy as np

    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    assert engine.supports_hotwords is False
    assert engine.transcribe(np.ones(16, dtype=np.float32), 16_000,
                             hotwords="Kubernetes, Allison") == "Hello there."


def test_parakeet_empty_audio_short_circuits(fake_parakeet):
    import numpy as np

    engine = make_engine("parakeet", "parakeet-tdt-0.6b-v3", "cpu")
    assert engine.transcribe(np.zeros(0, dtype=np.float32), 16_000) == ""


def test_parakeet_rejects_an_unknown_checkpoint(fake_parakeet):
    with pytest.raises(ValueError):
        make_engine("parakeet", "distil-small.en", "cpu")


# ---- prefetching, so a download can be watched and stopped (#114, #115) -----


@pytest.fixture
def fetched(monkeypatch):
    """Every `downloads.fetch` the prefetch would have made."""
    calls = []

    def fake_fetch(repo, patterns, download, *, cache_dir=None, local_dir=None):
        calls.append({"repo": repo, "patterns": patterns,
                      "cache_dir": cache_dir, "local_dir": local_dir})

    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    return calls


def test_prefetching_whisper_warms_the_cache_the_engine_already_reads(fetched):
    """Not a second copy of the weights. `WhisperModel(download_root=...)`
    resolves against a Hugging Face cache under MODELS_DIR, and that is exactly
    what the prefetch fills — so a complete prefetch means the load that
    follows never touches the network, and a partial one costs nothing."""
    assert stt.prefetch("distil-small.en", downloads.Download()) is True

    (call,) = fetched
    assert call["repo"] == "Systran/faster-distil-whisper-small.en"
    assert call["cache_dir"] == cfg.MODELS_DIR
    assert call["local_dir"] is None
    assert "model.bin" in call["patterns"]


def test_prefetching_parakeet_targets_the_directory_the_engine_loads_from(fetched):
    assert stt.prefetch("parakeet-tdt-0.6b-v2", downloads.Download()) is True

    (call,) = fetched
    assert call["repo"] == "istupakov/parakeet-tdt-0.6b-v2-onnx"
    assert call["local_dir"] == \
        cfg.MODELS_DIR / "parakeet" / "parakeet-tdt-0.6b-v2"
    assert call["cache_dir"] is None


def test_prefetching_parakeet_leaves_the_fp32_export_where_it_is(fetched):
    """The v2 repo carries a 2.4 GB fp32 blob beside the 661 MB int8 one. The
    prefetch has to ask for the same weights `_fetch_weights` does, or it
    quietly triples the download."""
    stt.prefetch("parakeet-tdt-0.6b-v2", downloads.Download())

    (call,) = fetched
    assert call["patterns"] == stt._WEIGHT_PATTERNS


def test_a_model_the_registry_does_not_know_is_left_to_the_engine(fetched):
    """config.json is hand-editable, so a name we have no repo for is a normal
    thing to meet. Saying so lets the caller fall back to the engine's own
    download — silent, as it has always been — rather than failing the load
    over a progress bar."""
    assert stt.prefetch("some-fork-of-whisper", downloads.Download()) is False
    assert fetched == []
