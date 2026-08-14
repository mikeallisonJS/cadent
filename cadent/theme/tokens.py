"""The design tokens (spec §1.4), and the contrast audit that keeps them honest.

Three parts: theme-independent BASE, plus DARK and LIGHT colour columns with
**identical key names** — that constraint is what makes swapping a column at
runtime safe, and `test_theme_tokens.py` asserts it.

Deliberately Qt-free: the audit is a pure function over hex strings so it can
run as a unit test rather than as a prototype script someone remembers to run.
"""

from __future__ import annotations

BASE: dict[str, object] = {
    # --- type -------------------------------------------------------------
    # A stack, not a name: Qt 6 QSS feeds the whole list to QFont.setFamilies,
    # so Windows keeps Segoe UI and macOS lands on a deliberate fallback
    # instead of whatever the missing-family alias happens to resolve to
    # (whose wider metrics wrap one-line hints, see test_settings_ui).
    "font_family": '"Segoe UI", "Helvetica Neue"',
    "fs_display": 22,     # wizard title
    "fs_title": 18,       # settings page title
    "fs_row": 10.5,       # setting row title
    "fs_body": 10,        # default / controls
    "fs_desc": 9,         # row description
    "fs_caps": 8.5,       # tracked capitals (sidebar groups, card headings)
    "fw_regular": 400,
    "fw_medium": 600,
    "fw_bold": 700,
    # Qt QSS has no letter-spacing property, so tracked capitals are set with
    # QFont.setLetterSpacing in code — see manager.tracked_caps(). Value is in
    # px at fs_caps.
    "tracking_caps": 2.0,
    # --- spacing (4-based) ------------------------------------------------
    "sp_1": 4,
    "sp_2": 8,
    "sp_3": 12,
    "sp_4": 18,
    "sp_5": 28,
    "sp_6": 36,
    # --- radii -------------------------------------------------------------
    "r_field": 9,
    "r_nav": 9,
    "r_row": 12,
    "r_card": 14,
    "r_btn": 17,
    # --- control metrics ---------------------------------------------------
    "toggle_w": 38,
    "toggle_h": 20,
    "toggle_knob": 14,
    "radio_outer": 16,
    "radio_ring": 5,
    "combo_arrow_w": 26,
    "chevron_w": 12,
    "chevron_h": 8,
    "row_pad_h": 18,
    "row_pad_v": 12,
    # A *minimum*, never a fixed width: Windows' Text size setting scales the
    # type and a fixed 220 clips outright at 150% (spec §8.5).
    "sidebar_min_w": 220,
    # --- focus ring (spec §8.2) -------------------------------------------
    "focus_w": 2,
    "focus_offset": 2,
    # --- pill geometry (spec §4.7) ----------------------------------------
    "pill_h": 40,
    "pill_r": 20,              # = pill_h / 2: a true pill at any width
    "pill_min_w": 132,
    "pill_max_w": 280,         # load-bearing: caps failure copy at ~30 chars
    "pill_margin_bottom": 80,
    "pill_shadow_margin": 28,  # the translucent top-level exceeds the frame by this
    "pill_pad_h": 14,
    "pill_glyph": 18,
    "pill_gap": 10,
    # --- meter -------------------------------------------------------------
    "meter_bars": 14,
    "meter_bar_w": 3,
    "meter_gap": 3,
    "meter_bar_r": 1.5,
    "meter_floor_h": 3,
    "meter_span": 22,          # full height, mirrored: ±11 about the centre
    "meter_decay_ms": 200,
    "meter_silence_ms": 2000,
    # --- motion (spec §4.7) ------------------------------------------------
    "fps": 30,
    "fade_in_ms": 100,         # reactive states only; recording is zero-fade
    "fade_out_ms": 180,
    "width_tween_ms": 150,
    "min_visible_ms": 400,
    "transient_ms": 1000,      # Cancelled
    "paused_ms": 1500,
    # --- tray state colours (spec §3.1) ------------------------------------
    # Theme-agnostic and taskbar-agnostic: the only colours in the set placed
    # on a background we cannot query, so each clears 3:1 against a near-black
    # AND a near-white taskbar. See test_theme_tokens.py for the proof.
    "tray_ready": "#7a5cff",
    "tray_paused": "#8b849e",
    "tray_attention": "#cc5f14",
}

DARK: dict[str, str] = {
    "bg": "#14111a",
    "bg_sidebar": "#191521",
    "sidebar_border": "#241e30",
    "surface": "#1c1726",
    "surface_hover": "#221c2e",
    "field": "#221c2e",
    "field_border": "#322a44",
    "selected": "#2a2140",
    "border": "#302943",
    "border_strong": "#3a3350",
    "text": "#edeaf6",
    "text_dim": "#a79fc0",
    "text_faint": "#8b83a6",
    # `accent` is split permanently: #9678ff on white is ~3:1, fine as a filled
    # gradient with white text, not fine as a violet label. Fills never move
    # between themes (the brand mark doesn't); violet *text* does.
    "accent_fill_a": "#9678ff",
    "accent_fill_b": "#7a5cff",
    "accent_fill_hover_a": "#a488ff",
    "accent_fill_hover_b": "#8a6eff",
    "accent_text": "#9678ff",
    "accent_soft_bg": "#221a38",
    "accent_soft_border": "#9678ff",
    "on_accent": "#ffffff",
    "danger": "#ff6b6b",
    "warning": "#f0a94c",
    "success": "#4fd18b",
    # The warning chip's own bed, in a model picker row (#112). `accent_soft_bg`
    # is the matching one for the "Recommended" chip; warnings needed a second
    # because a violet-bedded warning reads as another recommendation.
    "warning_soft_bg": "#33240f",
    "scrollbar": "#322a44",
    "focus_ring": "#9678ff",
    # Without this the ring is invisible on the violet gradient CTA — the
    # button the wizard's whole flow depends on (spec §8.2).
    "focus_ring_on_accent": "#fcfbff",
    # --- HUD: themed, but never inverts (spec §4.7) ------------------------
    "hud_surface": "#15121c",
    "hud_alpha": "248",
    "hud_border": "rgba(255,255,255,0.14)",
    "hud_text": "#f3f0fa",
    "hud_text_dim": "#b6adce",
    "hud_shadow": "rgba(0,0,0,0.59)",
    "hud_shadow_blur": "26",
    "hud_shadow_dy": "8",
    "hud_danger": "#ff7a7a",
    "hud_muted": "#8d84a6",
    "hud_meter_a": "#9678ff",
    "hud_meter_b": "#7a5cff",
    "hud_meter_silent": "#56506b",
}

LIGHT: dict[str, str] = {
    # Light neutrals keep the violet cast rather than going neutral grey — a
    # neutral-grey light column reads as a different app's theme. Cards stay
    # pure white so they lift off the tinted window.
    "bg": "#f1eff7",
    "bg_sidebar": "#f7f6fb",
    "sidebar_border": "#e4e0ee",
    "surface": "#ffffff",
    "surface_hover": "#f4f2f9",
    # `field` is not "one step raised": the card is white, so the input sits
    # *below* it. The name means "the input's own surface".
    "field": "#f5f3fa",
    "field_border": "#d8d3e6",
    "selected": "#ebe4fe",
    "border": "#e6e2f0",
    "border_strong": "#c9c2dc",
    "text": "#1b1826",
    "text_dim": "#57506e",
    "text_faint": "#6d6685",
    "accent_fill_a": "#9678ff",
    "accent_fill_b": "#7a5cff",
    "accent_fill_hover_a": "#8a6bf5",
    "accent_fill_hover_b": "#6d4df5",
    "accent_text": "#6f47e0",
    "accent_soft_bg": "#f4efff",
    "accent_soft_border": "#9678ff",
    "on_accent": "#ffffff",
    # State colours are not one hue with two tints: dark's #ff6b6b is 2.9:1 on
    # white, so light needs genuinely darker, more saturated values.
    "danger": "#c62f2f",
    "warning": "#9a5c07",
    "success": "#12754a",
    "warning_soft_bg": "#fdf0d5",
    "scrollbar": "#d8d3e6",
    "focus_ring": "#6f47e0",
    "focus_ring_on_accent": "#fcfbff",
    # A *different dark*, tuned to the light palette's neutrals — the pill's
    # background is foreign content (the user's document), so inverting it
    # would make it vanish over a white page in exactly the case light mode
    # exists to serve.
    "hud_surface": "#262130",
    "hud_alpha": "250",
    "hud_border": "rgba(255,255,255,0.14)",
    "hud_text": "#f6f4fb",
    "hud_text_dim": "#c2bad6",
    "hud_shadow": "rgba(31,26,45,0.35)",
    "hud_shadow_blur": "30",
    "hud_shadow_dy": "10",
    "hud_danger": "#ff8a8a",
    "hud_muted": "#9a92b2",
    "hud_meter_a": "#a88fff",
    "hud_meter_b": "#8b6dff",
    "hud_meter_silent": "#6b6484",
}


def tokens(theme: str) -> dict:
    """The full token dict for a theme.

    Written `light → LIGHT, else → DARK` on purpose: Qt reports
    `ColorScheme.Unknown` under a Windows contrast theme, and the naive
    `dark → DARK, else → LIGHT` form silently serves *light* tokens under a
    dark contrast theme. This form lands the overloaded Unknown on dark — the
    design's home column (spec §1.3).
    """
    return {**BASE, **(LIGHT if theme == "light" else DARK)}


# ---------------------------------------------------------------------------
# Contrast audit — the render pass is only a claim until these numbers exist.
# It already caught a real bug: dark `border` at #2a2438 sat at 1.17:1 against
# `surface`, an invisible card edge, and was bumped to #302943.
# ---------------------------------------------------------------------------

TASKBAR_DARK = "#1f1f1f"
TASKBAR_LIGHT = "#f3f3f3"


def _lin(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    """WCAG relative luminance of a `#rrggbb` string."""
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two `#rrggbb` strings."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# (foreground token, background token, required ratio)
AUDIT_PAIRS: tuple[tuple[str, str, float], ...] = (
    ("text", "surface", 4.5),
    ("text", "bg", 4.5),
    ("text_dim", "surface", 4.5),
    ("text_faint", "surface", 3.0),
    ("accent_text", "surface", 4.5),
    ("accent_text", "bg", 4.5),
    ("on_accent", "accent_fill_a", 3.0),
    ("on_accent", "accent_fill_b", 3.0),
    ("danger", "surface", 4.5),
    ("warning", "surface", 4.5),
    # The two model-picker chips. Both carry meaning colour alone can't, so
    # both are read as text and both are audited as text (#111, #112).
    ("accent_text", "accent_soft_bg", 4.5),
    ("warning", "warning_soft_bg", 4.5),
    ("success", "surface", 4.5),
    ("border", "surface", 1.2),
    ("hud_text", "hud_surface", 4.5),
    ("hud_text_dim", "hud_surface", 4.5),
    ("hud_danger", "hud_surface", 4.5),
    ("hud_muted", "hud_surface", 3.0),
    ("hud_meter_a", "hud_surface", 3.0),
)

# Every surface the focus ring can land on (spec §8.2). Non-text contrast, so
# 3:1 — except the accent fills, which the near-white ring covers instead.
FOCUS_SURFACES: tuple[str, ...] = (
    "bg", "bg_sidebar", "surface", "surface_hover", "field", "selected",
    "accent_soft_bg",
)
FOCUS_ON_ACCENT_SURFACES: tuple[str, ...] = ("accent_fill_a", "accent_fill_b")

TRAY_STATES: tuple[str, ...] = ("tray_ready", "tray_paused", "tray_attention")


def audit(theme: str) -> list[tuple[str, str, float, float]]:
    """(fg, bg, ratio, required) for every audited pair in one theme."""
    t = tokens(theme)
    rows = [(fg, bg, contrast(t[fg], t[bg]), want) for fg, bg, want in AUDIT_PAIRS]
    rows += [("focus_ring", bg, contrast(t["focus_ring"], t[bg]), 3.0)
             for bg in FOCUS_SURFACES]
    rows += [("focus_ring_on_accent", bg,
              contrast(t["focus_ring_on_accent"], t[bg]), 3.0)
             for bg in FOCUS_ON_ACCENT_SURFACES]
    return rows


def tray_audit() -> list[tuple[str, float, float]]:
    """(state, ratio on a dark taskbar, ratio on a light taskbar)."""
    return [(state, contrast(BASE[state], TASKBAR_DARK),
             contrast(BASE[state], TASKBAR_LIGHT))
            for state in TRAY_STATES]


def print_audit() -> None:
    """Human-readable audit, for `python -m cadent.theme.tokens`."""
    for theme in ("dark", "light"):
        print(f"\n  {theme.upper()}")
        for fg, bg, ratio, want in audit(theme):
            flag = "ok  " if ratio >= want else "FAIL"
            print(f"    {flag} {fg:>20} on {bg:<16} {ratio:5.2f}  (want {want})")
    print("\n  TRAY (theme-agnostic, both taskbar polarities)")
    for state, dark, light in tray_audit():
        flag = "ok  " if min(dark, light) >= 3.0 else "FAIL"
        print(f"    {flag} {state:<16} {BASE[state]}  dark {dark:4.2f}  "
              f"light {light:4.2f}   L={luminance(str(BASE[state])):.3f}")


if __name__ == "__main__":       # pragma: no cover - developer convenience
    print_audit()
