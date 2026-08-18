"""Entry-point behavior: the CUDA support-pack PATH prepend (M3 packaging, #47)."""

import os

from cadent import __main__ as entry
from cadent import config as cfg


def test_cuda_dir_prepended_when_present(tmp_path, monkeypatch):
    """An installed edition (the two cuBLAS DLLs) is activated at startup —
    a PATH prepend for the Windows edition (M6 ADR 0010 keys it by edition;
    an empty dir no longer counts, only a complete pack does)."""
    from cadent import gpu_pack

    cuda = tmp_path / "cuda"
    cuda.mkdir()
    for dll in gpu_pack.PACK_DLLS:
        (cuda / dll).write_bytes(b"MZ")
    monkeypatch.setattr(cfg, "CUDA_DIR", cuda)
    monkeypatch.setenv("PATH", "C:\\existing")

    entry._enable_cuda_support_pack()

    assert os.environ["PATH"] == f"{cuda}{os.pathsep}C:\\existing"


def test_path_untouched_when_no_support_pack(tmp_path, monkeypatch):
    from cadent import gpu_pack

    monkeypatch.setattr(cfg, "CUDA_DIR", tmp_path / "cuda")  # never created
    monkeypatch.setattr(gpu_pack, "CUDA_DIR", tmp_path / "cuda")
    monkeypatch.setenv("PATH", "C:\\existing")

    entry._enable_cuda_support_pack()

    assert os.environ["PATH"] == "C:\\existing"
