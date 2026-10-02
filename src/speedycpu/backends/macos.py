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

"""macOS backend - nice levels and low-power mode.

Support level: **experimental**, and deliberately conservative. macOS exposes
far less influence over another process' scheduling than Windows does, and the
knobs that do exist (Quality of Service classes, ``taskpolicy``) have
version-dependent semantics that vary between Intel and Apple Silicon. Rather
than guess, this backend only uses two well-defined levers:

* ``setpriority`` - same as the Windows priority class, but negative nice
  values require root.
* ``pmset -a lowpowermode 0`` - the closest equivalent of leaving a low power
  power plan. Requires root.

Everything else is reported as unsupported instead of silently pretending to
work.
"""

from __future__ import annotations

import os
import subprocess
import sys

from .. import __version__
from ..config import utc_timestamp
from ..i18n import t
from .base import Backend, BoostRecord, ProbeReport, ProcessInfo, SystemChange

__all__ = ["MacOSBackend"]

PRIO_PROCESS = 0
#: ``PRIO_DARWIN_BG`` demotes a process to background QoS. Clearing it (setting
#: the flag to 0) is the one QoS-adjacent lever that is safe to use.
PRIO_DARWIN_BG = getattr(os, "PRIO_DARWIN_BG", 0x1000)


def _run(args: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(args, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, completed.stdout.decode("utf-8", errors="replace")


class MacOSBackend(Backend):
    name = "macos"

    # ------------------------------------------------------------------
    @staticmethod
    def is_elevated() -> bool:
        try:
            return os.geteuid() == 0
        except AttributeError:  # pragma: no cover
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
        code, output = _run(["ps", "-axo", "pid=,comm="])
        if code != 0:
            return []
        found: list[ProcessInfo] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_text, _, command = line.partition(" ")
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            exe = command.strip()
            name = os.path.basename(exe) or exe
            found.append(ProcessInfo(pid, name, exe))
        return found

    def foreground_pid(self) -> int:
        # No supported API without extra frameworks; foreground mode is a no-op.
        return 0

    def timer_resolution_ms(self) -> float | None:
        return None

    def probe(self) -> ProbeReport:
        report = ProbeReport(
            platform=f"macOS ({sys.platform})",
            backend=self.name,
            python=".".join(str(part) for part in sys.version_info[:3]),
            elevated=self.is_elevated(),
            cpu_count=os.cpu_count() or 0,
            timer_resolution_ms=None,
        )
        code, output = _run(["pmset", "-g"])
        low_power = None
        if code == 0:
            for line in output.splitlines():
                if "lowpowermode" in line:
                    low_power = line.split()[-1]
        report.details["lowpowermode"] = low_power
        report.details["version"] = __version__
        report.problems.append("macOS support is experimental: only nice levels are used.")
        return report

    # ------------------------------------------------------------------
    def apply_system(self) -> list[SystemChange]:
        change = SystemChange(key="power_plan", description="")
        wanted = str(self.config.get("power_plan") or "none").lower()
        if wanted in {"none", "off", "false", ""}:
            change.skipped = True
            change.description = t("on.power_plan.skipped")
            return [change]

        code, output = _run(["pmset", "-g"])
        previous = None
        if code == 0:
            for line in output.splitlines():
                if "lowpowermode" in line:
                    previous = line.split()[-1]
        change.previous = previous
        if self.dry_run:
            change.applied = True
            change.description = t("on.power_plan", old=previous or "?", new="lowpowermode 0")
            return [change]
        if _run(["pmset", "-a", "lowpowermode", "0"])[0] == 0:
            change.applied = True
            change.description = t("on.power_plan", old=previous or "?", new="lowpowermode 0")
        else:
            change.skipped = True
            change.description = t("warn.not_admin", what="pmset -a lowpowermode 0", hint="hint: use sudo")
        return [change]

    def revert_system(self, changes: list[SystemChange]) -> list[SystemChange]:
        reverted: list[SystemChange] = []
        for change in changes:
            if change.key != "power_plan" or not change.applied or change.previous is None:
                continue
            if self.dry_run or _run(["pmset", "-a", "lowpowermode", str(change.previous)])[0] == 0:
                reverted.append(
                    SystemChange(
                        key="power_plan",
                        description=t("off.power_restored", plan=f"lowpowermode {change.previous}"),
                        applied=True,
                    )
                )
        return reverted

    def hold_timer_resolution(self, milliseconds: int) -> bool:
        return False

    def release_timer_resolution(self) -> bool:
        return False

    # ------------------------------------------------------------------
    def _nice_value(self) -> int:
        return -10 if self.config.get("aggressive") else -5

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

        # Clear the background QoS demotion if the OS exposed the constant.
        try:
            if hasattr(os, "PRIO_DARWIN_BG"):
                os.setpriority(PRIO_DARWIN_BG, pid, 0)
        except OSError:
            pass
        return record

    def unboost(self, record: BoostRecord) -> bool:
        if self.dry_run:
            return True
        if not self.is_alive(record.pid):
            return True
        if record.priority_before is None:
            return False
        try:
            os.setpriority(PRIO_PROCESS, record.pid, int(record.priority_before))
            return True
        except (OSError, ValueError):
            return False
