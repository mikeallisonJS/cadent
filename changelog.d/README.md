# Changelog fragments

Every PR that touches `cadent/`, `packaging/`, `scripts/`, or
`pyproject.toml` must add one file here (CI enforces it). Full contract:
`docs/agents/releases.md`.

- **Name**: `<issue>.<type>.md` — e.g. `162.feature.md`.
- **Types**: `breaking`, `feature`, `bugfix` render into the changelog;
  `chore` and `docs` don't render — they exist so housekeeping PRs pass the
  check without polluting release notes.
- **Body**: one or two sentences, user-facing voice, present tense — what
  changed for the person running the app, not how.

Fragments are consumed (deleted) by the release PR that ships them.
