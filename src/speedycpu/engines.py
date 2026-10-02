# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU Library General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Library General Public License for more
# details.  You should have received a copy of it in the LICENSE file.

"""Engine selection: which of the three boost engines actually runs.

SpeedyCPU ships three interchangeable engines that all do the same job through
the same Win32 calls, but with different runtime costs. Picking the right one is
a trade-off the user should be able to see and override:

===========  =============  ==========================  ======================
engine       footprint      how it notices focus         when to use it
===========  =============  ==========================  ======================
``native``   ~2 MB RSS      ``SetWinEventHook`` (event)  default when built
``java``     ~60 MB RSS     ``SetWinEventHook`` (event)  no compiler, portable
``python``   ~15 MB RSS     polling every 200 ms         always available
===========  =============  ==========================  ======================

The Python engine is the fallback that is always present, which is what makes
``pip install speedycpu`` work with no native toolchain at all. ``auto`` prefers
``native``, then ``java``, then ``python``.

Kept deliberately free of imports from :mod:`speedycpu.cli` so that the
resolution logic stays unit-testable without touching the machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as cfgmod

__all__ = [
    "ENGINE_ORDER",
    "EngineInfo",
    "engine_report",
    "find_java_agent",
    "find_native_watcher",
    "resolve_engine",
]

ENGINE_ORDER = ("native", "java", "python")

NATIVE_WATCHER_NAMES = ("scpu-watch.exe", "scpu-watch")
JAVA_AGENT_NAMES = ("scpu-agent.jar",)
TIMER_HELPER_NAMES = ("scpu-timer.exe", "scpu-timer")


@dataclass
class EngineInfo:
    name: str
    available: bool
    note: str = ""
    path: str | None = None
    version: str | None = None
    #: When set, the UI prefers ``t(note_key, **note_args)`` over ``note`` so
    #: engine descriptions are localised like everything else. ``note`` stays
    #: as the English/technical fallback for logs and ``--json`` output.
    note_key: str = ""
    note_args: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.note_args is None:
            self.note_args = {}

    def describe(self) -> str:
        if not self.available:
            return f"{self.name}: {self.note}"
        return f"{self.name}: {self.path}" + (f" ({self.note})" if self.note else "")


def _search_dirs() -> list[Path]:
    """Directories that may hold a bundled engine binary.

    Order matters: a file sitting next to the running executable wins over one
    found on PATH, so a portable install always uses its own engines.
    """
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    # Repository / wheel layout: <root>/src/speedycpu/engines.py
    here = Path(__file__).resolve()
    dirs.append(here.parent)
    for parent in (here.parent.parent, here.parent.parent.parent, here.parent.parent.parent.parent):
        dirs.append(parent)
        dirs.append(parent / "native" / "dist")
        dirs.append(parent / "native")
        dirs.append(parent / "java" / "dist")
        dirs.append(parent / "java")
    dirs.append(cfgmod.home_dir())
    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def _locate(names: tuple[str, ...]) -> Path | None:
    for directory in _search_dirs():
        for name in names:
            candidate = directory / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def find_native_watcher() -> Path | None:
    """Locate ``scpu-watch`` - the C++ engine."""
    return _locate(NATIVE_WATCHER_NAMES)


def find_native_timer() -> Path | None:
    """Locate ``scpu-timer`` - the standalone timer-resolution holder."""
    return _locate(TIMER_HELPER_NAMES)


def java_executable() -> Path | None:
    """``java`` from JAVA_HOME first, then PATH."""
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        for name in ("java.exe", "java"):
            candidate = Path(java_home) / "bin" / name
            if candidate.is_file():
                return candidate
    found = shutil.which("java")
    return Path(found) if found else None


def find_java_agent() -> Path | None:
    return _locate(JAVA_AGENT_NAMES)


#: ``java -version`` costs a full JVM start (~0.5-1 s), and ``engine_report`` is
#: called more than once per command (``status`` asks for it, then
#: ``resolve_engine`` asks again). Caching keeps ``s-cpu status`` snappy.
_VERSION_CACHE: dict[str, str | None] = {}


def java_version(java: Path | None) -> str | None:
    """Return the JVM's major version, or None when it cannot be queried."""
    if java is None:
        return None
    key = str(java)
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    _VERSION_CACHE[key] = _query_java_version(java)
    return _VERSION_CACHE[key]


def _query_java_version(java: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(java), "-XshowSettings:properties", "-version"],
            capture_output=True,
            check=False,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "java.specification.version" in line and "=" in line:
            return line.split("=", 1)[1].strip()
    return None


def _major(version: str | None) -> int:
    if not version:
        return 0
    head = version.strip().split(".")[0]
    try:
        return int(head)
    except ValueError:
        return 0


#: The Foreign Function & Memory API used by the agent became final in JDK 22.
JAVA_MIN_MAJOR = 22


def engine_report() -> list[EngineInfo]:
    """Availability of every engine, for ``s-cpu doctor``."""
    report: list[EngineInfo] = []

    watcher = find_native_watcher()
    timer = find_native_timer()
    if watcher:
        extras = " + scpu-timer" if timer else ""
        report.append(
            EngineInfo(
                "native",
                True,
                note=f"C++ engine{extras}",
                path=str(watcher),
                note_key="engine.native.ready",
                note_args={"extras": extras},
            )
        )
    else:
        report.append(
            EngineInfo(
                "native",
                False,
                note="not built; build with native/build.bat",
                note_key="engine.native.missing",
            )
        )

    java = java_executable()
    jar = find_java_agent()
    version = java_version(java) if (java and jar) else None
    if java and jar and _major(version) >= JAVA_MIN_MAJOR:
        report.append(
            EngineInfo(
                "java",
                True,
                note=f"JDK {version}",
                path=str(jar),
                version=version,
                note_key="engine.java.ready",
                note_args={"version": version},
            )
        )
    elif java and jar and _major(version) < JAVA_MIN_MAJOR:
        report.append(
            EngineInfo(
                "java",
                False,
                note=f"needs JDK {JAVA_MIN_MAJOR}+ (found {version or 'unknown'})",
                path=str(jar),
                version=version,
                note_key="engine.java.old_jdk",
                note_args={"min": JAVA_MIN_MAJOR, "version": version or "?"},
            )
        )
    elif not java:
        report.append(EngineInfo("java", False, note="java not found on PATH", note_key="engine.java.no_java"))
    else:
        report.append(EngineInfo("java", False, note="scpu-agent.jar not found", note_key="engine.java.no_jar"))

    report.append(
        EngineInfo("python", True, note="built in, always available", note_key="engine.python.ready")
    )
    return report


def resolve_engine(preference: str = "auto") -> EngineInfo:
    """Pick the engine to run, honouring an explicit preference.

    ``auto`` walks :data:`ENGINE_ORDER`. An explicit choice that is unavailable
    falls back to the Python engine rather than failing: ``s-cpu on`` should
    never leave the user with nothing.
    """
    preference = (preference or "auto").strip().lower()
    report = {info.name: info for info in engine_report()}

    if preference in {"", "auto"}:
        for name in ENGINE_ORDER:
            info = report.get(name)
            if info and info.available:
                return info
        return report["python"]

    info = report.get(preference)
    if info and info.available:
        return info
    fallback = report["python"]
    if info is not None:
        return EngineInfo(
            "python",
            True,
            note=f"requested '{preference}' unavailable ({info.note}); fell back to python",
            path=fallback.path,
            note_key="engine.fallback",
            note_args={"requested": preference, "reason": info.note},
        )
    return fallback


def native_launch_args(config: dict[str, Any], watcher: Path) -> list[str]:
    """Command line for the native engine, derived from the shared config."""
    excludes = ",".join(str(item) for item in (config.get("exclude") or []) if str(item).strip())
    args = [
        str(watcher),
        "--mode",
        str(config.get("mode") or "foreground"),
        "--priority",
        str(config.get("priority") or "high"),
        "--ms",
        str(int(config.get("timer_resolution") or 0)),
        "--stop-flag",
        str(cfgmod.stop_flag_path()),
        # The rollback file is what makes ``s-cpu off`` reliable even when the
        # engine is killed rather than asked to stop.
        "--state-file",
        str(cfgmod.native_state_path()),
    ]
    if excludes:
        args += ["--exclude", excludes]
    if config.get("verbose_engine"):
        args.append("--verbose")
    return args


def java_launch_args(java: Path, jar: Path, config: dict[str, Any]) -> list[str]:
    """Command line for the Java engine."""
    args = [str(java), "-jar", str(jar), "start"]
    turbo = int(config.get("turbo_threads") or 0)
    if turbo > 0:
        args += ["--turbo", str(turbo)]
    args += ["--mode", str(config.get("mode") or "foreground")]
    args += ["--priority", str(config.get("priority") or "high")]
    return args
