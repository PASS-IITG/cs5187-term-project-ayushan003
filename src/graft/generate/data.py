"""INSERT statements biased toward the values that actually break engines.

P07 asks for NULL around 20 percent, plus zero, minus one, boundary integers,
empty strings, duplicates and NaN where supported. The bias is the point: a
uniform random integer almost never finds a bug, while 0, -1, the int64
boundaries and NULL are where the mined corpus says the defects live.

``configs/capabilities.yaml`` decides which special values may be emitted at
all, because a value the engine does not preserve is not the test it looks
like: ``CAST('NaN' AS DOUBLE)`` is NaN on DuckDB, NULL on DataFusion and 0.0 on
SQLite and Turso.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Sequence

from graft.generate.schema import KIND_BOOL, KIND_INT, KIND_REAL, KIND_TEXT, Column, Schema, Table
from graft.generate.capabilities import EngineCapabilities

#: chance that any given cell is NULL
NULL_PROBABILITY = 0.2
#: integers worth trying, far more often than a random value
EDGE_INTEGERS = (0, 1, -1, 2, -2, 127, -128, 255, 32767, -32768, 2147483647,
                 -2147483648, 9223372036854775807, -9223372036854775808)

#: the inclusive range each declared integer type accepts. Emitting a value
#: outside it is a guaranteed error rather than a test, and DuckDB and
#: DataFusion both enforce these while SQLite ignores them.
INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "tinyint": (-128, 127),
    "smallint": (-32768, 32767),
    "integer": (-2147483648, 2147483647),
    "bigint": (-9223372036854775808, 9223372036854775807),
    "hugeint": (-9223372036854775808, 9223372036854775807),
    "utinyint": (0, 255),
    "usmallint": (0, 65535),
    "uinteger": (0, 4294967295),
    "ubigint": (0, 9223372036854775807),
}


def integers_for(label: str) -> tuple[int, ...]:
    """Return the edge integers that fit in ``label``'s declared range.

    The boundaries of the type itself are always included, since an off-by-one
    at a type boundary is exactly the integer_overflow bug class.
    """
    low, high = INTEGER_RANGES.get(label, (-9223372036854775808, 9223372036854775807))
    inside = tuple(v for v in EDGE_INTEGERS if low <= v <= high)
    return tuple(dict.fromkeys(inside + (low, high)))
#: strings worth trying
EDGE_STRINGS = ("", " ", "a", "A", "aa", "0", "1", "-1", "abc", "ABC", "é",
                "中文", "a'b", "x" * 100)
#: reals worth trying
EDGE_REALS = (0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 1e308, -1e308, 1e-308,
              9007199254740993.0)

#: the largest magnitude each declared real type accepts. DECIMAL(10,2) holds
#: at most 99999999.99, and FLOAT overflows past about 3.4e38. Emitting 1e308
#: into either is a guaranteed range error rather than a test - DuckDB says
#: "value can't be cast" and DataFusion reports it as an optimizer failure,
#: which looks far more alarming than it is.
REAL_LIMITS: dict[str, float] = {
    "decimal": 99999999.99,
    "real": 3.0e38,
    "float": 3.0e38,
    "double": 1e308,
}
#: values safe for a narrow or scaled type, including its own boundary
MODEST_REALS = (0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.5, -2.5, 1.25, 1e-3)


def reals_for(label: str) -> tuple[float, ...]:
    """Return the edge reals that fit in ``label``'s declared range."""
    limit = REAL_LIMITS.get(label, 1e308)
    inside = tuple(v for v in EDGE_REALS if abs(v) <= limit)
    extra = tuple(v for v in MODEST_REALS if abs(v) <= limit)
    return tuple(dict.fromkeys(inside + extra)) or (0.0,)

MIN_ROWS, MAX_ROWS = 0, 8


@dataclass(frozen=True)
class DataProfile:
    """How much data to generate, and how large the values may be.

    Why this exists. Turso is a storage engine: its hard parts are the B-tree,
    page splits, overflow pages and the WAL, not expression evaluation. Measured
    against the real thing:

    - ``PRAGMA page_size`` is 4096, and a cell spills to an OVERFLOW PAGE only
      past roughly a quarter of that. Our largest generated string was
      ``'x' * 100``; Turso's own fuzzer caps strings at 50 bytes
      (``sql_gen/src/policy.rs``: ``string_max_len: 50``). **Neither fuzzer has
      ever created an overflow page.**
    - A table of at most 8 rows of at most 100 bytes is a few hundred bytes:
      one page, never split. Turso's own fuzzer uses ``min_rows: 1,
      max_rows: 5``. **Neither fuzzer has ever split a B-tree page.**

    So both fuzzers have been testing the expression evaluator and calling it
    database testing. ``DEEP`` exists to reach the structures underneath, where
    oracles/index_invariance.py becomes a test of index maintenance across a
    multi-page B-tree rather than within a single page.
    """

    name: str = "default"
    min_rows: int = MIN_ROWS
    max_rows: int = MAX_ROWS
    #: chance a TEXT value is large enough to need an overflow page
    overflow_probability: float = 0.0
    #: size range for those large values, in bytes
    overflow_min: int = 1200
    overflow_max: int = 20000


#: the one profile the tool uses. A "deep" profile that generated 300-900 rows
#: and values past the 4096-byte overflow threshold was measured and removed:
#: it reached 767-page multi-level B-trees where this one reaches 10 pages, and
#: it found nothing in 14,939 cases. docs/BENCHMARK.md keeps the measurement.
DEFAULT_PROFILE = DataProfile()

PROFILES = {"default": DEFAULT_PROFILE}


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def random_value(
    column: Column,
    rng: random.Random,
    capabilities: EngineCapabilities,
    profile: DataProfile = DEFAULT_PROFILE,
) -> str:
    """Return a SQL literal for ``column``, biased to edge values."""
    if column.nullable and rng.random() < NULL_PROBABILITY:
        return "NULL"
    if column.kind == KIND_INT:
        return str(rng.choice(integers_for(column.label)))
    if column.kind == KIND_REAL:
        # The special float values, but only where the engine preserves them
        # AND the column can hold them: DECIMAL is exact and rejects NaN.
        specials = (
            [
                label
                for label in ("nan", "infinity", "neg_infinity")
                if capabilities.value_expression(label)
            ]
            if column.label in ("double", "real", "float")
            else []
        )
        if specials and rng.random() < 0.15:
            expression = capabilities.value_expression(rng.choice(specials))
            assert expression is not None
            return expression
        return repr(rng.choice(reals_for(column.label)))
    if column.kind == KIND_TEXT:
        if profile.overflow_probability and rng.random() < profile.overflow_probability:
            # Past the overflow threshold on a 4096-byte page, so the cell is
            # stored on overflow pages rather than inline. A repeated unit
            # keeps the value cheap to generate and easy to eyeball in a
            # reduced repro.
            size = rng.randint(profile.overflow_min, profile.overflow_max)
            unit = rng.choice(("x", "ab", "é", "中"))
            return _quote(unit * (size // len(unit.encode("utf-8")) + 1))
        return _quote(rng.choice(EDGE_STRINGS))
    if column.kind == KIND_BOOL:
        return rng.choice(("TRUE", "FALSE"))
    # an OTHER-kind column: only NULL is safe without inventing a format
    return "NULL"


def insert_statements(
    schema: Schema,
    rng: random.Random,
    capabilities: EngineCapabilities,
    rows: int | None = None,
    profile: DataProfile = DEFAULT_PROFILE,
) -> list[str]:
    """Return one INSERT per table, with duplicate rows deliberately likely.

    A table may get zero rows: empty input is where aggregate and optimizer
    bugs concentrate, and SPEC's own NoREC correction exists because of it.
    """
    statements: list[str] = []
    for table in schema.tables:
        count = (
            rows
            if rows is not None
            else rng.randint(profile.min_rows, profile.max_rows)
        )
        if count == 0:
            continue
        unique = unique_columns(table)
        # Values already used in a column that must stay unique. Measured: with
        # UNIQUE and PRIMARY KEY columns being generated and this bookkeeping
        # absent, 47 percent of cases died on "UNIQUE constraint failed" before
        # the query ever ran, because the duplicate bias below collides with
        # the constraint by construction.
        used: dict[str, set[str]] = {name: set() for name in unique}
        built: list[str] = []
        for _ in range(count):
            # repeat the previous row sometimes, so duplicates and the
            # DISTINCT/GROUP BY paths get exercised - but never when a column
            # of this table has to stay unique.
            if built and not unique and rng.random() < 0.25:
                built.append(built[-1])
                continue
            cells: list[str] = []
            for column in table.columns:
                value = random_value(column, rng, capabilities, profile)
                if column.name in unique:
                    value = _distinct_value(
                        column, value, used[column.name], rng, capabilities, profile
                    )
                    if value is None:
                        break
                    used[column.name].add(value)
                cells.append(value)
            if len(cells) != len(table.columns):
                # A unique column ran out of distinct values; stop adding rows
                # rather than emit an INSERT that is certain to fail.
                break
            built.append("(" + ", ".join(cells) + ")")
        if not built:
            continue
        statements.append(
            f"INSERT INTO {table.name} VALUES " + ", ".join(built)
        )
    return statements


def unique_columns(table: Any) -> frozenset[str]:
    """Return the names of columns this table requires to be unique.

    Both sources count: a column-level PRIMARY KEY or UNIQUE, and a
    single-column UNIQUE INDEX. A multi-column unique index is not included -
    only the tuple has to be distinct, and the per-column bookkeeping here
    would refuse legal rows.
    """
    names = {
        column.name
        for column in table.columns
        if column.constraint in ("primary_key", "unique")
    }
    for index in getattr(table, "indexes", ()):
        if index.unique and len(index.keys) == 1 and index.keys[0].isidentifier():
            names.add(index.keys[0])
    return frozenset(names)


#: how many times to redraw before giving up on a distinct value
_DISTINCT_ATTEMPTS = 12


def _distinct_value(
    column: Any,
    first: str,
    used: set[str],
    rng: random.Random,
    capabilities: EngineCapabilities,
    profile: DataProfile = DEFAULT_PROFILE,
) -> str | None:
    """Return a value not already in ``used``, or None if none could be found.

    NULL is handled deliberately: SQLite, Turso and DuckDB all allow repeated
    NULLs in a UNIQUE column, so NULL is only barred when the column is NOT
    NULL - in which case it would fail for that reason instead.
    """
    if first == "NULL":
        return None if not column.nullable else first
    if first not in used:
        return first
    for _ in range(_DISTINCT_ATTEMPTS):
        candidate = random_value(column, rng, capabilities, profile)
        if candidate == "NULL":
            continue
        if candidate not in used:
            return candidate
    # The curated edge-value sets are small on purpose, so exhausting them is
    # expected. Fall back to a counter for INT and a suffix for TEXT, which
    # keeps the row legal without abandoning the case.
    if column.kind == KIND_INT:
        for offset in range(len(used) + 1, len(used) + _DISTINCT_ATTEMPTS + 2):
            candidate = str(offset)
            if candidate not in used:
                return candidate
    elif column.kind == KIND_TEXT:
        for offset in range(len(used) + 1, len(used) + _DISTINCT_ATTEMPTS + 2):
            candidate = _quote(f"u{offset}")
            if candidate not in used:
                return candidate
    return None


#: how often a table gets a mutation after its rows are inserted
MUTATION_PROBABILITY = 0.45


def mutation_statements(
    schema: Schema,
    rng: random.Random,
    capabilities: EngineCapabilities,
) -> list[str]:
    """Return UPDATE and DELETE statements to run after the INSERTs.

    Why these exist. An index is maintained incrementally: every UPDATE and
    DELETE has to keep it in step with the table. An index left stale by a
    mutation is a wrong answer that no amount of query generation over static
    data can reach, and docs/FUZZER_AUDIT.md ranked exactly this - "build
    index, UPDATE, re-query" - as unreachable because the generator emitted
    neither indexes nor UPDATE.

    Paired with oracles/index_invariance.py this becomes a real test: run the
    query over a schema that was indexed while being mutated, and again over
    the same statements without the index, and the answers must agree.

    A column that must stay unique is never the target of an UPDATE. Setting it
    to a constant would collide as soon as two rows matched the WHERE, and the
    statement would fail for that reason rather than testing anything.
    """
    statements: list[str] = []
    for table in schema.tables:
        if rng.random() >= MUTATION_PROBABILITY:
            continue
        unique = unique_columns(table)
        if rng.random() < 0.5:
            targets = [
                column
                for column in table.columns
                if column.name not in unique
                and column.kind in (KIND_INT, KIND_REAL, KIND_TEXT)
            ]
            if not targets:
                continue
            column = rng.choice(targets)
            value = random_value(column, rng, capabilities)
            statements.append(
                f"UPDATE {table.name} SET {column.name} = {value}"
                f" WHERE {_mutation_predicate(table, rng, capabilities)}"
            )
        else:
            statements.append(
                f"DELETE FROM {table.name}"
                f" WHERE {_mutation_predicate(table, rng, capabilities)}"
            )
    return statements


def _mutation_predicate(
    table: Table,
    rng: random.Random,
    capabilities: EngineCapabilities,
) -> str:
    """Return a simple, deterministic WHERE for a mutation.

    Deliberately simple: the point is to move rows around under an index, not
    to test expression evaluation, and a predicate that errors would abort the
    setup and waste the case.
    """
    usable = [
        column
        for column in table.columns
        if column.kind in (KIND_INT, KIND_REAL, KIND_TEXT)
    ]
    if not usable:
        return "TRUE"
    column = rng.choice(usable)
    form = rng.random()
    if form < 0.25:
        return f"{column.name} IS NULL"
    if form < 0.4:
        return f"{column.name} IS NOT NULL"
    value = random_value(column, rng, capabilities)
    if value == "NULL":
        return f"{column.name} IS NULL"
    operator = rng.choice(("=", "<>", "<", ">", "<=", ">="))
    return f"{column.name} {operator} {value}"
