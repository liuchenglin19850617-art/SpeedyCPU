# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

"""The i18n layer, including the "catalogs must not drift" invariant."""

from __future__ import annotations

import pytest

from speedycpu import i18n


@pytest.fixture(autouse=True)
def _restore_language():
    original = i18n.get_language()
    yield
    i18n.set_language(original)


def test_both_catalogs_cover_exactly_the_same_keys():
    """A key present in one language but not the other is a release blocker."""
    english = set(i18n.CATALOG["en"])
    chinese = set(i18n.CATALOG["zh"])
    assert english - chinese == set(), "missing Chinese translations"
    assert chinese - english == set(), "Chinese keys with no English source"


def test_no_empty_translations():
    for lang, catalog in i18n.CATALOG.items():
        for key, value in catalog.items():
            assert str(value).strip(), f"{lang}.{key} is empty"


def test_format_placeholders_match_across_languages():
    """``{count}`` in English and ``{}`` in Chinese would blow up at runtime."""
    import re

    pattern = re.compile(r"\{(\w+)")
    for key, english in i18n.CATALOG["en"].items():
        chinese = i18n.CATALOG["zh"][key]
        assert set(pattern.findall(english)) == set(pattern.findall(chinese)), key


def test_translation_switches_language():
    i18n.set_language("en")
    assert i18n.t("common.on") == "ON"
    i18n.set_language("zh")
    assert i18n.t("common.on") == "已开启"


def test_interpolation():
    i18n.set_language("en")
    assert i18n.t("on.worker", pid=99) == "Background worker started (PID 99)"
    i18n.set_language("zh")
    assert "99" in i18n.t("on.worker", pid=99)


def test_unknown_key_returns_the_key_itself():
    i18n.set_language("en")
    assert i18n.t("this.key.does.not.exist") == "this.key.does.not.exist"


def test_missing_placeholder_degrades_gracefully():
    """Never raise: a bad format string must not take the CLI down."""
    i18n.set_language("en")
    rendered = i18n.t("on.worker")  # {pid} not supplied
    assert rendered == i18n.CATALOG["en"]["on.worker"]


def test_a_placeholder_called_key_does_not_collide_with_the_first_parameter():
    """Regression: ``t`` used to name its first parameter ``key``.

    ``t("config.no_key", key=name)`` then raised
    ``TypeError: got multiple values for argument 'key'``, turning a friendly
    "unknown config key" message into a traceback.
    """
    i18n.set_language("en")
    assert i18n.t("config.no_key", key="nonsense") == "Unknown config key: nonsense"
    i18n.set_language("zh")
    assert "nonsense" in i18n.t("config.no_key", key="nonsense")
    assert "poll_interval" in i18n.t("config.key_set", key="poll_interval", value=1.5)


def test_set_language_ignores_unknown_values():
    i18n.set_language("zh")
    i18n.set_language("klingon")
    assert i18n.get_language() == "zh"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("zh-CN", "zh"),
        ("zh_CN", "zh"),
        ("ZH", "zh"),
        ("chinese", "zh"),
        ("cn", "zh"),
        # Windows spells the locale out in English.
        ("Chinese (Simplified)_China", "zh"),
        ("Chinese (Traditional)_Taiwan", "zh"),
        ("en-US", "en"),
        ("English", "en"),
        ("English_United States", "en"),
        ("fr", None),
        ("C", None),
        ("", None),
        (None, None),
    ],
)
def test_language_normalisation(raw, expected):
    assert i18n._normalise(raw) == expected


def test_system_language_uses_the_locale_then_the_environment(monkeypatch):
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("Chinese (Simplified)_China", "cp936"))
    assert i18n._system_language() == "zh"

    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (None, None))
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.setenv("LC_MESSAGES", "")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n._system_language() == "zh"

    monkeypatch.setenv("LANG", "C")
    assert i18n._system_language() is None


def test_detect_language_precedence(home, monkeypatch):
    monkeypatch.setenv("SPEEDYCPU_LANG", "zh")
    # CLI flag beats the environment variable...
    assert i18n.detect_language(config_lang="en", cli_lang="en") == "en"
    # ...the environment variable beats the config file...
    assert i18n.detect_language(config_lang="en") == "zh"
    # ...and an unparseable CLI value falls through to the next source.
    assert i18n.detect_language(config_lang="en", cli_lang="??") == "zh"


def test_detect_language_falls_back_to_config(home, monkeypatch):
    monkeypatch.delenv("SPEEDYCPU_LANG", raising=False)
    monkeypatch.setenv("LANG", "C")
    assert i18n.detect_language(config_lang="zh") == "zh"


def test_available_languages():
    assert set(i18n.available_languages()) == set(i18n.CATALOG)
