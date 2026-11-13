"""Random schemas, limited to what the target engine actually supports.

Types come from ``configs/capabilities.yaml``, which is probed from the real
binaries, so the generator never declares a type the engine would resolve to
something else. That matters most for SQLite and Turso, where only four types
are distinct and every other name is an affinity alias: declaring ``DATE``
there really stores an integer, which would make a "date column" a fiction.

Everything is driven by a seeded :class:`random.Random`, so a seed reproduces a
schema exactly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from graft.generate.capabilities import EngineCapabilities

#: how the generator thinks about types, independent of dialect spelling
KIND_INT = "int"
KIND_REAL = "real"
KIND_TEXT = "text"
KIND_BOOL = "bool"
KIND_OTHER = "other"

#: capability type label -> generator kind
TYPE_KINDS: dict[str, str] = {
    "tinyint": KIND_INT, "smallint": KIND_INT, "integer": KIND_INT,
    "bigint": KIND_INT, "hugeint": KIND_INT, "utinyint": KIND_INT,
    "usmallint": KIND_INT, "uinteger": KIND_INT, "ubigint": KIND_INT,
    "decimal": KIND_REAL, "real": KIND_REAL, "float": KIND_REAL,
    "double": KIND_REAL,
    "varchar": KIND_TEXT, "text": KIND_TEXT,
    "boolean": KIND_BOOL,
    "blob": KIND_OTHER, "date": KIND_OTHER, "time": KIND_OTHER,
    "timestamp": KIND_OTHER, "interval": KIND_OTHER, "uuid": KIND_OTHER,
    "json": KIND_OTHER,
}

#: kinds the generator builds expressions over; OTHER is declared but only
#: ever selected or compared for NULL, never arithmetic
GENERATED_KINDS = (KIND_INT, KIND_REAL, KIND_TEXT, KIND_BOOL)

MIN_TABLES, MAX_TABLES = 1, 4
MIN_COLUMNS, MAX_COLUMNS = 1, 6


@dataclass(frozen=True)
class Column:
    """One generated column."""

    name: str
    label: str
    declared: str
    kind: str
    nullable: bool = True
    #: "primary_key", "unique" or None. A column-level constraint, which is
    #: also an index on most engines - that is the point of generating them.
    constraint: str | None = None

    def definition(self) -> str:
        """Return the column's DDL fragment."""
        text = f"{self.name} {self.declared}"
        if not self.nullable:
            text += " NOT NULL"
        if self.constraint == "primary_key":
            text += " PRIMARY KEY"
        elif self.constraint == "unique":
            text += " UNIQUE"
        return text


@dataclass(frozen=True)
class Index:
    """One generated index.

    Indexes are the reason this module grew: NoREC compares an optimisable
    query against a form the optimiser cannot help, and that comparison is
    only meaningful when an alternative access path exists. Without an index
    both sides run the same sequential scan.
    """

    name: str
    table: str
    #: index key expressions, already rendered (a column name, or an
    #: expression for an expression index)
    keys: tuple[str, ...]
    unique: bool = False
    #: a WHERE clause for a partial index, or None
    predicate: str | None = None

    def create(self) -> str:
        """Return the CREATE INDEX statement."""
        kind = "CREATE UNIQUE INDEX" if self.unique else "CREATE INDEX"
        text = f"{kind} {self.name} ON {self.table}({', '.join(self.keys)})"
        if self.predicate:
            text += f" WHERE {self.predicate}"
        return text

    def feature_tags(self) -> tuple[str, ...]:
        """Return SPEC tags describing this index."""
        tags = ["ddl:index"]
        if self.unique:
            tags.append("ddl:unique_index")
        if len(self.keys) > 1:
            tags.append("ddl:multi_column_index")
        if self.predicate:
            tags.append("ddl:partial_index")
        if any(not key.isidentifier() for key in self.keys):
            tags.append("ddl:expression_index")
        return tuple(tags)


@dataclass(frozen=True)
class Table:
    """One generated table."""

    name: str
    columns: tuple[Column, ...]
    indexes: tuple[Index, ...] = ()

    def create(self) -> str:
        """Return the CREATE TABLE statement."""
        body = ", ".join(column.definition() for column in self.columns)
        return f"CREATE TABLE {self.name}({body})"

    def columns_of(self, *kinds: str) -> list[Column]:
        """Return the columns whose kind is one of ``kinds``."""
        return [column for column in self.columns if column.kind in kinds]


@dataclass(frozen=True)
class Schema:
    """A generated schema and the statements that build it."""

    tables: tuple[Table, ...]

    def ddl(self) -> list[str]:
        """Return the CREATE TABLE statements, then the CREATE INDEX ones.

        Indexes come last so every table exists before anything indexes it.
        """
        statements = [table.create() for table in self.tables]
        for table in self.tables:
            statements.extend(index.create() for index in table.indexes)
        return statements

    def feature_tags(self) -> tuple[str, ...]:
        """Return SPEC tags for the declared columns, constraints and indexes."""
        tags = {f"type:{c.label}" for t in self.tables for c in t.columns}
        for table in self.tables:
            for column in table.columns:
                if column.constraint == "primary_key":
                    tags.add("ddl:primary_key")
                elif column.constraint == "unique":
                    tags.add("ddl:unique_constraint")
            for index in table.indexes:
                tags.update(index.feature_tags())
        return tuple(sorted(tags))


def usable_kinds(capabilities: EngineCapabilities) -> dict[str, list[str]]:
    """Return generator kind -> usable capability labels for this engine."""
    out: dict[str, list[str]] = {}
    for label in capabilities.usable_types():
        kind = TYPE_KINDS.get(label)
        if kind is None:
            continue
        out.setdefault(kind, []).append(label)
    return out


def random_schema(
    rng: random.Random,
    capabilities: EngineCapabilities,
    tables: int | None = None,
    prefix: str = "t",
) -> Schema:
    """Generate a schema of 1 to 4 tables with 1 to 6 columns each.

    Every column gets a type the engine reports distinctly, and each table is
    guaranteed at least one integer-ish column so that joins and aggregates
    have something to work with.
    """
    kinds = usable_kinds(capabilities)
    if not kinds:
        raise ValueError(f"{capabilities.name} has no usable types to generate from")
    preferred = [k for k in GENERATED_KINDS if k in kinds] or list(kinds)

    count = tables if tables is not None else rng.randint(MIN_TABLES, MAX_TABLES)
    built: list[Table] = []
    for index in range(count):
        width = rng.randint(MIN_COLUMNS, MAX_COLUMNS)
        columns: list[Column] = []
        for position in range(width):
            # the first column is always numeric where possible, so joins and
            # aggregates always have a usable key
            if position == 0 and KIND_INT in kinds:
                kind = KIND_INT
            else:
                kind = rng.choice(preferred)
            label = rng.choice(kinds[kind])
            declared = capabilities.declared_type(label) or "INTEGER"
            columns.append(
                Column(
                    name=f"c{position}",
                    label=label,
                    declared=declared,
                    kind=kind,
                    nullable=rng.random() > 0.2,
                )
            )
        table_name = f"{prefix}{index}"
        columns = _with_constraints(columns, rng, capabilities)
        built.append(
            Table(
                name=table_name,
                columns=tuple(columns),
                indexes=random_indexes(table_name, columns, rng, capabilities),
            )
        )
    return Schema(tables=tuple(built))


#: how often a table gets a column-level PRIMARY KEY or UNIQUE
CONSTRAINT_PROBABILITY = 0.35
#: how often a table gets at least one explicit index
INDEX_PROBABILITY = 0.6


def _with_constraints(
    columns: list[Column],
    rng: random.Random,
    capabilities: EngineCapabilities,
) -> list[Column]:
    """Give at most one column a PRIMARY KEY or UNIQUE constraint.

    At most one, because a second PRIMARY KEY is a syntax error and the point
    here is a valid alternative access path, not a stress test of the parser.
    A constraint is an implicit index on SQLite, Turso and DuckDB, so this is
    the cheapest way to give the optimiser a choice of plan.
    """
    if rng.random() >= CONSTRAINT_PROBABILITY:
        return columns
    choices: list[str] = []
    if capabilities.has_feature("ddl:primary_key"):
        choices.append("primary_key")
    if capabilities.has_feature("ddl:unique_constraint"):
        choices.append("unique")
    if not choices:
        return columns
    # Only an INT or TEXT column: a REAL or BOOL key is legal but a poor
    # index, and a NULL in a PRIMARY KEY column is rejected by some engines,
    # which would make the whole INSERT fail and waste the case.
    indexable = [
        position
        for position, column in enumerate(columns)
        if column.kind in (KIND_INT, KIND_TEXT)
    ]
    if not indexable:
        return columns
    position = rng.choice(indexable)
    constraint = rng.choice(choices)
    out = list(columns)
    chosen = out[position]
    # A PRIMARY KEY column must not be nullable, or the biased NULL-heavy data
    # generator will fail every insert against it.
    out[position] = Column(
        name=chosen.name,
        label=chosen.label,
        declared=chosen.declared,
        kind=chosen.kind,
        nullable=chosen.nullable and constraint != "primary_key",
        constraint=constraint,
    )
    return out


def random_indexes(
    table: str,
    columns: Sequence[Column],
    rng: random.Random,
    capabilities: EngineCapabilities,
) -> tuple[Index, ...]:
    """Generate 0 to 2 indexes for one table, gated on probed capabilities.

    Every form is gated separately because the engines genuinely differ:
    measured, DataFusion supports no index at all, DuckDB supports every form
    except a partial index, SQLite and Turso support all of them.
    """
    if not capabilities.has_feature("ddl:index"):
        return ()
    if rng.random() >= INDEX_PROBABILITY:
        return ()
    indexable = [c for c in columns if c.kind in (KIND_INT, KIND_TEXT, KIND_REAL)]
    if not indexable:
        return ()

    built: list[Index] = []
    for serial in range(rng.randint(1, 2)):
        name = f"ix_{table}_{serial}"
        keys: tuple[str, ...]
        column = rng.choice(indexable)
        form = rng.random()
        if (
            form < 0.2
            and len(indexable) > 1
            and capabilities.has_feature("ddl:multi_column_index")
        ):
            pair = rng.sample(indexable, 2)
            keys = tuple(c.name for c in pair)
        elif (
            form < 0.35
            and column.kind == KIND_INT
            and capabilities.has_feature("ddl:expression_index")
        ):
            keys = (f"abs({column.name})",)
        else:
            keys = (column.name,)
        predicate = None
        if (
            rng.random() < 0.2
            and capabilities.has_feature("ddl:partial_index")
            and column.kind == KIND_INT
        ):
            # A partial index is the sharpest NoREC probe there is: the
            # optimiser may only use it for a query whose predicate implies
            # this one, so getting that implication wrong is a wrong answer.
            predicate = f"{column.name} > 0"
        unique = (
            rng.random() < 0.25
            and capabilities.has_feature("ddl:unique_index")
            and predicate is None
            # Single key only. A unique multi-column index needs the TUPLE to
            # be distinct, which the per-column bookkeeping in gen/data.py
            # cannot guarantee, and it was the whole remaining cause of
            # "UNIQUE constraint failed" setup deaths.
            and len(keys) == 1
            and keys[0].isidentifier()
        )
        built.append(
            Index(
                name=name,
                table=table,
                keys=keys,
                unique=unique,
                predicate=predicate,
            )
        )
    # Two indexes may collide on the same single key, which is legal but
    # pointless; drop the duplicate rather than emit it.
    seen: set[tuple[str, ...]] = set()
    unique_indexes: list[Index] = []
    for index in built:
        signature = index.keys + (index.predicate or "",)
        if signature in seen:
            continue
        seen.add(signature)
        unique_indexes.append(index)
    return tuple(unique_indexes)


#: how often a table gets an ALTER TABLE ADD COLUMN after its rows exist
ALTER_PROBABILITY = 0.3


def alter_statements(
    schema: Schema,
    rng: random.Random,
    capabilities: EngineCapabilities,
) -> tuple[list[str], Schema]:
    """Return ``ALTER TABLE ... ADD COLUMN`` statements and the schema they make.

    Why only ADD COLUMN. DROP COLUMN and RENAME COLUMN are both supported by
    Turso (measured), but a query generated against the original schema would
    then reference a column that no longer exists, so the case would die in
    setup rather than test anything. Adding a column never invalidates an
    existing query, and the updated schema is returned so the generator can
    reference the new column - otherwise the ALTER would be executed but never
    actually exercised by a query.

    Why this is worth generating at all. Four of Turso's last 120 core commits
    touch ALTER, including ``05e6a1495`` "validate existing rows when ALTER
    TABLE adds a virtual generated column to a STRICT table" - and the
    generator emitted no ALTER at all. The interesting part is that the rows
    already EXIST when the column is added, so the engine has to either
    back-fill or compute them, and a generated column on an already-indexed
    table has to stay consistent with the index.

    Every form is gated on a probed capability, because the engines differ.
    """
    statements: list[str] = []
    tables: list[Table] = []
    for table in schema.tables:
        if not capabilities.has_feature("ddl:add_column") or (
            rng.random() >= ALTER_PROBABILITY
        ):
            tables.append(table)
            continue
        added: list[Column] = []
        for serial in range(rng.randint(1, 2)):
            built = _added_column(table, added, serial, rng, capabilities)
            if built is None:
                continue
            statement, column = built
            statements.append(statement)
            added.append(column)
        tables.append(
            Table(
                name=table.name,
                columns=table.columns + tuple(added),
                indexes=table.indexes,
            )
        )
    return statements, Schema(tables=tuple(tables))


def _added_column(
    table: Table,
    already: Sequence[Column],
    serial: int,
    rng: random.Random,
    capabilities: EngineCapabilities,
) -> tuple[str, Column] | None:
    """Build one ADD COLUMN statement and the Column it introduces."""
    name = f"z{serial}"
    if any(c.name == name for c in table.columns) or any(
        c.name == name for c in already
    ):
        return None

    numeric = [c for c in table.columns if c.kind == KIND_INT]
    forms: list[str] = ["plain"]
    if capabilities.has_feature("ddl:add_column_default"):
        forms.append("default")
    if capabilities.has_feature("ddl:add_column_not_null"):
        forms.append("not_null")
    if numeric and capabilities.has_feature("ddl:add_generated_column"):
        # The live one: the rows already exist, so the engine must compute this
        # for every one of them.
        forms.append("generated")
    form = rng.choice(forms)

    if form == "generated":
        source = rng.choice(numeric)
        expression = rng.choice(
            (
                f"{source.name} + 1",
                f"{source.name} * 2",
                f"abs({source.name})",
                f"{source.name} IS NULL",
            )
        )
        return (
            f"ALTER TABLE {table.name} ADD COLUMN {name} AS ({expression})",
            Column(
                name=name,
                label="integer",
                declared="INTEGER",
                kind=KIND_INT,
                nullable=True,
            ),
        )

    declared = capabilities.declared_type("integer") or "INTEGER"
    if form == "default":
        return (
            f"ALTER TABLE {table.name} ADD COLUMN {name} {declared} DEFAULT 7",
            Column(name=name, label="integer", declared=declared, kind=KIND_INT),
        )
    if form == "not_null":
        # A DEFAULT is required: the existing rows need a value, and without
        # one the statement is rejected rather than interesting.
        return (
            f"ALTER TABLE {table.name} ADD COLUMN {name} {declared} "
            f"NOT NULL DEFAULT 7",
            Column(
                name=name,
                label="integer",
                declared=declared,
                kind=KIND_INT,
                nullable=False,
            ),
        )
    return (
        f"ALTER TABLE {table.name} ADD COLUMN {name} {declared}",
        Column(name=name, label="integer", declared=declared, kind=KIND_INT),
    )
