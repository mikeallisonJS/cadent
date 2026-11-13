"""Installed-apps lookup from XDG data dirs (spec §5.2, ADR 0009): pure files,
so it runs on every tier with no bus."""


import pytest

# The lookup itself needs no bus, but importing it runs the Linux package's
# `__init__`, which imports the portal and so jeepney — a Linux-only
# dependency. Without this the module fails to collect on Windows and macOS
# and takes the whole run down with it.
pytest.importorskip("jeepney")

from cadent.platform.linux import desktop_files  # noqa: E402
from cadent.platform.linux.desktop_files import DesktopIndex  # noqa: E402


def write(root, rel, body):
    path = root / "applications" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\n" + body, encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The machine's own Flatpak apps are not part of any expectation here.
    monkeypatch.setattr(desktop_files, "FLATPAK_EXPORTS", ())
    home, usr, flat = tmp_path / "home", tmp_path / "usr", tmp_path / "flat"
    return {"XDG_DATA_HOME": str(home), "XDG_DATA_DIRS": str(usr),
            "LANG": "de_DE.UTF-8", "dirs": (home, usr, flat)}


def test_lists_only_visible_applications(env):
    home, usr, _ = env["dirs"]
    write(usr, "org.mozilla.firefox.desktop", "Type=Application\nName=Firefox\n")
    write(usr, "hidden.desktop", "Type=Application\nName=H\nHidden=true\n")
    write(usr, "nodisplay.desktop", "Type=Application\nName=N\nNoDisplay=true\n")
    write(usr, "link.desktop", "Type=Link\nName=L\n")
    assert DesktopIndex(env).installed() == [("Firefox", "org.mozilla.firefox")]


def test_earlier_directory_wins_and_dedupes_by_id(env):
    home, usr, _ = env["dirs"]
    write(home, "a.desktop", "Type=Application\nName=Mine\n")
    write(usr, "a.desktop", "Type=Application\nName=System\n")
    assert DesktopIndex(env).installed() == [("Mine", "a")]


def test_flatpak_exports_are_searched(env, tmp_path, monkeypatch):
    flat = tmp_path / "flatpak"
    write(flat, "org.gnome.Calculator.desktop", "Type=Application\nName=Calc\n")
    monkeypatch.setattr(desktop_files, "FLATPAK_EXPORTS", (str(flat),))
    assert DesktopIndex(env).installed() == [("Calc", "org.gnome.Calculator")]


def test_display_name_is_localized_and_works_for_closed_apps(env):
    _, usr, _ = env["dirs"]
    write(usr, "org.mozilla.firefox.desktop",
          "Type=Application\nName=Firefox\nName[de]=Feuerfuchs\n")
    index = DesktopIndex(env)
    assert index.display_name("org.mozilla.firefox") == "Feuerfuchs"
    assert index.display_name("ORG.Mozilla.Firefox") == "Feuerfuchs"
    assert index.display_name("com.gone.app") is None
    env["LANG"] = "C"
    assert index.display_name("org.mozilla.firefox") == "Firefox"


@pytest.mark.parametrize("lang,keys", [
    ("sr_RS.UTF-8@latin", ("sr_RS@latin", "sr_RS", "sr@latin", "sr")),
    ("de_AT.UTF-8", ("de_AT", "de")),
    ("de", ("de",)),
    ("C.UTF-8", ()),
    ("POSIX", ()),
    ("", ()),
])
def test_locale_keys_run_most_specific_first(lang, keys):
    assert desktop_files.locales_for({"LANG": lang}) == keys


def test_the_first_locale_variable_set_decides():
    env = {"LC_MESSAGES": "fr_FR.UTF-8", "LANGUAGE": "de:en", "LANG": "en_US.UTF-8"}
    assert desktop_files.locales_for(env) == ("fr_FR", "fr")
    del env["LC_MESSAGES"]
    assert desktop_files.locales_for(env) == ("de",)


def test_picker_is_identical_on_every_tier(env, monkeypatch):
    pytest.importorskip("Xlib")     # the X11 tier's adapters import it
    from test_linux_portal_input import FakePortal

    from cadent.platform import linux

    _, usr, _ = env["dirs"]
    write(usr, "a.desktop", "Type=Application\nName=Alpha\n")
    for key, value in env.items():
        if key != "dirs":
            monkeypatch.setenv(key, value)
    results = []
    for session in ({"XDG_SESSION_TYPE": "x11"},
                    {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
                    {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}):
        plat = linux.create(session, portal=FakePortal(), windows=lambda: None,
                            connect=lambda environ: None)
        assert plat.capabilities.app_picker
        results.append(plat.focused_app.running_apps())
    assert results[0] == results[1] == results[2] == [("Alpha", "a")]
