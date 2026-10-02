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

"""Console output helpers: ANSI colour, log levels and a dry-run aware writer.

Everything SpeedyCPU prints goes through :class:`Console` so that ``--quiet``,
``--no-color``, ``--json`` and ``NO_COLOR`` all behave consistently, and so the
background worker can redirect the same log stream into a file.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

__all__ = ["Console", "supports_color"]

_COLOR_KEYS = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "gray": "\x1b[90m",
}

# Terminal output is deliberately plain ASCII: no pictographs, no dingbats, no
# box drawing. A log line has to render identically in cmd.exe, in PowerShell,
# in a CI log and in a redirected file, so each severity carries a bracketed
# tag and nothing else.
_LEVEL_STYLE = {
    "ok": ("green", "[ OK ]"),
    "info": ("cyan", "[INFO]"),
    "step": ("blue", "[STEP]"),
    "warn": ("yellow", "[WARN]"),
    "error": ("red", "[FAIL]"),
    "debug": ("gray", "[TRACE]"),
}


def _enable_windows_ansi(stream: TextIO) -> bool:
    """Try to switch a Windows console into VT mode. Returns True on success."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11 if stream is sys.stdout else -12)
        mode = wt.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:  # pragma: no cover - non-console streams, exotic hosts
        return False


def supports_color(stream: TextIO | None = None, override: bool | None = None) -> bool:
    if override is not None:
        return override
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("SPEEDYCPU_FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    try:
        if not stream.isatty():
            # ConEmu / ANSICON / Windows Terminal set these even for pipes.
            return bool(os.environ.get("ANSICON") or os.environ.get("WT_SESSION"))
    except (AttributeError, ValueError):
        return False
    return _enable_windows_ansi(stream)


def display_width(text: str) -> int:
    """Terminal cell width of *text*.

    CJK glyphs occupy two cells, so ``f"{label:<20}"`` misaligns every column
    as soon as a Chinese label appears. This counts what the terminal counts.
    """
    width = 0
    for char in text:
        code = ord(char)
        if code == 0:
            continue
        if (
            0x1100 <= code <= 0x115F
            or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE6F
            or 0xFF00 <= code <= 0xFF60
            or 0xFFE0 <= code <= 0xFFE6
            or 0x1F300 <= code <= 0x1FAFF
            or 0x20000 <= code <= 0x3FFFD
        ):
            width += 2
        elif 0x0300 <= code <= 0x036F:  # combining marks
            continue
        else:
            width += 1
    return width


def pad(text: str, width: int) -> str:
    """Left align *text* to *width* terminal cells."""
    return text + " " * max(0, width - display_width(text))


class Console:
    """Small logging facade used across the whole project."""

    def __init__(
        self,
        stream: TextIO | None = None,
        color: bool | None = None,
        quiet: bool = False,
        verbose: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.stream = stream or sys.stdout
        self.color = supports_color(self.stream, color)
        self.quiet = quiet
        self.verbose = verbose
        self.dry_run = dry_run

    # -- low level ---------------------------------------------------------
    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        prefix = "".join(_COLOR_KEYS.get(s, "") for s in styles)
        return f"{prefix}{text}{_COLOR_KEYS['reset']}" if prefix else text

    def write(self, text: str = "") -> None:
        try:
            self.stream.write(text + "\n")
            self.stream.flush()
        except (ValueError, OSError):  # pragma: no cover - closed pipe / detached console
            pass

    # -- public API --------------------------------------------------------
    def line(self, text: str = "") -> None:
        self.write(text)

    def banner(self, title: str, subtitle: str = "") -> None:
        if self.quiet:
            return
        self.write()
        self.write(self.paint(f"  {title}", "bold", "cyan"))
        if subtitle:
            self.write(self.paint(f"  {subtitle}", "dim"))
        self.write(self.paint("  " + "-" * 52, "gray"))

    def _log(self, level: str, message: str, indent: int = 1) -> None:
        if self.quiet and level not in {"error", "warn"}:
            return
        if level == "debug" and not self.verbose:
            return
        style, glyph = _LEVEL_STYLE.get(level, ("cyan", "[INFO]"))
        if self.dry_run and level in {"ok", "info", "step"}:
            message = f"[dry-run] {message}"
        pad = "  " * max(indent, 0)
        glyph_text = self.paint(f"{glyph}", style)
        self.write(f"{pad}{glyph_text} {message}")

    def ok(self, message: str, indent: int = 1) -> None:
        self._log("ok", message, indent)

    def info(self, message: str, indent: int = 1) -> None:
        self._log("info", message, indent)

    def step(self, message: str, indent: int = 1) -> None:
        self._log("step", message, indent)

    def warn(self, message: str, indent: int = 1) -> None:
        self._log("warn", message, indent)

    def error(self, message: str, indent: int = 1) -> None:
        self._log("error", message, indent)

    def debug(self, message: str, indent: int = 2) -> None:
        self._log("debug", message, indent)

    def field(self, label: str, value: Any, indent: int = 1, width: int = 22) -> None:
        """Print an aligned ``label: value`` row (used by status/doctor)."""
        if self.quiet:
            return
        pad_text = "  " * max(indent, 0)
        label_text = self.paint(pad(label, width), "dim")
        self.write(f"{pad_text}{label_text}{value}")

    def kv_table(self, rows: list[tuple[str, Any]], indent: int = 1, width: int = 22) -> None:
        for label, value in rows:
            self.field(label, value, indent=indent, width=width)

    def dump_json(self, payload: Any) -> None:
        self.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
