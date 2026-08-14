# Research: the intermittent access violation in the Qt test suite

Ticket: #119
Date: 2026-08-12

## TL;DR

A full `pytest tests` run died at random with a Windows access violation
(exit `0xC0000005` / `139`) inside `qt_app.processEvents()`, with no failing
assertion and nothing in the exit code to say which test.

The cause was three defects stacked:

1. `PaneContext` defaults `vocab_path`/`snippets_path` to the **real**
   `%LOCALAPPDATA%\Cadent\vocabulary.json` and `snippets.json`. Every
   `SettingsWindow` a test built without passing paths therefore put a live
   `QFileSystemWatcher` on the developer's own two files.
2. Roughly 28 tests in `test_settings_ui.py` build a `SettingsWindow` and never
   destroy it. By the end of that file ~500 top-level widgets and ~13,900
   widgets were alive — and with them ~60 live watchers.
3. `VocabularyPane`'s watcher was unparented, so its lifetime was Python's
   rather than the pane's: destroying the pane did not stop the watch. This
   one did not contribute the sixty — windows that are never destroyed keep
   their watchers however they are parented — but it is why tidying the
   windows up would not, on its own, have stopped the watching promptly.

A `QFileSystemWatcher` on Windows is a native `ReadDirectoryChangesW` thread.
Sixty of them, all watching the same two real files, reporting into a process
that deliberately runs **no event loop** — until `test_a11y_coverage.py` shows
five windows at once and calls `processEvents()`, which pumps the lot. That is
where it faulted.

This is also why CI never saw it. On a machine with no
`%LOCALAPPDATA%\Cadent\vocabulary.json` the pane's `if path.exists()` is
False, nothing is watched, and the crash cannot happen. It reproduced 4 runs
out of 4 on a developer machine with those files present.

## What made it hard to attribute

Nothing about the failure points at any of the above. There is no assertion,
the process is simply killed, and the faulting frame is whichever
`processEvents()` happened to run first after the state accumulated — so the
crash site *moves* when timing changes. An earlier pass at #119 (`6bca54c`)
found and fixed two genuine races on the strength of that signature (a
cross-thread `emit` racing wizard destruction; probe threads outliving their
test) without the crash rate going to zero, because neither was this.

The tell, once measured, was that the crash landed on a fixed *position* in
the run — test 213 of 224 — rather than on a fixed test. Deselecting the Tab
walk moved the fault to the next test that called `processEvents()`, at the
same index. That is an accumulating resource, not a bad event.

## The experiments

All against `pytest tests/test_settings_ui.py tests/test_wizard.py
tests/test_a11y_coverage.py`, which is the smallest combination that
reproduces. `test_a11y.py` is not needed; either of `test_settings_ui.py` or
`test_wizard.py` alone is not enough.

**A full `pytest tests` run is not a useful measurement of this**, whatever
the ticket says. Collection is alphabetical, so `test_a11y_coverage.py` runs
*second* — long before `test_settings_ui.py` has leaked anything for it to
trip over. Measured on `origin/main`, in a clean worktree, before any of this
work: 8 full runs, 0 crashes. The acceptance soak below therefore uses the
four files the ticket names, where the before/after is 4/4 against 0/20.

| Variant | Crashes |
|---|---|
| baseline | 4 / 4 |
| `test_a11y_coverage.py` alone | 0 / 4 |
| `test_settings_ui.py` + `test_a11y_coverage.py` | 0 / 4 |
| `test_wizard.py` + `test_a11y_coverage.py` | 0 / 4 |
| destroy every window a test leaves behind | 0 / 6 |
| watchers kept alive **forever**, nothing else changed | 3 / 3 |
| watchers kept alive but `removePaths()` at teardown | 0 / 3 |
| watchers destroyed at teardown | 0 / 4 |
| `PaneContext` paths pointed at a directory that does not exist | 0 / 4 |

The two middle rows are the discriminator: same object count, same leak, same
timing shape — the only variable is whether the watchers are *watching*. Alive
and watching crashes; alive and idle does not.

## The fix

- `VocabularyPane`'s watcher is parented to the pane, so destroying the pane
  stops the watch (`cadent/settings_ui/vocab.py`).
- `PaneContext` resolves its three paths through `cfg` at construction instead
  of binding them as class-definition-time defaults, which is what makes them
  redirectable (`cadent/settings_ui/context.py`).
- `_panes_never_see_the_real_data_dir` in `tests/conftest.py` points those
  three at a directory that does not exist, for every test. Independently
  worth having: before it, ~28 tests loaded the developer's own vocabulary
  into the table they were asserting about.
- `_no_window_outlives_its_test` in `tests/conftest.py` destroys the top-level
  widgets a test leaves behind, and `tests/test_suite_guards.py` tests that it
  does. This is the general net rather than the specific fix — the leak is
  what turns one stray watcher into sixty — and it took the three-file
  combination from ~36s to ~19s, because `setStyleSheet()` re-polishes every
  live widget.

`scripts/soak.py` is the runner used throughout: `uv run python scripts/soak.py
20 tests` repeats a pytest invocation and counts native crashes, which plain
`pytest` cannot report because the process never survives to report anything.

Result: **0 crashes in 20 consecutive runs** of the four-file reproducer,
against 4 in 4 before.

## The deadlock the fix uncovered

Soaking for that number turned up a second, unrelated defect — a hard hang,
twice, with every thread idle and the process at ~1% CPU. `py-spy dump
--native` on the wedged process named it:

| Thread | Holds | Blocked wanting |
|---|---|---|
| Main, in `QLabel::QLabel` → `QObject::connectImpl` | the GIL | Qt's signal/slot connection mutex |
| Wizard probe, in `QObject::disconnect` → `Sbk_GetPyOverride` → `PyGILState_Ensure` | that mutex | the GIL |

Building a widget takes the connection mutex while the builder holds the GIL.
A garbage collection on the probe thread destroys some other `QObject`, which
takes the same mutex and *then* asks shiboken for the GIL back.

`SetupWizard.__init__` started the probe thread and then built its first page,
so it arranged that overlap on every open and only needed the collector to
land in the window. It now renders first. That removes the arranged overlap
rather than making the collision impossible — any thread can trigger a
collection at any time.

This is a hazard in the shipped app too, not only under test. It surfaced now
because the leaked windows were also what kept their Python wrappers
referenced: tidying them up is what made `QObject` wrappers collectible in the
first place, and a background thread can do the collecting.

Measured, full `pytest tests` runs: `origin/main` 8 clean / 0 hangs; this
branch before the wizard change, 2 hangs in ~13; after it, 12 clean / 0 hangs.

## What was not established

The faulting instruction of the *access violation*. The mechanism above is
established by the experiment table rather than by a stack: the causal claim —
remove the watching and the crash goes, restore it and the crash returns — is
reproducible in both directions, but the exact structure the watcher
notification touches during `processEvents()` is not identified. (The
deadlock, by contrast, has its stacks; `py-spy` would have shortened the first
half of this investigation considerably, and is worth reaching for early next
time.)

Nor is the deadlock proven gone — 12 clean runs against a rate of roughly 2 in
13 is consistent with the fix but does not establish absence. If it returns,
the next thing to try is reclaiming the destroyed windows' wrappers on the main
thread — a `gc.collect()` inside `_no_window_outlives_its_test` after the
destroy — so a background thread has less QObject garbage to trip over. That
was deliberately not added on spec.
