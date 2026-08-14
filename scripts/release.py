"""Release plumbing around towncrier (#168).

Towncrier renders CHANGELOG.md; everything it doesn't do lives here:

  next-version   print the version the pending fragments add up to (empty if none)
  prepare        bump pyproject.toml, render CHANGELOG.md, drop hidden fragments
  notes          print the CHANGELOG.md section for a version (release body)

Fragments live in changelog.d/ as <issue>.<type>.md. Types breaking/feature/
bugfix render into the changelog; chore/docs are the escape hatch — they
satisfy the PR check, decide nothing above a patch bump, and never render.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = REPO / "changelog.d"
PYPROJECT = REPO / "pyproject.toml"
CHANGELOG = REPO / "CHANGELOG.md"

VALID_TYPES = {"breaking", "feature", "bugfix", "chore", "docs"}
HIDDEN_TYPES = {"chore", "docs"}  # never rendered; towncrier never sees them


def fragment_type(path: Path) -> str | None:
    """`168.feature.md` -> `feature`; None when the name doesn't fit the contract."""
    parts = path.name.split(".")
    if len(parts) == 3 and parts[2] == "md" and parts[1] in VALID_TYPES:
        return parts[1]
    return None


def pending_fragments() -> dict[Path, str]:
    if not FRAGMENT_DIR.is_dir():
        return {}
    found = {}
    for path in sorted(FRAGMENT_DIR.iterdir()):
        if path.name == "README.md" or not path.is_file():
            continue
        kind = fragment_type(path)
        if kind is None:
            raise SystemExit(f"not a valid fragment name: changelog.d/{path.name}")
        found[path] = kind
    return found


def current_version() -> str:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def next_version(current: str, types: set[str]) -> str:
    major, minor, patch = (int(p) for p in current.split("."))
    # Semver-0: breaking changes ride the minor rung until 1.0 exists.
    if "breaking" in types and major >= 1:
        return f"{major + 1}.0.0"
    if "breaking" in types or "feature" in types:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    pattern = re.compile(r'^version = "[^"]+"$', flags=re.MULTILINE)
    if len(pattern.findall(text)) != 1:
        raise SystemExit("expected exactly one version line in pyproject.toml")
    PYPROJECT.write_text(pattern.sub(f'version = "{version}"', text), encoding="utf-8")


def changelog_section(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(
        rf"^## v{re.escape(version)}[^\n]*\n(.*?)(?=^## v|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise SystemExit(f"no CHANGELOG.md section for v{version}")
    return match.group(1).strip()


def cmd_next_version() -> None:
    fragments = pending_fragments()
    if fragments:
        print(next_version(current_version(), set(fragments.values())))


def cmd_prepare() -> None:
    fragments = pending_fragments()
    if not fragments:
        raise SystemExit("no pending fragments in changelog.d/")
    version = next_version(current_version(), set(fragments.values()))
    write_version(version)
    for path, kind in fragments.items():
        if kind in HIDDEN_TYPES:
            path.unlink()
    subprocess.run(
        [sys.executable, "-m", "towncrier", "build", "--yes", "--version", version],
        cwd=REPO,
        check=True,
    )
    print(version)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next-version")
    sub.add_parser("prepare")
    notes = sub.add_parser("notes")
    notes.add_argument("--version", required=True)
    args = parser.parse_args()

    if args.command == "next-version":
        cmd_next_version()
    elif args.command == "prepare":
        cmd_prepare()
    elif args.command == "notes":
        print(changelog_section(args.version))


if __name__ == "__main__":
    main()
