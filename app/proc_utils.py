"""Shared helpers for launching and killing subprocesses as process groups.

Both the tracker and the pipeline run as subprocesses so that "Cancel" can
kill them outright (`QThread.terminate()` on a thread doing in-process work
is unsafe — it can leave native locks held and hang the GUI thread).
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys

# Avoids a flurry of console windows popping up over the GUI when this
# windowed (console=False) app spawns helper processes on Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def popen_grouped(cmd: list[str], **kwargs) -> subprocess.Popen:
    """Launch *cmd* in its own process group/session so it can be killed as a tree."""
    if platform.system() == "Windows":
        kwargs.setdefault(
            "creationflags",
            subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW,
        )
    else:
        kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(cmd, **kwargs)


def kill_tree(pid: int) -> None:
    """Kill a process and all its children (Windows: taskkill /F /T, Unix: SIGKILL pgid)."""
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
