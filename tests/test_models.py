"""The one model registry (#111, #112).

Table-driven on purpose: the failure these guard against is an entry added
with a repo that doesn't exist, a size nobody filled in, or an id the config
parser rejects — none of which a pixel test would catch, and all of which
strand the user at a download that 404s.
"""

import pytest

from cadent import config, models, settings, stt
from cadent.config import Config

# ---- the speech table ------------------------------------------------------


def test_every_listed_speech_model_says_what_it_is():
    """Two lines and a size, for every row the user can see."""
    for model in models.listed_speech_models():
        assert model.tier
        assert model.name
        assert model.blurb
        assert model.size


def test_every_speech_id_survives_a_config_round_trip():
    for model in models.SPEECH_MODELS:
        cfg, issues = config.parse({"stt_engine": model.engine,
                                    "stt_model": model.id})
        assert cfg.stt_model == model.id
        assert cfg.stt_engine == model.engine
        assert issues == []


def test_the_engine_is_derived_from_the_model():
    """Engine is an implementation detail of the pick, which is the whole
    reason the two pickers merged."""
    for model in models.SPEECH_MODELS:
        assert models.engine_for_model(model.id) == model.engine
        assert settings.engine_for_model(model.id) == model.engine


def test_an_unknown_model_derives_the_default_engine():
    assert models.engine_for_model("whisper-from-the-future") == \
        settings.DEFAULT_STT_ENGINE


def test_the_listed_rows_are_the_ticket_s_six():
    assert [m.id for m in models.listed_speech_models()] == [
        "tiny.en", "distil-small.en", "distil-medium.en", "distil-large-v3",
        "parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3",
    ]


def test_the_older_whisper_sizes_stay_loadable_but_unlisted():
    """`small.en` and friends are what an existing config.json may name; they
    still have to load, they just don't need a row of their own."""
    unlisted = [m.id for m in models.SPEECH_MODELS if not m.listed]
    assert unlisted == ["base.en", "small.en", "medium.en", "large-v3"]
    for name in unlisted:
        assert name in settings.model_choices("faster-whisper")


def test_settings_model_choices_still_reads_off_the_registry():
    """The wrapper stays so existing call sites don't move (#111)."""
    assert settings.model_choices("parakeet") == (
        "parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3")
    assert Config().stt_model in settings.model_choices("faster-whisper")


def test_the_parakeet_repo_table_is_read_off_the_registry():
    """Derived, not restated — the same rule `cleanup.GGUF_REPOS` follows."""
    assert {
        m.id: m.hf_repo for m in models.speech_models("parakeet")} == stt.PARAKEET_REPOS


def test_every_speech_row_says_where_it_comes_from():
    """A row with no repo is a row nothing can prefetch, and prefetching is
    what buys progress and a working Cancel (#114, #115)."""
    for model in models.SPEECH_MODELS:
        assert model.hf_repo, model.id


def test_the_whisper_repos_match_faster_whisper_s_own_table():
    """faster-whisper hardcodes `tqdm_class=disabled_tqdm`, so a download it
    owns can never be watched or stopped. Cadent therefore fetches the
    weights itself into the very cache faster-whisper reads — which means
    holding a second copy of its size→repo map. A mirrored table is only safe
    while something checks it, so this is that check."""
    from faster_whisper.utils import _MODELS

    for model in models.speech_models("faster-whisper"):
        assert model.hf_repo == _MODELS[model.id], model.id


def test_a_title_is_the_tier_then_the_googleable_name():
    tiny = models.speech_model("tiny.en")
    assert tiny.title == "Fastest — Whisper Tiny"
    assert tiny.subtitle == "Least accurate. For older or slower PCs.  ·  78 MB"
    # Both tables read the same way, which is why the two lines live on the
    # models rather than in whichever surface renders them.
    assert models.CLEANUP_MODELS[0].title == "Fastest — Llama 3.2 1B"


def test_display_names_drop_the_distil_prefix_the_config_still_holds():
    """`distil-small.en` is a Hugging Face repo name, not a product name."""
    small = models.speech_model("distil-small.en")
    assert small.id == "distil-small.en"
    assert "distil" not in small.title.lower()


# ---- what wears the "Recommended" chip --------------------------------------


def test_a_suggested_model_that_is_listed_wears_the_chip_itself():
    assert models.recommended_speech_model("parakeet-tdt-0.6b-v2") == \
        "parakeet-tdt-0.6b-v2"
    assert models.recommended_speech_model("distil-small.en") == "distil-small.en"


def test_a_suggestion_the_list_does_not_show_falls_to_its_listed_neighbour():
    """`suggest_model` steps *down* on a small machine, so the stand-in has to
    step down too — rounding up would spend RAM it just decided isn't there."""
    assert models.recommended_speech_model("base.en") == "tiny.en"
    assert models.recommended_speech_model("large-v3") == "distil-large-v3"


def test_every_suggestion_the_hardware_table_can_make_lands_on_a_listed_row():
    from cadent import hardware

    sweep = [(None, 4.0, 2), (None, 7.0, 4), (None, 16.0, 8), (None, 16.0, 4),
             (4.0, 16.0, 8), (8.0, 32.0, 16)]
    listed = {m.id for m in models.listed_speech_models()}
    for vram, ram, cores in sweep:
        for dx12 in (False, True):
            suggested = hardware.suggest_model(vram, ram, cores, dx12).model
            assert models.recommended_speech_model(suggested) in listed


# ---- the cleanup table -----------------------------------------------------


def test_there_are_four_rungs_smallest_first():
    assert [m.name for m in models.CLEANUP_MODELS] == [
        "Llama 3.2 1B", "Qwen3 1.7B", "Llama 3.2 3B", "Qwen3 4B"]
    sizes = [m.size_gb for m in models.CLEANUP_MODELS]
    assert sizes == sorted(sizes)


def test_every_cleanup_rung_names_a_well_formed_repo_and_a_gguf():
    """The Qwen3 1.7B entry shipped untested; a typo here is a 404 mid-toast."""
    for model in models.CLEANUP_MODELS:
        owner, _, name = model.hf_repo.partition("/")
        assert owner and name and "/" not in name
        assert model.id.endswith(".gguf")
        assert model.tier and model.blurb and model.size
        assert model.params_b > 0 and model.size_gb > 0


def test_the_stored_default_is_still_the_best_quality_rung():
    assert models.cleanup_model(Config().llm_model_path) is models.CLEANUP_MODELS[-1]


def test_only_the_hybrid_thinking_rung_asks_for_no_think():
    """Qwen3-4B-Instruct-2507 is non-thinking by design; neither Llama thinks."""
    assert [m.name for m in models.CLEANUP_MODELS if m.no_think] == ["Qwen3 1.7B"]


def test_a_cleanup_model_is_found_by_a_full_path_too():
    model = models.CLEANUP_MODELS[0]
    assert models.cleanup_model(f"C:/anywhere/llm/{model.id}") is model
    assert models.cleanup_model("C:/mine/custom-finetune.gguf") is None


# ---- the hardware rules (unmeasured, and they say so) -----------------------

ROOMY = {"ram_gb": 32.0, "physical_cores": 16}


def test_a_roomy_machine_gets_no_warnings_and_the_top_rung():
    for model in models.CLEANUP_MODELS:
        assert models.cleanup_warning(model, **ROOMY) == ""
    assert models.recommended_cleanup(**ROOMY) == models.CLEANUP_MODELS[-1].id


def test_an_eight_gig_box_is_warned_off_the_two_heavy_rungs():
    """Expressed as free-RAM-after-load: llama.cpp holds the whole model
    resident, so that is the quantity that decides whether the box swaps."""
    warnings = [models.cleanup_warning(m, ram_gb=8.0, physical_cores=8)
                for m in models.CLEANUP_MODELS]
    assert warnings == ["", "", models.NOT_ENOUGH_MEMORY, models.NOT_ENOUGH_MEMORY]
    assert models.recommended_cleanup(ram_gb=8.0, physical_cores=8) == \
        "Qwen3-1.7B-Q4_K_M.gguf"


def test_a_four_core_box_is_warned_off_the_big_models_it_can_still_hold():
    warnings = [models.cleanup_warning(m, ram_gb=32.0, physical_cores=4)
                for m in models.CLEANUP_MODELS]
    assert warnings == ["", "", models.MAY_BE_SLOW, models.MAY_BE_SLOW]
    assert models.recommended_cleanup(ram_gb=32.0, physical_cores=4) == \
        "Qwen3-1.7B-Q4_K_M.gguf"


def test_metal_retires_the_core_gate_but_not_the_ram_test():
    """ADR 0003 (#146): MAY_BE_SLOW's physical-core gate mispredicts once
    Metal does the work — measured, not hedged: the 4B tier warm-cleans in
    0.15/0.42/1.3 s on the M1 Max — so on a Metal machine no rung wears it,
    whatever the core count. The RAM-headroom test stays: unified memory
    makes it more honest, the GPU can't page its way out of a too-big model."""
    warnings = [models.cleanup_warning(m, ram_gb=32.0, physical_cores=4,
                                       metal_gpu=True)
                for m in models.CLEANUP_MODELS]
    assert warnings == ["", "", "", ""]
    assert models.recommended_cleanup(ram_gb=32.0, physical_cores=4,
                                      metal_gpu=True) == \
        models.CLEANUP_MODELS[-1].id
    warnings = [models.cleanup_warning(m, ram_gb=8.0, physical_cores=8,
                                       metal_gpu=True)
                for m in models.CLEANUP_MODELS]
    assert warnings == ["", "", models.NOT_ENOUGH_MEMORY,
                        models.NOT_ENOUGH_MEMORY]


def test_not_enough_memory_outranks_may_be_slow():
    """One chip per row, and the one that means "don't" wins."""
    assert models.cleanup_warning(models.CLEANUP_MODELS[-1],
                                  ram_gb=8.0, physical_cores=4) == \
        models.NOT_ENOUGH_MEMORY


@pytest.mark.parametrize("ram_gb", [4.0, 6.0, 6.5])
def test_a_machine_too_small_for_any_rung_still_recommends_the_smallest(ram_gb):
    """A dropdown with nothing recommended is a dropdown that has given up."""
    recommended = models.recommended_cleanup(ram_gb=ram_gb, physical_cores=2)
    assert recommended == models.CLEANUP_MODELS[0].id


def test_the_recommendation_walks_down_as_the_machine_shrinks():
    ladder = [models.recommended_cleanup(ram_gb=ram, physical_cores=cores)
              for ram, cores in ((32.0, 16), (16.0, 8), (8.0, 8), (6.0, 4))]
    order = [m.id for m in models.CLEANUP_MODELS]
    assert [order.index(pick) for pick in ladder] == \
        sorted([order.index(pick) for pick in ladder], reverse=True)


def test_the_thresholds_are_labelled_as_unmeasured():
    """Both numbers are starting points, not findings — the module has to say
    so where the next person will read it."""
    assert "unmeasured" in models.__doc__.lower() or \
        "unvalidated" in models.__doc__.lower()
