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

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Per-process boosting, mirroring {@code speedycpu/backends/windows.py}.
 *
 * <p>The record field names are deliberately identical to the Python
 * {@code BoostRecord} dataclass so either engine can write a rollback file the
 * other one understands. That is what makes "started by the Java agent, stopped
 * by the Python CLI" a safe combination rather than a foot-gun.
 */
public final class Booster {

    private Booster() {}

    /** Processes that must never be touched, even in {@code mode = all}. */
    private static final List<String> NEVER_TOUCH = List.of(
            "system", "system idle process", "idle", "registry", "memory compression",
            "secure system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
            "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe", "audiodg.exe",
            "wudfhost.exe", "speedycpu.exe", "s-cpu.exe", "java.exe", "javaw.exe");

    public static final class Record {
        public int pid;
        public String name = "";
        public String exe = "";
        public String priorityBefore;
        public String priorityAfter;
        public int threadsRaised;
        public int threadsTotal;
        public List<int[]> threadPriorities = new ArrayList<>();
        public boolean ecoqosDisabled;
        public Long affinityBefore;
        public int affinityCoresAdded;
        public String boostedAt = "";
        public String reason = "";
        public boolean manual;

        public Map<String, Object> toMap() {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("pid", pid);
            map.put("name", name);
            map.put("exe", exe);
            map.put("priority_before", priorityBefore);
            map.put("priority_after", priorityAfter);
            map.put("threads_raised", threadsRaised);
            map.put("threads_total", threadsTotal);
            List<Object> threads = new ArrayList<>();
            for (int[] pair : threadPriorities) {
                threads.add(List.of(pair[0], pair[1]));
            }
            map.put("thread_priorities", threads);
            map.put("ecoqos_disabled", ecoqosDisabled);
            map.put("affinity_before", affinityBefore);
            map.put("affinity_cores_added", affinityCoresAdded);
            map.put("hard_working_set", false);
            map.put("boosted_at", boostedAt);
            map.put("manual", manual);
            map.put("reason", reason);
            return map;
        }

        public static Record fromMap(Map<String, Object> map) {
            Record record = new Record();
            record.pid = Json.integer(map, "pid", 0);
            record.name = Json.string(map, "name", "");
            record.exe = Json.string(map, "exe", "");
            record.priorityBefore = (String) map.get("priority_before");
            record.priorityAfter = (String) map.get("priority_after");
            record.threadsRaised = Json.integer(map, "threads_raised", 0);
            record.threadsTotal = Json.integer(map, "threads_total", 0);
            record.ecoqosDisabled = Json.bool(map, "ecoqos_disabled", false);
            Object affinity = map.get("affinity_before");
            if (affinity instanceof Number number) {
                record.affinityBefore = number.longValue();
            }
            record.affinityCoresAdded = Json.integer(map, "affinity_cores_added", 0);
            record.boostedAt = Json.string(map, "boosted_at", "");
            record.reason = Json.string(map, "reason", "");
            record.manual = Json.bool(map, "manual", false);
            Object threads = map.get("thread_priorities");
            if (threads instanceof List<?> list) {
                for (Object item : list) {
                    if (item instanceof List<?> pair && pair.size() >= 2
                            && pair.get(0) instanceof Number tid
                            && pair.get(1) instanceof Number priority) {
                        record.threadPriorities.add(new int[] {tid.intValue(), priority.intValue()});
                    }
                }
            }
            return record;
        }
    }

    // ------------------------------------------------------------------
    // Matching
    // ------------------------------------------------------------------
    private static boolean glob(String pattern, String text) {
        StringBuilder regex = new StringBuilder("^");
        for (char c : pattern.toCharArray()) {
            switch (c) {
                case '*' -> regex.append(".*");
                case '?' -> regex.append('.');
                default -> regex.append(Pattern.quote(String.valueOf(c)));
            }
        }
        regex.append('$');
        return Pattern.compile(regex.toString(), Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE)
                .matcher(text)
                .matches();
    }

    private static boolean matchesAny(String name, List<String> patterns) {
        if (name == null || name.isEmpty()) {
            return false;
        }
        String lowered = name.toLowerCase(Locale.ROOT);
        String stem = lowered.endsWith(".exe") ? lowered.substring(0, lowered.length() - 4) : lowered;
        for (String raw : patterns) {
            if (raw == null) {
                continue;
            }
            String pattern = raw.trim().toLowerCase(Locale.ROOT);
            if (pattern.isEmpty()) {
                continue;
            }
            if (pattern.equals("*")) {
                return true;
            }
            if (glob(pattern, lowered) || glob(pattern, stem)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Why {@code processName} deserves a boost, or {@code null}.
     *
     * <p>Same rules as the Python backend: exclusion list first, then explicit
     * patterns, then - in {@code foreground} mode - only the focused window.
     * Boosting every process instead would leave their relative shares
     * unchanged, which is why so many "boosters" measure as a no-op.
     */
    public static String matchReason(String processName, int pid, Workspace.Settings settings,
            int foregroundPid) {
        if (processName == null || processName.isEmpty()) {
            return null;
        }
        String lowered = processName.toLowerCase(Locale.ROOT);
        if (NEVER_TOUCH.contains(lowered)) {
            return null;
        }
        if (matchesAny(processName, settings.exclude)) {
            return null;
        }
        List<String> patterns = new ArrayList<>();
        for (String target : settings.targets) {
            String trimmed = target == null ? "" : target.trim();
            if (!trimmed.isEmpty() && !trimmed.equals("*")) {
                patterns.add(trimmed);
            }
        }
        if (matchesAny(processName, patterns)) {
            return "pattern";
        }
        if ("foreground".equalsIgnoreCase(settings.mode)) {
            return pid == foregroundPid && pid != 0 ? "foreground" : null;
        }
        if (settings.targets.isEmpty() || matchesAny(processName, settings.targets)) {
            return "all";
        }
        return null;
    }

    // ------------------------------------------------------------------
    // Apply / revert
    // ------------------------------------------------------------------
    public static Record boost(int pid, String name, String exe, Workspace.Settings settings,
            String reason) {
        int current = Win32.getPriorityClass(pid);
        if (current < 0) {
            return null;
        }
        Record record = new Record();
        record.pid = pid;
        record.name = name;
        record.exe = exe == null ? "" : exe;
        record.priorityBefore = Win32.priorityName(current);
        record.reason = reason;
        record.boostedAt = java.time.LocalDateTime.now().toString();

        int target = settings.targetPriorityClass();
        if (current == Win32.REALTIME_PRIORITY_CLASS || current == Win32.HIGH_PRIORITY_CLASS) {
            // Never downgrade something that is already running higher.
            record.priorityAfter = Win32.priorityName(current);
        } else if (current >= target) {
            record.priorityAfter = Win32.priorityName(current);
        } else if (Win32.setPriorityClass(pid, target)) {
            record.priorityAfter = Win32.priorityName(target);
        } else {
            return null;
        }

        if (settings.raiseThreads) {
            List<Win32.ThreadChange> changes = Win32.raiseThreads(pid, Win32.THREAD_PRIORITY_HIGHEST);
            record.threadsRaised = changes.size();
            record.threadsTotal = Win32.threadIds(pid).size();
            for (Win32.ThreadChange change : changes) {
                record.threadPriorities.add(new int[] {change.tid(), change.previousPriority()});
            }
        }

        if (settings.disableEcoqos) {
            record.ecoqosDisabled = Win32.setEcoQos(pid, true);
        }

        if (settings.resetAffinity) {
            long[] masks = Win32.getAffinity(pid);
            if (masks != null && masks[0] != 0L && masks[0] != masks[1]) {
                if (Win32.setAffinity(pid, masks[1])) {
                    record.affinityBefore = masks[0];
                    record.affinityCoresAdded =
                            Long.bitCount(masks[1]) - Long.bitCount(masks[0]);
                }
            }
        }
        return record;
    }

    public static boolean unboost(Record record) {
        if (record == null) {
            return false;
        }
        if (!Win32.isAlive(record.pid)) {
            return true;
        }
        boolean touched = false;
        if (!record.threadPriorities.isEmpty()) {
            List<Win32.ThreadChange> changes = new ArrayList<>();
            for (int[] pair : record.threadPriorities) {
                changes.add(new Win32.ThreadChange(pair[0], pair[1]));
            }
            touched |= Win32.restoreThreads(record.pid, changes) > 0;
        }
        if (record.priorityBefore != null) {
            touched |= Win32.setPriorityClass(record.pid, Win32.priorityValue(record.priorityBefore));
        }
        if (record.ecoqosDisabled) {
            touched |= Win32.setEcoQos(record.pid, false);
        }
        if (record.affinityBefore != null && record.affinityBefore != 0L) {
            touched |= Win32.setAffinity(record.pid, record.affinityBefore);
        }
        return touched;
    }
}
