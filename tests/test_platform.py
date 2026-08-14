"""The platform seam (spec §1, ADR 0005): the factory, the fact table, the
keycode tables, and the strategy-rename read alias."""

import sys
from dataclasses import FrozenInstanceError

import pytest

from cadent import config, platform
from cadent.chord import parse_combo
from cadent.inject import parse_paste_chord
from cadent.platform.keycodes import DARWIN_KEYCODES, WIN32_KEYCODES


def test_current_is_built_once():
    assert platform.current() is platform.current()


def test_current_matches_this_os():
    plat = platform.current()
    if sys.platform == "win32":
        assert type(plat.keyboard).__module__.endswith("platform.win32")
    elif sys.platform == "darwin":
        assert type(plat.autostart).__module__.endswith("platform.darwin")
        assert type(plat.hardware).__module__.endswith("platform.darwin")
        assert type(plat.single_instance).__module__.endswith("platform.darwin")
        assert type(plat.desktop).__module__.endswith("platform.darwin")
        assert type(plat.keyboard).__module__.endswith("platform.darwin")
        assert type(plat.clipboard).__module__.endswith("platform.darwin")
        assert type(plat.focused_app).__module__.endswith("platform.darwin")
        assert type(plat.hotkey_tap).__module__.endswith("platform.darwin")
    else:
        assert type(plat.keyboard).__module__.endswith("platform.fallback")


def test_capabilities_are_frozen_facts():
    caps = platform.current().capabilities
    with pytest.raises(FrozenInstanceError):
        caps.autostart_label = "Start with SkyNet"


def test_capabilities_carry_the_injection_column():
    """The §1.3 injection facts: win32 keeps the pre-seam values; darwin's
    column landed with #144 (paste by default, ADR 0001)."""
    caps = platform.current().capabilities
    if sys.platform == "darwin":
        assert caps.default_injection_strategy == "clipboard"
        assert caps.injection_rungs == ("paste", "type")
        assert caps.paste_chord == "cmd+v"
        assert caps.auto_learn_overrides is False
        assert caps.default_overrides == ()
        assert caps.default_override_reasons == {}
        assert caps.app_identity_placeholder == "com.example.app"
        assert caps.autostart_label == "Start at login"
    else:
        assert caps.default_injection_strategy == "type"
        assert caps.injection_rungs == ("type", "paste")
        assert caps.paste_chord == "ctrl+v"
        assert caps.auto_learn_overrides is True
        assert caps.app_identity_placeholder == "app.exe"
        assert caps.autostart_label == "Start with Windows"
        assert {o.process for o in caps.default_overrides} \
            == {o.process for o in config._default_overrides()}
        assert set(caps.default_override_reasons) <= \
            {o.process for o in caps.default_overrides}
    # The runtime column (#146) has its own test below.


# ---- the keycode table (§1.2: either OS's chord logic tests anywhere) --------

def test_win32_table_parses_the_default_combos():
    assert parse_combo("<ctrl>+<cmd>", WIN32_KEYCODES) == \
        [frozenset({0x11, 0xA2, 0xA3}), frozenset({0x5B, 0x5C})]
    assert parse_combo("<ctrl>+<alt>+f9", WIN32_KEYCODES)[-1] == frozenset({0x78})
    assert parse_combo("<ctrl>+j", WIN32_KEYCODES)[-1] == frozenset({ord("J")})


def test_win32_table_keeps_the_ord_fallback():
    """Hand-edited configs could always name any single character; the VK for
    A-Z/0-9 is its ASCII uppercase code, and the table keeps that contract."""
    assert parse_combo("<ctrl>+;", WIN32_KEYCODES)[-1] == frozenset({ord(";")})


def test_paste_chord_parses_against_the_table():
    assert parse_paste_chord("ctrl+v", WIN32_KEYCODES) == [0x11, ord("V")]
    assert parse_paste_chord("ctrl+shift+v", WIN32_KEYCODES) == [0x11, 0x10, ord("V")]
    # Unrecognized parts drop; a fully unparseable chord returns [] and the
    # caller falls back to Capabilities.paste_chord, the platform's spelling.
    assert parse_paste_chord("bogus+nothing", WIN32_KEYCODES) == []


def test_darwin_table_parses_the_default_combos():
    """The shipped combos must parse on a Mac from day one — an empty group
    table would raise at startup. Both sides of each modifier count as the
    part, mirroring the VK groups."""
    assert parse_combo("<ctrl>+<cmd>", DARWIN_KEYCODES) == \
        [frozenset({59, 62}), frozenset({55, 54})]
    assert parse_combo("<ctrl>+<shift>+<alt>", DARWIN_KEYCODES) == \
        [frozenset({59, 62}), frozenset({56, 60}), frozenset({58, 61})]


def test_darwin_table_parses_function_keys():
    """Carbon F-key codes are scattered, not a contiguous range like the VKs
    (#145); F21+ has no Carbon code at all and must raise like any other
    unknown part rather than land on an unrelated key."""
    assert parse_combo("<ctrl>+<alt>+f9", DARWIN_KEYCODES)[-1] == frozenset({101})
    assert parse_combo("<cmd>+f1", DARWIN_KEYCODES)[-1] == frozenset({122})
    assert parse_combo("<cmd>+f20", DARWIN_KEYCODES)[-1] == frozenset({90})
    with pytest.raises(ValueError):
        parse_combo("<cmd>+f21", DARWIN_KEYCODES)


def test_darwin_chord_machine_runs_on_carbon_events(monkeypatch):
    """§1.2's promise made concrete: pin a darwin-table platform and the
    untouched ChordStateMachine holds and releases on Carbon codes — the
    default combo drives dictation on a Mac with no machine changes (#145)."""
    import dataclasses

    from conftest import make_platform

    from cadent import platform as platform_pkg
    from cadent.chord import Action, ChordStateMachine

    plat = make_platform()
    caps = dataclasses.replace(plat.capabilities, keycode_table=DARWIN_KEYCODES)
    monkeypatch.setattr(platform_pkg, "_current",
                        dataclasses.replace(plat, capabilities=caps))
    sm = ChordStateMachine("<ctrl>+<cmd>", "hold", min_hold_s=0.2)
    acts = sm.on_event(59, True, False, 0.0)      # left ctrl down
    acts += sm.on_event(54, True, False, 0.0)     # *right* cmd down
    assert Action.START in acts
    acts = sm.on_event(54, False, False, 1.0)
    assert Action.STOP in acts


def test_darwin_capabilities_carry_the_accessibility_preflight():
    """ADR 0002: one permission — the darwin column names Accessibility as
    its preflight; every other platform has none."""
    from cadent.platform import fallback

    assert fallback.CAPABILITIES.permission_preflight is None
    if sys.platform == "darwin":
        caps = platform.current().capabilities
        assert caps.permission_preflight == "accessibility"


def test_darwin_capabilities_carry_the_runtime_column():
    """ADR 0003 (#146): one speech rung — both engines run on the CPU and
    nothing else, no engine needs a GPU to be worth offering, the runtime
    combo would offer one choice twice, and Metal ships in the build so there
    is no pack to download. win32 keeps its pre-seam values."""
    from cadent.platform import fallback

    assert fallback.CAPABILITIES.gpu_only_engines == frozenset({"parakeet"})
    assert fallback.CAPABILITIES.gpu_pack_available is True
    if sys.platform == "darwin":
        caps = platform.current().capabilities
        assert caps.stt_runtimes == {"faster-whisper": ("auto", "cpu"),
                                     "parakeet": ("auto", "cpu")}
        assert caps.gpu_only_engines == frozenset()
        assert caps.show_runtime_combo is False
        assert caps.gpu_pack_available is False


def test_darwin_capabilities_carry_the_ui_facts():
    """Spec §7 (#148): the running-apps picker exists, and a zero-only
    capture has words — the missing-mic-TCC failure mode is silent zeros, so
    the fact table carries what to say about them. win32 keeps free text and
    treats silence as silence."""
    from cadent.platform import fallback

    assert fallback.CAPABILITIES.app_picker is False
    assert fallback.CAPABILITIES.mic_permission_hint is None
    # The icon click is a spare gesture only where the menu lives on
    # right-click; on darwin the same click opens the menu (#160).
    assert fallback.CAPABILITIES.tray_click_toggles_pause is True
    # Colour carries the tray state except where the OS adapts template
    # images to the menu bar (#164).
    assert fallback.CAPABILITIES.tray_icon_is_template is False
    if sys.platform == "darwin":
        caps = platform.current().capabilities
        assert caps.app_picker is True
        assert "Microphone" in caps.mic_permission_hint
        assert caps.tray_click_toggles_pause is False
        assert caps.tray_icon_is_template is True


def test_the_permission_probes_answer_on_every_platform():
    """The #148 probes are part of the FocusedApp/DesktopEnv contract
    everywhere, not darwin extras: surfaces call them unconditionally and
    gate on the Capabilities fact, never on the OS."""
    plat = platform.current()
    assert plat.focused_app.permission_granted() in (True, False)
    assert isinstance(plat.focused_app.running_apps(), list)
    assert plat.focused_app.display_name("no.such.app.exists") is None


def test_darwin_table_parses_the_paste_chords():
    """Carbon codes for the paste chords #144 needs (cmd=55, v=9); the full
    combo-group table is #145's. VK ints never appear in this table."""
    assert parse_paste_chord("cmd+v", DARWIN_KEYCODES) == [55, 9]
    assert parse_paste_chord("cmd+shift+v", DARWIN_KEYCODES) == [55, 56, 9]
    # macOS terminals bind plain Cmd+V, but a hand-set chord still parses.
    assert parse_paste_chord("ctrl+shift+v", DARWIN_KEYCODES) == [59, 56, 9]


def test_darwin_table_has_no_ord_fallback():
    """Carbon codes are layout positions, not ASCII — ord() of an unknown
    character would press an unrelated key, so unknown parts must drop."""
    assert parse_paste_chord("bogus+nothing", DARWIN_KEYCODES) == []
    assert parse_paste_chord(";+v", DARWIN_KEYCODES) == [9]


# ---- the fallback adapters stay inert -----------------------------------------

def test_fallback_platform_is_harmless_everywhere():
    from cadent.platform import fallback

    plat = fallback.create()
    assert plat.single_instance.acquire() is True
    assert plat.hardware.cuda_total_memory() is None
    assert plat.focused_app.injection_blocked() is None
    assert plat.desktop.text_scale_factor() == 1.0
    assert plat.desktop.animations_enabled() is True
    plat.autostart.set_enabled(True)          # a no-op, never a crash
    assert plat.autostart.is_enabled() is False
    plat.hotkey_tap.start(lambda *a: None)    # hears nothing, raises nothing
    plat.hotkey_tap.stop()


# ---- the "sendinput" → "type" read alias (spec §2) ----------------------------

def test_old_configs_keep_loading_with_the_alias():
    cfg, issues = config.parse({
        "injection_method": "sendinput",
        "app_overrides": [{"process": "old.exe", "strategy": "sendinput"}],
    })
    assert cfg.injection_method == "type"
    assert cfg.app_overrides[0].strategy == "type"
    # The alias is honoured silently — it is not a config mistake to report.
    assert not [i for i in issues if i.field == "injection_method"]


def test_new_configs_default_to_the_platforms_strategy():
    """ADR 0001: Windows types by default, macOS pastes by default — a fresh
    config carries the platform's own column, not a hardcoded "type"."""
    expected = "clipboard" if sys.platform == "darwin" else "type"
    assert config.Config().injection_method == expected
    assert "type" in config.INJECTION_STRATEGIES
    assert "sendinput" not in config.INJECTION_STRATEGIES


def test_config_defaults_come_from_capabilities(monkeypatch):
    """The default strategy and the seed overrides are Capabilities facts —
    a darwin-shaped platform yields a pasting, seedless fresh config on any
    OS (spec §1.3, §5.2)."""
    import dataclasses

    from conftest import make_platform

    plat = make_platform()
    darwin_caps = dataclasses.replace(
        plat.capabilities, default_injection_strategy="clipboard",
        default_overrides=())
    monkeypatch.setattr(platform, "current",
                        lambda: dataclasses.replace(plat, capabilities=darwin_caps))
    cfg = config.Config()
    assert cfg.injection_method == "clipboard"
    assert cfg.app_overrides == []
