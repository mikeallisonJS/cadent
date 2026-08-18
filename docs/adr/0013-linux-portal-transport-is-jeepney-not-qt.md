# Linux talks to the portals through jeepney on one thread of its own, and the platform layer stays Qt-free

Three Linux seams share one bus: GlobalShortcuts (ADR 0008), the RemoteDesktop
and Clipboard portals (ADR 0007), and the Settings portal that the tray-ink
watcher subscribes to. The obvious transport was QtDBus — PySide6 is already a
hard dependency, so it costs nothing to bundle. It was rejected, and the
rejection was made a rule: **`cadent/platform/` never imports the GUI
toolkit.** ADR 0005 only forbade OS-specific imports outside the adapters; the
Qt-free adapter was an observed pattern of two ports, and one docstring
(`win32.py`'s ink watcher) already cited it as if it were the law. It now is.
Decided in #27 on the Linux porting map (#11).

- **The transport is `jeepney`**, pure Python, gated
  `sys_platform == 'linux'` in `pyproject.toml`. It speaks the socket directly,
  so it adds no native library to the bundle and no `LOAD_BEARING["linux"]`
  row (ADR 0011). Its `jeepney.io.threading.DBusRouter` is the shape the seams already
  promise: one connection, **one receive thread (`cadent-portal`)**, blocking
  `send_and_get_reply()` from any worker, signals delivered as filter queues.
- **One connection, owned by the Linux platform module, injected into every
  seam adapter.** Portal `Request` and `Session` object paths derive from the
  connection's unique name, `permission_granted()` (ADR 0012) reads state from
  two portals at once, and the `Closed`-recovery of ADR 0008 re-creates
  sessions on the same connection — three connections would be three sources
  of truth. Signals fan out to the seams' callbacks *from the portal thread*,
  exactly as `HotkeyTap.start` and `watch_tray_ink` document ("the hook's own
  thread; callers marshal"). Nothing else runs on it.
- **The seams stay synchronous; the adapter blocks the calling thread.**
  Policy is portable (ADR 0005), so `inject.py` and `hotkey.py` do not learn
  that portals are request/response. Typing and paste already run on the
  `_process` worker, where blocking is free. Two timeout classes, named in the
  adapter: **bounded** — every D-Bus method reply (`CreateSession`,
  `ListShortcuts`, `NotifyKeyboardKeysym`, `SetSelection`) waits at most 5 s,
  then the rung fails and dictation falls through to inserting the raw
  transcript; **consent** — `BindShortcuts` and `RemoteDesktop.Start` raise a
  dialog a human answers, so they are never awaited: fired from
  `request_permission()` (fire-and-forget by ADR 0012) with the outcome
  arriving on the `Response` signal. A blocking call from the GUI thread is a
  bug and is logged, not asserted — an assert can kill the tray.
- **Hand-built messages, not generated proxies.** Four interfaces and about a
  dozen calls, each with an explicit signature (`('a{sv}', {...})`), in one
  `portal.py`. Generated stubs would hide the `handle_token` / sender-path
  dance every portal call needs: subscribe to `Response` on
  `/org/freedesktop/portal/desktop/request/<unique-name-mangled>/<token>`
  *before* sending, then check the returned handle.
- **`cadent/platform/linux/` is a subpackage from day one.** ADR 0005 allowed
  the split "only if one bloats"; Linux carries X11, three portals, XDG entries
  and the tray door, and will. `portal.py` holds the connection, thread and
  Request plumbing; the seam adapters take it in their constructors, so tests
  drive them against a fake portal with no bus, and jeepney's I/O-free core
  lets message building be tested purely. The headless `ubuntu-24.04` CI leg
  runs no bus and no portal — it covers the fakes and the import-safety test,
  which now also asserts `PySide6` is absent from `cadent/platform/`.

## Considered options

- **QtDBus** — free to bundle, but wrong on three counts. Its signal slots
  fire only in a `QObject`'s thread with a Qt loop, so a hotkey would need a
  running `QApplication` to arrive — the only adapter with that need, paid for
  by every headless test and every fake at the seam. PySide's binding has an
  open bug where signals carrying `a{sv}` payloads silently fail
  (PYSIDE-2547), and every portal `Response` is exactly that; nested variants
  demarshal to `QDBusArgument`s walked by hand (PYSIDE-1904). And it loads the
  host's `libdbus-1.so.3` at runtime — one more load-bearing row.
- **dbus-fast** — the best-maintained option (bleak, Home Assistant), but
  asyncio/GLib-only: its loop would run on our thread and every blocking call
  would go through `run_coroutine_threadsafe`; not thread-safe across threads;
  a Cython `.so` in the bundle. More machinery for the same result.
- **python-sdbus** — its default bus is thread-local and its signals are
  asyncio-only; the wrong shape for shared-connection, blocking-from-workers.
- **dbus-python / pydbus** — need libdbus headers or the distro's PyGObject;
  not installable from the tarball or AppImage.
- **Leaving Qt-free as a preference** — rejected: a preference invites the
  next cheap Qt import, and the reasons it holds (headless adapter tests,
  uniform off-GUI-thread callback contract, swappable seams) are structural.
- **A connection per seam** — rejected: three unique names, three token
  stores, three recovery paths for one `xdg-desktop-portal` restart.
- **`QStyleHints.colorScheme` for the ink watcher** — the desktop-integration
  research's simpler route, now foreclosed by the rule; `watch_tray_ink`
  subscribes to `SettingChanged` on the shared connection instead.

## Consequences

**Maintenance risk is accepted knowingly.** jeepney has one maintainer and a
slow cadence (0.9.0, February 2025). The D-Bus wire protocol does not move,
Cadent uses a hand-countable slice of the library, and its I/O-free core is
small enough to vendor if it ever goes dark. The pinned version is a `uv.lock`
fact like any other.

**The tray-ink watcher (#20) inherits this** — same connection, same thread,
`SettingChanged` on `org.freedesktop.portal.Settings` — rather than deciding a
transport of its own.

**darwin and win32 change nothing**, but the invariant now names them: the
existing import-safety test grows one assertion, and any future adapter that
wants a toolkit loop has to argue with this ADR first.
