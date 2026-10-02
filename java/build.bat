@echo off
rem Build scpu-agent.jar with nothing but a JDK on PATH.
rem
rem   build.bat            -> dist\scpu-agent.jar
rem
rem Requires JDK 22 or newer (Foreign Function & Memory API). No JNI, no C++
rem toolchain, no Maven - just javac and jar.
setlocal enabledelayedexpansion

cd /d "%~dp0"

if "%RELEASE%"=="" set RELEASE=22
set OUT=build\classes
set DIST=dist

if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%" 2>nul
mkdir "%DIST%" 2>nul

set SOURCES=
for /r "src\main\java" %%f in (*.java) do set SOURCES=!SOURCES! "%%f"

if "!SOURCES!"=="" (
  echo error: no java sources found 1>&2
  exit /b 1
)

echo compiling with:
javac -version
javac --release %RELEASE% -encoding UTF-8 -d "%OUT%" !SOURCES!
if errorlevel 1 exit /b 1

echo packaging %DIST%\scpu-agent.jar ...
rem The manifest matters: without Enable-Native-Access: ALL-UNNAMED the JVM
rem refuses to let the agent call Linker.nativeLinker() on JDK 25 and later.
if not exist "MANIFEST.MF" (
  echo error: MANIFEST.MF is missing 1>&2
  exit /b 1
)
jar --create --file "%DIST%\scpu-agent.jar" --manifest MANIFEST.MF -C "%OUT%" .
if errorlevel 1 exit /b 1

echo done: %DIST%\scpu-agent.jar
endlocal
