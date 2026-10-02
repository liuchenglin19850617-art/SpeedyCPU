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
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * SpeedyCPU agent - the event-driven accelerator.
 *
 * <pre>
 * scpu-agent start [--turbo N] [--priority high|above_normal] [--mode foreground|all]
 * scpu-agent stop
 * scpu-agent status [--json]
 * scpu-agent boost &lt;pid|name&gt;
 * scpu-agent release
 * </pre>
 *
 * <p>Why this exists next to the Python worker: the Python worker has to poll
 * the focused window, and every poll is either latency (a short interval costs
 * CPU) or a missed fast alt-tab (a long interval). {@code SetWinEventHook}
 * removes that trade entirely - the kernel calls back the instant focus changes,
 * and the hook thread costs nothing in between because it is blocked in a
 * message pump.
 *
 * <p>The Python CLI starts and stops this agent; it is not meant to be driven by
 * hand. It is also fully optional: {@code s-cpu on --engine python} skips it.
 */
public final class AgentMain {

    private static final Map<Integer, Booster.Record> BOOSTED = new ConcurrentHashMap<>();
    private static final AtomicBoolean RUNNING = new AtomicBoolean(true);

    private static Workspace workspace;
    private static Workspace.Settings settings;
    private static int timerMilliseconds;
    private static TurboKeeper turbo;
    private static int foregroundPid;

    public static void main(String[] args) {
        workspace = Workspace.locate();
        settings = workspace.loadSettings();

        if (args.length == 0) {
            usage();
            System.exit(2);
        }
        String command = args[0].toLowerCase(Locale.ROOT);
        List<String> rest = List.of(args).subList(1, args.length);

        switch (command) {
            case "start" -> System.exit(start(rest));
            case "stop" -> System.exit(stop());
            case "status" -> System.exit(status(rest));
            case "boost" -> System.exit(boostOnce(rest));
            case "release" -> System.exit(release());
            case "-h", "--help", "help" -> {
                usage();
                System.exit(0);
            }
            default -> {
                System.err.println("unknown command: " + command);
                usage();
                System.exit(2);
            }
        }
    }

    private static void usage() {
        System.out.println("""
                SpeedyCPU agent %s

                usage:
                  scpu-agent start [--turbo N] [--priority high|above_normal] [--mode foreground|all]
                  scpu-agent stop
                  scpu-agent status [--json]
                  scpu-agent boost <pid|name>
                  scpu-agent release
                """.formatted(version()));
    }

    public static String version() {
        return "0.1.0";
    }

    // ------------------------------------------------------------------
    // start
    // ------------------------------------------------------------------
    private static int start(List<String> args) {
        int turboThreads = settings.turboThreads;
        for (int i = 0; i < args.size(); i++) {
            switch (args.get(i)) {
                case "--turbo" -> {
                    if (i + 1 < args.size()) {
                        turboThreads = Integer.parseInt(args.get(++i));
                    }
                }
                case "--priority" -> {
                    if (i + 1 < args.size()) {
                        settings.priority = args.get(++i);
                    }
                }
                case "--mode" -> {
                    if (i + 1 < args.size()) {
                        settings.mode = args.get(++i);
                    }
                }
                default -> { }
            }
        }

        int existing = Json.integer(workspace.readAgent(), "pid", 0);
        if (existing > 0 && existing != Win32.currentPid() && Win32.isAlive(existing)) {
            System.out.println("agent already running (pid " + existing + ")");
            return 0;
        }

        workspace.clearStopFlag();
        Runtime.getRuntime().addShutdownHook(new Thread(AgentMain::shutdown, "speedycpu-shutdown"));

        timerMilliseconds = Math.max(0, settings.timerResolution);
        if (timerMilliseconds > 0 && Win32.timeBeginPeriod(timerMilliseconds)) {
            workspace.log("timer resolution requested: " + timerMilliseconds + " ms");
        } else {
            timerMilliseconds = 0;
        }

        turbo = new TurboKeeper(turboThreads, 900, 120);
        turbo.start();
        if (turboThreads > 0) {
            workspace.log("turbo keep-alive started with " + turboThreads + " thread(s)");
        }

        publish("running");

        // The upcall stub installed by the hook must outlive the message loop,
        // so it lives in the global arena rather than a confined one.
        arena = Arena.global();
        long hook = Win32.hookForeground(AgentMain::onForeground, arena);
        if (hook == 0L) {
            workspace.log("SetWinEventHook failed; falling back to polling");
        } else {
            workspace.log("foreground hook installed");
        }

        // Initial pass so the app already on screen gets boosted immediately.
        onForeground(Win32.foregroundPid());

        Thread maintainer = new Thread(AgentMain::maintainLoop, "speedycpu-maintainer");
        maintainer.setDaemon(true);
        maintainer.start();

        workspace.log("agent " + Win32.currentPid() + " started, scope=" + scope());
        System.out.println("SpeedyCPU agent running (pid " + Win32.currentPid() + ")");
        System.out.flush();

        Win32.messageLoop(() -> RUNNING.get() && !workspace.stopRequested());
        Win32.unhook(hook);
        shutdown();
        return 0;
    }

    private static Arena arena;

    private static String scope() {
        if ("foreground".equalsIgnoreCase(settings.mode)) {
            List<String> patterns = new ArrayList<>();
            for (String target : settings.targets) {
                String trimmed = target == null ? "" : target.trim();
                if (!trimmed.isEmpty() && !trimmed.equals("*")) {
                    patterns.add(trimmed);
                }
            }
            return patterns.isEmpty()
                    ? "foreground window"
                    : "foreground window + " + String.join(", ", patterns);
        }
        return "all user processes";
    }

    /**
     * Called by the kernel whenever the focused window changes.
     *
     * <p>The previous focused process is released first. Without that half the
     * trick the whole thing is pointless: if yesterday's app keeps its elevated
     * priority, the new one has not actually gained anything.
     */
    private static void onForeground(int pid) {
        int previous;
        synchronized (AgentMain.class) {
            previous = foregroundPid;
            foregroundPid = pid;
        }
        if (previous != 0 && previous != pid) {
            Booster.Record record = BOOSTED.get(previous);
            if (record != null && "foreground".equals(record.reason)) {
                if (Booster.unboost(record)) {
                    BOOSTED.remove(previous);
                    workspace.log("released " + record.name + " (" + previous + ") on focus loss");
                    publish("running");
                }
            }
        }
        if (pid != 0 && !BOOSTED.containsKey(pid)) {
            String[] image = Win32.processImageName(pid);
            if (image != null
                    && "foreground".equals(Booster.matchReason(image[0], pid, settings, pid))) {
                boost(pid, image[0], image[1], "foreground");
            }
        }
    }

    private static boolean boost(int pid, String name, String exe, String reason) {
        long started = System.nanoTime();
        Booster.Record record = Booster.boost(pid, name, exe, settings, reason);
        if (record == null) {
            workspace.log("skip " + name + " (" + pid + "): not accessible");
            return false;
        }
        BOOSTED.put(pid, record);
        long micros = (System.nanoTime() - started) / 1000L;
        workspace.log(String.format("boosted %s (%d) [%s] %s -> %s, %d/%d threads, %d us",
                name, pid, reason, record.priorityBefore, record.priorityAfter,
                record.threadsRaised, record.threadsTotal, micros));
        publish("running");
        return true;
    }

    /** Re-apply boosts the OS or the target itself may have dropped. */
    private static void maintainLoop() {
        long interval = Math.max(5, settings.reboostSeconds) * 1000L;
        while (RUNNING.get() && !workspace.stopRequested()) {
            try {
                Thread.sleep(interval);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
            for (Map.Entry<Integer, Booster.Record> entry : new ArrayList<>(BOOSTED.entrySet())) {
                int pid = entry.getKey();
                Booster.Record record = entry.getValue();
                if (!Win32.isAlive(pid)) {
                    BOOSTED.remove(pid);
                    continue;
                }
                int current = Win32.getPriorityClass(pid);
                if (current >= 0 && current < settings.targetPriorityClass()) {
                    workspace.log("re-boosting " + record.name + " (" + pid + "): priority drifted");
                    Booster.Record fresh = Booster.boost(pid, record.name, record.exe, settings,
                            record.reason);
                    if (fresh != null) {
                        // Keep the *original* baseline: it describes the state
                        // before SpeedyCPU ever touched the process.
                        fresh.priorityBefore = record.priorityBefore;
                        fresh.threadPriorities.addAll(record.threadPriorities);
                        BOOSTED.put(pid, fresh);
                    }
                }
            }
            if ("all".equalsIgnoreCase(settings.mode)) {
                for (Win32.Proc process : Win32.processes()) {
                    if (process.pid() == Win32.currentPid() || BOOSTED.containsKey(process.pid())) {
                        continue;
                    }
                    String reason = Booster.matchReason(process.name(), process.pid(), settings,
                            foregroundPid);
                    if (reason != null) {
                        boost(process.pid(), process.name(), process.exe(), reason);
                    }
                }
            }
            publish("running");
        }
    }

    // ------------------------------------------------------------------
    // stop / release / status
    // ------------------------------------------------------------------
    private static int stop() {
        Map<String, Object> agent = workspace.readAgent();
        int pid = Json.integer(agent, "pid", 0);
        if (pid <= 0) {
            System.out.println("agent is not running");
            return 0;
        }
        try {
            java.nio.file.Files.writeString(workspace.stopFlagPath, "stop");
        } catch (java.io.IOException e) {
            System.err.println("cannot write stop flag: " + e.getMessage());
        }
        long deadline = System.currentTimeMillis() + 12_000L;
        while (System.currentTimeMillis() < deadline) {
            if (!Win32.isAlive(pid)) {
                System.out.println("agent stopped (pid " + pid + ")");
                return 0;
            }
            try {
                Thread.sleep(150);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        System.out.println("agent did not exit in time, terminating (pid " + pid + ")");
        Win32.terminate(pid);
        return 0;
    }

    private static int release() {
        int reverted = 0;
        for (Map.Entry<Integer, Booster.Record> entry : new ArrayList<>(BOOSTED.entrySet())) {
            if (Booster.unboost(entry.getValue())) {
                reverted++;
            }
            BOOSTED.remove(entry.getKey());
        }
        // Also sweep anything a previous run left behind.
        for (Object item : listOf(workspace.readAgent().get("boosted"))) {
            if (item instanceof Map<?, ?> map) {
                @SuppressWarnings("unchecked")
                Booster.Record record = Booster.Record.fromMap((Map<String, Object>) map);
                if (record.pid > 0 && Booster.unboost(record)) {
                    reverted++;
                }
            }
        }
        workspace.clearAgent();
        System.out.println("released " + reverted + " process(es)");
        return 0;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> listOf(Object value) {
        return value instanceof List<?> list ? (List<Object>) list : List.of();
    }

    private static int boostOnce(List<String> args) {
        if (args.isEmpty()) {
            System.err.println("usage: scpu-agent boost <pid|name>");
            return 2;
        }
        String spec = args.get(0);
        List<Win32.Proc> targets = new ArrayList<>();
        if (spec.chars().allMatch(Character::isDigit)) {
            int pid = Integer.parseInt(spec);
            String[] image = Win32.processImageName(pid);
            if (image != null) {
                targets.add(new Win32.Proc(pid, image[0], image[1]));
            }
        } else {
            for (Win32.Proc process : Win32.processes()) {
                if (process.name().toLowerCase(Locale.ROOT)
                        .contains(spec.toLowerCase(Locale.ROOT))) {
                    targets.add(process);
                }
            }
        }
        if (targets.isEmpty()) {
            System.out.println("no running process matched '" + spec + "'");
            return 1;
        }
        int count = 0;
        for (Win32.Proc process : targets) {
            if (boost(process.pid(), process.name(), process.exe(), "manual")) {
                count++;
                Booster.Record record = BOOSTED.get(process.pid());
                System.out.printf("%s (pid %d): priority %s -> %s, %d/%d threads%n",
                        record.name, record.pid, record.priorityBefore, record.priorityAfter,
                        record.threadsRaised, record.threadsTotal);
            }
        }
        publish("running");
        System.out.println("boosted " + count + " process(es)");
        return count > 0 ? 0 : 1;
    }

    private static int status(List<String> args) {
        Map<String, Object> agent = workspace.readAgent();
        int pid = Json.integer(agent, "pid", 0);
        boolean alive = pid > 0 && Win32.isAlive(pid);
        boolean json = args.contains("--json");

        if (json) {
            Map<String, Object> payload = new LinkedHashMap<>(agent);
            payload.put("alive", alive);
            payload.put("engine", "java");
            payload.put("version", version());
            payload.put("turbo_threads", Json.integer(agent, "turboThreads", 0));
            System.out.println(Json.write(payload));
            return 0;
        }
        System.out.println("engine        : java " + version());
        System.out.println("state         : " + (alive ? "running" : "stopped"));
        System.out.println("pid           : " + (pid > 0 ? pid : "-"));
        System.out.println("scope         : " + Json.string(agent, "scope", scope()));
        System.out.println("timer         : " + Json.integer(agent, "timerMs", 0) + " ms");
        System.out.println("turbo threads : " + Json.integer(agent, "turboThreads", 0));
        System.out.println("elevated      : " + Win32.isElevated());
        System.out.println("boosted       : " + listOf(agent.get("boosted")).size());
        return 0;
    }

    // ------------------------------------------------------------------
    private static void publish(String state) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("engine", "java");
        payload.put("version", version());
        payload.put("pid", Win32.currentPid());
        payload.put("state", state);
        payload.put("startedAt", startedAt);
        payload.put("mode", settings.mode);
        payload.put("scope", scope());
        payload.put("priority", settings.priority);
        payload.put("timerMs", timerMilliseconds);
        payload.put("turboThreads", turbo == null ? 0 : turbo.threadCount());
        payload.put("elevated", Win32.isElevated());
        payload.put("foregroundPid", foregroundPid);
        List<Object> records = new ArrayList<>();
        for (Booster.Record record : BOOSTED.values()) {
            records.add(record.toMap());
        }
        payload.put("boosted", records);
        workspace.writeAgent(payload);
    }

    private static final String startedAt = java.time.LocalDateTime.now().toString();

    private static void shutdown() {
        if (!RUNNING.compareAndSet(true, false)) {
            return;
        }
        int reverted = 0;
        for (Map.Entry<Integer, Booster.Record> entry : new ArrayList<>(BOOSTED.entrySet())) {
            if (Booster.unboost(entry.getValue())) {
                reverted++;
            }
        }
        BOOSTED.clear();
        if (turbo != null) {
            turbo.close();
        }
        if (timerMilliseconds > 0) {
            Win32.timeEndPeriod(timerMilliseconds);
        }
        workspace.clearStopFlag();
        workspace.clearAgent();
        workspace.log("agent stopped, reverted " + reverted + " process(es)");
    }
}
