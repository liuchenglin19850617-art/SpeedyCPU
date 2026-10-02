#!/usr/bin/env bash
# Build scpu-agent.jar with nothing but a JDK on PATH.
#
#   ./build.sh            -> dist/scpu-agent.jar
#   ./build.sh --uber     -> also emit the sources list (debug aid)
#
# Requires JDK 22 or newer: the agent talks to Win32 through the Foreign
# Function & Memory API, which became final in JDK 22. There is no JNI and no
# C++ toolchain involved, so the jar is portable across machines that only have
# a JRE/JDK installed.
set -euo pipefail

cd "$(dirname "$0")"
JAVAC="${JAVAC:-javac}"
JAR="${JAR:-jar}"
RELEASE="${RELEASE:-22}"

OUT="build/classes"
DIST="dist"

rm -rf "$OUT"
mkdir -p "$OUT" "$DIST"

mapfile -t SOURCES < <(find src/main/java -name '*.java' | sort)
if [ "${#SOURCES[@]}" -eq 0 ]; then
  echo "error: no java sources found" >&2
  exit 1
fi

echo "compiling ${#SOURCES[@]} source file(s) with $("$JAVAC" -version 2>&1) ..."
"$JAVAC" --release "$RELEASE" -encoding UTF-8 -Xlint:all -d "$OUT" "${SOURCES[@]}"

echo "packaging $DIST/scpu-agent.jar ..."
# The manifest matters: without Enable-Native-Access: ALL-UNNAMED the JVM
# refuses to let the agent call Linker.nativeLinker() on JDK 25 and later.
if [ ! -f MANIFEST.MF ]; then
  echo "error: MANIFEST.MF is missing" >&2
  exit 1
fi
"$JAR" --create --file "$DIST/scpu-agent.jar" \
  --manifest MANIFEST.MF \
  -C "$OUT" .

echo "done: $DIST/scpu-agent.jar"
