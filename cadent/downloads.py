"""Model downloads you can watch and stop (#114, #115).

Every model Cadent needs comes from Hugging Face, and until now every one of
them was a tray balloon at the start and silence afterwards — for anything from
78 MB to 3.1 GB. The wizard even offered a Cancel button that emitted a signal
nobody consumed, told the user "Download cancelled." and let the download run
on. **A button that reports success while doing nothing is worse than no
button**, so progress and cancellation are one problem with one answer here.

The seam is huggingface_hub's `tqdm_class`: hub builds one bar per file and
calls `update(n)` on it per chunk, from the downloading thread. That single
callback is both the only place a byte count is available and the only place a
running download can be stopped — raising from it unwinds `http_get`, which
catches network errors and nothing else.

Three consequences shape the rest of this module:

- **Plain HTTP, never Xet.** Measured against huggingface_hub 1.25.1: over Xet
  a 75 MB model produced exactly one callback, for the whole 75 MB, after it had
  landed — and `hf_xet` *swallowed* the exception raised from it, printing a
  traceback while the download completed. Neither progress nor cancellation
  survives that path.
- **One file at a time, never `snapshot_download`.** Its aggregate bars are the
  wrong shape (a total that grows as files begin, so 60% falls back to 20%), and
  it also hands our class to a file-count bar measured in files rather than
  bytes. `hf_hub_download` builds exactly one byte bar per file.
- **The total is ours, asked for up front.** `model_info` sizes the whole job
  before the first byte, which is what keeps the reading monotone.

A cancelled download **starts over** if asked again: hub removes its own
`.incomplete` blob when the transfer raises, keeping only `refs/main`. Measured,
not assumed. That is what the wizard's inert Escape key is protecting against —
a stray keypress really does bin the whole thing.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Decimal, not binary: this caption sits under a picker row that already
# disclosed a size, and Hugging Face quotes decimal too. A bar counting in MiB
# beneath a row counting in MB would report "1.4 GB of 1.4 GB" against a row
# promising 1.5 GB, and leave the user to work out which of the two lied.
_MB = 1_000_000
_GB = 1_000 * _MB


class Cancelled(Exception):
    """Raised out of a download the user asked to stop.

    **Not a failure**: nothing is faulted and nothing is toasted — the app was
    doing what it was told. It is still a loss, though: hub discards the
    partial, so what was fetched is gone.
    """


@dataclass(frozen=True)
class Progress:
    """One reading, in bytes. `total` is 0 when we could not size the job."""

    done: int
    total: int

    @property
    def percent(self) -> int:
        """Clamped, because resumes and retries can over-count and 104% reads
        as a bug rather than as progress."""
        if self.total <= 0:
            return 0
        return min(100, self.done * 100 // self.total)

    @property
    def caption(self) -> str:
        """"130 MB of 793 MB" — both halves in the unit the *total* calls for,
        so the two numbers are never measured differently."""
        if self.total <= 0:
            return _size(self.done, _unit_for(self.done))
        unit = _unit_for(self.total)
        return f"{_size(self.done, unit)} of {_size(self.total, unit)}"


def _unit_for(size: int) -> int:
    return _GB if size >= _GB else _MB


def _size(size: int, unit: int) -> str:
    if unit == _GB:
        return f"{size / _GB:.1f} GB"
    return f"{round(size / _MB)} MB"


class Download:
    """One fetch, watched and stoppable.

    `cancel` is called from the UI thread while `advance` runs on the
    downloading one, which is why the flag is an Event and the running total is
    only ever written by the downloader.
    """

    def __init__(self, on_progress: Callable[[Progress], None] | None = None) -> None:
        self._on_progress = on_progress
        self._stop = threading.Event()
        self._expected = 0      # every byte of the job, known before the first
        self._banked = 0        # whole files that have landed
        self._in_flight = 0     # bytes of the file currently downloading

    # ---- what the caller drives ---------------------------------------------

    def expect(self, total: int) -> None:
        self._expected = total

    def credit(self, size: int) -> None:
        """A whole file has landed — downloaded now, or already on disk.

        Cached files cross no network, so hub never builds a bar for them.
        Crediting the file itself rather than only its chunks is what stops the
        reading stalling short of the end on a resumed download.
        """
        self._banked += size
        self._in_flight = 0
        self._report()

    def cancel(self) -> None:
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def raise_if_cancelled(self) -> None:
        if self._stop.is_set():
            raise Cancelled("download cancelled")

    @property
    def progress(self) -> Progress:
        return Progress(self._banked + self._in_flight, self._expected)

    # ---- what huggingface_hub drives ----------------------------------------

    @property
    def tqdm_class(self) -> type:
        """The bar class to hand hub. Bound to this download, so every file's
        bar feeds one running total."""
        download = self

        class _Bar(_tqdm()):
            def __init__(self, *args, **kwargs) -> None:
                # hub passes no `disable` to a class that isn't its own tqdm
                # subclass, and a bar painting itself onto a windowed app's
                # stderr helps nobody. Our accounting runs before super(), so
                # switching the display off costs nothing.
                kwargs["disable"] = True
                super().__init__(*args, **kwargs)

            def update(self, n=1):
                download._advance(int(n or 0))
                return super().update(n)

        return _Bar

    def _advance(self, delta: int) -> None:
        """One chunk, from the downloading thread. Checked before it is banked,
        so a cancel never credits the chunk it refused."""
        self.raise_if_cancelled()
        # Negative: hub rewinds its own bar when a server ignores a Range header
        # and re-sends a file we were resuming.
        self._in_flight = max(0, self._in_flight + delta)
        self._report()

    def _report(self) -> None:
        if self._on_progress is not None:
            self._on_progress(self.progress)


def _tqdm() -> type:
    """tqdm's own class, imported late.

    Subclassed rather than duck-typed because hub reaches for more of the
    interface than `update` — `set_postfix_str`, `refresh`, the context manager
    — and the set differs by path.
    """
    from tqdm import tqdm

    return tqdm


# ---- the hub calls, named so tests can stand in for them --------------------


def _repo_files(repo: str) -> dict[str, int]:
    """Every file in the repo and its size, in one call."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True)
    return {f.rfilename: f.size or 0 for f in info.siblings or ()}


def _download_file(repo: str, name: str, *, download: Download,
                   cache_dir: Path | None, local_dir: Path | None) -> Path:
    from huggingface_hub import hf_hub_download

    # No `revision`: the default writes `refs/main` into the cache, which is
    # what faster-whisper's own `local_files_only` lookup resolves against.
    # Pinning the commit we sized would skip that ref and hide the download
    # from the very loader it was for.
    return Path(hf_hub_download(
        repo, name,
        cache_dir=str(cache_dir) if cache_dir else None,
        local_dir=str(local_dir) if local_dir else None,
        tqdm_class=download.tqdm_class,
    ))


def _disable_xet() -> None:
    """Route hub over plain HTTP for the rest of the process.

    `is_xet_available()` reads this constant on every call, so setting it here
    is enough — unlike the `HF_HUB_DISABLE_XET` environment variable it is
    normally derived from, which is read once at import and so is already too
    late by the time anything asks us to download.
    """
    from huggingface_hub import constants

    constants.HF_HUB_DISABLE_XET = True


# ---- the one entry point ----------------------------------------------------


def fetch(repo: str, patterns: list[str], download: Download, *,
          cache_dir: Path | None = None, local_dir: Path | None = None) -> None:
    """Download every file in `repo` matching `patterns`, reporting as it goes.

    Exactly one destination: `cache_dir` for the Hugging Face cache layout that
    faster-whisper resolves against, `local_dir` for a plain directory of files,
    which is how Parakeet's weights and the cleanup GGUFs are stored. They stay
    two arguments rather than one because they mean two different layouts to
    hub, not two spellings of the same place.

    Raises `Cancelled` if the download was stopped, `FileNotFoundError` if
    nothing in the repo matches — an empty destination handed to an engine
    fails several steps later as something that reads like a bug rather than
    like a repo we asked the wrong question of.
    """
    _disable_xet()
    download.raise_if_cancelled()

    sizes = _repo_files(repo)
    wanted = {name: size for name, size in sizes.items()
              if any(fnmatch.fnmatch(name, p) for p in patterns)}
    if not wanted:
        raise FileNotFoundError(
            f"{repo} has no files matching {', '.join(patterns)}")

    download.expect(sum(wanted.values()))
    log.info("downloading %d file(s), %d bytes, from %s",
             len(wanted), sum(wanted.values()), repo)
    for name, size in wanted.items():
        download.raise_if_cancelled()
        _download_file(repo, name, download=download,
                       cache_dir=cache_dir, local_dir=local_dir)
        download.credit(size)
