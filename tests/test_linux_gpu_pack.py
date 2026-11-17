"""Linux runtimes (#38, ADR 0010): the two-edition GPU pack, the RTLD_LOCAL
preload, the driver probe and the Recommended chip's Linux branches."""

import ctypes
import dataclasses
import pathlib
import zipfile

import pytest
from conftest import make_platform

from cadent import gpu_pack, hardware, stt
from cadent import platform as platform_pkg
from cadent.platform import linux
from cadent.platform.linux.hardware import ProcHardware

WHISPER = gpu_pack.EDITIONS["cublas-linux"]
PARAKEET = gpu_pack.EDITIONS["cuda13-linux"]
R580, R570 = 13000, 12080


@pytest.fixture(autouse=True)
def linux_column(monkeypatch):
    plat = make_platform()
    caps = dataclasses.replace(
        plat.capabilities,
        stt_runtimes={"faster-whisper": ("auto", "cuda", "cpu"),
                      "parakeet": ("auto", "cuda", "cpu")},
        gpu_only_engines=frozenset(),
        gpu_pack_editions={"faster-whisper": "cublas-linux",
                           "parakeet": "cuda13-linux"},
        recommend_parakeet_on_cpu=True)
    monkeypatch.setattr(platform_pkg, "_current",
                        dataclasses.replace(plat, capabilities=caps))


# ---- the two editions ------------------------------------------------------

def test_each_engine_discloses_its_own_edition_and_size():
    assert gpu_pack.edition_for("faster-whisper") is WHISPER
    assert gpu_pack.edition_for("parakeet") is PARAKEET
    assert gpu_pack.download_size("faster-whisper") == "~600 MB"
    assert gpu_pack.download_size("parakeet") == "~1.3 GB"


def test_the_two_editions_do_not_share_a_file():
    assert not set(WHISPER.required) & set(PARAKEET.required)


def test_parakeet_edition_needs_an_r580_driver(tmp_path):
    cuda = tmp_path / "cuda"
    offer = dict(stt_engine="parakeet", driver_present=True)
    assert gpu_pack.should_offer("auto", "cpu", cuda, driver_cuda=R580, **offer)
    assert not gpu_pack.should_offer("auto", "cpu", cuda, driver_cuda=R570, **offer)
    assert not gpu_pack.should_offer("auto", "cpu", cuda, driver_cuda=None, **offer)


def test_pre_580_driver_gets_the_update_row_not_the_edition():
    assert gpu_pack.needs_driver_update("parakeet", True, R570)
    assert not gpu_pack.needs_driver_update("parakeet", True, R580)
    assert not gpu_pack.needs_driver_update("parakeet", False, None)
    assert not gpu_pack.needs_driver_update("faster-whisper", True, R570)
    assert "580" in gpu_pack.DRIVER_UPDATE_NOTE


def test_whisper_edition_is_offered_on_any_nvidia_driver(tmp_path):
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, driver_present=True,
                                 driver_cuda=R570)


def test_installing_one_edition_leaves_the_other_offerable(tmp_path):
    for name in WHISPER.required:
        (tmp_path / name).write_bytes(b"\x7fELF")
    assert not gpu_pack.should_offer("auto", "cpu", tmp_path, driver_present=True)
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, stt_engine="parakeet",
                                 driver_present=True, driver_cuda=R580)


def test_wizard_page_follows_the_suggested_engine_and_driver():
    page = hardware.should_offer_gpu_page
    assert page(True, 8.0, False, "parakeet", cuda_driver=R580)
    assert not page(True, 8.0, False, "parakeet", cuda_driver=R570)
    assert page(True, 8.0, False, "faster-whisper", cuda_driver=R570)


# ---- install and activation ------------------------------------------------

def fake_fetch(project, version, tags, dest):
    with zipfile.ZipFile(dest, "w") as zf:
        for name in PARAKEET.required + ("libcudnn_graph.so.9",
                                         "libnvrtc-builtins.so.13.0"):
            zf.writestr(f"nvidia/{project}/lib/{name}", b"\x7fELF")
        zf.writestr("nvidia/x/lib/libunwanted.so.1", b"no")


def test_parakeet_install_extracts_the_stack_flat_and_preloads_it(tmp_path, monkeypatch):
    loaded = []
    monkeypatch.setattr(ctypes, "CDLL", lambda path, mode=None: loaded.append((path, mode)))

    gpu_pack.install_pack(PARAKEET, tmp_path, fetch=fake_fetch)

    names = {p.name for p in tmp_path.iterdir()}
    assert set(PARAKEET.required) <= names
    assert "libcudnn_graph.so.9" in names and "libunwanted.so.1" not in names
    assert [m for _, m in loaded] == [ctypes.RTLD_LOCAL] * len(PARAKEET.preload)
    assert [p.rsplit("/", 1)[1] for p, _ in loaded] == list(PARAKEET.preload)


def test_activation_is_never_rtld_global(monkeypatch):
    modes = []
    monkeypatch.setattr(ctypes, "CDLL", lambda path, mode=None: modes.append(mode))
    gpu_pack.activate(WHISPER)
    gpu_pack.activate(PARAKEET)
    assert modes and ctypes.RTLD_GLOBAL not in modes
    assert set(modes) == {ctypes.RTLD_LOCAL}


def test_a_library_that_will_not_load_does_not_stop_startup(tmp_path, monkeypatch):
    def refuse(path, mode=None):
        raise OSError("cannot open shared object file")
    monkeypatch.setattr(ctypes, "CDLL", refuse)
    gpu_pack.activate(WHISPER, tmp_path)


def test_startup_activates_only_installed_editions(tmp_path, monkeypatch):
    loaded = []
    monkeypatch.setattr(ctypes, "CDLL", lambda path, mode=None: loaded.append(path))
    for name in WHISPER.required:
        (tmp_path / name).write_bytes(b"\x7fELF")
    gpu_pack.activate_installed(tmp_path)
    assert len(loaded) == len(WHISPER.preload)


def test_failed_install_leaves_no_partial_pack(tmp_path):
    def short(project, version, tags, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("lib/libcublas.so.12", b"x")
    with pytest.raises(RuntimeError):
        gpu_pack.install_pack(WHISPER, tmp_path, fetch=short)
    assert list(tmp_path.iterdir()) == []


# ---- the rung -------------------------------------------------------------

def test_auto_reaches_for_cuda_then_cpu_on_linux():
    runtimes = ("auto", "cuda", "cpu")
    assert stt._provider_ladder("auto", runtimes) == (
        "CUDAExecutionProvider", "CPUExecutionProvider")
    assert stt._provider_ladder("cpu", runtimes) == ("CPUExecutionProvider",)


def test_windows_auto_still_leads_with_directml():
    runtimes = ("auto", "directml", "cuda", "cpu")
    assert stt._provider_ladder("auto", runtimes) == (
        "DmlExecutionProvider", "CPUExecutionProvider")


# ---- the probe -------------------------------------------------------------

def test_driver_version_reads_cu_driver_get_version(monkeypatch):
    class Lib:
        def cuDriverGetVersion(self, ref):
            ref._obj.value = R580
            return 0
    monkeypatch.setattr(ProcHardware, "_libcuda", staticmethod(lambda: Lib()))
    assert ProcHardware().cuda_driver_version() == R580


def test_no_libcuda_means_no_driver_and_no_version(monkeypatch):
    def missing():
        raise OSError("libcuda.so.1")
    monkeypatch.setattr(ProcHardware, "_libcuda", staticmethod(missing))
    probe = ProcHardware()
    assert not probe.nvidia_driver_present()
    assert probe.cuda_driver_version() is None
    assert probe.cuda_total_memory() is None
    assert not probe.dx12_gpu_present() and not probe.metal_gpu_present()


def test_processor_name_comes_from_proc_cpuinfo(tmp_path):
    info = tmp_path / "cpuinfo"
    info.write_text("processor\t: 0\nmodel name\t: AMD Ryzen 9\n")
    assert ProcHardware(info).processor_name() == "AMD Ryzen 9"
    assert ProcHardware(tmp_path / "nope").processor_name() == ""


def test_linux_capabilities_offer_both_editions_and_no_gpu_only_engine():
    assert linux.ProcHardware  # the adapter the platform assembles
    src = pathlib.Path(linux.__file__).read_text()
    assert "gpu_only_engines=frozenset()" in src
    assert '"parakeet": "cuda13-linux"' in src


# ---- the Recommended chip --------------------------------------------------

def suggest(vram=None, ram=16.0, cores=8, driver=None, cpu=True):
    return hardware.suggest_model(vram_gb=vram, ram_gb=ram, physical_cores=cores,
                                  cuda_driver=driver, parakeet_on_cpu=cpu).model


PARAKEET_V2 = "parakeet-tdt-0.6b-v2"


def test_nvidia_with_a_cuda_13_driver_gets_parakeet_from_4_gb():
    assert suggest(vram=4.0, driver=R580) == PARAKEET_V2
    assert suggest(vram=24.0, driver=R580 + 100) == PARAKEET_V2


def test_nvidia_below_4_gb_does_not_take_the_gpu_branch():
    assert suggest(vram=3.0, driver=R580, ram=6.0, cores=2) == "base.en"


def test_old_driver_nvidia_keeps_the_whisper_vram_rows():
    assert suggest(vram=8.0, driver=R570) == "distil-large-v3"
    assert suggest(vram=4.0, driver=R570) == "distil-medium.en"


def test_no_gpu_takes_parakeet_at_four_cores_and_eight_gigs():
    assert suggest(cores=4, ram=8.0) == PARAKEET_V2
    assert suggest(cores=16, ram=64.0) == PARAKEET_V2


def test_below_the_cpu_bar_falls_to_the_whisper_rows():
    assert suggest(cores=2, ram=16.0) == "distil-small.en"
    assert suggest(cores=8, ram=7.0) == "base.en"
    assert suggest(cores=8, ram=5.0) == "tiny.en"


def test_distil_medium_is_never_a_linux_cpu_default():
    for cores, ram in ((2, 32.0), (4, 8.0), (8, 16.0), (16, 64.0)):
        assert suggest(cores=cores, ram=ram) != "distil-medium.en"


def test_other_platforms_do_not_get_the_cpu_parakeet_branch():
    assert suggest(cpu=False, cores=8, ram=16.0) == "distil-medium.en"
