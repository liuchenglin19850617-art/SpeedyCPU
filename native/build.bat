@echo off
rem Build the SpeedyCPU native engine with MSVC.
rem
rem   build.bat            - release build into native\dist
rem   build.bat debug      - debug build
rem
rem Requires Visual Studio 2019/2022 with "Desktop development with C++"
rem (or the standalone Build Tools). No CMake required.
setlocal

cd /d "%~dp0"

set CONFIG=Release
if /i "%1"=="debug" set CONFIG=Debug

set OUT=dist
if not exist "%OUT%" mkdir "%OUT%"

rem --- locate a compiler -------------------------------------------------
where cl.exe >nul 2>nul
if errorlevel 1 (
  echo cl.exe not on PATH, looking for Visual Studio ...
  set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
  if exist "!VSWHERE!" (
    for /f "usebackq tokens=*" %%i in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSPATH=%%i"
  )
  if defined VSPATH (
    call "!VSPATH!\VC\Auxiliary\Build\vcvars64.bat" >nul
  ) else (
    echo.
    echo error: no C++ compiler found.
    echo   install "Desktop development with C++" from Visual Studio, or
    echo   use build.sh which also supports zig / MinGW.
    exit /b 1
  )
)

echo ------------------------------------------------------------------
cl.exe 2>&1 | findstr /i version
echo configuration: %CONFIG%
echo ------------------------------------------------------------------

set CFLAGS=/nologo /std:c++17 /EHsc /W4 /D_WIN32_WINNT=0x0A00 /MT
if /i "%CONFIG%"=="Debug" (
  set CFLAGS=!CFLAGS! /Zi /Od
) else (
  set CFLAGS=!CFLAGS! /O2 /GL /DNDEBUG
)

echo building scpu-watch.exe ...
cl %CFLAGS% /Fe:"%OUT%\scpu-watch.exe" /Fo:"%OUT%\scpu-watch.obj" scpu_watch.cpp /link psapi.lib user32.lib winmm.lib /LTCG
if errorlevel 1 exit /b 1

echo building scpu-timer.exe ...
cl %CFLAGS% /Fe:"%OUT%\scpu-timer.exe" /Fo:"%OUT%\scpu-timer.obj" scpu_timer.cpp /link winmm.lib /LTCG
if errorlevel 1 exit /b 1

del /q "%OUT%\*.obj" "%OUT%\*.exp" "%OUT%\*.lib" 2>nul

echo.
echo done:
dir /b "%OUT%\*.exe"
endlocal
