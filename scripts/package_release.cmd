@echo off
rem SPDX-License-Identifier: LGPL-2.0-or-later
rem
rem Launcher for package_release.ps1.
rem
rem Many Windows machines ship with ExecutionPolicy = Restricted, which makes
rem running a .ps1 at all an error ("running scripts is disabled on this
rem system"). A .cmd file is not subject to that policy, so this wrapper starts a
rem fresh PowerShell with the policy bypassed *for that invocation only* -
rem nothing on the machine is changed, and no permanent setting is touched.
rem
rem   scripts\package_release.cmd -Version 0.1.0
rem   scripts\package_release.cmd -Version 0.1.0 -Zip
rem
rem Any arguments are forwarded verbatim to the PowerShell script.

setlocal
set "SCRIPT=%~dp0package_release.ps1"
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%SCRIPT%" %*
exit /b %ERRORLEVEL%
