# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU Library General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Library General Public License for more
# details.  You should have received a copy of it in the LICENSE file.

"""Tiny zero-dependency i18n layer.

SpeedyCPU ships English and Simplified Chinese message catalogs. English is the
default; Chinese is an explicit opt-in. The active language is resolved in this
order:

1. the ``--lang`` command line flag (via :func:`set_language`)
2. the ``SPEEDYCPU_LANG`` environment variable
3. the ``lang`` key in ``config.json``
4. English

The operating system locale is deliberately *not* consulted. Automatic
detection made a Chinese Windows build print Chinese by default, which is the
wrong behaviour for a tool whose documentation, issue tracker and releases are
English. Set ``SPEEDYCPU_LANG=zh``, pass ``--lang zh``, or write
``"lang": "zh"`` into config.json to switch to Chinese.

Unknown keys fall back to English, then to the key itself, so a missing
translation can never crash the CLI.
"""

from __future__ import annotations

import contextlib
import locale
import os

__all__ = ["available_languages", "get_language", "set_language", "t"]

_DEFAULT = "en"
_SUPPORTED = ("en", "zh")

_LANG = _DEFAULT

CATALOG: dict[str, dict[str, str]] = {
    "en": {
        # ---- generic ----------------------------------------------------
        "app.name": "SpeedyCPU",
        "app.tagline": "command line CPU responsiveness booster",
        "common.yes": "yes",
        "common.no": "no",
        "common.none": "none",
        "common.enabled": "enabled",
        "common.disabled": "disabled",
        "common.on": "ON",
        "common.off": "OFF",
        "common.unknown": "unknown",
        "common.seconds": "{value:.1f}s",
        # ---- scopes -----------------------------------------------------
        "scope.foreground": "focused window",
        "scope.all": "all user processes",
        # ---- engines ----------------------------------------------------
        "engine.native.ready": "C++ engine{extras}",
        "engine.native.missing": "not built; build with native/build.bat",
        "engine.java.ready": "JDK {version}",
        "engine.java.old_jdk": "needs JDK {min}+ (found {version})",
        "engine.java.no_java": "java not found on PATH",
        "engine.java.no_jar": "scpu-agent.jar not found",
        "engine.python.ready": "built in, always available",
        "engine.fallback": "requested '{requested}' unavailable ({reason}); fell back to python",
        "engine.in_process": "in-process",
        # ---- errors -----------------------------------------------------
        "err.unsupported_platform": "SpeedyCPU has no backend for platform '{platform}'. Supported: Windows, Linux, macOS.",
        "err.not_admin": "This operation requires administrator/root privileges.",
        "err.not_found": "Not found: {what}",
        "err.permission": "Access denied while touching {what} (PID {pid}). Try running as administrator.",
        "err.value": "Invalid value for {what}: {value}",
        "err.no_daemon": "The SpeedyCPU background worker is not running.",
        "err.hint_admin": "hint: right-click Windows Terminal / CMD and choose 'Run as administrator'.",
        # ---- s-cpu on ---------------------------------------------------
        "on.title": "Enabling SpeedyCPU",
        "on.already": "SpeedyCPU is already ON (worker PID {pid}). Configuration refreshed.",
        "on.power_plan": "Power plan: {old} -> {new}",
        "on.power_plan.skipped": "Power plan: left untouched (--no-power-plan)",
        "on.power_plan.unavailable": "Power plan: '{spec}' is not available on this machine, left as is",
        "on.cpu_tuning": "CPU tuning: minimum processor state {minimum}%, boost mode = aggressive, core parking off",
        "on.cpu_tuning.skipped": "CPU tuning: skipped",
        "on.timer": "Timer resolution: {old} ms -> {new} ms",
        "on.throttling": "Global power throttling (EcoQoS) disabled",
        "on.throttling.skipped": "Global power throttling: left untouched",
        "on.worker": "Background worker started (PID {pid})",
        "on.worker.inline": "Running in the foreground; press Ctrl+C to stop",
        "on.engine": "Engine: {engine} ({note})",
        "on.engine.fallback": "Engine: {engine} - requested engine was unavailable, fell back",
        "on.recovered": "Cleaned up {count} leftover boost(s) from a previous session",
        "on.mode": "Target mode: {mode}",
        "on.done": "SpeedyCPU is ON. Launch any program and it will be boosted automatically.",
        "on.done_off_hint": "Turn it off with:  s-cpu off",
        "on.already_on_note": "System-level settings were already applied.",
        # ---- s-cpu off --------------------------------------------------
        "off.title": "Disabling SpeedyCPU",
        "off.not_running": "SpeedyCPU is not running, nothing to undo.",
        "off.worker_stopped": "Background worker stopped (PID {pid})",
        "off.engine_stopped": "Engine stopped: {engine} (PID {pid})",
        "off.worker_forced": "Background worker did not exit in time, terminated.",
        "off.reverted": "Reverted {count} boosted process(es)",
        "off.power_restored": "Power plan restored: {plan}",
        "off.cpu_tuning_restored": "CPU power settings restored to their previous values",
        "off.timer_released": "Timer resolution released",
        "off.throttling_restored": "Global power throttling setting restored",
        "off.done": "SpeedyCPU is OFF. Everything has been put back the way it was.",
        # ---- s-cpu status ----------------------------------------------
        "status.title": "SpeedyCPU status",
        "status.state": "State",
        "status.engine": "Engine",
        "status.engine_none": "no session",
        "status.uptime": "Uptime",
        "status.worker": "Background worker",
        "status.worker_pid": "pid {pid}",
        "status.worker_dead": "not running (stale pid {pid})",
        "status.mode": "Target mode",
        "status.targets": "Targets",
        "status.boosted": "Boosted processes",
        "status.power_plan": "Active power plan",
        "status.timer": "System timer resolution",
        "status.timer_held": "{value} ms (requested by SpeedyCPU)",
        "status.timer_default": "{value} ms",
        "status.admin": "Running elevated",
        "status.config": "Config file",
        "status.no_boosted": "no boosted processes yet",
        "status.boosted_line": "{name} (pid {pid}) - {count} threads @ {priority}",
        # ---- s-cpu boost / unboost --------------------------------------
        "boost.title": "Boosting {target}",
        "boost.done": "Boosted {count} process(es), {threads} thread(s) raised to {priority}.",
        "boost.none": "No running process matched '{target}'.",
        "boost.single": "{name} (pid {pid}): priority {old} -> {new}, {threads} thread(s) raised, EcoQoS off, affinity {affinity} core(s).",
        "boost.already": "{name} (pid {pid}) is already boosted.",
        "unboost.done": "Reverted {count} process(es).",
        "unboost.none": "No boosted process matched '{target}'.",
        "unboost.failed": "Could not revert {what} - it stays on the list, retry from an elevated prompt.",
        # ---- s-cpu targets ---------------------------------------------
        "targets.title": "Boost targets",
        "targets.all": "boosting every user process except the exclusion list",
        "targets.empty": "No always-on targets: only the focused window gets boosted. Add more with 'targets add'.",
        "targets.added": "Added target pattern: {pattern}",
        "targets.exists": "Pattern already present: {pattern}",
        "targets.removed": "Removed target pattern: {pattern}",
        "targets.missing": "Pattern not in the list: {pattern}",
        "targets.excluded": "Excluded patterns ({count})",
        # ---- s-cpu doctor ----------------------------------------------
        "doctor.title": "SpeedyCPU environment check",
        "doctor.engines": "Boost engines",
        "doctor.engine_line": "{status} {name} - {note}",
        "doctor.os": "Operating system",
        "doctor.python": "Python",
        "doctor.admin": "Elevation",
        "doctor.admin.yes": "elevated - full feature set available",
        "doctor.admin.no": "not elevated - power plan and global EcoQoS changes will be skipped",
        "doctor.cpu": "Logical processors",
        "doctor.backend": "Backend",
        "doctor.power_plans": "Available power plans",
        "doctor.ultimate": "Ultimate Performance plan",
        "doctor.ultimate.yes": "present",
        "doctor.ultimate.no": "not created yet (created on demand by power_plan = 'ultimate')",
        "doctor.fail": "{count} check(s) reported a problem.",
        "doctor.ok": "All checks passed. SpeedyCPU can use the full feature set.",
        # ---- s-cpu config ----------------------------------------------
        "config.path": "Config file: {path}",
        "config.state_path": "State file:  {path}",
        "config.written": "Config saved.",
        "config.reset": "Config reset to defaults.",
        "config.key_set": "config.{key} = {value}",
        "config.no_key": "Unknown config key: {key}",
        "config.invalid_value": "Cannot parse '{value}' as the type of config key '{key}' ({type}).",
        # ---- warnings ---------------------------------------------------
        "warn.not_admin": "Running without elevation: {what} was skipped. {hint}",
        "warn.dry_run": "[dry-run] would perform: {what}",
        "warn.skip_protected": "Skipping protected system process {name} (pid {pid})",
        "warn.no_targets": "No target patterns configured; add one with: s-cpu targets add <name.exe>",
    },
    "zh": {
        # ---- generic ----------------------------------------------------
        "app.name": "SpeedyCPU",
        "app.tagline": "命令行 CPU 流畅度加速器",
        "common.yes": "是",
        "common.no": "否",
        "common.none": "无",
        "common.enabled": "已启用",
        "common.disabled": "已禁用",
        "common.on": "已开启",
        "common.off": "已关闭",
        "common.unknown": "未知",
        "common.seconds": "{value:.1f} 秒",
        # ---- scopes -----------------------------------------------------
        "scope.foreground": "当前前台窗口",
        "scope.all": "全部用户进程",
        # ---- engines ----------------------------------------------------
        "engine.native.ready": "C++ 引擎{extras}",
        "engine.native.missing": "未编译；请用 native/build.bat 构建",
        "engine.java.ready": "JDK {version}",
        "engine.java.old_jdk": "需要 JDK {min}+（当前 {version}）",
        "engine.java.no_java": "PATH 中找不到 java",
        "engine.java.no_jar": "找不到 scpu-agent.jar",
        "engine.python.ready": "内置，始终可用",
        "engine.fallback": "指定的 '{requested}' 不可用（{reason}），已降级为 python",
        "engine.in_process": "进程内运行",
        # ---- errors -----------------------------------------------------
        "err.unsupported_platform": "SpeedyCPU 暂不支持平台 '{platform}'。目前支持: Windows、Linux、macOS。",
        "err.not_admin": "该操作需要管理员 / root 权限。",
        "err.not_found": "未找到: {what}",
        "err.permission": "无权访问 {what}（PID {pid}）。请尝试以管理员身份运行。",
        "err.value": "{what} 的取值非法: {value}",
        "err.no_daemon": "SpeedyCPU 后台进程没有在运行。",
        "err.hint_admin": "提示: 右键点击终端 / CMD，选择“以管理员身份运行”。",
        # ---- s-cpu on ---------------------------------------------------
        "on.title": "正在开启 SpeedyCPU",
        "on.already": "SpeedyCPU 已经处于开启状态（后台进程 PID {pid}），已刷新配置。",
        "on.power_plan": "电源计划: {old} -> {new}",
        "on.power_plan.skipped": "电源计划: 未改动",
        "on.power_plan.unavailable": "电源计划: 本机不存在 '{spec}'，保持原样",
        "on.cpu_tuning": "CPU 电源参数: 最小处理器状态 {minimum}%、性能提升模式=激进、关闭核心停放",
        "on.cpu_tuning.skipped": "CPU 电源参数: 已跳过",
        "on.timer": "系统计时器精度: {old} ms -> {new} ms",
        "on.throttling": "已关闭全局电源节流（EcoQoS）",
        "on.throttling.skipped": "全局电源节流: 未改动",
        "on.worker": "后台进程已启动（PID {pid}）",
        "on.worker.inline": "正在前台运行，按 Ctrl+C 停止",
        "on.engine": "执行引擎: {engine}（{note}）",
        "on.engine.fallback": "执行引擎: {engine} —— 指定的引擎不可用，已自动降级",
        "on.recovered": "已清理上次会话遗留的 {count} 个加速进程",
        "on.mode": "目标模式: {mode}",
        "on.done": "SpeedyCPU 已开启。之后打开任何程序都会自动获得加速。",
        "on.done_off_hint": "关闭命令:  s-cpu off",
        "on.already_on_note": "系统级设置此前已应用，本次只刷新了监控配置。",
        # ---- s-cpu off --------------------------------------------------
        "off.title": "正在关闭 SpeedyCPU",
        "off.not_running": "SpeedyCPU 当前未运行，无需还原。",
        "off.worker_stopped": "后台进程已停止（PID {pid}）",
        "off.engine_stopped": "引擎已停止: {engine}（PID {pid}）",
        "off.worker_forced": "后台进程未及时退出，已强制结束。",
        "off.reverted": "已还原 {count} 个被加速的进程",
        "off.power_restored": "电源计划已还原为: {plan}",
        "off.cpu_tuning_restored": "CPU 电源参数已还原为加速前的取值",
        "off.timer_released": "系统计时器精度已释放",
        "off.throttling_restored": "全局电源节流设置已还原",
        "off.done": "SpeedyCPU 已关闭，所有改动均已还原。",
        # ---- s-cpu status ----------------------------------------------
        "status.title": "SpeedyCPU 状态",
        "status.state": "状态",
        "status.engine": "执行引擎",
        "status.engine_none": "无会话",
        "status.uptime": "已运行",
        "status.worker": "后台进程",
        "status.worker_pid": "PID {pid}",
        "status.worker_dead": "未运行（残留 PID {pid}）",
        "status.mode": "目标模式",
        "status.targets": "加速目标",
        "status.boosted": "已加速进程",
        "status.power_plan": "当前电源计划",
        "status.timer": "系统计时器精度",
        "status.timer_held": "{value} ms（SpeedyCPU 已提升）",
        "status.timer_default": "{value} ms",
        "status.admin": "管理员权限",
        "status.config": "配置文件",
        "status.no_boosted": "暂未加速任何进程",
        "status.boosted_line": "{name}（PID {pid}） - {count} 个线程 @ {priority}",
        # ---- s-cpu boost / unboost --------------------------------------
        "boost.title": "正在加速 {target}",
        "boost.done": "已加速 {count} 个进程，{threads} 个线程提升到 {priority}。",
        "boost.none": "没有找到正在运行的进程匹配 '{target}'。",
        "boost.single": "{name}（PID {pid}）: 优先级 {old} -> {new}，已提升 {threads} 个线程，已关闭 EcoQoS，可用核心 {affinity} 个。",
        "boost.already": "{name}（PID {pid}）已经被加速过了。",
        "unboost.done": "已还原 {count} 个进程。",
        "unboost.none": "没有已加速的进程匹配 '{target}'。",
        "unboost.failed": "无法还原 {what} —— 记录已保留，请以管理员身份重试。",
        # ---- s-cpu targets ---------------------------------------------
        "targets.title": "加速目标列表",
        "targets.all": "正在加速除排除列表以外的全部用户进程",
        "targets.empty": "没有常驻加速目标：只加速当前前台窗口。可用 'targets add' 追加。",
        "targets.added": "已添加目标: {pattern}",
        "targets.exists": "该目标已存在: {pattern}",
        "targets.removed": "已移除目标: {pattern}",
        "targets.missing": "列表中不存在: {pattern}",
        "targets.excluded": "排除列表（{count} 项）",
        # ---- s-cpu doctor ----------------------------------------------
        "doctor.title": "SpeedyCPU 环境自检",
        "doctor.engines": "可用的加速引擎",
        "doctor.engine_line": "{status} {name} —— {note}",
        "doctor.os": "操作系统",
        "doctor.python": "Python",
        "doctor.admin": "提权状态",
        "doctor.admin.yes": "已提权 —— 全部功能可用",
        "doctor.admin.no": "未提权 —— 将跳过电源计划与全局 EcoQoS 调整",
        "doctor.cpu": "逻辑处理器",
        "doctor.backend": "后端实现",
        "doctor.power_plans": "可用电源计划",
        "doctor.ultimate": "卓越性能计划",
        "doctor.ultimate.yes": "已存在",
        "doctor.ultimate.no": "尚未创建（将 power_plan 设为 'ultimate' 时按需创建）",
        "doctor.fail": "有 {count} 项检查发现问题。",
        "doctor.ok": "全部检查通过，SpeedyCPU 可以使用完整功能。",
        # ---- s-cpu config ----------------------------------------------
        "config.path": "配置文件: {path}",
        "config.state_path": "状态文件: {path}",
        "config.written": "配置已保存。",
        "config.reset": "配置已恢复默认值。",
        "config.key_set": "config.{key} = {value}",
        "config.no_key": "未知的配置项: {key}",
        "config.invalid_value": "无法把 '{value}' 解析为配置项 '{key}' 的类型（{type}）。",
        # ---- warnings ---------------------------------------------------
        "warn.not_admin": "当前未提权: 已跳过 {what}。{hint}",
        "warn.dry_run": "[试运行] 将执行: {what}",
        "warn.skip_protected": "跳过受保护的系统进程 {name}（PID {pid}）",
        "warn.no_targets": "没有配置任何加速目标；用下面命令添加: s-cpu targets add <name.exe>",
    },
}


def _normalise(value: str | None) -> str | None:
    """Map a locale-ish string onto a supported language, or None."""
    if not value:
        return None
    value = value.strip().lower().replace("_", "-")
    # Windows spells locales out: 'Chinese (Simplified)_China'. Accepting the
    # English name as well as the POSIX 'zh-CN' form is what makes autodetection
    # work on a Chinese Windows install.
    if value.startswith("zh") or value.startswith("chinese") or value.startswith("cn"):
        return "zh"
    if value.startswith("en") or value.startswith("english"):
        return "en"
    return None


def set_language(value: str | None) -> None:
    """Force the active language. Unknown values are ignored."""
    global _LANG
    lang = _normalise(value)
    if lang:
        _LANG = lang


def get_language() -> str:
    return _LANG


def available_languages() -> tuple[str, ...]:
    return _SUPPORTED


def detect_language(config_lang: str | None = None, cli_lang: str | None = None) -> str:
    """Resolve the language to use, without mutating global state.

    English wins when nothing was requested explicitly. See the module
    docstring for why the OS locale plays no part in this decision.
    """
    for candidate in (cli_lang, os.environ.get("SPEEDYCPU_LANG"), config_lang):
        lang = _normalise(candidate)
        if lang:
            return lang
    return _DEFAULT


def _system_language() -> str | None:
    """Best-effort guess at the OS locale, then the POSIX env vars.

    Retained for diagnostics: it is not part of language resolution any more.
    """
    candidates: list[str | None] = []
    # On Windows this yields e.g. 'Chinese (Simplified)_China'; on POSIX
    # 'zh_CN' or 'en_US'. ``getdefaultlocale`` is deprecated (and gone in
    # Python 3.15), so only the supported call is used here.
    with contextlib.suppress(ValueError, TypeError):  # pragma: no cover - exotic locales
        candidates.extend(locale.getlocale())
    candidates.extend(os.environ.get(var) for var in ("LC_ALL", "LC_MESSAGES", "LANG"))
    for candidate in candidates:
        lang = _normalise(candidate)
        if lang:
            return lang
    return None


def t(message_id: str, **kwargs: object) -> str:
    """Translate ``message_id`` and interpolate ``kwargs``. Never raises.

    The first parameter is deliberately *not* called ``key``: several catalogs
    have a ``{key}`` placeholder (``config.no_key``, ``config.key_set``), and a
    parameter named ``key`` would make ``t("config.no_key", key=name)`` raise
    ``TypeError: got multiple values for argument 'key'``.
    """
    catalog = CATALOG.get(_LANG, CATALOG[_DEFAULT])
    text = catalog.get(message_id) or CATALOG[_DEFAULT].get(message_id, message_id)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError, TypeError):
        return text
