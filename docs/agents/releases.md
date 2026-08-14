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
   beneath it), and dispatches both platform builds at the tag —
   `build-installer.yml` (Windows, Inno Setup `.exe`) and
   `build-installer-macos.yml` (macOS, drag-to-Applications `.dmg`). The tag
   is the trigger contract: a future platform or store workflow hooks the
   same tag and attaches to the same release. The two legs are independent,
   so a failure on one still ships the other.

The macOS leg is ad-hoc signed unless the Developer ID secrets are set — the
workflow header names the six and what each does. Unsigned means users
right-click ▸ Open once, and re-grant Accessibility after every update, since
macOS keys TCC off the signature. That is the thing worth fixing first if the
Mac build ever gets real users.

Local dry-run: `uv run python scripts/release.py next-version` shows what the
pending fragments add up to.
