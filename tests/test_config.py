# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

"""Configuration and state persistence."""

from __future__ import annotations

import json

import pytest

from speedycpu import config as cfgmod


def test_first_load_creates_the_file(home):
    cfg = cfgmod.load_config()
    assert cfg == cfgmod.DEFAULT_CONFIG
    assert cfgmod.config_path().exists()


def test_paths_honour_speedycpu_home(home):
    assert cfgmod.home_dir() == home
    assert cfgmod.config_path().parent == home
    assert cfgmod.state_path().parent == home
    assert cfgmod.native_state_path().parent == home


def test_unknown_keys_are_dropped_on_load(home):
    cfgmod.config_path().write_text(
        json.dumps({**cfgmod.DEFAULT_CONFIG, "definitely_not_a_key": 1}), encoding="utf-8"
    )
    cfg = cfgmod.load_config()
    assert "definitely_not_a_key" not in cfg
    assert cfg["mode"] == cfgmod.DEFAULT_CONFIG["mode"]


def test_partial_file_is_merged_with_defaults(home):
    cfgmod.config_path().write_text(json.dumps({"mode": "all"}), encoding="utf-8")
    cfg = cfgmod.load_config()
    assert cfg["mode"] == "all"
    assert cfg["priority"] == cfgmod.DEFAULT_CONFIG["priority"]


def test_save_is_atomic_and_leaves_no_temp_file(home):
    cfgmod.save_config({"mode": "all"})
    leftovers = list(home.glob("*.tmp"))
    assert leftovers == []
    assert cfgmod.load_config()["mode"] == "all"


def test_corrupt_json_does_not_raise(home):
    cfgmod.config_path().write_text("{ not json at all", encoding="utf-8")
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("YES", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("NO", False),
        ("off", False),
        ("0", False),
        ("", False),
    ],
)
def test_coerce_bool(value, expected):
    assert cfgmod.coerce_value("aggressive", value) is expected


def test_coerce_non_bool_string_raises():
    with pytest.raises(ValueError):
        cfgmod.coerce_value("aggressive", "maybe")


def test_coerce_collections_and_numbers():
    assert cfgmod.coerce_value("targets", "chrome.exe, game*.exe ,") == ["chrome.exe", "game*.exe"]
    assert cfgmod.coerce_value("poll_interval", "1.5") == 1.5
    assert cfgmod.coerce_value("timer_resolution", "0") == 0
    assert cfgmod.coerce_value("engine", "native") == "native"


def test_coerce_lang_accepts_auto():
    assert cfgmod.coerce_value("lang", "auto") is None
    assert cfgmod.coerce_value("lang", "zh") == "zh"


def test_coerce_rejects_garbage_number():
    with pytest.raises(ValueError):
        cfgmod.coerce_value("poll_interval", "soon")


def test_set_config_value_round_trips(home):
    cfgmod.set_config_value("targets", "a.exe,b.exe")
    assert cfgmod.load_config()["targets"] == ["a.exe", "b.exe"]


def test_set_unknown_key_raises_keyerror(home):
    with pytest.raises(KeyError):
        cfgmod.set_config_value("nope", "1")


def test_reset_config_restores_defaults(home):
    cfgmod.set_config_value("mode", "all")
    assert cfgmod.reset_config() == cfgmod.DEFAULT_CONFIG
    assert cfgmod.load_config()["mode"] == cfgmod.DEFAULT_CONFIG["mode"]


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
def test_default_state_shape():
    for key in ("enabled", "engine", "boosted", "stats", "system_applied"):
        assert key in cfgmod.DEFAULT_STATE


def test_state_round_trip(home):
    cfgmod.save_state({"enabled": True, "worker_pid": 4321, "engine": "native"})
    state = cfgmod.load_state()
    assert state["enabled"] is True
    assert state["worker_pid"] == 4321
    assert state["engine"] == "native"


def test_state_merges_nested_stats_with_defaults(home):
    cfgmod.save_state({"stats": {"processes_boosted": 7}})
    stats = cfgmod.load_state()["stats"]
    # The keys the caller did not set must survive, or the UI loses fields.
    assert stats["processes_boosted"] == 7
    assert "threads_raised" in stats
    assert "last_scan" in stats


def test_reset_state(home):
    cfgmod.save_state({"enabled": True})
    cfgmod.reset_state()
    assert cfgmod.load_state()["enabled"] is False


def test_corrupt_state_falls_back_to_defaults(home):
    cfgmod.state_path().write_text("]]]", encoding="utf-8")
    assert cfgmod.load_state() == cfgmod.DEFAULT_STATE


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
def test_human_duration():
    assert cfgmod.human_duration(9) == "9s"
    assert cfgmod.human_duration(75) == "1m 15s"
    assert cfgmod.human_duration(3 * 3600 + 65) == "3h 01m 05s"
    assert cfgmod.human_duration(-5) == "0s"


def test_timestamp_is_iso_like():
    stamp = cfgmod.utc_timestamp()
    assert len(stamp) == 19 and stamp[4] == "-" and stamp[10] == "T"


def test_exclusion_list_is_sane():
    assert len(cfgmod.DEFAULT_EXCLUDE) == len(set(cfgmod.DEFAULT_EXCLUDE))
    assert all(item == item.lower() for item in cfgmod.DEFAULT_EXCLUDE)
    # Touching these would destabilise the machine, so they must never come off.
    for critical in ("csrss.exe", "lsass.exe", "winlogon.exe", "services.exe"):
        assert critical in cfgmod.NEVER_TOUCH
        assert critical in cfgmod.DEFAULT_EXCLUDE
