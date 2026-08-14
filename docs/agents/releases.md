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
   beneath it), and dispatches `build-installer.yml` at the tag. The tag is
   the trigger contract for platform builds — future macOS/store workflows
   hook the same tag.

Local dry-run: `uv run python scripts/release.py next-version` shows what the
pending fragments add up to.
