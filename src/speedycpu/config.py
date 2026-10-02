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

"""Configuration and persistent state handling.

Two JSON files live side by side in the SpeedyCPU home directory:

``config.json``
    User preferences, edited by ``s-cpu config set`` or by hand.
``state.json``
    Runtime bookkeeping written by ``s-cpu on`` / the background worker: which
    power plan was active before, which processes are currently boosted, etc.
    This is what makes ``s-cpu off`` able to put the machine back exactly the
    way it found it.

The home directory is resolved in this order: ``$SPEEDYCPU_HOME`` →
``%LOCALAPPDATA%\\SpeedyCPU`` (Windows) → ``~/Library/Application Support/SpeedyCPU``
(macOS) → ``$XDG_CONFIG_HOME/speedycpu`` or ``~/.config/speedycpu`` (Linux).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .i18n import t

__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_STATE",
    "coerce_value",
    "config_path",
    "home_dir",
    "load_config",
    "load_state",
    "log_path",
    "reset_config",
    "reset_state",
    "save_config",
    "save_state",
    "set_config_value",
    "state_path",
    "stop_flag_path",
]

# Critical OS processes we refuse to touch even when the user asks for '*'.
# Boosting these buys nothing and can destabilise the machine.
NEVER_TOUCH = (
    "system",
    "system idle process",
    "idle",
    "registry",
    "memory compression",
    "secure system",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "audiodg.exe",
    "wudfhost.exe",
    "speedycpu.exe",
    "s-cpu.exe",
    "speedycpu",
)

# Default exclusions for ``targets = ["*"]`` mode. These are background noise:
# raising their priority starves the very program the user wants to feel snappy.
DEFAULT_EXCLUDE = [
    "system",
    "registry",
    "memory compression",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "dwm.exe",
    "conhost.exe",
    "ctfmon.exe",
    "sihost.exe",
    "taskhostw.exe",
    "runtimebroker.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "searchhost.exe",
    "searchindexer.exe",
    "searchapp.exe",
    "msmpeng.exe",
    "mssense.exe",
    "nissrv.exe",
    "securityhealthservice.exe",
    "securityhealthsystray.exe",
    "wmiprvse.exe",
    "mousocoreworker.exe",
    "usoclient.exe",
    "tiworker.exe",
    "trustedinstaller.exe",
    "backgroundtaskhost.exe",
    "applicationframehost.exe",
    "sppsvc.exe",
    "dllhost.exe",
    "compattelrunner.exe",
    "widgetservice.exe",
    "widgets.exe",
    "phoneexperiencehost.exe",
    "yourphone.exe",
    "gamebar.exe",
    "gamebarftserver.exe",
    "speedycpu",
]

DEFAULT_CONFIG: dict[str, Any] = {
    # UI
    "lang": None,  # None -> autodetect from the OS locale
    # Which engine runs the boost loop:
    #   "auto"   - native (C++) if built, else java, else python
    #   "native" - scpu-watch.exe   ~2 MB RSS,  event driven
    #   "java"   - scpu-agent.jar   ~60 MB RSS, event driven
    #   "python" - built in worker  ~15 MB RSS, 200 ms polling
    "engine": "auto",
    # Number of keep-alive clock-hold threads for the Java engine. 0 = off.
    # They cost idle power, so this is opt-in and documented as such.
    "turbo_threads": 0,
    # Ask the native engine for a per-process trace in the log.
    "verbose_engine": False,
    # Which processes get boosted.
    #   "foreground" - only the focused window (plus any patterns in targets)
    #   "all"        - every process that is not excluded
    # "foreground" is the default on purpose: raising *every* process to high
    # priority leaves their relative shares unchanged, which is why so many
    # "boosters" do nothing measurable. Letting the focused app win does help.
    "mode": "foreground",
    "targets": [],
    "exclude": list(DEFAULT_EXCLUDE),
    # How hard we push.
    "priority": "high",  # "above_normal" | "high"
    "raise_threads": True,
    "disable_ecoqos": True,
    "reset_affinity": True,
    # "aggressive" pins each boosted process' working set so the memory manager
    # cannot trim it while the user alt-tabs away. Effective, but it holds RAM
    # hostage; off by default and documented as such.
    "aggressive": False,
    # System level knobs.
    "power_plan": "high_performance",  # "none" | "high_performance" | "ultimate"
    # Tune the *current* power scheme's processor settings (minimum processor
    # state, boost mode, core parking). This is the reliable half: unlike
    # switching plans it works without administrator rights.
    "cpu_tuning": True,
    "timer_resolution": 1,  # milliseconds; 0 disables
    "global_power_throttling_off": False,  # needs admin, writes to HKLM
    # Worker behaviour.
    "poll_interval": 3.0,  # seconds between process scans
    "reboost_interval": 20.0,  # seconds before a process is re-checked
    "repair_after_ms": 1500,  # give up on a process after this long
}

DEFAULT_STATE: dict[str, Any] = {
    "enabled": False,
    "started_at": None,
    "worker_pid": None,
    #: Which engine owns the running session: native | java | python.
    "engine": None,
    "previous_power_plan": None,
    "previous_power_plan_name": None,
    "previous_global_throttling": None,
    "system_applied": [],
    "timer_ms": None,
    "foreground_pid": None,
    "boosted": {},  # str(pid) -> record
    "stats": {"processes_boosted": 0, "threads_raised": 0, "last_scan": None},
}


def _env_path(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def home_dir() -> Path:
    """Return (and create) the SpeedyCPU home directory."""
    override = _env_path("SPEEDYCPU_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        base = _env_path("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "SpeedyCPU"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "SpeedyCPU"
    else:
        base = _env_path("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "speedycpu"
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return home_dir() / "config.json"


def state_path() -> Path:
    return home_dir() / "state.json"


def log_path() -> Path:
    return home_dir() / "worker.log"


def stop_flag_path() -> Path:
    return home_dir() / "stop.flag"


def native_state_path() -> Path:
    """Rollback file written by the C++ engine while it is boosting."""
    return home_dir() / "native-state.tsv"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic-ish write: temp file + replace, so a crash can't truncate state."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _merge_known(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only recognised keys so a stale config can never inject junk."""
    cfg = dict(DEFAULT_CONFIG)
    for key, value in raw.items():
        if key in DEFAULT_CONFIG:
            cfg[key] = value
    return cfg


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        save_config(DEFAULT_CONFIG, path)
        return dict(DEFAULT_CONFIG)
    return _merge_known(_read_json(path))


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    _write_json(path or config_path(), _merge_known(cfg))


def reset_config(path: Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    save_config(cfg, path)
    return cfg


_TRUE = {"1", "true", "yes", "y", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "n", "off", "disable", "disabled", ""}


def coerce_value(key: str, value: str) -> Any:
    """Cast a CLI string into the type of the matching default. Raises ValueError."""
    default = DEFAULT_CONFIG.get(key)
    if isinstance(default, bool):
        low = value.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(t("config.invalid_value", value=value, key=key, type="bool"))
    if isinstance(default, list):
        items = [item.strip() for item in value.split(",") if item.strip()]
        return items
    if isinstance(default, float):
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(t("config.invalid_value", value=value, key=key, type="float")) from exc
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(t("config.invalid_value", value=value, key=key, type="int")) from exc
    if key == "lang":
        return None if value.strip().lower() in {"auto", "none", ""} else value.strip()
    if value.strip().lower() in {"none", "null"}:
        return None
    return value


def set_config_value(key: str, value: str, path: Path | None = None) -> Any:
    if key not in DEFAULT_CONFIG:
        raise KeyError(t("config.no_key", key=key))
    parsed = coerce_value(key, value)
    cfg = load_config(path)
    cfg[key] = parsed
    save_config(cfg, path)
    return parsed


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def load_state(path: Path | None = None) -> dict[str, Any]:
    path = path or state_path()
    state = dict(DEFAULT_STATE)
    stored = _read_json(path)
    for key, value in stored.items():
        if key in DEFAULT_STATE and isinstance(DEFAULT_STATE[key], dict) and isinstance(value, dict):
            merged = dict(DEFAULT_STATE[key])
            merged.update(value)
            state[key] = merged
        else:
            state[key] = value
    return state


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    payload = dict(DEFAULT_STATE)
    payload.update(state)
    _write_json(path or state_path(), payload)


def reset_state(path: Path | None = None) -> dict[str, Any]:
    state = dict(DEFAULT_STATE)
    save_state(state, path)
    return state


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
