"""SQLite adapter, built on the Python standard library ``sqlite3``.

SQLite is the project's *reference* engine (SPEC: diff_sqlite compares Turso
against it). There is no sqlite3 CLI on this host, so this adapter is
``kind: python`` and talks to the stdlib module in-process against a fresh
``:memory:`` database.

Cell types (see adapters.base for the canonical mapping): sqlite3 hands back
``None``, ``int``, ``float``, ``str`` and ``bytes`` directly, which are already
canonical, so no conversion is lossy here. SQLite has no boolean type; a
boolean column comes back as ``int`` 0/1, which is what the engine really
stores and what the normalizer must account for.

Timeouts use ``sqlite3.Connection.set_progress_handler``: the handler aborts the
statement once the deadline passes, so a runaway query raises
``OperationalError("interrupted")`` instead of hanging. There is no separate
process, so a true crash (segfault) would take this process down with it; that
cannot be caught and is documented rather than handled.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from graft.engines import errors
from graft.engines.base import UNKNOWN, Engine, Result, canonical_row

#: how many VM instructions between progress-handler calls
_PROGRESS_INTERVAL = 1000


class SqliteEngine(Engine):
    """In-process SQLite reference engine."""

    name = "sqlite"
    kind = "python"
    text_cells = False

    def __init__(self, timeout_s: float = 10.0, configured_commit: str | None = None):
        """Create the adapter. The connection opens on :meth:`start`."""
        super().__init__(timeout_s=timeout_s)
        self.configured_commit = configured_commit
        self._conn: sqlite3.Connection | None = None

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Open a fresh in-memory database."""
        if self._conn is None:
            self._conn = sqlite3.connect(":memory:", isolation_level=None)
            self._conn.execute("PRAGMA foreign_keys = ON")
        super().start()

    def close(self) -> None:
        """Close the connection. Safe to call twice."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - close rarely fails
                pass
            self._conn = None
        super().close()

    def reset(self) -> None:
        """Throw the database away and open an empty one."""
        self.close()
        self.start()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.start()
        assert self._conn is not None
        return self._conn

    # --- identity ------------------------------------------------------------

    def version(self) -> str:
        """Return the stdlib's SQLite library version, e.g. ``3.46.1``."""
        return sqlite3.sqlite_version or UNKNOWN

    def commit(self) -> str:
        """Return SQLite's own ``sqlite_source_id()``, or UNKNOWN."""
        try:
            row = self._connection().execute("SELECT sqlite_source_id()").fetchone()
        except sqlite3.Error:  # pragma: no cover - ancient builds only
            return UNKNOWN
        if not row or not row[0]:
            return UNKNOWN
        # "<date> <time> <sha>" - the sha is the part that identifies the build.
        return str(row[0]).split()[-1]

    # --- execution -----------------------------------------------------------

    def execute(self, sql: str, timeout: float | None = None) -> Result:
        """Run one statement. Never raises, whatever the SQL says."""
        limit = self.timeout_s if timeout is None else timeout
        conn = self._connection()
        deadline = time.monotonic() + limit
        timed_out = False

        def watchdog() -> int:
            nonlocal timed_out
            if time.monotonic() >= deadline:
                timed_out = True
                return 1  # non-zero aborts the statement
            return 0

        started = time.monotonic()
        conn.set_progress_handler(watchdog, _PROGRESS_INTERVAL)
        try:
            cursor = conn.execute(sql)
            columns = (
                [d[0] for d in cursor.description] if cursor.description else None
            )
            rows = (
                [canonical_row(r) for r in cursor.fetchall()]
                if cursor.description
                else []
            )
            cursor.close()
            return Result.success(
                columns=columns, rows=rows, elapsed_s=time.monotonic() - started
            )
        except Exception as exc:  # sqlite3 raises many types; none may escape
            elapsed = time.monotonic() - started
            message = f"{type(exc).__name__}: {exc}"
            if timed_out:
                return Result.failure(message, errors.TIMEOUT, elapsed_s=elapsed)
            return Result.failure(
                message,
                errors.classify(message, returncode=0, engine=self.name, failed=True),
                elapsed_s=elapsed,
            )
        finally:
            conn.set_progress_handler(None, 0)
