"""Tests for common.proc: exit codes, stdin, timeouts, memory caps."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from graft.proc import DEFAULT_MEM_MB, DEFAULT_TIMEOUT_S, run_with_limits


def test_defaults_match_claude_md():
    assert DEFAULT_TIMEOUT_S == 10.0
    assert DEFAULT_MEM_MB > 0


# --- known-good --------------------------------------------------------------


def test_successful_command():
    result = run_with_limits([sys.executable, "-I", "-c", "print('hello')"])
    assert result.ok
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.elapsed_s >= 0


def test_stdin_is_passed_and_closed():
    result = run_with_limits(
        [sys.executable, "-I", "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        input="select 1;\n",
    )
    assert result.ok
    assert result.stdout == "select 1;\n"


def test_stderr_and_nonzero_exit_are_returned_not_raised():
    result = run_with_limits(
        [sys.executable, "-I", "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
    )
    assert result.returncode == 3
    assert result.ok is False
    assert result.stderr.strip() == "boom"


def test_undecodable_output_is_replaced_not_raised():
    result = run_with_limits(
        [sys.executable, "-I", "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"]
    )
    assert result.returncode == 0
    assert result.stdout  # replacement characters, no UnicodeDecodeError


def test_cwd_and_env_are_honoured(tmp_path):
    result = run_with_limits(
        [sys.executable, "-I", "-c", "import os; print(os.getcwd()); print(os.environ.get('GRAFT_MARKER'))"],
        cwd=tmp_path,
        env={**os.environ, "GRAFT_MARKER": "set"},
    )
    lines = result.stdout.splitlines()
    assert os.path.realpath(lines[0]) == os.path.realpath(str(tmp_path))
    assert lines[1] == "set"


# --- timeout (the required 5 s sleep under a 1 s limit) ----------------------


def test_timeout_kills_a_five_second_sleep_after_one_second():
    started = time.monotonic()
    result = run_with_limits(
        [sys.executable, "-I", "-c", "import time; time.sleep(5)"],
        timeout_s=1.0,
    )
    wall = time.monotonic() - started
    assert result.timed_out is True
    assert result.ok is False
    assert wall < 4.0, f"timeout did not stop the child promptly ({wall:.1f}s)"
    assert result.returncode != 0


def test_timeout_kills_the_whole_process_group(tmp_path):
    """A grandchild outliving its parent must be killed with the group."""
    marker = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, os, time\n"
        f"p = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(30)\n"
    )
    result = run_with_limits([sys.executable, "-I", "-c", script], timeout_s=1.5)
    assert result.timed_out is True
    grandchild_pid = int(marker.read_text())
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild_pid, 9)
        pytest.fail("grandchild survived the timeout kill")


# --- memory cap ---------------------------------------------------------------


def test_memory_cap_stops_a_large_allocation():
    result = run_with_limits(
        [sys.executable, "-I", "-c", "b = bytearray(400 * 1024 * 1024); print(len(b))"],
        mem_mb=200,
    )
    assert result.ok is False
    assert result.timed_out is False
    assert "MemoryError" in result.stderr


def test_allocation_within_the_cap_succeeds():
    result = run_with_limits(
        [sys.executable, "-I", "-c", "b = bytearray(16 * 1024 * 1024); print(len(b))"],
        mem_mb=512,
    )
    assert result.ok
    assert result.stdout.strip() == str(16 * 1024 * 1024)


# --- known-bad arguments ------------------------------------------------------


def test_empty_cmd_raises():
    with pytest.raises(ValueError):
        run_with_limits([])


def test_non_positive_timeout_raises():
    with pytest.raises(ValueError):
        run_with_limits([sys.executable, "-I", "-c", "pass"], timeout_s=0)


def test_missing_binary_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_with_limits([str(tmp_path / "no-such-engine")])


# --- on_spawn hook -----------------------------------------------------------


def test_on_spawn_receives_the_child_pid():
    seen = []
    result = run_with_limits(
        [sys.executable, "-I", "-c", "import os; print(os.getpid())"],
        on_spawn=seen.append,
    )
    assert result.ok
    assert seen == [int(result.stdout.strip())]


def test_a_broken_on_spawn_hook_does_not_break_the_run():
    def boom(pid):
        raise RuntimeError("hook exploded")

    result = run_with_limits([sys.executable, "-I", "-c", "print('fine')"], on_spawn=boom)
    assert result.ok
    assert result.stdout.strip() == "fine"
