"""GPU support pack (M3 #55): offer gate and disclosed download/extract."""

import os
import zipfile

import pytest

from cadent import gpu_pack


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    """The pack reads its editions off the platform (ADR 0010); these tests
    describe the Windows cuBLAS edition on every OS."""


def pack_dir(cuda):
    """Where the Windows edition's files land under a cuda dir."""
    return gpu_pack.pack_dir(gpu_pack.edition_for("faster-whisper"), cuda)


# ---- offer gate ------------------------------------------------------------

def test_offers_when_driver_present_but_stt_fell_back_to_cpu(tmp_path):
    assert gpu_pack.should_offer("auto", "cpu", tmp_path / "cuda",
                                 driver_present=True) is True


def test_no_offer_when_engine_already_on_cuda(tmp_path):
    assert gpu_pack.should_offer("auto", "cuda", tmp_path / "cuda",
                                 driver_present=True) is False


def test_no_offer_when_user_configured_cpu(tmp_path):
    assert gpu_pack.should_offer("cpu", "cpu", tmp_path / "cuda",
                                 driver_present=True) is False


def test_no_offer_without_nvidia_driver(tmp_path):
    assert gpu_pack.should_offer("auto", "cpu", tmp_path / "cuda",
                                 driver_present=False) is False


def test_driver_presence_defaults_to_the_platform_probe(tmp_path, machine):
    machine(driver=True)
    assert gpu_pack.should_offer("auto", "cpu", tmp_path / "cuda") is True


def test_no_offer_when_the_running_engine_is_not_the_one_the_pack_helps(tmp_path):
    """The pack is ctranslate2's cuBLAS. Parakeet runs on ONNX Runtime's
    DirectML provider and would not touch a byte of it (#72), so offering a
    550 MB download there is offering a placebo."""
    assert gpu_pack.should_offer("auto", "cpu", tmp_path / "cuda",
                                 stt_engine="parakeet",
                                 driver_present=True) is False
    assert gpu_pack.should_offer("auto", "cpu", tmp_path / "cuda",
                                 stt_engine="faster-whisper",
                                 driver_present=True) is True


def test_no_offer_when_pack_already_installed(tmp_path):
    cuda = tmp_path / "cuda"
    pack_dir(cuda).mkdir(parents=True)
    for dll in gpu_pack.PACK_DLLS:
        (pack_dir(cuda) / dll).write_bytes(b"MZ")
    assert gpu_pack.should_offer("auto", "cpu", cuda, driver_present=True) is False


# ---- install ---------------------------------------------------------------

def fake_fetch(dest, dlls=gpu_pack.PACK_DLLS):
    """Stand-in for the PyPI download: writes a wheel-shaped zip to dest."""
    with zipfile.ZipFile(dest, "w") as zf:
        for dll in dlls:
            zf.writestr(f"nvidia/cublas/bin/{dll}", b"MZ" + dll.encode())
        zf.writestr("nvidia/cublas/bin/nvblas64_12.dll", b"MZ extra")  # not wanted
        zf.writestr("nvidia_cublas_cu12-12.9.2.10.dist-info/METADATA", b"meta")


def test_install_extracts_exactly_the_pack_dlls(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    gpu_pack.install_pack(cuda, fetch=fake_fetch)

    assert sorted(p.name for p in pack_dir(cuda).iterdir()) == sorted(gpu_pack.PACK_DLLS)
    assert (pack_dir(cuda) / "cublas64_12.dll").read_bytes() == b"MZcublas64_12.dll"


def test_install_prepends_cuda_dir_to_path(tmp_path, monkeypatch):
    """A same-session STT restart must find the DLLs without an app restart."""
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    gpu_pack.install_pack(cuda, fetch=fake_fetch)

    assert os.environ["PATH"] == f"{pack_dir(cuda)}{os.pathsep}C:\\existing"


def test_install_fails_cleanly_when_wheel_lacks_a_dll(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    with pytest.raises(RuntimeError):
        gpu_pack.install_pack(
            cuda, fetch=lambda dest: fake_fetch(dest, dlls=("cublas64_12.dll",)))

    assert not gpu_pack.pack_installed(cuda)
    assert os.environ["PATH"] == "C:\\existing"      # no prepend on failure
    assert not list(cuda.rglob("*.whl"))             # temp wheel cleaned up


def test_install_leaves_no_wheel_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    gpu_pack.install_pack(cuda, fetch=fake_fetch)

    assert not list(cuda.rglob("*.whl"))


# ---- wheel URL selection ---------------------------------------------------

def test_wheel_url_picks_windows_wheel():
    # Shape of PyPI's /pypi/<pkg>/<version>/json response.
    data = {"urls": [
        {"filename": "nvidia_cublas_cu12-x-py3-none-manylinux_x86_64.whl",
         "url": "https://files.example/linux.whl"},
        {"filename": "nvidia_cublas_cu12-x-py3-none-win_amd64.whl",
         "url": "https://files.example/win.whl"},
    ]}
    assert gpu_pack._wheel_url(data) == "https://files.example/win.whl"


def test_wheel_url_missing_windows_wheel_raises():
    data = {"urls": [
        {"filename": "nvidia_cublas_cu12-x-py3-none-manylinux_x86_64.whl",
         "url": "https://files.example/linux.whl"},
    ]}
    with pytest.raises(RuntimeError):
        gpu_pack._wheel_url(data)


# ---- editions (ADR 0010) ---------------------------------------------------

def linux_editions(monkeypatch):
    from conftest import pin_linux_ui_platform

    plat = pin_linux_ui_platform(monkeypatch, tier="whole")
    return plat.capabilities.gpu_pack_editions


def test_the_edition_follows_the_speech_engine(monkeypatch):
    """One surface, engine-keyed: faster-whisper reads cuBLAS 12, Parakeet the
    CUDA-13 stack, and each discloses its own size."""
    editions = linux_editions(monkeypatch)
    assert gpu_pack.edition_for("faster-whisper") is editions["faster-whisper"]
    assert gpu_pack.edition_for("parakeet") is editions["parakeet"]
    assert editions["faster-whisper"].size != editions["parakeet"].size
    assert editions["faster-whisper"].activation == "preload"


def test_windows_has_no_parakeet_edition():
    """The pack's engine test is a real selector, not a placebo guard: on
    Windows Parakeet runs on DirectML and reads no CUDA edition at all."""
    assert gpu_pack.edition_for("parakeet") is None
    assert gpu_pack.should_offer("auto", "cpu", stt_engine="parakeet",
                                 driver_present=True) is False


def test_the_parakeet_edition_needs_a_cuda_13_driver(monkeypatch, tmp_path):
    """Pre-580 driver → the edition is not offered; the Speech pane says to
    update the driver instead."""
    editions = linux_editions(monkeypatch)
    parakeet = editions["parakeet"]
    assert gpu_pack.driver_can_use(parakeet, 12080) is False
    assert gpu_pack.driver_can_use(parakeet, None) is False
    assert gpu_pack.driver_can_use(parakeet, 13000) is True
    assert gpu_pack.driver_can_use(editions["faster-whisper"], None) is True
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, stt_engine="parakeet",
                                 driver_present=True, driver_cuda_version=12080) is False
    assert gpu_pack.should_offer("auto", "cpu", tmp_path, stt_engine="parakeet",
                                 driver_present=True, driver_cuda_version=13000) is True


def test_each_edition_keeps_its_own_directory(monkeypatch, tmp_path):
    """Two CUDA majors share a process by soname, not a folder: deleting one
    edition's directory must leave the other installed."""
    editions = linux_editions(monkeypatch)
    for edition in editions.values():
        directory = gpu_pack.pack_dir(edition, tmp_path)
        directory.mkdir(parents=True)
        for name in edition.files:
            (directory / name).write_bytes(b"\x7fELF")
    assert gpu_pack.pack_installed(tmp_path, editions["parakeet"])
    for name in editions["parakeet"].files:
        (gpu_pack.pack_dir(editions["parakeet"], tmp_path) / name).unlink()
    assert not gpu_pack.pack_installed(tmp_path, editions["parakeet"])
    assert gpu_pack.pack_installed(tmp_path, editions["faster-whisper"])


def test_a_preload_edition_never_touches_path(monkeypatch, tmp_path):
    """Linux activates by dlopen, not PATH — a missing library fails the
    activation and leaves the environment alone."""
    editions = linux_editions(monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin")
    edition = editions["faster-whisper"]
    assert gpu_pack.activate(edition, tmp_path) is False       # not installed
    directory = gpu_pack.pack_dir(edition, tmp_path)
    directory.mkdir(parents=True)
    for name in edition.files:
        (directory / name).write_bytes(b"not an ELF")
    assert gpu_pack.activate(edition, tmp_path) is False       # dlopen refused it
    assert os.environ["PATH"] == "/usr/bin"


def test_wheel_url_picks_the_manylinux_wheel_for_a_linux_edition():
    data = {"urls": [
        {"filename": "nvidia_cublas_cu12-x-py3-none-manylinux_2_27_x86_64.whl",
         "url": "https://files.example/linux.whl"},
        {"filename": "nvidia_cublas_cu12-x-py3-none-win_amd64.whl",
         "url": "https://files.example/win.whl"},
    ]}
    assert gpu_pack._wheel_url(data, "manylinux") == "https://files.example/linux.whl"
