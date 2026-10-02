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

// scpu-watch - the low-overhead focus-driven accelerator.
//
// Same job as the Java agent and the Python worker, but as a ~100 KB native
// process with a ~2 MB working set instead of a JVM's ~60 MB or a Python
// interpreter's ~15 MB. For a daemon that is supposed to sit in the background
// making the machine feel faster, being one of the smaller things on the box is
// a feature in its own right - a booster that eats a gigabyte of page file is
// arguing against itself.
//
// How it works, in order of importance:
//
//   1. SetWinEventHook(EVENT_SYSTEM_FOREGROUND) tells us the instant focus
//      moves. No polling, no sampling interval to tune: the kernel calls us.
//   2. The newly focused process gets HIGH priority class, every one of its
//      threads is raised to THREAD_PRIORITY_HIGHEST, and its EcoQoS throttling
//      bit is cleared.
//   3. The previously focused process is put back exactly as it was. This half
//      is what actually changes who wins the CPU: if yesterday's window keeps
//      its elevated priority, today's has gained nothing.
//
// Usage:
//   scpu-watch.exe [--mode foreground|all] [--priority high|above_normal]
//                  [--ms N] [--stop-flag PATH] [--exclude a.exe,b.exe]
//                  [--parent-pid N] [--verbose]

#include <windows.h>
#include <mmsystem.h>
#include <psapi.h>
#include <tlhelp32.h>

#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

#ifndef PROCESS_QUERY_LIMITED_INFORMATION
#define PROCESS_QUERY_LIMITED_INFORMATION 0x1000
#endif
#ifndef PROCESS_POWER_THROTTLING_CURRENT_VERSION
#define PROCESS_POWER_THROTTLING_CURRENT_VERSION 1
#define PROCESS_POWER_THROTTLING_EXECUTION_SPEED 0x1
#define PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION 0x4
#define PROCESS_POWER_THROTTLING_STATE_COMPAT 1
struct PROCESS_POWER_THROTTLING_STATE_COMPAT_T {
    ULONG Version;
    ULONG ControlMask;
    ULONG StateMask;
};
typedef struct PROCESS_POWER_THROTTLING_STATE_COMPAT_T PROCESS_POWER_THROTTLING_STATE;
#define ProcessPowerThrottling 4
#endif

using SetProcessInformationFn = BOOL(WINAPI*)(HANDLE, int, LPVOID, DWORD);

struct ThreadChange {
    DWORD tid;
    int previous_priority;
};

struct Boost {
    DWORD pid = 0;
    std::string name;
    int priority_before = 0;
    int priority_after = 0;
    std::vector<ThreadChange> threads;
    bool ecoqos_disabled = false;
    bool for_focus = false;
};

struct Options {
    std::string mode = "foreground";
    std::string priority = "high";
    unsigned period_ms = 1;
    std::string stop_flag;
    std::string state_file;
    DWORD parent_pid = 0;
    long seconds = 0;
    bool verbose = false;
    std::unordered_set<std::string> exclude;
};

std::unordered_map<DWORD, Boost> g_boosted;
DWORD g_foreground = 0;
volatile LONG g_running = 1;
SetProcessInformationFn g_set_process_information = nullptr;
Options g_options;

// Defined below, next to release_all(); declared here so every mutation of
// g_boosted can persist the rollback file.
void write_state_file();
void remove_state_file();

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
std::string to_lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

bool file_exists(const std::string& path) {
    return !path.empty() && GetFileAttributesA(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}

bool process_alive(DWORD pid) {
    HANDLE handle = OpenProcess(SYNCHRONIZE, FALSE, pid);
    if (handle == nullptr) {
        return false;
    }
    bool alive = WaitForSingleObject(handle, 0) == WAIT_TIMEOUT;
    CloseHandle(handle);
    return alive;
}

std::string process_name(DWORD pid) {
    HANDLE handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (handle == nullptr) {
        return std::string();
    }
    char buffer[MAX_PATH] = {0};
    DWORD size = MAX_PATH;
    std::string name;
    if (QueryFullProcessImageNameA(handle, 0, buffer, &size)) {
        std::string full(buffer);
        size_t slash = full.find_last_of("\\/");
        name = slash == std::string::npos ? full : full.substr(slash + 1);
    }
    CloseHandle(handle);
    return name;
}

void log_line(const char* format, ...) {
    if (!g_options.verbose) {
        return;
    }
    SYSTEMTIME now;
    GetLocalTime(&now);
    std::printf("%04d-%02d-%02d %02d:%02d:%02d ", now.wYear, now.wMonth, now.wDay, now.wHour,
                now.wMinute, now.wSecond);
    va_list args;
    va_start(args, format);
    std::vprintf(format, args);
    va_end(args);
    std::printf("\n");
    std::fflush(stdout);
}

// ---------------------------------------------------------------------------
// Thread enumeration
// ---------------------------------------------------------------------------
std::vector<DWORD> thread_ids(DWORD pid) {
    std::vector<DWORD> ids;
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return ids;
    }
    THREADENTRY32 entry;
    entry.dwSize = sizeof(entry);
    if (Thread32First(snapshot, &entry)) {
        do {
            if (entry.th32OwnerProcessID == pid) {
                ids.push_back(entry.th32ThreadID);
            }
        } while (Thread32Next(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return ids;
}

int get_thread_priority(DWORD tid) {
    HANDLE handle = OpenThread(THREAD_QUERY_INFORMATION, FALSE, tid);
    if (handle == nullptr) {
        return INT_MIN;
    }
    int value = GetThreadPriority(handle);
    CloseHandle(handle);
    return value;
}

bool set_thread_priority(DWORD tid, int priority) {
    HANDLE handle = OpenThread(THREAD_SET_INFORMATION, FALSE, tid);
    if (handle == nullptr) {
        return false;
    }
    bool ok = SetThreadPriority(handle, priority) != FALSE;
    CloseHandle(handle);
    return ok;
}

// ---------------------------------------------------------------------------
// Boost / revert
// ---------------------------------------------------------------------------
int target_priority_class() {
    return g_options.priority == "above_normal" ? ABOVE_NORMAL_PRIORITY_CLASS
                                               : HIGH_PRIORITY_CLASS;
}

bool is_excluded(const std::string& name) {
    if (name.empty()) {
        return true;
    }
    std::string lowered = to_lower(name);
    static const std::unordered_set<std::string> never = {
        "system", "system idle process", "idle", "registry", "memory compression",
        "secure system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
        "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe", "audiodg.exe",
        "wudfhost.exe", "speedycpu.exe", "scpu-watch.exe", "scpu-timer.exe",
        "scpu-agent.jar", "java.exe", "javaw.exe", "python.exe", "pythonw.exe"};
    return never.count(lowered) > 0 || g_options.exclude.count(lowered) > 0;
}

Boost boost_process(DWORD pid, const std::string& name, bool for_focus) {
    Boost record;
    record.pid = pid;
    record.name = name;
    record.for_focus = for_focus;

    HANDLE handle =
        OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (handle == nullptr) {
        return record;
    }
    int current = GetPriorityClass(handle);
    record.priority_before = current;

    int target = target_priority_class();
    if (current == REALTIME_PRIORITY_CLASS || current == HIGH_PRIORITY_CLASS || current >= target) {
        record.priority_after = current;
    } else if (SetPriorityClass(handle, target)) {
        record.priority_after = target;
    } else {
        CloseHandle(handle);
        record.pid = 0;
        return record;
    }
    CloseHandle(handle);

    // Every thread, not just the main one: a process that parked 40 worker
    // threads in NORMAL would otherwise still lose those races.
    for (DWORD tid : thread_ids(pid)) {
        int previous = get_thread_priority(tid);
        if (previous == INT_MIN || previous >= THREAD_PRIORITY_HIGHEST) {
            continue;
        }
        if (set_thread_priority(tid, THREAD_PRIORITY_HIGHEST)) {
            record.threads.push_back({tid, previous});
        }
    }

    if (g_set_process_information != nullptr) {
        HANDLE info_handle =
            OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
        if (info_handle != nullptr) {
            PROCESS_POWER_THROTTLING_STATE state{};
            state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION;
            state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED |
                                PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION;
            state.StateMask = 0;  // 0 = throttling off
            if (g_set_process_information(info_handle, ProcessPowerThrottling, &state,
                                          sizeof(state))) {
                record.ecoqos_disabled = true;
            }
            CloseHandle(info_handle);
        }
    }
    return record;
}

void unboost(const Boost& record) {
    if (record.pid == 0 || !process_alive(record.pid)) {
        return;
    }
    std::vector<DWORD> alive = thread_ids(record.pid);
    auto still_alive = [&alive](DWORD tid) {
        return std::find(alive.begin(), alive.end(), tid) != alive.end();
    };
    for (const ThreadChange& change : record.threads) {
        if (still_alive(change.tid)) {
            set_thread_priority(change.tid, change.previous_priority);
        }
    }

    HANDLE handle = OpenProcess(PROCESS_SET_INFORMATION, FALSE, record.pid);
    if (handle != nullptr) {
        if (record.priority_before != 0) {
            SetPriorityClass(handle, static_cast<DWORD>(record.priority_before));
        }
        CloseHandle(handle);
    }

    if (record.ecoqos_disabled && g_set_process_information != nullptr) {
        HANDLE info_handle = OpenProcess(PROCESS_SET_INFORMATION, FALSE, record.pid);
        if (info_handle != nullptr) {
            PROCESS_POWER_THROTTLING_STATE state{};
            state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION;
            state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED |
                                PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION;
            state.StateMask = state.ControlMask;  // throttling allowed again
            g_set_process_information(info_handle, ProcessPowerThrottling, &state, sizeof(state));
            CloseHandle(info_handle);
        }
    }
}

void boost_foreground(DWORD pid) {
    if (pid == 0 || pid == GetCurrentProcessId()) {
        return;
    }
    std::string name = process_name(pid);
    if (is_excluded(name) || g_boosted.count(pid) > 0) {
        return;
    }
    Boost record = boost_process(pid, name, true);
    if (record.pid == 0) {
        log_line("skip %s (%lu): not accessible", name.c_str(), pid);
        return;
    }
    g_boosted[pid] = record;
    write_state_file();
    log_line("boosted %s (%lu) %d -> %d, %zu threads", name.c_str(), pid, record.priority_before,
             record.priority_after, record.threads.size());
}

void release_all(bool only_focus) {
    for (auto it = g_boosted.begin(); it != g_boosted.end();) {
        if (!only_focus || it->second.for_focus) {
            unboost(it->second);
            it = g_boosted.erase(it);
        } else {
            ++it;
        }
    }
}

// ---------------------------------------------------------------------------
// Rollback file
// ---------------------------------------------------------------------------
// A tab separated dump of everything currently boosted, rewritten on every
// change and deleted on a clean exit.
//
// Graceful shutdown already restores every process, so this file only matters
// when "graceful" is not an option: a crash, an OOM kill, or a user ending the
// task from Task Manager. Without it, those processes would keep their elevated
// priority for the rest of the session with nothing left to undo them.
//
// The format is deliberately line based rather than JSON: hand rolling a JSON
// writer in C++ buys nothing here, and a parser for this is three lines in
// Python.
//
//   pid<TAB>priority_before<TAB>name<TAB>tid:prio,tid:prio,...
void write_state_file() {
    if (g_options.state_file.empty()) {
        return;
    }
    FILE* file = std::fopen(g_options.state_file.c_str(), "wb");
    if (file == nullptr) {
        return;
    }
    std::fprintf(file, "# SpeedyCPU native engine rollback file\n");
    std::fprintf(file, "# pid\tpriority_before\tname\tthread_priorities\n");
    for (const auto& entry : g_boosted) {
        const Boost& record = entry.second;
        std::fprintf(file, "%lu\t%d\t%s\t", record.pid, record.priority_before,
                     record.name.c_str());
        for (size_t i = 0; i < record.threads.size(); ++i) {
            std::fprintf(file, "%s%lu:%d", i == 0 ? "" : ",", record.threads[i].tid,
                         record.threads[i].previous_priority);
        }
        std::fprintf(file, "\n");
    }
    std::fclose(file);
}

void remove_state_file() {
    if (!g_options.state_file.empty()) {
        DeleteFileA(g_options.state_file.c_str());
    }
}

void CALLBACK foreground_hook(HWINEVENTHOOK, DWORD, HWND, LONG, LONG, DWORD, DWORD) {
    HWND window = GetForegroundWindow();
    if (window == nullptr) {
        return;
    }
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (pid == 0 || pid == g_foreground) {
        return;
    }
    DWORD previous = g_foreground;
    g_foreground = pid;

    if (previous != 0) {
        auto it = g_boosted.find(previous);
        if (it != g_boosted.end() && it->second.for_focus) {
            log_line("released %s (%lu) on focus loss", it->second.name.c_str(), previous);
            unboost(it->second);
            g_boosted.erase(it);
            write_state_file();
        }
    }
    boost_foreground(pid);
}

void scan_all() {
    std::vector<DWORD> pids(4096);
    DWORD needed = 0;
    while (true) {
        if (!EnumProcesses(pids.data(), static_cast<DWORD>(pids.size() * sizeof(DWORD)), &needed)) {
            return;
        }
        if (needed < pids.size() * sizeof(DWORD)) {
            break;
        }
        pids.resize(pids.size() * 2);
    }
    size_t count = needed / sizeof(DWORD);
    for (size_t i = 0; i < count; ++i) {
        DWORD pid = pids[i];
        if (pid == 0 || pid == GetCurrentProcessId() || g_boosted.count(pid) > 0) {
            continue;
        }
        std::string name = process_name(pid);
        if (is_excluded(name)) {
            continue;
        }
        Boost record = boost_process(pid, name, false);
        if (record.pid != 0) {
            g_boosted[pid] = record;
        }
    }
    write_state_file();
}

BOOL WINAPI console_handler(DWORD event) {
    if (event == CTRL_C_EVENT || event == CTRL_CLOSE_EVENT || event == CTRL_BREAK_EVENT) {
        InterlockedExchange(&g_running, 0);
        PostThreadMessageW(GetCurrentThreadId(), WM_NULL, 0, 0);
        return TRUE;
    }
    return FALSE;
}

void print_usage() {
    std::printf(
        "scpu-watch - focus-driven CPU booster (native engine)\n"
        "\n"
        "usage:\n"
        "  scpu-watch [--mode foreground|all] [--priority high|above_normal]\n"
        "             [--ms N] [--stop-flag PATH] [--exclude a.exe,b.exe]\n"
        "             [--parent-pid N] [--seconds N] [--state-file PATH] [--verbose]\n");
}

}  // namespace

int main(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "error: %s needs a value\n", name);
                std::exit(2);
            }
            return argv[++i];
        };
        if (arg == "--mode") {
            g_options.mode = next("--mode");
        } else if (arg == "--priority") {
            g_options.priority = next("--priority");
        } else if (arg == "--ms") {
            g_options.period_ms = static_cast<unsigned>(
                std::strtoul(next("--ms").c_str(), nullptr, 10));
        } else if (arg == "--stop-flag") {
            g_options.stop_flag = next("--stop-flag");
        } else if (arg == "--parent-pid") {
            g_options.parent_pid =
                static_cast<DWORD>(std::strtoul(next("--parent-pid").c_str(), nullptr, 10));
        } else if (arg == "--state-file") {
            g_options.state_file = next("--state-file");
        } else if (arg == "--seconds") {
            // Bounded run - used by the smoke test and by CI, where a daemon
            // that never exits is not helpful.
            g_options.seconds = std::strtol(next("--seconds").c_str(), nullptr, 10);
        } else if (arg == "--exclude") {
            std::string raw = next("--exclude");
            size_t start = 0;
            while (start <= raw.size()) {
                size_t comma = raw.find(',', start);
                std::string item = raw.substr(start, comma == std::string::npos ? std::string::npos
                                                                               : comma - start);
                if (!item.empty()) {
                    g_options.exclude.insert(to_lower(item));
                }
                if (comma == std::string::npos) {
                    break;
                }
                start = comma + 1;
            }
        } else if (arg == "--verbose" || arg == "-v") {
            g_options.verbose = true;
        } else if (arg == "-h" || arg == "--help") {
            print_usage();
            return 0;
        } else {
            std::fprintf(stderr, "error: unknown argument %s\n", arg.c_str());
            print_usage();
            return 2;
        }
    }

    // Optional: per-process EcoQoS control needs Windows 8 or newer.
    if (HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll")) {
        g_set_process_information = reinterpret_cast<SetProcessInformationFn>(
            reinterpret_cast<void*>(GetProcAddress(kernel32, "SetProcessInformation")));
    }

    double before = 0.0;
    if (g_options.period_ms >= 1 && g_options.period_ms <= 15 &&
        timeBeginPeriod(g_options.period_ms) == TIMERR_NOERROR) {
        (void)before;
    }

    SetConsoleCtrlHandler(console_handler, TRUE);

    HWINEVENTHOOK hook = SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, nullptr,
                                        foreground_hook, 0, 0, WINEVENT_OUTOFCONTEXT);
    log_line("scpu-watch started (pid %lu), mode=%s, hook=%s", GetCurrentProcessId(),
             g_options.mode.c_str(), hook != nullptr ? "ok" : "unavailable");

    if (hook != nullptr) {
        // Boost whatever is already on screen before we start waiting.
        if (HWND window = GetForegroundWindow()) {
            DWORD pid = 0;
            GetWindowThreadProcessId(window, &pid);
            g_foreground = pid;
            boost_foreground(pid);
        }
    } else {
        // Degrade to a coarse poll rather than doing nothing at all.
        scan_all();
    }

    ULONGLONG last_reboost = GetTickCount64();
    ULONGLONG started = GetTickCount64();
    while (InterlockedCompareExchange(&g_running, 1, 1) == 1) {
        DWORD wait = MsgWaitForMultipleObjectsEx(0, nullptr, 200, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        if (wait == WAIT_OBJECT_0) {
            MSG message;
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }

        if (!g_options.stop_flag.empty() && file_exists(g_options.stop_flag)) {
            break;
        }
        if (g_options.parent_pid != 0 && !process_alive(g_options.parent_pid)) {
            log_line("parent pid %lu is gone, shutting down", g_options.parent_pid);
            break;
        }
        if (g_options.seconds > 0 &&
            GetTickCount64() - started >= static_cast<ULONGLONG>(g_options.seconds) * 1000ULL) {
            break;
        }

        // Re-apply boosts the OS or the process itself may have dropped, and
        // keep mode=all up to date as programs come and go.
        if (GetTickCount64() - last_reboost > 15000ULL) {
            last_reboost = GetTickCount64();
            for (auto& entry : g_boosted) {
                if (!process_alive(entry.first)) {
                    continue;
                }
                HANDLE handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, entry.first);
                if (handle == nullptr) {
                    continue;
                }
                int current = GetPriorityClass(handle);
                CloseHandle(handle);
                if (current != 0 && current < target_priority_class()) {
                    Boost fresh = boost_process(entry.first, entry.second.name, entry.second.for_focus);
                    if (fresh.pid != 0) {
                        fresh.priority_before = entry.second.priority_before;
                        // Keep the original thread baselines.
                        fresh.threads.insert(fresh.threads.end(), entry.second.threads.begin(),
                                             entry.second.threads.end());
                        entry.second = fresh;
                    }
                }
            }
            if (g_options.mode == "all") {
                scan_all();
            } else {
                write_state_file();
            }
        }
    }

    if (hook != nullptr) {
        UnhookWinEvent(hook);
    }
    size_t reverted = g_boosted.size();
    release_all(false);
    remove_state_file();
    if (g_options.period_ms >= 1 && g_options.period_ms <= 15) {
        timeEndPeriod(g_options.period_ms);
    }
    log_line("scpu-watch stopped, reverted %zu process(es)", reverted);
    std::printf("scpu-watch stopped, reverted %zu process(es)\n", reverted);
    return 0;
}
