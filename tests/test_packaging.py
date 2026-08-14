"""The packaging inputs, checked on both OSes (#171).

Everything here is read from source rather than imported. `cadent.spec` is a
PyInstaller script — it only runs with Analysis and friends injected — and
`platform/darwin.py` imports fcntl and pyobjc, so neither can be imported on a
Windows runner. An `ast.literal_eval` of the two files gets what matters
without either, which is the point: these facts have to agree on the machine
that *isn't* building the .app.
"""

from __future__ import annotations

import ast
import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "cadent.spec"
ENTITLEMENTS = ROOT / "packaging" / "cadent.entitlements"
DARWIN = ROOT / "cadent" / "platform" / "darwin.py"


class _Resolve(ast.NodeTransformer):
    """Swap references to earlier module-level literals for their values, so a
    dict that names a constant instead of repeating it stays readable in the
    spec and still evaluates here."""

    def __init__(self, known: dict[str, object]) -> None:
        self._known = known

    def visit_Name(self, node: ast.Name):
        if node.id in self._known:
            return ast.copy_location(ast.Constant(self._known[node.id]), node)
        return node


def literal(path: Path, name: str):
    """The value of a module-level `name = <literal>` assignment.

    Assignments are read in file order, so a later one may refer to an earlier
    one by name — the only non-literal these files contain.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    known: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        try:
            value = ast.literal_eval(_Resolve(known).visit(node.value))
        except ValueError:
            continue        # env reads and the like: not our business
        for target in targets:
            known[target] = value
        if name in targets:
            return value
    raise AssertionError(f"{name} is not a module-level literal in {path.name}")


# ---- app identity ----------------------------------------------------------

def test_bundle_id_matches_the_launch_agent_label():
    """One identity, two files. The LaunchAgent labels this app, macOS files
    the TCC grants under it, and a drift between them would leave a login item
    naming a bundle id nothing on the machine has."""
    assert literal(SPEC, "BUNDLE_ID") == literal(DARWIN, "LAUNCH_AGENT_LABEL")


def test_info_plist_declares_the_same_bundle_id():
    assert literal(SPEC, "INFO_PLIST")["CFBundleIdentifier"] == literal(SPEC, "BUNDLE_ID")


# ---- the Info.plist keys the app cannot start without ----------------------

def test_the_app_is_a_menu_bar_app():
    """LSUIElement is what keeps Cadent out of the Dock and out of its own
    way: injection pastes into the frontmost app (ADR 0001) and overrides key
    off the frontmost app (ADR 0004), so an accessory app is the only kind
    that can show a settings window without becoming either."""
    assert literal(SPEC, "INFO_PLIST")["LSUIElement"] is True


def test_microphone_usage_description_is_present_and_says_something():
    """Not boilerplate: a hardened-runtime app that reaches for the mic with
    no usage string is killed outright rather than prompted for, and the
    string is the sentence the user reads when deciding."""
    reason = literal(SPEC, "INFO_PLIST")["NSMicrophoneUsageDescription"]
    assert "Cadent" in reason and len(reason) > 40


def test_version_keys_are_grafted_on_not_baked_in():
    """The tag supplies the version (no package metadata survives the freeze),
    so the literal must not carry a stale one — only the env-fed lines below
    it may set these."""
    static = literal(SPEC, "INFO_PLIST")
    assert "CFBundleShortVersionString" not in static
    assert "CFBundleVersion" not in static


# ---- the hardened-runtime entitlements -------------------------------------

@pytest.fixture(scope="module")
def entitlements():
    """A malformed plist here fails nowhere but a signing run on a tag, which
    is the worst possible place to find out."""
    with ENTITLEMENTS.open("rb") as f:
        return plistlib.load(f)


def test_library_validation_is_disabled(entitlements):
    """llama.cpp's backends are ctypes-loaded and signed by nobody we know;
    library validation refuses every one of them."""
    assert entitlements["com.apple.security.cs.disable-library-validation"] is True


def test_audio_input_is_entitled(entitlements):
    """Under the hardened runtime the mic needs the entitlement as well as the
    usage string — without it the prompt never appears and capture is silence,
    which is the failure mode macOS gives no error for."""
    assert entitlements["com.apple.security.device.audio-input"] is True


def test_the_app_is_not_sandboxed(entitlements):
    """A sandboxed app may not tap global keystrokes or paste into other
    apps, which is the entire product."""
    assert "com.apple.security.app-sandbox" not in entitlements
