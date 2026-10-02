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

"""Shared fixtures.

The most important one is :func:`home`: every test that touches configuration
or state must never see (or write) the real ``%LOCALAPPDATA%\\SpeedyCPU``.
Redirecting ``SPEEDYCPU_HOME`` is enough because every path helper in
:mod:`speedycpu.config` goes through :func:`speedycpu.config.home_dir`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    """Point every config/state lookup at a throwaway directory.

    The directory is created eagerly so a test can drop a rollback file into it
    without having to call :func:`speedycpu.config.home_dir` first.
    """
    target = tmp_path / "SpeedyCPU"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SPEEDYCPU_HOME", str(target))
    monkeypatch.delenv("SPEEDYCPU_LANG", raising=False)
    return target


@pytest.fixture()
def no_engines(monkeypatch):
    """Pretend no optional engine binary exists.

    Engine discovery walks the source tree and looks on ``PATH``, so on a
    developer machine it will happily find the real ``scpu-watch.exe`` and
    ``scpu-agent.jar``. Tests that assert on *selection logic* need a
    deterministic, empty world instead.
    """
    from speedycpu import engines

    monkeypatch.setattr(engines, "find_native_watcher", lambda: None)
    monkeypatch.setattr(engines, "find_native_timer", lambda: None)
    monkeypatch.setattr(engines, "find_java_agent", lambda: None)
    monkeypatch.setattr(engines, "java_executable", lambda: None)
    return engines
