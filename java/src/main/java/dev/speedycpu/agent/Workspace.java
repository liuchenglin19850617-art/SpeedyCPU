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

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The agent's view of the shared SpeedyCPU config/state files.
 *
 * <p>The Python CLI owns {@code config.json} and {@code stop.flag}; the agent
 * reads the first and honours the second, and writes its own
 * {@code agent.json} so the CLI can report on it and roll it back. Neither
 * process writes the other's file, which keeps the two implementations free of
 * locking games.
 */
public final class Workspace {

    public final Path home;
    public final Path configPath;
    public final Path agentPath;
    public final Path stopFlagPath;
    public final Path statePath;
    public final Path logPath;

    private Workspace(Path home) {
        this.home = home;
        this.configPath = home.resolve("config.json");
        this.agentPath = home.resolve("agent.json");
        this.statePath = home.resolve("state.json");
        this.stopFlagPath = home.resolve("stop.flag");
        this.logPath = home.resolve("agent.log");
    }

    public static Workspace locate() {
        String override = System.getenv("SPEEDYCPU_HOME");
        Path home;
        if (override != null && !override.isBlank()) {
            home = Path.of(override);
        } else {
            String localAppData = System.getenv("LOCALAPPDATA");
            if (localAppData != null && !localAppData.isBlank()) {
                home = Path.of(localAppData, "SpeedyCPU");
            } else {
                home = Path.of(System.getProperty("user.home"), ".speedycpu");
            }
        }
        try {
            Files.createDirectories(home);
        } catch (IOException ignored) {
            // The caller will surface a clearer error when a write fails.
        }
        return new Workspace(home);
    }

    /** Defaults mirror {@code speedycpu/config.py} deliberately. */
    public static final class Settings {
        public String mode = "foreground";
        public String priority = "high";
        public List<String> targets = new ArrayList<>();
        public List<String> exclude = new ArrayList<>(List.of(
                "system", "registry", "memory compression", "smss.exe", "csrss.exe", "wininit.exe",
                "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe",
                "audiodg.exe", "spoolsv.exe", "dwm.exe", "conhost.exe", "ctfmon.exe", "sihost.exe",
                "taskhostw.exe", "runtimebroker.exe", "shellexperiencehost.exe",
                "startmenuexperiencehost.exe", "textinputhost.exe", "searchhost.exe",
                "searchindexer.exe", "searchapp.exe", "msmpeng.exe", "mssense.exe", "nissrv.exe",
                "securityhealthservice.exe", "securityhealthsystray.exe", "wmiprvse.exe",
                "mousocoreworker.exe", "usoclient.exe", "tiworker.exe", "trustedinstaller.exe",
                "backgroundtaskhost.exe", "applicationframehost.exe", "sppsvc.exe", "dllhost.exe",
                "compattelrunner.exe", "widgetservice.exe", "widgets.exe", "phoneexperiencehost.exe",
                "yourphone.exe", "gamebar.exe", "gamebarftserver.exe", "speedycpu"));
        public boolean raiseThreads = true;
        public boolean disableEcoqos = true;
        public boolean resetAffinity = true;
        public int timerResolution = 1;
        public int reboostSeconds = 20;
        /** Clock keep-alive workers. Off by default: they cost idle power. */
        public int turboThreads = 0;

        public int targetPriorityClass() {
            return switch (priority.toLowerCase(Locale.ROOT)) {
                case "above_normal", "above" -> Win32.ABOVE_NORMAL_PRIORITY_CLASS;
                default -> Win32.HIGH_PRIORITY_CLASS;
            };
        }
    }

    public Settings loadSettings() {
        Settings settings = new Settings();
        try {
            Map<String, Object> map = Json.readObject(configPath);
            settings.mode = Json.string(map, "mode", settings.mode);
            settings.priority = Json.string(map, "priority", settings.priority);
            List<String> targets = Json.stringList(map, "targets");
            if (!targets.isEmpty()) {
                settings.targets = targets;
            }
            List<String> exclude = Json.stringList(map, "exclude");
            if (!exclude.isEmpty()) {
                settings.exclude = exclude;
            }
            settings.raiseThreads = Json.bool(map, "raise_threads", settings.raiseThreads);
            settings.disableEcoqos = Json.bool(map, "disable_ecoqos", settings.disableEcoqos);
            settings.resetAffinity = Json.bool(map, "reset_affinity", settings.resetAffinity);
            settings.timerResolution = Json.integer(map, "timer_resolution", settings.timerResolution);
            settings.reboostSeconds = Json.integer(map, "reboost_interval", settings.reboostSeconds);
        } catch (IOException | RuntimeException ignored) {
            // Fall back to defaults; a broken config must never stop the agent.
        }
        return settings;
    }

    // ------------------------------------------------------------------
    public void writeAgent(Map<String, Object> payload) {
        try {
            Files.writeString(agentPath, Json.write(payload), StandardCharsets.UTF_8);
        } catch (IOException ignored) {
            // Reporting is best effort.
        }
    }

    public void clearAgent() {
        try {
            Files.deleteIfExists(agentPath);
        } catch (IOException ignored) {
            // nothing to do
        }
    }

    public Map<String, Object> readAgent() {
        try {
            return Json.readObject(agentPath);
        } catch (IOException | RuntimeException e) {
            return new LinkedHashMap<>();
        }
    }

    public boolean stopRequested() {
        return Files.exists(stopFlagPath);
    }

    public void clearStopFlag() {
        try {
            Files.deleteIfExists(stopFlagPath);
        } catch (IOException ignored) {
            // nothing to do
        }
    }

    public void log(String message) {
        try {
            String stamp = java.time.LocalDateTime.now()
                    .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
            Files.writeString(logPath, stamp + " " + message + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    Files.exists(logPath)
                            ? java.nio.file.StandardOpenOption.APPEND
                            : java.nio.file.StandardOpenOption.CREATE);
        } catch (IOException ignored) {
            // logging must never break the agent
        }
    }
}
