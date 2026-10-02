@echo off
rem SPDX-License-Identifier: LGPL-2.0-or-later
rem
rem The `s-cpu` command for a portable install: drop this file next to
rem SpeedyCPU.exe and put the folder on PATH, and `s-cpu on` works from any
rem CMD window without installing anything.
rem
rem %~dp0 is the folder containing this script, which is also where the
rem bundled engines live.

setlocal
set "SPEEDYCPU_DIR=%~dp0"
"%SPEEDYCPU_DIR%SpeedyCPU.exe" %*
exit /b %ERRORLEVEL%
