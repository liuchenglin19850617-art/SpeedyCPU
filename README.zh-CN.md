<div align="center">

# SpeedyCPU

**`s-cpu on` · `s-cpu off`**

命令行 CPU 流畅度加速器（Windows）。
一条命令开启，一条命令还原。

[![CI](https://github.com/speedycpu/speedycpu/actions/workflows/ci.yml/badge.svg)](https://github.com/speedycpu/speedycpu/actions/workflows/ci.yml)
[![License: LGPL-2.0-or-later](https://img.shields.io/badge/license-LGPL--2.0--or--later-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

[English](README.md) · [实现原理](docs/HOW-IT-WORKS.md) · [更新日志](CHANGELOG.md)

</div>

---

## 请先读这一段

SpeedyCPU **不能让 CPU 变快**。任何软件都做不到。没有可以解锁的寄存器，没有隐藏的核心，
也没有任何"优化"能让一颗 3.2 GHz 的芯片超出它自己的频率。

它**也不能让程序多创建线程**。单线程程序永远是单线程，旁边跑什么都不会改变这一点。

**SpeedyCPU 真正做的事，是在多个程序同时抢 CPU 时改变"谁赢"。** 这是一件真实、可测量、
也确实有用的事：你眼前的程序不再被现代 Windows 那两百个后台进程饿死，卡顿和输入延迟下降，
前台动画能稳住帧率。它只用公开文档化的 Win32 API，会明确告诉你改了哪些值，
`s-cpu off` 会把每一个值都放回去。

如果有项目向你承诺"CPU 变快"或"多线程加速"，那卖给你的要么是安慰剂，要么是个吃资源的软件。
这个项目会老实告诉你它到底做了什么。

## 它到底改了什么

| | 项目 | 为什么有用 |
|---|---|---|
| **优先级** | 把目标进程提升到 `High`，再遍历它的**每一个线程**逐个提权 | 调度器从此优先照顾这个进程，而不是后台噪音。逐线程提权是大多数工具省掉的一步，而对一个开了 100+ 线程的浏览器或游戏来说，恰恰是这步最关键。 |
| **EcoQoS** | 清除该进程的 [电源节流](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation) 标记 | Windows 会悄悄把"后台"任务塞到低功耗核上并压低频率。清掉这个标记就解除停靠。 |
| **前台焦点** | 只加速你**正在看**的那个窗口；切走时把上一个还原 | 把所有进程都提到 `High` 等于谁都没提，相对份额完全不变 —— 这也是为什么很多"加速器"测出来是零。让前台应用单独赢，才是有效的那一半。 |
| **CPU 亲和性** | 解除残留的单核亲和性掩码 | 启动器和某些 DRM 壳会把游戏钉在一个核上并且再也不解开。 |
| **计时器精度** | 在会话期间把系统计时器从 15.625 ms 提升到 1 ms | 游戏和音频用 `Sleep` 做节拍。15.625 ms 精度下你最短也只能节出 15.6 ms 一帧；1 ms 下就能节到 1 ms。 |
| **CPU 电源参数** | 调**当前**电源计划的最小处理器状态、性能提升模式、核心停放 | **不需要管理员权限**，这一点和"切到高性能计划"不同。 |
| **可选** | 锁定工作集（`--aggressive`）、全局关闭 EcoQoS（`--global-throttling`）、切换高性能计划 | 默认全部关闭，每一项都有代价，见[注意事项](#注意事项)。 |

以上每一项在修改前都会被记录，并在 `s-cpu off`、引擎退出、以及卸载时还原。
不写注册表、不动系统目录。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `source/` | 完整源码树：Python 命令行、C++ 引擎、Java agent、安装脚本、测试套件与文档 |
| `release/` | 已组装的发布物。每个版本一个子目录，例如 `release/SpeedyCPU-0.1.0-win64/` |

## 安装

### Windows 安装包

从 [Releases](https://github.com/speedycpu/speedycpu/releases) 下载
`SpeedyCPU-Setup-x.y.z.exe` 直接运行。它会把安装目录加进**当前用户**的 PATH
（不需要重启，不涉及全局），可以顺手勾选"立即启用"，卸载时会先执行 `s-cpu off`。

### 便携版 zip

下载 `SpeedyCPU-x.y.z-win64.zip`，解压到任意目录，运行目录里的 `s-cpu.cmd`；
或者把目录加入 PATH，这样在任何 CMD 里都能直接敲 `s-cpu`：

```bat
setx PATH "%PATH%;C:\path\to\SpeedyCPU-0.1.0-win64"
```

### pip

```bat
pip install speedycpu
```

这样得到的是 CLI 和内置的 Python 引擎，不含可选的 C++ / Java 引擎，详见[引擎](#引擎)。

### 从源码

```bat
git clone https://github.com/speedycpu/speedycpu
cd speedycpu
pip install -e ".[dev]"
python -m speedycpu on
```

要一并编译可选的引擎，见[构建](#构建)。

## 快速上手

```bat
s-cpu on          :: 开启。之后打开任何程序都会自动被加速
s-cpu status      :: 看当前状态，以及哪些进程正在被加速
s-cpu off         :: 关闭，停掉后台进程，完整还原
```

这就是全部核心功能。其余命令都是诊断和微调：

```bat
s-cpu doctor                     :: 环境自检，会告诉你不支持哪些功能
s-cpu targets add chrome.exe     :: 让这个程序常驻加速，不切焦点也加速
s-cpu targets add game*.exe
s-cpu targets list               :: 查看加速目标与 47 项排除列表
s-cpu boost notepad.exe          :: 立刻加速某个程序，不起后台进程
s-cpu unboost                    :: 还原加速
s-cpu config set mode all        :: 改成加速全部用户进程（默认只加速前台）
s-cpu config show                :: 列出所有配置项与当前值
s-cpu on --dry-run               :: 只打印"将要做什么"，不实际修改任何东西
```

`s-cpu on --help` 会列出所有单次覆盖参数（`--engine`、`--mode`、`--priority`、
`--no-cpu-tuning`、`--no-power-plan`、`--timer-ms`、`--aggressive`、
`--global-throttling`、`--no-daemon`）。全局参数（`--dry-run`、`--no-color`、
`-q`、`-v`、`--lang`）放在子命令前后都可以，`s-cpu on --dry-run` 和
`s-cpu --dry-run on` 都成立。

### 终端输出格式

输出为纯 ASCII，每种日志级别使用方括号标签，因此在 `cmd.exe`、PowerShell、
CI 日志和重定向文件里得到的内容完全一致：

```
[ OK ]   CPU 电源参数已还原为加速前的取值
[STEP]   writing state.json
[ WARN]  未提权，已跳过电源计划 / 全局节流调整
[FAIL]   could not revert notepad.exe (pid 4821)
```

输出中不含任何图形符号、对勾或制表线字符。

### 语言

**默认英文**，且不自动探测操作系统区域：中文版 Windows 不会擅自把一款文档、
Issue 与发布说明均为英文的工具切成中文。简体中文需显式开启，优先级如下：

| 来源 | 示例 |
|---|---|
| `--lang` 参数 | `s-cpu --lang zh status` |
| `SPEEDYCPU_LANG` 环境变量 | `set SPEEDYCPU_LANG=zh` |
| `config.json` | `s-cpu config set lang zh` |

### 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 出错（没匹配到进程、权限不足、引擎起不来） |
| `2` | 用法错误 |

`--dry-run` 只报告"本来会做什么"而不做任何修改，因此可以放心写进脚本。

## 引擎

SpeedyCPU 内置三个可互换的引擎。它们调用同一批 Win32 API，写同一种回滚记录，
区别只在于内存占用和"发现你切换了窗口"的速度。

| 引擎 | 内存占用 | 感知焦点的方式 | 何时会被选中 |
|---|---|---|---|
| **native**（C++） | ~2 MB | `SetWinEventHook`，事件驱动，即时 | 编译存在时默认 |
| **java**（JAR） | ~60 MB | `SetWinEventHook`，事件驱动，即时 | 没有 C++ 产物但有 JDK 22+ 时 |
| **python**（内置） | ~15 MB | 每 200 ms 轮询一次 | 永远可用，兜底 |

`auto`（默认）按上表顺序取第一个可用的。`s-cpu doctor` 会告诉你这台机器有哪几个。
用 `s-cpu on --engine native` 可以强制指定。

Python 引擎就是"`pip install speedycpu` 不需要任何编译器"的原因：它纯标准库，
所有 Win32 调用都用 `ctypes` 手写绑定 —— 不依赖 `psutil`，不依赖 `pywin32`，
运行时零依赖。

想用 Java 引擎，把 JDK 22 或更高版本放进 PATH 即可。该引擎通过
Foreign Function & Memory API 调用 Win32，所以**不需要 JNI，也不需要编译器**。
（也正因为用了这个 API，JDK 21 及更低版本会被直接判定为不可用，而不是勉强一试。）

## 配置

所有文件都在 `%LOCALAPPDATA%\SpeedyCPU\`：

| 文件 | 用途 |
|---|---|
| `config.json` | 你的偏好设置 |
| `state.json` | 当前已应用的内容，用于还原 |
| `native-state.tsv` / `agent.json` | C++ / Java 引擎写的回滚记录 |
| `worker.log` / `native.log` / `agent.log` | 各引擎日志 |

可以用 `s-cpu config set` 修改，也可以直接编辑。影响行为最大的几项：

| 键 | 默认值 | 说明 |
|---|---|---|
| `engine` | `auto` | `auto` \| `native` \| `java` \| `python` |
| `mode` | `foreground` | `foreground`（只加速前台窗口）\| `all`（全部用户进程） |
| `targets` | `[]` | 常驻加速目标，例如 `["chrome.exe", "game*.exe"]` |
| `exclude` | 47 项 | 永不触碰。用 `s-cpu targets list` 查看 |
| `priority` | `high` | `high` \| `above_normal` |
| `cpu_tuning` | `true` | 调处理器电源参数（不需要管理员） |
| `power_plan` | `high_performance` | `high_performance` \| `ultimate` \| `balanced` \| `<GUID>` \| `none` |
| `timer_resolution` | `1` | 请求的毫秒精度，`0` 表示不请求 |
| `aggressive` | `false` | 同时锁定被加速进程的工作集 |
| `global_power_throttling_off` | `false` | 全局关闭 EcoQoS（需要管理员） |
| `poll_interval` | `3.0` | 全量扫描进程的间隔（秒） |
| `turbo_threads` | `0` | 仅 Java 引擎，见下 |

`mode = all` 是支持但不推荐的：把所有进程都提到 `High`，它们之间的相对份额一点都没变。
默认用 `foreground`，是因为只有它真正改变了结果。

`turbo_threads` 是"多调用线程数"这个说法的诚实实现：Java 引擎会起 *N* 条高优先级线程，
做短促的算术突发，阻止 CPU 掉进深度空闲状态，从而可测量地降低**下一批**任务的响应延迟。
代价是它平时白烧一个核的功耗和电量，所以默认关闭，需要你主动打开。

## 注意事项

SpeedyCPU 是调度工具，不是魔法。以下是它做不到的事：

- **它不能提高频率。** 已经撞到温度墙或功耗墙的 CPU 还在那里。笔记本因为过热降频的话，
  该清灰还是得清灰。
- **它不能凭空增加线程。** 单线程程序依然单线程。
- **`--aggressive` 是拿内存换流畅。** 它会锁定被加速进程的工作集，让内存管理器在你切走后
  无法把这些页裁掉。这确实有效，但也意味着这些页谁都别想用。内存紧张的机器别开。
- **`turbo_threads` 费电。** 见上。
- **反作弊可能不欢迎它。** 内核级反作弊会把"调整其他进程优先级或亲和性"的行为视作可疑。
  SpeedyCPU 只调用有文档的、无需提权的 API，但对某些反作弊来说"少见"本身就足够可疑了。
  不要在有内核反作弊的竞技游戏里同时运行它。
- **跑分不会显示 2 倍提升。** 真正改善的是负载下的帧时间一致性和输入延迟 —— 这才是"流畅"
  的实际含义，而吞吐型跑分看不到这一点。请比较帧时间的 99 分位，不要看平均 FPS。
- **打不开的进程就加速不了。** 部分系统与受保护进程完全无法调整，会被跳过并给出提示。
- **Linux / macOS 后端是 beta。** 它们存在并且做了合理的事（`setpriority`、
  `sched_setaffinity`、`cpufreq` governor、`pmset lowpowermode`），
  但真正被持续打磨的是 Windows 后端。

## 安全性

- **没有永久性改动。** 每一次修改都是有文档的 Win32 调用，且修改前先记录旧值。
- **排除列表不可绕过。** 即使你写 `targets = ["*"]`，关键进程（`csrss.exe`、
  `lsass.exe`、`winlogon.exe`、`services.exe`、`svchost.exe` 等）依然会被拒绝。
- **崩溃了也能恢复。** 每个引擎在加速的同时会写回滚文件；`s-cpu on` 启动时会先读取
  全部回滚文件并清理上次会话的残留，因此加速不会跨会话累积。
- **卸载前先还原。** 安装包会在删除"知道怎么还原的那个程序"之前先执行 `s-cpu off`。
- **不需要管理员权限**，只有切换高性能计划与全局关闭 EcoQoS 才会用到。

## 构建

需要 Python 3.9+。C++ 与 Java 引擎都是可选的：没有它们 CLI 照样工作，会自动降级。

```bat
:: 1. CLI，打包成单个便携 exe
pip install -e ".[dev]"
pyinstaller --noconfirm SpeedyCPU.spec
:: -> dist\SpeedyCPU.exe

:: 2a. C++ 引擎，用 MSVC
cd native && build.bat && cd ..
:: 2b. ……或用任意 clang/g++（build.sh 会自动回退到 zig cc）
cd native && ./build.sh && cd ..
:: -> native\dist\scpu-watch.exe, native\dist\scpu-timer.exe

:: 3. Java 引擎（需要 JDK 22+）
cd java && build.bat && cd ..
:: -> java\dist\scpu-agent.jar

:: 4. 组装便携包与安装包
./scripts/package_release.ps1 -Version 0.1.0 -Zip
```

第 4 步会把成品铺到 `../release/`，也就是与本源码目录并列的那个 `release` 文件夹。
然后由 `installer/speedycpu.iss` 基于它生成安装包：

```bat
iscc installer\speedycpu.iss /DAppVersion=0.1.0
```

### 测试

```bat
python -m pytest -q     :: 179 个用例，不需要管理员权限
ruff check src tests
```

测试全程使用隔离的 `SPEEDYCPU_HOME` 与 `--dry-run`，因此**不会**改动运行它的机器 ——
这也是它能同时在 CI 的 Linux 上跑通的原因。

## 平台支持

| 平台 | 状态 |
|---|---|
| Windows 10 / 11（x64、ARM64） | 正式支持 |
| Windows Server 2019+ | 应该可用，未测试 |
| Linux | Beta：`setpriority`、`sched_setaffinity`、`cpufreq` governor |
| macOS | Beta：`setpriority`、`pmset lowpowermode 0` |

在不支持的平台上，CLI 会正常导入并给出说明，而不是抛异常。

## 常见问题

**它自己占 CPU 吗？**
每 200 ms 两次系统调用，每 3 秒一次进程扫描。native 引擎空闲时约 2 MB 内存、0% CPU。
真正会改变这个数字的只有 `turbo_threads` 和 `--aggressive`，两者默认都关。

**所谓"多调用线程数"是真的吗？**
一部分是，而且我们说清楚是哪一部分。SpeedyCPU 会把目标进程**内部每一个线程**都提权 ——
这是真实有效的，也是大部分收益的来源。但它无法让程序创建它本来不会创建的线程；
`turbo_threads` 是另一件事：一个默认关闭、需要主动开启、要付电池代价的技巧，
用来阻止 CPU 时钟停靠。

**我能感觉到吗？**
后台负载重的机器上通常能 —— 切进应用时的卡顿变少、拖窗口更跟手、游戏 1% low 更好。
而在一台空闲的高性能台式机上大概感觉不到，因为本来就没有东西跟它抢。

**怎么彻底删掉？**
`s-cpu off`，然后删掉程序目录和 `%LOCALAPPDATA%\SpeedyCPU`。

**为什么 `s-cpu off` 要等一小会儿？**
它会先等引擎干净退出，再还原残留项，这样不会留下"只还原了一半"的状态。

## 参与贡献

欢迎提 Issue 和 PR。提交前请确保 `ruff check src tests` 与 `python -m pytest -q` 都通过；
CI 会在 Windows 与 Linux 上跑这两项，另外还有 MSVC 的 C++ 构建和 JDK 22 / 25 的 Java 构建。

## 许可证

SpeedyCPU 是自由软件，采用
[GNU 宽通用公共许可证 v2.0 或更新版本](LICENSE)。LGPL 所基于的 GPL-2.0 全文见
[COPYING](COPYING)。

版权所有 (C) 2026 SpeedyCPU contributors。
