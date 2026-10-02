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

"""Windows backend.

The interesting part of SpeedyCPU lives here. Every knob below is a documented
Windows scheduling/power-management feature - nothing is faked:

======================  ==========================================================
Knob                    What it actually changes
======================  ==========================================================
priority class          ``SetPriorityClass``: the scheduler's per-process weight
thread priorities       ``SetThreadPriority`` on *every* thread, so a process that
                        parked 40 of its threads in NORMAL stops losing races to
                        the ones that did not
EcoQoS                  ``SetProcessInformation(ProcessPowerThrottling)``: clears
                        the throttling bit Windows sets on background work, which
                        is what parks threads on E-cores and drops clocks
timer resolution        ``timeBeginPeriod(1)``: system tick 15.625 ms -> 1 ms, so
                        a thread that has work to do gets scheduled far sooner
affinity                ``SetProcessAffinityMask``: undoes stale single-core
                        pinning left behind by buggy launchers
working set             ``SetProcessWorkingSetSizeEx``: optional, aggressive,
                        keeps pages resident across alt-tabs
power plan              ``powercfg /setactive``: High performance / Ultimate
======================  ==========================================================

Everything is attempted at the lowest privilege level possible and degrades
gracefully: an unelevated run still boosts same-user processes, it just skips
the machine-wide power plan changes and reports that it did.
"""

from __future__ import annotations

import locale
import os
import re
import subprocess

from .. import __version__
from ..config import utc_timestamp
from ..i18n import t
from . import winapi
from .base import Backend, BoostRecord, ProbeReport, ProcessInfo, SystemChange

__all__ = ["WindowsBackend"]

# Well known power scheme GUIDs.
GUID_BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
GUID_HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GUID_POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"
GUID_ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
GUID_SCHEME_CURRENT = "SCHEME_CURRENT"

#: ``SUB_PROCESSOR`` holds every processor power-management setting we care
#: about. Tuning these is the one big win that does **not** require elevation:
#: the Power Options control panel does not ask for it either.
SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"

#: ``name -> (setting GUID, target AC value)``.
#:
#: ``procthrottlemin``  minimum processor state: stops the CPU idling down so
#:                      the first frame after a launch is not spent ramping up
#: ``perfboostmode``    processor performance boost mode: 2 = Aggressive, the
#:                      CPU stops waiting for sustained load before boosting
#: ``cpmincores``       minimum core parking: 100 = no cores parked, so a
#:                      newly started process is not queued behind parked ones
CPU_SETTINGS = {
    "procthrottlemin": ("893dee8e-2bef-41e0-89c6-b55d0929964c", 100),
    "perfboostmode": ("be337238-0d82-4146-a960-4f3749d470c7", 2),
    "cpmincores": ("0cc5b647-c1df-4637-891a-dec35c318583", 100),
}

PLAN_ALIASES = {
    "balanced": GUID_BALANCED,
    "high_performance": GUID_HIGH_PERFORMANCE,
    "high": GUID_HIGH_PERFORMANCE,
    "performance": GUID_HIGH_PERFORMANCE,
    "power_saver": GUID_POWER_SAVER,
    "saver": GUID_POWER_SAVER,
    "ultimate": GUID_ULTIMATE,
    "ultimate_performance": GUID_ULTIMATE,
}

_GUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
_PLAN_LINE_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"\s*\(([^)]*)\)"
)

POWER_THROTTLING_KEY = r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling"
POWER_THROTTLING_VALUE = "PowerThrottlingOff"

#: Processes whose priority we never lower, only report.
_KEEP_AT_LEAST = {
    winapi.REALTIME_PRIORITY_CLASS: "realtime",
    winapi.HIGH_PRIORITY_CLASS: "high",
}


def _console_encoding() -> str:
    """Best guess at the code page ``powercfg`` writes its output in.

    Python may be running in UTF-8 mode, in which case
    ``locale.getpreferredencoding()`` lies and localised plan names come back
    as mojibake. The console output code page is the honest answer.
    """
    try:
        code_page = int(winapi.kernel32.GetConsoleOutputCP())
        if code_page:
            return f"cp{code_page}"
    except Exception:  # pragma: no cover - non-console host
        pass
    try:
        return f"cp{int(winapi.kernel32.GetOEMCP())}"
    except Exception:  # pragma: no cover
        return locale.getpreferredencoding(False)


def _decode(raw: bytes) -> str:
    """Decode console output from powercfg, trying the plausible code pages.

    ``powercfg`` prints localised text (e.g. ``电源方案`` / ``平衡``) in the system
    code page, so blindly using UTF-8 mangles it on non-English Windows.
    """
    if not raw:
        return ""
    for encoding in (_console_encoding(), "utf-8", "utf-16-le"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\ufffd" not in text:
            return text
    return raw.decode(_console_encoding(), errors="replace")


class WindowsBackend(Backend):
    name = "windows"

    # ------------------------------------------------------------------
    # environment
    # ------------------------------------------------------------------
    @staticmethod
    def is_elevated() -> bool:
        return winapi.is_admin()

    @staticmethod
    def is_alive(pid: int) -> bool:
        return winapi.is_process_alive(pid)

    @staticmethod
    def terminate(pid: int) -> bool:
        return winapi.terminate_process(pid)

    @staticmethod
    def current_pid() -> int:
        return winapi.current_pid()

    def list_processes(self) -> list[ProcessInfo]:
        return [ProcessInfo(entry.pid, entry.name, entry.exe) for entry in winapi.iter_processes()]

    def foreground_pid(self) -> int:
        return winapi.foreground_pid()

    def process_info(self, pid: int) -> ProcessInfo | None:
        """Cheap single-process lookup: no full enumeration needed."""
        name, full = winapi.process_image_name(pid)
        return ProcessInfo(pid, name, full) if name else None

    def timer_resolution_ms(self) -> float | None:
        return winapi.query_timer_resolution_ms()

    def probe(self) -> ProbeReport:
        report = ProbeReport(
            platform=f"Windows ({os.environ.get('OS', 'unknown')})",
            backend=self.name,
            python=".".join(str(part) for part in __import__("sys").version_info[:3]),
            elevated=self.is_elevated(),
            cpu_count=os.cpu_count() or 0,
            timer_resolution_ms=self.timer_resolution_ms(),
        )
        plans = self.list_power_plans()
        active = self.active_power_plan()
        report.details["power_plans"] = [f"{name} ({guid})" for guid, name in plans]
        report.details["active_power_plan"] = f"{active[1]} ({active[0]})" if active else None
        report.details["ultimate_available"] = any(guid.lower() == GUID_ULTIMATE for guid, _ in plans)
        report.details["global_throttling_off"] = self.read_global_throttling_off()
        report.details["version"] = __version__
        if not report.elevated:
            report.problems.append(t("doctor.admin.no"))
        return report

    # ------------------------------------------------------------------
    # power plans
    # ------------------------------------------------------------------
    @staticmethod
    def _powercfg(*args: str) -> tuple[int, str]:
        try:
            completed = subprocess.run(
                ["powercfg", *args],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, ValueError) as exc:  # pragma: no cover - powercfg missing
            return 1, str(exc)
        return completed.returncode, _decode(completed.stdout) + _decode(completed.stderr)

    def list_power_plans(self) -> list[tuple[str, str]]:
        code, output = self._powercfg("/list")
        if code != 0:
            return []
        plans: list[tuple[str, str]] = []
        for guid, name in _PLAN_LINE_RE.findall(output):
            plans.append((guid.lower(), name.strip()))
        return plans

    def active_power_plan(self) -> tuple[str, str] | None:
        code, output = self._powercfg("/getactivescheme")
        if code != 0:
            return None
        match = _PLAN_LINE_RE.search(output)
        if not match:
            return None
        return match.group(1).lower(), match.group(2).strip()

    def resolve_power_plan(self, spec: str) -> str | None:
        """Turn a user supplied plan spec into a GUID that exists on this box.

        Accepts a raw GUID, a friendly alias (``high_performance``,
        ``ultimate``, ``balanced``, ``power_saver``) or a substring of a
        localised plan name.
        """
        spec = (spec or "").strip()
        if not spec or spec.lower() in {"none", "off", "no", "false"}:
            return None
        plans = self.list_power_plans()
        existing = dict(plans)

        if _GUID_RE.fullmatch(spec):
            guid = spec.lower()
            if guid in existing:
                return guid
            # Try to materialise a hidden built-in scheme.
            if self.is_elevated() and self._powercfg("/duplicatescheme", guid)[0] == 0:
                for candidate, _name in self.list_power_plans():
                    if candidate == guid:
                        return candidate
            return None

        alias = PLAN_ALIASES.get(spec.lower().replace(" ", "_"))
        if alias:
            if alias in existing:
                return alias
            if self.is_elevated() and self._powercfg("/duplicatescheme", alias)[0] == 0:
                for candidate, _name in self.list_power_plans():
                    if candidate == alias:
                        return candidate
            return None

        lowered = spec.lower()
        for guid, name in plans:
            if lowered in name.lower():
                return guid
        return None

    def set_power_plan(self, guid: str) -> bool:
        if self.dry_run:
            return True
        return self._powercfg("/setactive", guid)[0] == 0

    def activate_current_scheme(self) -> bool:
        if self.dry_run:
            return True
        return self._powercfg("/setactive", GUID_SCHEME_CURRENT)[0] == 0

    # ------------------------------------------------------------------
    # individual processor power settings on the active scheme
    # ------------------------------------------------------------------
    def query_cpu_setting(self, name: str) -> int | None:
        """Read the current AC value of one processor setting."""
        entry = CPU_SETTINGS.get(name)
        if not entry:
            return None
        setting_guid, _target = entry
        code, output = self._powercfg("/query", GUID_SCHEME_CURRENT, SUB_PROCESSOR, setting_guid)
        if code != 0:
            return None
        # powercfg prints "Current AC Power Setting Index: 0x00000064" (localised
        # prefix, same hex payload), and it is the first 0x token in the block
        # because the "Possible Setting Index" lines are decimal.
        match = re.search(r"0x([0-9a-fA-F]+)", output)
        return int(match.group(1), 16) if match else None

    def set_cpu_setting(self, name: str, value: int, include_dc: bool = False) -> bool:
        entry = CPU_SETTINGS.get(name)
        if not entry:
            return False
        setting_guid, _target = entry
        if self.dry_run:
            return True
        ok = self._powercfg("/setacvalueindex", GUID_SCHEME_CURRENT, SUB_PROCESSOR, setting_guid, str(value))[0] == 0
        if include_dc:
            self._powercfg("/setdcvalueindex", GUID_SCHEME_CURRENT, SUB_PROCESSOR, setting_guid, str(value))
        return ok

    def apply_cpu_tuning(self) -> SystemChange:
        """Stop the CPU from idling down / parking cores on the active scheme.

        These three settings are what actually removes the "the program takes a
        moment to wake up" feeling. They apply to the current scheme only, so
        ``s-cpu off`` restores them individually without touching which plan
        the user prefers.
        """
        change = SystemChange(key="cpu_tuning", description="")
        if not self.config.get("cpu_tuning"):
            change.skipped = True
            change.description = t("on.cpu_tuning.skipped")
            return change

        previous: dict[str, int | None] = {}
        wanted: dict[str, int] = {}
        for name in CPU_SETTINGS:
            previous[name] = self.query_cpu_setting(name)
            wanted[name] = CPU_SETTINGS[name][1]

        # Only settings the active scheme actually exposes can be tuned; some
        # builds hide "performance boost mode" entirely. Ignore the rest
        # instead of failing the whole step.
        applicable = [name for name, value in previous.items() if value is not None]
        if not applicable:
            change.skipped = True
            change.description = t("on.cpu_tuning.skipped")
            change.note = "processor power settings not exposed"
            return change

        change.previous = previous
        change.current = {name: wanted[name] for name in applicable}
        change.note = ", ".join(
            f"{name} {previous[name]} -> {wanted[name]}"
            for name in applicable
            if previous[name] != wanted[name]
        )
        if self.dry_run:
            change.applied = True
            change.description = t(
                "on.cpu_tuning",
                minimum=wanted["procthrottlemin"],
                boost=wanted["perfboostmode"],
            )
            return change

        include_dc = bool(self.config.get("aggressive"))
        applied = True
        for name in applicable:
            if previous[name] == wanted[name]:
                continue
            applied &= self.set_cpu_setting(name, wanted[name], include_dc=include_dc)
        if applied:
            self.activate_current_scheme()
            change.applied = True
            change.description = t(
                "on.cpu_tuning",
                minimum=wanted["procthrottlemin"],
                boost=wanted["perfboostmode"],
            )
        else:
            change.skipped = True
            change.description = t("on.cpu_tuning.skipped")
        return change

    def revert_cpu_tuning(self, change: SystemChange) -> SystemChange | None:
        if not change.applied or not isinstance(change.previous, dict):
            return None
        ok = True
        for name, value in change.previous.items():
            if value is None:
                continue
            ok &= self.set_cpu_setting(name, int(value), include_dc=bool(self.config.get("aggressive")))
        if not ok:
            return None
        self.activate_current_scheme()
        return SystemChange(key="cpu_tuning", description=t("off.cpu_tuning_restored"), applied=True)

    # ------------------------------------------------------------------
    # global power throttling (HKLM)
    # ------------------------------------------------------------------
    def read_global_throttling_off(self) -> int | None:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, POWER_THROTTLING_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, POWER_THROTTLING_VALUE)
                return int(value)
        except (FileNotFoundError, OSError, ValueError):
            return None

    def write_global_throttling_off(self, value: int) -> bool:
        if self.dry_run:
            return True
        try:
            import winreg

            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, POWER_THROTTLING_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, POWER_THROTTLING_VALUE, 0, winreg.REG_DWORD, int(value))
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # system wide changes
    # ------------------------------------------------------------------
    def apply_system(self) -> list[SystemChange]:
        changes: list[SystemChange] = []

        spec = str(self.config.get("power_plan") or "none")
        change = SystemChange(key="power_plan", description="")
        active = self.active_power_plan()
        change.previous = active[0] if active else None
        change.note = active[1] if active else ""
        if spec.lower() in {"none", "off", "false", ""}:
            change.skipped = True
            change.description = t("on.power_plan.skipped")
        else:
            guid = self.resolve_power_plan(spec)
            if guid is None:
                change.skipped = True
                change.description = t("on.power_plan.unavailable", spec=spec)
                change.note = spec
            elif active and active[0] == guid:
                change.applied = True
                change.current = guid
                change.description = t("on.power_plan", old=active[1], new=active[1])
            else:
                change.current = guid
                ok = self.set_power_plan(guid)
                change.applied = ok
                if ok:
                    name = dict(self.list_power_plans()).get(guid, guid)
                    change.description = t(
                        "on.power_plan",
                        old=change.note or t("common.unknown"),
                        new=name,
                    )
                else:
                    change.skipped = True
                    change.description = t("warn.not_admin", what="powercfg /setactive", hint=t("err.hint_admin"))
        changes.append(change)

        # Per-setting CPU tuning works without elevation, so it is the reliable
        # half of the machine-wide story; the plan switch is the bonus.
        changes.append(self.apply_cpu_tuning())

        if self.config.get("global_power_throttling_off"):
            throttle = SystemChange(key="global_throttling", description=t("on.throttling"))
            previous = self.read_global_throttling_off()
            throttle.previous = previous
            if self.write_global_throttling_off(1):
                throttle.applied = True
                throttle.current = 1
            else:
                throttle.skipped = True
                throttle.description = t("warn.not_admin", what="HKLM PowerThrottlingOff", hint=t("err.hint_admin"))
            changes.append(throttle)
        else:
            changes.append(
                SystemChange(key="global_throttling", description=t("on.throttling.skipped"), skipped=True)
            )
        return changes

    def revert_system(self, changes: list[SystemChange]) -> list[SystemChange]:
        reverted: list[SystemChange] = []
        for change in changes:
            if change.key == "power_plan" and change.applied and change.previous:
                if self.set_power_plan(str(change.previous)):
                    name = dict(self.list_power_plans()).get(str(change.previous), str(change.previous))
                    reverted.append(
                        SystemChange(
                            key="power_plan",
                            description=t("off.power_restored", plan=name),
                            applied=True,
                            current=str(change.previous),
                        )
                    )
            elif change.key == "cpu_tuning":
                restored = self.revert_cpu_tuning(change)
                if restored:
                    reverted.append(restored)
            elif change.key == "global_throttling" and change.applied:
                if change.previous is None:
                    if self.dry_run:
                        ok = True
                    else:
                        try:
                            import winreg

                            with winreg.OpenKey(
                                winreg.HKEY_LOCAL_MACHINE, POWER_THROTTLING_KEY, 0, winreg.KEY_SET_VALUE
                            ) as key:
                                winreg.DeleteValue(key, POWER_THROTTLING_VALUE)
                            ok = True
                        except OSError:
                            ok = False
                else:
                    ok = self.write_global_throttling_off(int(change.previous))
                if ok:
                    reverted.append(
                        SystemChange(
                            key="global_throttling",
                            description=t("off.throttling_restored"),
                            applied=True,
                        )
                    )
        return reverted

    # ------------------------------------------------------------------
    # timer resolution
    # ------------------------------------------------------------------
    def hold_timer_resolution(self, milliseconds: int) -> bool:
        if self.dry_run:
            return True
        if self._timer is None:
            self._timer = winapi.TimerResolution(milliseconds)
        else:
            self._timer.milliseconds = int(milliseconds)
        return self._timer.acquire()

    def release_timer_resolution(self) -> bool:
        if self._timer is None:
            return False
        released = self._timer.release()
        return bool(released)

    # ------------------------------------------------------------------
    # per process
    # ------------------------------------------------------------------
    def _target_priority_class(self) -> int:
        wanted = str(self.config.get("priority") or "high").lower()
        if wanted in {"above_normal", "above", "abovenormal"}:
            return winapi.ABOVE_NORMAL_PRIORITY_CLASS
        if wanted in {"high", "highest"}:
            return winapi.HIGH_PRIORITY_CLASS
        return winapi.HIGH_PRIORITY_CLASS

    def boost(self, process: ProcessInfo, manual: bool = False) -> BoostRecord | None:
        pid = process.pid
        if pid <= 0:
            return None
        current_class = winapi.get_priority_class(pid)
        if current_class is None:
            return None

        record = BoostRecord(
            pid=pid,
            name=process.name,
            exe=process.exe,
            priority_before=winapi.PRIORITY_CLASS_NAMES.get(current_class, str(current_class)),
            boosted_at=utc_timestamp(),
            manual=manual,
        )

        if self.dry_run:
            record.priority_after = winapi.PRIORITY_CLASS_NAMES.get(
                self._target_priority_class(), "high"
            )
            record.threads_total = winapi.thread_count(pid)
            record.threads_raised = record.threads_total
            return record

        # 1) priority class - never downgrade something already running higher.
        target = self._target_priority_class()
        if current_class in _KEEP_AT_LEAST and _KEEP_AT_LEAST[current_class] == "realtime":
            record.priority_after = "realtime"
        elif current_class >= target:
            record.priority_after = winapi.PRIORITY_CLASS_NAMES.get(current_class, "high")
        elif winapi.set_priority_class(pid, target):
            record.priority_after = winapi.PRIORITY_CLASS_NAMES.get(target, "high")
        else:
            return None

        # 2) every thread of the process gets the same favour, so a single hot
        #    thread can no longer be starved by its own background workers.
        if self.config.get("raise_threads"):
            raised, total, changes = winapi.raise_threads(pid, winapi.THREAD_PRIORITY_HIGHEST)
            record.threads_raised = raised
            record.threads_total = total
            record.thread_priorities = [[tid, prio] for tid, prio in changes]

        # 3) clear EcoQoS so the scheduler stops treating it as background work.
        if self.config.get("disable_ecoqos"):
            record.ecoqos_disabled = winapi.set_process_ecoqos(pid, disable=True)

        # 4) un-pin the process if a launcher left it on a single core.
        if self.config.get("reset_affinity"):
            masks = winapi.get_affinity(pid)
            if masks:
                process_mask, system_mask = masks
                # A process pinned to a strict subset of the machine is being
                # throttled, so hand it every core back.
                if process_mask and process_mask != system_mask and winapi.set_affinity(pid, system_mask):
                    record.affinity_before = process_mask
                    record.affinity_cores_added = winapi.popcount(system_mask) - winapi.popcount(process_mask)

        # 5) optional: keep the working set resident across alt-tabs.
        if self.config.get("aggressive"):
            size = winapi.working_set_size(pid)
            if size:
                record.hard_working_set = winapi.set_hard_working_set(
                    pid, int(size * 0.75), int(size * 1.5)
                )

        return record

    def unboost(self, record: BoostRecord) -> bool:
        if self.dry_run:
            return True
        pid = record.pid
        if not winapi.is_process_alive(pid):
            return True  # nothing to do, the process is gone
        touched = False

        if record.thread_priorities:
            changes = [(int(tid), int(prio)) for tid, prio in record.thread_priorities]
            touched |= winapi.restore_thread_priorities(pid, changes) > 0

        if record.priority_before:
            value = winapi.PRIORITY_CLASS_VALUES.get(record.priority_before)
            if value is not None:
                touched |= winapi.set_priority_class(pid, value)

        if record.ecoqos_disabled:
            # Hand the process back to Windows' own judgement.
            touched |= winapi.set_process_ecoqos(pid, disable=False)

        if record.affinity_before:
            touched |= winapi.set_affinity(pid, int(record.affinity_before))

        if record.hard_working_set:
            touched |= winapi.clear_hard_working_set(pid)

        return bool(touched)
