"""Entry-point behavior: the CUDA support-pack activation (M3 packaging, #47;
ADR 0010) — a PATH prepend for the Windows edition, nothing when no edition
is installed."""

import os

import pytest

from cadent import __main__ as entry
from cadent import gpu_pack


@pytest.fixture(autouse=True)
def _win32_facts(pinned_win32_facts):
    """The entry point activates whatever editions the platform names."""


def test_cuda_dir_prepended_when_present(tmp_path, monkeypatch):
    cuda = tmp_path / "cuda"
    edition = gpu_pack.edition_for("faster-whisper")
    directory = gpu_pack.pack_dir(edition, cuda)
    directory.mkdir(parents=True)
    for name in edition.files:
        (directory / name).write_bytes(b"MZ")
    monkeypatch.setattr(gpu_pack, "CUDA_DIR", cuda)
    monkeypatch.setenv("PATH", "C:\\existing")

    entry._enable_cuda_support_pack()

    assert os.environ["PATH"] == f"{directory}{os.pathsep}C:\\existing"


def test_path_untouched_when_no_support_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_pack, "CUDA_DIR", tmp_path / "cuda")  # never created
    monkeypatch.setenv("PATH", "C:\\existing")

    entry._enable_cuda_support_pack()

    assert os.environ["PATH"] == "C:\\existing"
