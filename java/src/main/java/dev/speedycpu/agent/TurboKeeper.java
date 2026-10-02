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
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The honest implementation of "use more threads to feel smoother".
 *
 * <p>No external program can force another process to create more threads -
 * the kernel simply does not expose that. What <em>is</em> real is that a CPU
 * which has parked its cores and dropped to a deep sleep state needs a while to
 * come back, and that window is exactly the stutter the user notices when they
 * alt-tab or open a program. Keeping a small number of deliberately busy
 * high-priority threads alive holds the package in a high performance state, so
 * the foreground workload never waits for the silicon to wake up.
 *
 * <p>That is the whole trick, and it is a trade: the keep-alive threads burn
 * power and produce heat the entire time they run. On a laptop this is visible
 * in battery life. It is therefore opt-in ({@code --turbo N}, default 0) and
 * documented as such rather than enabled by default to look impressive.
 */
public final class TurboKeeper implements AutoCloseable {

    private final int threadCount;
    private final long burstNanos;
    private final long periodMillis;
    private final List<Thread> threads = new ArrayList<>();
    private final AtomicBoolean running = new AtomicBoolean(false);

    /** Written by every worker so the JIT can never prove the loop is dead. */
    private static final AtomicLong SINK = new AtomicLong();

    public TurboKeeper(int threadCount, long burstMicros, long periodMillis) {
        this.threadCount = Math.max(0, threadCount);
        this.burstNanos = Math.max(200_000L, burstMicros * 1_000L);
        this.periodMillis = Math.max(10L, periodMillis);
    }

    public int threadCount() {
        return threadCount;
    }

    public boolean isRunning() {
        return running.get();
    }

    public void start() {
        if (threadCount == 0 || !running.compareAndSet(false, true)) {
            return;
        }
        for (int i = 0; i < threadCount; i++) {
            Thread worker = new Thread(this::loop, "speedycpu-turbo-" + i);
            worker.setDaemon(true);
            threads.add(worker);
            worker.start();
        }
    }

    private void loop() {
        // Ask Windows for real-time-adjacent scheduling for this worker. Java's
        // own Thread.setPriority() maps only coarsely, so the native call is
        // what actually matters here.
        Win32.promoteCurrentThread(Win32.THREAD_PRIORITY_HIGHEST);

        long value = 0x9E3779B97F4A7C15L;
        long deadline;
        while (running.get() && !Thread.currentThread().isInterrupted()) {
            deadline = System.nanoTime() + burstNanos;
            // A short, bounded arithmetic burst: enough to keep the core out of
            // deep idle, far too short to be noticed by the user.
            while (System.nanoTime() < deadline) {
                value ^= value >>> 12;
                value ^= value << 25;
                value ^= value >>> 27;
            }
            SINK.lazySet(value);
            try {
                Thread.sleep(periodMillis);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
    }

    @Override
    public void close() {
        running.set(false);
        for (Thread worker : threads) {
            worker.interrupt();
        }
        for (Thread worker : threads) {
            try {
                worker.join(500);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        threads.clear();
    }

    public static long sink() {
        return SINK.get();
    }
}
