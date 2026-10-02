# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors
"""Frozen-build entry point.

PyInstaller needs a real script rather than a package to point at, and this
also gives the frozen binary a place to tweak ``sys.path`` before the CLI runs.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    # Windows consoles default to a legacy code page; the CLI prints UTF-8
    # (Chinese labels, box drawing). Doing this here covers the frozen build
    # without asking the user to run ``chcp``.
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    from speedycpu.cli import main

    sys.exit(main())
