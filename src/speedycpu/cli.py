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

"""The ``s-cpu`` command line interface.

The two commands that matter to a user are ``s-cpu on`` and ``s-cpu off``.
Everything else is diagnostics or fine tuning:

======================  ========================================================
``s-cpu on``            apply every optimisation and start the background worker
``s-cpu off``           stop the worker and put the machine back as it was
``s-cpu status``        what is currently on, and what is boosted
``s-cpu boost NAME``    boost one program right now, worker or no worker
``s-cpu unboost [NAME]`` undo boosts (all of them when NAME is omitted)
``s-cpu targets ...``   manage the include patterns and the exclusion list
``s-cpu doctor``        environment self-check, explains what is unavailable
``s-cpu config ...``    read/write the configuration file
``s-cpu version``       print the version
======================  ========================================================

Exit codes: ``0`` success, ``1`` runtime failure, ``2`` bad usage.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
from typing import Any

from . import __version__, engines, i18n
from . import config as cfgmod
from .backends import BoostRecord, ProcessInfo, SystemChange, get_backend, matches_any
from .console import Console
from .i18n import t
from .worker import (
    Worker,
    read_engine_leftovers,
    spawn_engine,
    stop_worker,
    sweep_rollback_files,
    worker_command,
)

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

#: How long to wait for the freshly spawned worker to report in.
WORKER_HANDSHAKE_TIMEOUT = 6.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _backend_or_die(console: Console, config: dict[str, Any], dry_run: bool):
    try:
        return get_backend(config, console=console, dry_run=dry_run)
    except NotImplementedError:
        console.error(t("err.unsupported_platform", platform=sys.platform))
        raise SystemExit(EXIT_FAIL) from None


def _state_changes(state: dict[str, Any]) -> list[SystemChange]:
    return [SystemChange.from_dict(item) for item in state.get("system_applied") or []]


def _state_records(state: dict[str, Any]) -> dict[int, BoostRecord]:
    records: dict[int, BoostRecord] = {}
    for key, payload in (state.get("boosted") or {}).items():
        try:
            records[int(key)] = BoostRecord.from_dict(payload)
        except (TypeError, ValueError):
            continue
    return records


def _match_processes(backend, spec: str) -> list[ProcessInfo]:
    """Resolve a CLI target (a PID, a name, or a glob) to running processes."""
    spec = (spec or "").strip()
    if not spec:
        return []
    found: list[ProcessInfo] = []
    if spec.isdigit():
        pid = int(spec)
        for process in backend.list_processes():
            if process.pid == pid:
                return [process]
        return []
    for process in backend.list_processes():
        if matches_any(process.name, [spec]):
            found.append(process)
    return found


def _scope_text(backend) -> str:
    """Translate the backend's scope description into the UI language."""
    scope = backend.describe_scope()
    prefix = "foreground window"
    if scope.startswith(prefix):
        return t("scope.foreground") + scope[len(prefix) :]
    if scope == "all user processes":
        return t("scope.all")
    return scope


def _engine_note(info: engines.EngineInfo) -> str:
    """Localised engine description, falling back to the technical string."""
    if info.note_key:
        return t(info.note_key, **(info.note_args or {}))
    return info.note


def _apply_overrides(config: dict[str, Any], args: argparse.Namespace, console: Console) -> dict[str, Any]:
    """Fold ``s-cpu on`` flags into the config and persist them."""
    changed = False
    if getattr(args, "targets", None):
        config["targets"] = [item.strip() for item in args.targets.split(",") if item.strip()]
        changed = True
    if getattr(args, "mode", None):
        config["mode"] = args.mode
        changed = True
    if getattr(args, "priority", None):
        config["priority"] = args.priority
        changed = True
    if getattr(args, "power_plan", None):
        config["power_plan"] = args.power_plan
        changed = True
    if getattr(args, "no_power_plan", False):
        config["power_plan"] = "none"
        changed = True
    if getattr(args, "no_cpu_tuning", False):
        config["cpu_tuning"] = False
        changed = True
    if getattr(args, "timer_ms", None) is not None:
        config["timer_resolution"] = max(0, int(args.timer_ms))
        changed = True
    if getattr(args, "aggressive", False):
        config["aggressive"] = True
        changed = True
    if getattr(args, "global_throttling", False):
        config["global_power_throttling_off"] = True
        changed = True
    if getattr(args, "engine", None):
        config["engine"] = args.engine
        changed = True
    if getattr(args, "turbo", None) is not None:
        config["turbo_threads"] = max(0, int(args.turbo))
        changed = True
    if changed:
        cfgmod.save_config(config)
        console.debug("config updated from command line flags")
    return config


def _confirm_engine(
    engine_name: str, pid: int, backend, console: Console, timeout: float = WORKER_HANDSHAKE_TIMEOUT
) -> int | None:
    """Make sure a freshly spawned engine is actually alive.

    The Python worker announces itself by writing its PID into ``state.json``;
    the native and Java engines are plain child processes, so the PID we got
    from the spawn call is already authoritative - only liveness needs checking.
    """
    if engine_name != "python":
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not backend.is_alive(pid):
                return None
            time.sleep(0.2)
        return pid if backend.is_alive(pid) else None

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = cfgmod.load_state()
        reported = state.get("worker_pid")
        if reported and backend.is_alive(int(reported)):
            return int(reported)
        time.sleep(0.15)
    return None


def _recover_previous_session(backend, console: Console, dry_run: bool) -> int:
    """Undo leftovers from a session that died without cleaning up.

    A crash, a Task Manager "End task" or a hard reboot can leave processes
    sitting at elevated priority forever. Reverting them here means the damage
    never accumulates across sessions.
    """
    records = read_engine_leftovers(None, cfgmod.load_state())
    if not records:
        return 0
    recovered = 0
    failed: list[BoostRecord] = []
    for record in records:
        if dry_run or backend.unboost(record):
            recovered += 1
        else:
            failed.append(record)
    if not dry_run:
        sweep_rollback_files(failed, console)
    if recovered and not dry_run:
        console.info(t("on.recovered", count=recovered))
    return recovered


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_on(args: argparse.Namespace, console: Console) -> int:
    config = _apply_overrides(cfgmod.load_config(), args, console)
    backend = _backend_or_die(console, config, args.dry_run)
    state = cfgmod.load_state()

    console.banner(t("app.name"), t("app.tagline"))

    if state.get("enabled") and state.get("worker_pid") and backend.is_alive(int(state["worker_pid"])):
        console.ok(t("on.already", pid=int(state["worker_pid"])))
        return EXIT_OK

    # A previous session may have died without reverting. Clean that up first so
    # boosts cannot pile up session after session.
    _recover_previous_session(backend, console, args.dry_run)

    engine = engines.resolve_engine(str(config.get("engine") or "auto"))
    console.info(t("on.engine", engine=engine.name, note=_engine_note(engine)))

    if not backend.is_elevated():
        console.warn(t("warn.not_admin", what="power plan / global throttling", hint=t("err.hint_admin")))

    # 1) machine-wide tweaks first, so the engine inherits a fast system.
    console.write()
    console.info(t("on.title"))
    timer_before = backend.timer_resolution_ms()
    changes = backend.apply_system()
    for change in changes:
        if change.skipped:
            console.debug(change.description)
        elif change.applied:
            console.ok(change.description)
        else:
            console.warn(change.description)
    if args.dry_run:
        for change in changes:
            console.warn(t("warn.dry_run", what=change.description))

    state["system_applied"] = [change.to_dict() for change in changes if change.applied]
    state["enabled"] = True
    state["engine"] = engine.name
    state["started_at"] = state.get("started_at") or cfgmod.utc_timestamp()

    console.info(t("on.mode", mode=_scope_text(backend)))

    if args.dry_run:
        console.warn(t("warn.dry_run", what="write state.json"))
        if engine.name == "python":
            console.warn(t("warn.dry_run", what=" ".join(worker_command())))
        else:
            console.warn(t("warn.dry_run", what=f"start the {engine.name} engine ({engine.path})"))
        console.write()
        console.ok(t("on.done"))
        return EXIT_OK

    # 2) The engine holds the timer resolution and reacts to focus changes.
    #    --no-daemon forces the in-process Python worker, which is the only
    #    engine that can sensibly run attached to this console.
    if args.no_daemon:
        console.info(t("on.worker.inline"))
        console.info(t("on.engine", engine="python", note=t("engine.in_process")))
        state["engine"] = "python"
        cfgmod.save_state(state)
        worker = Worker(config=config, console=console)
        worker.state = cfgmod.load_state()
        console.write()
        console.ok(t("on.done"))
        console.line("   " + t("on.done_off_hint"))
        return worker.run()

    cfgmod.save_state(state)

    pid = spawn_engine(engine, config, console)
    confirmed = _confirm_engine(engine.name, pid, backend, console) if pid else None
    if confirmed is None:
        if engine.name != "python":
            # Last resort: the Python engine is always present.
            console.warn(f"{engine.name} engine did not start; falling back to python")
            engine = engines.EngineInfo("python", True, note="fallback")
            state["engine"] = "python"
            cfgmod.save_state(state)
            pid = spawn_engine(engine, config, console)
            confirmed = _confirm_engine("python", pid, backend, console) if pid else None
        if confirmed is None:
            console.error("failed to start any boost engine")
            return EXIT_FAIL

    state = cfgmod.load_state()
    state["worker_pid"] = confirmed
    state["engine"] = engine.name
    state["enabled"] = True
    cfgmod.save_state(state)
    console.ok(t("on.worker", pid=confirmed))

    timer_after = backend.timer_resolution_ms()
    if timer_after is not None and timer_before is not None and timer_after < timer_before:
        console.ok(t("on.timer", old=f"{timer_before:.3f}", new=f"{timer_after:.3f}"))
    elif int(config.get("timer_resolution") or 0) > 0 and timer_after is not None:
        console.debug(f"timer resolution now {timer_after:.3f} ms")

    # 3) Report how many processes are already under management. The Python
    #    engine publishes that in state.json; the others write their own
    #    rollback files (agent.json / native-state.tsv), which are read here.
    #    The wait is short for the native and Java engines because both react to
    #    a focus event rather than waiting for a polling interval.
    boosted = 0
    budget = 5.0 if engine.name == "python" else 2.5
    deadline = time.time() + budget
    while time.time() < deadline:
        if engine.name == "python":
            boosted = int((cfgmod.load_state().get("stats") or {}).get("processes_boosted") or 0)
        else:
            boosted = len(read_engine_leftovers(engine.name, {}))
        if boosted:
            break
        time.sleep(0.25)
    if boosted:
        console.ok(f"{boosted} process(es) boosted on the first pass")

    console.write()
    console.ok(t("on.done"))
    console.line("   " + t("on.done_off_hint"))
    return EXIT_OK


def cmd_off(args: argparse.Namespace, console: Console) -> int:
    config = cfgmod.load_config()
    backend = _backend_or_die(console, config, args.dry_run)
    state = cfgmod.load_state()

    console.banner(t("app.name"), t("app.tagline"))
    console.info(t("off.title"))

    engine_name = str(state.get("engine") or "python")
    worker_pid = state.get("worker_pid")
    running = bool(worker_pid and backend.is_alive(int(worker_pid)))
    reverted = int(state.get("last_reverted") or 0)

    if running:
        if args.dry_run:
            console.warn(t("warn.dry_run", what=f"stop {engine_name} engine (pid {int(worker_pid)})"))
        else:
            outcome = stop_worker(state, backend, console=console)
            if outcome == "graceful":
                console.ok(t("off.engine_stopped", engine=engine_name, pid=int(worker_pid)))
            state = cfgmod.load_state()
            reverted = max(reverted, int(state.get("last_reverted") or 0))
    else:
        console.debug(t("err.no_daemon"))

    # Anything the engine could not undo (killed hard, or one-shot boosts) is
    # undone here so ``s-cpu off`` is always a complete rollback. Every engine's
    # record is swept: the Python state file, the Java agent's report and the
    # C++ engine's rollback file.
    leftovers = read_engine_leftovers(engine_name, state)
    failed: list[BoostRecord] = []
    for record in leftovers:
        # A dry run counts the record as handled; a real failure is collected so
        # its rollback file survives for a retry.
        if args.dry_run or backend.unboost(record):
            reverted += 1
        else:
            failed.append(record)
    if not args.dry_run:
        sweep_rollback_files(failed, console)
    if reverted:
        console.ok(t("off.reverted", count=reverted))
    for record in failed:
        console.warn(t("unboost.failed", what=f"{record.name} (pid {record.pid})"))

    changes = _state_changes(state)
    if changes:
        for change in backend.revert_system(changes):
            console.ok(change.description)
    elif not running and not leftovers:
        console.info(t("off.not_running"))

    if backend.release_timer_resolution():
        console.ok(t("off.timer_released"))

    if not args.dry_run:
        cfgmod.reset_state()
    console.write()
    console.ok(t("off.done"))
    return EXIT_OK


def cmd_status(args: argparse.Namespace, console: Console) -> int:
    config = cfgmod.load_config()
    backend = _backend_or_die(console, config, args.dry_run)
    state = cfgmod.load_state()
    engine_name = str(state.get("engine") or "") or None
    # Records may live in either engine's file; merge so the listing is complete.
    records = {record.pid: record for record in read_engine_leftovers(engine_name, state)}

    worker_pid = state.get("worker_pid")
    worker_alive = bool(worker_pid and backend.is_alive(int(worker_pid)))
    enabled = bool(state.get("enabled")) and worker_alive
    preferred = engines.resolve_engine(str(config.get("engine") or "auto"))

    timer = backend.timer_resolution_ms()
    plan = getattr(backend, "active_power_plan", lambda: None)()

    if args.json:
        console.dump_json(
            {
                "enabled": enabled,
                "engine": engine_name,
                "engine_available": [info.name for info in engines.engine_report() if info.available],
                "engine_preferred": preferred.name,
                "worker_pid": int(worker_pid) if worker_pid else None,
                "worker_alive": worker_alive,
                "started_at": state.get("started_at"),
                "mode": config.get("mode"),
                "targets": config.get("targets"),
                "turbo_threads": config.get("turbo_threads"),
                "power_plan": plan[1] if plan else None,
                "power_plan_guid": plan[0] if plan else None,
                "timer_resolution_ms": timer,
                "elevated": backend.is_elevated(),
                "boosted": {str(pid): rec.to_dict() for pid, rec in records.items()},
                "stats": state.get("stats"),
                "config_path": str(cfgmod.config_path()),
                "state_path": str(cfgmod.state_path()),
            }
        )
        return EXIT_OK

    console.banner(t("app.name"), t("app.tagline"))
    state_text = console.paint(t("common.on"), "bold", "green") if enabled else console.paint(
        t("common.off"), "bold", "yellow"
    )
    rows: list[tuple[str, Any]] = [(t("status.state"), state_text)]

    if state.get("started_at") and enabled:
        try:
            started = time.mktime(time.strptime(str(state["started_at"]), "%Y-%m-%dT%H:%M:%S"))
            rows.append((t("status.uptime"), cfgmod.human_duration(time.time() - started + time.timezone)))
        except (ValueError, OverflowError):
            rows.append((t("status.uptime"), state["started_at"]))

    if engine_name:
        rows.append((t("status.engine"), engine_name))
    else:
        rows.append((t("status.engine"), f"{preferred.name} ({_engine_note(preferred)})"))

    if worker_pid and worker_alive:
        rows.append((t("status.worker"), t("status.worker_pid", pid=int(worker_pid))))
    elif worker_pid:
        rows.append((t("status.worker"), console.paint(t("status.worker_dead", pid=int(worker_pid)), "yellow")))
    else:
        rows.append((t("status.worker"), t("common.none")))

    rows.append((t("status.mode"), _scope_text(backend)))
    targets = config.get("targets") or []
    rows.append((t("status.targets"), ", ".join(str(item) for item in targets) or t("common.none")))
    rows.append((t("status.admin"), t("common.yes") if backend.is_elevated() else t("common.no")))
    if plan:
        rows.append((t("status.power_plan"), f"{plan[1]} ({plan[0]})"))
    if timer is not None:
        label = t("status.timer_held", value=f"{timer:.3f}") if state.get("timer_ms") else t(
            "status.timer_default", value=f"{timer:.3f}"
        )
        rows.append((t("status.timer"), label))
    rows.append((t("status.config"), str(cfgmod.config_path())))
    console.kv_table(rows, width=20)

    # Engine availability is the first thing to check when something is slow to
    # react, so surface it here rather than only in ``doctor``.
    console.write()
    console.info(t("doctor.engines"))
    for info in engines.engine_report():
        mark = console.paint("available", "green") if info.available else console.paint("unavailable", "gray")
        console.write(f"    {info.name:<8} {mark}  {console.paint(_engine_note(info), 'dim')}")

    console.write()
    console.info(f"{t('status.boosted')} ({len(records)})")
    if not records:
        console.debug(t("status.no_boosted"))
    else:
        for record in sorted(records.values(), key=lambda item: item.name.lower())[: args.limit]:
            console.write(
                "    "
                + console.paint(record.name, "bold")
                + f" (pid {record.pid}) - {record.threads_raised}/{record.threads_total} threads @ "
                + console.paint(record.priority_after or "?", "green")
            )
        if len(records) > args.limit:
            console.debug(f"... and {len(records) - args.limit} more")
    return EXIT_OK


def cmd_boost(args: argparse.Namespace, console: Console) -> int:
    config = cfgmod.load_config()
    backend = _backend_or_die(console, config, args.dry_run)
    processes = _match_processes(backend, args.target)
    console.banner(t("app.name"), t("boost.title", target=args.target))

    if not processes:
        console.warn(t("boost.none"))
        return EXIT_FAIL

    state = cfgmod.load_state()
    records = state.get("boosted") or {}
    boosted = 0
    threads = 0
    for process in processes:
        if str(process.pid) in records:
            console.debug(t("boost.already", name=process.name, pid=process.pid))
            continue
        record = backend.boost(process, manual=True)
        if record is None:
            console.warn(t("err.permission", what=process.name, pid=process.pid))
            continue
        record.reason = "manual"
        records[str(process.pid)] = record.to_dict()
        boosted += 1
        threads += record.threads_raised
        console.ok(
            t(
                "boost.single",
                name=record.name,
                pid=record.pid,
                old=record.priority_before or "?",
                new=record.priority_after or "?",
                threads=record.threads_raised,
                affinity=record.affinity_cores_added or "-",
            )
        )

    state["boosted"] = records
    if not args.dry_run:
        cfgmod.save_state(state)
    console.write()
    console.ok(t("boost.done", count=boosted, threads=threads, priority=config.get("priority")))
    return EXIT_OK if boosted else EXIT_FAIL


def cmd_unboost(args: argparse.Namespace, console: Console) -> int:
    config = cfgmod.load_config()
    backend = _backend_or_die(console, config, args.dry_run)
    state = cfgmod.load_state()
    records = _state_records(state)

    console.banner(t("app.name"), t("app.tagline"))
    count = 0
    failed: list[str] = []
    for pid, record in list(records.items()):
        if args.target and not matches_any(record.name, [args.target]) and str(pid) != args.target:
            continue
        if args.dry_run or backend.unboost(record):
            count += 1
            # Only forget a record once the revert actually happened. Keeping a
            # failed one is what lets the user retry, and it keeps ``s-cpu off``
            # able to undo it even after the process has been re-boosted.
            records.pop(pid, None)
        else:
            failed.append(f"{record.name} (pid {pid})")

    state["boosted"] = {str(pid): record.to_dict() for pid, record in records.items()}
    if not args.dry_run:
        cfgmod.save_state(state)

    for item in failed:
        console.warn(t("unboost.failed", what=item))
    if count:
        console.ok(t("unboost.done", count=count))
        return EXIT_OK
    console.info(t("unboost.none"))
    return EXIT_FAIL


def cmd_targets(args: argparse.Namespace, console: Console) -> int:
    config = cfgmod.load_config()
    targets = list(config.get("targets") or [])
    console.banner(t("app.name"), t("targets.title"))

    if args.action == "list":
        if "*" in [str(item).strip() for item in targets]:
            console.info(t("targets.all"))
        elif not targets:
            console.info(t("targets.empty"))
        for pattern in targets:
            console.write("    " + console.paint(str(pattern), "bold"))
        console.write()
        exclude = list(config.get("exclude") or [])
        console.info(t("targets.excluded", count=len(exclude)))
        console.debug(", ".join(str(item) for item in exclude))
        console.write()
        console.info(t("on.mode", mode=_scope_text(_backend_or_die(console, config, args.dry_run))))
        return EXIT_OK

    if not args.pattern:
        console.error("a pattern is required, e.g.  s-cpu targets add chrome.exe")
        return EXIT_USAGE

    pattern = args.pattern.strip()
    if args.action == "add":
        if pattern in targets:
            console.info(t("targets.exists", pattern=pattern))
            return EXIT_OK
        targets.append(pattern)
        console.ok(t("targets.added", pattern=pattern))
    else:
        if pattern not in targets:
            console.warn(t("targets.missing", pattern=pattern))
            return EXIT_FAIL
        targets.remove(pattern)
        console.ok(t("targets.removed", pattern=pattern))

    config["targets"] = targets
    if not args.dry_run:
        cfgmod.save_config(config)
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, console: Console) -> int:
    config = cfgmod.load_config()
    backend = _backend_or_die(console, config, args.dry_run)
    report = backend.probe()

    if args.json:
        console.dump_json(
            {
                "platform": report.platform,
                "backend": report.backend,
                "python": report.python,
                "elevated": report.elevated,
                "cpu_count": report.cpu_count,
                "timer_resolution_ms": report.timer_resolution_ms,
                "engines": [
                    {"name": info.name, "available": info.available, "note": info.note, "path": info.path}
                    for info in engines.engine_report()
                ],
                "details": report.details,
                "problems": report.problems,
            }
        )
        return EXIT_FAIL if (args.strict and report.problems) else EXIT_OK

    console.banner(t("app.name"), t("doctor.title"))
    rows: list[tuple[str, Any]] = [
        (t("doctor.os"), report.platform),
        (t("doctor.python"), report.python),
        (t("doctor.backend"), report.backend),
        (t("doctor.cpu"), report.cpu_count),
        (
            t("doctor.admin"),
            console.paint(t("doctor.admin.yes"), "green")
            if report.elevated
            else console.paint(t("doctor.admin.no"), "yellow"),
        ),
    ]
    if report.timer_resolution_ms is not None:
        rows.append((t("status.timer"), f"{report.timer_resolution_ms:.3f} ms"))
    if "ultimate_available" in report.details:
        rows.append(
            (
                t("doctor.ultimate"),
                t("doctor.ultimate.yes") if report.details["ultimate_available"] else t("doctor.ultimate.no"),
            )
        )
    if report.details.get("global_throttling_off") is not None:
        rows.append(
            ("PowerThrottlingOff", t("common.yes") if report.details["global_throttling_off"] else t("common.no"))
        )
    console.kv_table(rows, width=22)

    console.write()
    console.info(t("doctor.engines"))
    for info in engines.engine_report():
        mark = console.paint("available", "green") if info.available else console.paint("unavailable", "gray")
        console.write(f"    {console.paint(info.name, 'bold'):<10} {mark:<12} {_engine_note(info)}")
    preferred = engines.resolve_engine(str(config.get("engine") or "auto"))
    console.debug(f"would use: {preferred.name}")

    plans = report.details.get("power_plans") or []
    if plans:
        console.write()
        console.info(f"{t('doctor.power_plans')} ({len(plans)})")
        for plan in plans:
            console.write("    " + str(plan))

    console.write()
    if report.problems:
        for problem in report.problems:
            console.warn(problem)
        console.write()
        console.warn(t("doctor.fail", count=len(report.problems)))
    else:
        console.ok(t("doctor.ok"))
    return EXIT_FAIL if (args.strict and report.problems) else EXIT_OK


def cmd_config(args: argparse.Namespace, console: Console) -> int:
    console.banner(t("app.name"), t("config.path", path=cfgmod.config_path()))
    if args.action == "path":
        console.line(str(cfgmod.config_path()))
        console.line(str(cfgmod.state_path()))
        return EXIT_OK
    if args.action == "reset":
        cfgmod.reset_config()
        console.ok(t("config.reset"))
        return EXIT_OK
    if args.action == "set":
        if not args.key:
            console.error("usage: s-cpu config set <key> <value>")
            return EXIT_USAGE
        try:
            value = cfgmod.set_config_value(args.key, args.value if args.value is not None else "")
        except KeyError as exc:
            console.error(str(exc.args[0]))
            console.write()
            console.info("valid keys: " + ", ".join(sorted(cfgmod.DEFAULT_CONFIG)))
            return EXIT_FAIL
        except ValueError as exc:
            console.error(str(exc))
            return EXIT_FAIL
        console.ok(t("config.key_set", key=args.key, value=value))
        return EXIT_OK

    config = cfgmod.load_config()
    console.write()
    for key in sorted(config):
        default = cfgmod.DEFAULT_CONFIG.get(key)
        marker = "" if config[key] == default else console.paint(" *", "yellow")
        console.line(f"    {console.paint(key, 'bold'):<28} {config[key]!r}{marker}")
    console.write()
    console.debug(t("config.state_path", path=cfgmod.state_path()))
    console.debug("* = differs from the default")
    return EXIT_OK


def cmd_version(_args: argparse.Namespace, console: Console) -> int:
    console.line(f"{t('app.name')} {__version__}")
    console.line(f"  python  {sys.version.split()[0]}")
    console.line(f"  backend {__import__('speedycpu.backends', fromlist=['x']).backend_kind()}")
    for info in engines.engine_report():
        mark = "available" if info.available else "unavailable"
        console.line(f"  engine  {info.name:<7} {mark:<12} {_engine_note(info)}")
    console.line(f"  config  {cfgmod.config_path()}")
    console.line(f"  worker  {' '.join(worker_command())}")
    return EXIT_OK


def cmd_worker(args: argparse.Namespace, console: Console) -> int:
    """Internal entry point used by the detached background process."""
    log_console = Console(quiet=True, verbose=args.verbose)
    return Worker(console=log_console).run()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s-cpu",
        description=(
            "SpeedyCPU - command line CPU responsiveness booster.\n"
            "SpeedyCPU —— 命令行 CPU 流畅度加速器。"
        ),
        epilog=(
            "quick start:\n"
            "  s-cpu on            enable the boost (开启加速)\n"
            "  s-cpu off           disable and restore everything (关闭并还原)\n"
            "  s-cpu status        show what is boosted (查看状态)\n"
            "  s-cpu doctor        explain what this machine supports (环境自检)\n"
            "docs: https://github.com/speedycpu/speedycpu"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"SpeedyCPU {__version__}")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    parser.add_argument("-q", "--quiet", action="store_true", help="only warnings and errors")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument(
        "--lang",
        choices=list(i18n.available_languages()),
        help="UI language; English is the default, 'zh' selects Simplified Chinese",
    )

    sub = parser.add_subparsers(dest="command")

    on = sub.add_parser("on", help="enable SpeedyCPU (开启加速)")
    on.add_argument("--targets", metavar="A,B", help="comma separated process patterns to boost")
    on.add_argument("--mode", choices=["all", "foreground"], help="boost everything, or only the focused window")
    on.add_argument("--priority", choices=["above_normal", "high"], help="target priority class")
    on.add_argument("--power-plan", metavar="SPEC", help="high_performance | ultimate | balanced | <GUID> | none")
    on.add_argument("--no-power-plan", action="store_true", help="leave the active power plan alone")
    on.add_argument("--no-cpu-tuning", action="store_true", help="do not touch processor power settings")
    on.add_argument("--timer-ms", type=float, metavar="MS", help="timer resolution to request (default 1, 0 = off)")
    on.add_argument("--aggressive", action="store_true", help="also pin each boosted process' working set")
    on.add_argument("--global-throttling", action="store_true", help="disable EcoQoS machine wide (needs admin)")
    on.add_argument("--no-daemon", action="store_true", help="run in the foreground instead of detaching")
    on.add_argument(
        "--engine",
        choices=["auto", "native", "java", "python"],
        help="which engine runs the boost loop (default: auto)",
    )
    on.add_argument(
        "--turbo",
        type=int,
        metavar="N",
        help="java engine: number of clock keep-alive threads (costs idle power)",
    )
    on.set_defaults(func=cmd_on)

    off = sub.add_parser("off", help="disable SpeedyCPU and restore everything (关闭并还原)")
    off.set_defaults(func=cmd_off)

    status = sub.add_parser("status", help="show current state (查看状态)")
    status.add_argument("--json", action="store_true", help="machine readable output")
    status.add_argument("--limit", type=int, default=15, help="how many boosted processes to list")
    status.set_defaults(func=cmd_status)

    boost = sub.add_parser("boost", help="boost one program right now")
    boost.add_argument("target", help="process name, glob or PID")
    boost.set_defaults(func=cmd_boost)

    unboost = sub.add_parser("unboost", help="undo boosts")
    unboost.add_argument("target", nargs="?", help="process name, glob or PID (default: all)")
    unboost.set_defaults(func=cmd_unboost)

    targets = sub.add_parser("targets", help="manage boost targets")
    targets.add_argument("action", choices=["list", "add", "remove"])
    targets.add_argument("pattern", nargs="?", help="process pattern, e.g. chrome.exe or game*")
    targets.set_defaults(func=cmd_targets)

    doctor = sub.add_parser("doctor", help="environment self-check (环境自检)")
    doctor.add_argument("--json", action="store_true", help="machine readable output")
    doctor.add_argument("--strict", action="store_true", help="exit non-zero when a problem is found")
    doctor.set_defaults(func=cmd_doctor)

    config = sub.add_parser("config", help="inspect or edit the configuration")
    config.add_argument("action", nargs="?", choices=["show", "path", "set", "reset"], default="show")
    config.add_argument("key", nargs="?", help="config key (for 'set')")
    config.add_argument("value", nargs="?", help="config value (for 'set')")
    config.set_defaults(func=cmd_config)

    version = sub.add_parser("version", help="print version information")
    version.set_defaults(func=cmd_version)

    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.set_defaults(func=cmd_worker)

    return parser


#: Top level flags. argparse only accepts these *before* the subcommand, but
#: people write ``s-cpu on --dry-run`` and ``s-cpu status --no-color``. So they
#: are hoisted back to the front before parsing (see :func:`_normalise_argv`).
GLOBAL_BOOL_FLAGS = frozenset({"--dry-run", "--no-color", "--quiet", "-q", "--verbose", "-v"})
GLOBAL_VALUE_FLAGS = frozenset({"--lang"})
GLOBAL_FLAGS = GLOBAL_BOOL_FLAGS | GLOBAL_VALUE_FLAGS

SUBCOMMANDS = frozenset(
    {"on", "off", "status", "boost", "unboost", "targets", "doctor", "config", "version", "_worker"}
)


def _normalise_argv(argv: list[str]) -> list[str]:
    """Make global flags work on either side of the subcommand.

    ``argparse`` stops looking at the top level parser once it sees a
    subcommand, so ``s-cpu on --dry-run`` would fail with "unrecognized
    arguments" unless the flags are moved in front of ``on``. Moving them keeps
    the documented spelling working without duplicating every flag on every
    subparser.
    """
    if not argv:
        return argv
    position = next((index for index, token in enumerate(argv) if token in SUBCOMMANDS), None)
    if position is None:
        return argv

    head = list(argv[:position])
    hoisted: list[str] = []
    kept: list[str] = []
    body = argv[position:]
    index = 0
    while index < len(body):
        token = body[index]
        if token in GLOBAL_VALUE_FLAGS:
            hoisted.append(token)
            if index + 1 < len(body):
                hoisted.append(body[index + 1])
                index += 1
        elif token.startswith("--lang=") or any(token.startswith(flag + "=") for flag in GLOBAL_BOOL_FLAGS) or token in GLOBAL_BOOL_FLAGS:
            hoisted.append(token)
        else:
            kept.append(token)
        index += 1
    return head + hoisted + kept


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalise_argv(raw))

    config: dict[str, Any] = {}
    with contextlib.suppress(OSError):
        config = cfgmod.load_config()
    i18n.set_language(i18n.detect_language(config.get("lang"), args.lang))
    console = Console(
        color=False if args.no_color else None,
        quiet=args.quiet,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        return int(args.func(args, console))
    except KeyboardInterrupt:
        console.write()
        console.warn("interrupted")
        return EXIT_FAIL
    except NotImplementedError:
        console.error(t("err.unsupported_platform", platform=sys.platform))
        return EXIT_FAIL
    except PermissionError as exc:
        console.error(t("err.not_admin"))
        console.debug(str(exc))
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
