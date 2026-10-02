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

"""Linux backend - nice levels, CPU affinity and cpufreq governors.

Support level: **beta**. The concepts map cleanly onto Linux, but the levers are
different from Windows and some of them need root:

* ``setpriority``: same idea as the Windows priority class. Negative nice
  values require ``CAP_SYS_NICE`` (i.e. root), otherwise the call is skipped.
* ``sched_setaffinity``: undoes a stale taskset pinning.
* ``scaling_governor``: writing ``performance`` to every CPU is the closest
  equivalent of switching to the High performance power plan. Requires root.
* ``energy_performance_preference``: on Intel P-state / AMD drivers, setting
  ``performance`` stops the CPU from trading latency for power.

There is no Linux equivalent of ``timeBeginPeriod``: the scheduler tick is a
kernel build-time property, so ``timer_resolution`` is reported as unsupported.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .. import __version__
from ..config import utc_timestamp
from ..i18n import t
from .base import Backend, BoostRecord, ProbeReport, ProcessInfo, SystemChange

__all__ = ["LinuxBackend"]

PRIO_PROCESS = 0
_SYSFS_CPUS = "/sys/devices/system/cpu"
GOVERNOR_PATH = _SYSFS_CPUS + "/cpu{cpu}/cpufreq/scaling_governor"
EPP_PATH = _SYSFS_CPUS + "/cpu{cpu}/cpufreq/energy_performance_preference"


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _write_text(path: str, value: str) -> bool:
    try:
        with open(path, "w", encoding="ascii") as handle:
            handle.write(value)
        return True
    except OSError:
        return False


def _cpu_ids() -> list[int]:
    ids: list[int] = []
    try:
        for entry in sorted(os.listdir(_SYSFS_CPUS)):
            if entry.startswith("cpu") and entry[3:].isdigit():
                ids.append(int(entry[3:]))
    except OSError:
        pass
    return ids


class LinuxBackend(Backend):
    name = "linux"

    # ------------------------------------------------------------------
    @staticmethod
    def is_elevated() -> bool:
        try:
            return os.geteuid() == 0
        except AttributeError:  # pragma: no cover - non-POSIX
            return False

    @staticmethod
    def is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    @staticmethod
    def terminate(pid: int) -> bool:
        try:
            import signal

            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False

    def list_processes(self) -> list[ProcessInfo]:
        found: list[ProcessInfo] = []
        try:
            entries = os.listdir("/proc")
        except OSError:
            return found
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            name = _read_text(f"/proc/{pid}/comm") or ""
            if not name:
                continue
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                exe = ""
            found.append(ProcessInfo(pid, name, exe))
        return found

    def foreground_pid(self) -> int:
        """Best effort: requires ``xdotool`` on X11. Returns 0 when unknown."""
        if not shutil.which("xdotool"):
            return 0
        try:
            window = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if window.returncode != 0 or not window.stdout.strip():
                return 0
            pid = subprocess.run(
                ["xdotool", "getwindowpid", window.stdout.strip()],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            return int(pid.stdout.strip()) if pid.returncode == 0 and pid.stdout.strip() else 0
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0

    def timer_resolution_ms(self) -> float | None:
        return None  # not applicable on Linux

    def probe(self) -> ProbeReport:
        report = ProbeReport(
            platform=f"Linux ({sys.platform})",
            backend=self.name,
            python=".".join(str(part) for part in sys.version_info[:3]),
            elevated=self.is_elevated(),
            cpu_count=os.cpu_count() or 0,
            timer_resolution_ms=None,
        )
        governor = _read_text(GOVERNOR_PATH.format(cpu=0))
        report.details["scaling_governor"] = governor
        report.details["version"] = __version__
        if governor is None:
            report.details["cpufreq"] = "not exposed by this kernel/CPU"
        if not report.elevated:
            report.problems.append(t("doctor.admin.no"))
        return report

    # ------------------------------------------------------------------
    def _governor_paths(self, template: str) -> list[str]:
        return [template.format(cpu=cpu) for cpu in _cpu_ids()]

    def apply_system(self) -> list[SystemChange]:
        changes: list[SystemChange] = []
        wanted = str(self.config.get("power_plan") or "none").lower()
        change = SystemChange(key="power_plan", description="")
        if wanted in {"none", "off", "false", ""}:
            change.skipped = True
            change.description = t("on.power_plan.skipped")
            changes.append(change)
            return changes

        paths = self._governor_paths(GOVERNOR_PATH)
        if not paths:
            change.skipped = True
            change.description = t("on.power_plan.skipped")
            change.note = "cpufreq not available"
            changes.append(change)
            return changes

        previous = {path: _read_text(path) for path in paths}
        active = _read_text(paths[0]) or t("common.unknown")
        change.previous = previous
        change.current = "performance"
        change.note = active
        if self.dry_run:
            change.applied = True
            change.description = t("on.power_plan", old=active, new="performance")
            changes.append(change)
            return changes

        ok = all(_write_text(path, "performance") for path in paths)
        change.applied = ok
        if ok:
            change.description = t("on.power_plan", old=active, new="performance")
        else:
            change.skipped = True
            change.description = t("warn.not_admin", what="scaling_governor", hint="hint: sudo")
        changes.append(change)

        epp_paths = [path for path in self._governor_paths(EPP_PATH) if _read_text(path)]
        if epp_paths:
            epp = SystemChange(key="epp", description="energy_performance_preference -> performance")
            epp.previous = {path: _read_text(path) for path in epp_paths}
            epp.applied = all(_write_text(path, "performance") for path in epp_paths)
            epp.skipped = not epp.applied
            changes.append(epp)
        return changes

    def revert_system(self, changes: list[SystemChange]) -> list[SystemChange]:
        reverted: list[SystemChange] = []
        for change in changes:
            if not change.applied or not isinstance(change.previous, dict):
                continue
            restored = True
            for path, value in change.previous.items():
                if value is None:
                    continue
                restored &= _write_text(path, value)
            if restored:
                reverted.append(
                    SystemChange(
                        key=change.key,
                        description=t(
                            "off.power_restored",
                            plan=change.note or t("common.unknown"),
                        ),
                        applied=True,
                    )
                )
        return reverted

    def hold_timer_resolution(self, milliseconds: int) -> bool:
        return False  # not applicable

    def release_timer_resolution(self) -> bool:
        return False

    # ------------------------------------------------------------------
    def _nice_value(self) -> int:
        aggressive = bool(self.config.get("aggressive"))
        return -10 if aggressive else -5

    def boost(self, process: ProcessInfo, manual: bool = False) -> BoostRecord | None:
        pid = process.pid
        try:
            before = os.getpriority(PRIO_PROCESS, pid)
        except OSError:
            return None

        record = BoostRecord(
            pid=pid,
            name=process.name,
            exe=process.exe,
            priority_before=str(before),
            boosted_at=utc_timestamp(),
            manual=manual,
        )
        target = self._nice_value()
        if self.dry_run:
            record.priority_after = str(target)
            return record

        if before <= target:
            record.priority_after = str(before)
        else:
            try:
                os.setpriority(PRIO_PROCESS, pid, target)
                record.priority_after = str(target)
            except OSError:
                return None

        try:
            current = os.sched_getaffinity(pid)
            all_cpus = os.sched_getaffinity(0)
            if current != all_cpus:
                os.sched_setaffinity(pid, all_cpus)
                record.affinity_before = len(current)
                record.affinity_cores_added = len(all_cpus) - len(current)
        except (OSError, AttributeError):
            pass

        record.threads_total = self._thread_count(pid)
        record.threads_raised = record.threads_total
        return record

    @staticmethod
    def _thread_count(pid: int) -> int:
        try:
            return len(os.listdir(f"/proc/{pid}/task"))
        except OSError:
            return 0

    def unboost(self, record: BoostRecord) -> bool:
        if self.dry_run:
            return True
        if not self.is_alive(record.pid):
            return True
        touched = False
        if record.priority_before is not None:
            try:
                os.setpriority(PRIO_PROCESS, record.pid, int(record.priority_before))
                touched = True
            except (OSError, ValueError):
                pass
        if record.affinity_before:
            try:
                cpus = sorted(os.sched_getaffinity(0))[: int(record.affinity_before)]
                os.sched_setaffinity(record.pid, set(cpus))
                touched = True
            except (OSError, AttributeError):
                pass
        return bool(touched)
