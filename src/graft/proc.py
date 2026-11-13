"""Running a child process under a wall-clock timeout and a memory cap.

Every query execution in this project goes through :func:`run_with_limits`
(CLAUDE.md: "Every query execution has a timeout (default 10 s) and a memory
cap").  The child is put in its own process group so that a timeout kills the
whole group, not just the direct child: engine CLIs spawn helpers that would
otherwise survive.
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MEM_MB = 2048
#: seconds to wait for a killed process group to be reaped before giving up
_REAP_GRACE_S = 5.0


@dataclass(frozen=True)
class ProcResult:
    """Outcome of one child process run.

    Attributes:
        stdout: captured standard output (decoded, undecodable bytes replaced).
        stderr: captured standard error.
        returncode: exit status, or the negative signal number if killed;
            ``None`` only if the process could not be reaped after a timeout.
        timed_out: True if the wall-clock limit was hit and the group was killed.
        elapsed_s: wall-clock seconds spent, including the kill.
    """

    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    elapsed_s: float

    @property
    def ok(self) -> bool:
        """True if the process exited 0 and did not time out."""
        return self.returncode == 0 and not self.timed_out


def _preexec(mem_mb: int | None) -> Callable[[], None]:
    """Build the child-side hook: new process group, memory cap, no core dumps."""

    def apply() -> None:  # pragma: no cover - runs in the forked child
        os.setsid()
        if mem_mb:
            limit = int(mem_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the child's whole process group, falling back to the child."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def run_with_limits(
    cmd: Sequence[str],
    input: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    mem_mb: int | None = DEFAULT_MEM_MB,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    on_spawn: Callable[[int], None] | None = None,
) -> ProcResult:
    """Run ``cmd``, feeding ``input`` on stdin, under a timeout and memory cap.

    Args:
        cmd: argv of the process to run (never a shell string).
        input: text written to the child's stdin, then closed.
        timeout_s: wall-clock limit; on expiry the process group is SIGKILLed
            and ``timed_out`` is True.
        mem_mb: address-space cap (RLIMIT_AS) in MiB; ``None`` disables it.
        cwd: working directory for the child.
        env: full environment for the child; ``None`` inherits this process's.
        on_spawn: called with the child's pid right after it starts, before any
            output is read. Used by the adapters so a test can kill an engine
            mid-query; exceptions raised by the callback are ignored.

    Returns:
        A :class:`ProcResult`. Non-zero exits are returned, not raised.

    Raises:
        FileNotFoundError: if ``cmd[0]`` does not exist.
        ValueError: if ``cmd`` is empty or ``timeout_s`` is not positive.
    """
    argv = list(cmd)
    if not argv:
        raise ValueError("cmd must not be empty")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be positive, got {timeout_s}")

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(cwd)) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        preexec_fn=_preexec(mem_mb),
        close_fds=True,
    )

    if on_spawn is not None:
        try:
            on_spawn(proc.pid)
        except Exception:  # pragma: no cover - a broken hook must not kill the run
            pass

    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=_REAP_GRACE_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - child ignored SIGKILL
            stdout, stderr = "", ""

    return ProcResult(
        stdout=stdout or "",
        stderr=stderr or "",
        returncode=proc.returncode,
        timed_out=timed_out,
        elapsed_s=time.monotonic() - started,
    )
