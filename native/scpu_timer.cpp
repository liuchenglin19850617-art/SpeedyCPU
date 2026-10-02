/* SPDX-License-Identifier: LGPL-2.0-or-later
 * Copyright (C) 2026 SpeedyCPU contributors
 *
 * This program is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Library General Public License as published by the Free
 * Software Foundation; either version 2 of the License, or (at your option) any
 * later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE.  See the GNU Library General Public License for more
 * details.  You should have received a copy of it in the LICENSE file.
 */

// scpu-timer - hold a raised Windows timer resolution, and nothing else.
//
// Why this is a separate process at all:
//
//   timeBeginPeriod() only lasts while the process that called it is alive. If
//   the process that wants the 1 ms tick dies - a crash, a Task Manager
//   "End task", a sandbox reaping the tree - the tick silently drops back to
//   15.625 ms and every boosted thread starts waking up late again, with no
//   visible symptom other than the machine feeling sluggish.
//
//   A 60 KB native process is a much better owner of that request than a
//   Python interpreter or a JVM: it survives restarts of the smart part, it
//   costs nothing, and it can hand the tick over cleanly via timeEndPeriod.
//
// Usage:
//   scpu-timer.exe [--ms N] [--stop-flag PATH] [--parent-pid N] [--seconds N]
//   scpu-timer.exe --probe
//
// Exit codes: 0 clean shutdown, 2 bad usage, 3 could not acquire.

#include <windows.h>
#include <mmsystem.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {

// NtQueryTimerResolution is undocumented but stable since Windows 2000 and is
// the only way to read the *current* tick without guessing.
typedef LONG(WINAPI* NtQueryTimerResolutionFn)(PULONG, PULONG, PULONG);

bool query_timer_resolution_ms(double* out) {
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (ntdll == nullptr) {
        ntdll = LoadLibraryW(L"ntdll.dll");
    }
    if (ntdll == nullptr) {
        return false;
    }
    auto fn = reinterpret_cast<NtQueryTimerResolutionFn>(
        reinterpret_cast<void*>(GetProcAddress(ntdll, "NtQueryTimerResolution")));
    if (fn == nullptr) {
        return false;
    }
    ULONG minimum = 0;
    ULONG maximum = 0;
    ULONG current = 0;
    if (fn(&minimum, &maximum, &current) != 0 || current == 0) {
        return false;
    }
    // Values are in 100 ns units.
    *out = static_cast<double>(current) / 10000.0;
    return true;
}

volatile LONG g_running = 1;

BOOL WINAPI console_handler(DWORD event) {
    if (event == CTRL_C_EVENT || event == CTRL_CLOSE_EVENT || event == CTRL_BREAK_EVENT) {
        InterlockedExchange(&g_running, 0);
        return TRUE;
    }
    return FALSE;
}

bool process_alive(DWORD pid) {
    if (pid == 0) {
        return true;
    }
    HANDLE handle = OpenProcess(SYNCHRONIZE, FALSE, pid);
    if (handle == nullptr) {
        return false;
    }
    DWORD result = WaitForSingleObject(handle, 0);
    CloseHandle(handle);
    return result == WAIT_TIMEOUT;
}

bool file_exists(const std::string& path) {
    if (path.empty()) {
        return false;
    }
    DWORD attributes = GetFileAttributesA(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES;
}

void print_usage() {
    std::printf(
        "scpu-timer - hold a raised Windows timer resolution\n"
        "\n"
        "usage:\n"
        "  scpu-timer [--ms N] [--stop-flag PATH] [--parent-pid N] [--seconds N]\n"
        "  scpu-timer --probe\n"
        "\n"
        "  --ms N           timer period to request, 1..15 (default 1)\n"
        "  --stop-flag P    exit as soon as file P appears\n"
        "  --parent-pid N   exit when process N is gone (0 = ignore)\n"
        "  --seconds N      exit after N seconds (0 = run until signalled)\n"
        "  --probe          print the current resolution and exit\n");
}

}  // namespace

int main(int argc, char** argv) {
    UINT period_ms = 1;
    std::string stop_flag;
    DWORD parent_pid = 0;
    long seconds = 0;
    bool probe_only = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "error: %s needs a value\n", name);
                std::exit(2);
            }
            return argv[++i];
        };
        if (arg == "--ms") {
            period_ms = static_cast<UINT>(std::strtoul(next("--ms").c_str(), nullptr, 10));
        } else if (arg == "--stop-flag") {
            stop_flag = next("--stop-flag");
        } else if (arg == "--parent-pid") {
            parent_pid = static_cast<DWORD>(std::strtoul(next("--parent-pid").c_str(), nullptr, 10));
        } else if (arg == "--seconds") {
            seconds = std::strtol(next("--seconds").c_str(), nullptr, 10);
        } else if (arg == "--probe") {
            probe_only = true;
        } else if (arg == "-h" || arg == "--help") {
            print_usage();
            return 0;
        } else {
            std::fprintf(stderr, "error: unknown argument %s\n", arg.c_str());
            print_usage();
            return 2;
        }
    }

    if (probe_only) {
        double current = 0.0;
        if (query_timer_resolution_ms(&current)) {
            std::printf("%.3f\n", current);
            return 0;
        }
        std::printf("unknown\n");
        return 3;
    }

    if (period_ms < 1 || period_ms > 15) {
        std::fprintf(stderr, "error: --ms must be between 1 and 15\n");
        return 2;
    }

    double before = 0.0;
    query_timer_resolution_ms(&before);

    if (timeBeginPeriod(period_ms) != TIMERR_NOERROR) {
        std::fprintf(stderr, "error: timeBeginPeriod(%u) failed\n", period_ms);
        return 3;
    }

    double after = 0.0;
    query_timer_resolution_ms(&after);

    if (before > 0.0 && after > 0.0) {
        std::printf("timer-resolution %.3f -> %.3f ms\n", before, after);
    } else {
        std::printf("timer-resolution held at %u ms\n", period_ms);
    }
    std::fflush(stdout);

    SetConsoleCtrlHandler(console_handler, TRUE);

    ULONGLONG started = GetTickCount64();
    while (InterlockedCompareExchange(&g_running, 1, 1) == 1) {
        Sleep(200);

        if (!stop_flag.empty() && file_exists(stop_flag)) {
            break;
        }
        if (parent_pid != 0 && !process_alive(parent_pid)) {
            break;
        }
        if (seconds > 0 && (GetTickCount64() - started) >= static_cast<ULONGLONG>(seconds) * 1000ULL) {
            break;
        }
    }

    timeEndPeriod(period_ms);
    std::printf("timer-resolution released\n");
    return 0;
}
