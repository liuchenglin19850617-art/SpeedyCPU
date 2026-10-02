<div align="center">

# SpeedyCPU

**A command-line CPU responsiveness booster for Windows**

`s-cpu on` applies boosting. `s-cpu off` restores every value that was changed.

[![CI](https://github.com/speedycpu/speedycpu/actions/workflows/ci.yml/badge.svg)](https://github.com/speedycpu/speedycpu/actions/workflows/ci.yml)
[![License: LGPL-2.0-or-later](https://img.shields.io/badge/license-LGPL--2.0--or--later-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)](#platform-support)

[简体中文](README.zh-CN.md) · [How it works](docs/HOW-IT-WORKS.md) · [Changelog](CHANGELOG.md)

</div>

---

## Scope of the program

SpeedyCPU does not increase the speed of a central processing unit. No program
can do so. There is no register to unlock, no concealed core to enable, and no
optimisation that raises a processor beyond its own clock or power envelope.

Nor does SpeedyCPU cause a program to create threads that it was not written to
create. A single-threaded program remains single-threaded irrespective of what
runs alongside it.

**SpeedyCPU determines which process is granted the CPU when several processes
compete for it.** This effect is real and measurable. The process the operator is
working in is no longer starved by the background processes that a standard
Windows installation starts; frame pacing and input latency improve; foreground
animation maintains its frame rate. The program achieves this by means of
documented Win32 APIs, reports every value it has modified, and restores each of
them under `s-cpu off`.

Software that promises a faster processor or a "multi-thread boost" offers either
a placebo or an increase in resource consumption. The present project states in
[How it works](docs/HOW-IT-WORKS.md) exactly which mechanisms it uses.

## Modifications applied

| Area | Modification | Purpose |
|---|---|---|
| Process priority | The target process is raised to `High`; each of its threads is then raised individually | The scheduler prefers this process over background work. Raising individual threads is the step most tools omit, and it is the step that matters for a browser or a game exposing more than one hundred threads. |
| Power throttling | The [EcoQoS](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation) flag is cleared for the process | Windows assigns "background" work to low-power cores and applies a clock cap. Clearing the flag un-parks the work. |
| Focus | Only the process owning the foreground window is boosted; moving focus releases the previously boosted process | Raising *every* process to `High` leaves the ratios between them unchanged, which is why boosters that take that approach produce no measurable benefit. Prioritising the focused application is the half of the mechanism that alters the outcome. |
| Affinity | A residual single-core affinity mask is cleared | Launchers and some digital-rights-management wrappers pin a game to one core and do not release the pin. |
| Timer resolution | A 1 ms timer is requested in place of the 15.625 ms default for the duration of the session | Games and audio use `Sleep`-based pacing. At 15.625 ms the shortest frame that can be paced is 15.6 ms; at 1 ms it is 1 ms. |
| Processor power settings | The *active* power scheme is tuned: minimum processor state, performance boost mode, core parking | Effective **without administrator privileges**, unlike switching to the High performance plan. |
| Optional | Working-set pinning (`--aggressive`), machine-wide power throttling (`--global-throttling`), High performance plan switch | Disabled by default; each incurs a cost. See [Limitations](#limitations). |

Every value listed above is recorded before it is modified, and is restored by
`s-cpu off`, by the exit of the background worker, and by uninstallation. No
registry entry and no system directory is written.

## Repository layout

| Path | Contents |
|---|---|
| `source/` | The complete source tree: Python CLI, C++ engine, Java agent, installer script, test suite and documentation |
| `release/` | Assembled distributables. One subfolder per release, for example `release/SpeedyCPU-0.1.0-win64/` |

## Installation

### Windows installer

Obtain `SpeedyCPU-Setup-x.y.z.exe` from
[Releases](https://github.com/speedycpu/speedycpu/releases) and execute it. The
installer adds the installation directory to the **user** PATH (no restart, no
system-wide change), offers to enable boosting immediately, and executes
`s-cpu off` prior to uninstallation.

### Portable archive

Obtain `SpeedyCPU-x.y.z-win64.zip`, extract it to any location, and run
`s-cpu.cmd` from that directory. Alternatively, add the directory to PATH so that
`s-cpu` is available from any terminal:

```bat
setx PATH "%PATH%;C:\path\to\SpeedyCPU-0.1.0-win64"
```

### Package index

```bat
pip install speedycpu
```

This provides the command-line interface and the built-in Python engine. It does
not include the optional C++ or Java engines; see [Engines](#engines).

### From source

```bat
git clone https://github.com/speedycpu/speedycpu
cd speedycpu
pip install -e ".[dev]"
python -m speedycpu on
```

To additionally build the optional engines, see [Building](#building).

## Usage

```bat
s-cpu on          :: enable boosting; any program started afterwards is boosted
s-cpu status      :: report what is active and which processes are boosted
s-cpu off         :: disable boosting, stop the background worker, restore all values
```

These three commands constitute the product. Every other subcommand provides
diagnostics or fine tuning:

```bat
s-cpu doctor                     :: self-check of the environment, and of what is unavailable
s-cpu targets add chrome.exe     :: always boost this program, regardless of focus
s-cpu targets add game*.exe
s-cpu targets list               :: list the include patterns and the exclusion list
s-cpu boost notepad.exe          :: boost one program immediately, without a background worker
s-cpu unboost                    :: undo boosts
s-cpu config set mode all        :: boost every user process instead of only the focused one
s-cpu config show                :: display every setting and its current value
s-cpu on --dry-run               :: print exactly what would change, and change nothing
```

`s-cpu on --help` documents the per-session overrides (`--engine`, `--mode`,
`--priority`, `--no-cpu-tuning`, `--no-power-plan`, `--timer-ms`, `--aggressive`,
`--global-throttling`, `--no-daemon`). The global flags (`--dry-run`,
`--no-color`, `-q`, `-v`, `--lang`) are accepted on either side of the
subcommand; both `s-cpu on --dry-run` and `s-cpu --dry-run on` are valid.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | An error occurred: no match, access denied, or the engine failed to start |
| `2` | Invalid usage |

`--dry-run` reports the actions that would be taken without performing them, and
is therefore safe to use in automated contexts.

### Terminal output

Output is restricted to plain ASCII. Each severity is prefixed with a bracketed
tag, so that the emitted bytes are identical in `cmd.exe`, in PowerShell, in a CI
log, and in a redirected file:

```
[ OK ]   CPU power settings restored to their pre-boost values
[STEP]   writing state.json
[ WARN]  not elevated, skipping power plan changes
[FAIL]   could not revert notepad.exe (pid 4821)
```

No pictographs, check marks, or box-drawing characters are emitted.

### Language

English is the default language. The operating-system locale is deliberately not
consulted, so that a Chinese or Japanese Windows build does not switch a program
whose documentation and releases are English. Simplified Chinese is an explicit
opt-in, resolved in the following order:

| Source | Example |
|---|---|
| `--lang` flag | `s-cpu --lang zh status` |
| `SPEEDYCPU_LANG` | `set SPEEDYCPU_LANG=zh` |
| `config.json` | `s-cpu config set lang zh` |

## Engines

SpeedyCPU provides three interchangeable engines. They issue the same Win32 calls
and write the same rollback records; they differ in footprint and in the latency
with which they detect a change of focus.

| Engine | Footprint | Focus detection | Selected when |
|---|---|---|---|
| **native** (C++) | approximately 2 MB | `SetWinEventHook`, event-driven, immediate | default, provided it has been built |
| **java** (JAR) | approximately 60 MB | `SetWinEventHook`, event-driven, immediate | no C++ build is present and a JDK 22 or newer is available |
| **python** (built in) | approximately 15 MB | polling every 200 ms | always available; used as fallback |

`auto`, the default, evaluates that list in order and selects the first engine
that is present. `s-cpu doctor` reports which engines this machine provides. An
engine may be forced with `s-cpu on --engine native`.

The Python engine is the reason `pip install speedycpu` functions without any
compiler: it is pure standard library, and every Win32 call is a hand-written
`ctypes` binding. There is no dependency on `psutil`, on `pywin32`, or on any
other third-party package.

The Java engine requires a JDK 22 or newer on PATH. The agent accesses Win32
through the Foreign Function & Memory API, so no JNI and no compiler are involved.
(JDK 21 and older are rejected rather than attempted, for the same reason.)

## Configuration

All state is stored under `%LOCALAPPDATA%\SpeedyCPU\`:

| File | Purpose |
|---|---|
| `config.json` | User preferences |
| `state.json` | What is currently applied, so that it can be undone |
| `native-state.tsv` / `agent.json` | Rollback records written by the C++ and Java engines |
| `worker.log` / `native.log` / `agent.log` | Engine logs |

The configuration is edited with `s-cpu config set` or by hand. The keys that
alter behaviour most:

| Key | Default | Meaning |
|---|---|---|
| `engine` | `auto` | `auto` \| `native` \| `java` \| `python` |
| `mode` | `foreground` | `foreground` (focused window only) \| `all` (every user process) |
| `targets` | `[]` | Process patterns to boost unconditionally, for example `["chrome.exe", "game*.exe"]` |
| `exclude` | 47 entries | Never touched. Enumerated by `s-cpu targets list` |
| `priority` | `high` | `high` \| `above_normal` |
| `cpu_tuning` | `true` | Tune processor power settings (no administrator rights required) |
| `power_plan` | `high_performance` | `high_performance` \| `ultimate` \| `balanced` \| `<GUID>` \| `none` |
| `timer_resolution` | `1` | Timer resolution to request, in milliseconds; `0` disables the feature |
| `aggressive` | `false` | Also pin the working set of each boosted process |
| `global_power_throttling_off` | `false` | Disable machine-wide EcoQoS (requires administrator rights) |
| `poll_interval` | `3.0` | Interval between full process scans, in seconds |
| `turbo_threads` | `0` | Java engine only. Described below |

`mode = all` is implemented, but is in most cases a regression: raising every
process to `High` preserves the ratios between them. `foreground` is the default
because it is the mode that changes the outcome.

`turbo_threads` is the literal implementation of "use more threads". The Java
agent starts *N* high-priority threads performing short arithmetic bursts to
prevent the processor from entering deep idle states, which measurably reduces
the latency of the *next* burst of work. It also consumes the power of one core
for the remainder of the time, which is why it is disabled by default and must be
requested explicitly.

## Limitations

SpeedyCPU is a scheduling tool. The following limitations are inherent:

- **A clock cannot be raised.** A processor operating at its thermal or power
  limit remains there. If a laptop is throttling because it is hot, the cooling
  path must be serviced.
- **Threads cannot be added.** A single-threaded program remains single-threaded.
- **`--aggressive` exchanges memory for responsiveness.** It pins the working set
  of each boosted process so that the memory manager cannot trim it while the
  operator switches away. This is effective, and it also means those pages are
  unavailable to other processes. It should not be used on a host with a memory
  shortage.
- **`turbo_threads` consumes battery.** See the description above.
- **Kernel anti-cheat software may object.** Kernel-level anti-cheat can treat
  modification of another process's priority or affinity as suspicious.
  SpeedyCPU issues only documented, unprivileged calls, but the fact that such
  modification is unusual is sufficient for some products. Running it alongside
  competitive games with kernel anti-cheat is not advised.
- **Throughput benchmarks will not show a doubling.** What improves is frame-time
  consistency and input latency under load, which is the operational meaning of
  "smooth", and which a throughput benchmark does not observe. The 99th
  percentile of frame time is the appropriate metric, not average frame rate.
- **A process that cannot be opened cannot be boosted.** Certain system and
  protected processes cannot be adjusted; they are skipped with a notice.
- **The Linux and macOS backends are beta quality.** They are functional
  (`setpriority`, `sched_setaffinity`, the `cpufreq` governor, `pmset
  lowpowermode`), but the Windows backend receives the greater share of work.

## Safety

- **No change is permanent.** Each modification is a documented Win32 call whose
  previous value is recorded beforehand.
- **The exclusion list is mandatory.** Critical processes (`csrss.exe`,
  `lsass.exe`, `winlogon.exe`, `services.exe`, `svchost.exe`, and others) are
  refused even if `targets = ["*"]` is configured.
- **Crashes are recoverable.** Each engine writes a rollback file as it boosts.
  `s-cpu on` reads all of them before applying anything, and removes anything a
  previous session left behind, so that boosts cannot accumulate between
  sessions.
- **Uninstallation restores first.** The installer executes `s-cpu off` before
  removing the binary that would otherwise be required to do so.
- **Administrator rights are not required.** They are used only for switching to
  the High performance power plan and for the machine-wide power throttling
  switch.

## Building

Python 3.9 or newer is required. The native and Java engines are optional; the
command-line interface runs without them and falls back automatically.

```bat
:: 1. the command-line interface, as a single portable executable
pip install -e ".[dev]"
pyinstaller --noconfirm SpeedyCPU.spec
:: -> dist\SpeedyCPU.exe

:: 2a. the C++ engine, using MSVC
cd native && build.bat && cd ..
:: 2b. ...or using any clang/g++ (build.sh falls back to zig cc)
cd native && ./build.sh && cd ..
:: -> native\dist\scpu-watch.exe, native\dist\scpu-timer.exe

:: 3. the Java engine (requires JDK 22 or newer)
cd java && build.bat && cd ..
:: -> java\dist\scpu-agent.jar

:: 4. assemble the portable bundle and the installer
./scripts/package_release.ps1 -Version 0.1.0 -Zip
```

Step 4 lays the bundle out in `../release/`, that is to say the `release`
directory adjacent to this source tree. `installer/speedycpu.iss` then produces
the installer from that directory:

```bat
iscc installer\speedycpu.iss /DAppVersion=0.1.0
```

### Tests

```bat
python -m pytest -q     # 182 tests; no administrator rights required
ruff check src tests
```

The suite executes against an isolated `SPEEDYCPU_HOME` and in `--dry-run` mode,
so it never modifies the host it runs on. This is also why the suite executes on
Linux in continuous integration.

## Platform support

| Platform | Status |
|---|---|
| Windows 10 / 11 (x64, ARM64) | Supported |
| Windows Server 2019 and later | Expected to work; unverified |
| Linux | Beta: `setpriority`, `sched_setaffinity`, `cpufreq` governor |
| macOS | Beta: `setpriority`, `pmset lowpowermode 0` |

On an unsupported platform the command-line interface imports cleanly and reports
the condition rather than failing.

## Frequently asked questions

**Does this consume additional CPU?**
Two system calls every 200 ms, and a process scan every three seconds. The
native engine idles at approximately 2 MB and 0% CPU. `turbo_threads` and
`--aggressive` are the only settings that alter this, and both are disabled by
default.

**Is the "more threads" claim accurate?**
Partly, and the distinction is stated here. SpeedyCPU raises the priority of
*every thread in the target process*, which is genuine and accounts for most of
the benefit. It cannot cause a program to create threads it does not create. The
`turbo_threads` option is a separate, opt-in mechanism that consumes battery in
order to keep the processor from parking.

**Will the effect be observable?**
On a host carrying heavy background load, generally yes: reduced stutter when
switching into an application, more responsive window movement, improved 1% lows
in games. On an idle, fast desktop, probably not, because there is nothing to
compete with.

**How is SpeedyCPU removed?**
`s-cpu off`. The directory and `%LOCALAPPDATA%\SpeedyCPU` may then be deleted.

**Why does `s-cpu off` pause briefly?**
It waits for the engine to terminate cleanly before reverting the residual
values, so that nothing is left half-applied.

## Contributing

Issues and pull requests are welcome. `ruff check src tests` and
`python -m pytest -q` must pass. Continuous integration runs both on Windows and
Linux, together with a C++ build under MSVC and a Java build on JDK 22 and 25.

## License

SpeedyCPU is free software distributed under the
[GNU Lesser General Public License v2.0 or later](LICENSE). The full GPL-2.0 text
on which the LGPL is based is provided in [COPYING](COPYING).

Copyright (C) 2026 SpeedyCPU contributors.
