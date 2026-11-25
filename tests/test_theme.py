"""Follow-the-desktop theming.

The bug these pin down: "Follow desktop" came up light on a dark desktop,
because the only evidence used was the QPalette Qt handed us at startup —
which is light whenever the settings portal did not answer in time (Linux) or
whenever the Fusion style is in use (Windows, always).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QPalette                        # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from rifftag.gui import theme                                   # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def captured(monkeypatch):
    """Pretend Qt handed us a palette of a given polarity."""
    def _set(dark: bool):
        monkeypatch.setattr(theme, "_system_palette",
                            theme._build(theme._DARK if dark
                                         else theme._LIGHT))
    return _set


@pytest.fixture
def desktop(monkeypatch):
    """Pretend the desktop said dark / light / nothing at all."""
    def _set(answer):
        monkeypatch.setattr(theme, "_forced_preference", answer)
    return _set


def test_dark_desktop_with_a_light_startup_palette_comes_up_dark(
        qapp, captured, desktop):
    """The actual bug. Qt said light, the desktop says dark; dark wins."""
    captured(False)
    desktop(True)
    assert theme.is_dark(theme.palette_for(theme.Theme.SYSTEM))


def test_light_desktop_with_a_dark_startup_palette_comes_up_light(
        qapp, captured, desktop):
    captured(True)
    desktop(False)
    assert not theme.is_dark(theme.palette_for(theme.Theme.SYSTEM))


def test_an_agreeing_palette_is_kept_as_it_is(qapp, monkeypatch, desktop):
    """The desktop's own colours survive when the polarity already matches.

    This is why the fix substitutes rather than always overrides: a native
    palette carries the user's accent colour, and replacing a perfectly good
    dark palette with our generic one would be a visible regression.
    """
    native = theme._build(theme._DARK)
    native.setColor(QPalette.ColorRole.Highlight, QColor("#ff00ff"))
    monkeypatch.setattr(theme, "_system_palette", native)
    desktop(True)
    result = theme.palette_for(theme.Theme.SYSTEM)
    assert result.color(QPalette.ColorRole.Highlight).name() == "#ff00ff"


def test_an_unknown_desktop_falls_back_to_the_startup_palette(
        qapp, captured, desktop):
    captured(True)
    desktop(None)
    assert theme.is_dark(theme.palette_for(theme.Theme.SYSTEM))


def test_explicit_themes_ignore_the_desktop(qapp, captured, desktop):
    captured(False)
    desktop(False)
    assert theme.is_dark(theme.palette_for(theme.Theme.DARK))
    desktop(True)
    assert not theme.is_dark(theme.palette_for(theme.Theme.LIGHT))


def test_apply_system_puts_the_corrected_palette_on_the_app(
        qapp, captured, desktop):
    captured(False)
    desktop(True)
    try:
        theme.apply(theme.Theme.SYSTEM, qapp)
        assert theme.is_dark(qapp.palette())
    finally:
        qapp.setPalette(QPalette())


# --- the individual sources ------------------------------------------

def test_portal_reply_values_map_to_a_preference():
    """1 = prefer dark, 2 = prefer light, 0 = the desktop has no opinion."""
    assert theme._unwrap_scheme(1) is True
    assert theme._unwrap_scheme(2) is False
    assert theme._unwrap_scheme(0) is None
    assert theme._unwrap_scheme(None) is None


def test_portal_reply_is_unwrapped_through_its_variants():
    class Variant:
        def __init__(self, inner):
            self.value = inner

    assert theme._unwrap_scheme(Variant(Variant(1))) is True


def test_gtk_colour_scheme_is_believed_when_it_says_anything(monkeypatch):
    answers = {("org.gnome.desktop.interface", "color-scheme"): "prefer-dark"}
    monkeypatch.setattr(theme, "_gsettings",
                        lambda s, k: answers.get((s, k)))
    assert theme._gtk_prefers_dark() is True

    answers[("org.gnome.desktop.interface", "color-scheme")] = "prefer-light"
    assert theme._gtk_prefers_dark() is False


def test_a_dark_theme_name_counts_when_colour_scheme_says_default(monkeypatch):
    """Mint leaves color-scheme at "default" while running a dark theme.

    Dave's is Mint-L-Darker-Aqua, which is why this is a substring match and
    not a check for a "-dark" suffix.
    """
    answers = {
        ("org.gnome.desktop.interface", "color-scheme"): "default",
        ("org.gnome.desktop.interface", "gtk-theme"): "Mint-L-Darker-Aqua",
    }
    monkeypatch.setattr(theme, "_gsettings",
                        lambda s, k: answers.get((s, k)))
    monkeypatch.delenv("GTK_THEME", raising=False)
    assert theme._gtk_prefers_dark() is True

    answers[("org.gnome.desktop.interface", "gtk-theme")] = "Mint-Y-Aqua"
    assert theme._gtk_prefers_dark() is False


def test_nothing_to_ask_means_no_opinion(monkeypatch):
    monkeypatch.setattr(theme, "_gsettings", lambda s, k: None)
    monkeypatch.delenv("GTK_THEME", raising=False)
    assert theme._gtk_prefers_dark() is None


def test_windows_registry_flag_is_inverted(monkeypatch):
    """AppsUseLightTheme is 0 for dark — the name reads backwards."""
    import sys
    import types

    calls = {}

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda root, path: FakeKey(),
        QueryValueEx=lambda key, name: (calls["value"], 4),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)

    calls["value"] = 0
    assert theme._windows_prefers_dark() is True
    calls["value"] = 1
    assert theme._windows_prefers_dark() is False


def test_a_missing_registry_value_is_not_an_error(monkeypatch):
    import sys
    import types

    def boom(*a, **k):
        raise OSError("no such key")

    monkeypatch.setitem(sys.modules, "winreg",
                        types.SimpleNamespace(HKEY_CURRENT_USER=object(),
                                              OpenKey=boom,
                                              QueryValueEx=boom))
    assert theme._windows_prefers_dark() is None
