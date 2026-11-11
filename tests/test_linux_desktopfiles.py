"""Installed-apps lookup from XDG data dirs (spec §5.2, ADR 0009): pure files,
so it runs on every tier with no bus."""


import pytest

from cadent.platform.linux import desktopfiles


def write(root, rel, body):
    path = root / "applications" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\n" + body, encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    home, usr, flat = tmp_path / "home", tmp_path / "usr", tmp_path / "flat"
    return {"XDG_DATA_HOME": str(home), "XDG_DATA_DIRS": str(usr),
            "LANG": "de_DE.UTF-8", "dirs": (home, usr, flat)}


def test_lists_only_visible_applications(env):
    home, usr, _ = env["dirs"]
    write(usr, "org.mozilla.firefox.desktop", "Type=Application\nName=Firefox\n")
    write(usr, "hidden.desktop", "Type=Application\nName=H\nHidden=true\n")
    write(usr, "nodisplay.desktop", "Type=Application\nName=N\nNoDisplay=true\n")
    write(usr, "link.desktop", "Type=Link\nName=L\n")
    apps = desktopfiles.installed_apps(env)
    assert [(a.name, a.id) for a in apps] == [("Firefox", "org.mozilla.firefox")]


def test_earlier_directory_wins_and_dedupes_by_id(env):
    home, usr, _ = env["dirs"]
    write(home, "a.desktop", "Type=Application\nName=Mine\n")
    write(usr, "a.desktop", "Type=Application\nName=System\n")
    assert [(a.name, a.id) for a in desktopfiles.installed_apps(env)] == [("Mine", "a")]


def test_flatpak_exports_are_searched(env, tmp_path, monkeypatch):
    flat = tmp_path / "flatpak"
    write(flat, "org.gnome.Calculator.desktop", "Type=Application\nName=Calc\n")
    monkeypatch.setattr(desktopfiles, "FLATPAK_EXPORTS", (str(flat),))
    assert [a.id for a in desktopfiles.installed_apps(env)] == ["org.gnome.Calculator"]


def test_display_name_is_localized_and_works_for_closed_apps(env):
    _, usr, _ = env["dirs"]
    write(usr, "org.mozilla.firefox.desktop",
          "Type=Application\nName=Firefox\nName[de]=Feuerfuchs\n")
    assert desktopfiles.display_name("org.mozilla.firefox", env) == "Feuerfuchs"
    assert desktopfiles.display_name("ORG.Mozilla.Firefox", env) == "Feuerfuchs"
    assert desktopfiles.display_name("com.gone.app", env) is None
    env["LANG"] = "C"
    assert desktopfiles.display_name("org.mozilla.firefox", env) == "Firefox"


def test_picker_is_identical_on_every_tier(env, monkeypatch):
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
        plat = linux.create(session, portal=object())
        assert plat.capabilities.app_picker
        results.append(plat.focused_app.running_apps())
    assert results[0] == results[1] == results[2] == [("Alpha", "a")]
