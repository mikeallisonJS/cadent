"""The Linux platform's pure parts, on every OS (spec §1, §2.4, §4): tier
detection from the environment, the Capabilities columns each tier fills,
the tier line, the keysym table and the trigger grammar. Nothing here opens
a bus, a display or a socket — the adapters' live behaviour is a hardware
item (spec §12)."""

import sys

import pytest

from cadent.chord import Action, ChordStateMachine, TapChord, parse_combo
from cadent.inject import parse_paste_chord
from cadent.platform.keycodes import LINUX_KEYCODES

linux = pytest.importorskip("cadent.platform.linux",
                            reason="jeepney and python-xlib are Linux-only dependencies")
from cadent.platform.linux import wayland  # noqa: E402

X11_KDE = {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}
WAYLAND_KDE = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}
WAYLAND_GNOME = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
WAYLAND_HYPR = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "Hyprland"}


# ---- tier detection (§1.1, §1.2) ---------------------------------------------

def test_x11_is_whole_on_any_desktop():
    for desktop in ("KDE", "GNOME", "XFCE", "ubuntu:GNOME", ""):
        env = {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": desktop}
        assert linux.detect_tier(env).tier == linux.WHOLE


def test_wayland_on_plasma_and_wlroots_is_portal():
    assert linux.detect_tier(WAYLAND_KDE).tier == linux.PORTAL
    hypr = linux.detect_tier(WAYLAND_HYPR)
    assert hypr.tier == linux.PORTAL
    assert hypr.wlroots is True
    assert linux.detect_tier(WAYLAND_KDE).wlroots is False


def test_wayland_on_gnome_is_reduced_however_the_list_spells_it():
    """`XDG_CURRENT_DESKTOP` is a list (`ubuntu:GNOME`), matched on any entry."""
    assert linux.detect_tier(WAYLAND_GNOME).tier == linux.REDUCED
    assert linux.detect_tier({"XDG_SESSION_TYPE": "wayland",
                              "XDG_CURRENT_DESKTOP": "GNOME"}).tier == linux.REDUCED
    assert linux.detect_tier({"XDG_SESSION_TYPE": "wayland",
                              "XDG_CURRENT_DESKTOP": "pop:GNOME"}).tier == linux.REDUCED


def test_a_missing_session_variable_falls_back_to_the_display_sockets():
    assert linux.detect_tier({"WAYLAND_DISPLAY": "wayland-0"}).tier == linux.PORTAL
    assert linux.detect_tier({"DISPLAY": ":0"}).tier == linux.WHOLE
    assert linux.detect_tier({}).tier == linux.WHOLE


# ---- the tier line (§9.4, ADR 0012) --------------------------------------------

def test_the_tier_line_names_the_tier_and_its_consequence():
    assert linux.support_tier_summary(linux.detect_tier(X11_KDE)) == (
        "X11 session on KDE Plasma — Whole support: everything Cadent does works here.")
    assert linux.support_tier_summary(linux.detect_tier(WAYLAND_KDE)) == (
        "Wayland session on KDE Plasma — Portal support: your desktop owns the hotkey, "
        "Cadent won't learn on its own which apps need pasting, and there's no overlay.")
    assert linux.support_tier_summary(linux.detect_tier(WAYLAND_GNOME)) == (
        "Wayland session on GNOME — Reduced support: no overlay, no per-app overrides.")


def test_an_unknown_desktop_drops_the_desktop_clause_rather_than_printing_it():
    env = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "weird:thing"}
    line = linux.support_tier_summary(linux.detect_tier(env))
    assert line.startswith("Wayland session — Portal support")
    assert "weird" not in line


# ---- the Capabilities columns (§2.4) ---------------------------------------------

def test_whole_is_windows_parity_with_an_empty_override_table():
    caps = linux.capabilities(linux.detect_tier(X11_KDE), per_app_overrides=True, paste=True)
    assert caps.support_tier == "whole"
    assert caps.default_injection_strategy == "type"
    assert caps.injection_rungs == ("type", "paste")
    assert caps.auto_learn_overrides is True
    assert caps.permission is None
    assert caps.overlay == "windowed"
    assert caps.per_app_overrides is True
    assert caps.default_overrides == ()
    assert caps.default_combo == "<ctrl>+<cmd>"
    assert caps.default_cleanup_combo == "<ctrl>+<shift>+<alt>"


def test_the_wayland_tiers_paste_first_with_a_keysym_chord_and_no_overlay():
    for env, tier in ((WAYLAND_KDE, "portal"), (WAYLAND_GNOME, "reduced")):
        caps = linux.capabilities(linux.detect_tier(env),
                                  per_app_overrides=tier == "portal", paste=True)
        assert caps.support_tier == tier
        assert caps.default_injection_strategy == "clipboard"
        assert caps.injection_rungs == ("paste", "type")
        assert caps.auto_learn_overrides is False
        assert caps.permission is not None and caps.permission.name == "portal"
        assert caps.overlay is None
        assert caps.default_combo == "<ctrl>+<cmd>+space"
        assert caps.default_cleanup_combo == "<ctrl>+<cmd>+c"
        assert caps.tray_click_toggles_pause is False
    assert linux.capabilities(linux.detect_tier(WAYLAND_GNOME), per_app_overrides=False,
                              paste=True).per_app_overrides is False


def test_a_run_with_no_paste_mechanism_collapses_to_typing_only():
    """§1.3: not a fall-through and not a fourth tier — one fewer rung, and
    the default strategy follows it so a fresh config does not ask to paste
    through a mechanism the session lacks."""
    caps = linux.capabilities(linux.detect_tier(WAYLAND_GNOME), per_app_overrides=False,
                              paste=False)
    assert caps.injection_rungs == ("type",)
    assert caps.default_injection_strategy == "type"


def test_the_shared_facts_hold_on_every_tier():
    for env in (X11_KDE, WAYLAND_KDE, WAYLAND_GNOME):
        caps = linux.capabilities(linux.detect_tier(env), per_app_overrides=True, paste=True)
        assert caps.stt_runtimes == {"faster-whisper": ("auto", "cuda", "cpu"),
                                     "parakeet": ("auto", "cuda", "cpu")}
        assert caps.gpu_only_engines == frozenset()
        assert caps.show_runtime_combo is True
        assert caps.gpu_pack_available is True
        assert set(caps.gpu_pack_editions) == {"faster-whisper", "parakeet"}
        assert caps.app_picker is True
        assert caps.app_identity_placeholder == "org.mozilla.firefox"
        assert caps.mic_permission_hint is None
        assert caps.tray_icon_painted_by_os is False
        assert caps.modifier_captions["cmd"] == "Super"
        assert caps.theme_subtitle == "Follows your desktop's colour scheme"
        assert caps.parakeet_on_cpu_recommended is True


def test_the_portal_wording_never_names_a_desktop():
    """ADR 0012: "your desktop", "the prompts" — never GNOME, Plasma or portal,
    and never a count of dialogs."""
    grant = linux.PORTAL_GRANT
    for text in (grant.banner, grant.wizard_body, grant.waiting, grant.granted):
        assert "your desktop" in text.lower() or "Permission" in text
        for word in ("GNOME", "Plasma", "portal", "two prompts", "one prompt"):
            assert word not in text


# ---- the keysym table (ADR 0008) -----------------------------------------------

def test_the_linux_table_parses_every_default_chord():
    assert parse_combo("<ctrl>+<cmd>", LINUX_KEYCODES) == \
        [frozenset({0xFFE3, 0xFFE4}), frozenset({0xFFEB, 0xFFEC})]
    assert parse_combo("<ctrl>+<cmd>+space", LINUX_KEYCODES)[-1] == frozenset({0x20})
    assert parse_combo("<ctrl>+<cmd>+c", LINUX_KEYCODES)[-1] == frozenset({ord("c")})
    assert parse_combo("<ctrl>+<shift>+<alt>", LINUX_KEYCODES)[-1] == frozenset({0xFFE9, 0xFFEA})
    assert parse_combo("<super>+f9", LINUX_KEYCODES) == \
        [frozenset({0xFFEB, 0xFFEC}), frozenset({0xFFBE + 8})]


def test_the_linux_table_has_no_ord_fallback_and_names_keys_back():
    with pytest.raises(ValueError):
        parse_combo("<ctrl>+;", LINUX_KEYCODES)
    assert parse_paste_chord("ctrl+v", LINUX_KEYCODES) == [0xFFE3, ord("v")]
    assert parse_paste_chord("ctrl+shift+v", LINUX_KEYCODES) == [0xFFE3, 0xFFE1, ord("v")]
    assert LINUX_KEYCODES.part_for(0xFFEC, sided=True) == "<rcmd>"
    assert LINUX_KEYCODES.part_for(0x20) == "space"
    assert LINUX_KEYCODES.part_for(0xFFBE) == "f1"
    assert LINUX_KEYCODES.part_for(0xFFE5) is None     # Caps_Lock


def test_the_chord_machine_runs_on_keysyms():
    """The portal tap synthesizes exactly these; the X11 tap reports them.
    Either way hold-and-release drives the untouched machine."""
    sm = ChordStateMachine("<ctrl>+<cmd>+space", "hold", min_hold_s=0.0)
    acts = []
    for keysym in wayland.chord_keysyms("<ctrl>+<cmd>+space"):
        acts += sm.on_event(keysym, True, False, 0.0)
    assert Action.START in acts
    assert Action.STOP in sm.on_event(0x20, False, False, 1.0)


def test_the_cleanup_tap_fires_on_a_synthesized_press_and_release():
    tap = TapChord("<ctrl>+<cmd>+c")
    keys = wayland.chord_keysyms("<ctrl>+<cmd>+c")
    assert not any(tap.on_event(k, True, False) for k in keys)
    assert any(tap.on_event(k, False, False) for k in reversed(keys))


# ---- the trigger grammar (ADR 0008) --------------------------------------------

def test_chords_spell_as_portal_triggers():
    assert wayland.trigger_for("<ctrl>+<cmd>+space") == "CTRL+LOGO+space"
    assert wayland.trigger_for("<ctrl>+<cmd>+c") == "CTRL+LOGO+c"
    assert wayland.trigger_for("<rctrl>+<shift>+f9") == "CTRL+SHIFT+F9"
    assert wayland.trigger_for("<alt_r>+j") == "ALT+j"


def test_a_modifiers_only_chord_cannot_be_a_trigger():
    """The freedesktop grammar needs a keysym: the shipped Ctrl+Super is
    unbindable on Wayland, so the tier default binds instead and the copy
    says so."""
    assert wayland.trigger_for("<ctrl>+<cmd>") is None
    assert wayland.trigger_for("<ctrl>+<shift>+<alt>") is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the real factory")
def test_the_factory_builds_a_linux_platform_here():
    from cadent import platform

    plat = platform.current()
    assert plat.capabilities.support_tier in ("whole", "portal", "reduced")
    assert plat.capabilities.support_tier_summary
    assert plat.focused_app.permission_granted() in (True, False)
    assert plat.hotkey_tap.available() in (True, False)
    assert isinstance(dict(plat.hotkey_tap.bindings()), dict)
    assert plat.desktop.tray_ink().startswith("#")
