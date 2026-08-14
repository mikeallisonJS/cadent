"""Shared fixtures: a llama_cpp stand-in so cleanup tests never need the real
LLM, and the one QApplication the widget tests share."""

import sys
import time
import types

import pytest


@pytest.fixture(scope="session")
def qt_app():
    """The process-wide QApplication. Qt permits exactly one, so every widget
    test shares this instance rather than building its own."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.setStyleSheet("")


@pytest.fixture
def styled(qt_app):
    """The app as it actually runs: Fusion, plus the app-wide stylesheet.

    Most assertions are about behaviour and need neither. The ones about the
    design's own geometry need both — QSS padding is what sizes a nav row,
    the stylesheet's font is what a label wraps or elides in, and the base
    style decides what the padding is added to. Measured: the same sheet
    gives 37px rows on Fusion and 51px on the Windows 11 style; and a CI
    runner's default font is not the dev machine's (#143), so unstyled
    geometry isn't just different — it is nondeterministic across machines.
    """
    from cadent.theme.manager import ThemeManager

    manager = ThemeManager(qt_app, "dark")
    yield qt_app
    manager.setParent(None)
    manager.deleteLater()
    qt_app.setStyleSheet("")


def _destroy_pending_widgets():
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _drain_deferred_deletes():
    """Actually destroy widgets that asked to be deleted.

    deleteLater() only queues a DeferredDelete event, and these tests never
    run an event loop — so without this every widget any test ever built stays
    alive. That is not just memory: app.setStyleSheet() re-polishes every live
    widget, so a leaking suite gets quadratically slower.

    Drained at setup as well as teardown (#119): a teardown-only drain leaves
    anything that queued its delete *during* teardown — a replaced cell
    widget, a focus proxy — alive into the next test, where the first
    processEvents() delivers those deletes amid five shown windows. Starting
    every test with an empty queue means processEvents() can only ever deliver
    deletes the test itself queued.

    Tried first as the whole fix for #119's access violation, and it was not:
    nor were the two real races it turned up on the way (the wizard probe
    thread's cross-thread emit, see `_probe_hardware`, and threads outliving
    their test, see `_no_thread_outlives_its_test`). What closed that crash
    was `_panes_never_see_the_real_data_dir` and `_no_window_outlives_its_test`
    below — either was enough on its own in a soak, and both are kept;
    `docs/research/qt-suite-access-violation.md` has the mechanism.
    """
    _destroy_pending_widgets()
    yield
    _destroy_pending_widgets()


def top_level_widgets():
    """The windows alive right now, as a set that survives their deletion.

    Membership is by identity — PySide6 hands out one wrapper per C++ object —
    so this stays answerable even after some of them have been destroyed.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return set(app.topLevelWidgets()) if app is not None else set()


def destroy_windows_since(before):
    """Destroy every top-level widget that appeared after `before` was taken.

    Added, never inherited: a fixture's own window is built inside that
    fixture's setup, so tearing down "every window" would take it out from
    under the test that asked for it.
    """
    for widget in top_level_widgets() - before:
        widget.close()
        widget.deleteLater()
    _destroy_pending_widgets()


@pytest.fixture(autouse=True)
def _no_window_outlives_its_test():
    """Close and destroy the windows a test left behind.

    `deleteLater()` at the end of a test is easy to forget and nothing notices:
    the assertions still pass, so the only symptom is that the next test starts
    with one more Settings window alive. `test_settings_ui.py` alone used to
    end with ~500 of them, which is what #119's crash fed on and what made the
    file take twice as long as it needed to — `setStyleSheet()` re-polishes
    every live widget. `tests/test_suite_guards.py` tests this fixture,
    because a guard nobody can see failing is a guard that stops working.
    """
    before = top_level_widgets()
    yield
    destroy_windows_since(before)


@pytest.fixture(autouse=True)
def _no_thread_outlives_its_test():
    """Join every thread the test started before the next test begins.

    The wizard probes hardware on a daemon thread (§6.2), so every wizard
    construction starts one — and tests build wizards constantly. A probe
    that outlives its test races the next test two distinct ways (#119): it
    warms `hardware._cached` *after* that test's reset, with values computed
    under the previous test's monkeypatches — a gpu_wizard test then reads a
    dx12_gpu=False machine and recommends the wrong model — and it drives
    native probes (cuInit, D3D12, DXGI COM) concurrently with whatever the
    next test is doing, which on a bad day is an access violation in a test
    that never started a thread at all.
    """
    import threading

    before = set(threading.enumerate())
    yield
    for thread in threading.enumerate():
        if thread not in before and thread.daemon:
            thread.join(timeout=10)


@pytest.fixture(scope="session")
def _absent_data_dir(tmp_path_factory):
    """A data directory that is not the developer's, and does not exist.

    Not created: the app's own files are absent on a fresh machine, which is
    the state CI runs in and therefore the one the suite should describe.
    """
    return tmp_path_factory.mktemp("not-the-real-data-dir") / "absent"


@pytest.fixture(autouse=True)
def _panes_never_see_the_real_data_dir(monkeypatch, _absent_data_dir):
    """Point the paths a pane falls back on somewhere harmless (#119).

    `PaneContext` defaults to the real `%LOCALAPPDATA%/Cadent` files, which
    is correct in the app and wrong here twice: a Settings window built
    without explicit paths loaded the developer's own vocabulary.json into the
    table under test, and its watcher then watched that file. The second is
    what made #119 machine-dependent rather than merely intermittent — see
    `docs/research/qt-suite-access-violation.md`.

    Scoped to the three paths `PaneContext` resolves through `cfg` at
    construction, which is the only seam a monkeypatch here can reach. Every
    other reader of these constants binds its own copy with `from .config
    import ...` — `MODELS_DIR` in `stt`, and `CONFIG_PATH` as `ConfigStore`'s
    default argument — so this fixture does not redirect a bare
    `ConfigStore()`. No test builds one; a test that wants to has to pass a
    path itself.
    """
    from cadent import config as cfg

    monkeypatch.setattr(cfg, "VOCAB_PATH", _absent_data_dir / "vocabulary.json")
    monkeypatch.setattr(cfg, "SNIPPETS_PATH", _absent_data_dir / "snippets.json")
    monkeypatch.setattr(cfg, "CONFIG_PATH", _absent_data_dir / "config.json")


@pytest.fixture(autouse=True)
def _reset_hardware_cache():
    """Never let one test's probe answer another test's question.

    `hardware.detect_cached()` is a module-level cache by design — the probe
    runs once per session (§6.2). In tests that makes the result depend on
    whether something earlier happened to build a wizard, which probes on
    construction. The failure is silent and order-dependent: monkeypatching
    `detect` does nothing once the cache is warm, so the test asserts against
    the developer's own GPU instead of the fallback it meant to check.
    """
    from cadent import hardware

    hardware.reset_cache()
    yield
    hardware.reset_cache()


class FakeMonitor:
    """A microphone that is only ever as loud as the test says.

    Stands in for `audio.LevelMonitor` wherever a surface is asked to show a
    live level — the real one needs a device, and the property under test is
    almost always *that something is listening*, not what it heard.
    """

    def __init__(self, level: float = 0.3) -> None:
        self.level = 0.0
        self._when_open = level
        self.opened: list[str | None] = []
        self.stops = 0
        self.suspends = 0
        self.resumes = 0

    @property
    def listening(self) -> bool:
        return self.level > 0

    def start(self, device):
        self.opened.append(device)
        self.level = self._when_open

    def stop(self):
        self.stops += 1
        self.level = 0.0

    def suspend(self):
        self.suspends += 1
        self.level = 0.0

    def resume(self):
        self.resumes += 1
        if self.opened:
            self.level = self._when_open


@pytest.fixture
def mic():
    return FakeMonitor()


# Real Windows input device names, measured on the machine that reported #106.
# Shared because two files need them for different reasons — how a row divides
# space, and whether that survives 225% text scale — and the point of them is
# that they are *real* lengths: every render pass and test before #106 used
# short fakes ("Rode NT-USB Mini" is 16 characters), which is exactly why a
# combo sizing itself to its widest item went unnoticed.
LONG_DEVICE_NAMES = [
    "Microphone (Steam Streaming Microphone Wave)",   # 44
    "Stereo Mix (Realtek HD Audio Stereo input)",     # 42
    "Microphone (Realtek HD Audio Mic input)",        # 39
    "Line In (Realtek HD Audio Line input)",          # 37
]


@pytest.fixture
def long_device_names():
    return list(LONG_DEVICE_NAMES)


class FakeLlama:
    instances = []

    # Predicates on the constructor kwargs, set by tests that need a rung of
    # the runtime ladder to fail (#116). Both failure modes are real and
    # different: a GPU backend that can't be initialised throws at
    # construction, while one that initialises and then can't compute — a
    # driver missing the shaders llama.cpp wants — only throws at the first
    # generation, which is the #38 rule restated for the cleanup path.
    fail_load_when = None
    fail_generate_when = None

    # What `llama_cpp.llama_supports_gpu_offload()` says, and how many times
    # it was asked. Default False, so a test that wants the GPU rung has to
    # say so: it is the honest default for the machines this suite runs on,
    # and the third failure mode it stands for — no GPU there at all — is
    # the one llama.cpp does *not* report as a failure (it quietly puts every
    # layer on the CPU and succeeds).
    gpu_offload_supported = False
    offload_queries = 0

    def __init__(self, model_path, **kwargs):
        self.model_path = model_path
        self.kwargs = kwargs
        self.reply = "cleaned text"
        self.delay = 0.0
        self.calls = []
        FakeLlama.instances.append(self)      # appended first: a failed rung
        if FakeLlama.fail_load_when and FakeLlama.fail_load_when(kwargs):
            raise RuntimeError("llama could not load")   # is still an attempt
        self.raise_on_call = bool(FakeLlama.fail_generate_when
                                  and FakeLlama.fail_generate_when(kwargs))

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on_call:
            raise RuntimeError("llama crashed")
        if self.delay:
            time.sleep(self.delay)
        return {"choices": [{"message": {"content": self.reply}}]}


def _fake_supports_gpu_offload():
    FakeLlama.offload_queries += 1
    return FakeLlama.gpu_offload_supported


@pytest.fixture
def fake_llama(monkeypatch):
    FakeLlama.instances = []
    FakeLlama.fail_load_when = None
    FakeLlama.fail_generate_when = None
    FakeLlama.gpu_offload_supported = False
    FakeLlama.offload_queries = 0
    monkeypatch.setitem(sys.modules, "llama_cpp", types.SimpleNamespace(
        Llama=FakeLlama,
        llama_supports_gpu_offload=_fake_supports_gpu_offload))
    return FakeLlama


@pytest.fixture
def model_file(tmp_path):
    p = tmp_path / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    p.write_bytes(b"gguf")
    return p


# ---- fakes at the platform seams (ADR 0005) ---------------------------------
# The formalization of the monkeypatch seam test_inject.py invented: app-logic
# tests hand these to the constructor under test; adapter internals are
# platform-skipped and run on the real machines.

class FakeKeyboard:
    def __init__(self, *, short_send=False, raise_on_type=False, held=False):
        self.typed: list[list[int]] = []
        self.chords: list[list[int]] = []
        self.mask_keys = 0
        self.short_send = short_send
        self.raise_on_type = raise_on_type
        self.held = held

    def send_text_units(self, units):
        if self.raise_on_type:
            raise OSError("boom")
        self.typed.append(list(units))
        return not self.short_send

    def send_chord(self, keys):
        self.chords.append(list(keys))

    def send_mask_key(self):
        self.mask_keys += 1

    def modifiers_down(self):
        return self.held


class FakeClipboard:
    def __init__(self, *, raise_on_set=False):
        self.text: str | None = None
        self.writes: list[str] = []
        self.raise_on_set = raise_on_set
        self._seq = 0

    def get_text(self):
        return self.text

    def set_text(self, text, exclude_from_history):
        if self.raise_on_set:
            raise OSError("clipboard busy")
        self.text = text
        self.writes.append(text)
        self._seq += 1

    def sequence_number(self):
        return self._seq


class FakeFocusedApp:
    def __init__(self, name="app.exe", rect=None, blocked=None, *,
                 granted=True, running=None, names=None):
        self._name = name
        self._rect = rect
        self._blocked = blocked
        # The #148 probes: the permission grant, the running-apps picker rows
        # ((display, identity) pairs) and the display-name lookup table.
        self.granted = granted
        self.running = running or []
        self.names = names or {}

    def name(self):
        return self._name

    def window_rect(self):
        return self._rect

    def injection_blocked(self):
        return self._blocked

    def permission_granted(self):
        return self.granted

    def running_apps(self):
        return list(self.running)

    def display_name(self, identity):
        return self.names.get(identity.lower())


class FakeHotkeyTap:
    """Feed events with .press()/.release() as if the OS hook fired."""

    def __init__(self):
        self.on_event = None
        self.starts = 0
        self.stops = 0

    def start(self, on_event):
        self.on_event = on_event
        self.starts += 1

    def stop(self):
        self.on_event = None
        self.stops += 1

    def press(self, keycode, injected=False):
        self.on_event(keycode, True, injected)

    def release(self, keycode, injected=False):
        self.on_event(keycode, False, injected)


class FakeHardwareProbe:
    def __init__(self, *, vram=None, dx12=False, cpu="Test CPU", driver=False,
                 metal=False):
        self.vram = vram
        self.dx12 = dx12
        self.cpu = cpu
        self.driver = driver
        self.metal = metal
        self.cuda_probes = 0

    def cuda_total_memory(self):
        self.cuda_probes += 1
        return self.vram

    def dx12_gpu_present(self):
        return self.dx12

    def processor_name(self):
        return self.cpu

    def nvidia_driver_present(self):
        return self.driver

    def metal_gpu_present(self):
        return self.metal


class FakeDesktop:
    """Counts the shell actions a surface asked for; answers like NullDesktop."""

    def __init__(self):
        self.opened: list = []
        self.permission_settings_opens = 0
        # The tray ink a test wants this desktop to be sitting on, and the
        # callback the app registered — so a test can drive a taskbar theme
        # change without a registry.
        self.ink = "#ffffff"
        self.ink_watcher = None
        self.ink_watch_stops = 0

    def open_path(self, path):
        self.opened.append(path)

    def open_permission_settings(self):
        self.permission_settings_opens += 1

    def text_scale_factor(self):
        return 1.0

    def high_contrast(self):
        return False

    def animations_enabled(self):
        return True

    def tray_ink(self):
        return self.ink

    def watch_tray_ink(self, on_change):
        self.ink_watcher = on_change

    def stop_watching_tray_ink(self):
        self.ink_watcher = None
        self.ink_watch_stops += 1


def make_platform(**overrides):
    """A Platform whose adapters are all fakes; override any piece by name
    (including `capabilities`)."""
    import dataclasses

    from cadent.platform import fallback

    base = fallback.create()
    fakes = dict(
        keyboard=FakeKeyboard(),
        clipboard=FakeClipboard(),
        focused_app=FakeFocusedApp(),
        hotkey_tap=FakeHotkeyTap(),
        hardware=FakeHardwareProbe(),
        desktop=FakeDesktop(),
    )
    fakes.update(overrides)
    return dataclasses.replace(base, **fakes)


@pytest.fixture
def fake_platform():
    return make_platform()


def pin_one_rung_platform(monkeypatch):
    """Pin `platform.current()` to a fake carrying darwin's runtime column
    (ADR 0003): one speech rung for both engines. The counterpart of
    `pinned_win32_facts` for tests that describe the one-rung behavior —
    config sanitize, the provider ladder — on any OS."""
    import dataclasses

    from cadent import platform as platform_pkg

    plat = make_platform()
    caps = dataclasses.replace(
        plat.capabilities,
        stt_runtimes={"faster-whisper": ("auto", "cpu"),
                      "parakeet": ("auto", "cpu")})
    monkeypatch.setattr(platform_pkg, "_current",
                        dataclasses.replace(plat, capabilities=caps))


def pin_darwin_ui_platform(monkeypatch, *, granted=True, running=None,
                           names=None):
    """Pin `platform.current()` to a fake carrying darwin's UI-facing column
    (spec §1.3, #148): Accessibility preflight, no GPU pack, hidden runtime
    combo, the running-apps picker, the zero-buffer hint. Returns the platform
    so tests can drive its FakeFocusedApp/FakeDesktop directly — darwin UI
    behavior is tested by building these facts, never by the host OS."""
    import dataclasses

    from cadent import platform as platform_pkg

    plat = make_platform(
        focused_app=FakeFocusedApp(name="com.apple.Terminal", granted=granted,
                                   running=running, names=names))
    caps = dataclasses.replace(
        plat.capabilities,
        default_injection_strategy="clipboard",
        injection_rungs=("paste", "type"),
        paste_chord="cmd+v",
        default_overrides=(),
        default_override_reasons={},
        auto_learn_overrides=False,
        gpu_only_engines=frozenset(),
        show_runtime_combo=False,
        gpu_pack_available=False,
        permission_preflight="accessibility",
        autostart_label="Start at login",
        app_identity_placeholder="com.example.app",
        app_picker=True,
        mic_permission_hint="check Microphone permission for your terminal "
                            "or IDE in System Settings",
        tray_click_toggles_pause=False,
        tray_icon_painted_by_os=True,
        modifier_captions={"ctrl": "Ctrl", "shift": "Shift",
                           "alt": "Option", "option": "Option",
                           "win": "Cmd", "cmd": "Cmd"},
        theme_subtitle="Follows your Mac's Appearance setting",
        high_contrast_reason=("macOS is set to increase contrast, so "
                              "Cadent is following your system colours"))
    plat = dataclasses.replace(plat, capabilities=caps)
    monkeypatch.setattr(platform_pkg, "_current", plat)
    return plat


@pytest.fixture
def pinned_win32_facts(monkeypatch):
    """Pin `platform.current()` to a fake carrying the portable win32-column
    facts (fallback's Capabilities: the VK table, "type" default, seed
    overrides). Tests that *describe* that column — chord parsing over VK
    ints, seed rules in config and the overrides pane — use this so they stay
    deterministic on the darwin CI leg, where the real column now diverges
    (#144). Darwin-column behavior is tested by building darwin-shaped
    capabilities explicitly, never by running these tests on a Mac."""
    import dataclasses

    plat = make_platform()
    from cadent import platform as platform_pkg

    # Named-setting copy is the one column fallback deliberately does *not*
    # mirror (it has no OS to name), so the win32 strings are spelled out here
    # the way pin_darwin_ui_platform spells out darwin's.
    plat = dataclasses.replace(plat, capabilities=dataclasses.replace(
        plat.capabilities,
        theme_subtitle="Follows your Windows app colour mode",
        high_contrast_reason=("Windows is using a contrast theme, so Cadent "
                              "is following your system colours")))

    # The memo, not the function: the `machine` fixture layers a hardware
    # probe on top via `dataclasses.replace(platform_pkg.current(), ...)`,
    # which must see (and then re-pin over) this platform.
    monkeypatch.setattr(platform_pkg, "_current", plat)
    return plat


@pytest.fixture
def machine(monkeypatch):
    """Shape the machine `hardware.detect()` (and everything above it) sees.

    Replaces the pre-seam pattern of monkeypatching `hardware.cuda_total_memory`
    and friends, which now live on the platform's HardwareProbe.
    """
    import dataclasses

    import cadent.platform as platform_pkg

    def set_machine(*, vram=None, dx12=False, cpu="Test CPU", driver=False,
                    metal=False):
        probe = FakeHardwareProbe(vram=vram, dx12=dx12, cpu=cpu, driver=driver,
                                  metal=metal)
        plat = dataclasses.replace(platform_pkg.current(), hardware=probe)
        monkeypatch.setattr(platform_pkg, "_current", plat)
        return probe

    return set_machine
