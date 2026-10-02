# Changelog

All notable changes to SpeedyCPU are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

**Terminal output is plain ASCII and English by default**

- Every log severity is now a bracketed tag (`[ OK ]`, `[INFO]`, `[STEP]`,
  `[WARN]`, `[FAIL]`, `[TRACE]`) instead of pictographs. The previous output
  used `U+2713` / `U+2717` / `U+2192` / `U+2022`, which rendered differently in
  `cmd.exe`, PowerShell, CI logs and redirected files. The banner rule is
  ASCII `-` for the same reason.
- Engine availability lines read `native  available    C++ engine + scpu-timer`
  instead of a leading check mark.
- **English is now the default language and the operating system locale is no
  longer auto-detected.** A Chinese Windows build previously printed Chinese by
  default. Simplified Chinese is an explicit opt-in, still resolved from
  `--lang zh`, then `SPEEDYCPU_LANG=zh`, then `"lang": "zh"` in `config.json`.

### Planned

- A `--watch` mode that prints a live frame-time / priority table instead of
  exiting, so the effect can be observed without a second terminal.
- Per-profile configurations (`s-cpu on --profile gaming`) so a user can keep a
  "foreground only, no power changes" profile for a laptop and a "everything,
  aggressive" profile for a desktop.
- ARM64 smoke tests in CI. The code paths are architecture independent, but
  nothing currently proves it.

## [0.1.0] - 2026-10-02

First release. Everything below is new.

### Added

**Core**

- `s-cpu on` / `s-cpu off` with a complete, verified rollback: every value
  SpeedyCPU changes is read before it is written, and restored by `s-cpu off`,
  on engine exit, and on uninstall.
- Per-thread raise, not just `SetPriorityClass`: every thread of the target
  process is walked with `Thread32First`/`Thread32Next` and raised individually.
  This is the difference between "ranked higher" and "actually wins".
- Focus-driven boosting: only the process owning `GetForegroundWindow()` is
  boosted, and switching away releases it. Without this, raising every process
  leaves their relative shares unchanged.
- Machine-wide tunables: timer resolution (`timeBeginPeriod`, 15.625 ms → 1 ms),
  power plan switching, and processor power settings (minimum processor state,
  performance boost mode, core parking) that work **without** administrator
  rights.
- EcoQoS / power throttling cleared per process, and optionally machine-wide.
- Residual CPU affinity removed for launchers that pin a process to one core.

**Three interchangeable engines**

- `native` - a C++ engine (`scpu-watch.exe`, ~2 MB) that reacts to
  `EVENT_SYSTEM_FOREGROUND` via `SetWinEventHook`, plus a standalone timer helper
  (`scpu-timer.exe --probe`).
- `java` - `scpu-agent.jar`, calling Win32 through the Foreign Function & Memory
  API (JDK 22+). No JNI, no C++ compiler to run it.
- `python` - built in, pure stdlib, every Win32 call hand written with `ctypes`.
  No `psutil`, no `pywin32`, zero runtime dependencies.
- `auto` selection with automatic degradation, and `--engine` to force a choice.

**Safety**

- Cross-engine rollback files (`state.json`, `agent.json`, `native-state.tsv`)
  written *while* boosting, so a hard kill still leaves enough to clean up.
- Self-healing on start: `s-cpu on` reverts leftovers from a previous session
  before applying anything new, so boosts cannot accumulate across crashes.
- Thread-ID reuse guarded on revert: only threads still owned by the recorded PID
  are restored.
- A `NEVER_TOUCH` list that cannot be overridden, plus a 47-entry default
  exclusion list, both checked before any Win32 call is made.
- `s-cpu unboost` keeps the rollback record when a revert fails, so an elevated
  retry can finish the job.

**CLI**

- `on`, `off`, `status`, `boost`, `unboost`, `targets`, `doctor`, `config`,
  `version`, plus a hidden `_worker` entry point for the detached process.
- `status --json` and `doctor --json` for scripting; `--dry-run` on every mutating
  command, which prints what would happen and changes nothing.
- Global flags accepted on either side of the subcommand, so `s-cpu on --dry-run`
  works as typed.
- Bilingual output (English / Simplified Chinese) with automatic detection,
  including from the spelled-out Windows locale name.
- Exit codes: `0` success, `1` runtime failure, `2` bad usage.

**Packaging**

- PyInstaller one-file build with a Windows version resource (`SpeedyCPU.exe`).
- `scripts/package_release.ps1` (and a `.cmd` wrapper for machines where
  `ExecutionPolicy` is `Restricted`) assembling the portable bundle, plus
  `installer/speedycpu.iss` for the installer, which runs `s-cpu off` before
  uninstalling.
- CI on Windows and Linux across Python 3.9/3.12/3.13, an MSVC build of the
  native engine with runtime smoke tests, and a Java build on JDK 22 and 25.
- Tagged releases publish the portable zip and the installer to GitHub Releases.

### Fixed during development

Recorded because each one was a real defect found by testing, not a hypothetical:

- `EnumProcesses` was passed a `byref` pointer, which `ctypes` rejects; the array
  must be passed directly.
- `powercfg` output is localised, and decoding it with
  `locale.getpreferredencoding()` under Python's UTF-8 mode produced mojibake -
  which meant the rollback restored a garbage value. Now decoded with the real
  console code page from `GetConsoleOutputCP`.
- `s-cpu unboost` dropped the rollback record even when the revert failed,
  stranding a process at elevated priority with nothing left to undo it.
- `t()` named its first parameter `key`, so `t("config.no_key", key=...)` raised
  `TypeError: got multiple values for argument 'key'` - turning a friendly error
  into a traceback.
- `Arena.ofGlobal()` does not exist; the FFM API call is `Arena.global()`.
- The Java agent's jar was built with `--main-class` instead of the manifest,
  silently dropping `Enable-Native-Access: ALL-UNNAMED` and breaking the FFM
  calls on JDK 25.
- CJK labels misaligned every table column because `f"{label:<20}"` pads by
  character count, not display width.
- `java_version()` started a whole JVM per call, and `s-cpu status` called it
  several times.

[Unreleased]: https://github.com/speedycpu/speedycpu/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/speedycpu/speedycpu/releases/tag/v0.1.0
