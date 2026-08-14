"""The token set and its contrast audit (spec §1.4, §3.1, §8.2).

The audit is the mechanism that caught the invisible dark card border in the
prototype (dark `border` at 1.17:1 against `surface`). It must not stay a
prototype script, so it lives here and runs on every commit.
"""

import pytest

from cadent.theme import tokens as tk


def test_colour_columns_have_identical_key_names():
    """The identical-key-names rule is what makes swapping a column safe."""
    assert set(tk.DARK) == set(tk.LIGHT)


def test_base_keys_never_collide_with_a_colour_column():
    assert set(tk.BASE).isdisjoint(tk.DARK)


def test_tokens_merges_base_and_the_named_column():
    dark = tk.tokens("dark")
    assert dark["bg"] == tk.DARK["bg"]
    assert dark["pill_h"] == tk.BASE["pill_h"]


def test_unknown_scheme_serves_dark_not_light():
    """Qt reports ColorScheme.Unknown under a Windows contrast theme. The
    naive `dark → dark, else → light` branch silently serves *light* tokens
    under a dark contrast theme; this form lands Unknown on dark (spec §1.3)."""
    assert tk.tokens("unknown") == tk.tokens("dark")
    assert tk.tokens("system") == tk.tokens("dark")
    assert tk.tokens("light") != tk.tokens("dark")


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_contrast_audit_passes(theme):
    failures = [f"{fg} on {bg}: {ratio:.2f} < {want}"
                for fg, bg, ratio, want in tk.audit(theme) if ratio < want]
    assert not failures, f"{theme}: " + "; ".join(failures)


def test_the_brand_mark_clears_both_taskbar_polarities():
    """The window / taskbar / Alt-Tab icon is the one mark placed on a
    background we cannot query, so it must clear 3:1 on a near-black AND a
    near-white taskbar. The tray's mark is exempt — it is painted in an ink
    taken from the surface it lands on (ADR 0006).

    Clearing 3:1 against both #1f1f1f and #f3f3f3 confines relative luminance
    to [0.143, 0.263], a band barely 1.8x wide. Asserted rather than described
    so a later "let's brighten the purple" is caught, not argued about."""
    for name, dark, light in tk.taskbar_audit():
        assert min(dark, light) >= 3.0, f"{name}: dark {dark:.2f} light {light:.2f}"
        lum = tk.luminance(str(tk.BASE[name]))
        assert 0.143 <= lum <= 0.263, f"{name}: L={lum:.3f}"


def test_accent_is_split_between_fill_and_text():
    """#9678ff on white is ~3:1 — fine as a filled gradient with white text,
    not fine as a violet label. The fill never moves between themes; the text
    colour does."""
    assert tk.DARK["accent_fill_a"] == tk.LIGHT["accent_fill_a"]
    assert tk.DARK["accent_fill_b"] == tk.LIGHT["accent_fill_b"]
    assert tk.DARK["accent_text"] != tk.LIGHT["accent_text"]


def test_the_pill_never_inverts():
    """Light mode gives the HUD a *different dark*, never a light pill — its
    background is the user's document (spec §4.7)."""
    assert tk.luminance(tk.DARK["hud_surface"]) < 0.05
    assert tk.luminance(tk.LIGHT["hud_surface"]) < 0.05
    assert tk.contrast(tk.LIGHT["hud_text"], tk.LIGHT["hud_surface"]) >= 4.5


def test_pill_radius_is_half_its_height():
    """A true pill at any width."""
    assert tk.BASE["pill_r"] * 2 == tk.BASE["pill_h"]


def test_luminance_and_contrast_agree_with_wcag_anchors():
    assert tk.luminance("#000000") == pytest.approx(0.0)
    assert tk.luminance("#ffffff") == pytest.approx(1.0)
    assert tk.contrast("#ffffff", "#000000") == pytest.approx(21.0)
