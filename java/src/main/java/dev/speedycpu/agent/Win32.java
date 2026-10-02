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

package dev.speedycpu.agent;

import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * Win32 bindings for the SpeedyCPU agent, built on the Foreign Function &amp;
 * Memory API (final since JDK 22).
 *
 * <p>Why the FFM API instead of JNI: a JNI bridge would need a C++ toolchain on
 * every build machine, and a native DLL to ship per architecture. FFM talks to
 * the very same DLLs (kernel32/user32/winmm) with no compilation step at all,
 * so the agent stays a single portable jar and can still do everything the
 * Python backend does. The C++ helpers in {@code native/} exist for the
 * ultra-low-latency path; they are an optimisation, not a requirement.
 *
 * <p>Only the slice of the API the agent uses is bound here.
 */
public final class Win32 {

    private Win32() {}

    // ------------------------------------------------------------------
    // Constants
    // ------------------------------------------------------------------
    public static final int PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    public static final int PROCESS_SET_INFORMATION = 0x0200;
    public static final int PROCESS_SET_QUOTA = 0x0100;
    public static final int PROCESS_TERMINATE = 0x0001;
    public static final int SYNCHRONIZE = 0x00100000;

    public static final int THREAD_SET_INFORMATION = 0x0020;
    public static final int THREAD_QUERY_INFORMATION = 0x0040;

    public static final int IDLE_PRIORITY_CLASS = 0x00000040;
    public static final int BELOW_NORMAL_PRIORITY_CLASS = 0x00004000;
    public static final int NORMAL_PRIORITY_CLASS = 0x00000020;
    public static final int ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000;
    public static final int HIGH_PRIORITY_CLASS = 0x00000080;
    public static final int REALTIME_PRIORITY_CLASS = 0x00000100;

    public static final int THREAD_PRIORITY_IDLE = -15;
    public static final int THREAD_PRIORITY_LOWEST = -2;
    public static final int THREAD_PRIORITY_BELOW_NORMAL = -1;
    public static final int THREAD_PRIORITY_NORMAL = 0;
    public static final int THREAD_PRIORITY_ABOVE_NORMAL = 1;
    public static final int THREAD_PRIORITY_HIGHEST = 2;
    public static final int THREAD_PRIORITY_TIME_CRITICAL = 15;

    private static final int TH32CS_SNAPTHREAD = 0x00000004;
    private static final long INVALID_HANDLE_VALUE = -1L;
    private static final int WAIT_TIMEOUT = 258;

    private static final int PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1;
    private static final int PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1;
    private static final int PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4;
    private static final int ProcessPowerThrottling = 4;

    /** {@code EVENT_SYSTEM_FOREGROUND} - fired whenever the focused window changes. */
    public static final int EVENT_SYSTEM_FOREGROUND = 3;
    public static final int WINEVENT_OUTOFCONTEXT = 0x0000;
    public static final int WINEVENT_SKIPOWNPROCESS = 0x0002;

    /** Size of {@code THREADENTRY32}: 7 DWORD-sized fields, no padding on x64. */
    public static final long THREADENTRY32_SIZE = 28L;

    // ------------------------------------------------------------------
    // Symbol lookups + handles
    // ------------------------------------------------------------------
    private static final Linker LINKER = Linker.nativeLinker();
    private static final SymbolLookup KERNEL32 =
            SymbolLookup.libraryLookup("kernel32.dll", Arena.global());
    private static final SymbolLookup USER32 =
            SymbolLookup.libraryLookup("user32.dll", Arena.global());
    private static final SymbolLookup PSAPI =
            SymbolLookup.libraryLookup("psapi.dll", Arena.global());
    private static final SymbolLookup WINMM =
            SymbolLookup.libraryLookup("winmm.dll", Arena.global());

    private static MethodHandle bind(SymbolLookup lib, String name, FunctionDescriptor descriptor) {
        return LINKER.downcallHandle(
                lib.find(name).orElseThrow(() -> new UnsatisfiedLinkError("missing symbol: " + name)),
                descriptor);
    }

    private static final class H {
        static final MethodHandle OPEN_PROCESS = bind(KERNEL32, "OpenProcess",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.JAVA_INT,
                        ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));
        static final MethodHandle CLOSE_HANDLE = bind(KERNEL32, "CloseHandle",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG));
        static final MethodHandle GET_CURRENT_PROCESS_ID = bind(KERNEL32, "GetCurrentProcessId",
                FunctionDescriptor.of(ValueLayout.JAVA_INT));
        static final MethodHandle GET_CURRENT_THREAD = bind(KERNEL32, "GetCurrentThread",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG));
        static final MethodHandle GET_PRIORITY_CLASS = bind(KERNEL32, "GetPriorityClass",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG));
        static final MethodHandle SET_PRIORITY_CLASS = bind(KERNEL32, "SetPriorityClass",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_INT));
        static final MethodHandle SET_PROCESS_INFORMATION = bind(KERNEL32, "SetProcessInformation",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_INT, ValueLayout.ADDRESS, ValueLayout.JAVA_INT));
        static final MethodHandle GET_AFFINITY = bind(KERNEL32, "GetProcessAffinityMask",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.ADDRESS, ValueLayout.ADDRESS));
        static final MethodHandle SET_AFFINITY = bind(KERNEL32, "SetProcessAffinityMask",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_LONG));
        static final MethodHandle QUERY_IMAGE_NAME = bind(KERNEL32, "QueryFullProcessImageNameW",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_INT, ValueLayout.ADDRESS, ValueLayout.ADDRESS));
        static final MethodHandle OPEN_THREAD = bind(KERNEL32, "OpenThread",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.JAVA_INT,
                        ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));
        static final MethodHandle GET_THREAD_PRIORITY = bind(KERNEL32, "GetThreadPriority",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG));
        static final MethodHandle SET_THREAD_PRIORITY = bind(KERNEL32, "SetThreadPriority",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_INT));
        static final MethodHandle CREATE_SNAPSHOT = bind(KERNEL32, "CreateToolhelp32Snapshot",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.JAVA_INT,
                        ValueLayout.JAVA_INT));
        static final MethodHandle THREAD32_FIRST = bind(KERNEL32, "Thread32First",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.ADDRESS));
        static final MethodHandle THREAD32_NEXT = bind(KERNEL32, "Thread32Next",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.ADDRESS));
        static final MethodHandle WAIT_FOR_SINGLE = bind(KERNEL32, "WaitForSingleObject",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_INT));
        static final MethodHandle TERMINATE_PROCESS = bind(KERNEL32, "TerminateProcess",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.JAVA_INT));

        static final MethodHandle GET_FOREGROUND_WINDOW = bind(USER32, "GetForegroundWindow",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG));
        static final MethodHandle GET_WINDOW_THREAD_PROCESS_ID = bind(USER32,
                "GetWindowThreadProcessId",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG,
                        ValueLayout.ADDRESS));
        static final MethodHandle SET_WIN_EVENT_HOOK = bind(USER32, "SetWinEventHook",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.JAVA_INT,
                        ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG, ValueLayout.ADDRESS,
                        ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));
        static final MethodHandle UNHOOK_WIN_EVENT = bind(USER32, "UnhookWinEvent",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_LONG));
        static final MethodHandle GET_MESSAGE = bind(USER32, "GetMessageW",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS,
                        ValueLayout.JAVA_LONG, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));
        static final MethodHandle TRANSLATE_MESSAGE = bind(USER32, "TranslateMessage",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS));
        static final MethodHandle DISPATCH_MESSAGE = bind(USER32, "DispatchMessageW",
                FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.ADDRESS));
        static final MethodHandle PEEK_MESSAGE = bind(USER32, "PeekMessageW",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS,
                        ValueLayout.JAVA_LONG, ValueLayout.JAVA_INT, ValueLayout.JAVA_INT,
                        ValueLayout.JAVA_INT));

        static final MethodHandle TIME_BEGIN_PERIOD = bind(WINMM, "timeBeginPeriod",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));
        static final MethodHandle TIME_END_PERIOD = bind(WINMM, "timeEndPeriod",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.JAVA_INT));

        static final MethodHandle IS_USER_AN_ADMIN = bind(SHELL32(), "IsUserAnAdmin",
                FunctionDescriptor.of(ValueLayout.JAVA_INT));

        static final MethodHandle ENUM_PROCESSES = bind(PSAPI, "EnumProcesses",
                FunctionDescriptor.of(ValueLayout.JAVA_INT, ValueLayout.ADDRESS,
                        ValueLayout.JAVA_INT, ValueLayout.ADDRESS));
    }

    private static SymbolLookup SHELL32() {
        return SymbolLookup.libraryLookup("shell32.dll", Arena.global());
    }

    private static int call(MethodHandle handle, Object... args) {
        try {
            return (int) handle.invokeWithArguments(args);
        } catch (Throwable t) {
            throw new IllegalStateException(t);
        }
    }

    private static long callLong(MethodHandle handle, Object... args) {
        try {
            return (long) handle.invokeWithArguments(args);
        } catch (Throwable t) {
            throw new IllegalStateException(t);
        }
    }

    // ------------------------------------------------------------------
    // Process helpers
    // ------------------------------------------------------------------
    public static int currentPid() {
        return call(H.GET_CURRENT_PROCESS_ID);
    }

    public static boolean isElevated() {
        try {
            return call(H.IS_USER_AN_ADMIN) != 0;
        } catch (RuntimeException e) {
            return false;
        }
    }

    public static long openProcess(int pid, int access) {
        long handle = callLong(H.OPEN_PROCESS, access, 0, pid);
        return handle == 0L ? 0L : handle;
    }

    public static void closeHandle(long handle) {
        if (handle != 0L && handle != INVALID_HANDLE_VALUE) {
            call(H.CLOSE_HANDLE, handle);
        }
    }

    /** Returns {@code [basename, fullPath]} or {@code null}. */
    public static String[] processImageName(int pid) {
        long handle = openProcess(pid, PROCESS_QUERY_LIMITED_INFORMATION);
        if (handle == 0L) {
            return null;
        }
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment buffer = arena.allocate(2048);
            MemorySegment size = arena.allocate(ValueLayout.JAVA_INT);
            size.set(ValueLayout.JAVA_INT, 0, 1024);
            if (call(H.QUERY_IMAGE_NAME, handle, 0, buffer, size) == 0) {
                return null;
            }
            String full = buffer.getString(0, StandardCharsets.UTF_16LE);
            if (full.isEmpty()) {
                return null;
            }
            int slash = Math.max(full.lastIndexOf('\\'), full.lastIndexOf('/'));
            return new String[] {slash >= 0 ? full.substring(slash + 1) : full, full};
        }
    }

    public static boolean isAlive(int pid) {
        long handle = openProcess(pid, SYNCHRONIZE);
        if (handle == 0L) {
            return false;
        }
        try {
            return call(H.WAIT_FOR_SINGLE, handle, 0) == WAIT_TIMEOUT;
        } finally {
            closeHandle(handle);
        }
    }

    public static boolean terminate(int pid) {
        long handle = openProcess(pid, PROCESS_TERMINATE);
        if (handle == 0L) {
            return false;
        }
        try {
            return call(H.TERMINATE_PROCESS, handle, 0) != 0;
        } finally {
            closeHandle(handle);
        }
    }

    public static int foregroundPid() {
        try (Arena arena = Arena.ofConfined()) {
            long window = callLong(H.GET_FOREGROUND_WINDOW);
            if (window == 0L) {
                return 0;
            }
            MemorySegment pid = arena.allocate(ValueLayout.JAVA_INT);
            call(H.GET_WINDOW_THREAD_PROCESS_ID, window, pid);
            return pid.get(ValueLayout.JAVA_INT, 0);
        }
    }

    // ------------------------------------------------------------------
    // Enumeration
    // ------------------------------------------------------------------
    /** A live process, as returned by {@link #processes()}. */
    public record Proc(int pid, String name, String exe) {}

    /**
     * Snapshot every live process.
     *
     * <p>{@code EnumProcesses} also reports PIDs that have already exited; they
     * are filtered out by the failure to read an image name, which is the same
     * liveness test the Python backend relies on.
     */
    public static List<Proc> processes() {
        List<Proc> found = new ArrayList<>();
        for (int pid : processIds()) {
            String[] image = processImageName(pid);
            if (image != null) {
                found.add(new Proc(pid, image[0], image[1]));
            }
        }
        return found;
    }

    public static List<Integer> processIds() {
        List<Integer> ids = new ArrayList<>();
        int capacity = 4096;
        try (Arena arena = Arena.ofConfined()) {
            while (true) {
                MemorySegment buffer = arena.allocate(ValueLayout.JAVA_INT, capacity);
                MemorySegment needed = arena.allocate(ValueLayout.JAVA_INT);
                if (call(H.ENUM_PROCESSES, buffer, capacity * 4, needed) == 0) {
                    return ids;
                }
                int returned = needed.get(ValueLayout.JAVA_INT, 0) / 4;
                if (returned < capacity) {
                    for (int i = 0; i < returned; i++) {
                        int pid = buffer.getAtIndex(ValueLayout.JAVA_INT, i);
                        if (pid > 0) {
                            ids.add(pid);
                        }
                    }
                    return ids;
                }
                capacity *= 2;
            }
        }
    }

    // ------------------------------------------------------------------
    // Priority
    // ------------------------------------------------------------------
    public static int getPriorityClass(int pid) {
        long handle = openProcess(pid, PROCESS_QUERY_LIMITED_INFORMATION);
        if (handle == 0L) {
            return -1;
        }
        try {
            return call(H.GET_PRIORITY_CLASS, handle);
        } finally {
            closeHandle(handle);
        }
    }

    public static boolean setPriorityClass(int pid, int priorityClass) {
        long handle = openProcess(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION);
        if (handle == 0L) {
            return false;
        }
        try {
            return call(H.SET_PRIORITY_CLASS, handle, priorityClass) != 0;
        } finally {
            closeHandle(handle);
        }
    }

    public static String priorityName(int value) {
        return switch (value) {
            case IDLE_PRIORITY_CLASS -> "idle";
            case BELOW_NORMAL_PRIORITY_CLASS -> "below_normal";
            case NORMAL_PRIORITY_CLASS -> "normal";
            case ABOVE_NORMAL_PRIORITY_CLASS -> "above_normal";
            case HIGH_PRIORITY_CLASS -> "high";
            case REALTIME_PRIORITY_CLASS -> "realtime";
            default -> "unknown(" + value + ")";
        };
    }

    public static int priorityValue(String name) {
        return switch (name) {
            case "idle" -> IDLE_PRIORITY_CLASS;
            case "below_normal" -> BELOW_NORMAL_PRIORITY_CLASS;
            case "normal" -> NORMAL_PRIORITY_CLASS;
            case "above_normal" -> ABOVE_NORMAL_PRIORITY_CLASS;
            case "high" -> HIGH_PRIORITY_CLASS;
            case "realtime" -> REALTIME_PRIORITY_CLASS;
            default -> NORMAL_PRIORITY_CLASS;
        };
    }

    // ------------------------------------------------------------------
    // Threads
    // ------------------------------------------------------------------
    public record ThreadChange(int tid, int previousPriority) {}

    public static List<Integer> threadIds(int pid) {
        List<Integer> ids = new ArrayList<>();
        long snapshot = callLong(H.CREATE_SNAPSHOT, TH32CS_SNAPTHREAD, 0);
        if (snapshot == 0L || snapshot == INVALID_HANDLE_VALUE) {
            return ids;
        }
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment entry = arena.allocate(THREADENTRY32_SIZE);
            entry.set(ValueLayout.JAVA_INT, 0, (int) THREADENTRY32_SIZE);
            if (call(H.THREAD32_FIRST, snapshot, entry) == 0) {
                return ids;
            }
            while (true) {
                int owner = entry.get(ValueLayout.JAVA_INT, 12);
                if (owner == pid) {
                    ids.add(entry.get(ValueLayout.JAVA_INT, 8));
                }
                if (call(H.THREAD32_NEXT, snapshot, entry) == 0) {
                    break;
                }
            }
        } finally {
            closeHandle(snapshot);
        }
        return ids;
    }

    public static int getThreadPriority(int tid) {
        long handle = callLong(H.OPEN_THREAD, THREAD_QUERY_INFORMATION, 0, tid);
        if (handle == 0L) {
            return Integer.MIN_VALUE;
        }
        try {
            return call(H.GET_THREAD_PRIORITY, handle);
        } finally {
            closeHandle(handle);
        }
    }

    public static boolean setThreadPriority(int tid, int priority) {
        long handle = callLong(H.OPEN_THREAD, THREAD_SET_INFORMATION, 0, tid);
        if (handle == 0L) {
            return false;
        }
        try {
            return call(H.SET_THREAD_PRIORITY, handle, priority) != 0;
        } finally {
            closeHandle(handle);
        }
    }

    /** Raise every thread of {@code pid} to at least {@code priority}. */
    public static List<ThreadChange> raiseThreads(int pid, int priority) {
        List<ThreadChange> changes = new ArrayList<>();
        for (int tid : threadIds(pid)) {
            int current = getThreadPriority(tid);
            if (current == Integer.MIN_VALUE || current >= priority) {
                continue;
            }
            if (setThreadPriority(tid, priority)) {
                changes.add(new ThreadChange(tid, current));
            }
        }
        return changes;
    }

    /** Put recorded threads back, skipping IDs the kernel has recycled. */
    public static int restoreThreads(int pid, List<ThreadChange> changes) {
        if (changes == null || changes.isEmpty()) {
            return 0;
        }
        List<Integer> alive = threadIds(pid);
        int restored = 0;
        for (ThreadChange change : changes) {
            if (!alive.contains(change.tid())) {
                continue;
            }
            if (setThreadPriority(change.tid(), change.previousPriority())) {
                restored++;
            }
        }
        return restored;
    }

    /** Promote the calling thread. Used by the turbo keep-alive workers. */
    public static boolean promoteCurrentThread(int priority) {
        return call(H.SET_THREAD_PRIORITY, callLong(H.GET_CURRENT_THREAD), priority) != 0;
    }

    // ------------------------------------------------------------------
    // EcoQoS / affinity
    // ------------------------------------------------------------------
    public static boolean setEcoQos(int pid, boolean disable) {
        long handle = openProcess(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION);
        if (handle == 0L) {
            return false;
        }
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment state = arena.allocate(12);
            int control = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
                    | PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION;
            state.set(ValueLayout.JAVA_INT, 0, PROCESS_POWER_THROTTLING_CURRENT_VERSION);
            state.set(ValueLayout.JAVA_INT, 4, control);
            state.set(ValueLayout.JAVA_INT, 8, disable ? 0 : control);
            return call(H.SET_PROCESS_INFORMATION, handle, ProcessPowerThrottling, state, 12) != 0;
        } finally {
            closeHandle(handle);
        }
    }

    /** Returns {@code [processMask, systemMask]} or {@code null}. */
    public static long[] getAffinity(int pid) {
        long handle = openProcess(pid, PROCESS_QUERY_LIMITED_INFORMATION);
        if (handle == 0L) {
            return null;
        }
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment processMask = arena.allocate(ValueLayout.JAVA_LONG);
            MemorySegment systemMask = arena.allocate(ValueLayout.JAVA_LONG);
            if (call(H.GET_AFFINITY, handle, processMask, systemMask) == 0) {
                return null;
            }
            return new long[] {
                processMask.get(ValueLayout.JAVA_LONG, 0), systemMask.get(ValueLayout.JAVA_LONG, 0)
            };
        } finally {
            closeHandle(handle);
        }
    }

    public static boolean setAffinity(int pid, long mask) {
        long handle = openProcess(pid, PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION);
        if (handle == 0L) {
            return false;
        }
        try {
            return call(H.SET_AFFINITY, handle, mask) != 0;
        } finally {
            closeHandle(handle);
        }
    }

    // ------------------------------------------------------------------
    // Timer resolution
    // ------------------------------------------------------------------
    public static boolean timeBeginPeriod(int milliseconds) {
        return call(H.TIME_BEGIN_PERIOD, milliseconds) == 0;
    }

    public static boolean timeEndPeriod(int milliseconds) {
        return call(H.TIME_END_PERIOD, milliseconds) == 0;
    }

    // ------------------------------------------------------------------
    // Focus events
    // ------------------------------------------------------------------
    public interface ForegroundListener {
        void onForeground(int pid);
    }

    /**
     * Install an {@code EVENT_SYSTEM_FOREGROUND} hook.
     *
     * <p>The returned handle must be passed to {@link #unhook(long)}. The hook
     * only fires while this thread pumps messages, which is what
     * {@link #messageLoop} does.
     */
    public static long hookForeground(ForegroundListener listener, Arena arena) {
        MethodHandle callback;
        try {
            callback = MethodHandles.lookup()
                    .findVirtual(ForegroundListener.class, "onForeground",
                            MethodType.methodType(void.class, int.class))
                    .bindTo(listener);
        } catch (NoSuchMethodException | IllegalAccessException e) {
            throw new IllegalStateException("cannot bind the foreground listener", e);
        }
        MemorySegment stub = LINKER.upcallStub(
                callback,
                FunctionDescriptor.ofVoid(
                        ValueLayout.JAVA_LONG, // hWinEventHook
                        ValueLayout.JAVA_INT,  // event
                        ValueLayout.JAVA_LONG, // hwnd
                        ValueLayout.JAVA_INT,  // idObject
                        ValueLayout.JAVA_INT,  // idChild
                        ValueLayout.JAVA_INT,  // dwEventThread
                        ValueLayout.JAVA_INT), // dwmsEventTime
                arena);
        return callLong(H.SET_WIN_EVENT_HOOK, EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
                0L, stub, 0, 0, WINEVENT_OUTOFCONTEXT);
    }

    public static void unhook(long hook) {
        if (hook != 0L) {
            call(H.UNHOOK_WIN_EVENT, hook);
        }
    }

    /**
     * Pump messages until a stop condition says otherwise.
     *
     * <p>{@code GetMessageW} blocks, which is exactly what we want: the thread
     * costs nothing while the user is not switching windows, and reacts in
     * microseconds when they do.
     */
    public static void messageLoop(java.util.function.BooleanSupplier keepGoing) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment msg = arena.allocate(64);
            while (keepGoing.getAsBoolean()) {
                // Peek with a timeout so the stop condition is re-evaluated
                // even when no window event ever arrives.
                int result = call(H.PEEK_MESSAGE, msg, 0L, 0, 0, 0x0001 /* PM_REMOVE */);
                if (result != 0) {
                    call(H.TRANSLATE_MESSAGE, msg);
                    callLong(H.DISPATCH_MESSAGE, msg);
                } else {
                    java.util.concurrent.locks.LockSupport.parkNanos(20_000_000L);
                }
            }
        }
    }
}
