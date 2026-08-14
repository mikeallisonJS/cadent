# macOS pastes by default, Windows types by default

The injection strategy default belongs to the platform, not to Cadent. On Windows
we type (`SendInput` Unicode) and fall back to the clipboard, because Ctrl+V is not
paste in terminals and a dozen shipped app overrides exist to work around that. On
macOS we paste (NSPasteboard + Cmd-V) and do not automatically fall back, because
Cmd-V *is* paste essentially everywhere — Terminal, iTerm2, Alacritty and WezTerm
included — and because `CGEventKeyboardSetUnicodeString` truncates at 20 UTF-16 units
per event, so typing there is a paced sequence of ~15 events for a normal transcript
rather than one atomic call. Decided in #130, on the macOS porting map (#124).

## Considered options

Parity — type-first on both platforms — was the starting recommendation and was
rejected. The two facts above are the reason, plus the precedent: the established
macOS dictation apps ship paste-first, and where they offer a keystroke mode at all
it is explicitly experimental and US-QWERTY-only. Per-platform enum values (`sendinput` on
win32, `cgevent` on darwin) were rejected outright: they would fork the Settings
dropdown and the override table for no user-visible benefit.

## Consequences

Two things follow that a reader would otherwise find surprising.

**Auto-learn (#45) is Windows-only.** It fires on a *detectable* typing failure, and
paste has none — writing to `NSPasteboard` cannot meaningfully fail, and posting Cmd-V
tells us nothing about whether the app consumed it. `typing_failed` is therefore always
false on macOS and no learned override is ever written there. This is deliberate: the
alternative is a heuristic that guesses at failure, which would teach the app wrong
things silently.

**macOS ships an empty default override table.** Every Windows seed came out of a
measured sweep and carries a hand-written reason in `DEFAULT_OVERRIDE_REASONS`; we will
not seed a rule we have not tested. Remote-desktop and VM clients (Microsoft Remote
Desktop, Citrix, Parallels, VMware Fusion, UTM) are the known suspects — they forward
keystrokes to a guest with its own clipboard, the same failure that earned `mstsc.exe`
its rule — and are left for a later verification pass to seed with evidence.

"Type it" remains offered on macOS as an explicit per-app override; it is simply never
reached automatically.
