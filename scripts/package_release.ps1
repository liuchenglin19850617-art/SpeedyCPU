# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors
<#
.SYNOPSIS
    Assemble the SpeedyCPU portable release bundle.

.DESCRIPTION
    Takes the build artefacts produced by the other toolchains and lays them
    out the way the product expects to find them at runtime:

        SpeedyCPU.exe        frozen CLI            (PyInstaller)
        s-cpu.cmd            the `s-cpu` alias     (scripts/s-cpu.cmd)
        scpu-watch.exe       C++ engine            (native/dist)
        scpu-timer.exe       timer helper          (native/dist)
        scpu-agent.jar       Java engine           (java/dist)
        LICENSE / COPYING    the LGPL-2.0 texts
        README.md            quick start

    Missing pieces are reported and skipped rather than aborting, because a
    bundle with only the Python engine is still a working product.

.EXAMPLE
    ./scripts/package_release.ps1 -Version 0.1.0

.EXAMPLE
    # CI assembles outside the checkout so a build never dirties the tree.
    ./scripts/package_release.ps1 -Version 0.1.0 -Zip -ReleaseDir $env:RUNNER_TEMP/release
#>
[CmdletBinding()]
param(
    [string]$Version = '0.1.0',
    # Defaults to <source>/../release, i.e. the "release" folder that sits
    # beside "source" in the shipped two-folder layout.
    [string]$ReleaseDir,
    [switch]$Zip
)

$ErrorActionPreference = 'Stop'

$SourceRoot = Split-Path -Parent $PSScriptRoot
if (-not $ReleaseDir) {
    $ReleaseDir = Join-Path (Split-Path -Parent $SourceRoot) 'release'
}
$ReleaseDir = [System.IO.Path]::GetFullPath($ReleaseDir)
$StageDir = Join-Path $ReleaseDir "SpeedyCPU-$Version-win64"

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "    ok   $message" -ForegroundColor Green }
function Write-Skip($message) { Write-Host "    skip $message" -ForegroundColor Yellow }

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

Write-Step "assembling SpeedyCPU $Version"

# --- frozen CLI ---------------------------------------------------------
$frozen = Join-Path $SourceRoot "dist/SpeedyCPU.exe"
if (Test-Path $frozen) {
    Copy-Item $frozen $StageDir
    Write-Ok "SpeedyCPU.exe"
} else {
    Write-Skip "SpeedyCPU.exe not found - run: pyinstaller --noconfirm SpeedyCPU.spec"
}

# --- s-cpu alias --------------------------------------------------------
$alias = Join-Path $SourceRoot "scripts/s-cpu.cmd"
if (Test-Path $alias) {
    Copy-Item $alias $StageDir
    Write-Ok "s-cpu.cmd"
}

# --- native engine ------------------------------------------------------
foreach ($binary in @('scpu-watch.exe', 'scpu-timer.exe')) {
    $path = Join-Path $SourceRoot "native/dist/$binary"
    if (Test-Path $path) {
        Copy-Item $path $StageDir
        Write-Ok $binary
    } else {
        Write-Skip "$binary not found - build with native/build.bat or CMake"
    }
}

# --- java engine --------------------------------------------------------
$jar = Join-Path $SourceRoot "java/dist/scpu-agent.jar"
if (Test-Path $jar) {
    Copy-Item $jar $StageDir
    Write-Ok "scpu-agent.jar"
} else {
    Write-Skip "scpu-agent.jar not found - build with java/build.bat"
}

# --- paperwork ----------------------------------------------------------
foreach ($file in @(
        'LICENSE', 'COPYING',
        'README.md', 'README.zh-CN.md', 'CHANGELOG.md',
        'docs/HOW-IT-WORKS.md'
    )) {
    $path = Join-Path $SourceRoot $file
    if (Test-Path $path) {
        # Preserve any subdirectory: the bundle keeps a docs/ folder instead of
        # flattening it, so the relative links in README.md stay valid in the
        # shipped copy, not only in the repository.
        $target = Join-Path $StageDir (Split-Path $file -Leaf)
        $parent = Split-Path $file -Parent
        if ($parent) {
            New-Item -ItemType Directory -Path (Join-Path $StageDir $parent) -Force | Out-Null
        }
        Copy-Item $path $target
    }
}
Write-Ok "license and docs"

# --- quick start --------------------------------------------------------
# The text lives in scripts/README-PORTABLE.txt rather than in a here-string so
# it can be reviewed, diffed and spell-checked like any other file.
$quickStartTemplate = Join-Path $PSScriptRoot 'README-PORTABLE.txt'
if (Test-Path $quickStartTemplate) {
    $quickStart = (Get-Content $quickStartTemplate -Raw) -replace '@VERSION@', $Version
    Set-Content -Path (Join-Path $StageDir 'README-PORTABLE.txt') -Value $quickStart -Encoding UTF8
    Write-Ok "README-PORTABLE.txt"
} else {
    Write-Skip "README-PORTABLE.txt template missing"
}

# --- zip ----------------------------------------------------------------
if ($Zip) {
    $zipPath = Join-Path $ReleaseDir "SpeedyCPU-$Version-win64.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $StageDir '*') -DestinationPath $zipPath
    Write-Ok "SpeedyCPU-$Version-win64.zip"
}

Write-Step "done: $StageDir"
Get-ChildItem $StageDir | Select-Object Name, Length | Format-Table -AutoSize
