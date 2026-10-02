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

"""Platform backend registry.

Importing a backend pulls in platform specific modules, so the concrete
implementation is imported lazily inside :func:`get_backend`. That keeps
``import speedycpu`` working on every OS, including ones we do not support.
"""

from __future__ import annotations

import sys
from typing import Any

from .base import (
    PRIORITY_CLASS_NAMES,
    PRIORITY_CLASS_VALUES,
    Backend,
    BoostRecord,
    ProbeReport,
    ProcessInfo,
    SystemChange,
    is_protected,
    matches_any,
)

__all__ = [
    "PRIORITY_CLASS_NAMES",
    "PRIORITY_CLASS_VALUES",
    "Backend",
    "BoostRecord",
    "ProbeReport",
    "ProcessInfo",
    "SystemChange",
    "backend_kind",
    "get_backend",
    "is_protected",
    "matches_any",
    "supported_platforms",
]

_PLATFORM_BACKENDS = {
    "win32": "windows",
    "cygwin": "windows",
    "msys": "windows",
    "linux": "linux",
    "linux2": "linux",
    "darwin": "macos",
}


def backend_kind(platform: str | None = None) -> str | None:
    """Map a ``sys.platform`` value onto a backend module name."""
    return _PLATFORM_BACKENDS.get(platform or sys.platform)


def supported_platforms() -> tuple[str, ...]:
    return ("windows", "linux", "macos")


def get_backend(
    config: dict[str, Any],
    console: Any = None,
    dry_run: bool = False,
    kind: str | None = None,
) -> Backend:
    """Instantiate the backend for the current (or the given) platform.

    Raises :class:`NotImplementedError` with a translated message when the
    platform has no backend, which the CLI turns into a friendly error instead
    of a traceback.
    """
    kind = kind or backend_kind()
    if kind == "windows":
        from .windows import WindowsBackend

        return WindowsBackend(config, console=console, dry_run=dry_run)
    if kind == "linux":
        from .linux import LinuxBackend

        return LinuxBackend(config, console=console, dry_run=dry_run)
    if kind == "macos":
        from .macos import MacOSBackend

        return MacOSBackend(config, console=console, dry_run=dry_run)
    raise NotImplementedError(sys.platform)
