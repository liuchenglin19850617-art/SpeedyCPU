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

"""The background worker behind ``s-cpu on``.

Why a worker at all? Because two of the things SpeedyCPU does are inherently
*stateful* and cannot be done by a fire-and-forget command:

``timeBeginPeriod(1)``
    The raised timer resolution lasts only while the process that requested it
    is alive. If ``s-cpu on`` simply exited, the 1 ms tick would die with it.
    Threads of the boosted programs would then sleep up to 15.6 ms per wake-up
    regardless of priority.

continuous process watching
    "Open any program and it gets faster" means someone has to notice new
    processes. That someone is this loop.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config as cfgmod
from . import engines
from .backends import PRIORITY_CLASS_NAMES, BoostRecord, ProcessInfo, get_backend
from .console import Console
from .i18n import t

__all__ = [
    "Worker",
    "read_engine_leftovers",
    "spawn_engine",
    "spawn_worker",
    "stop_worker",
    "sweep_rollback_files",
    "worker_command",
]

LOG_MAX_BYTES = 1_000_000

#: The focus watcher has to be quicker than a human alt-tab, and it only costs
#: two syscalls per tick, so 200 ms is affordable.
FAST_TICK_SECONDS = 0.2


def worker_command() -> list[str]:
    """Command line used to relaunch this tool as a detached worker."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-file/one-dir build: the exe *is* the entry point.
        return [sys.executable, "_worker"]
    return [sys.executable, "-m", "speedycpu", "_worker"]


def _rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            backup = path.with_suffix(".log.1")
            with contextlib.suppress(OSError):
                backup.unlink()
            path.replace(backup)
    except OSError:
        pass


def spawn_detached(command: list[str], log_name: str, console: Console | None = None) -> int | None:
    """Run *command* detached from the current console, logging to *log_name*."""
    log = cfgmod.home_dir() / log_name
    _rotate_log(log)
    try:
        handle = open(log, "a", encoding="utf-8")
    except OSError:
        handle = None
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": handle or subprocess.DEVNULL,
        "stderr": subprocess.STDOUT if handle else subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        if console:
            console.error(f"cannot start engine: {exc}")
        return None
    finally:
        if handle:
            handle.close()
    return process.pid


def spawn_worker(console: Console | None = None) -> int | None:
    """Start the Python worker detached from the current console."""
    return spawn_detached(worker_command(), "worker.log", console)


def spawn_engine(engine: engines.EngineInfo, config: dict[str, Any], console: Console | None = None) -> int | None:
    """Start whichever engine was resolved, detached from the console.

    Returns the engine's PID, or None when it could not be started (the caller
    is then expected to fall back to the Python worker).
    """
    if engine.name == "native" and engine.path:
        command = engines.native_launch_args(config, Path(engine.path))
        return spawn_detached(command, "native.log", console)
    if engine.name == "java" and engine.path:
        java = engines.java_executable()
        if java is None:
            return None
        command = engines.java_launch_args(java, Path(engine.path), config)
        return spawn_detached(command, "agent.log", console)
    return spawn_worker(console)


def read_engine_leftovers(engine_name: str | None, state: dict[str, Any]) -> list[BoostRecord]:
    """Boosts a previous session may have left behind, from any engine.

    Every engine keeps its own rollback record, and they are all read here:

    ``state.json``  the Python worker's ``boosted`` map
    ``agent.json``  the Java agent's report
    ``native-state.tsv``  the C++ engine's rollback file

    Reading all three means ``s-cpu off`` is a complete rollback no matter which
    engine ran, and no matter whether it exited cleanly at all.
    """
    records: dict[int, BoostRecord] = {}

    for _key, payload in (state.get("boosted") or {}).items():
        try:
            record = BoostRecord.from_dict(payload)
        except (TypeError, ValueError):
            continue
        records[record.pid] = record

    home = cfgmod.home_dir()

    agent_path = home / "agent.json"
    if agent_path.exists():
        try:
            import json

            with agent_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for item in payload.get("boosted") or []:
                record = BoostRecord.from_dict(item)
                records.setdefault(record.pid, record)
        except (OSError, ValueError, TypeError):
            pass

    for record in _read_native_state(home / "native-state.tsv"):
        records.setdefault(record.pid, record)

    return list(records.values())


def _read_native_state(path: Path) -> list[BoostRecord]:
    """Parse the C++ engine's tab separated rollback file.

    Format (see ``native/scpu_watch.cpp``)::

        pid<TAB>priority_before<TAB>name<TAB>tid:prio,tid:prio,...
    """
    try:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    records: list[BoostRecord] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            priority = int(parts[1])
        except ValueError:
            continue
        threads: list[list[int]] = []
        if len(parts) >= 4 and parts[3].strip():
            for item in parts[3].split(","):
                if ":" not in item:
                    continue
                tid_text, _, prio_text = item.partition(":")
                try:
                    threads.append([int(tid_text), int(prio_text)])
                except ValueError:
                    continue
        records.append(
            BoostRecord(
                pid=pid,
                name=parts[2].strip(),
                priority_before=PRIORITY_CLASS_NAMES.get(priority, str(priority)),
                thread_priorities=threads,
                reason="native-rollback",
            )
        )
    return records


def sweep_rollback_files(kept: list[BoostRecord], console: Console | None = None) -> None:
    """Delete the engines' rollback files once their records have been undone.

    Leaving a consumed rollback file behind is not harmless. The next ``s-cpu
    off`` would revert the same records a second time - and by then the recorded
    PID may belong to a completely different process, so a stale file can end up
    modifying something the user never asked to touch. Windows recycles PIDs
    eagerly, so this is a matter of when, not if.

    A record that could *not* be reverted is the exception: the files are then
    left alone so the operation can be retried, at the cost of replaying the
    reverts that did succeed. That is the safer of the two failure modes.
    """
    if kept:
        if console:
            console.debug(
                f"{len(kept)} boost record(s) could not be reverted; "
                "rollback files kept for a retry"
            )
        return

    home = cfgmod.home_dir()
    for name in ("agent.json", "native-state.tsv"):
        path = home / name
        try:
            if path.exists():
                path.unlink()
                if console:
                    console.debug(f"removed consumed rollback file {name}")
        except OSError:
            # A file we cannot remove is a cosmetic problem, not a correctness
            # one: the next run reverts an already-reverted process, which is a
            # no-op for everything except a recycled PID.
            if console:
                console.debug(f"could not remove {path}")


def stop_worker(state: dict[str, Any], backend: Any, timeout: float = 12.0, console: Console | None = None) -> str:
    """Ask the worker to exit, escalating to a hard kill if it ignores us.

    Returns ``"absent"``, ``"graceful"`` or ``"forced"``.
    """
    pid = state.get("worker_pid")
    if not pid:
        return "absent"
    pid = int(pid)
    if not backend.is_alive(pid):
        return "absent"

    flag = cfgmod.stop_flag_path()
    with contextlib.suppress(OSError):
        flag.write_text("stop", encoding="utf-8")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not backend.is_alive(pid):
            with contextlib.suppress(OSError):
                flag.unlink()
            return "graceful"
        time.sleep(0.2)

    if console:
        console.warn(t("off.worker_forced"))
    backend.terminate(pid)
    for _ in range(20):
        if not backend.is_alive(pid):
            break
        time.sleep(0.1)
    with contextlib.suppress(OSError):
        flag.unlink()
    return "forced"


class Worker:
    """The scan/boost/maintain loop."""

    def __init__(self, config: dict[str, Any] | None = None, console: Console | None = None) -> None:
        self.config = config or cfgmod.load_config()
        self.console = console or Console(quiet=True)
        self.backend = get_backend(self.config, console=self.console)
        self.state = cfgmod.load_state()
        self.records: dict[int, BoostRecord] = {}
        self.last_check: dict[int, float] = {}
        self._foreground_pid = 0
        self._running = True
        self._log_file = None
        self._last_state_write = 0.0
        self._last_scan = 0.0

    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        self.console.debug(message)
        try:
            if self._log_file is None:
                self._log_file = open(cfgmod.log_path(), "a", encoding="utf-8")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self._log_file.write(f"{stamp} {message}\n")
            self._log_file.flush()
        except OSError:
            pass

    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def handler(_signum, _frame):  # pragma: no cover - signal driven
            self._running = False

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            value = getattr(signal, name, None)
            if value is None:
                continue
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                signal.signal(value, handler)

    def _stop_requested(self) -> bool:
        return cfgmod.stop_flag_path().exists()

    # ------------------------------------------------------------------
    def _all_targets(self) -> list[ProcessInfo]:
        return self.backend.list_processes()

    def _scan_once(self) -> tuple[int, int]:
        """One pass over the process list. Returns ``(new, total_managed)``."""
        new_boosts = 0
        me = os.getpid()
        for process in self._all_targets():
            pid = process.pid
            if pid == me or pid <= 0:
                continue
            if pid in self.records:
                continue
            reason = self.backend.match_reason(process)
            if reason is None:
                continue
            if self._boost(process, reason):
                new_boosts += 1
        return new_boosts, len(self.records)

    def _boost(self, process: ProcessInfo, reason: str) -> bool:
        started = time.perf_counter()
        try:
            record = self.backend.boost(process)
        except Exception as exc:
            self.log(f"boost failed for {process.name} ({process.pid}): {exc!r}")
            return False
        if record is None:
            self.log(f"skip {process.name} ({process.pid}): not accessible")
            return False
        record.reason = reason
        self.records[process.pid] = record
        self.last_check[process.pid] = time.time()
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.log(
            f"boosted {record.name} ({process.pid}) [{reason}] - priority "
            f"{record.priority_before} -> {record.priority_after}, "
            f"{record.threads_raised}/{record.threads_total} threads, {elapsed_ms:.0f} ms"
        )
        return True

    def _release(self, pid: int) -> bool:
        record = self.records.pop(pid, None)
        self.last_check.pop(pid, None)
        if record is None:
            return False
        try:
            return bool(self.backend.unboost(record))
        except Exception as exc:
            self.log(f"unboost failed for {record.name} ({pid}): {exc!r}")
            return False

    def _tick_foreground(self) -> int:
        """React to focus changes without paying for a full process scan.

        ``GetForegroundWindow`` + ``GetWindowThreadProcessId`` is two syscalls,
        so this can run many times a second. When the user alt-tabs away from a
        process we boosted only because it was focused, the boost is released,
        which is the half of the trick that actually changes who wins.
        """
        if self.backend.mode() != "foreground":
            return 0
        pid = self.backend.foreground_pid()
        if pid == self._foreground_pid:
            return 0
        previous, self._foreground_pid = self._foreground_pid, pid
        changes = 0

        if (
            previous
            and previous in self.records
            and self.records[previous].reason == "foreground"
            and self._release(previous)
        ):
            changes += 1
            self.log(f"released {previous} (focus moved away)")

        if pid and pid not in self.records:
            process = self.backend.process_info(pid)
            if process is not None and self.backend.match_reason(process) == "foreground" and self._boost(
                process, "foreground"
            ):
                changes += 1
        return changes

    def _maintain(self) -> None:
        """Prune dead processes and re-apply boosts that the OS dropped."""
        interval = float(self.config.get("reboost_interval") or 20.0)
        now = time.time()
        for pid in list(self.records):
            record = self.records[pid]
            if not self.backend.is_alive(pid):
                self.records.pop(pid, None)
                self.last_check.pop(pid, None)
                continue
            if now - self.last_check.get(pid, 0.0) < interval:
                continue
            self.last_check[pid] = now
            try:
                fresh = self.backend.boost(ProcessInfo(pid, record.name, record.exe))
            except Exception:
                continue
            if fresh is not None:
                self._merge(record, fresh)

    @staticmethod
    def _merge(original: BoostRecord, fresh: BoostRecord) -> None:
        """Fold a re-boost into the existing record without losing history.

        ``priority_before`` and the recorded thread priorities describe the
        state *before SpeedyCPU ever touched the process*, so they must never
        be overwritten by a later measurement.
        """
        original.priority_after = fresh.priority_after or original.priority_after
        original.threads_raised = fresh.threads_raised
        original.threads_total = fresh.threads_total
        known = {int(tid) for tid, _ in original.thread_priorities}
        for tid, prio in fresh.thread_priorities:
            if int(tid) not in known:
                original.thread_priorities.append([int(tid), int(prio)])
                known.add(int(tid))
        original.ecoqos_disabled = original.ecoqos_disabled or fresh.ecoqos_disabled
        if original.affinity_before is None and fresh.affinity_before is not None:
            original.affinity_before = fresh.affinity_before
            original.affinity_cores_added = fresh.affinity_cores_added

    # ------------------------------------------------------------------
    def _persist(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_state_write < 2.0:
            return
        self._last_state_write = now
        self.state["boosted"] = {str(pid): record.to_dict() for pid, record in self.records.items()}
        self.state["stats"] = {
            "processes_boosted": len(self.records),
            "threads_raised": sum(record.threads_raised for record in self.records.values()),
            "last_scan": cfgmod.utc_timestamp(),
        }
        cfgmod.save_state(self.state)

    def _revert_all(self) -> int:
        reverted = 0
        for pid, record in list(self.records.items()):
            try:
                if self.backend.unboost(record):
                    reverted += 1
            except Exception as exc:
                self.log(f"unboost failed for {record.name} ({pid}): {exc!r}")
            self.records.pop(pid, None)
        return reverted

    # ------------------------------------------------------------------
    def run(self) -> int:
        self._install_signal_handlers()
        self._last_state_write = 0.0
        self.state.update(
            {
                "enabled": True,
                "worker_pid": os.getpid(),
                "started_at": self.state.get("started_at") or cfgmod.utc_timestamp(),
            }
        )
        # Clear a stale stop flag from a previous run.
        with contextlib.suppress(OSError):
            cfgmod.stop_flag_path().unlink()

        timer_ms = int(self.config.get("timer_resolution") or 0)
        before = self.backend.timer_resolution_ms()
        if timer_ms > 0 and self.backend.hold_timer_resolution(timer_ms):
            after = self.backend.timer_resolution_ms()
            self.state["timer_ms"] = timer_ms
            self.log(f"timer resolution {before} ms -> {after} ms")

        self.state["system_applied"] = self.state.get("system_applied") or []
        self.log(f"worker {os.getpid()} started, scope={self.backend.describe_scope()}")

        reverted = 0
        try:
            while self._running and not self._stop_requested():
                try:
                    self.config = cfgmod.load_config()
                    self.backend.config = self.config
                    interval = float(self.config.get("poll_interval") or 3.0)
                except Exception:
                    interval = 3.0

                # Cheap focus check runs at the fast tick rate; the expensive
                # process enumeration only every ``poll_interval``.
                try:
                    focus_changes = self._tick_foreground()
                except Exception as exc:
                    self.log(f"foreground tick error: {exc!r}")
                    focus_changes = 0

                now = time.monotonic()
                if now - self._last_scan >= interval:
                    self._last_scan = now
                    try:
                        new_boosts, managed = self._scan_once()
                        self._maintain()
                    except Exception as exc:
                        self.log(f"scan error: {exc!r}")
                        new_boosts, managed = 0, len(self.records)
                    self._persist(force=bool(new_boosts))
                    if new_boosts:
                        self.log(f"+{new_boosts} boosted, {managed} under management")
                elif focus_changes:
                    self._persist(force=True)

                time.sleep(FAST_TICK_SECONDS)
        finally:
            reverted = self._revert_all()
            self.backend.release_timer_resolution()
            # NOTE: ``system_applied`` is owned by the CLI, not the worker. The
            # worker must leave it untouched so ``s-cpu off`` can still undo the
            # power plan change after the worker has exited.
            self.state.update(
                {
                    "enabled": False,
                    "worker_pid": None,
                    "boosted": {},
                    "timer_ms": None,
                    "last_reverted": reverted,
                }
            )
            self.state["stats"]["processes_boosted"] = 0
            self._persist(force=True)
            with contextlib.suppress(OSError):
                cfgmod.stop_flag_path().unlink()
            self.log(f"worker stopped, reverted {reverted} process(es)")
            if self._log_file:
                with contextlib.suppress(OSError):
                    self._log_file.close()
        return 0


def run_worker() -> int:
    """Entry point used by the detached process."""
    try:
        return Worker().run()
    except KeyboardInterrupt:  # pragma: no cover
        return 0
