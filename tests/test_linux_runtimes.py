"""Runtimes on Linux (#38; spec M6 §6, ADR 0010): the two-edition GPU pack,
the driver-version gate, the RTLD_LOCAL preload, the CUDA rung for both
engines, and the Recommended-chip branches — all data and fakes; the CUDA
libraries themselves are a hardware item (§12.9).
"""

import ctypes
import dataclasses
import zipfile

import pytest
from conftest import make_platform

from cadent import gpu_pack, hardware, stt
from cadent import platform as platform_pkg
from cadent.platform import gpu_packs, linux
from cadent.platform.linux.hardware import LinuxHardware


def linux_caps(session="x11", desktop="KDE"):
    return linux.capabilities_for(linux.detect({"XDG_SESSION_TYPE": session,
                                                "XDG_CURRENT_DESKTOP": desktop}))


# ---- editions per platform --------------------------------------------------

def test_the_editions_follow_the_platform():
    from cadent.platform import fallback

    assert set(fallback.CAPABILITIES.gpu_pack_editions) == {"faster-whisper"}
    assert fallback.CAPABILITIES.gpu_pack_editions["faster-whisper"] is gpu_packs.CUBLAS12_WIN
    assert set(linux_caps().gpu_pack_editions) == {"faster-whisper", "parakeet"}
    assert gpu_pack.edition_for("parakeet", fallback.CAPABILITIES) is None
    assert gpu_pack.edition_for("parakeet", linux_caps()) is gpu_packs.CUDA13_LINUX
    # gpu_pack_available is the same fact as "an edition exists".
    for caps in (fallback.CAPABILITIES, linux_caps()):
        assert caps.gpu_pack_available == bool(caps.gpu_pack_editions)


def test_the_cuda13_edition_needs_an_r580_driver():
    edition = gpu_packs.CUDA13_LINUX
    assert gpu_pack.driver_supports(edition, 13000) is True
    assert gpu_pack.driver_supports(edition, 12080) is False
    assert gpu_pack.driver_supports(edition, None) is False
    assert gpu_pack.driver_supports(gpu_packs.CUBLAS12_LINUX, 12080) is True
    hint = gpu_pack.driver_hint("parakeet", 12080, linux_caps())
    assert hint is not None and "580" in hint
    assert gpu_pack.driver_hint("parakeet", 13000, linux_caps()) is None
    assert gpu_pack.driver_hint("faster-whisper", 12080, linux_caps()) is None


def test_should_offer_is_edition_and_driver_keyed(tmp_path):
    caps = linux_caps()
    # Parakeet on Linux with a CUDA-13 driver: offered.
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, "parakeet", driver_present=True,
                                 cuda_driver_version=13000, caps=caps) is True
    # Pre-580: not offered — the row says to update instead.
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, "parakeet", driver_present=True,
                                 cuda_driver_version=12080, caps=caps) is False
    # faster-whisper does not care about the driver's CUDA version.
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, "faster-whisper",
                                 driver_present=True, cuda_driver_version=12080,
                                 caps=caps) is True
    # No edition (Parakeet on Windows): never.
    from cadent.platform import fallback

    assert gpu_pack.should_offer("auto", "cpu", tmp_path, "parakeet", driver_present=True,
                                 cuda_driver_version=13000,
                                 caps=fallback.CAPABILITIES) is False


# ---- install and preload -------------------------------------------------------

def wheel_writer(files_by_package):
    def fetch(dest, package, version, tag):
        assert tag == "manylinux"
        with zipfile.ZipFile(dest, "w") as zf:
            for name in files_by_package.get(package, ()):
                zf.writestr(f"nvidia/{package}/lib/{name}", b"\x7fELF" + name.encode())
            zf.writestr(f"{package}-{version}.dist-info/METADATA", b"meta")
    return fetch


def test_the_linux_edition_extracts_into_its_own_dir_and_preloads(tmp_path, monkeypatch):
    loaded = []
    monkeypatch.setattr(gpu_pack.ctypes, "CDLL", lambda path: loaded.append(path) or object())
    gpu_pack._preloaded.clear()
    fetch = wheel_writer({"nvidia-cublas-cu12": ["libcublas.so.12", "libcublasLt.so.12",
                                                 "libnvblas.so.12"]})
    gpu_pack.install_pack(tmp_path, fetch=fetch, edition=gpu_packs.CUBLAS12_LINUX)
    dest = tmp_path / "cublas12"
    assert sorted(p.name for p in dest.iterdir()) == ["libcublas.so.12", "libcublasLt.so.12"]
    # Preloaded in the edition's order — Lt before cuBLAS — with plain CDLL
    # (RTLD_LOCAL by default), never a PATH/LD_LIBRARY_PATH change.
    assert [p.split("/")[-1].split("\\")[-1] for p in loaded] == \
        ["libcublasLt.so.12", "libcublas.so.12"]
    assert gpu_pack.pack_installed(tmp_path, "faster-whisper", linux_caps()) is True
    assert not list(dest.glob("*.whl"))


def test_the_cuda13_edition_gathers_its_files_across_wheels(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_pack.ctypes, "CDLL", lambda path: object())
    gpu_pack._preloaded.clear()
    fetch = wheel_writer({
        "nvidia-cuda-runtime": ["libcudart.so.13"],
        "nvidia-nvjitlink": ["libnvJitLink.so.13"],
        "nvidia-cuda-nvrtc": ["libnvrtc.so.13", "libnvrtc-builtins.so.13.0"],
        "nvidia-cublas": ["libcublas.so.13", "libcublasLt.so.13"],
        "nvidia-cufft": ["libcufft.so.12"],
        "nvidia-curand": ["libcurand.so.10"],
        "nvidia-cudnn-cu13": ["libcudnn.so.9", "libcudnn_graph.so.9", "libcudnn_ops.so.9",
                              "libcudnn_engines_precompiled.so.9",
                              "libcudnn_engines_runtime_compiled.so.9",
                              "libcudnn_heuristic.so.9", "libcudnn_adv.so.9",
                              "libcudnn_cnn.so.9"],
    })
    gpu_pack.install_pack(tmp_path, fetch=fetch, edition=gpu_packs.CUDA13_LINUX)
    dest = tmp_path / "cuda13"
    assert (dest / "libcudnn_graph.so.9").exists()
    assert gpu_pack.pack_installed(tmp_path, "parakeet", linux_caps()) is True
    # The two editions live side by side, one dir each.
    assert not (tmp_path / "cublas12").exists()


def test_a_missing_library_leaves_no_partial_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_pack.ctypes, "CDLL", lambda path: object())
    fetch = wheel_writer({"nvidia-cublas-cu12": ["libcublas.so.12"]})   # no Lt
    with pytest.raises(RuntimeError):
        gpu_pack.install_pack(tmp_path, fetch=fetch, edition=gpu_packs.CUBLAS12_LINUX)
    assert gpu_pack.pack_installed(tmp_path, "faster-whisper", linux_caps()) is False
    assert not (tmp_path / "cublas12" / "libcublas.so.12").exists()


def test_activate_installed_preloads_every_edition_on_disk(tmp_path, monkeypatch):
    loaded = []
    monkeypatch.setattr(gpu_pack.ctypes, "CDLL", lambda path: loaded.append(path) or object())
    gpu_pack._preloaded.clear()
    for edition, names in ((gpu_packs.CUBLAS12_LINUX, ["libcublasLt.so.12", "libcublas.so.12"]),
                           (gpu_packs.CUDA13_LINUX, ["libcudart.so.13"])):
        d = tmp_path / edition.subdir
        d.mkdir()
        for name in names:
            (d / name).write_bytes(b"x")
    gpu_pack.activate_installed(tmp_path, caps=linux_caps())
    # cuBLAS 12 (complete) preloaded; the CUDA-13 dir is incomplete so it is not.
    assert len(loaded) == 2 and all("cublas12" in p for p in loaded)


def test_pypi_helpers_pick_manylinux_wheels_and_prefix_versions():
    data = {"urls": [
        {"filename": "nvidia_cublas_cu12-x-py3-none-manylinux_2_27_x86_64.whl",
         "url": "https://files.example/linux.whl"},
        {"filename": "nvidia_cublas_cu12-x-py3-none-win_amd64.whl",
         "url": "https://files.example/win.whl"}]}
    assert gpu_pack._wheel_url(data, "manylinux") == "https://files.example/linux.whl"
    assert gpu_pack._wheel_url(data, "win_amd64") == "https://files.example/win.whl"
    project = {"info": {"name": "nvidia-cublas"},
               "releases": {"12.9.1.4": [{}], "13.0.0.19": [{}], "13.1.0.3": [{}],
                            "13.2.0.1": [{"yanked": True}]}}
    assert gpu_pack._pick_version(project, "13.") == "13.1.0.3"
    assert gpu_pack._pick_version(project, "12.9.1.4") == "12.9.1.4"
    with pytest.raises(RuntimeError):
        gpu_pack._pick_version(project, "14.")


# ---- the Linux hardware probe -------------------------------------------------

class FakeCuda:
    def __init__(self, version=13000, total=8 * 1024 ** 3):
        self._version = version
        self._total = total

    def cuInit(self, _flags):
        return 0

    def cuDeviceGet(self, device, _ordinal):
        return 0

    def cuDeviceTotalMem_v2(self, total, _device):
        ctypes.cast(total, ctypes.POINTER(ctypes.c_size_t)).contents.value = self._total
        return 0

    def cuDriverGetVersion(self, version):
        ctypes.cast(version, ctypes.POINTER(ctypes.c_int)).contents.value = self._version
        return 0


def test_the_linux_probe_reads_the_driver_api_and_cpuinfo(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nmodel name\t: AMD Ryzen 9 9950X\n")
    probe = LinuxHardware(loader=lambda name: FakeCuda(), cpuinfo=cpuinfo)
    assert probe.nvidia_driver_present() is True
    assert probe.cuda_driver_version() == 13000
    assert probe.cuda_total_memory() == 8.0
    assert probe.processor_name() == "AMD Ryzen 9 9950X"
    assert probe.dx12_gpu_present() is False and probe.metal_gpu_present() is False


def test_the_linux_probe_without_a_driver_answers_honestly(tmp_path):
    def no_driver(name):
        raise OSError("libcuda.so.1: cannot open shared object file")
    probe = LinuxHardware(loader=no_driver, cpuinfo=tmp_path / "missing")
    assert probe.nvidia_driver_present() is False
    assert probe.cuda_driver_version() is None
    assert probe.cuda_total_memory() is None
    assert probe.processor_name() == ""


def test_hardware_detect_carries_the_driver_version():
    from conftest import FakeHardwareProbe

    hw = hardware.detect(FakeHardwareProbe(vram=8.0, driver=True, driver_cuda=13000))
    assert hw.cuda_driver_version == 13000


# ---- the Recommended chip (§6.3) -----------------------------------------------

def suggest(**kw):
    defaults = dict(vram_gb=None, ram_gb=16.0, physical_cores=8)
    defaults.update(kw)
    return hardware.suggest_model(**defaults).model


def test_nvidia_with_a_cuda13_driver_earns_parakeet():
    assert suggest(vram_gb=8.0, parakeet_cuda=True) == "parakeet-tdt-0.6b-v2"
    # Pre-580 driver on the same card: the Whisper VRAM rows.
    assert suggest(vram_gb=8.0, parakeet_cuda=False) == "distil-large-v3"


def test_the_linux_cpu_branch_recommends_parakeet_at_the_floor():
    floor = (4, 8.0)
    assert suggest(physical_cores=4, ram_gb=8.0, parakeet_cpu_floor=floor) == \
        "parakeet-tdt-0.6b-v2"
    assert suggest(physical_cores=16, ram_gb=32.0, parakeet_cpu_floor=floor) == \
        "parakeet-tdt-0.6b-v2"
    assert suggest(physical_cores=2, ram_gb=8.0, parakeet_cpu_floor=floor) == \
        "distil-small.en"
    assert suggest(physical_cores=8, ram_gb=7.0, parakeet_cpu_floor=floor) == "base.en"
    assert suggest(physical_cores=8, ram_gb=4.0, parakeet_cpu_floor=floor) == "tiny.en"
    # distil-medium.en is never a Linux CPU default, whatever the box.
    assert suggest(physical_cores=16, ram_gb=64.0, parakeet_cpu_floor=floor) != \
        "distil-medium.en"
    # Without the floor (win32 today), the old rows hold.
    assert suggest(physical_cores=16, ram_gb=64.0) == "distil-medium.en"


def test_suggest_for_this_machine_reads_the_platform_facts(monkeypatch):
    from conftest import FakeHardwareProbe

    plat = make_platform(hardware=FakeHardwareProbe(vram=None, cpu="x", driver=False))
    plat = dataclasses.replace(plat, capabilities=linux_caps())
    monkeypatch.setattr(platform_pkg, "_current", plat)
    hardware.reset_cache()
    monkeypatch.setattr(hardware, "detect_cached", lambda: hardware.Hardware(
        ram_gb=16.0, physical_cores=8))
    assert hardware.suggest_for_this_machine().model == "parakeet-tdt-0.6b-v2"
    monkeypatch.setattr(hardware, "detect_cached", lambda: hardware.Hardware(
        ram_gb=16.0, physical_cores=8, vram_gb=8.0, nvidia_driver=True,
        cuda_driver_version=12080))
    assert hardware.suggest_for_this_machine().model == "distil-large-v3"
    hardware.reset_cache()


# ---- provider ladders: both engines walk CUDA on Linux ----------------------------

def test_parakeet_auto_walks_cuda_then_cpu_on_linux():
    linux_runtimes = ("auto", "cuda", "cpu")
    assert stt._provider_ladder("auto", linux_runtimes) == (
        "CUDAExecutionProvider", "CPUExecutionProvider")
    assert stt._provider_ladder("cuda", linux_runtimes) == (
        "CUDAExecutionProvider", "CPUExecutionProvider")
    assert stt._provider_ladder("cpu", linux_runtimes) == ("CPUExecutionProvider",)
    # Windows keeps DirectML as auto's GPU rung; CUDA stays an explicit ask.
    win = ("auto", "directml", "cuda", "cpu")
    assert stt._provider_ladder("auto", win) == ("DmlExecutionProvider",
                                                 "CPUExecutionProvider")
    assert stt._provider_ladder("cuda", win) == ("CUDAExecutionProvider",
                                                 "CPUExecutionProvider")
    # darwin: one rung.
    assert stt._provider_ladder("auto", ("auto", "cpu")) == ("CPUExecutionProvider",)


def test_the_gpu_page_gate_reads_the_edition_and_driver():
    assert hardware.should_offer_gpu_page(True, 8.0, False, "parakeet",
                                          edition_exists=True, driver_ok=True) is True
    assert hardware.should_offer_gpu_page(True, 8.0, False, "parakeet",
                                          edition_exists=True, driver_ok=False) is False
    assert hardware.should_offer_gpu_page(True, 8.0, False, "parakeet",
                                          edition_exists=False) is False


def test_the_speech_pane_shows_the_driver_row(qt_app, tmp_path, monkeypatch):
    from conftest import FakeHardwareProbe

    from cadent.config_store import ConfigStore
    from cadent.settings_ui import SettingsWindow
    from cadent.theme.tokens import tokens

    plat = make_platform(hardware=FakeHardwareProbe(vram=8.0, driver=True,
                                                    driver_cuda=12080))
    monkeypatch.setattr(platform_pkg, "_current",
                        dataclasses.replace(plat, capabilities=linux_caps()))
    monkeypatch.setattr(hardware, "detect_safely", lambda: hardware.Hardware(
        ram_gb=16.0, physical_cores=8, vram_gb=8.0, nvidia_driver=True,
        cuda_driver_version=12080))
    store = ConfigStore(tmp_path / "config.json")
    store.set("stt_engine", "parakeet")
    store.set("stt_model", "parakeet-tdt-0.6b-v2")
    win = SettingsWindow(store, tokens=tokens("dark"), devices=[])
    try:
        assert win.speech.pack_hint.isVisibleTo(win.speech) is True
        assert "580" in win.speech.pack_hint.text()
    finally:
        win.close()
