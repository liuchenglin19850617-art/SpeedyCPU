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

"""SpeedyCPU - a command-line CPU responsiveness booster.

SpeedyCPU does not and cannot make a CPU physically faster, and no external
program can force another process to spawn more threads. What SpeedyCPU really
does is stack every *legitimate*, measurable scheduling knob the OS exposes:

* raise the priority class and every thread's priority of the target process
* clear Windows EcoQoS / power throttling so the scheduler stops parking cores
* switch the active power plan to High performance (optionally Ultimate)
* raise the global timer resolution from ~15.6 ms to 1 ms
* lift memory priority and remove stale CPU affinity restrictions

See ``docs/HOW-IT-WORKS.md`` for the honest version, including what it cannot do.
"""

from __future__ import annotations

__all__ = ["APP_NAME", "__author__", "__version__"]

__version__ = "0.1.0"
__author__ = "SpeedyCPU contributors"
APP_NAME = "SpeedyCPU"
