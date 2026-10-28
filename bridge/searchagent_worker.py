"""Bounded process launcher for the fixed SearchAgent worker command."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


MAX_WORKER_STDOUT_BYTES = 16 * 1024
MAX_WORKER_STDERR_BYTES = 8 * 1024
_READ_CHUNK_SIZE = 4096


class SearchAgentWorkerTimeout(TimeoutError):
    pass


class SearchAgentWorkerOutputTooLarge(ValueError):
    pass


class SearchAgentWorkerLaunchError(RuntimeError):
    pass


def _minimal_worker_environment() -> dict[str, str]:
    """Pass OS/runtime variables only; keep Bridge credentials out of the worker."""
    names = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "HOME",
        "TMPDIR",
    )
    env = {name: os.environ[name] for name in names if name in os.environ}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _read_bounded(
    stream,
    *,
    maximum: int,
    destination: bytearray,
    overflow: threading.Event,
) -> None:
    try:
        read_chunk = getattr(stream, "read1", stream.read)
        while True:
            chunk = read_chunk(_READ_CHUNK_SIZE)
            if not chunk:
                return
            remaining = maximum + 1 - len(destination)
            if remaining > 0:
                destination.extend(chunk[:remaining])
            if len(destination) > maximum or len(chunk) > max(remaining, 0):
                overflow.set()
    except (OSError, ValueError):
        return


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            windows_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
            taskkill = Path(windows_root or r"C:\Windows") / "System32" / "taskkill.exe"
            result = subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


def run_searchagent_worker(
    *,
    python_executable: Path,
    project_root: Path,
    request: bytes,
    timeout_seconds: float,
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> tuple[bytes, int]:
    """Run only ``<python> <root>/main.py worker`` with a bounded JSON exchange."""
    main_file = project_root / "main.py"
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or not python_executable.is_absolute()
        or not project_root.is_absolute()
        or not python_executable.is_file()
        or not project_root.is_dir()
        or not main_file.is_file()
    ):
        raise SearchAgentWorkerLaunchError()

    command = [str(python_executable), str(main_file), "worker"]
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    )
    try:
        process = popen_factory(
            command,
            cwd=str(project_root),
            env=_minimal_worker_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SearchAgentWorkerLaunchError() from None

    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    readers = [
        threading.Thread(
            target=_read_bounded,
            kwargs={
                "stream": process.stdout,
                "maximum": MAX_WORKER_STDOUT_BYTES,
                "destination": stdout,
                "overflow": overflow,
            },
            daemon=True,
        ),
        threading.Thread(
            target=_read_bounded,
            kwargs={
                "stream": process.stderr,
                "maximum": MAX_WORKER_STDERR_BYTES,
                "destination": stderr,
                "overflow": overflow,
            },
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    try:
        assert process.stdin is not None
        try:
            process.stdin.write(request)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            try:
                process.stdin.close()
            except OSError:
                pass

        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if overflow.is_set():
                _terminate_process_tree(process)
                raise SearchAgentWorkerOutputTooLarge()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(process)
                raise SearchAgentWorkerTimeout()
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue

        process.wait()
        for reader in readers:
            reader.join(timeout=1)
        if (
            overflow.is_set()
            or len(stdout) > MAX_WORKER_STDOUT_BYTES
            or len(stderr) > MAX_WORKER_STDERR_BYTES
        ):
            raise SearchAgentWorkerOutputTooLarge()
        return bytes(stdout), int(process.returncode)
    finally:
        if process.poll() is None:
            _terminate_process_tree(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for reader in readers:
            reader.join(timeout=1)
