# macOS asks for one permission: Accessibility

The hotkey listener is a listen-only event tap, which formally sits in macOS's
*Input Monitoring* TCC class — a lighter grant than the *Accessibility* grant
that text injection (posting Cmd-V, ADR 0001) requires. We do not ask for it.
Cadent's macOS onboarding requests exactly one permission, Accessibility,
checks it with `AXIsProcessTrusted`, and surfaces it in the single wizard step
and Settings banner that #130 already ordered. Decided in #131, on the macOS
porting map (#124).

## Considered options

A two-grant model — `IOHIDCheckAccess` gating the listener, `AXIsProcessTrusted`
gating injection, each with its own UX — was rejected. There is no useful
configuration of the app that holds only one of the grants: a hotkey that cannot
inject is pointless, and Accessibility supersedes Input Monitoring for event
taps. Two prompts would double the onboarding friction to unlock zero extra
states.

## Consequences

`AXIsProcessTrusted` becomes the app's one honest health check. This matters
because macOS answers a missing grant with *silence*, not an error: the
dev-environment survey (#129) measured pynput's listener reporting
`running == True` while receiving nothing, warning only on stderr. Any "is the
hotkey alive?" probe must ask TCC, never the listener.

One verification item rides on this: macOS documentation implies but does not
promise that a listen-only tap delivers events when Accessibility is granted and
Input Monitoring is not. The first real-Mac hotkey test must confirm it; if it
fails, the fallback is adding Input Monitoring as a second wizard line, not
abandoning the single-check design elsewhere.

During development the grant belongs to the *responsible process* — Terminal or
the IDE, not the venv python — so a granted dev machine proves nothing about a
packaged app, and re-signing resets the grant.
