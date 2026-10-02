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

"""Hand written ctypes bindings for the Win32 APIs SpeedyCPU needs.

Only the small slice of the Win32 API used by SpeedyCPU is declared here. That
keeps the project free of third-party dependencies (no ``psutil``, no
``pywin32``), which in turn keeps the frozen PyInstaller binary small and the
supply chain boring.

This module is Windows-only and is imported lazily by
:mod:`speedycpu.backends.windows`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
from collections.abc import Iterator
from typing import NamedTuple

from .base import PRIORITY_CLASS_NAMES as _BASE_PRIORITY_CLASS_NAMES

if sys.platform != "win32":  # pragma: no cover - guarded by the caller
    raise ImportError("speedycpu.backends.winapi is Windows-only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

DWORD_PTR = ctypes.c_size_t
SIZE_T = ctypes.c_size_t

# ---------------------------------------------------------------------------
# Access rights
# ---------------------------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_INFORMATION = 0x0200
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000

THREAD_SET_INFORMATION = 0x0020
THREAD_QUERY_INFORMATION = 0x0040

# ---------------------------------------------------------------------------
# Priority classes / thread priorities
# ---------------------------------------------------------------------------
IDLE_PRIORITY_CLASS = 0x00000040
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
HIGH_PRIORITY_CLASS = 0x00000080
REALTIME_PRIORITY_CLASS = 0x00000100

PRIORITY_CLASS_NAMES = {
    IDLE_PRIORITY_CLASS: "idle",
    BELOW_NORMAL_PRIORITY_CLASS: "below_normal",
    NORMAL_PRIORITY_CLASS: "normal",
    ABOVE_NORMAL_PRIORITY_CLASS: "above_normal",
    HIGH_PRIORITY_CLASS: "high",
    REALTIME_PRIORITY_CLASS: "realtime",
}
PRIORITY_CLASS_VALUES = {name: value for value, name in PRIORITY_CLASS_NAMES.items()}
# The same mapping is needed by ``speedycpu.worker`` to parse the C++ engine's
# rollback file, so both sides must agree on it rather than each keeping a copy.
assert PRIORITY_CLASS_NAMES == _BASE_PRIORITY_CLASS_NAMES, "priority class tables drifted apart"

THREAD_PRIORITY_IDLE = -15
THREAD_PRIORITY_LOWEST = -2
THREAD_PRIORITY_BELOW_NORMAL = -1
THREAD_PRIORITY_NORMAL = 0
THREAD_PRIORITY_ABOVE_NORMAL = 1
THREAD_PRIORITY_HIGHEST = 2
THREAD_PRIORITY_TIME_CRITICAL = 15

THREAD_PRIORITY_NAMES = {
    -15: "idle",
    -2: "lowest",
    -1: "below_normal",
    0: "normal",
    1: "above_normal",
    2: "highest",
    15: "time_critical",
}

# ---------------------------------------------------------------------------
# PROCESS_INFORMATION_CLASS / memory priority
# ---------------------------------------------------------------------------
ProcessMemoryPriority = 0
ProcessPowerThrottling = 4

MEMORY_PRIORITY_VERY_LOW = 1
MEMORY_PRIORITY_LOW = 2
MEMORY_PRIORITY_MEDIUM = 3
MEMORY_PRIORITY_BELOW_NORMAL = 4
MEMORY_PRIORITY_NORMAL = 5

PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4

# ---------------------------------------------------------------------------
# Toolhelp32
# ---------------------------------------------------------------------------
TH32CS_SNAPTHREAD = 0x00000004
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

MAX_PATH = 260
WAIT_TIMEOUT = 258
WAIT_OBJECT_0 = 0

TOKEN_QUERY = 0x0008
TokenElevation = 20


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ThreadID", wt.DWORD),
        ("th32OwnerProcessID", wt.DWORD),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", wt.DWORD),
    ]


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wt.ULONG),
        ("ControlMask", wt.ULONG),
        ("StateMask", wt.ULONG),
    ]


class MEMORY_PRIORITY_INFORMATION(ctypes.Structure):
    _fields_ = [("MemoryPriority", wt.ULONG)]


class ProcessEntry(NamedTuple):
    pid: int
    name: str
    exe: str


# ---------------------------------------------------------------------------
# Prototypes. Declaring argtypes matters on 64-bit: without them ctypes
# truncates pointers to 32 bits and handles get silently corrupted.
# ---------------------------------------------------------------------------
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wt.DWORD
kernel32.GetPriorityClass.argtypes = [wt.HANDLE]
kernel32.GetPriorityClass.restype = wt.DWORD
kernel32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.SetPriorityClass.restype = wt.BOOL
kernel32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
kernel32.SetProcessInformation.restype = wt.BOOL
kernel32.GetProcessAffinityMask.argtypes = [wt.HANDLE, ctypes.POINTER(DWORD_PTR), ctypes.POINTER(DWORD_PTR)]
kernel32.GetProcessAffinityMask.restype = wt.BOOL
kernel32.SetProcessAffinityMask.argtypes = [wt.HANDLE, DWORD_PTR]
kernel32.SetProcessAffinityMask.restype = wt.BOOL
kernel32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenThread.restype = wt.HANDLE
kernel32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
kernel32.SetThreadPriority.restype = wt.BOOL
kernel32.GetThreadPriority.argtypes = [wt.HANDLE]
kernel32.GetThreadPriority.restype = ctypes.c_int
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Thread32First.argtypes = [wt.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32First.restype = wt.BOOL
kernel32.Thread32Next.argtypes = [wt.HANDLE, ctypes.POINTER(THREADENTRY32)]
kernel32.Thread32Next.restype = wt.BOOL
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.GetConsoleOutputCP.argtypes = []
kernel32.GetConsoleOutputCP.restype = wt.UINT
kernel32.GetOEMCP.argtypes = []
kernel32.GetOEMCP.restype = wt.UINT

psapi.EnumProcesses.argtypes = [ctypes.POINTER(wt.DWORD), wt.DWORD, ctypes.POINTER(wt.DWORD)]
psapi.EnumProcesses.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

winmm.timeBeginPeriod.argtypes = [wt.UINT]
winmm.timeBeginPeriod.restype = wt.UINT
winmm.timeEndPeriod.argtypes = [wt.UINT]
winmm.timeEndPeriod.restype = wt.UINT

ntdll.NtQueryTimerResolution.argtypes = [
    ctypes.POINTER(wt.ULONG),
    ctypes.POINTER(wt.ULONG),
    ctypes.POINTER(wt.ULONG),
]
ntdll.NtQueryTimerResolution.restype = ctypes.c_long

advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
advapi32.GetTokenInformation.restype = wt.BOOL

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD


def last_error_message() -> str:
    code = ctypes.get_last_error()
    try:
        return f"{code}: {ctypes.FormatError(code)}"
    except (OSError, ValueError):  # pragma: no cover
        return str(code)


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------
def is_admin() -> bool:
    """True when the current process holds an elevated token."""
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        elevation = wt.DWORD(0)
        returned = wt.DWORD(0)
        ok = advapi32.GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        )
        return bool(ok and elevation.value)
    finally:
        kernel32.CloseHandle(token)


# ---------------------------------------------------------------------------
# Process handles
# ---------------------------------------------------------------------------
def open_process(pid: int, access: int) -> int | None:
    handle = kernel32.OpenProcess(access, False, pid)
    return handle or None


def close_handle(handle: int | None) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def process_image_name(pid: int) -> tuple[str, str]:
    """Return ``(basename, full_path)`` for *pid*, or ``("", "")`` on failure."""
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return "", ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return "", ""
        full = buffer.value
        return os.path.basename(full), full
    finally:
        close_handle(handle)


def iter_processes() -> Iterator[ProcessEntry]:
    """Yield every live process as ``(pid, basename, full_path)``.

    ``EnumProcesses`` also returns stale PIDs; they are filtered out here by
    failure to open a query handle.
    """
    capacity = 4096
    for _ in range(4):
        buffer = (wt.DWORD * capacity)()
        needed = wt.DWORD(0)
        # Pass the array itself: ctypes converts an array to a pointer for a
        # POINTER(T) parameter, whereas byref() would yield pointer-to-array.
        if not psapi.EnumProcesses(buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            return
        count = needed.value // ctypes.sizeof(wt.DWORD)
        if count < capacity:
            break
        capacity *= 2
    else:  # pragma: no cover - would need >32k processes
        count = capacity

    for index in range(count):
        pid = int(buffer[index])
        if pid <= 0:
            continue
        name, full = process_image_name(pid)
        if not name:
            continue
        yield ProcessEntry(pid, name, full)


def is_process_alive(pid: int) -> bool:
    handle = open_process(pid, SYNCHRONIZE)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        close_handle(handle)


def terminate_process(pid: int) -> bool:
    handle = open_process(pid, PROCESS_TERMINATE)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 0))
    finally:
        close_handle(handle)


def current_pid() -> int:
    return int(kernel32.GetCurrentProcessId())


def foreground_pid() -> int:
    window = user32.GetForegroundWindow()
    if not window:
        return 0
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    return int(pid.value)


# ---------------------------------------------------------------------------
# Priority class
# ---------------------------------------------------------------------------
def get_priority_class(pid: int) -> int | None:
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return None
    try:
        value = kernel32.GetPriorityClass(handle)
        return int(value) if value else None
    finally:
        close_handle(handle)


def set_priority_class(pid: int, priority_class: int) -> bool:
    handle = open_process(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        return bool(kernel32.SetPriorityClass(handle, priority_class))
    finally:
        close_handle(handle)


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------
def iter_thread_ids(pid: int) -> list[int]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return []
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(THREADENTRY32)
        threads: list[int] = []
        if not kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            return []
        while True:
            if entry.th32OwnerProcessID == pid:
                threads.append(int(entry.th32ThreadID))
            if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                break
        return threads
    finally:
        close_handle(snapshot)


def get_thread_priority(tid: int) -> int | None:
    handle = kernel32.OpenThread(THREAD_QUERY_INFORMATION, False, tid)
    if not handle:
        return None
    try:
        value = kernel32.GetThreadPriority(handle)
        return int(value)
    finally:
        close_handle(handle)


def set_thread_priority(tid: int, priority: int) -> bool:
    handle = kernel32.OpenThread(THREAD_SET_INFORMATION, False, tid)
    if not handle:
        return False
    try:
        return bool(kernel32.SetThreadPriority(handle, priority))
    finally:
        close_handle(handle)


def raise_threads(
    pid: int, priority: int = THREAD_PRIORITY_HIGHEST
) -> tuple[int, int, list[tuple[int, int]]]:
    """Raise every thread of *pid* to at least *priority*.

    Returns ``(raised, total, changes)`` where *changes* is a list of
    ``(thread_id, previous_priority)`` pairs for the threads actually modified,
    so the operation can be undone exactly later. Threads already at or above
    the target are left alone, making repeated calls cheap and idempotent.
    """
    raised = 0
    total = 0
    changes: list[tuple[int, int]] = []
    for tid in iter_thread_ids(pid):
        total += 1
        current = get_thread_priority(tid)
        if current is None or current >= priority:
            continue
        if set_thread_priority(tid, priority):
            raised += 1
            changes.append((tid, current))
    return raised, total, changes


def restore_thread_priorities(pid: int, changes: list[tuple[int, int]]) -> int:
    """Put the given threads back to their recorded priorities.

    Thread IDs are recycled by the kernel after a thread exits, so each ID is
    first verified to still belong to *pid* before being touched.
    """
    if not changes:
        return 0
    alive = set(iter_thread_ids(pid))
    restored = 0
    for tid, previous in changes:
        if tid not in alive:
            continue
        if set_thread_priority(tid, int(previous)):
            restored += 1
    return restored


def thread_count(pid: int) -> int:
    return len(iter_thread_ids(pid))


# ---------------------------------------------------------------------------
# EcoQoS (power throttling) - per process
# ---------------------------------------------------------------------------
def set_process_ecoqos(pid: int, disable: bool) -> bool:
    """Disable (or restore) Windows power throttling for a single process.

    When power throttling is disabled the scheduler stops demoting the process'
    threads to "efficiency" cores and stops throttling its clock, which is the
    single most noticeable fluidity win on modern hybrid-core CPUs.
    """
    handle = open_process(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        control = PROCESS_POWER_THROTTLING_EXECUTION_SPEED | PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION
        state = PROCESS_POWER_THROTTLING_STATE(
            PROCESS_POWER_THROTTLING_CURRENT_VERSION,
            control,
            0 if disable else control,
        )
        return bool(
            kernel32.SetProcessInformation(
                handle,
                ProcessPowerThrottling,
                ctypes.byref(state),
                ctypes.sizeof(state),
            )
        )
    finally:
        close_handle(handle)


# ---------------------------------------------------------------------------
# Memory priority
# ---------------------------------------------------------------------------
def set_memory_priority(pid: int, priority: int = MEMORY_PRIORITY_NORMAL) -> bool:
    handle = open_process(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        info = MEMORY_PRIORITY_INFORMATION(priority)
        return bool(
            kernel32.SetProcessInformation(
                handle,
                ProcessMemoryPriority,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
        )
    finally:
        close_handle(handle)


# ---------------------------------------------------------------------------
# CPU affinity
# ---------------------------------------------------------------------------
def get_affinity(pid: int) -> tuple[int, int] | None:
    """Return ``(process_mask, system_mask)`` for *pid*."""
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return None
    try:
        process_mask = DWORD_PTR(0)
        system_mask = DWORD_PTR(0)
        ok = kernel32.GetProcessAffinityMask(
            handle, ctypes.byref(process_mask), ctypes.byref(system_mask)
        )
        if not ok:
            return None
        return int(process_mask.value), int(system_mask.value)
    finally:
        close_handle(handle)


def set_affinity(pid: int, mask: int) -> bool:
    handle = open_process(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        return bool(kernel32.SetProcessAffinityMask(handle, DWORD_PTR(mask)))
    finally:
        close_handle(handle)


def popcount(value: int) -> int:
    return bin(value).count("1")


# ---------------------------------------------------------------------------
# Working set (memory residency)
# ---------------------------------------------------------------------------
QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001
QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
QUOTA_LIMITS_HARDWS_MAX_ENABLE = 0x00000004
QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", SIZE_T),
        ("WorkingSetSize", SIZE_T),
        ("QuotaPeakPagedPoolUsage", SIZE_T),
        ("QuotaPagedPoolUsage", SIZE_T),
        ("QuotaPeakNonPagedPoolUsage", SIZE_T),
        ("QuotaNonPagedPoolUsage", SIZE_T),
        ("PagefileUsage", SIZE_T),
        ("PeakPagefileUsage", SIZE_T),
    ]


psapi.GetProcessMemoryInfo.argtypes = [
    wt.HANDLE,
    ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
    wt.DWORD,
]
psapi.GetProcessMemoryInfo.restype = wt.BOOL
kernel32.SetProcessWorkingSetSizeEx.argtypes = [wt.HANDLE, SIZE_T, SIZE_T, wt.DWORD]
kernel32.SetProcessWorkingSetSizeEx.restype = wt.BOOL


def working_set_size(pid: int) -> int | None:
    """Current working set of *pid* in bytes."""
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return None
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    finally:
        close_handle(handle)


def set_hard_working_set(pid: int, minimum: int, maximum: int) -> bool:
    """Pin a process' working set between *minimum* and *maximum* bytes.

    This is the "aggressive" mode: keeping a hard floor stops the memory
    manager from trimming the process down to standby pages while the user
    switches away and back, which is a common source of first-frame stutter.
    """
    handle = open_process(pid, PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION)
    if not handle:
        return False
    try:
        flags = QUOTA_LIMITS_HARDWS_MIN_ENABLE | QUOTA_LIMITS_HARDWS_MAX_ENABLE
        return bool(
            kernel32.SetProcessWorkingSetSizeEx(handle, SIZE_T(int(minimum)), SIZE_T(int(maximum)), flags)
        )
    finally:
        close_handle(handle)


def clear_hard_working_set(pid: int) -> bool:
    handle = open_process(pid, PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION)
    if not handle:
        return False
    try:
        flags = QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE
        return bool(kernel32.SetProcessWorkingSetSizeEx(handle, SIZE_T(0), SIZE_T(0), flags))
    finally:
        close_handle(handle)


# ---------------------------------------------------------------------------
# Timer resolution
# ---------------------------------------------------------------------------
def query_timer_resolution_ms() -> float | None:
    """Current system timer resolution in milliseconds (Windows default: 15.625)."""
    minimum = wt.ULONG(0)
    maximum = wt.ULONG(0)
    current = wt.ULONG(0)
    status = ntdll.NtQueryTimerResolution(
        ctypes.byref(minimum), ctypes.byref(maximum), ctypes.byref(current)
    )
    if status != 0 or current.value == 0:
        return None
    return current.value / 10000.0


class TimerResolution:
    """Context manager around ``timeBeginPeriod`` / ``timeEndPeriod``.

    The raised resolution only lasts while some process is holding it, which is
    exactly why SpeedyCPU keeps a background worker alive while it is ON.
    ``timeEndPeriod`` must be called by the same process (and with the same
    period) that requested it, hence the reference counting here.
    """

    def __init__(self, milliseconds: int = 1) -> None:
        self.milliseconds = int(milliseconds)
        self.acquired = False

    def acquire(self) -> bool:
        if self.acquired or self.milliseconds <= 0:
            return self.acquired
        if winmm.timeBeginPeriod(self.milliseconds) == 0:
            self.acquired = True
        return self.acquired

    def release(self) -> bool:
        if not self.acquired:
            return False
        winmm.timeEndPeriod(self.milliseconds)
        self.acquired = False
        return True
