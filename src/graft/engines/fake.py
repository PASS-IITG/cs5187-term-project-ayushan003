"""A scripted in-memory engine, for testing everything that consumes adapters.

It never touches a database. Responses are looked up by exact SQL text, which
keeps oracle and runner tests deterministic and fast, and it can be told to
fail in each of the SPEC error classes so crash handling can be exercised
without killing a real engine.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from graft.engines import errors
from graft.engines.base import UNKNOWN, Engine, Result, canonical_row


class FakeEngine(Engine):
    """An engine whose answers are scripted up front."""

    name = "fake"
    kind = "python"
    text_cells = False

    def __init__(
        self,
        responses: Mapping[str, Result] | None = None,
        default: Result | None = None,
        version: str = "fake-1.0",
        commit: str = UNKNOWN,
        timeout_s: float = 10.0,
    ) -> None:
        """Create the engine.

        Args:
            responses: exact-SQL-text to Result mapping.
            default: returned for SQL not in ``responses``; defaults to an
                empty successful result.
            version: what :meth:`version` returns.
            commit: what :meth:`commit` returns.
            timeout_s: default timeout, unused but part of the interface.
        """
        super().__init__(timeout_s=timeout_s)
        self.responses: dict[str, Result] = dict(responses or {})
        self.default = default if default is not None else Result.success(rows=[])
        self._version = version
        self._commit = commit
        #: every statement this engine was asked to run, in order
        self.executed: list[str] = []
        self.resets = 0

    # --- lifecycle -----------------------------------------------------------

    def reset(self) -> None:
        """Forget the executed statements; counts as a fresh empty database."""
        self.executed = []
        self.resets += 1

    # --- identity ------------------------------------------------------------

    def version(self) -> str:
        """Return the scripted version string."""
        return self._version

    def commit(self) -> str:
        """Return the scripted commit."""
        return self._commit

    # --- execution -----------------------------------------------------------

    def execute(self, sql: str, timeout: float | None = None) -> Result:
        """Return the scripted result for ``sql``. Never raises."""
        self.executed.append(sql)
        result = self.responses.get(sql.strip(), self.default)
        if result.error_class in (errors.CRASH, errors.PANIC):
            self.restarts += 1
        return result

    # --- helpers for tests ---------------------------------------------------

    def script(self, sql: str, result: Result) -> "FakeEngine":
        """Add one scripted response and return self, so calls can chain."""
        self.responses[sql.strip()] = result
        return self

    @staticmethod
    def rows(*values: Any) -> list[tuple[Any, ...]]:
        """Build canonical rows from plain tuples."""
        return [canonical_row(v if isinstance(v, tuple) else (v,)) for v in values]


#: the planted bugs P06 task 7 asks for, as switchable names
BUG_DROP_NULL_ROW = "drop_null_row_in_filter"
BUG_WRONG_COUNT = "wrong_count"
BUG_CRASH_ON_FUNCTION = "crash_on_function"
BUG_BREAK_TLP = "break_tlp_recombination"

PLANTED_BUGS = (
    BUG_DROP_NULL_ROW,
    BUG_WRONG_COUNT,
    BUG_CRASH_ON_FUNCTION,
    BUG_BREAK_TLP,
)


class PlantedBugEngine(Engine):
    """A real engine with a deliberate defect, for proving the oracles work.

    The point of this class is to answer a question the campaign cannot answer
    on its own: when an oracle reports nothing, is that because the engine is
    correct or because the oracle is broken? A run over thousands of cases that
    finds nothing is worthless evidence unless the same oracles are known to
    catch a bug when one is present.

    So rather than simulate SQL, it executes it for real through the stdlib
    sqlite3 adapter and then corrupts one specific behaviour:

    ``drop_null_row_in_filter``
        silently drops result rows containing NULL when the query has a WHERE,
        which is the shape of several mined null_handling bugs.
    ``wrong_count``
        returns one less than the true COUNT.
    ``crash_on_function``
        reports an internal error whenever a named function appears.
    ``break_tlp_recombination``
        drops a row from a UNION ALL, so only the recombined side of a TLP
        check is wrong - the subtlest of the four, since the original query
        still answers correctly.

    With ``bugs`` empty it is simply SQLite, which is what the zero-false-
    positive half of the acceptance needs.
    """

    name = "planted"
    kind = "python"
    text_cells = False

    def __init__(
        self,
        bugs: Iterable[str] = (),
        crash_function: str = "abs",
        timeout_s: float = 10.0,
    ) -> None:
        """Create the engine.

        Args:
            bugs: which planted bugs to switch on; empty means a correct engine.
            crash_function: the function name ``crash_on_function`` reacts to.
            timeout_s: per-statement timeout, passed through.
        """
        super().__init__(timeout_s=timeout_s)
        unknown = sorted(set(bugs) - set(PLANTED_BUGS))
        if unknown:
            raise ValueError(f"unknown planted bug(s): {', '.join(unknown)}")
        self.bugs = frozenset(bugs)
        self.crash_function = crash_function
        self._inner: Any = None

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Open the underlying SQLite database."""
        if self._inner is None:
            from graft.engines.sqlite import SqliteEngine

            self._inner = SqliteEngine(timeout_s=self.timeout_s)
            self._inner.start()
        super().start()

    def close(self) -> None:
        """Close the underlying database."""
        if self._inner is not None:
            self._inner.close()
            self._inner = None
        super().close()

    def reset(self) -> None:
        """Throw the database away and start again."""
        if self._inner is not None:
            self._inner.reset()

    def version(self) -> str:
        """Return a version naming the active bugs, so a finding records them."""
        active = ",".join(sorted(self.bugs)) or "none"
        return f"planted-1.0 (bugs: {active})"

    def commit(self) -> str:
        """Return a stable fake commit."""
        return "planted0000"

    # --- execution -----------------------------------------------------------

    def execute(self, sql: str, timeout: float | None = None) -> Result:
        """Run ``sql`` for real, then apply whichever defect is switched on."""
        if BUG_CRASH_ON_FUNCTION in self.bugs and re.search(
            rf"\b{re.escape(self.crash_function)}\s*\(", sql, re.IGNORECASE
        ):
            return Result.failure(
                f"INTERNAL Error: planted crash in {self.crash_function}()",
                errors.INTERNAL_ERROR,
            )

        result = self._connection().execute(sql, timeout=timeout)
        if not result.ok or result.rows is None:
            return result

        rows = list(result.rows)

        if BUG_DROP_NULL_ROW in self.bugs and re.search(
            r"\bWHERE\b", sql, re.IGNORECASE
        ):
            rows = [row for row in rows if not any(cell is None for cell in row)]

        if BUG_WRONG_COUNT in self.bugs and re.search(
            r"\bcount\s*\(", sql, re.IGNORECASE
        ):
            rows = [
                tuple(
                    (cell - 1 if isinstance(cell, int) and not isinstance(cell, bool) else cell)
                    for cell in row
                )
                for row in rows
            ]

        if BUG_BREAK_TLP in self.bugs and re.search(
            r"\bUNION\s+ALL\b", sql, re.IGNORECASE
        ):
            rows = rows[:-1] if rows else rows

        if rows == list(result.rows):
            return result
        return Result.success(
            columns=result.columns, rows=rows, elapsed_s=result.elapsed_s
        )

    def _connection(self) -> Any:
        if self._inner is None:
            self.start()
        return self._inner
