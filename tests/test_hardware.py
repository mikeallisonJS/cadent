"""Hardware detection and the model suggestion table (spec §6.2).

The table is a pure function over (vram, ram, cores) precisely so it can be
tested without the machine it describes. Detection itself is best-effort by
design: `cuInit` failing means "don't suggest GPU models", which is the right
answer rather than an error.
"""

import pytest
from conftest import FakeHardwareProbe

from cadent import hardware, platform


def suggest(vram=None, ram=16.0, cores=8, dx12=False, metal=False):
    return hardware.suggest_model(vram_gb=vram, ram_gb=ram, physical_cores=cores,
                                  dx12_gpu=dx12, metal_gpu=metal)


# ---- the darwin branch (spec §4.3, measured on the M1 Max) -----------------

def test_apple_silicon_gets_parakeet_at_sixteen_gigs():
    """Parakeet v2 wears the Apple-Silicon Recommended chip at RAM ≥ 16 GB:
    varied-length median 0.30 s on the CPU EP, well under the ~1 s bar
    (docs/research/macos-bench-m1max.md)."""
    assert suggest(metal=True, ram=16.0).model == "parakeet-tdt-0.6b-v2"
    assert suggest(metal=True, ram=32.0, cores=10).model == "parakeet-tdt-0.6b-v2"


def test_apple_silicon_below_the_ram_bar_gets_distil_small():
    """distil-small.en is the sub-second Whisper fallback (0.78 s median);
    distil-medium/large sit above the 1 s line and are never darwin defaults
    — even on a core count that would earn distil-medium on the win32 rows."""
    assert suggest(metal=True, ram=8.0).model == "distil-small.en"
    assert suggest(metal=True, ram=15.9, cores=10).model == "distil-small.en"


# ---- the table, top-down, first match wins --------------------------------

def test_a_big_gpu_gets_distil_large():
    assert suggest(vram=8.0).model == "distil-large-v3"


def test_a_mid_gpu_gets_distil_medium():
    assert suggest(vram=4.0).model == "distil-medium.en"
    assert suggest(vram=5.9).model == "distil-medium.en"


def test_large_v3_is_never_auto_suggested():
    """~6x the latency of distil-large-v3 for +1.3 short-form WER. It stays a
    manual "accuracy over latency" pick."""
    for vram in (None, 4.0, 6.0, 24.0):
        assert suggest(vram=vram).model != "large-v3"


def test_a_small_gpu_falls_through_to_the_cpu_rows():
    """Under 4 GB the GPU tier can't hold fp16 weights plus ~1.4 GB of runtime
    VRAM and compositor headroom, so the CPU rows decide."""
    assert suggest(vram=2.0, ram=32.0, cores=16).model == "distil-medium.en"
    assert suggest(vram=2.0, ram=8.0, cores=4).model == "distil-small.en"


def test_low_ram_gets_the_small_models():
    assert suggest(ram=7.0).model == "base.en"
    assert suggest(ram=5.0).model == "tiny.en"


def test_low_ram_beats_a_high_core_count():
    """Row 4 is above row 5 in the table: cores don't buy memory."""
    assert suggest(ram=7.0, cores=32).model == "base.en"


def test_a_big_cpu_box_gets_distil_medium():
    assert suggest(ram=16.0, cores=8).model == "distil-medium.en"
    assert suggest(ram=64.0, cores=24).model == "distil-medium.en"


def test_a_big_cpu_box_needs_both_ram_and_cores():
    assert suggest(ram=16.0, cores=6).model == "distil-small.en"
    assert suggest(ram=12.0, cores=16).model == "distil-small.en"


def test_everything_else_gets_the_current_default():
    assert suggest(ram=12.0, cores=4).model == "distil-small.en"


def test_every_suggestion_carries_a_one_line_reason():
    for kwargs in ({"vram": 8.0}, {"vram": 4.0}, {"ram": 5.0}, {"ram": 7.0},
                   {"ram": 32.0, "cores": 16}, {"ram": 12.0, "cores": 4}):
        suggestion = suggest(**kwargs)
        assert suggestion.reason
        assert "\n" not in suggestion.reason


def test_every_suggested_model_is_one_settings_offers():
    from cadent import settings

    for kwargs in ({"vram": 8.0}, {"vram": 4.0}, {"ram": 5.0}, {"ram": 7.0},
                   {"ram": 32.0, "cores": 16}, {"ram": 12.0, "cores": 4},
                   {"vram": 8.0, "dx12": True}):
        suggestion = suggest(**kwargs)
        assert suggestion.model in settings.model_choices(engine_of(suggestion))


# ---- the Parakeet row, above the rest (#72) --------------------------------

def engine_of(suggestion):
    """The model name is what carries the engine — see `engine_for_model`."""
    from cadent import settings

    return settings.engine_for_model(suggestion.model)


def test_a_dx12_gpu_with_real_vram_gets_parakeet():
    suggestion = suggest(vram=24.0, dx12=True)
    assert engine_of(suggestion) == "parakeet"
    # v2, the English-only checkpoint: measured 2.87% WER against v3's 4.35%.
    assert suggestion.model == "parakeet-tdt-0.6b-v2"


def test_parakeet_is_not_suggested_without_a_direct3d_12_device():
    """DirectML is the runtime; no D3D12 means no runtime, whatever the
    NVIDIA driver says."""
    assert engine_of(suggest(vram=24.0, dx12=False)) == "faster-whisper"


def test_parakeet_is_not_suggested_on_a_gpu_too_small_to_hold_it():
    assert engine_of(suggest(vram=2.0, dx12=True)) == "faster-whisper"


def test_parakeet_is_not_suggested_when_vram_cannot_be_measured():
    """`cuda_total_memory` is NVIDIA-only, so an AMD or Intel GPU reports
    None. Parakeet stays selectable there — just not recommended sight
    unseen, because an integrated GPU is a bad surprise for a 670 MB
    download."""
    assert engine_of(suggest(vram=None, dx12=True)) == "faster-whisper"


def test_every_suggestion_names_an_engine_that_exists():
    from cadent.config import STT_ENGINES

    for kwargs in ({"vram": 8.0}, {"vram": 8.0, "dx12": True}, {"ram": 5.0},
                   {"ram": 12.0, "cores": 4}):
        assert engine_of(suggest(**kwargs)) in STT_ENGINES


def test_the_dx12_probe_answers_without_raising():
    assert platform.current().hardware.dx12_gpu_present() in (True, False)


def test_detection_reports_whether_directml_has_anything_to_run_on():
    assert hardware.detect(FakeHardwareProbe(dx12=True)).dx12_gpu is True


# ---- detection is best-effort, and its failure mode is the right answer ----

def test_a_thrown_probe_falls_back_to_the_current_default(monkeypatch):
    def boom():
        raise OSError("no such device")

    monkeypatch.setattr(hardware, "detect", boom)
    assert hardware.suggest_for_this_machine().model == "distil-small.en"


def test_a_machine_with_no_gpu_is_described_without_one():
    detected = hardware.detect(FakeHardwareProbe())
    assert detected.vram_gb is None
    assert detected.ram_gb > 0
    assert detected.physical_cores >= 1


def test_detection_is_probed_once_and_cached(machine):
    probe = machine()
    hardware.reset_cache()
    hardware.detect_cached()
    hardware.detect_cached()
    assert probe.cuda_probes == 1
    hardware.reset_cache()


def test_the_gpu_pack_page_is_offered_when_the_driver_is_there_but_unused():
    """If the driver is present, VRAM >= 4 GB and the pack isn't installed,
    the pack page comes *before* the model page — accepting it changes the
    recommendation (§6.2)."""
    assert hardware.should_offer_gpu_page(driver_present=True, vram_gb=6.0,
                                          pack_installed=False) is True
    assert hardware.should_offer_gpu_page(driver_present=True, vram_gb=6.0,
                                          pack_installed=True) is False
    assert hardware.should_offer_gpu_page(driver_present=True, vram_gb=2.0,
                                          pack_installed=False) is False
    assert hardware.should_offer_gpu_page(driver_present=False, vram_gb=None,
                                          pack_installed=False) is False


def test_the_gpu_pack_page_is_skipped_when_parakeet_is_what_we_will_suggest():
    """The page exists because accepting the pack changes the recommendation.
    It changes nothing for a machine about to be recommended Parakeet, which
    never loads a cuBLAS DLL — so asking for 550 MB there is the same placebo
    `gpu_pack.should_offer` refuses to serve, one page earlier (#72)."""
    assert hardware.should_offer_gpu_page(
        driver_present=True, vram_gb=24.0, pack_installed=False,
        suggested_engine="parakeet") is False
    assert hardware.should_offer_gpu_page(
        driver_present=True, vram_gb=24.0, pack_installed=False,
        suggested_engine="faster-whisper") is True


def test_cuda_probe_answers_or_returns_none():
    """It talks to a real driver or it doesn't; either way it never raises."""
    result = platform.current().hardware.cuda_total_memory()
    assert result is None or result > 0


def test_an_existing_install_is_recognised_by_any_model_on_disk(tmp_path):
    """faster-whisper stores models under Hugging Face's cache naming, so
    matching the config value against a directory name would be a guess. The
    presence of a model.bin is a fact (§6.4)."""
    assert hardware.any_speech_model_downloaded(tmp_path) is False
    snapshot = (tmp_path / "models--Systran--faster-distil-whisper-small.en"
                / "snapshots" / "abc123")
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"weights")
    assert hardware.any_speech_model_downloaded(tmp_path) is True


def test_a_parakeet_install_also_counts_as_an_existing_install(tmp_path):
    """Parakeet's weights are ONNX, not a ctranslate2 model.bin — so a user
    who only ever downloaded Parakeet must not be sent back through the
    wizard (#72)."""
    weights = tmp_path / "parakeet" / "parakeet-tdt-0.6b-v3"
    weights.mkdir(parents=True)
    (weights / "encoder-model.int8.onnx").write_bytes(b"onnx")
    assert hardware.any_speech_model_downloaded(tmp_path) is True


def test_a_cleanup_llm_alone_is_not_a_speech_model(tmp_path):
    llm = tmp_path / "llm"
    llm.mkdir()
    (llm / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf").write_bytes(b"gguf")
    assert hardware.any_speech_model_downloaded(tmp_path) is False


def test_a_missing_models_dir_is_not_an_existing_install(tmp_path):
    assert hardware.any_speech_model_downloaded(tmp_path / "nope") is False


@pytest.mark.parametrize("gb", [3.99, 4.0, 5.99, 6.0])
def test_the_vram_thresholds_are_inclusive_at_the_stated_numbers(gb):
    expected = ("distil-large-v3" if gb >= 6 else
                "distil-medium.en" if gb >= 4 else "distil-medium.en")
    assert suggest(vram=gb, ram=32.0, cores=16).model == expected
