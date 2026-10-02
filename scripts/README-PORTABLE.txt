SpeedyCPU @VERSION@ - portable build
================================================

Thank you for trying SpeedyCPU. Please read the first section: it tells you
what this tool can and cannot do, which matters more here than in most
downloads.

WHAT IT ACTUALLY DOES
---------------------
SpeedyCPU cannot make your CPU faster, and it cannot make a program create
more threads than it was written to. What it does is change who wins when
several programs want the CPU at the same time:

  * raises the priority of every thread of the program you are looking at
  * clears Windows' power throttling (EcoQoS) on that program
  * releases the boost the moment you switch to another window
  * removes a locked single-core affinity mask, if one was left behind
  * raises the system timer from 15.625 ms to 1 ms for the session
  * tunes the processor power settings - no administrator rights needed

Every value it changes is recorded first, and `s-cpu off` puts all of them
back. It writes nothing to the registry and touches no system directory.

WHAT IT DOES NOT DO
-------------------
  * It cannot raise a clock speed. A throttling laptop is still throttling.
  * It cannot add threads to a single-threaded program.
  * It will not show up as "2x faster" in a benchmark. What improves is frame
    time consistency and input latency under load. Compare 99th percentile
    frame times, not average FPS.
  * Some kernel-level anti-cheat software dislikes anything that adjusts
    another process' priority. Do not run it with competitive games that use
    kernel anti-cheat.

Full details: see HOW-IT-WORKS.md and README.md in this folder.

QUICK START
-----------
1. Add this folder to PATH (optional, but it is what makes the documented
   command work from anywhere):

     setx PATH "%PATH%;<this folder>"

   Or just run s-cpu.cmd from this folder.

2. Turn it on and off:

     s-cpu on        enable the boost
     s-cpu off       disable and restore everything
     s-cpu status    what is running right now
     s-cpu doctor    what this machine supports

ENGINES BUNDLED HERE
--------------------
  scpu-watch.exe   C++ engine    ~2 MB, reacts to focus changes instantly
  scpu-agent.jar   Java engine   needs JDK 22+ on PATH
  (built in)       Python engine always available, used when nothing else is

`s-cpu on` picks the best available engine automatically. Force one with:

     s-cpu on --engine native
     s-cpu on --engine java
     s-cpu on --engine python

ADMINISTRATOR RIGHTS
--------------------
Not required. Everything the tool does to individual processes works
unelevated. Running an elevated CMD additionally unlocks the High performance
power plan and the machine-wide EcoQoS switch:

     s-cpu on --global-throttling

CONFIGURATION AND LOGS
----------------------
     %LOCALAPPDATA%\SpeedyCPU\config.json      your settings
     %LOCALAPPDATA%\SpeedyCPU\state.json       what is currently applied
     %LOCALAPPDATA%\SpeedyCPU\worker.log       engine log

     s-cpu config show
     s-cpu config set mode all
     s-cpu targets add chrome.exe

UNINSTALLING
------------
     s-cpu off
     then delete this folder and %LOCALAPPDATA%\SpeedyCPU

Licence: LGPL-2.0-or-later. See LICENSE and COPYING.
Source:  https://github.com/speedycpu/speedycpu
