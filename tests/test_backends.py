# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

"""Matching rules, record serialisation and backend selection.

The matching logic is the part of SpeedyCPU with the highest blast radius: get
it wrong and the tool boosts something it must never touch. These tests pin the
rules down explicitly.
"""

from __future__ import annotations

import sys

import pytest

from speedycpu import config as cfgmod
from speedycpu.backends import (
    PRIORITY_CLASS_NAMES,
    PRIORITY_CLASS_VALUES,
    Backend,
    BoostRecord,
    ProcessInfo,
    SystemChange,
    backend_kind,
    get_backend,
    is_protected,
    matches_any,
)


# ---------------------------------------------------------------------------
# matches_any
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,patterns,expected",
    [
        ("chrome.exe", ["chrome.exe"], True),
        ("chrome.exe", ["CHROME.EXE"], True),
        ("chrome.exe", ["chrome"], True),  # .exe suffix is optional
        ("chrome.exe", ["*chrome*"], True),
        ("chrome.exe", ["firefox.exe"], False),
        ("chrome.exe", ["*"], True),
        ("chrome.exe", [], False),
        ("chrome.exe", ["", "   "], False),
        ("chrome.exe", ["  chrome.exe  "], True),
        ("msedge.exe", ["edge*"], False),  # anchored globs, not substrings
        ("msedge.exe", ["*edge*"], True),
        ("game-launcher.exe", ["game-*.exe"], True),
        ("", ["*"], False),
        ("explorer.exe", [None], False),
    ],
)
def test_matches_any(name, patterns, expected):
    assert matches_any(name, patterns) is expected


def test_is_protected():
    assert is_protected("csrss.exe")
    assert is_protected("LSASS.EXE")
    assert is_protected("System")
    assert not is_protected("chrome.exe")
    assert not is_protected("")


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------
def test_boost_record_round_trip():
    original = BoostRecord(
        pid=1234,
        name="game.exe",
        exe=r"C:\games\game.exe",
        priority_before="normal",
        priority_after="high",
        threads_raised=3,
        threads_total=40,
        thread_priorities=[[100, 0], [101, -2]],
        ecoqos_disabled=True,
        affinity_before=0b1111,
        affinity_cores_added=4,
        hard_working_set=True,
        reason="foreground",
    )
    restored = BoostRecord.from_dict(original.to_dict())
    assert restored == original


def test_boost_record_from_dict_ignores_unknown_keys():
    record = BoostRecord.from_dict({"pid": 7, "name": "x.exe", "brand_new_field": "?"})
    assert record.pid == 7
    assert record.name == "x.exe"


def test_boost_record_requires_pid_and_name():
    with pytest.raises((TypeError, KeyError, ValueError)):
        BoostRecord.from_dict({"pid": 7})


def test_system_change_round_trip():
    change = SystemChange(
        key="cpu:procthrottlemin",
        description="minimum processor state 0% -> 100%",
        applied=True,
        previous="0",
        current="100",
    )
    assert SystemChange.from_dict(change.to_dict()) == change


def test_system_change_from_dict_ignores_unknown_keys():
    change = SystemChange.from_dict({"key": "power_plan", "description": "x", "future": 1})
    assert change.key == "power_plan"
    assert change.applied is False


def test_priority_tables_are_consistent():
    assert set(PRIORITY_CLASS_NAMES.values()) == set(PRIORITY_CLASS_VALUES)
    for value, name in PRIORITY_CLASS_NAMES.items():
        assert PRIORITY_CLASS_VALUES[name] == value


@pytest.mark.skipif(sys.platform != "win32", reason="winapi is Windows-only")
def test_winapi_priority_table_matches_the_shared_one():
    """The C++ engine writes numbers; the parser reads names. They must agree."""
    from speedycpu.backends import winapi

    assert winapi.PRIORITY_CLASS_NAMES == PRIORITY_CLASS_NAMES
    assert winapi.PRIORITY_CLASS_VALUES == PRIORITY_CLASS_VALUES


# ---------------------------------------------------------------------------
# match_reason
# ---------------------------------------------------------------------------
class FakeBackend(Backend):
    """Backend with a scripted process list; touches no system state."""

    name = "fake"

    def __init__(self, config, foreground=0, processes=()):
        super().__init__(config)
        self._foreground = foreground
        self._processes = list(processes)

    @staticmethod
    def is_elevated() -> bool:
        return False

    def probe(self):  # pragma: no cover - not exercised here
        raise NotImplementedError

    def list_processes(self):
        return list(self._processes)

    def foreground_pid(self):
        return self._foreground

    def apply_system(self):
        return []

    def revert_system(self, changes):
        return []

    def hold_timer_resolution(self, milliseconds):
        return False

    def release_timer_resolution(self):
        return False

    def boost(self, process, manual=False):
        return None

    def unboost(self, record):
        return False


def make_backend(foreground: int = 0, processes=(), **overrides):
    """Build a fake backend; keyword args other than the first two are config."""
    config = dict(cfgmod.DEFAULT_CONFIG)
    config.update(overrides)
    return FakeBackend(config, foreground=foreground, processes=processes)


def test_protected_processes_are_never_matched():
    backend = make_backend(targets=["*"], mode="all")
    assert backend.match_reason(ProcessInfo(1, "csrss.exe", "")) is None
    assert backend.match_reason(ProcessInfo(2, "lsass.exe", "")) is None
    assert backend.match_reason(ProcessInfo(3, "svchost.exe", "")) is None


def test_exclusions_win_over_an_explicit_target():
    """An exclusion is a deliberate safety net, so it must beat targets."""
    backend = make_backend(targets=["dwm.exe"], mode="foreground")
    assert backend.match_reason(ProcessInfo(10, "dwm.exe", "")) is None


def test_explicit_target_matches_without_focus():
    backend = make_backend(targets=["game.exe"], foreground=999)
    assert backend.match_reason(ProcessInfo(5, "game.exe", "")) == "pattern"


def test_foreground_mode_only_matches_the_focused_pid():
    backend = make_backend(mode="foreground", foreground=42)
    assert backend.match_reason(ProcessInfo(42, "app.exe", "")) == "foreground"
    assert backend.match_reason(ProcessInfo(7, "other.exe", "")) is None


def test_all_mode_matches_everything_not_excluded():
    backend = make_backend(mode="all", targets=["*"])
    assert backend.match_reason(ProcessInfo(7, "other.exe", "")) == "all"


def test_all_mode_without_wildcard_and_without_targets_matches_everything():
    backend = make_backend(mode="all", targets=[])
    assert backend.match_reason(ProcessInfo(7, "other.exe", "")) == "all"


def test_all_mode_with_a_restrictive_target_list_matches_only_those():
    backend = make_backend(mode="all", targets=["game.exe"])
    assert backend.match_reason(ProcessInfo(7, "other.exe", "")) is None
    assert backend.match_reason(ProcessInfo(8, "game.exe", "")) == "pattern"


def test_unnamed_process_is_skipped():
    assert make_backend(targets=["*"], mode="all").match_reason(ProcessInfo(4, "", "")) is None


def test_describe_scope():
    assert make_backend(mode="foreground").describe_scope() == "foreground window"
    assert make_backend(mode="foreground", targets=["a.exe"]).describe_scope() == "foreground window + a.exe"
    assert make_backend(mode="all", targets=["*"]).describe_scope() == "all user processes"
    assert make_backend(mode="all", targets=["a.exe"]).describe_scope() == "a.exe"


# ---------------------------------------------------------------------------
# backend registry
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "platform,kind",
    [("win32", "windows"), ("cygwin", "windows"), ("linux", "linux"), ("darwin", "macos"), ("aix", None)],
)
def test_backend_kind(platform, kind):
    assert backend_kind(platform) == kind


def test_unknown_platform_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        get_backend({}, kind="plan9")


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows backend")
def test_windows_backend_is_constructible():
    backend = get_backend(dict(cfgmod.DEFAULT_CONFIG), dry_run=True)
    assert backend.name == "windows"
    assert isinstance(backend.list_processes(), list)
