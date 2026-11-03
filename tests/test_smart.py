# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors
#
# Tests for the smart mode: active / launch / idle process tiering.

from __future__ import annotations

import sys

import pytest

from speedycpu import config as cfgmod
from speedycpu.backends import Backend, BoostRecord, ProcessInfo


# ---------------------------------------------------------------------------
# Fake backend (no system access) - same shape as tests/test_backends.py
# ---------------------------------------------------------------------------
class FakeBackend(Backend):
    name = "fake"

    def __init__(self, config, foreground=0, processes=()):
        super().__init__(config)
        self._foreground = foreground
        self._processes = list(processes)

    @staticmethod
    def is_elevated() -> bool:
        return False

    def probe(self):  # pragma: no cover - unused here
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

    def throttle(self, process, manual=False):
        return None

    def reapply_throttle(self, record):
        return False


def make_backend(foreground=0, targets=None, **overrides):
    config = dict(cfgmod.DEFAULT_CONFIG)
    config.update(overrides)
    if targets is not None:
        config["targets"] = targets
    return FakeBackend(config, foreground=foreground)


# ---------------------------------------------------------------------------
# config defaults
# ---------------------------------------------------------------------------
def test_smart_config_defaults():
    for key in ("idle_priority", "idle_memory_priority", "keep_active_ram", "launch_boost_seconds"):
        assert key in cfgmod.DEFAULT_CONFIG
    assert cfgmod.DEFAULT_CONFIG["idle_priority"] == "below_normal"
    assert cfgmod.DEFAULT_CONFIG["idle_memory_priority"] is True
    assert cfgmod.DEFAULT_CONFIG["keep_active_ram"] is True
    assert cfgmod.DEFAULT_CONFIG["launch_boost_seconds"] >= 0


def test_coerce_smart_keys():
    assert cfgmod.coerce_value("idle_memory_priority", "true") is True
    assert cfgmod.coerce_value("keep_active_ram", "no") is False
    assert cfgmod.coerce_value("launch_boost_seconds", "5") == 5.0
    assert cfgmod.coerce_value("idle_priority", "idle") == "idle"


# ---------------------------------------------------------------------------
# match_reason in smart mode
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pid,name,foreground,targets,expected",
    [
        (42, "app.exe", 42, [], "foreground"),   # focused window
        (7, "other.exe", 42, [], "idle"),        # not focused, not targeted
        (8, "game.exe", 42, ["game.exe"], "pattern"),  # matched target
        (9, "dwm.exe", 42, [], None),            # excluded
        (1, "csrss.exe", 42, ["*"], None),       # protected, never touched
        (7, "", 42, [], None),                   # unnamed
    ],
)
def test_smart_match_reason(pid, name, foreground, targets, expected):
    backend = make_backend(mode="smart", foreground=foreground, targets=targets)
    assert backend.match_reason(ProcessInfo(pid, name, "")) == expected


def test_describe_scope_smart():
    assert make_backend(mode="smart").describe_scope() == "smart"
    assert make_backend(mode="smart", targets=["a.exe"]).describe_scope() == "smart + a.exe"


# ---------------------------------------------------------------------------
# BoostRecord carries the new idle / memory-priority fields
# ---------------------------------------------------------------------------
def test_boost_record_round_trip_includes_idle_fields():
    original = BoostRecord(
        pid=1234,
        name="bg.exe",
        priority_before="normal",
        priority_after="below_normal",
        threads_raised=2,
        threads_total=8,
        thread_priorities=[[10, 0]],
        idle_throttled=True,
        memory_priority_lowered=True,
        reason="idle",
    )
    restored = BoostRecord.from_dict(original.to_dict())
    assert restored == original


# ---------------------------------------------------------------------------
# Worker desired-state machine (needs the real backend only at construction)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="Worker constructs a backend")
def test_worker_desired_tiers_processes():
    from speedycpu.console import Console
    from speedycpu.worker import Worker

    config = dict(cfgmod.DEFAULT_CONFIG)
    config["mode"] = "smart"
    worker = Worker(config=config, console=Console(quiet=True))
    worker.backend = make_backend(mode="smart", foreground=42)
    worker._baseline = {100}  # pid 100 was already running at worker start
    now = 1_000_000.0

    # A brand new process (not in the baseline) is granted a launch window.
    state, launch = worker._desired(ProcessInfo(200, "launched.exe", ""), now)
    assert state == "active"
    assert launch is True
    assert 200 in worker._launch_until

    # While the launch window is open it stays active.
    state, launch = worker._desired(ProcessInfo(200, "launched.exe", ""), now + 1.0)
    assert state == "active"
    assert launch is True

    # After the launch window it becomes idle.
    state, launch = worker._desired(ProcessInfo(200, "launched.exe", ""), now + 10_000.0)
    assert state == "idle"
    assert launch is False

    # A baseline process that is not focused / targeted is idle immediately.
    state, launch = worker._desired(ProcessInfo(100, "bg.exe", ""), now)
    assert state == "idle"
    assert launch is False

    # The focused window is active.
    state, launch = worker._desired(ProcessInfo(42, "app.exe", ""), now)
    assert state == "active"
    assert launch is False


# ---------------------------------------------------------------------------
# Windows-only: throttle records and restores memory priority in dry-run
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows backend")
def test_windows_throttle_dry_run_records_idle():
    from speedycpu.backends import get_backend

    backend = get_backend(dict(cfgmod.DEFAULT_CONFIG), dry_run=True)
    record = backend.throttle(ProcessInfo(__import__("os").getpid(), "python.exe", ""))
    assert record is not None
    assert record.idle_throttled is True
    assert record.memory_priority_lowered is True
    assert record.priority_after in ("below_normal", "idle")
    # dry-run: unboost must be a no-op success, not touch the system
    assert backend.unboost(record) is True
