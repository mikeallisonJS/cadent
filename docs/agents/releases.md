# Releases

Changeset-style releases (#168): each PR declares its own release intent as a
fragment; a bot-maintained release PR aggregates them; merging that PR ships.

## The per-PR contract

Any PR touching `cadent/`, `packaging/`, `scripts/`, or `pyproject.toml`
must add exactly one fragment file (the `changelog-check` workflow hard-fails
without one):

- **Path**: `changelog.d/<issue>.<type>.md` — issue number of the ticket the
  PR closes.
- **Types and the bump they imply** (highest pending type wins; mapping lives
  in `scripts/release.py`):
  - `breaking` → minor while 0.x, major from 1.0
  - `feature` → minor
  - `bugfix` → patch
  - `chore`, `docs` → patch, **never rendered** into the changelog. This is
    the escape hatch for housekeeping PRs — the check still passes, the notes
    stay clean.
- **Body**: one or two sentences, user-facing voice — what changed for the
  person running the app.

## The release flow

1. Each push to `main` runs `release-pr.yml`: it computes the next version
   from pending fragment types, bumps `pyproject.toml` (the single version
   source — `cadent/__init__.py` reads it via `importlib.metadata`),
   renders the fragments into `CHANGELOG.md` via towncrier, and keeps a
   `release/next` PR up to date with all of it.
2. A human merges the release PR. That is the release decision.
3. `tag-release.yml` tags the merge commit `vX.Y.Z`, publishes the GitHub
   Release with the curated changelog section (auto-generated notes collapsed
   beneath it), and dispatches the platform builds at the tag —
   `build-installer.yml` (Windows, Inno Setup `.exe`),
   `build-installer-macos.yml` (macOS, drag-to-Applications `.dmg`) and
   `build-installer-linux.yml` (Linux, `.tar.zst` + AppImage; the AUR
   `cadent-bin` PKGBUILD in `packaging/aur/` repacks the tarball by hand).
   The tag is the trigger contract: a future platform or store workflow hooks
   the same tag and attaches to the same release. The legs are independent,
   so a failure on one still ships the others.

The macOS leg has three signing states, and the secrets decide which one a
release lands on. The workflow header names all six and what each does.

- **Ad-hoc** (no `MACOS_*` secrets) — what ships today. Enough for arm64 to
  load the app at all, and nothing more: users right-click ▸ Open past
  Gatekeeper once, and re-grant Accessibility after every update, because an
  ad-hoc signature changes with each build and macOS keys TCC off it.
- **Developer ID, not notarized** (the three certificate secrets) — grants now
  survive updates, but Gatekeeper still stops the first open, so the
  right-click stays. The workflow warns when a build lands here.
- **Developer ID, notarized** (all six) — double-click, no warning, grants
  persist. Worth reaching for first if the Mac build gets real users.

A partial set of either trio fails the build rather than falling back: half a
configuration is a mistake, not a request for an ad-hoc release.

Local dry-run: `uv run python scripts/release.py next-version` shows what the
pending fragments add up to.
