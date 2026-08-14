# Cadent — Agent Instructions

## Agent skills

### Issue tracker

Issues and specs live as GitHub Issues on this repo, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

Numeric references already present in code comments, ADRs and specs (`#45`, `#130`, `ADR 0004`, …) predate this repository and do **not** resolve against its tracker — a `#130` here is not the `#130` a comment means. Treat them as historical labels for decisions, which the surrounding prose always states in full. Don't "fix" them into links, and don't renumber against them.

### Triage labels

The five canonical triage roles are used as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Releases

Every PR that touches code adds a changelog fragment in `changelog.d/`; a
bot-maintained release PR aggregates them and merging it ships. See
`docs/agents/releases.md`.

### Domain docs

Single-context layout — one `CONTEXT.md` at the repo root plus `docs/adr/` for architectural decisions. See `docs/agents/domain.md`.
