"""GPU support pack (M3 #55): offer gate and disclosed download/extract."""

import os
import zipfile

import pytest

from cadent import gpu_pack

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
    cuda.mkdir()
    for dll in gpu_pack.PACK_DLLS:
        (cuda / dll).write_bytes(b"MZ")
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

    assert sorted(p.name for p in cuda.iterdir()) == sorted(gpu_pack.PACK_DLLS)
    assert (cuda / "cublas64_12.dll").read_bytes() == b"MZcublas64_12.dll"


def test_install_prepends_cuda_dir_to_path(tmp_path, monkeypatch):
    """A same-session STT restart must find the DLLs without an app restart."""
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    gpu_pack.install_pack(cuda, fetch=fake_fetch)

    assert os.environ["PATH"] == f"{cuda}{os.pathsep}C:\\existing"


def test_install_fails_cleanly_when_wheel_lacks_a_dll(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    with pytest.raises(RuntimeError):
        gpu_pack.install_pack(
            cuda, fetch=lambda dest: fake_fetch(dest, dlls=("cublas64_12.dll",)))

    assert not gpu_pack.pack_installed(cuda)
    assert os.environ["PATH"] == "C:\\existing"      # no prepend on failure
    assert not list(cuda.glob("*.whl"))              # temp wheel cleaned up


def test_install_leaves_no_wheel_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "C:\\existing")
    cuda = tmp_path / "cuda"

    gpu_pack.install_pack(cuda, fetch=fake_fetch)

    assert not list(cuda.glob("*.whl"))


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
