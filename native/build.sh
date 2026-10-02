#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors
#
# Build the SpeedyCPU native engine without Visual Studio.
#
# Tries, in order:
#   1. zig c++   - a complete C/C++ toolchain, installable with `pip install ziglang`
#   2. clang++   - with -target x86_64-windows-gnu
#   3. g++       - MinGW-w64
#
#   ./build.sh              -> native/dist/{scpu-watch.exe,scpu-timer.exe}
#   CXX="zig c++" ./build.sh
set -euo pipefail

cd "$(dirname "$0")"
OUT="dist"
mkdir -p "$OUT"

DETECTED="${CXX:-}"

# --- zig: prefer the one installed as a Python package, it is the easiest ----
ZIG="$(command -v zig || true)"
if [ -z "$ZIG" ] && command -v python3 >/dev/null 2>&1; then
  ZIG="$(python3 -c 'import ziglang,os,sys;print(os.path.join(os.path.dirname(ziglang.__file__),"zig.exe" if sys.platform=="win32" else "zig"))' 2>/dev/null || true)"
  [ -f "${ZIG:-/nonexistent}" ] || ZIG=""
fi

if [ -z "$DETECTED" ]; then
  if [ -n "$ZIG" ]; then
    DETECTED="$ZIG c++"
  elif command -v clang++ >/dev/null 2>&1; then
    DETECTED="clang++"
  elif command -v g++ >/dev/null 2>&1; then
    DETECTED="g++"
  fi
fi

if [ -z "$DETECTED" ]; then
  echo "error: no C++ compiler found." >&2
  echo "  easiest fix:  pip install ziglang" >&2
  echo "  or install MinGW-w64 / LLVM, or use build.bat with Visual Studio." >&2
  exit 1
fi

echo "compiler: $DETECTED"

# Shared flags. -static keeps the exes self contained so the release folder has
# no DLL surprises; -s strips symbols.
COMMON=(-O2 -std=c++17 -w -D_WIN32_WINNT=0x0A00 -static -s)

case "$DETECTED" in
  *zig*|*clang*)
    TARGET=(-target x86_64-windows-gnu)
    ;;
  *)
    TARGET=()
    ;;
esac

echo "building scpu-watch.exe ..."
# shellcheck disable=SC2086
$DETECTED "${TARGET[@]}" "${COMMON[@]}" -o "$OUT/scpu-watch.exe" scpu_watch.cpp -lpsapi -luser32 -lwinmm

echo "building scpu-timer.exe ..."
# shellcheck disable=SC2086
$DETECTED "${TARGET[@]}" "${COMMON[@]}" -o "$OUT/scpu-timer.exe" scpu_timer.cpp -lwinmm

# Zig emits PDBs next to the binaries; they are build artefacts, not products.
rm -f "$OUT"/*.pdb "$OUT"/*.obj

echo
echo "done:"
ls -la "$OUT"
