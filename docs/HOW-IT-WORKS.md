# How SpeedyCPU works

This document is the technical companion to the README. It explains which Win32
calls are made, why each one helps, what the failure modes are, and how the
revert path is guaranteed to be complete. If you are reviewing SpeedyCPU before
running it on a machine you care about, this is the file to read.

---

## 1. The problem with "CPU boosters"

Three claims appear on almost every product in this category. Two of them are
false and the third is usually implemented in a way that cancels itself out.

**"It makes your CPU faster."** False. The clock is set by the power management
unit, bound by the thermal design power of the package, and the operating system
can only ask for a *lower* ceiling, never a higher one. The maximum turbo
frequency is a firmware decision. Nothing running in user mode changes it.

**"It makes programs use more threads."** False, and usually stated as if thread
count were a dial. A program creates the threads it wants. An external tool
cannot inject work into another process' address space - and doing so would be
a ring-0 operation that no legitimate utility should attempt. The one honest
thing in this space is keeping the CPU from *parking* between bursts, which is
what SpeedyCPU's opt-in `turbo_threads` does, at a power cost.

**"It sets everything to high priority."** Technically true, and self-defeating.
The Windows scheduler allocates CPU in proportion to priority *within a priority
band*, but when every process in a band is raised, the relative shares inside
that band are exactly what they were before. A tool that walks the process list
setting `High` priority on all of it has changed nothing measurable - which is
why so many of them report success while their users feel nothing.

SpeedyCPU's approach follows from that last observation. The useful thing to
change is not the absolute priority of everything, but the *relative* priority
of the one process the user is looking at.

---

## 2. The core mechanism: focus-driven boosting

```
                every 200 ms (python)  /  immediately (native, java)
                            |
                            v
                 GetForegroundWindow()
                            |
                  GetWindowThreadProcessId()
                            |
                            v
        +-------------------------------------------+
        |  pid changed?                             |
        |   no  -> nothing to do (two syscalls)     |
        |   yes -> release the previous foreground   |
        |          process, then boost the new one   |
        +-------------------------------------------+
```

The insight is that the user experiences exactly one window at a time. Boosting
that window's process - and *only* that one - changes the ratio between the
focused application and the several hundred background processes that a modern
Windows install keeps alive. Releasing the boost when focus moves means the
previous application comes back down, so nothing accumulates.

This is also why the Python engine polls at 200 ms instead of the 3 s used for
full scans: the focus check costs two syscalls, and 200 ms is below the
threshold where a human notices a delay after alt-tab. The C++ and Java engines
do better than that by not polling at all: they subscribe to
`EVENT_SYSTEM_FOREGROUND` through `SetWinEventHook`, which delivers the change as
a window message.

### Why per-thread, not just per-process

`SetPriorityClass` sets the *base* priority of the process. Each thread has its
own priority value that is added to that base. Windows does not retroactively
raise existing threads to match a new base class in every case - and, more
importantly, threads that a program deliberately created at a lower priority
(e.g. a browser's background tabs) stay where they were. Walking the thread list
with `Thread32First`/`Thread32Next` and calling `SetThreadPriority` on each one is
what actually moves the whole process.

In practice, this is the single largest difference between SpeedyCPU and the
average "game booster": a browser at `High` with 200 normal-priority threads
still competes; the same browser with all 200 threads raised does not.

---

## 3. Per-process changes

All of these are applied by `WindowsBackend.boost()`, and every value that is
about to change is recorded into a `BoostRecord` first.

### 3.1 Priority class

```python
SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
```

Requires `PROCESS_SET_INFORMATION`. Skipped for protected processes (see §6).

### 3.2 Every thread

```python
for tid in iter_thread_ids(pid):
    handle = OpenThread(THREAD_SET_INFORMATION | THREAD_QUERY_INFORMATION, False, tid)
    before = GetThreadPriority(handle)          # recorded
    SetThreadPriority(handle, THREAD_PRIORITY_HIGHEST)
```

Two details matter:

- A thread may exit between the enumeration and the `OpenThread` call. This is
  normal and expected; the thread is simply skipped.
- Thread priorities set this way are *relative* to the process class, so a thread
  left at `THREAD_PRIORITY_HIGHEST` returns to its previous absolute priority
  when the process class is restored. SpeedyCPU records the previous value anyway
  so the revert is exact.

### 3.3 EcoQoS / power throttling

```python
SetProcessInformation(handle, ProcessPowerThrottling, &state, sizeof(state))
# state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
# state.StateMask   = 0
```

Windows 11 aggressively treats processes it classifies as "background" as
candidates for energy-efficient scheduling: they run on the E-cores of a hybrid
CPU and at a reduced clock. The classification is based on whether the process is
in the foreground, whether it has user input, and several heuristics. Setting the
control mask and clearing the state mask tells the power manager to stop
throttling this specific process, which is usually worth more than the priority
change on a modern hybrid CPU.

Requires Windows 8 or later. On older systems the call fails and is ignored.

### 3.4 Affinity

```python
GetProcessAffinityMask(handle, &process_mask, &system_mask)
if process_mask and process_mask != system_mask:
    SetProcessAffinityMask(handle, system_mask)
```

Some launchers, installers and legacy DRM wrappers pin a process to a single core
and never remove the mask. The process then runs on one core forever, no matter
how many threads it has. SpeedyCPU restores the system mask and records the
original one, including how many cores were added (used in the `status` output).

### 3.5 Working set (opt-in, `--aggressive`)

```python
SetProcessWorkingSetSizeEx(handle, size * 0.75, size * 1.5, QUOTA_LIMITS_HARDWS_MIN_ENABLE)
```

The memory manager trims the working set of a process that has been idle for a
few seconds. When the user alt-tabs back, the process takes soft page faults to
bring its pages back in, and *that* - not CPU scheduling - is often the source of
the "it takes a second to wake up" feeling.

Pinning the minimum working set stops that trim. It also holds those pages
hostage against every other process, so it is off by default. **Do not enable it
on a machine that is short on RAM.**

---

## 4. System-wide changes

### 4.1 Timer resolution

```python
timeBeginPeriod(1)      # winmm
```

Windows' default scheduler tick is 15.625 ms (1/64 s). A thread that calls
`Sleep(1)` in a loop actually sleeps 1-2 ticks, i.e. 15-31 ms. Games and audio
players that pace their frame or buffer loop with `Sleep` therefore cannot
produce a frame shorter than ~15.6 ms regardless of how fast the CPU is.

`timeBeginPeriod(1)` raises the global timer to 1 ms for as long as at least one
process holds the request. It is a reference-counted system-wide setting, so
releasing it is as important as taking it:

```python
timeEndPeriod(1)
```

This is applied by the engine process itself (the request is per-process but has
a system-wide effect). If the engine is killed, the request dies with it, so
there is nothing to clean up - the counter is maintained by the kernel.

The cost: a shorter tick means more timer interrupts, which costs a small amount
of idle power. On a desktop that is irrelevant; on battery it is not. Set
`timer_resolution = 0` to skip it.

Current resolution is reported by `NtQueryTimerResolution` for the `status` and
`doctor` output.

### 4.2 Power plan (needs administrator)

```python
powercfg /setactive <GUID>
```

Switching to High performance raises the minimum processor state to 100% and
disables core parking, which is effective and crude. It requires elevation, and
it is persistent - a crash before `s-cpu off` leaves the machine on the High
performance plan indefinitely. SpeedyCPU therefore records the previous scheme
GUID first and restores it by GUID, which works even if the user renamed the
plan in between.

### 4.3 Processor power settings (no administrator needed)

This is the reliable half, and the one most tools miss:

```
powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR <setting-guid> <value>
powercfg /setactive SCHEME_CURRENT
```

Editing the *current* scheme is allowed for a normal user; only activating a
*different* scheme requires elevation. SpeedyCPU tunes three settings:

| Setting | GUID | Value | Effect |
|---|---|---|---|
| Minimum processor state | `893dee8e-2bef-41e0-89c6-b55d0929964c` | `100` | Stops the CPU from dropping to low P-states at idle |
| Performance boost mode | `be337238-0d82-4146-a960-4f3749d470c7` | `2` (aggressive) | Chooses the aggressive turbo ramp policy |
| Processor performance core parking min cores | `0cc5b647-c1df-4637-891a-dec35c318583` | `100` | Disables core parking (percentage value, hence 100) |

`SUB_PROCESSOR` is the alias `54533251-82be-4824-96c1-47b60b740d00`.

Not every machine exposes every setting - a desktop with no core parking, for
instance, has no `cpmincores` value. SpeedyCPU queries each one first
(`powercfg /query`) and only writes the settings that exist, so a missing value
is skipped rather than written blindly.

The read-back is done by parsing `powercfg /query` output. That output is
localised - on a Chinese Windows install the plan is named "平衡" rather than
"Balanced" - which means the parser must decode it with the *console* code page
(`GetConsoleOutputCP`) rather than `locale.getpreferredencoding()`, which lies
under Python's UTF-8 mode. This was a real bug during development: the current
value came back as mojibake and the revert restored a garbage value.

### 4.4 Global power throttling (opt-in, needs administrator)

Writes `PowerThrottlingOff = 1` to
`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power\PowerThrottling`,
which disables EcoQoS machine-wide. Effective, invasive, and off by default;
`--global-throttling` opts in.

---

## 5. The engines

Three implementations of the same loop, for three different machines.

| | native (C++) | java | python |
|---|---|---|---|
| Binary | `scpu-watch.exe` | `scpu-agent.jar` | built in |
| Footprint | ~2 MB | ~60 MB (JVM) | ~15 MB |
| Focus detection | `SetWinEventHook` | `SetWinEventHook` (FFM) | polling, 200 ms |
| Timer helper | `scpu-timer.exe` | in-process | in-process |
| Needs | a C++ compiler to build | JDK 22+ to run | nothing |

The Java engine uses the **Foreign Function & Memory API** (final in JDK 22) to
call Win32 directly:

```java
Linker linker = Linker.nativeLinker();
SymbolLookup user32 = SymbolLookup.libraryLookup("user32", Arena.global());
MethodHandle hook = linker.downcallHandle(
        user32.find("SetWinEventHook").orElseThrow(), ...);
```

No JNI, no generated headers, no C++ compiler. The callback is an
`upcallStub` built from a Java method. The trade-off is the JVM's own footprint
and a hard requirement on JDK 22+; before that release the API was in preview and
the shape of it changed, so older JDKs are refused rather than attempted.

Because the FFM API is a *restricted* method, the jar's manifest must declare:

```
Enable-Native-Access: ALL-UNNAMED
```

Without it, the JVM refuses the call on JDK 25 and later. (`java/build.sh` and
`build.bat` both package the manifest for this reason.)

---

## 6. Refusing to touch things

Two independent gates, both enforced inside `Backend.match_reason()`:

1. **`NEVER_TOUCH`** - kernel and session-critical processes (`System`, `csrss.exe`,
   `winlogon.exe`, `lsass.exe`, `services.exe`, `svchost.exe`, `smss.exe`, …).
   These are refused unconditionally, even with `targets = ["*"]`. Raising the
   priority of `csrss.exe` does not make anything faster; it makes the machine
   unstable.
2. **`exclude`** (47 entries by default) - background noise: the shell, the
   search indexer, Defender components, updater services, the widget host, the
   Game Bar. Raising these starves the very application the user is trying to
   make responsive, so the exclusion list is part of the default configuration
   rather than something the user has to discover.

Exclusions are checked *before* targets, so an explicit
`s-cpu targets add dwm.exe` cannot override the safety net.

---

## 7. Rolling back completely

This is the part that matters most, and the part that is easiest to get subtly
wrong.

### 7.1 The record

`BoostRecord` captures everything needed to undo a boost, including
`thread_priorities: [[tid, priority_before], …]` for each thread that was
changed. The thread *IDs* are recorded, not just a count.

### 7.2 The thread ID reuse trap

The kernel recycles thread IDs. If a boost record says "thread 4820 was at
`THREAD_PRIORITY_NORMAL` before" and the process has since exited, thread 4820 may
now belong to something else entirely - and restoring "its" priority would
change an unrelated thread.

`restore_thread_priorities()` therefore re-enumerates the target process' threads
and only restores IDs that are *still owned by that PID*:

```python
live = set(iter_thread_ids(pid))
for tid, previous in record.thread_priorities:
    if tid not in live:
        continue                    # the thread died; nothing to restore
    ...
```

The same reasoning applies to the PID itself: every revert path checks
`is_alive(pid)` and skips dead processes, because a dead PID's new owner must not
inherit a priority change.

### 7.3 Cross-engine rollback files

Each engine writes its own rollback record *as it boosts*, not at exit, so that a
hard kill (`taskkill /F`, Task Manager "End task", power loss) still leaves enough
information to clean up:

| Engine | File | Format |
|---|---|---|
| python | `state.json` | `boosted` map, `str(pid) -> BoostRecord` |
| java | `agent.json` | `boosted` list of the same fields |
| native | `native-state.tsv` | `pid \t priority \t name \t tid:prio,tid:prio,…` |

The TSV exists because the C++ engine must be able to write it incrementally with
a single `fopen`/`fprintf`/`fclose` and no JSON library.

`s-cpu off` reads **all three** and reverts the union. The Python engine's record
is treated as authoritative for a given PID because it is the richest; the others
fill in gaps. That means `s-cpu off` is a complete rollback no matter which
engine ran, and no matter whether it exited cleanly.

### 7.4 Self-healing on start

```
s-cpu on
   |
   +-- read_engine_leftovers()  ->  anything a previous session left behind
   |                                 is reverted *before* anything new is applied
   |
   +-- then apply the new session
```

A crash cannot cause boosts to accumulate across sessions. This was added after a
development session where a hard-killed worker left `chrome.exe` sitting at `High`
priority with 200 raised threads indefinitely - exactly the failure mode a user
would never notice and could not easily fix by hand.

### 7.5 Uninstall

The Inno Setup script runs `s-cpu off` from `CurUninstallStepChanged` before it
removes any files, and again as an `[UninstallRun]` entry as a backstop for silent
uninstalls. Removing the binary that knows how to restore the machine, while the
machine is still modified, would be the worst possible ordering.

---

## 8. Failure modes and what happens

| Situation | Behaviour |
|---|---|
| Target process exits mid-boost | Record is dropped on the next scan; revert is a no-op |
| `OpenProcess` denied | The process is skipped and reported under `err.permission` |
| Protected process matched | Refused by `NEVER_TOUCH` before any call is made |
| Engine killed hard | Rollback file on disk; next `s-cpu on` or `s-cpu off` cleans up |
| `powercfg` output localised | Decoded using the console code page, not the locale encoding |
| Setting absent from the power scheme | Queried first; skipped instead of written |
| JDK older than 22 | Engine reported unavailable with the reason, Python engine used |
| Native binary missing | Engine reported unavailable, Python engine used |
| `s-cpu off` while nothing is running | Reports nothing to undo; still sweeps rollback files |
| `s-cpu unboost` partially fails | Failed records are **kept** so a retry (elevated) can finish the job |

---

## 9. What SpeedyCPU deliberately does not do

- **No kernel driver.** Everything here is documented user-mode API. A driver
  could do more, and would also mean the user has to trust a binary blob with
  ring-0 access.
- **No process injection.** No `WriteProcessMemory`, no hooking, no DLL
  injection. This is why kernel anti-cheat is unlikely to flag it (though some
  will still object to priority changes - see the README caveats).
- **No registry cleaning, "memory optimising", or service disabling.** Those are
  different products with different failure modes, and most of them are
  superstition.
- **No telemetry.** No network code at all, in any component.
- **No fake progress.** The CLI prints the actual previous and new values it read
  from the system, and `--dry-run` shows exactly what would change.

---

## 10. Verifying it yourself

Everything SpeedyCPU claims is checkable with tools already on the machine.

```bat
:: What the current timer resolution actually is
cd native && dist\scpu-timer.exe --probe

:: The current processor power settings, before and after
powercfg /query SCHEME_CURRENT SUB_PROCESSOR

:: A process' priority class, from PowerShell
(Get-Process chrome).PriorityClass

:: A specific thread's priority, from PowerShell
(Get-Process chrome).Threads | Select-Object Id, PriorityLevel

:: What SpeedyCPU thinks it has done
s-cpu status --json
```

Take those readings before `s-cpu on` and after, and after `s-cpu off`. The
`off` readings should be identical to the `before` readings - if any of them are
not, that is a bug worth reporting.

---

## 11. Related reading

- [`SetPriorityClass`](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)
- [`SetThreadPriority`](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadpriority)
- [`PROCESS_POWER_THROTTLING_STATE`](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_power_throttling_state)
- [EcoQoS / power throttling](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation)
- [`SetWinEventHook`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook)
- [`timeBeginPeriod`](https://learn.microsoft.com/en-us/windows/win32/api/timeapi/nf-timeapi-timebeginperiod)
- [Scheduling priorities](https://learn.microsoft.com/en-us/windows/win32/procthread/scheduling-priorities)
- [Powercfg command-line options](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options)
- [JEP 454: Foreign Function & Memory API](https://openjdk.org/jeps/454)
