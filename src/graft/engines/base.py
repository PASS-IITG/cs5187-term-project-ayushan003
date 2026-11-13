"""The adapter contract: ``Result``, ``Engine``, and the shared CLI session.

SPEC, "Adapter interface":

    class Engine: name, kind; start(), close(), reset() (fresh empty db);
    version()->str; commit()->str; execute(sql, timeout=10.0)->Result.

    Result: ok, columns, rows, error, error_class, elapsed_s.

    Rules: a dead process -> error_class crash, adapter restarts it. Rust panic
    text, INTERNAL Error banner, assertion failure, segfault -> internal_error /
    panic / crash, never user_error. Adapters never raise on bad SQL; they
    return a Result. Every cell coerced to a canonical python type.

Canonical cell types
--------------------
Every cell in ``Result.rows`` is one of ``None, int, float, str, bool, bytes``.
The mapping from engine output is:

==================== ====================================================
engine value         python cell
==================== ====================================================
NULL                 ``None``
integer              ``int`` (arbitrary precision; no width clamping)
floating point       ``float``
boolean              ``bool``
text                 ``str``
blob / binary        ``bytes``
DECIMAL / NUMERIC    ``str``, keeping the engine's digits. SPEC: "Decimals
                     keep precision; not coerced to float before compare."
date / time / interval ``str``, in the engine's own rendering
list / struct / map  ``str`` holding the engine's JSON or literal rendering
==================== ====================================================

Engines reached over a text protocol (the Turso shell) cannot tell ``1`` from
``'1'``; those adapters set :attr:`Engine.text_cells` to True and return every
non-NULL cell as ``str``. The normalizer (P06) must consult ``text_cells``
before comparing a text-protocol engine against a typed one.

CLI session model
-----------------
:class:`CliEngine` does not hold an interactive child process. Engine shells
interleave results and diagnostics across stdout and stderr with no per
statement framing, so attributing output to a statement in a long-lived session
is guesswork. Instead a session is a *replayed prefix*: the adapter remembers
the statements that have succeeded, and each :meth:`CliEngine.execute` runs
``prefix + [sql]`` in a fresh process, parsing only the output that follows a
unique marker emitted just before the final statement. Consequences:

* a crashed engine needs no restart logic - the next call starts a new process -
  but :attr:`Engine.restarts` still counts the deaths, as SPEC asks;
* ``reset()`` is just "forget the prefix", which really is a fresh empty db;
* read-only statements are not added to the prefix, so prefixes stay short;
* :meth:`Engine.run_case` runs a whole TestCase in one process, which is the
  path the oracles and the fuzz runner should use.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Iterable, Sequence

from graft.engines import errors
from graft.proc import ProcResult, run_with_limits

#: SPEC/CLAUDE.md hard rule 7: what we do not know, we write as UNKNOWN.
UNKNOWN = "UNKNOWN"

DEFAULT_TIMEOUT_S = 10.0

#: Statements that cannot change the database, so they never join a replay
#: prefix. Deliberately conservative: anything not matched here is replayed.
_READ_ONLY_RE = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*"
    r"(?P<head>SELECT|VALUES|TABLE|EXPLAIN|DESCRIBE|SHOW)\b",
    re.IGNORECASE | re.DOTALL,
)
_MUTATING_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|ATTACH|DETACH|TRUNCATE|"
    r"COPY|SET|PRAGMA|BEGIN|COMMIT|ROLLBACK|VACUUM|ANALYZE|REINDEX)\b",
    re.IGNORECASE,
)


def is_read_only(sql: str) -> bool:
    """True if ``sql`` is a plain query that cannot change the database.

    Used to keep replay prefixes short. A false negative only costs time (the
    statement is replayed); a false positive would lose state, so the test is
    conservative: a leading SELECT/VALUES/TABLE/EXPLAIN/DESCRIBE/SHOW with no
    mutating keyword anywhere in the text.
    """
    if not _READ_ONLY_RE.match(sql):
        return False
    body = _READ_ONLY_RE.match(sql)
    assert body is not None
    rest = sql[body.end("head") :]
    return _MUTATING_RE.search(rest) is None


def canonical_cell(value: Any) -> None | int | float | str | bool | bytes:
    """Coerce one engine value to a canonical python cell type.

    See the module docstring for the full mapping. Anything unrecognised
    becomes ``str(value)`` rather than raising, so a surprising type from an
    engine can never break a campaign.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value
    return str(value)


def canonical_row(values: Iterable[Any]) -> tuple[Any, ...]:
    """Coerce a whole row with :func:`canonical_cell`."""
    return tuple(canonical_cell(v) for v in values)


@dataclass(frozen=True)
class Result:
    """The outcome of one statement, exactly as SPEC defines it.

    Attributes:
        ok: True if the statement completed without an error.
        columns: column names, or None when the engine did not report any
            (a statement with no result set, or an empty result in a format
            that carries names only on rows).
        rows: result rows as tuples of canonical cells, or None on failure.
        error: the engine's raw message, or None on success.
        error_class: one of ``adapters.errors.ERROR_CLASSES``.
        elapsed_s: wall-clock seconds the statement took.
    """

    ok: bool
    columns: list[str] | None = None
    rows: list[tuple[Any, ...]] | None = None
    error: str | None = None
    error_class: str = errors.NONE
    elapsed_s: float = 0.0

    def __post_init__(self) -> None:
        if self.error_class not in errors.ERROR_CLASSES:
            raise ValueError(f"unknown error_class {self.error_class!r}")

    @property
    def row_count(self) -> int:
        """Number of rows returned (0 when the statement produced none)."""
        return len(self.rows or ())

    @classmethod
    def success(
        cls,
        columns: list[str] | None = None,
        rows: list[tuple[Any, ...]] | None = None,
        elapsed_s: float = 0.0,
    ) -> "Result":
        """Build a successful result."""
        return cls(
            ok=True,
            columns=columns,
            rows=rows if rows is not None else [],
            error=None,
            error_class=errors.NONE,
            elapsed_s=elapsed_s,
        )

    @classmethod
    def failure(
        cls, error: str, error_class: str, elapsed_s: float = 0.0
    ) -> "Result":
        """Build a failed result."""
        return cls(
            ok=False,
            columns=None,
            rows=None,
            error=error,
            error_class=error_class,
            elapsed_s=elapsed_s,
        )


@dataclass
class TestCase:
    """SPEC "TestCase": setup statements plus the query under test."""

    #: pytest would otherwise try to collect this as a test class because of
    #: its name; it is a data carrier, not a test.
    __test__ = False

    setup: list[str] = field(default_factory=list)
    query: str = ""
    origin: str = "variant"
    seed: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class Engine(ABC):
    """Base class for every engine adapter.

    Subclasses must never raise on bad SQL or on a dead process; they return a
    :class:`Result` whose ``error_class`` says what happened.
    """

    #: engine name, matching the key in configs/targets.yaml
    name: str = ""
    #: "cli" or "python"
    kind: str = ""
    #: True when the protocol loses value types and every cell comes back as str
    text_cells: bool = False

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        #: how many times the engine process died under this adapter
        self.restarts = 0
        self._started = False

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Make the engine ready to accept statements."""
        self._started = True

    def close(self) -> None:
        """Release everything the engine holds. Safe to call twice."""
        self._started = False

    @abstractmethod
    def reset(self) -> None:
        """Throw the database away and start again from an empty one."""

    @property
    def started(self) -> bool:
        """True between :meth:`start` and :meth:`close`."""
        return self._started

    def __enter__(self) -> "Engine":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- identity ------------------------------------------------------------

    @abstractmethod
    def version(self) -> str:
        """Return the engine's version string, or UNKNOWN."""

    @abstractmethod
    def commit(self) -> str:
        """Return the source commit the engine *itself* reports, or UNKNOWN.

        Never the commit of a checkout in ~/targets unless the running binary
        reports it (CLAUDE.md hard rule 7). The commit recorded in
        configs/targets.yaml is exposed separately as
        :attr:`configured_commit`.
        """

    #: the pinned_commit from configs/targets.yaml, if the adapter was built
    #: from a Target. Informational only; it is not what commit() returns.
    configured_commit: str | None = None

    # --- execution -----------------------------------------------------------

    @abstractmethod
    def execute(self, sql: str, timeout: float | None = None) -> Result:
        """Run one statement and return its :class:`Result`. Never raises."""

    def run_case(
        self,
        setup: Sequence[str],
        query: str,
        timeout: float | None = None,
    ) -> Result:
        """Run ``setup`` then ``query`` and return the query's result.

        The default implementation resets, replays the setup, and stops with the
        first failing setup statement's result. CLI adapters override it to do
        the whole thing in a single process.
        """
        self.reset()
        for statement in setup:
            result = self.execute(statement, timeout=timeout)
            if not result.ok:
                return result
        return self.execute(query, timeout=timeout)


class CliEngine(Engine):
    """An engine driven as a subprocess, one process per statement.

    Subclasses provide :meth:`build_argv` and :meth:`parse`; this class owns the
    replay prefix, the output marker, timeouts, and crash accounting.
    """

    kind = "cli"

    def __init__(
        self,
        binary: str,
        name: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        mem_mb: int | None = None,
        configured_commit: str | None = None,
    ) -> None:
        """Set up the adapter.

        Args:
            binary: path to the engine executable (from configs/targets.yaml).
            name: engine name; defaults to the class attribute.
            timeout_s: default per-statement timeout.
            mem_mb: address-space cap passed to common.proc; None means the
                common.proc default.
            configured_commit: pinned_commit recorded in configs/targets.yaml.
        """
        super().__init__(timeout_s=timeout_s)
        if name:
            self.name = name
        self.binary = binary
        self.mem_mb = mem_mb
        self.configured_commit = configured_commit
        self._prefix: list[str] = []
        self._version: str | None = None
        self._commit: str | None = None
        #: pid of the child currently running, or None. Tests use it to kill
        #: an engine mid-query.
        self.current_pid: int | None = None

    # --- lifecycle -----------------------------------------------------------

    def reset(self) -> None:
        """Forget the replay prefix: the next statement sees an empty database."""
        self._prefix = []

    def close(self) -> None:
        super().close()
        self._prefix = []

    @property
    def prefix(self) -> list[str]:
        """The statements replayed before each new one."""
        return list(self._prefix)

    # --- subclass hooks ------------------------------------------------------

    @abstractmethod
    def build_argv(self, statements: Sequence[str], marker: str) -> list[str]:
        """Return the argv running ``statements`` with ``marker`` before the last.

        The marker must be printed by the engine on stdout as its own output so
        :meth:`parse` can split the final statement's output from the prefix's.
        """

    def build_stdin(self, statements: Sequence[str], marker: str) -> str | None:
        """Return the text piped to the child, or None when argv carries the SQL."""
        return None

    @abstractmethod
    def parse(self, proc: ProcResult, marker: str) -> Result:
        """Turn a finished process into the final statement's :class:`Result`."""

    # --- execution -----------------------------------------------------------

    def _marker(self) -> str:
        """A token no engine output can collide with by accident."""
        return f"__GRAFT_{uuid.uuid4().hex}__"

    def _spawn(self, statements: Sequence[str], timeout: float) -> tuple[ProcResult, str]:
        marker = self._marker()
        argv = self.build_argv(statements, marker)
        stdin = self.build_stdin(statements, marker)

        def remember(pid: int) -> None:
            self.current_pid = pid

        try:
            proc = run_with_limits(
                argv,
                input=stdin,
                timeout_s=timeout,
                mem_mb=self.mem_mb,
                on_spawn=remember,
            )
        finally:
            self.current_pid = None
        return proc, marker

    def _run(self, statements: Sequence[str], timeout: float | None) -> Result:
        timeout = self.timeout_s if timeout is None else timeout
        try:
            proc, marker = self._spawn(statements, timeout)
        except FileNotFoundError as exc:
            return Result.failure(
                f"engine binary not found: {exc}", errors.CRASH, elapsed_s=0.0
            )
        except OSError as exc:  # pragma: no cover - fork/exec failure
            return Result.failure(f"could not start engine: {exc}", errors.CRASH)

        if proc.timed_out:
            # The process group was killed, so the next call starts fresh.
            # Partial stdout is marker noise, not a message: only stderr adds
            # anything useful to the timeout itself.
            self.restarts += 1
            detail = proc.stderr.strip()
            return Result.failure(
                f"timed out after {timeout}s" + (f": {detail}" if detail else ""),
                errors.TIMEOUT,
                elapsed_s=proc.elapsed_s,
            )

        result = self.parse(proc, marker)
        if result.error_class in (errors.CRASH, errors.PANIC):
            # The process died; the next call simply starts a new one.
            self.restarts += 1
        return result

    def execute(self, sql: str, timeout: float | None = None) -> Result:
        """Run ``sql`` after the session's replay prefix. Never raises."""
        result = self._run([*self._prefix, sql], timeout)
        if result.ok and not is_read_only(sql):
            self._prefix.append(sql)
        return result

    def run_case(
        self,
        setup: Sequence[str],
        query: str,
        timeout: float | None = None,
    ) -> Result:
        """Run setup + query in one process and return the query's result."""
        self.reset()
        return self._run([*setup, query], timeout)

    # --- identity ------------------------------------------------------------

    def _probe(self, argv: Sequence[str], timeout: float = 20.0) -> str:
        """Run a short identity probe and return its stdout, or ''."""
        try:
            proc = run_with_limits(list(argv), timeout_s=timeout, mem_mb=self.mem_mb)
        except OSError:
            return ""
        return proc.stdout if proc.returncode == 0 else ""
