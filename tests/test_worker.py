# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

"""Rollback plumbing.

This is the safety-critical half of SpeedyCPU: whatever an engine did, ``s-cpu
off`` has to be able to undo it. All three engines write their own rollback
record, and these tests pin the merge logic down.
"""

from __future__ import annotations

import json
import sys

import pytest

from speedycpu import config as cfgmod
from speedycpu.backends import PRIORITY_CLASS_NAMES, PRIORITY_CLASS_VALUES, BoostRecord
from speedycpu.worker import (
    _read_native_state,
    read_engine_leftovers,
    sweep_rollback_files,
    worker_command,
)


def _high() -> int:
    return PRIORITY_CLASS_VALUES["high"]


def _normal() -> int:
    return PRIORITY_CLASS_VALUES["normal"]


# ---------------------------------------------------------------------------
# native-state.tsv
# ---------------------------------------------------------------------------
def test_native_state_parses_tsv(home):
    path = cfgmod.native_state_path()
    path.write_text(
        f"4242\t{_normal()}\tgame.exe\t100:0,101:-2\n",
        encoding="utf-8",
    )
    records = _read_native_state(path)
    assert len(records) == 1
    record = records[0]
    assert record.pid == 4242
    assert record.name == "game.exe"
    assert record.priority_before == "normal"
    assert record.thread_priorities == [[100, 0], [101, -2]]
    assert record.reason == "native-rollback"


def test_native_state_without_thread_column_is_valid(home):
    path = cfgmod.native_state_path()
    path.write_text(f"7\t{_high()}\tapp.exe\n", encoding="utf-8")
    record = _read_native_state(path)[0]
    assert record.thread_priorities == []
    assert record.priority_before == "high"


def test_native_state_skips_junk_lines(home):
    path = cfgmod.native_state_path()
    path.write_text(
        "\n".join(
            [
                "# pid\tpriority\tname",
                "",
                "   ",
                "not-a-pid\t1\tx.exe",
                "9\tnot-a-priority\ty.exe",
                "11",  # too few columns
                f"12\t{_normal()}\tz.exe\tbroken,13:notanint,14:1",
            ]
        ),
        encoding="utf-8",
    )
    records = _read_native_state(path)
    assert [record.pid for record in records] == [12]
    # The two unparseable thread entries are dropped, the good one survives.
    assert records[0].thread_priorities == [[14, 1]]


def test_native_state_unknown_priority_number_becomes_a_string(home):
    path = cfgmod.native_state_path()
    path.write_text("3\t999\todd.exe\n", encoding="utf-8")
    assert _read_native_state(path)[0].priority_before == "999"


def test_native_state_missing_file_is_not_an_error(home):
    assert _read_native_state(cfgmod.native_state_path()) == []


def test_priority_names_cover_what_the_cpp_engine_writes():
    """scpu_watch.cpp writes these six values; each must map to a name."""
    for value in (0x40, 0x4000, 0x20, 0x8000, 0x80, 0x100):
        assert value in PRIORITY_CLASS_NAMES


# ---------------------------------------------------------------------------
# read_engine_leftovers
# ---------------------------------------------------------------------------
def test_leftovers_empty_when_nothing_happened(home):
    assert read_engine_leftovers(None, cfgmod.load_state()) == []


def test_leftovers_from_the_python_state_file(home):
    cfgmod.save_state(
        {
            "boosted": {
                "100": {"pid": 100, "name": "a.exe", "priority_before": "normal", "reason": "foreground"}
            }
        }
    )
    records = read_engine_leftovers("python", cfgmod.load_state())
    assert [record.pid for record in records] == [100]
    assert records[0].name == "a.exe"


def test_leftovers_ignore_malformed_state_entries(home):
    cfgmod.save_state({"boosted": {"x": {"name": "no-pid.exe"}, "200": {"pid": 200, "name": "ok.exe"}}})
    records = read_engine_leftovers("python", cfgmod.load_state())
    assert [record.pid for record in records] == [200]


def test_leftovers_from_the_java_agent_report(home):
    (home / "agent.json").write_text(
        json.dumps({"boosted": [{"pid": 300, "name": "b.exe", "priority_before": "normal"}]}),
        encoding="utf-8",
    )
    assert [record.pid for record in read_engine_leftovers("java", {})] == [300]


def test_corrupt_agent_json_is_ignored(home):
    (home / "agent.json").write_text("{ broken", encoding="utf-8")
    assert read_engine_leftovers("java", {}) == []


def test_leftovers_merge_all_three_sources(home):
    """Whichever engine ran, ``s-cpu off`` must still see everything."""
    cfgmod.save_state({"boosted": {"1": {"pid": 1, "name": "py.exe"}}})
    (home / "agent.json").write_text(json.dumps({"boosted": [{"pid": 2, "name": "java.exe"}]}), encoding="utf-8")
    cfgmod.native_state_path().write_text(f"3\t{_normal()}\tnative.exe\n", encoding="utf-8")

    records = {record.pid: record.name for record in read_engine_leftovers("python", cfgmod.load_state())}
    assert records == {1: "py.exe", 2: "java.exe", 3: "native.exe"}


def test_python_state_wins_over_the_native_file_for_the_same_pid(home):
    """state.json carries the richest record, so it should not be overwritten."""
    cfgmod.save_state(
        {
            "boosted": {
                "5": {
                    "pid": 5,
                    "name": "both.exe",
                    "priority_after": "high",
                    "threads_raised": 9,
                    "thread_priorities": [[50, 0]],
                }
            }
        }
    )
    cfgmod.native_state_path().write_text(f"5\t{_normal()}\tboth.exe\t51:0\n", encoding="utf-8")

    record = read_engine_leftovers("python", cfgmod.load_state())[0]
    assert record.priority_after == "high"
    assert record.threads_raised == 9
    assert record.thread_priorities == [[50, 0]]


# ---------------------------------------------------------------------------
# sweep_rollback_files
# ---------------------------------------------------------------------------
def test_sweep_removes_consumed_rollback_files(home):
    """A consumed rollback file must not survive, or the next run replays it.

    The recorded PID may by then belong to an unrelated process, so a stale file
    can end up modifying something the user never asked to touch.
    """
    (home / "agent.json").write_text(json.dumps({"boosted": [{"pid": 1, "name": "a.exe"}]}), encoding="utf-8")
    cfgmod.native_state_path().write_text(f"2\t{_normal()}\tb.exe\n", encoding="utf-8")

    sweep_rollback_files([])

    assert not (home / "agent.json").exists()
    assert not cfgmod.native_state_path().exists()


def test_sweep_keeps_the_files_when_a_revert_failed(home):
    """Replaying a revert is safe; losing the record is not."""
    (home / "agent.json").write_text("{}", encoding="utf-8")
    cfgmod.native_state_path().write_text(f"2\t{_normal()}\tb.exe\n", encoding="utf-8")
    kept = [BoostRecord(pid=2, name="b.exe", priority_before="normal")]

    sweep_rollback_files(kept)

    assert (home / "agent.json").exists()
    assert cfgmod.native_state_path().exists()


def test_sweep_is_a_no_op_when_there_is_nothing_to_remove(home):
    sweep_rollback_files([])
    assert read_engine_leftovers(None, {}) == []


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
def test_worker_command_shape():
    command = worker_command()
    assert command, "the worker command must not be empty"
    assert command[-1] == "_worker"
    assert "python" in command[0].lower() or command[0].lower().endswith(".exe")


@pytest.mark.skipif(sys.platform != "win32", reason="only meaningful on Windows")
def test_worker_command_uses_module_form_when_not_frozen():
    assert worker_command() == [sys.executable, "-m", "speedycpu", "_worker"]
