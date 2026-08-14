"""The design language: one token set, one stylesheet, one theme manager.

`tokens` is the single source of truth for both the QSS (`qss`) and the
custom painters (the overlay pill's meter, the generated control indicators
in `pixmaps`). Nothing outside this package may hard-code a colour.

Deliberately empty of re-exports: `tokens.tokens()` is a function inside a
module of the same name, so a convenience import here would shadow the module
for `from cadent.theme import tokens`.
"""
