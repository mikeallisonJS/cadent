"""The one app-wide stylesheet, rendered from the token dict (§1.1, §1.4).

QSS has no variables; `string.Template` substitution over the token dict *is*
the variable mechanism. One string, one `app.setStyleSheet()` — every surface
including the overlay pill's chrome renders from here.

Two rules the template must keep honouring:

- **Never partially style `QComboBox` or `QScrollBar`.** QSS styling of these
  is all-or-nothing; a partial rule yields a broken control, so both carry
  their full sub-control set below.
- **No `letter-spacing`.** Qt QSS silently ignores it; tracked capitals come
  from `manager.tracked_caps()` instead.
"""

from __future__ import annotations

from string import Template

from .pixmaps import PixmapSet

# `${name}` everywhere rather than `$name`: `$r_field` immediately followed by
# `px` would be read as one identifier.
_APP = """
/* ---- windows and text ------------------------------------------------- */
QWidget#Root, QDialog#Root { background: ${bg}; }
QWidget#Pane { background: transparent; }

QLabel { color: ${text}; font-family: ${font_family}; font-size: ${fs_body}pt; }
QLabel#PageTitle  { font-size: ${fs_title}pt; font-weight: ${fw_medium};
                    padding-bottom: ${sp_2}px; }
QLabel#Display    { font-size: ${fs_display}pt; font-weight: ${fw_medium}; }
QLabel#RowTitle   { font-size: ${fs_row}pt; }
QLabel#RowDesc    { color: ${text_dim}; font-size: ${fs_desc}pt; }
QLabel#RowHint    { color: ${text_faint}; font-size: ${fs_desc}pt; }
QLabel#Caps       { color: ${text_faint}; font-size: ${fs_caps}pt;
                    font-weight: ${fw_bold}; }
QLabel#CapsAccent { color: ${accent_text}; font-size: ${fs_caps}pt;
                    font-weight: ${fw_bold}; }
QLabel#Danger     { color: ${danger}; font-size: ${fs_desc}pt; }
QLabel#Warning    { color: ${warning}; font-size: ${fs_desc}pt; }
QLabel#Success    { color: ${success}; font-size: ${fs_desc}pt; }
QLabel#Chip       { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                      stop:0 ${accent_fill_a}, stop:1 ${accent_fill_b});
                    color: ${on_accent}; border-radius: 10px;
                    padding: 3px 12px; font-size: ${fs_caps}pt;
                    font-weight: ${fw_bold}; }
QLabel#ChipMuted  { background: ${field}; border: 1px solid ${field_border};
                    color: ${text_dim}; border-radius: 10px;
                    padding: 3px 12px; font-size: ${fs_caps}pt; }

/* ---- sidebar (a QListWidget: one tab stop, arrow keys, §8.3) ----------- */
QListWidget#Sidebar {
    background: ${bg_sidebar}; border: none;
    border-right: 1px solid ${sidebar_border};
    outline: 0; padding: ${sp_3}px ${sp_2}px; }
/* Row height is this padding. A QListWidgetItem's default sizeHint is
   QSize(-1, -1) — *invalid* — so building a hint on top of it produces
   QSize(-1, h), which Qt discards entirely: rows collapsed to a bare line of
   text and the group spacing never appeared. Let the stylesheet size them. */
QListWidget#Sidebar::item {
    color: ${text_dim}; border-radius: ${r_nav}px;
    padding: 9px 14px; margin: 1px 0; font-size: ${fs_body}pt; }
QListWidget#Sidebar::item:hover { background: ${surface_hover}; color: ${text}; }
QListWidget#Sidebar::item:selected {
    background: ${selected}; color: ${text}; font-weight: ${fw_medium}; }
/* Group headings are NoItemFlags items styled in code, not here: a QSS
   attribute selector matches widgets, and list items are not widgets. */

/* Plain lists (snippet triggers) — without this they fall back to the
   platform selection colour, which is not in the token set at all. */
QListWidget { background: ${surface}; border: 1px solid ${border};
    border-radius: ${r_row}px; color: ${text}; outline: 0;
    font-family: ${font_family}; font-size: ${fs_body}pt; }
QListWidget::item { padding: 6px 10px; border-radius: ${r_nav}px; }
QListWidget::item:hover { background: ${surface_hover}; }
QListWidget::item:selected { background: ${selected}; color: ${text}; }

/* ---- cards and rows ---------------------------------------------------- */
QFrame#Card     { background: ${surface}; border: 1px solid ${border};
                  border-radius: ${r_card}px; }
QFrame#RowSep   { background: ${border}; max-height: 1px; border: none; }
QFrame#Row      { background: transparent; border: none; }
QFrame#RowRec   { background: ${accent_soft_bg};
                  border: 1px solid ${accent_soft_border};
                  border-radius: ${r_row}px; }
QFrame#RowPlain { background: ${surface}; border: 1px solid ${border};
                  border-radius: ${r_row}px; }
QFrame#Banner   { background: ${accent_soft_bg};
                  border: 1px solid ${accent_soft_border};
                  border-radius: ${r_row}px; }
QFrame#Undo     { background: ${field}; border: 1px solid ${field_border};
                  border-radius: ${r_row}px; }

/* ---- text inputs ------------------------------------------------------- */
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit {
    background: ${field}; border: 1px solid ${field_border};
    border-radius: ${r_field}px; padding: 6px 12px; color: ${text};
    selection-background-color: ${selected}; selection-color: ${text};
    font-family: ${font_family}; font-size: ${fs_body}pt; }
QLineEdit:hover, QSpinBox:hover { border-color: ${border_strong}; }
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: ${accent_fill_a}; }
QLineEdit:disabled, QSpinBox:disabled { color: ${text_faint}; }
QSpinBox::up-button, QSpinBox::down-button { width: 16px; border: none;
    background: transparent; }
QSpinBox::up-arrow, QSpinBox::down-arrow { width: 7px; height: 7px;
    background: ${text_dim}; border-radius: 3px; }

/* ---- combo box: all-or-nothing, so every sub-control is here ----------- */
QComboBox {
    background: ${field}; border: 1px solid ${field_border};
    border-radius: ${r_field}px; padding: 6px 12px; color: ${text};
    font-family: ${font_family}; font-size: ${fs_body}pt; min-width: 8em; }
QComboBox:hover { border-color: ${border_strong}; }
QComboBox:focus { border-color: ${accent_fill_a}; }
QComboBox:disabled { color: ${text_faint}; }
QComboBox::drop-down { border: none; width: ${combo_arrow_w}px;
    subcontrol-origin: padding; subcontrol-position: center right; }
QComboBox::down-arrow { image: url(${chevron}); }
QComboBox QAbstractItemView {
    background: ${field}; border: 1px solid ${field_border};
    border-radius: ${r_field}px; color: ${text};
    selection-background-color: ${selected}; selection-color: ${text};
    outline: 0; padding: 4px; }

/* ---- toggle-pill checkbox and ring radio (generated pixmaps, §1.5) ----- */
QCheckBox { color: ${text}; font-family: ${font_family};
            font-size: ${fs_body}pt; spacing: 10px; }
QCheckBox::indicator { width: ${toggle_w}px; height: ${toggle_h}px;
    image: url(${toggle_off}); }
QCheckBox::indicator:checked { image: url(${toggle_on}); }
QCheckBox:disabled { color: ${text_faint}; }

QRadioButton { color: ${text}; font-family: ${font_family};
               font-size: ${fs_row}pt; spacing: 10px; }
QRadioButton::indicator { width: ${radio_outer}px; height: ${radio_outer}px;
    image: url(${radio_off}); }
QRadioButton::indicator:checked { image: url(${radio_on}); }

/* ---- buttons ----------------------------------------------------------- */
QPushButton {
    background: transparent; border: 1px solid ${border_strong};
    border-radius: ${r_btn}px; padding: 8px 22px; color: ${text_dim};
    font-family: ${font_family}; font-size: ${fs_body}pt; }
QPushButton:hover { border-color: ${accent_fill_a}; color: ${text}; }
QPushButton:disabled { color: ${text_faint}; border-color: ${border}; }
QPushButton#Accent {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 ${accent_fill_a}, stop:1 ${accent_fill_b});
    border: none; color: ${on_accent}; font-weight: ${fw_medium};
    padding: 10px 26px; }
QPushButton#Accent:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 ${accent_fill_hover_a}, stop:1 ${accent_fill_hover_b}); }
QPushButton#Accent:disabled { background: ${field}; color: ${text_faint}; }
QPushButton#Danger { border-color: ${danger}; color: ${danger}; }
QPushButton#Link { border: none; padding: 2px 4px; color: ${accent_text};
    font-size: ${fs_desc}pt; text-align: left; }
QPushButton#Link:hover { color: ${accent_fill_a}; }
QPushButton#RowAction { border: none; padding: 4px 8px; color: ${text_dim};
    font-size: ${fs_body}pt; }
QPushButton#RowAction:hover { color: ${danger}; }

/* ---- progress bar: the wizard's model download (#115) ------------------ */
/* All-or-nothing like the combo, so the chunk is here beside the groove.
   Wears the accent gradient the primary button wears: this bar only ever
   appears where that button was, reporting on what pressing it started. */
QProgressBar { background: ${field}; border: 1px solid ${field_border};
    border-radius: ${r_field}px; height: 8px; text-align: center;
    color: ${text_dim}; font-family: ${font_family};
    font-size: ${fs_desc}pt; }
QProgressBar::chunk { border-radius: ${r_field}px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 ${accent_fill_a}, stop:1 ${accent_fill_b}); }

/* ---- scroll areas: all-or-nothing, so both orientations are here ------- */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: ${scrollbar}; border-radius: 5px;
    min-height: 40px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: ${scrollbar}; border-radius: 5px;
    min-width: 40px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none;
    background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- tables (vocabulary, snippets, app overrides, history) ------------- */
QTableView, QTableWidget {
    background: ${surface}; alternate-background-color: ${surface};
    border: 1px solid ${border}; border-radius: ${r_row}px;
    gridline-color: ${border}; color: ${text};
    selection-background-color: ${selected}; selection-color: ${text};
    font-family: ${font_family}; font-size: ${fs_body}pt; outline: 0; }
QTableView::item, QTableWidget::item { padding: 6px 8px;
    border-bottom: 1px solid ${border}; }
QHeaderView::section {
    background: ${bg_sidebar}; color: ${text_faint};
    border: none; border-bottom: 1px solid ${border};
    padding: 6px 8px; font-size: ${fs_caps}pt; font-weight: ${fw_bold}; }
QTableCornerButton::section { background: ${bg_sidebar}; border: none; }

/* ---- the tray menu is a real widget and follows the theme (§3.2) ------- */
QMenu { background: ${surface}; border: 1px solid ${border};
        border-radius: ${r_row}px; padding: 6px;
        font-family: ${font_family}; font-size: ${fs_body}pt; color: ${text}; }
QMenu::item { padding: 7px 28px 7px 24px; border-radius: ${r_nav}px;
              color: ${text}; }
QMenu::item:selected { background: ${selected}; }
QMenu::item:disabled { color: ${text_faint}; }
QMenu::separator { height: 1px; background: ${border}; margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; margin-left: 6px; }

QToolTip { background: ${surface}; color: ${text};
           border: 1px solid ${border}; padding: 5px 8px; }

/* ---- focus-visible: ring on Tab, nothing on click (§8.2) --------------- */
/* Set by a11y.FocusVisibleFilter, which repolishes so the rule re-matches. */
QPushButton[kbdFocus="true"], QComboBox[kbdFocus="true"] {
    outline: ${focus_w}px solid ${focus_ring};
    outline-offset: ${focus_offset}px; outline-radius: ${r_btn}px; }
QLineEdit[kbdFocus="true"], QSpinBox[kbdFocus="true"],
QPlainTextEdit[kbdFocus="true"], QTextEdit[kbdFocus="true"] {
    outline: ${focus_w}px solid ${focus_ring};
    outline-offset: ${focus_offset}px; outline-radius: ${r_field}px; }
QCheckBox[kbdFocus="true"], QRadioButton[kbdFocus="true"],
QListWidget[kbdFocus="true"], QTableView[kbdFocus="true"] {
    outline: ${focus_w}px solid ${focus_ring};
    outline-offset: ${focus_offset}px; outline-radius: ${r_nav}px; }
/* Scoped override: the plain ring is invisible on the violet gradient CTA. */
QPushButton#Accent[kbdFocus="true"] {
    outline: ${focus_w}px solid ${focus_ring_on_accent}; }

"""

# The pill's chrome, kept separate for one reason: it is applied **twice**.
# Once as part of the app-wide sheet, so the HUD token is literally one rule
# and the pill can never drift from the token set; and once as a widget-level
# sheet on the pill itself, because §4.6 has the pill opt out of High Contrast
# — and under a contrast theme there is no app-wide sheet left to render it.
# A widget-level sheet survives that, and applying both is idempotent.
PILL = """
QFrame#overlayPill { background: ${hud_surface};
    border: 1px solid ${hud_border}; border-radius: ${pill_r}px; }
QLabel#overlayLabel { color: ${hud_text}; font-family: ${font_family};
    font-size: ${fs_body}pt; font-weight: ${fw_medium}; }
QLabel#overlayLabelMuted { color: ${hud_text_dim};
    font-family: ${font_family}; font-size: ${fs_body}pt; }
QLabel#overlayLabelDanger { color: ${hud_danger};
    font-family: ${font_family}; font-size: ${fs_body}pt;
    font-weight: ${fw_medium}; }
"""

TEMPLATE = Template(_APP + PILL)
PILL_TEMPLATE = Template(PILL)


def render(t: dict, pixmaps: PixmapSet) -> str:
    """The whole stylesheet for one theme."""
    return TEMPLATE.substitute(
        chevron=pixmaps.chevron,
        toggle_off=pixmaps.toggle_off,
        toggle_on=pixmaps.toggle_on,
        radio_off=pixmaps.radio_off,
        radio_on=pixmaps.radio_on,
        **t,
    )


def render_pill(t: dict) -> str:
    """Just the pill's chrome, for the widget-level sheet it keeps under a
    Windows contrast theme."""
    return PILL_TEMPLATE.substitute(**t)
