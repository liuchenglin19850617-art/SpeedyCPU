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

"""Platform backend interface shared by Windows / Linux / macOS.

Keeping the contract in one place means the CLI, the worker and the tests never
need to know which OS they are on: they talk to a :class:`Backend` and the
concrete implementation handles the syscall soup.
"""

from __future__ import annotations

import fnmatch
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, NamedTuple

from ..config import NEVER_TOUCH

__all__ = [
    "PRIORITY_CLASS_NAMES",
    "PRIORITY_CLASS_VALUES",
    "Backend",
    "BoostRecord",
    "ProcessInfo",
    "SystemChange",
    "is_protected",
    "matches_any",
]

#: Numeric Win32 priority class -> the name used everywhere in SpeedyCPU.
#:
#: This table is Win32 vocabulary but carries no Win32 dependency, so it lives
#: here rather than in :mod:`speedycpu.backends.winapi`. That matters because
#: the C++ engine writes its rollback file with the *numeric* value, and parsing
#: that file has to work on any platform - including the Linux CI runners that
#: never load ``kernel32``.
PRIORITY_CLASS_NAMES: dict[int, str] = {
    0x00000040: "idle",
    0x00004000: "below_normal",
    0x00000020: "normal",
    0x00008000: "above_normal",
    0x00000080: "high",
    0x00000100: "realtime",
}
PRIORITY_CLASS_VALUES: dict[str, int] = {name: value for value, name in PRIORITY_CLASS_NAMES.items()}


class ProcessInfo(NamedTuple):
    pid: int
    name: str
    exe: str


@dataclass
class BoostRecord:
    """Everything needed to undo a boost, plus a bit of reporting sugar."""

    pid: int
    name: str
    exe: str = ""
    priority_before: str | None = None
    priority_after: str | None = None
    threads_raised: int = 0
    threads_total: int = 0
    #: ``[(thread_id, priority_before), ...]`` for the threads we modified, so
    #: ``s-cpu off`` can put each one back exactly where it was.
    thread_priorities: list[list[int]] = field(default_factory=list)
    ecoqos_disabled: bool = False
    affinity_before: int | None = None
    affinity_cores_added: int = 0
    hard_working_set: bool = False
    boosted_at: str = ""
    manual: bool = False
    #: Why this process was picked: ``"foreground"`` (it owns the focused
    #: window), ``"pattern"`` (it matched a user pattern) or ``"all"``. Only
    #: ``foreground`` records are released when the user switches away - that
    #: reversion is what actually makes the focused app win.
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BoostRecord:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = {k: v for k, v in payload.items() if k in known}
        thread_priorities = data.get("thread_priorities")
        if thread_priorities:
            data["thread_priorities"] = [[int(a), int(b)] for a, b in thread_priorities]
        return cls(**data)


@dataclass
class SystemChange:
    """A single machine-wide tweak, recorded so it can be undone precisely."""

    key: str
    description: str
    applied: bool = False
    skipped: bool = False
    previous: Any = None
    current: Any = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SystemChange:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})


@dataclass
class ProbeReport:
    """Result of ``s-cpu doctor``."""

    platform: str = ""
    backend: str = ""
    python: str = ""
    elevated: bool = False
    cpu_count: int = 0
    timer_resolution_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    """Case-insensitive glob match of a process name against patterns.

    ``*`` matches everything. A pattern without a dot also matches the name
    without its ``.exe`` suffix, so ``s-cpu targets add chrome`` works.
    """
    if not name:
        return False
    lowered = name.lower()
    stem = lowered[:-4] if lowered.endswith(".exe") else lowered
    for pattern in patterns:
        pattern = (pattern or "").strip().lower()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if fnmatch.fnmatchcase(lowered, pattern) or fnmatch.fnmatchcase(stem, pattern):
            return True
    return False


def is_protected(name: str) -> bool:
    """True for kernel / critical processes SpeedyCPU refuses to touch."""
    lowered = (name or "").strip().lower()
    return lowered in NEVER_TOUCH


class Backend(ABC):
    """The contract every platform backend implements."""

    #: Short identifier used in ``s-cpu doctor`` output and log lines.
    name = "base"

    def __init__(self, config: dict[str, Any], console: Any = None, dry_run: bool = False) -> None:
        self.config = config
        self.console = console
        self.dry_run = dry_run
        self._timer = None

    # -- environment -------------------------------------------------------
    @staticmethod
    @abstractmethod
    def is_elevated() -> bool:
        """True when running with administrator / root privileges."""

    @abstractmethod
    def probe(self) -> ProbeReport:
        """Collect read-only diagnostics for ``s-cpu doctor``."""

    @abstractmethod
    def list_processes(self) -> list[ProcessInfo]:
        """Snapshot the running process list."""

    @abstractmethod
    def foreground_pid(self) -> int:
        """PID owning the currently focused window (0 when unknown)."""

    @staticmethod
    def is_alive(pid: int) -> bool:
        raise NotImplementedError

    @staticmethod
    def terminate(pid: int) -> bool:
        raise NotImplementedError

    # -- system wide -------------------------------------------------------
    @abstractmethod
    def apply_system(self) -> list[SystemChange]:
        """Apply machine-wide tweaks (power plan, global throttling, ...)."""

    @abstractmethod
    def revert_system(self, changes: list[SystemChange]) -> list[SystemChange]:
        """Undo whatever :meth:`apply_system` changed."""

    # -- timer resolution --------------------------------------------------
    @abstractmethod
    def hold_timer_resolution(self, milliseconds: int) -> bool:
        """Raise the system timer resolution and keep it raised."""

    @abstractmethod
    def release_timer_resolution(self) -> bool:
        """Drop the timer resolution request made by this process."""

    def timer_resolution_ms(self) -> float | None:
        return None

    # -- per process -------------------------------------------------------
    @abstractmethod
    def boost(self, process: ProcessInfo, manual: bool = False) -> BoostRecord | None:
        """Apply every per-process optimisation. Returns None if impossible."""

    @abstractmethod
    def unboost(self, record: BoostRecord) -> bool:
        """Restore the state recorded in *record*."""

    # -- helpers -----------------------------------------------------------
    def targets(self) -> list[str]:
        return list(self.config.get("targets") or [])

    def exclusions(self) -> list[str]:
        return list(self.config.get("exclude") or [])

    def mode(self) -> str:
        return str(self.config.get("mode") or "foreground").lower()

    def process_info(self, pid: int) -> ProcessInfo | None:
        """Look up a single PID cheaply. Backends should override this."""
        for process in self.list_processes():
            if process.pid == pid:
                return process
        return None

    def match_reason(self, process: ProcessInfo) -> str | None:
        """Return why *process* should be boosted, or None to leave it alone."""
        name = process.name
        if not name or is_protected(name):
            return None
        if matches_any(name, self.exclusions()):
            return None
        targets = [pattern for pattern in self.targets() if str(pattern).strip() not in {"", "*"}]
        if matches_any(name, targets):
            return "pattern"
        if self.mode() == "foreground":
            # Only the focused window gets the boost, so the program the user
            # is actually looking at wins the CPU against everything else.
            # Boosting every process instead would leave the ratios unchanged.
            return "foreground" if process.pid == self.foreground_pid() else None
        if "*" in [str(pattern).strip() for pattern in self.targets()] or not self.targets():
            return "all"
        return None

    def should_boost(self, process: ProcessInfo) -> bool:
        """Decide whether *process* is in scope for boosting right now."""
        return self.match_reason(process) is not None

    def describe_scope(self) -> str:
        patterns = [str(item).strip() for item in self.targets() if str(item).strip() and str(item).strip() != "*"]
        if self.mode() == "foreground":
            base = "foreground window"
            if patterns:
                return f"{base} + {', '.join(patterns)}"
            return base
        if "*" in [str(item).strip() for item in self.targets()] or not self.targets():
            return "all user processes"
        return ", ".join(patterns)
