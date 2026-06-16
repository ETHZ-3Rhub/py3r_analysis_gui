"""Create/refresh a tracking_env/ venv: PyTorch (CUDA build auto-detected by
uv, or CPU fallback) + pinned ultralytics/lap.

Invoked via `--setup-tracking-env <dir>` (see app/main.py) by the in-app
"Reinstall tracking environment" button — a frozen exe can't run an
arbitrary .py script directly, so this is dispatched through the same
entry point instead.
"""

from __future__ import annotations

import datetime
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QThread, Signal

from app.proc_utils import NO_WINDOW

# Substring -> plain-English diagnosis, checked against combined install
# output when a step fails. Not a parser - covers the common cases only.
_KNOWN_ERRORS = [
    ("Could not connect", "No internet connection - connect and try again."),
    ("Temporary failure in name resolution", "No internet connection - connect and try again."),
    ("Network is unreachable", "No internet connection - connect and try again."),
    ("No space left on device", "Disk is full - free up space and try again."),
    (
        "Permission denied",
        "Permission error - check folder permissions or try running as administrator.",
    ),
    (
        "No matching distribution",
        "Could not find a matching package version - this may be a temporary "
        "index issue, try again later.",
    ),
]

# Hosts the install actually downloads from - used for the pre-flight
# connectivity check.
_CONNECTIVITY_HOSTS = ["pypi.org", "download.pytorch.org"]


def _uv_exe() -> str:
    """Path to the uv binary: bundled copy in a frozen app, else PATH."""
    if getattr(sys, "frozen", False):
        candidate = Path(sys._MEIPASS) / "vendor" / "uv.exe"
        if candidate.exists():
            return str(candidate)

    found = shutil.which("uv")
    if found:
        return found

    sys.exit(
        "uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/"
    )


def _uv_version(uv: str) -> str:
    try:
        r = subprocess.run(
            [uv, "--version"], capture_output=True, text=True, creationflags=NO_WINDOW
        )
        return (r.stdout.strip() or r.stderr.strip()) or "unknown"
    except Exception:
        return "unknown"


def _has_nvidia_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


def _check_internet(timeout: float = 3.0) -> bool:
    """Cheap pre-flight reachability check against the hosts the install
    actually downloads from. Not a reliability oracle - short timeout, no
    retries."""
    for host in _CONNECTIVITY_HOSTS:
        try:
            socket.create_connection((host, 443), timeout=timeout).close()
        except OSError:
            return False
    return True


def _classify_error(text: str) -> str | None:
    for needle, message in _KNOWN_ERRORS:
        if needle in text:
            return message
    return None


def _versions_yaml_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "versions.yaml"


def _load_tracking_versions() -> dict[str, str]:
    data = yaml.safe_load(_versions_yaml_path().read_text())
    return data["dependencies"]["tracking"]


def _python_exe(tracking_env: Path) -> Path:
    if sys.platform == "win32":
        return tracking_env / "Scripts" / "python.exe"
    return tracking_env / "bin" / "python"


_CUDA_SMOKE_TEST = """
import sys, torch
if not torch.cuda.is_available():
    print("CUDA_NOT_AVAILABLE")
    sys.exit(1)
try:
    torch.zeros(1, device="cuda")
    print(f"CUDA_OK:{torch.cuda.get_device_name(0)}")
except Exception as e:
    print(f"CUDA_ERROR:{e}")
    sys.exit(1)
"""


def _run(log_lines: list[str], *cmd: str) -> str:
    """Run *cmd*, appending its combined stdout/stderr to *log_lines* and
    printing it.

    Returns the combined output. Raises `subprocess.CalledProcessError`
    (with `.output` set) on non-zero exit.
    """
    header = f"\n$ {' '.join(cmd)}"
    print(header)
    log_lines.append(header + "\n")
    r = subprocess.run(list(cmd), capture_output=True, text=True, creationflags=NO_WINDOW)
    combined = r.stdout + r.stderr
    print(combined, end="")
    log_lines.append(combined)
    if r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, cmd, output=combined)
    return combined


def _install_torch(
    log_lines: list[str], uv: str, python: str, versions: dict[str, str], backend: str
) -> str:
    return _run(
        log_lines,
        uv,
        "pip",
        "install",
        "--python",
        python,
        f"torch=={versions['torch']}",
        f"torchvision=={versions['torchvision']}",
        "--torch-backend",
        backend,
    )


def _verify_cuda(python: str) -> tuple[bool, str]:
    """Smoke-test the installed torch. Returns (gpu_ok, message)."""
    r = subprocess.run(
        [python, "-c", _CUDA_SMOKE_TEST], capture_output=True, text=True, creationflags=NO_WINDOW
    )
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith("CUDA_OK:"):
            return True, line[len("CUDA_OK:") :]
        if line.startswith("CUDA_NOT_AVAILABLE"):
            return False, "CUDA not available"
        if line.startswith("CUDA_ERROR:"):
            return False, line[len("CUDA_ERROR:") :]
    return False, (r.stdout + r.stderr).strip() or "unknown error"


_INSTALL_LOG_NAME = "install.log"


def setup(tracking_env: Path) -> int:
    """Create/refresh *tracking_env* in place. Returns a process exit code.

    Writes a full install log to `tracking_env/install.log` (held in memory
    and written at the end, since `uv venv --clear` wipes the directory
    first). On failure, prints a `DIAGNOSIS: <message>` line with a
    plain-English summary for `_ReinstallWorker` to surface.
    """
    uv = _uv_exe()
    log_lines: list[str] = []

    def out(msg: str = "") -> None:
        print(msg)
        log_lines.append(msg + "\n")

    # Install the base Python *inside the app directory* (alongside
    # tracking_env), not in uv's default %LOCALAPPDATA%\uv\python. That default
    # location may be unwritable, redirected, or quota'd under locked-down /
    # roaming Windows profiles, and can be wiped by IT cleanup - either of which
    # leaves the thin venv with a dangling `home=`. The app dir is already
    # proven writable (we build tracking_env into it), so it's the safe home.
    # UV_MANAGED_PYTHON forces a uv-managed Python here rather than borrowing a
    # system interpreter we don't control (which would reintroduce the same
    # external-base fragility). Set on os.environ - setup() runs in its own
    # `--setup-tracking-env` subprocess, so this can't leak into the GUI.
    python_install_dir = tracking_env.parent / "uv_python"
    os.environ["UV_PYTHON_INSTALL_DIR"] = str(python_install_dir)
    os.environ["UV_MANAGED_PYTHON"] = "1"

    try:
        out(f"Tracking env install log - {datetime.datetime.now().isoformat()}")
        out(f"Tracking env:  {tracking_env}")
        out(f"Python base:   {python_install_dir} (uv-managed)")
        out(f"uv:            {_uv_version(uv)}")

        if not _check_internet():
            out("\nNo internet connection detected.")
            out("DIAGNOSIS: No internet connection - connect and try again.")
            return 1

        versions = _load_tracking_versions()
        out(
            f"PyTorch:       {versions['torch']} / torchvision {versions['torchvision']} "
            "(torch-backend=auto)"
        )
        out(f"ultralytics:   {versions['ultralytics']}")
        out(f"lap:           {versions['lap']}")

        try:
            _run(log_lines, uv, "venv", str(tracking_env), "--python", "3.12", "--clear")
            python = str(_python_exe(tracking_env))
            _install_torch(log_lines, uv, python, versions, "auto")
            _run(
                log_lines,
                uv,
                "pip",
                "install",
                "--python",
                python,
                f"ultralytics=={versions['ultralytics']}",
                f"lap=={versions['lap']}",
            )
        except subprocess.CalledProcessError as exc:
            out(f"\nInstall command failed (exit {exc.returncode}).")
            diag = _classify_error(exc.output or "") or (
                f"Setup failed - see tracking_env/{_INSTALL_LOG_NAME} for details."
            )
            out(f"DIAGNOSIS: {diag}")
            return 1

        out("\nVerifying GPU...")
        ok, msg = _verify_cuda(python)
        if ok:
            out(f"\nReady. GPU: {msg}")
        elif _has_nvidia_gpu():
            out(f"GPU verification failed: {msg}")
            out("Falling back to CPU - reinstalling PyTorch...")
            try:
                _install_torch(log_lines, uv, python, versions, "cpu")
            except subprocess.CalledProcessError as exc:
                out(f"\nInstall command failed (exit {exc.returncode}).")
                diag = _classify_error(exc.output or "") or (
                    f"Setup failed - see tracking_env/{_INSTALL_LOG_NAME} for details."
                )
                out(f"DIAGNOSIS: {diag}")
                return 1
            out("\nReady. Running on CPU (GPU unavailable).")
        else:
            out("\nReady. Running on CPU (no GPU detected).")

        return 0
    finally:
        # tracking_env may not exist if `uv venv` itself failed (or we never
        # got that far, e.g. offline) - make sure it does before writing.
        tracking_env.mkdir(parents=True, exist_ok=True)
        (tracking_env / _INSTALL_LOG_NAME).write_text("".join(log_lines), encoding="utf-8")


class _ReinstallWorker(QThread):
    output = Signal(str)
    done = Signal(bool, str)  # success, diagnosis ("" if none)

    def run(self) -> None:
        from app.trackers.yolo_tracker import tracking_env_dir

        target = tracking_env_dir()
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--setup-tracking-env", str(target)]
        else:
            cmd = [sys.executable, "-m", "app.main", "--setup-tracking-env", str(target)]
        diagnosis = ""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW,
            )
            for raw in proc.stdout:
                text = raw.decode("utf-8", errors="replace")
                self.output.emit(text)
                for line in text.splitlines():
                    if line.startswith("DIAGNOSIS:"):
                        diagnosis = line[len("DIAGNOSIS:") :].strip()
            proc.wait()
            self.done.emit(proc.returncode == 0, diagnosis)
        except Exception as exc:
            self.output.emit(f"Error: {exc}\n")
            self.done.emit(False, str(exc))
