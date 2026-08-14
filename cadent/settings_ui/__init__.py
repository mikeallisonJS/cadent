"""The Settings surface: a grouped sidebar over six panes (spec §5).

A package rather than a module because instant apply gave each pane real
behaviour of its own — vocabulary rows, per-app injection knobs, a history
list — and the M3 single-form version had none.
"""

from .window import SettingsWindow

__all__ = ["SettingsWindow"]
