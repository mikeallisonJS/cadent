"""Model downloads you can watch and stop (#114, #115).

The seam under test is the one huggingface_hub gives us: a `tqdm_class` it
builds one bar from per file and calls `update(n)` on per chunk. Everything
here drives that seam directly, so none of it needs a network.
"""

from pathlib import Path

import pytest

from cadent import downloads

# ---- how a reading reads ----------------------------------------------------


def test_a_reading_says_what_is_done_out_of_what_was_promised():
    reading = downloads.Progress(130_000_000, 792_723_456)
    assert reading.caption == "130 MB of 793 MB"
    assert reading.percent == 16


def test_a_reading_counts_in_the_same_megabytes_the_picker_promised():
    """Decimal, like the registry row above it and like Hugging Face. In MiB
    this same download would report "756 MB" under a row promising 793 MB, and
    leave the user to work out which of the two was lying."""
    assert downloads.Progress(0, 792_723_456).caption.endswith("793 MB")


def test_both_halves_of_a_reading_use_the_same_unit():
    """"0.4 GB of 3.1 GB", never "400 MB of 3.1 GB" — a caption whose two
    numbers are measured differently is a caption you have to do arithmetic on.
    """
    assert downloads.Progress(400_000_000, 3_100_000_000).caption == \
        "0.4 GB of 3.1 GB"


def test_a_download_with_no_known_total_still_says_how_far_it_has_got():
    """`model_info` can fail where the download itself would work. Bytes so far
    is worth more than nothing."""
    reading = downloads.Progress(130_000_000, 0)
    assert reading.caption == "130 MB"
    assert reading.percent == 0


def test_a_reading_never_goes_past_the_end():
    """Resumes and retries can over-count. 104% is a bug report, not progress."""
    assert downloads.Progress(100, 50).percent == 100


# ---- the handle -------------------------------------------------------------


def bar(download, total=400):
    """A progress bar built the way huggingface_hub builds one."""
    return download.tqdm_class(desc="model.bin", total=total, initial=0,
                               unit="B", unit_scale=True)


def test_bytes_off_the_wire_reach_the_watcher():
    seen = []
    download = downloads.Download(seen.append)
    download.expect(1_000)

    handle = bar(download)
    handle.update(100)
    handle.update(150)

    assert [reading.done for reading in seen] == [100, 250]
    # The denominator is the whole job, not the file this bar happens to be on.
    assert seen[-1].total == 1_000


def test_a_cancelled_download_stops_at_the_next_chunk():
    """The only place a running download can be stopped is the callback it makes
    per chunk, so that is where the cancel lands (#114)."""
    download = downloads.Download()
    handle = bar(download)
    handle.update(100)

    download.cancel()

    with pytest.raises(downloads.Cancelled):
        handle.update(100)
    assert download.cancelled is True


def test_a_cancel_does_not_bank_the_chunk_it_refused():
    download = downloads.Download()
    handle = bar(download)
    handle.update(100)
    download.cancel()
    with pytest.raises(downloads.Cancelled):
        handle.update(100)

    assert download.progress.done == 100


def test_a_rolled_back_chunk_comes_off_the_count():
    """hub rewinds its own bar when a server ignores a Range header and sends
    the whole file again; a counter that only ever grows would end past 100%."""
    download = downloads.Download()
    handle = bar(download)
    handle.update(200)
    handle.update(-200)

    assert download.progress.done == 0


# ---- fetching a repo --------------------------------------------------------


class FakeHub:
    """The two hub calls `fetch` makes, with no network behind them."""

    def __init__(self, files: dict[str, int], cached: tuple[str, ...] = ()):
        self.files = files
        self.cached = cached        # already on disk: no bytes, so no bar
        self.downloaded: list[str] = []
        self.kwargs: list[dict] = []

    def repo_files(self, repo):
        return dict(self.files)

    def download_file(self, repo, name, *, download, **kwargs):
        self.downloaded.append(name)
        self.kwargs.append(kwargs)
        if name not in self.cached:
            handle = bar(download, total=self.files[name])
            handle.update(self.files[name])
        return Path(name)


@pytest.fixture
def hub(monkeypatch):
    def install(files, cached=()):
        fake = FakeHub(files, cached)
        monkeypatch.setattr(downloads, "_repo_files", fake.repo_files)
        monkeypatch.setattr(downloads, "_download_file", fake.download_file)
        return fake

    return install


def test_fetch_sizes_the_whole_job_before_the_first_byte(hub):
    """A percentage that starts at 60 and falls back to 20 as later files begin
    is worse than no percentage, which is what asking hub to aggregate would
    give us."""
    hub({"model.bin": 800, "config.json": 200})
    seen = []
    download = downloads.Download(seen.append)

    downloads.fetch("repo", ["*"], download, cache_dir=Path("cache"))

    assert seen[0].total == 1_000
    assert all(reading.total == 1_000 for reading in seen)


def test_fetch_downloads_what_matches_and_nothing_else(hub):
    fake = hub({"model.bin": 800, "README.md": 10, "model.fp32.onnx": 9_000})

    downloads.fetch("repo", ["model.bin", "*.json"], downloads.Download(),
                    cache_dir=Path("cache"))

    assert fake.downloaded == ["model.bin"]


def test_a_repo_with_nothing_that_matches_is_an_error(hub):
    """Silently succeeding here would hand the engine an empty directory and
    let it fail several steps later, which is the shape #112 already fixed once.
    """
    hub({"README.md": 10})

    with pytest.raises(FileNotFoundError):
        downloads.fetch("repo", ["*.bin"], downloads.Download(),
                        cache_dir=Path("cache"))


def test_a_file_already_on_disk_still_counts_towards_the_total(hub):
    """Nothing crosses the network for a cached file, so hub never builds a bar
    for it. Without crediting the file itself the reading would stall short of
    the end and never arrive."""
    hub({"model.bin": 800, "config.json": 200}, cached=("config.json",))
    download = downloads.Download()

    downloads.fetch("repo", ["*"], download, cache_dir=Path("cache"))

    assert download.progress.done == 1_000
    assert download.progress.percent == 100


def test_a_cancel_between_files_leaves_the_rest_alone(hub):
    fake = hub({"a.bin": 100, "b.bin": 100, "c.bin": 100})
    download = downloads.Download()
    download.cancel()

    with pytest.raises(downloads.Cancelled):
        downloads.fetch("repo", ["*"], download, cache_dir=Path("cache"))

    assert fake.downloaded == []


def test_fetch_hands_hub_the_destination_it_was_given(hub):
    """Whisper reads a Hugging Face cache; Parakeet and the cleanup GGUFs read
    a plain directory. Both destinations go through the same call."""
    fake = hub({"model.bin": 800})

    downloads.fetch("repo", ["*"], downloads.Download(), local_dir=Path("here"))

    assert fake.kwargs[0] == {"cache_dir": None, "local_dir": Path("here")}


def test_downloads_go_over_plain_http_rather_than_xet(hub):
    """Measured, not assumed: over Xet a 75 MB model produced exactly one
    progress callback — for the whole 75 MB, after it had landed — and the
    exception a cancel raises from that callback was swallowed, leaving the
    download to run to completion. Both of this module's reasons to exist die
    on that path, so `fetch` takes the plain one."""
    from huggingface_hub import constants

    hub({"model.bin": 800})
    downloads.fetch("repo", ["*"], downloads.Download(), local_dir=Path("here"))

    assert constants.HF_HUB_DISABLE_XET is True
