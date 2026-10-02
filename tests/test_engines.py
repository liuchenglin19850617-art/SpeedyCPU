# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

"""Engine discovery, selection and launch-argument construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from speedycpu import config as cfgmod
from speedycpu import engines


def _info(report, name):
    return next(item for item in report if item.name == name)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def test_report_always_has_all_three_engines():
    report = engines.engine_report()
    assert [item.name for item in report] == list(engines.ENGINE_ORDER)


def test_python_engine_is_always_available(no_engines):
    report = no_engines.engine_report()
    python = _info(report, "python")
    assert python.available is True
    assert python.note_key == "engine.python.ready"


def test_missing_binaries_are_reported_with_a_reason(no_engines):
    report = no_engines.engine_report()
    assert _info(report, "native").available is False
    assert _info(report, "native").note_key == "engine.native.missing"
    assert _info(report, "java").available is False
    assert _info(report, "java").note_key == "engine.java.no_java"


def test_a_found_jar_without_java_reports_missing_java(no_engines, monkeypatch, tmp_path):
    jar = tmp_path / "scpu-agent.jar"
    jar.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(no_engines, "find_java_agent", lambda: jar)
    monkeypatch.setattr(no_engines, "java_executable", lambda: None)
    info = _info(no_engines.engine_report(), "java")
    assert info.available is False
    assert info.note_key == "engine.java.no_java"


def test_a_found_java_without_jar_reports_missing_jar(no_engines, monkeypatch, tmp_path):
    java = tmp_path / "java.exe"
    java.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_java_agent", lambda: None)
    monkeypatch.setattr(no_engines, "java_executable", lambda: java)
    info = _info(no_engines.engine_report(), "java")
    assert info.available is False
    assert info.note_key == "engine.java.no_jar"


def test_an_old_jdk_is_reported_as_unavailable(no_engines, monkeypatch, tmp_path):
    """The FFM API the agent relies on only became final in JDK 22."""
    java = tmp_path / "java.exe"
    java.write_bytes(b"")
    jar = tmp_path / "scpu-agent.jar"
    jar.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_java_agent", lambda: jar)
    monkeypatch.setattr(no_engines, "java_executable", lambda: java)
    monkeypatch.setattr(no_engines, "java_version", lambda _java: "17.0.9")
    info = _info(no_engines.engine_report(), "java")
    assert info.available is False
    assert info.note_key == "engine.java.old_jdk"
    assert info.note_args == {"min": engines.JAVA_MIN_MAJOR, "version": "17.0.9"}


def test_a_new_enough_jdk_is_available(no_engines, monkeypatch, tmp_path):
    java = tmp_path / "java.exe"
    java.write_bytes(b"")
    jar = tmp_path / "scpu-agent.jar"
    jar.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_java_agent", lambda: jar)
    monkeypatch.setattr(no_engines, "java_executable", lambda: java)
    monkeypatch.setattr(no_engines, "java_version", lambda _java: "25.0.1")
    info = _info(no_engines.engine_report(), "java")
    assert info.available is True
    assert info.version == "25.0.1"


def test_native_engine_mentions_the_timer_helper(no_engines, monkeypatch, tmp_path):
    watcher = tmp_path / "scpu-watch.exe"
    watcher.write_bytes(b"")
    timer = tmp_path / "scpu-timer.exe"
    timer.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_native_watcher", lambda: watcher)
    monkeypatch.setattr(no_engines, "find_native_timer", lambda: timer)
    info = _info(no_engines.engine_report(), "native")
    assert info.available is True
    assert info.note_args == {"extras": " + scpu-timer"}


# ---------------------------------------------------------------------------
# version parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [(None, 0), ("", 0), ("17", 17), ("22.0.1", 22), ("25", 25), ("garbage", 0), ("1.8.0", 1)],
)
def test_major_version_parsing(raw, expected):
    assert engines._major(raw) == expected


def test_java_version_is_cached(monkeypatch, tmp_path):
    """``s-cpu status`` would otherwise start a JVM several times per run."""
    java = tmp_path / "java.exe"
    java.write_bytes(b"")
    calls = []

    class Completed:
        stdout = b"    java.specification.version = 25\n"
        stderr = b""

    def fake_run(*_args, **_kwargs):
        calls.append(1)
        return Completed()

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    engines._VERSION_CACHE.clear()
    try:
        assert engines.java_version(java) == "25"
        assert engines.java_version(java) == "25"
        assert len(calls) == 1
    finally:
        engines._VERSION_CACHE.clear()


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def test_auto_prefers_native_then_java_then_python(no_engines):
    assert no_engines.resolve_engine("auto").name == "python"


def test_auto_prefers_native_when_built(no_engines, monkeypatch, tmp_path):
    watcher = tmp_path / "scpu-watch.exe"
    watcher.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_native_watcher", lambda: watcher)
    assert no_engines.resolve_engine("auto").name == "native"


def test_auto_prefers_java_over_python(no_engines, monkeypatch, tmp_path):
    java = tmp_path / "java.exe"
    java.write_bytes(b"")
    jar = tmp_path / "scpu-agent.jar"
    jar.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_java_agent", lambda: jar)
    monkeypatch.setattr(no_engines, "java_executable", lambda: java)
    monkeypatch.setattr(no_engines, "java_version", lambda _java: "25")
    assert no_engines.resolve_engine("auto").name == "java"


def test_explicit_choice_is_honoured(no_engines):
    assert no_engines.resolve_engine("python").name == "python"


def test_unavailable_explicit_choice_falls_back_to_python(no_engines):
    info = no_engines.resolve_engine("native")
    assert info.name == "python"
    assert info.available is True
    assert info.note_key == "engine.fallback"
    assert info.note_args["requested"] == "native"


def test_unknown_preference_falls_back_quietly(no_engines):
    info = no_engines.resolve_engine("cobol")
    assert info.name == "python"


def test_blank_preference_means_auto(no_engines):
    assert no_engines.resolve_engine("").name == "python"
    assert no_engines.resolve_engine(None).name == "python"


def test_describe_shows_the_path_when_available(no_engines, monkeypatch, tmp_path):
    watcher = tmp_path / "scpu-watch.exe"
    watcher.write_bytes(b"")
    monkeypatch.setattr(no_engines, "find_native_watcher", lambda: watcher)
    described = no_engines.resolve_engine("native").describe()
    assert "scpu-watch.exe" in described


# ---------------------------------------------------------------------------
# launch arguments
# ---------------------------------------------------------------------------
def _config(**overrides):
    config = dict(cfgmod.DEFAULT_CONFIG)
    config.update(overrides)
    return config


def test_native_launch_args_carry_the_rollback_paths(home):
    config = _config(mode="all", priority="above_normal", timer_resolution=2)
    args = engines.native_launch_args(config, Path("C:/bin/scpu-watch.exe"))

    assert args[0].endswith("scpu-watch.exe")
    assert _value(args, "--mode") == "all"
    assert _value(args, "--priority") == "above_normal"
    assert _value(args, "--ms") == "2"
    # Without these the engine cannot be cleaned up after a hard kill.
    assert _value(args, "--state-file") == str(cfgmod.native_state_path())
    assert _value(args, "--stop-flag") == str(cfgmod.stop_flag_path())
    assert "--verbose" not in args


def _value(args, flag):
    return args[args.index(flag) + 1]


def test_native_launch_args_add_verbose_only_when_asked(home):
    args = engines.native_launch_args(_config(verbose_engine=True), Path("w.exe"))
    assert "--verbose" in args


def test_native_launch_args_pass_the_exclusion_list(home):
    args = engines.native_launch_args(_config(exclude=["a.exe", "b.exe"]), Path("w.exe"))
    assert _value(args, "--exclude") == "a.exe,b.exe"


def test_native_launch_args_omit_an_empty_exclusion_list(home):
    args = engines.native_launch_args(_config(exclude=[]), Path("w.exe"))
    assert "--exclude" not in args


def test_java_launch_args_default_to_no_turbo():
    args = engines.java_launch_args(Path("java.exe"), Path("agent.jar"), _config())
    assert args[:4] == ["java.exe", "-jar", "agent.jar", "start"]
    # Keep-alive threads cost idle power, so they are opt-in.
    assert "--turbo" not in args


def test_java_launch_args_add_turbo_threads_when_enabled():
    args = engines.java_launch_args(Path("java.exe"), Path("agent.jar"), _config(turbo_threads=4))
    assert _value(args, "--turbo") == "4"


def test_java_launch_args_forward_mode_and_priority():
    args = engines.java_launch_args(
        Path("java.exe"), Path("agent.jar"), _config(mode="all", priority="above_normal")
    )
    assert _value(args, "--mode") == "all"
    assert _value(args, "--priority") == "above_normal"
