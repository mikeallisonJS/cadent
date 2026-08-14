"""scripts/release.py — the bump inference and text surgery around towncrier (#168)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "release", Path(__file__).parent.parent / "scripts" / "release.py"
)
release = importlib.util.module_from_spec(_spec)
sys.modules["release"] = release
_spec.loader.exec_module(release)


class TestFragmentType:
    def test_valid_names(self):
        assert release.fragment_type(Path("162.feature.md")) == "feature"
        assert release.fragment_type(Path("7.breaking.md")) == "breaking"
        assert release.fragment_type(Path("168.chore.md")) == "chore"

    def test_rejects_unknown_type(self):
        assert release.fragment_type(Path("162.enhancement.md")) is None

    def test_rejects_missing_type(self):
        assert release.fragment_type(Path("162.md")) is None
        assert release.fragment_type(Path("notes.txt")) is None


class TestNextVersion:
    def test_bugfix_bumps_patch(self):
        assert release.next_version("0.2.0", {"bugfix"}) == "0.2.1"

    def test_feature_bumps_minor_and_resets_patch(self):
        assert release.next_version("0.2.3", {"feature", "bugfix"}) == "0.3.0"

    def test_breaking_rides_minor_while_zero_x(self):
        assert release.next_version("0.2.0", {"breaking"}) == "0.3.0"

    def test_breaking_bumps_major_from_one_point_oh(self):
        assert release.next_version("1.2.3", {"breaking", "feature"}) == "2.0.0"

    def test_hidden_types_still_bump_patch(self):
        assert release.next_version("0.2.0", {"chore", "docs"}) == "0.2.1"


class TestWriteVersion:
    def test_rewrites_only_the_project_version(self, tmp_path, monkeypatch):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "cadent"\nversion = "0.2.0"\n'
            '[tool.other]\nrequires = ["setuptools>=68"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(release, "PYPROJECT", pyproject)
        release.write_version("0.3.0")
        assert 'version = "0.3.0"' in pyproject.read_text(encoding="utf-8")

    def test_refuses_ambiguous_version_lines(self, tmp_path, monkeypatch):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('version = "0.2.0"\nversion = "9.9.9"\n', encoding="utf-8")
        monkeypatch.setattr(release, "PYPROJECT", pyproject)
        with pytest.raises(SystemExit):
            release.write_version("0.3.0")


class TestChangelogSection:
    CHANGELOG = (
        "# Changelog\n\n<!-- towncrier release notes start -->\n\n"
        "## v0.3.0 — 2026-08-12\n\n### Features\n\n- The new thing. (#162)\n\n"
        "## v0.2.0 — 2026-08-10\n\nOlder notes.\n"
    )

    def test_extracts_one_version_section(self, tmp_path, monkeypatch):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(self.CHANGELOG, encoding="utf-8")
        monkeypatch.setattr(release, "CHANGELOG", changelog)
        section = release.changelog_section("0.3.0")
        assert "The new thing" in section
        assert "Older notes" not in section
        assert "## v" not in section

    def test_missing_version_exits(self, tmp_path, monkeypatch):
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(self.CHANGELOG, encoding="utf-8")
        monkeypatch.setattr(release, "CHANGELOG", changelog)
        with pytest.raises(SystemExit):
            release.changelog_section("0.9.9")


class TestPendingFragments:
    def test_collects_types_and_skips_readme(self, tmp_path, monkeypatch):
        (tmp_path / "README.md").write_text("contract", encoding="utf-8")
        (tmp_path / "162.feature.md").write_text("x", encoding="utf-8")
        (tmp_path / "163.chore.md").write_text("y", encoding="utf-8")
        monkeypatch.setattr(release, "FRAGMENT_DIR", tmp_path)
        assert set(release.pending_fragments().values()) == {"feature", "chore"}

    def test_invalid_name_exits(self, tmp_path, monkeypatch):
        (tmp_path / "162.enhancement.md").write_text("x", encoding="utf-8")
        monkeypatch.setattr(release, "FRAGMENT_DIR", tmp_path)
        with pytest.raises(SystemExit):
            release.pending_fragments()
