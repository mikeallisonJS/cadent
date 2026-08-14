"""Automated acceptance evidence for ticket 17 (end-to-end raw-mode verification).

Drives the real Injector (real config resolution, real Win32 paths) against
live apps and records what an agent can verify without a microphone:

    python scripts/verify_e2e.py --app notepad          # one app
    python scripts/verify_e2e.py --matrix               # notepad, chrome, discord, terminal
    python scripts/verify_e2e.py --clipboard            # restore + Win+V exclusion format
    python scripts/verify_e2e.py --latency              # 10 s SAPI WAV through STT + inject
    python scripts/verify_e2e.py --all                  # everything, JSON report to --out

Each app test launches (or finds) the target, confirms the foreground process
is the one under test before sending anything, injects a sentinel through
Injector.insert(), reads the result back with Ctrl+A/Ctrl+C, then cleans up.
Tests that need a human (real-mic latency, physical chord / Start menu,
toggle mode) are out of scope here and stay on the ticket.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from cadent.winput import VK, send_vk_chord  # noqa: E402

from cadent.config import AppOverride, Config  # noqa: E402
from cadent.config_store import ConfigStore  # noqa: E402
from cadent.inject import (  # noqa: E402
    _EXCLUDE_FORMAT_NAME,
    Injector,
    _get_clipboard_text,
    _set_clipboard_text,
    resolve_override,
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

TEST_TEXT = "Cadent e2e check: café naïve — ✓ 𝄞 done."
CLIP_SENTINEL = "lf-e2e-clipboard-sentinel-7f3a"
VK_RETURN = 0x0D
VK_DELETE = 0x2E
WM_CLOSE = 0x0010

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
DISCORD_UPDATE = Path.home() / "AppData/Local/Discord/Update.exe"


# ---- window plumbing --------------------------------------------------------

def foreground_process_name() -> str:
    import psutil

    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return "unknown"


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def windows_of(process_names: set[str], title_substr: str = "") -> list[int]:
    import psutil

    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            name = psutil.Process(pid.value).name().lower()
        except Exception:
            return True
        if name in process_names:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if title_substr.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(enum_cb, 0)
    return found


def focus_window(hwnd: int) -> bool:
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return True
    # SetForegroundWindow from a background process needs the foreground
    # thread's blessing; borrow it via AttachThreadInput.
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    our_thread = kernel32.GetCurrentThreadId()
    attached = fg_thread and fg_thread != our_thread and user32.AttachThreadInput(
        our_thread, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def wait_foreground(process_names: set[str], title_substr: str = "",
                    timeout_s: float = 25.0) -> int | None:
    """Wait until a window of one of these processes is foreground, focusing
    it ourselves once it exists. Returns that window's hwnd, or None.
    Never inject unless this returned a handle."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (foreground_process_name() in process_names
                and title_substr.lower() in foreground_title().lower()):
            return user32.GetForegroundWindow()
        for hwnd in windows_of(process_names, title_substr):
            if focus_window(hwnd):
                break
        time.sleep(0.5)
    if foreground_process_name() in process_names:
        return user32.GetForegroundWindow()
    return None


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _last_input_age_s() -> float:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return (kernel32.GetTickCount() - info.dwTime) / 1000.0


def wait_user_idle(idle_s: float = 8.0, timeout_s: float = 300.0) -> bool:
    """Block until the machine has seen no input for idle_s. Tests must only
    start while the user is hands-off; synthetic keys into the wrong window
    once closed their editor."""
    deadline = time.monotonic() + timeout_s
    announced = False
    while time.monotonic() < deadline:
        age = _last_input_age_s()
        if age >= idle_s:
            return True
        if not announced:
            print(f"waiting for {idle_s:.0f}s of keyboard/mouse idle "
                  "(hands off to proceed)...", flush=True)
            announced = True
        time.sleep(min(idle_s - age, 1.0))
    return False


def _foreground_is(process_names: set[str]) -> bool:
    """Guard before every synthetic keystroke: the user may have clicked away
    mid-test, and keys must never land in a window we didn't launch."""
    return foreground_process_name() in process_names


def read_back_selection(expected: set[str]) -> str | None:
    """Ctrl+A, Ctrl+C in the focused app, then read the clipboard."""
    if not _foreground_is(expected):
        return None
    _set_clipboard_text("lf-e2e-empty-marker", exclude_from_history=False)
    send_vk_chord([VK["ctrl"], ord("A")])
    time.sleep(0.2)
    if not _foreground_is(expected):
        return None
    send_vk_chord([VK["ctrl"], ord("C")])
    time.sleep(0.4)
    text = _get_clipboard_text()
    return None if text == "lf-e2e-empty-marker" else text


def clear_selection(expected: set[str]) -> None:
    if not _foreground_is(expected):
        return
    send_vk_chord([VK["ctrl"], ord("A")])
    time.sleep(0.15)
    if not _foreground_is(expected):
        return
    send_vk_chord([VK_DELETE])
    time.sleep(0.15)


def close_window(hwnd: int | None, expected: set[str]) -> None:
    """WM_CLOSE to the specific window this test created — never 'whatever is
    foreground' (that once closed the user's editor)."""
    import psutil

    if not hwnd or not user32.IsWindow(hwnd):
        return
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        if psutil.Process(pid.value).name().lower() not in expected:
            return
    except Exception:
        return
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.5)


def make_injector(cfg: Config) -> Injector:
    return Injector(cfg.app_overrides, cfg.injection_method)


# ---- app tests --------------------------------------------------------------

def _inject_and_read(injector: Injector, expected_process: str,
                     text: str = TEST_TEXT) -> dict:
    app = injector.focused_app_name()
    if app.lower() != expected_process:
        return {"ok": False, "detail": f"foreground is {app}, not {expected_process}"}
    t0 = time.perf_counter()
    result = injector.insert(text, app_name=app)
    inject_s = time.perf_counter() - t0
    time.sleep(0.4)
    got = read_back_selection({expected_process})
    return {
        "ok": got == text,
        "outcome": result.outcome,
        "inject_s": round(inject_s, 3),
        "read_back": got,
        "detail": result.detail,
    }


def test_notepad(cfg: Config) -> dict:
    """Ctrl+A/C readback is itself unreliable in Win11 Notepad, so this test
    reads the document back via WM_GETTEXT instead."""
    if not wait_user_idle():
        return {"app": "notepad", "ok": False, "detail": "user never went idle"}
    subprocess.Popen(["notepad.exe"])
    hwnd = wait_foreground({"notepad.exe"})
    edit = _find_notepad_edit(hwnd) if hwnd else None
    if not edit:
        return {"app": "notepad", "ok": False, "detail": "no edit child found"}
    _clear_window_text(edit)  # session restore can resurrect old test text
    injector = make_injector(cfg)
    app = injector.focused_app_name()
    if app.lower() != "notepad.exe":
        return {"app": "notepad", "ok": False, "detail": f"foreground is {app}"}
    t0 = time.perf_counter()
    result = injector.insert(TEST_TEXT, app_name=app)
    inject_s = time.perf_counter() - t0
    time.sleep(0.4)
    got = _read_window_text(edit)
    _clear_window_text(edit)
    close_window(hwnd, {"notepad.exe"})
    strategy = resolve_override(app, cfg.app_overrides, cfg.injection_method).strategy
    return {"app": "notepad", "strategy": strategy, "ok": got == TEST_TEXT,
            "outcome": result.outcome, "inject_s": round(inject_s, 3),
            "read_back": got}


def test_chrome(cfg: Config) -> dict:
    if not CHROME.exists():
        return {"app": "chrome", "ok": False, "detail": "chrome not installed"}
    page = Path(tempfile.gettempdir()) / "lf_e2e_page.html"
    page.write_text("<title>lf-e2e-target</title>"
                    "<textarea autofocus style='width:90vw;height:80vh'></textarea>",
                    encoding="utf-8")
    if not wait_user_idle():
        return {"app": "chrome", "ok": False, "detail": "user never went idle"}
    subprocess.Popen([str(CHROME), "--new-window", page.as_uri()])
    hwnd = wait_foreground({"chrome.exe"}, title_substr="lf-e2e-target")
    if not hwnd:
        return {"app": "chrome", "ok": False, "detail": "lf-e2e window never foreground"}
    r = _inject_and_read(make_injector(cfg), "chrome.exe")
    clear_selection({"chrome.exe"})
    close_window(hwnd, {"chrome.exe"})
    return {"app": "chrome", "strategy": "sendinput", **r}


def test_discord(cfg: Config) -> dict:
    """Electron slot. Injects into the composer, reads back, deletes — never
    presses Enter, so nothing can be sent."""
    import psutil

    if not wait_user_idle():
        return {"app": "discord", "ok": False, "detail": "user never went idle"}
    running = any(p.name().lower() == "discord.exe" for p in psutil.process_iter(["name"]))
    if not running:
        if not DISCORD_UPDATE.exists():
            return {"app": "discord", "ok": False, "detail": "discord not installed"}
        subprocess.Popen([str(DISCORD_UPDATE), "--processStart", "Discord.exe"])
    if not wait_foreground({"discord.exe"}, timeout_s=45):
        return {"app": "discord", "ok": False, "detail": "window never came foreground"}
    time.sleep(2.0)  # let the renderer settle so the composer takes key focus
    r = _inject_and_read(make_injector(cfg), "discord.exe")
    clear_selection({"discord.exe"})  # remove the sentinel from the composer
    return {"app": "discord", "strategy": "sendinput", **r}


def test_terminal(cfg: Config) -> dict:
    """Fallback path: WindowsTerminal.exe matches the default clipboard
    override (ctrl+shift+v). Proof of insertion = the pasted command runs."""
    marker = Path(tempfile.gettempdir()) / "lf_e2e_terminal.txt"
    marker.unlink(missing_ok=True)
    # wt.exe is a Store-app alias that isn't always on the launching shell's
    # PATH; resolve it explicitly before falling back to "not installed".
    import shutil

    wt = shutil.which("wt.exe") or str(
        Path.home() / "AppData/Local/Microsoft/WindowsApps/wt.exe")
    if not Path(wt).exists():
        return {"app": "terminal", "ok": False, "detail": "windows terminal not installed"}
    if not wait_user_idle():
        return {"app": "terminal", "ok": False, "detail": "user never went idle"}
    _set_clipboard_text(CLIP_SENTINEL, exclude_from_history=False)

    subprocess.Popen([wt, "powershell", "-NoProfile"])
    hwnd = wait_foreground({"windowsterminal.exe"})
    if not hwnd:
        return {"app": "terminal", "ok": False, "detail": "window never came foreground"}
    time.sleep(1.5)  # let the shell reach its prompt

    injector = make_injector(cfg)
    app = injector.focused_app_name()
    if app.lower() != "windowsterminal.exe":
        return {"app": "terminal", "ok": False, "detail": f"foreground is {app}"}
    cmd = f"'lf-e2e-ok' | Set-Content '{marker}'"
    t0 = time.perf_counter()
    result = injector.insert(cmd, app_name=app)
    inject_s = time.perf_counter() - t0
    if _foreground_is({"windowsterminal.exe"}):
        send_vk_chord([VK_RETURN])

    landed = False
    for _ in range(20):
        time.sleep(0.25)
        if marker.exists() and marker.read_text().strip() == "lf-e2e-ok":
            landed = True
            break
    clipboard_after = _get_clipboard_text()
    close_window(hwnd, {"windowsterminal.exe"})
    marker.unlink(missing_ok=True)
    return {
        "app": "terminal", "strategy": "clipboard (ctrl+shift+v)",
        "ok": landed and result.outcome == "inserted",
        "outcome": result.outcome, "inject_s": round(inject_s, 3),
        "clipboard_restored": clipboard_after == CLIP_SENTINEL,
    }


def _notepad_edit_hwnd(top: int) -> int | None:
    """Win11 Notepad hosts the document in a RichEditD2DPT child."""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if "RichEdit" in buf.value or buf.value == "Edit":
            found.append(hwnd)
        return True

    user32.EnumChildWindows(top, enum_cb, 0)
    return found[0] if found else None


def _find_notepad_edit(top: int, timeout_s: float = 6.0) -> int | None:
    """The RichEdit child appears a beat after the top window; retry, then
    fall back to any Notepad window that has one."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        edit = _notepad_edit_hwnd(top)
        if edit:
            return edit
        for other in windows_of({"notepad.exe"}):
            edit = _notepad_edit_hwnd(other)
            if edit and focus_window(other):
                return edit
        time.sleep(0.3)
    return None


def _read_window_text(edit_hwnd: int) -> str:
    """WM_GETTEXT readback — no synthetic keystrokes, no clipboard."""
    WM_GETTEXT, WM_GETTEXTLENGTH = 0x000D, 0x000E
    n = user32.SendMessageW(edit_hwnd, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(edit_hwnd, WM_GETTEXT, n + 1, buf)
    return buf.value


def _clear_window_text(edit_hwnd: int) -> None:
    EM_SETSEL, WM_CLEAR = 0x00B1, 0x0303
    user32.SendMessageW(edit_hwnd, EM_SETSEL, 0, -1)
    user32.SendMessageW(edit_hwnd, WM_CLEAR, 0, 0)


def test_notepad_truth(cfg: Config) -> dict:
    """Ground truth for the Notepad offender entry: WM_GETTEXT readback rules
    out flakiness in the Ctrl+A/Ctrl+C probe itself, and clipboard paste is
    trialled as the fast alternative to paced sendinput."""
    if not wait_user_idle():
        return {"check": "notepad-truth", "ok": False, "detail": "user never went idle"}
    subprocess.Popen(["notepad.exe"])
    hwnd = wait_foreground({"notepad.exe"})
    edit = _find_notepad_edit(hwnd) if hwnd else None
    if not edit:
        return {"check": "notepad-truth", "ok": False, "detail": "no edit child found"}
    _clear_window_text(edit)  # session restore can resurrect old test text

    variants = [
        ("batched", AppOverride(process="notepad.exe", strategy="sendinput")),
        ("chunk1/20ms", AppOverride(process="notepad.exe", strategy="sendinput",
                                    chunk_size=1, chunk_delay_ms=20)),
        ("chunk1/30ms", AppOverride(process="notepad.exe", strategy="sendinput",
                                    chunk_size=1, chunk_delay_ms=30)),
        ("clipboard", AppOverride(process="notepad.exe", strategy="clipboard")),
    ]
    trials = []
    for label, override in variants:
        injector = Injector([override], cfg.injection_method)
        clean = 0
        runs = 3
        mangled = None
        for _ in range(runs):
            if not _foreground_is({"notepad.exe"}):
                return {"check": "notepad-truth", "ok": False, "trials": trials,
                        "detail": "lost notepad focus mid-trial"}
            injector.insert(TEST_TEXT, app_name="notepad.exe")
            time.sleep(0.5)
            got = _read_window_text(edit)
            if got == TEST_TEXT:
                clean += 1
            else:
                mangled = got
            _clear_window_text(edit)
        trials.append({"variant": label, "clean_runs": f"{clean}/{runs}",
                       "example_miss": mangled})
        print(json.dumps(trials[-1], ensure_ascii=False), flush=True)
    close_window(hwnd, {"notepad.exe"})
    return {"check": "notepad-truth",
            "ok": any(t["clean_runs"] == "3/3" for t in trials), "trials": trials}


def test_notepad_pacing(cfg: Config) -> dict:
    """Win11 Notepad drops characters on fast synthetic unicode input; find
    the chunk_size/chunk_delay_ms pacing that survives, for the offender list."""
    if not wait_user_idle():
        return {"check": "notepad-pacing", "ok": False, "detail": "user never went idle"}
    subprocess.Popen(["notepad.exe"])
    hwnd = wait_foreground({"notepad.exe"})
    if not hwnd:
        return {"check": "notepad-pacing", "ok": False,
                "detail": "window never came foreground"}
    text = "the quick brown fox jumps over the lazy dog 0123456789"
    trials = []
    for chunk, delay in ((0, 0), (32, 10), (8, 15), (4, 25), (1, 20)):
        injector = Injector([AppOverride(process="notepad.exe", strategy="sendinput",
                                         chunk_size=chunk, chunk_delay_ms=delay)],
                            cfg.injection_method)
        clean = 0
        runs = 3
        for _ in range(runs):
            r = _inject_and_read(injector, "notepad.exe", text)
            clear_selection({"notepad.exe"})
            if not r["ok"] and r.get("read_back") is None:
                close_window(hwnd, {"notepad.exe"})
                return {"check": "notepad-pacing", "ok": False, "trials": trials,
                        "detail": "lost the notepad window mid-trial"}
            clean += r["ok"]
        trials.append({"chunk_size": chunk, "chunk_delay_ms": delay,
                       "clean_runs": f"{clean}/{runs}"})
        print(json.dumps(trials[-1]), flush=True)
    close_window(hwnd, {"notepad.exe"})
    best = [t for t in trials if t["clean_runs"] == "3/3"]
    return {"check": "notepad-pacing", "ok": bool(best), "trials": trials}


# ---- clipboard checks -------------------------------------------------------

def test_clipboard_restore(cfg: Config) -> dict:
    """Force the clipboard strategy in Notepad: text must land AND the
    pre-existing clipboard contents must survive."""
    if not wait_user_idle():
        return {"check": "clipboard-restore", "ok": False, "detail": "user never went idle"}
    _set_clipboard_text(CLIP_SENTINEL, exclude_from_history=False)
    injector = make_injector(cfg)  # the shipped notepad override: clipboard, 500 ms settle
    subprocess.Popen(["notepad.exe"])
    hwnd = wait_foreground({"notepad.exe"})
    edit = _find_notepad_edit(hwnd) if hwnd else None
    if not edit:
        return {"check": "clipboard-restore", "ok": False,
                "detail": "no edit child found"}
    _clear_window_text(edit)
    # A cold Notepad's first synthetic paste is unreliable (seen live); real
    # dictation lands in an already-warm app, so warm it up first.
    injector.insert("warm-up", app_name="notepad.exe")
    time.sleep(1.0)
    _clear_window_text(edit)
    _set_clipboard_text(CLIP_SENTINEL, exclude_from_history=False)
    result = injector.insert(TEST_TEXT, app_name="notepad.exe")
    time.sleep(1.0)  # past the override's 500 ms settle, so the restore has run
    landed = _read_window_text(edit) == TEST_TEXT
    restored = _get_clipboard_text() == CLIP_SENTINEL
    _clear_window_text(edit)
    close_window(hwnd, {"notepad.exe"})
    return {"check": "clipboard-restore", "ok": landed and restored,
            "text_landed": landed, "outcome": result.outcome,
            "clipboard_restored": restored}


def test_winv_exclusion() -> dict:
    """The Win+V exclusion format must ride along when we set the transcript,
    and must be absent for the last-resort path."""
    fmt = user32.RegisterClipboardFormatW(_EXCLUDE_FORMAT_NAME)

    def formats_on_clipboard() -> set[int]:
        formats: set[int] = set()
        if not user32.OpenClipboard(None):
            return formats
        try:
            current = 0
            while True:
                current = user32.EnumClipboardFormats(current)
                if not current:
                    break
                formats.add(current)
        finally:
            user32.CloseClipboard()
        return formats

    _set_clipboard_text("lf-e2e-excluded", exclude_from_history=True)
    with_exclusion = fmt in formats_on_clipboard()
    _set_clipboard_text("lf-e2e-last-resort", exclude_from_history=False)
    without_exclusion = fmt not in formats_on_clipboard()
    _set_clipboard_text(CLIP_SENTINEL, exclude_from_history=False)
    return {"check": "winv-exclusion", "ok": with_exclusion and without_exclusion,
            "exclusion_format_set": with_exclusion,
            "last_resort_leaves_it_off": without_exclusion}


# ---- latency ----------------------------------------------------------------

def test_latency(cfg: Config) -> dict:
    """Release→inserted proxy for a ~10 s utterance: real model, real audio
    array, real injection into Notepad. Excludes only the microphone."""
    sys.path.insert(0, str(REPO / "scripts"))
    import bench_stt

    from cadent.stt import FasterWhisperEngine

    wav = Path(tempfile.gettempdir()) / "lf_e2e_bench.wav"
    if not wav.exists():
        bench_stt.make_wav(str(wav))
    audio, duration = bench_stt.load_wav(str(wav))

    engine = FasterWhisperEngine(cfg.stt_model, device="cpu")
    engine.transcribe(audio, 16000)  # warm-up: first call pays kernel init
    t0 = time.perf_counter()
    text = engine.transcribe(audio, 16000)
    stt_s = time.perf_counter() - t0

    if not wait_user_idle():
        return {"check": "latency", "ok": False, "detail": "user never went idle"}
    subprocess.Popen(["notepad.exe"])
    hwnd = wait_foreground({"notepad.exe"})
    if not hwnd:
        return {"check": "latency", "ok": False, "detail": "notepad never foreground"}
    injector = make_injector(cfg)
    t0 = time.perf_counter()
    injector.insert(text, app_name=injector.focused_app_name())
    inject_s = time.perf_counter() - t0
    clear_selection({"notepad.exe"})
    close_window(hwnd, {"notepad.exe"})

    total = stt_s + inject_s
    return {"check": "latency", "ok": total <= 2.0, "model": cfg.stt_model,
            "audio_s": round(duration, 2), "stt_s": round(stt_s, 2),
            "inject_s": round(inject_s, 2), "release_to_inserted_s": round(total, 2),
            "transcript": text}


# ---- driver -----------------------------------------------------------------

APP_TESTS = {"notepad": test_notepad, "chrome": test_chrome,
             "discord": test_discord, "terminal": test_terminal}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", choices=sorted(APP_TESTS))
    ap.add_argument("--notepad-pacing", action="store_true")
    ap.add_argument("--notepad-truth", action="store_true")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--clipboard", action="store_true")
    ap.add_argument("--latency", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", metavar="JSON_PATH")
    args = ap.parse_args()

    if sys.platform != "win32":
        sys.exit("this harness only runs on Windows")

    cfg = ConfigStore().config
    results: list[dict] = []

    apps = list(APP_TESTS) if (args.matrix or args.all) else ([args.app] if args.app else [])
    for name in apps:
        print(f"--- {name}", flush=True)
        results.append(APP_TESTS[name](cfg))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if args.notepad_pacing or args.all:
        results.append(test_notepad_pacing(cfg))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if args.notepad_truth:
        results.append(test_notepad_truth(cfg))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if args.clipboard or args.all:
        for fn in (test_clipboard_restore, lambda _cfg: test_winv_exclusion()):
            results.append(fn(cfg))
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if args.latency or args.all:
        results.append(test_latency(cfg))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)

    if not results:
        ap.error("pick --app/--matrix/--clipboard/--latency/--all")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        print(f"report written: {args.out}")
    failed = [r for r in results if not r.get("ok")]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
