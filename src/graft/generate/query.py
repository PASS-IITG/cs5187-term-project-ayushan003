"""The SELECT generator: joins, grouping, aggregates, subqueries, CTEs, windows.

Every element is gated on ``configs/capabilities.yaml``, which is probed from
the real binaries, so the generator never emits a clause the target cannot
parse. That is the difference between a 70 percent validity rate and a 20
percent one: SQLite and Turso have no ROLLBACK/CUBE/GROUPING SETS, no QUALIFY,
no DISTINCT ON, no derived-table column alias list and no LATERAL, while
DataFusion has no LATERAL - all measured, none assumed.

Every generated query carries the SPEC feature tags for what it used, built up
as it is constructed rather than re-derived by matching text afterwards.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from graft.generate.expr import Expr, ExprContext, build, predicate
from graft.generate.schema import (
    KIND_BOOL,
    KIND_INT,
    KIND_REAL,
    KIND_TEXT,
    Column,
    Schema,
    Table,
)
from graft.generate.capabilities import EngineCapabilities


@dataclass(frozen=True)
class GeneratedQuery:
    """A generated query, its tags, and the predicate TLP may partition on."""

    sql: str
    tags: tuple[str, ...]
    #: a boolean expression over the query's own columns, for TLP
    tlp_predicate: str | None = None
    #: True when the query is a plain single-table scan, which NoREC needs
    single_table: bool = True


def _scope(table: Table, alias: str | None = None) -> tuple[tuple[str, Column], ...]:
    prefix = alias or table.name
    return tuple((f"{prefix}.{column.name}", column) for column in table.columns)


def _aggregate_tags(function: str) -> tuple[str, ...]:
    return (f"agg:{function}",)


class QueryGenerator:
    """Builds SELECT statements over a generated schema."""

    def __init__(
        self,
        schema: Schema,
        capabilities: EngineCapabilities,
        rng: random.Random,
        max_depth: int = 3,
    ) -> None:
        """Set the generator up for one schema and engine."""
        self.schema = schema
        self.capabilities = capabilities
        self.rng = rng
        self.max_depth = max_depth

    # --- helpers ------------------------------------------------------------

    def _context(self, columns: Sequence[tuple[str, Column]]) -> ExprContext:
        return ExprContext(
            rng=self.rng,
            capabilities=self.capabilities,
            columns=tuple(columns),
            max_depth=self.max_depth,
        )

    def _has(self, feature: str) -> bool:
        return self.capabilities.has_feature(feature)

    def _table(self) -> Table:
        return self.rng.choice(self.schema.tables)

    # --- pieces -------------------------------------------------------------

    def _indexed_columns(self) -> dict[str, set[str]]:
        """Return table name -> the column names an index can seek on.

        Only the FIRST key of each index: a composite index can only be sought
        on a prefix, and a predicate on its second column alone does not give
        the optimiser a seek. Expression keys like ``abs(c0)`` are excluded
        because a predicate on the bare column cannot use them.
        """
        out: dict[str, set[str]] = {}
        for table in self.schema.tables:
            names: set[str] = {
                column.name
                for column in table.columns
                if column.constraint in ("primary_key", "unique")
            }
            for index in getattr(table, "indexes", ()):
                if index.keys and index.keys[0].isidentifier():
                    names.add(index.keys[0])
            if names:
                out[table.name] = names
        return out

    def _sargable_term(
        self, scope: Sequence[tuple[str, Column]]
    ) -> tuple[str, tuple[str, ...]] | None:
        """Return a predicate the optimiser can answer with an index SEEK.

        Why this exists, measured rather than assumed. Generating indexes was
        not enough: over 200 NoREC-eligible queries on indexed schemas, only
        1.0 percent produced a SEARCH (an index seek), while 63.5 percent were
        plain table scans and 35.5 percent read the index as a covering SCAN.
        A covering scan reads every row anyway, so the optimised and
        unoptimised forms cannot disagree - which is why NoREC still found
        nothing after the indexes landed.

        A seek needs a SARGABLE comparison - an indexed column against a
        constant, not wrapped in a function - so that the optimiser has to
        decide which rows the index lets it skip. Getting that decision wrong
        IS the optimiser bug NoREC is built to catch.
        """
        indexed = self._indexed_columns()
        if not indexed:
            return None
        candidates = [
            (qualified, column)
            for qualified, column in scope
            # qualified is "t0.c1"; match the table it came from
            if qualified.split(".")[0] in indexed
            and column.name in indexed[qualified.split(".")[0]]
            and column.kind in (KIND_INT, KIND_REAL, KIND_TEXT)
        ]
        if not candidates:
            return None
        qualified, column = self.rng.choice(candidates)
        literal = build(column.kind, self._context(()), self.max_depth)
        operator = self.rng.choice(("=", "<", "<=", ">", ">=", "<>"))
        form = self.rng.random()
        if form < 0.2:
            high = build(column.kind, self._context(()), self.max_depth)
            return (
                f"{qualified} BETWEEN {literal.sql} AND {high.sql}",
                ("op:between",) + literal.tags,
            )
        if form < 0.35:
            other = build(column.kind, self._context(()), self.max_depth)
            return (
                f"{qualified} IN ({literal.sql}, {other.sql})",
                ("op:in",) + literal.tags,
            )
        tag = {
            "=": "op:eq", "<": "op:lt", "<=": "op:lte",
            ">": "op:gt", ">=": "op:gte", "<>": "op:ne",
        }[operator]
        return f"{qualified} {operator} {literal.sql}", (tag,) + literal.tags

    def _from_clause(self) -> tuple[str, tuple[tuple[str, Column], ...], tuple[str, ...], bool]:
        """Return (FROM text, columns in scope, tags, is_single_table)."""
        left = self._table()
        if len(self.schema.tables) < 2 or self.rng.random() < 0.45:
            return left.name, _scope(left), (), True

        right = self.rng.choice([t for t in self.schema.tables if t.name != left.name])
        kinds: list[tuple[str, str]] = [
            ("JOIN", "join:inner"),
            ("LEFT JOIN", "join:left"),
            ("CROSS JOIN", "join:cross"),
        ]
        if self._has("join:right"):
            kinds.append(("RIGHT JOIN", "join:right"))
        if self._has("join:full_outer"):
            kinds.append(("FULL OUTER JOIN", "join:full"))
        keyword, tag = self.rng.choice(kinds)
        scope = _scope(left) + _scope(right)

        if keyword == "CROSS JOIN":
            return f"{left.name} CROSS JOIN {right.name}", scope, (tag,), False

        left_key = left.columns[0]
        right_key = right.columns[0]
        if left_key.kind != right_key.kind:
            # join on something well-typed, or fall back to a cross join
            return f"{left.name} CROSS JOIN {right.name}", scope, ("join:cross",), False
        condition = f"{left.name}.{left_key.name} = {right.name}.{right_key.name}"
        return (
            f"{left.name} {keyword} {right.name} ON {condition}",
            scope,
            (tag, "clause:on", "op:eq"),
            False,
        )

    def _select_list(
        self,
        scope: Sequence[tuple[str, Column]],
        aggregated: bool,
        recombinable: bool = False,
        joined: bool = False,
    ) -> tuple[str, tuple[str, ...]]:
        if recombinable:
            return self._recombinable_select_list(scope, aggregated, joined)
        if aggregated:
            return self._aggregate_list(scope)
        if self.rng.random() < 0.2 and not joined:
            return "*", ()
        count = self.rng.randint(1, min(3, max(1, len(scope))))
        items: list[str] = []
        tags: tuple[str, ...] = ()
        for index in range(count):
            kind = self.rng.choice((KIND_INT, KIND_REAL, KIND_TEXT, KIND_BOOL))
            expression = build(kind, self._context(scope))
            items.append(f"{expression.sql} AS a{index}")
            tags += expression.tags
        return ", ".join(items), tags

    def _recombinable_select_list(
        self,
        scope: Sequence[tuple[str, Column]],
        aggregated: bool,
        joined: bool = False,
    ) -> tuple[str, tuple[str, ...]]:
        """A select list oracles/shape.py accepts: columns, or one aggregate.

        ``*`` is avoided over a join. Two generated tables share column names,
        so ``SELECT *`` over a join produces duplicate output names, which
        DataFusion's nd-json cannot be read back from unambiguously - the
        adapter now refuses such a result rather than guessing, so asking for
        it would just throw coverage away. Explicit aliases keep the shape
        partitionable and the answer readable.
        """
        if not aggregated:
            if not joined:
                return "*", ()
            items = ", ".join(
                f"{qualified} AS a{index}"
                for index, (qualified, _) in enumerate(scope)
            )
            return items, ()
        numeric = [p for p in scope if p[1].kind in (KIND_INT, KIND_REAL)]
        if not numeric:
            return "count(*)", ("agg:count",)
        function = self.rng.choice(
            [
                name
                for name in ("count", "sum", "min", "max")
                if name == "count" or self.capabilities.has_function(f"agg:{name}")
            ]
        )
        if function == "count":
            return "count(*)", ("agg:count",)
        qualified, _ = self.rng.choice(numeric)
        return f"{function}({qualified})", (f"agg:{function}",)

    def _recombinable_grouped_select_list(
        self, scope: Sequence[tuple[str, Column]]
    ) -> tuple[str, tuple[str, ...], str] | None:
        """Return (select list, tags, group key) for a partitionable grouped
        aggregate, or None if this scope cannot produce one.

        The shape is exactly what ``oracles/shape.py`` accepts: one bare group
        key, then one aggregate over one bare column or ``*``, no HAVING and no
        ORDER BY. AVG is excluded because a mean of means is not the mean.
        """
        keys = [p for p in scope if p[1].kind in (KIND_INT, KIND_TEXT)]
        if not keys:
            return None
        key, _ = self.rng.choice(keys)
        numeric = [p for p in scope if p[1].kind in (KIND_INT, KIND_REAL)]
        functions = ["count"] + [
            name
            for name in ("sum", "min", "max")
            if numeric and self.capabilities.has_function(f"agg:{name}")
        ]
        function = self.rng.choice(functions)
        if function == "count":
            return f"{key}, count(*)", ("agg:count", "clause:group_by"), key
        qualified, _ = self.rng.choice(numeric)
        return (
            f"{key}, {function}({qualified})",
            (f"agg:{function}", "clause:group_by"),
            key,
        )

    def _aggregate_list(
        self, scope: Sequence[tuple[str, Column]]
    ) -> tuple[str, tuple[str, ...]]:
        numeric = [p for p in scope if p[1].kind in (KIND_INT, KIND_REAL)]
        options: list[tuple[str, str]] = [("count(*)", "count")]
        if numeric:
            qualified, _ = self.rng.choice(numeric)
            for function in ("sum", "avg", "min", "max"):
                if self.capabilities.has_function(f"agg:{function}"):
                    options.append((f"{function}({qualified})", function))
        text = ", ".join(
            f"{sql} AS a{index}"
            for index, (sql, _) in enumerate(
                [self.rng.choice(options) for _ in range(self.rng.randint(1, 2))]
            )
        )
        used = tuple(
            tag
            for sql, name in options
            if sql in text
            for tag in _aggregate_tags(name)
        )
        return text, used

    def _window_item(
        self, scope: Sequence[tuple[str, Column]]
    ) -> tuple[str, tuple[str, ...]] | None:
        if not self._has("win:over"):
            return None
        functions = [
            name
            for name in ("row_number", "rank", "dense_rank", "lead", "lag")
            if self.capabilities.has_function(f"win:{name}")
        ]
        if not functions:
            return None
        name = self.rng.choice(functions)
        numeric = [p for p in scope if p[1].kind in (KIND_INT, KIND_REAL)]
        if name in ("lead", "lag"):
            if not numeric:
                return None
            argument = self.rng.choice(numeric)[0]
            call = f"{name}({argument})"
        else:
            call = f"{name}()"
        order_by = self.rng.choice(scope)[0]
        frame = ""
        tags = (f"win:{name}", "clause:order_by")
        if self._has("clause:window_frame_rows") and self.rng.random() < 0.5:
            frame = " ROWS BETWEEN 1 PRECEDING AND CURRENT ROW"
            tags += ("clause:window_frame_rows",)
        return f"{call} OVER (ORDER BY {order_by}{frame})", tags

    def _affinity_subquery(
        self,
        scope: Sequence[tuple[str, Column]],
        other: Table,
        style: str,
    ) -> tuple[str, tuple[str, ...]] | None:
        """Compare a column against a subquery of a DIFFERENT declared affinity.

        Returns None when this scope and table cannot supply two columns of
        different kinds, which is common for a one-column table.
        """
        pairs = [
            (qualified, column)
            for qualified, column in scope
            if column.kind in (KIND_INT, KIND_REAL, KIND_TEXT)
        ]
        if not pairs:
            return None
        qualified, column = self.rng.choice(pairs)
        # The subquery column must be of a DIFFERENT kind: a TEXT column
        # against an INTEGER subquery, or the reverse.
        wanted = (
            (KIND_TEXT,)
            if column.kind in (KIND_INT, KIND_REAL)
            else (KIND_INT, KIND_REAL)
        )
        candidates = [c for c in other.columns if c.kind in wanted]
        if not candidates:
            return None
        key = self.rng.choice(candidates).name
        tags = ("clause:subquery", f"type:{column.label}")
        if style == "affinity_in" and self._has("op:in_subquery"):
            return (
                f"{qualified} IN (SELECT {key} FROM {other.name})",
                ("op:in",) + tags,
            )
        if style == "affinity_eq" and self._has("clause:subquery"):
            operator = self.rng.choice(("=", "<>", "<", ">"))
            tag = {"=": "op:eq", "<>": "op:ne", "<": "op:lt", ">": "op:gt"}[
                operator
            ]
            # A scalar subquery, so max() keeps it to one row whatever the data.
            return (
                f"{qualified} {operator} (SELECT max({key}) FROM {other.name})",
                (tag, "agg:max") + tags,
            )
        return None

    def _subquery(self, scope: Sequence[tuple[str, Column]]) -> tuple[str, tuple[str, ...]] | None:
        other = self._table()
        numeric = [c for c in other.columns if c.kind in (KIND_INT, KIND_REAL)]
        style = self.rng.choice(
            ("exists", "in", "scalar", "affinity_in", "affinity_eq")
        )
        # Affinity-mismatched subquery comparisons. docs/TURSO_ATTACK_SURFACE.md
        # section 3.1: four of the last 120 core commits fix scalar-subquery
        # AFFINITY in one narrow area - keeping it through semi-join rewrites
        # (2f35b07c3), in table references (de1c01494), in IN (SELECT ...)
        # (8f3ee1740), and skipping hash join keys whose subquery reads another
        # table (f075768d9). Four fixes in one window means the invariant is
        # subtle and the area is live.
        #
        # These produce WRONG RESULTS rather than crashes, and unlike the crash
        # space SQLite is a legitimate oracle here, because affinity conversion
        # is SQLite-defined behaviour that Turso is trying to match - so a
        # disagreement is a real compatibility bug, not dialect noise.
        #
        # The existing "in" style compares a numeric column against a numeric
        # subquery column, which is the case where affinity does NOT have to
        # convert anything. These two deliberately mismatch the declared
        # affinity of the two sides, which is what forces the conversion.
        if style in ("affinity_in", "affinity_eq"):
            built = self._affinity_subquery(scope, other, style)
            if built is not None:
                return built
            style = "exists"
        if style == "exists" and self._has("op:exists"):
            return (
                f"EXISTS (SELECT 1 FROM {other.name})",
                ("op:exists", "clause:subquery"),
            )
        if style == "in" and numeric and self._has("op:in_subquery"):
            key = self.rng.choice(numeric).name
            column = next((p for p in scope if p[1].kind in (KIND_INT, KIND_REAL)), None)
            if column is None:
                return None
            return (
                f"{column[0]} IN (SELECT {key} FROM {other.name})",
                ("op:in", "clause:subquery"),
            )
        if numeric and self._has("clause:subquery"):
            key = self.rng.choice(numeric).name
            return (
                f"(SELECT max({key}) FROM {other.name}) IS NOT NULL",
                ("clause:subquery", "agg:max", "op:is_not_null"),
            )
        return None

    # --- the query ----------------------------------------------------------

    def generate(self, recombinable: bool = False) -> GeneratedQuery:
        """Return one generated SELECT with its tags.

        Args:
            recombinable: keep the query inside the family
                ``oracles/shape.py`` can partition - no GROUP BY, no ORDER BY,
                no LIMIT, no window, no set operation, and a select list that
                is either plain columns or a single aggregate. The first
                campaign measured only 31 percent of generated cases reaching
                TLP or NoREC at all, because the richer shapes are refused;
                this is how that share is raised without weakening the gate.
        """
        from_clause, scope, from_tags, single = self._from_clause()
        tags: tuple[str, ...] = ("clause:select",) + from_tags

        aggregated = self.rng.random() < 0.3
        # A recombinable case may now be GROUPED: oracles/shape.py learned the
        # `SELECT k, agg(x) ... GROUP BY k` shape, and the OOPSLA Query
        # Partitioning result is that the more complex partitioning queries are
        # the ones that expose bugs - so a grouped partition is worth more than
        # a flat one, not less.
        recombinable_grouped = (
            recombinable and aggregated and self.rng.random() < 0.5
        )
        grouped = aggregated and (
            recombinable_grouped or (not recombinable and self.rng.random() < 0.5)
        )
        if recombinable_grouped:
            built = self._recombinable_grouped_select_list(scope)
            if built is None:
                recombinable_grouped = False
                grouped = False
            else:
                select_list, select_tags, group_key = built
                tags += select_tags
        if not recombinable_grouped:
            group_key = None
            select_list, select_tags = self._select_list(
                scope, aggregated, recombinable, joined=not single
            )
            tags += select_tags

        if not aggregated and not recombinable and self.rng.random() < 0.25:
            window = self._window_item(scope)
            if window is not None:
                select_list = f"{select_list}, {window[0]} AS w0"
                tags += window[1]

        if (
            self.rng.random() < 0.2
            and not aggregated
            and self._has("clause:distinct")
        ):
            select_list = f"DISTINCT {select_list}"
            tags += ("clause:distinct",)

        parts = [f"SELECT {select_list} FROM {from_clause}"]

        where: Expr | None = None
        # Bias the WHERE toward a term the optimiser can SEEK on. Without this
        # only 1.0 percent of NoREC-eligible queries produced an index seek,
        # so NoREC was comparing two forms that both read every row.
        seekable = (
            self._sargable_term(scope) if self.rng.random() < 0.55 else None
        )
        if self.rng.random() < 0.7 or seekable is not None:
            where = predicate(self._context(scope))
            clause = where.sql
            if seekable is not None:
                clause = f"({seekable[0]}) AND ({clause})"
                tags += seekable[1]
            if not recombinable and self.rng.random() < 0.2:
                sub = self._subquery(scope)
                if sub is not None:
                    clause = f"({clause}) AND ({sub[0]})"
                    tags += sub[1]
            parts.append(f"WHERE {clause}")
            tags += where.tags + ("clause:where",)

        group_column: str | None = None
        if recombinable_grouped and group_key:
            # The select list already starts with the key, and no HAVING is
            # added: HAVING decides which groups survive and is not
            # distributive over a partition, so oracles/shape.py refuses it.
            group_column = group_key
            parts.append(f"GROUP BY {group_key}")
        elif grouped and scope:
            group_column = self.rng.choice(scope)[0]
            parts[0] = f"SELECT {group_column}, {select_list} FROM {from_clause}"
            parts.append(f"GROUP BY {group_column}")
            tags += ("clause:group_by",)
            if self.rng.random() < 0.4 and self._has("clause:having"):
                parts.append("HAVING count(*) > 0")
                tags += ("clause:having", "agg:count")

        core = " ".join(parts)
        is_star_single = select_list == "*" and single

        # Wrap BEFORE adding ORDER BY or LIMIT. Appending them first put the
        # LIMIT on the left arm of a set operation, which Turso rejects with
        # "LIMIT clause should come after UNION not before".
        if (
            not recombinable
            and self.rng.random() < 0.15
            and self._has("cte:non_recursive")
        ):
            core = f"WITH g AS ({core}) SELECT * FROM g"
            tags += ("cte:non_recursive",)
            single = False
        elif (
            is_star_single
            and not recombinable
            and self.rng.random() < 0.15
            and self._has("set:union_all")
        ):
            # Both arms must have the same column count. Using the same table
            # on both sides guarantees that; building the right arm from
            # another table produced "UNION queries have different number of
            # columns" on DataFusion and the equivalent on Turso.
            operator = self.rng.choice(("UNION ALL", "UNION"))
            table_name = from_clause.split()[0]
            core = f"{core} {operator} SELECT * FROM {table_name}"
            tags += ("set:union_all" if operator == "UNION ALL" else "set:union",)
            single = False

        trailing: list[str] = []
        if not recombinable and self.rng.random() < 0.4:
            # ordering by position keeps it valid whatever the select list is
            direction = self.rng.choice(("", " DESC"))
            nulls = ""
            if self._has("clause:nulls_first") and self.rng.random() < 0.5:
                nulls = self.rng.choice((" NULLS FIRST", " NULLS LAST"))
                tags += ("clause:nulls_first",)
            trailing.append(f"ORDER BY 1{direction}{nulls}")
            tags += ("clause:order_by",)
        if (
            not recombinable
            and self.rng.random() < 0.25
            and self._has("clause:limit_offset")
        ):
            trailing.append(f"LIMIT {self.rng.randint(0, 5)}")
            tags += ("clause:limit",)

        sql = " ".join([core, *trailing])

        tlp = None
        if scope and self.rng.random() < 0.8:
            tlp = predicate(self._context(scope)).sql

        return GeneratedQuery(
            sql=sql,
            tags=tuple(dict.fromkeys(tags)),
            tlp_predicate=tlp,
            single_table=single,
        )


def generate_case(
    rng: random.Random,
    capabilities: EngineCapabilities,
    recombinable: bool = False,
    profile: Any = None,
) -> tuple[list[str], GeneratedQuery, Schema]:
    """Generate a full case: schema DDL, data, and one query.

    Args:
        recombinable: ask for a shape TLP and NoREC can check.
    """
    from graft.generate.data import insert_statements, mutation_statements
    from graft.generate.schema import random_schema

    schema = random_schema(rng, capabilities)
    # Order matters: tables, then indexes, then rows, then mutations. The
    # indexes exist while the rows are inserted AND while they are updated or
    # deleted, so index maintenance is exercised rather than bypassed.
    from graft.generate.data import DEFAULT_PROFILE
    from graft.generate.schema import alter_statements

    chosen = profile or DEFAULT_PROFILE
    # Order matters, and each part reads a DIFFERENT schema on purpose:
    #   - ddl() and the INSERTs use the ORIGINAL columns, because a positional
    #     INSERT ... VALUES must match the table as created;
    #   - the ALTERs then add columns, so the rows already EXIST when a
    #     generated column is computed, which is the interesting case;
    #   - the mutations use the original schema too, since a generated column
    #     cannot be the target of an UPDATE;
    #   - only the QUERY sees the widened schema, so the added columns are
    #     actually exercised rather than merely created.
    base_ddl = schema.ddl()
    inserts = insert_statements(schema, rng, capabilities, profile=chosen)
    alters, widened = alter_statements(schema, rng, capabilities)
    mutations = mutation_statements(schema, rng, capabilities)
    setup = base_ddl + inserts + alters + mutations
    query = QueryGenerator(widened, capabilities, rng).generate(recombinable)
    return setup, query, widened
