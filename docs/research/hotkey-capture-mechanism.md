# Research: Hotkey capture mechanism for Ctrl+Win hold-to-talk

Ticket: #10 (M1 ticket 01 — Hotkey capture mechanism)
Date: 2026-07-23

## TL;DR

`RegisterHotKey` cannot express a modifiers-only chord — a virtual-key code is mandatory. A **low-level keyboard hook (`SetWindowsHookEx(WH_KEYBOARD_LL)`)** is the correct mechanism, driven via **pynput's `keyboard.Listener` with `win32_event_filter`** (or a thin ctypes hook if pynput's suppression quirks bite). Do **not** suppress the Ctrl/Win key events themselves; the Ctrl half of the chord already masks the Start menu in most orderings, and a defensive dummy-key injection (`VK 0xFF`) on Win-keyup covers the rest — the same technique AutoHotkey uses (`#MenuMaskKey`). Elevated windows are a documented v1 limitation (UIPI); mitigation options are "run Cadent elevated" or a future UIAccess manifest. Conflict detection for a modifier-only chord is effectively N/A at registration time; for VK-based custom chords a `RegisterHotKey` probe (register → check → unregister) is a cheap, reliable detector.

---

## 1. Why RegisterHotKey is out for the default chord

`RegisterHotKey(hWnd, id, fsModifiers, vk)` — the `vk` parameter is "The virtual-key code of the hot key" and `fsModifiers` are "the keys that must be pressed **in combination with the key specified by the vk parameter**". There is no way to register a hotkey consisting of modifiers alone (`Ctrl+Win` with no VK). Additionally, `MOD_WIN` combos are formally "reserved for use by the operating system."
Source: [RegisterHotKey (winuser.h), Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)

RegisterHotKey also fires only on the *press* edge (WM_HOTKEY); there is no release notification, so even for VK-based chords it cannot implement hold-to-talk by itself — you'd still need `GetAsyncKeyState` polling or a hook to detect release.

## 2. The mechanism: WH_KEYBOARD_LL

`SetWindowsHookEx(WH_KEYBOARD_LL, ...)` delivers every `WM_KEYDOWN`/`WM_KEYUP`/`WM_SYSKEYDOWN`/`WM_SYSKEYUP` to a callback in the installing process, with a `KBDLLHOOKSTRUCT` carrying `vkCode`, `scanCode`, and flags (including `LLKHF_INJECTED` for synthetic input — useful to ignore our own `SendInput` traffic).
Key operational facts from [LowLevelKeyboardProc, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc):

- The thread that installs the hook **must run a message loop** (pynput's listener thread does this internally).
- Returning nonzero from the callback **swallows the event** (it never reaches the target app or later hooks).
- **Timeout hazard:** the callback must return within `LowLevelHooksTimeout` (HKCU\Control Panel\Desktop; capped at 1000 ms on Win10 1709+). On Windows 7+ a hook that times out is **silently removed with no notification**. Consequence for Cadent: the hook callback must do *nothing* but set flags / post to a queue — never touch audio, models, or Qt from inside it. Microsoft explicitly recommends "a dedicated thread that passes the work off to a worker thread and then immediately returns."
- Injected input is visible to the hook and flagged (`flags & LLKHF_INJECTED`).

Microsoft suggests Raw Input as an alternative for pure *monitoring*, but Raw Input cannot suppress keys and needs a window to receive `WM_INPUT`; for a hotkey with optional suppression the LL hook is the standard choice.

### pynput specifics

pynput's Windows keyboard listener is a WH_KEYBOARD_LL hook. Relevant facts:

- `win32_event_filter(msg, data)` gives the raw `KBDLLHOOKSTRUCT`; calling `listener.suppress_event()` inside the filter swallows that one event. Returning `False` from the filter only hides the event from pynput's own callbacks, it does **not** suppress it system-wide. Source: [pynput FAQ](https://pynput.readthedocs.io/en/latest/faq.html)
- Known sharp edges: returning `False` from `win32_event_filter` prevents `on_press`/`on_release` from firing for that event ([pynput #679](https://github.com/moses-palmer/pynput/issues/679)); calling `suppress_event()` from the wrong place (e.g. `on_press` instead of the filter) kills the listener thread silently ([pynput #605](https://github.com/moses-palmer/pynput/issues/605)).
- Practical implication: keep suppression usage minimal (ideally none — see §4), and if suppression is ever needed, do it *only* inside `win32_event_filter`. If pynput's abstraction becomes a liability, a ~60-line ctypes `WH_KEYBOARD_LL` hook on a dedicated thread is a well-trodden fallback (see [MS blog: low-level keyboard hook in C#](https://learn.microsoft.com/en-us/archive/blogs/toub/low-level-keyboard-hook-in-c) for the canonical shape).

## 3. Detecting press-and-hold and release of Ctrl+Win

State-machine over raw events; no suppression required:

- Track four VKs: `VK_LCONTROL`/`VK_RCONTROL` (0xA2/0xA3) and `VK_LWIN`/`VK_RWIN` (0x5B/0x5C). Treat left/right variants as equivalent (configurable later).
- **Chord down** = both groups down simultaneously, in either order. Fire "start recording" on the transition into the both-down state.
- **Auto-repeat:** Windows auto-repeats keydown while held — the hook sees repeated `WM_KEYDOWN` for the same VK. Guard with a per-key "already down" flag so repeats are idempotent (modifier keys do repeat at the LL-hook level).
- **Chord up** = either key of the chord goes up while in the recording state → "stop recording". Don't wait for both keyups; the first release ends the hold (matches user intuition and avoids ordering ambiguity).
- **Min-hold / debounce:** a chord held < ~150–250 ms should be treated as an accidental tap — discard the (near-empty) recording rather than inserting junk. This also absorbs the common "Ctrl+Win brushed on the way to Ctrl+Win+Arrow" case. PRD requires capture start <100 ms after keydown, so start recording immediately on chord-down and *discard* on early release, rather than delaying the start.
- **Other keys during hold:** `Ctrl+Win+←/→` are OS virtual-desktop shortcuts. If a non-chord key is pressed while the chord is held, either (a) cancel the dictation, or (b) ignore it. Recommend (a) cancel — the OS will act on the shortcut anyway and the user clearly wasn't dictating.
- Ignore events with `LLKHF_INJECTED` set so Cadent's own `SendInput` text insertion can never re-trigger or confuse the chord state machine.

## 4. Start-menu suppression on Win keyup

The shell opens the Start menu when it sees a Win keydown followed by Win keyup **with no intervening keypress**. AutoHotkey documents this and its fix: "If the system detects only a Win or Alt keydown and keyup with no intervening keypress, it would usually activate a menu. To prevent this, the keyboard or mouse hook may automatically send the mask key" — by default Ctrl. Sources: [A_MenuMaskKey, AutoHotkey v2 docs](https://www.autohotkey.com/docs/v2/lib/A_MenuMaskKey.htm), [#MenuMaskKey, AutoHotkey v1 docs](https://www.autohotkey.com/docs/v1/lib/_MenuMaskKey.htm).

For Cadent's specific chord this mostly solves itself, **because Ctrl is part of the chord**:

- Win pressed first, then Ctrl → Ctrl keydown is an intervening keypress → Start menu naturally suppressed.
- Ctrl pressed first, then Win, releases in any order → the Ctrl keydown/keyup activity around the Win press means the shell does not treat it as a bare Win tap in practice (this is exactly why Ctrl is AHK's default mask key).

**Do not swallow the Ctrl/Win events** in the hook: passing them through keeps the OS modifier state consistent (important because Cadent will immediately follow with `SendInput` text insertion; a stuck-down Win in the OS's view would turn typed letters into Win+letter shortcuts).

**Defensive measure (recommended):** on the chord-release edge, before/immediately after the Win keyup passes through, inject a dummy keystroke with an unmapped VK — AHK uses `vk 0xFF` ("It has no visible effect in most windows") — via `SendInput` with keydown+keyup. This guarantees the Start menu never appears regardless of release ordering or timing races. The injected event carries `LLKHF_INJECTED`, so our own state machine ignores it. Cost: two synthetic events; no visible side effects. ([A_MenuMaskKey docs](https://www.autohotkey.com/docs/v2/lib/A_MenuMaskKey.htm) discuss vkFF as the low-side-effect mask choice.)

The alternative — swallowing the Win keyup in the hook and re-injecting it — is strictly worse: it risks stuck-modifier states and interacts badly with the pynput suppression bugs in §2.

## 5. Elevated (admin) windows — UIPI

User Interface Privilege Isolation prevents lower-integrity processes from installing hooks into / reading input destined for higher-integrity processes. When an elevated app has focus, a non-elevated Cadent's LL hook **does not receive those keystrokes**, so the hotkey simply doesn't fire. Likewise `SendInput` "does not permit injecting input into applications at a higher integrity level" ([SendInput, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — UIPI remark), so text insertion would fail there too, independent of the hotkey.

Options:

1. **Accept as v1 limitation (recommended).** Document: "dictation is unavailable while an elevated window is focused." This is what most non-elevated utilities (e.g., stock AutoHotkey) do. PRD §6 already hedges: "where OS permits."
2. Run Cadent elevated ("Run as administrator" / scheduled-task autostart with highest privileges). Works, but complicates auto-start UX and means the app runs with admin rights all the time — poor fit for a privacy-first tool.
3. `uiAccess="true"` manifest: lets a process read input across integrity levels via low-level hooks, but requires the binary to be **Authenticode-signed and installed under a secure path** (Program Files / System32) ([UAC: Only elevate UIAccess applications that are installed in secure locations, Microsoft Learn](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/jj852244(v=ws.11))). A code-signing cert + installer requirement — plausible at M3, not before.

## 6. Hold-to-talk vs toggle on this mechanism

Both modes ride the same hook/state machine; only the edge semantics differ:

- **Hold:** chord-down edge → start; first-keyup edge → stop. Min-hold threshold (§3) discards accidental taps.
- **Toggle:** chord-down edge → start; *next* chord-down edge → stop. Keyups are ignored except to re-arm edge detection (chord must fully release before it can re-trigger — this is the debounce). A max-utterance guard (PRD: 120 s) is the safety net for a forgotten toggle.
- A combined "tap-to-toggle, hold-to-talk" mode (tap = release within ~300 ms → toggle on; longer hold = PTT) is possible on the same machine but adds ambiguity; defer past v1.

## 7. Hotkey conflict detection (PRD risk #5)

- **For the default modifier-only chord:** there is nothing to detect at registration. No other app can register `Ctrl+Win` via `RegisterHotKey` (impossible), and multiple LL hooks coexist by design (they chain). Real conflicts are *runtime* ones: OS shortcuts sharing the prefix (`Ctrl+Win+Arrow` = virtual desktop switch, `Ctrl+Win+Q` = Quick Assist). Handle via the cancel-on-extra-key rule (§3) and a docs note.
- **For user-configured VK-based chords** (e.g., `Ctrl+Alt+Space`): probe with `RegisterHotKey` — it "fails if the keystrokes specified for the hot key have already been registered for another hot key" (GetLastError = `ERROR_HOTKEY_ALREADY_REGISTERED`, 1409). Register → on success immediately `UnregisterHotKey` and drive it with the hook as usual; on failure, warn the user at settings time. Caveat from the docs: some OS defaults (e.g., PrintScreen) can be silently overridden, and F12 is always debugger-reserved, so the probe is a strong hint, not a guarantee. Source: [RegisterHotKey, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
- A hook-based approach cannot detect that *another hook-based app* (AHK, Discord PTT) also listens for the same chord — no API enumerates LL hooks. Best effort only.

## 8. Recommendation

1. **Mechanism:** WH_KEYBOARD_LL via **pynput `keyboard.Listener`** with `win32_event_filter` for raw VK/flag access; state machine as in §3, all real work handed off to a queue/worker (LL-hook timeout constraint). Keep a ctypes fallback in mind if pynput's suppression bugs surface (we barely need suppression, so risk is low).
2. **Start menu:** pass all chord events through; inject dummy `VK 0xFF` down/up on chord release as a mask (AHK-proven). Ignore `LLKHF_INJECTED` events in the state machine.
3. **Elevated windows:** v1 limitation, documented; revisit uiAccess-signed binary at M3/installer time.
4. **Modes:** hold = down→up edges with ~200 ms min-hold discard; toggle = down-edge flip-flop with full-release re-arm and 120 s guard.
5. **Conflicts:** no registration-time detection needed for the default chord; `RegisterHotKey` probe for custom VK chords; cancel dictation if a non-chord key arrives mid-hold.

## Sources

- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey
- https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- https://pynput.readthedocs.io/en/latest/faq.html
- https://github.com/moses-palmer/pynput/issues/679
- https://github.com/moses-palmer/pynput/issues/605
- https://www.autohotkey.com/docs/v2/lib/A_MenuMaskKey.htm
- https://www.autohotkey.com/docs/v1/lib/_MenuMaskKey.htm
- https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/jj852244(v=ws.11)
- https://learn.microsoft.com/en-us/archive/blogs/toub/low-level-keyboard-hook-in-c
