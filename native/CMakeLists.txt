cmake_minimum_required(VERSION 3.16)
project(SpeedyCPU-native LANGUAGES CXX)

# SPDX-License-Identifier: LGPL-2.0-or-later
# Copyright (C) 2026 SpeedyCPU contributors

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT WIN32)
    message(FATAL_ERROR "SpeedyCPU's native engine is Windows only (kernel32/user32/winmm).")
endif()

add_executable(scpu-watch scpu_watch.cpp)
target_compile_definitions(scpu-watch PRIVATE _WIN32_WINNT=0x0A00 UNICODE _UNICODE)
target_link_libraries(scpu-watch PRIVATE psapi user32 winmm)
if(MSVC)
    target_compile_options(scpu-watch PRIVATE /W4 /permissive-)
    # Static CRT so the exe drops into the release folder with no redistributable.
    set_property(TARGET scpu-watch PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
else()
    target_compile_options(scpu-watch PRIVATE -Wall -Wextra)
    target_link_options(scpu-watch PRIVATE -static -s)
endif()

add_executable(scpu-timer scpu_timer.cpp)
target_compile_definitions(scpu-timer PRIVATE _WIN32_WINNT=0x0A00 UNICODE _UNICODE)
target_link_libraries(scpu-timer PRIVATE winmm)
if(MSVC)
    target_compile_options(scpu-timer PRIVATE /W4 /permissive-)
    set_property(TARGET scpu-timer PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
else()
    target_compile_options(scpu-timer PRIVATE -Wall -Wextra)
    target_link_options(scpu-timer PRIVATE -static -s)
endif()

# Everything lands in one place so the packaging scripts have a single source.
set_target_properties(scpu-watch scpu-timer PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_SOURCE_DIR}/dist"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_SOURCE_DIR}/dist"
)
