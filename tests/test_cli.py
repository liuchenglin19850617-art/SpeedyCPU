# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

"""End-to-end CLI behaviour.

Everything here runs with ``--dry-run`` where a real change would otherwise
happen, and with an isolated ``SPEEDYCPU_HOME``, so running the suite never
alters the machine it runs on.
"""

from __future__ import annotations

import json
import sys

import pytest

from speedycpu import config as cfgmod
from speedycpu.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, build_parser, main


@pytest.fixture(autouse=True)
def _no_ambient_language(monkeypatch):
    monkeypatch.delenv("SPEEDYCPU_LANG", raising=False)
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "C")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def test_parser_exposes_every_documented_command():
    parser = build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert len(actions) == 1
    assert set(actions[0].choices) >= {
        "on",
        "off",
        "status",
        "boost",
        "unboost",
        "targets",
        "doctor",
        "config",
        "version",
    }


def test_the_worker_subcommand_is_hidden():
    """It exists for the detached process, not for humans."""
    parser = build_parser()
    subparsers = next(a for a in parser._actions if a.dest == "command")
    worker = subparsers.choices["_worker"]
    assert worker.description is None
    assert worker.usage == "" or "_worker" not in (worker.usage or "")


def test_no_arguments_prints_help(capsys):
    assert main([]) == EXIT_OK
    assert "s-cpu on" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "SpeedyCPU" in capsys.readouterr().out


def test_bad_usage_exits_two():
    with pytest.raises(SystemExit) as excinfo:
        main(["frobnicate"])
    assert excinfo.value.code == EXIT_USAGE


# ---------------------------------------------------------------------------
# version / config
# ---------------------------------------------------------------------------
def test_version_command_lists_engines(capsys, home):
    assert main(["version", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    for name in ("native", "java", "python"):
        assert name in out


def test_config_path_prints_both_files(capsys, home):
    assert main(["config", "path", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    assert str(cfgmod.config_path()) in out
    assert str(cfgmod.state_path()) in out


def test_config_show_marks_non_default_values(capsys, home):
    cfgmod.set_config_value("mode", "all")
    assert main(["config", "show", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "mode" in out


def test_config_set_and_persist(capsys, home):
    assert main(["config", "set", "poll_interval", "1.5", "--no-color"]) == EXIT_OK
    assert cfgmod.load_config()["poll_interval"] == 1.5
    capsys.readouterr()


def test_config_set_rejects_an_unknown_key(capsys, home):
    assert main(["config", "set", "nonsense", "1", "--no-color"]) == EXIT_FAIL
    assert "nonsense" in capsys.readouterr().out


def test_config_set_rejects_a_bad_value(capsys, home):
    assert main(["config", "set", "poll_interval", "soon", "--no-color"]) == EXIT_FAIL
    capsys.readouterr()


def test_config_reset(capsys, home):
    cfgmod.set_config_value("mode", "all")
    assert main(["config", "reset", "--no-color"]) == EXIT_OK
    assert cfgmod.load_config()["mode"] == cfgmod.DEFAULT_CONFIG["mode"]
    capsys.readouterr()


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------
def test_targets_add_list_remove(capsys, home):
    assert main(["targets", "add", "chrome.exe", "--no-color"]) == EXIT_OK
    assert main(["targets", "add", "game*.exe", "--no-color"]) == EXIT_OK
    assert cfgmod.load_config()["targets"] == ["chrome.exe", "game*.exe"]

    capsys.readouterr()
    assert main(["targets", "list", "--no-color"]) == EXIT_OK
    assert "chrome.exe" in capsys.readouterr().out

    assert main(["targets", "remove", "chrome.exe", "--no-color"]) == EXIT_OK
    assert cfgmod.load_config()["targets"] == ["game*.exe"]
    capsys.readouterr()


def test_targets_add_is_idempotent(capsys, home):
    main(["targets", "add", "chrome.exe", "--no-color"])
    assert main(["targets", "add", "chrome.exe", "--no-color"]) == EXIT_OK
    assert cfgmod.load_config()["targets"] == ["chrome.exe"]
    capsys.readouterr()


def test_targets_remove_missing_pattern_fails(capsys, home):
    assert main(["targets", "remove", "ghost.exe", "--no-color"]) == EXIT_FAIL
    capsys.readouterr()


def test_targets_add_without_a_pattern_is_a_usage_error(capsys, home):
    assert main(["targets", "add", "--no-color"]) == EXIT_USAGE
    capsys.readouterr()


# ---------------------------------------------------------------------------
# on / off
# ---------------------------------------------------------------------------
def test_on_dry_run_changes_nothing(capsys, home):
    before = cfgmod.load_state()
    assert main(["on", "--dry-run", "--no-color"]) == EXIT_OK
    assert cfgmod.load_state() == before
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_on_dry_run_records_no_worker_pid(capsys, home):
    main(["on", "--dry-run", "--no-color"])
    assert cfgmod.load_state()["worker_pid"] is None
    capsys.readouterr()


def test_on_dry_run_honours_flags_without_spawning(capsys, home):
    assert main(["on", "--dry-run", "--targets", "a.exe,b.exe", "--mode", "all", "--no-color"]) == EXIT_OK
    config = cfgmod.load_config()
    assert config["targets"] == ["a.exe", "b.exe"]
    assert config["mode"] == "all"
    capsys.readouterr()


def test_on_no_cpu_tuning_flag_is_persisted(capsys, home):
    main(["on", "--dry-run", "--no-cpu-tuning", "--no-color"])
    assert cfgmod.load_config()["cpu_tuning"] is False
    capsys.readouterr()


def test_on_no_power_plan_flag_is_persisted(capsys, home):
    main(["on", "--dry-run", "--no-power-plan", "--no-color"])
    assert cfgmod.load_config()["power_plan"] == "none"
    capsys.readouterr()


def test_on_engine_flag_forces_the_python_engine(capsys, home):
    assert main(["on", "--dry-run", "--engine", "python", "--no-color"]) == EXIT_OK
    assert "python" in capsys.readouterr().out


def test_on_falls_back_when_the_native_engine_is_missing(capsys, home, no_engines):
    assert main(["on", "--dry-run", "--engine", "native", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "python" in out


def test_off_dry_run_is_safe_when_nothing_is_running(capsys, home):
    assert main(["off", "--dry-run", "--no-color"]) == EXIT_OK
    capsys.readouterr()


def test_off_dry_run_reports_leftovers_without_reverting(capsys, home):
    cfgmod.save_state(
        {"enabled": True, "boosted": {"4242": {"pid": 4242, "name": "ghost.exe", "priority_before": "normal"}}}
    )
    assert main(["off", "--dry-run", "--no-color"]) == EXIT_OK
    # The record must survive a dry run, otherwise the real rollback is lost.
    assert "4242" in cfgmod.load_state()["boosted"]
    capsys.readouterr()


# ---------------------------------------------------------------------------
# status / doctor
# ---------------------------------------------------------------------------
def test_status_json_has_the_documented_keys(capsys, home):
    assert main(["status", "--json", "--no-color"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    for key in (
        "enabled",
        "engine",
        "engine_available",
        "engine_preferred",
        "worker_pid",
        "worker_alive",
        "mode",
        "targets",
        "timer_resolution_ms",
        "elevated",
        "boosted",
        "config_path",
        "state_path",
    ):
        assert key in payload, key
    assert payload["enabled"] is False
    assert "python" in payload["engine_available"]


def test_status_json_reports_a_disabled_session_even_with_a_stale_pid(capsys, home):
    cfgmod.save_state({"enabled": True, "worker_pid": 999_999_999})
    assert main(["status", "--json", "--no-color"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False
    assert payload["worker_alive"] is False


def test_status_lists_boosted_processes(capsys, home):
    cfgmod.save_state(
        {
            "boosted": {
                "10": {
                    "pid": 10,
                    "name": "a.exe",
                    "threads_raised": 3,
                    "threads_total": 10,
                    "priority_after": "high",
                }
            }
        }
    )
    assert main(["status", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "a.exe" in out
    assert "3/10" in out


def test_doctor_json_reports_engines(capsys, home):
    assert main(["doctor", "--json", "--no-color"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert {"platform", "backend", "python", "engines", "problems"} <= set(payload)
    names = {item["name"] for item in payload["engines"]}
    assert names == {"native", "java", "python"}


def test_doctor_strict_exit_code_matches_the_problem_list(capsys, home):
    code = main(["doctor", "--json", "--strict", "--no-color"])
    payload = json.loads(capsys.readouterr().out)
    assert code == (EXIT_FAIL if payload["problems"] else EXIT_OK)


# ---------------------------------------------------------------------------
# boost / unboost
# ---------------------------------------------------------------------------
def test_boost_fails_when_nothing_matches(capsys, home):
    assert main(["boost", "definitely-not-running-xyz.exe", "--no-color"]) == EXIT_FAIL
    capsys.readouterr()


def test_unboost_with_nothing_recorded_reports_nothing(capsys, home):
    assert main(["unboost", "--no-color"]) == EXIT_FAIL
    capsys.readouterr()


def test_unboost_dry_run_keeps_the_records(capsys, home):
    cfgmod.save_state({"boosted": {"11": {"pid": 11, "name": "b.exe", "priority_before": "normal"}}})
    assert main(["unboost", "--dry-run", "--no-color"]) == EXIT_OK
    assert "11" in cfgmod.load_state()["boosted"]
    capsys.readouterr()


def test_unboost_removes_records_that_were_reverted(capsys, home, monkeypatch):
    """``unboost`` must not keep claiming a process it no longer controls."""
    cfgmod.save_state({"boosted": {"11": {"pid": 11, "name": "b.exe", "priority_before": "normal"}}})

    class RevertingBackend:
        name = "fake"

        def unboost(self, record):
            return True

    monkeypatch.setattr("speedycpu.cli.get_backend", lambda *a, **k: RevertingBackend())
    assert main(["unboost", "--no-color"]) == EXIT_OK
    assert cfgmod.load_state()["boosted"] == {}
    capsys.readouterr()


def test_unboost_keeps_records_it_could_not_revert(capsys, home, monkeypatch):
    """A process we failed to revert must stay on the books for a later attempt."""
    cfgmod.save_state({"boosted": {"11": {"pid": 11, "name": "b.exe", "priority_before": "normal"}}})

    class FailingBackend:
        name = "fake"

        def unboost(self, record):
            return False

    monkeypatch.setattr("speedycpu.cli.get_backend", lambda *a, **k: FailingBackend())
    assert main(["unboost", "--no-color"]) == EXIT_FAIL
    assert "11" in cfgmod.load_state()["boosted"]
    capsys.readouterr()


# ---------------------------------------------------------------------------
# language switching
# ---------------------------------------------------------------------------
def test_lang_flag_switches_the_ui(capsys, home):
    assert main(["--lang", "zh", "version"]) == EXIT_OK
    assert "内置" in capsys.readouterr().out


def test_unknown_lang_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        main(["--lang", "klingon", "version"])


@pytest.mark.skipif(sys.platform != "win32", reason="needs a real backend")
def test_status_without_json_does_not_crash(capsys, home):
    assert main(["status", "--no-color"]) == EXIT_OK
    assert "SpeedyCPU" in capsys.readouterr().out
