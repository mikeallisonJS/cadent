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

The Windows leg has two, gated the same way on the six `AZURE_SIGN_*` secrets
the workflow header names.

- **Unsigned** (no secrets) — what the M3 charter accepted. The app installs
  and runs; SmartScreen warns on first launch, and the Microsoft Store will
  not accept the installer at all.
- **Signed** (all six) — Azure Artifact Signing, formerly Trusted Signing.
  No SmartScreen warning, and the installer meets the Store's signing bar.

A partial set fails the build, for the same reason the macOS trios do.

Signing runs twice per Windows build, on either side of Inno Setup, because
ISCC compresses the payload into the installer: files signed after ISCC are
signed in `dist/` and unsigned in the artefact anyone downloads.
`scripts/sign_windows.py` handles both passes, skips files that already carry
a valid signature (most of Qt and CPython arrive signed, and Artifact Signing
bills per signature), and re-verifies afterwards so a signature that failed to
timestamp is caught now rather than when a certificate expires.

Local dry-run: `uv run python scripts/release.py next-version` shows what the
pending fragments add up to.

## Microsoft Store

Cadent lists on the Store through the **MSI/EXE submission path**, not as an
MSIX: Partner Center takes a URL to the signed Inno installer and the Store
hands users that same binary. The choice is deliberate. MSIX would get free
signing, but it virtualizes exactly the mechanisms the app is built on — the
`HKCU\...\Run` autostart value becomes a `windows.startupTask` extension,
`%LOCALAPPDATA%\Cadent` and the PATH-prepended GPU support pack move under
package-private paths, and the pack's runtime DLL download runs into the
policy on dynamically included code. The EXE path changes no runtime
behaviour at all.

What that path requires, and where each requirement is kept honest:

| Requirement | Where it is met |
| --- | --- |
| Installer is `.exe` or `.msi` | `packaging/cadent.iss` |
| Installer **and every PE file inside it** chain to a Microsoft-trusted root | `scripts/sign_windows.py`, both passes |
| Installs with no UI (UAC is allowed; we never even prompt) | `PrivilegesRequired=lowest`, exercised by the `/VERYSILENT` step in `build-installer.yml` |
| Standalone installer — no downloading during setup | the onedir payload is embedded; model downloads are first-run app behaviour, not setup |
| Versioned download URL whose binary never changes | the `vX.Y.Z` GitHub Release asset, which `tag-release.yml` publishes once |

Submitting a release is therefore: ship the tag as usual, wait for
`build-installer.yml` to attach the signed installer, then point the Partner
Center submission at that release's asset URL. A new version means a new
submission with a new URL — never repointing an old one, which the Store
forbids.

Account setup, Azure Artifact Signing provisioning and the first submission
are one-time human steps: run `scripts/store_setup_wizard.sh`.
