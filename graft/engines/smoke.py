"""``make smoke``: run a fixed set of statements against every adapter.

Prints two tables: what each engine says it is, and what each engine does with
four statements - one valid, one that divides by zero, one that reads a table
that does not exist, and one that is not SQL at all. Nothing here may raise: a
dead or missing engine has to show up as a ``crash`` row.
"""

from __future__ import annotations

import sys
from typing import Iterable, Sequence

from graft.engines import create_engine, errors
from graft.engines.base import UNKNOWN, Engine, Result

#: (engine name, kind) pairs to probe, in report order
ENGINES: tuple[tuple[str, str], ...] = (
    ("sqlite", "python"),
    ("turso", "cli"),
)

#: the statements every engine is asked to run
STATEMENTS: tuple[str, ...] = (
    "SELECT 1",
    "SELECT 1/0",
    "SELECT * FROM missing_table",
    "SELCT ((( not sql",
)


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """Render a fixed-width text table."""
    body = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in body)) if body else len(headers[i])
        for i in range(len(headers))
    ]
    line = "-+-".join("-" * w for w in widths)
    out = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), line]
    out.extend(" | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) for row in body)
    return "\n".join(out)


def _outcome(result: Result) -> str:
    """One-line summary of a Result for the table."""
    if result.ok:
        rows = result.rows or []
        preview = ", ".join(repr(r) for r in rows[:2])
        return f"ok rows={len(rows)} {preview}"[:52]
    first = (result.error or "").strip().splitlines()
    return (first[0] if first else "(no message)")[:52]


def run(engines: Sequence[tuple[str, str]] = ENGINES) -> int:
    """Probe every engine and print the tables.

    Returns:
        0 if every engine started and no statement crashed or panicked,
        1 otherwise - a missing binary or a dead engine must fail the target.
    """
    identity: list[list[str]] = []
    outcomes: list[list[str]] = []
    failures = 0

    for name, kind in engines:
        label = f"{name} ({kind})"
        engine: Engine | None = None
        try:
            engine = create_engine(name, kind)
            identity.append(
                [
                    label,
                    engine.version(),
                    engine.commit(),
                    engine.configured_commit or "-",
                    "text" if engine.text_cells else "typed",
                ]
            )
        except Exception as exc:  # a missing binary must be reported, not raised
            failures += 1
            identity.append([label, "UNAVAILABLE", UNKNOWN, "-", "-"])
            outcomes.append([label, "(startup)", "crash", f"{type(exc).__name__}: {exc}"[:52]])
            continue

        try:
            for sql in STATEMENTS:
                engine.reset()
                result = engine.execute(sql)
                if result.error_class in (errors.CRASH, errors.PANIC):
                    failures += 1
                outcomes.append([label, sql, result.error_class, _outcome(result)])
        finally:
            engine.close()

    print("Engines")
    print(_table(["engine", "version", "commit", "configured", "cells"], identity))
    print()
    print("Statements")
    print(_table(["engine", "sql", "class", "result"], outcomes))
    print()
    print(f"engines probed: {len(engines)}, failures: {failures}")
    return 1 if failures else 0


def main() -> int:
    """Entry point for ``python -m adapters.smoke``."""
    return run()


if __name__ == "__main__":
    sys.exit(main())
