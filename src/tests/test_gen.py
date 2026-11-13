"""Tests for the generator: determinism, the tag vocabulary, and validity."""

from __future__ import annotations

import random
import re

import pytest

from graft.generate import data as D
from graft.generate import expr as E
from graft.generate import query as Q
from graft.generate import schema as S
from graft.corpus import templates as T
from graft.generate.capabilities import get_capabilities

#: the SPEC tag vocabulary
TAG_RE = re.compile(
    r"^(fn|op|clause|type|agg|join|cte|set|win|ddl):[a-z0-9_]+$"
)


@pytest.fixture(scope="module")
def turso_caps():
    return get_capabilities("turso")


@pytest.fixture(scope="module")
def sqlite_caps():
    return get_capabilities("sqlite")


@pytest.fixture(scope="module")
def datafusion_caps():
    """DataFusion supports no CREATE INDEX form; measured, not assumed."""
    return get_capabilities("datafusion")


# --- schema ---------------------------------------------------------------


def test_schema_sizes_are_within_the_prompt_limits(turso_caps):
    for seed in range(20):
        schema = S.random_schema(random.Random(seed), turso_caps)
        assert S.MIN_TABLES <= len(schema.tables) <= S.MAX_TABLES
        for table in schema.tables:
            assert S.MIN_COLUMNS <= len(table.columns) <= S.MAX_COLUMNS


def test_a_schema_only_declares_types_the_engine_distinguishes(sqlite_caps):
    """SQLite resolves any other name to an affinity, so a DATE column there
    would really store an integer."""
    usable = set(sqlite_caps.usable_types())
    for seed in range(20):
        schema = S.random_schema(random.Random(seed), sqlite_caps)
        for table in schema.tables:
            for column in table.columns:
                assert column.label in usable


def test_every_table_has_a_numeric_first_column(turso_caps):
    for seed in range(10):
        schema = S.random_schema(random.Random(seed), turso_caps)
        for table in schema.tables:
            assert table.columns[0].kind == S.KIND_INT


def test_schema_generation_is_deterministic(turso_caps):
    first = S.random_schema(random.Random(5), turso_caps)
    second = S.random_schema(random.Random(5), turso_caps)
    assert first.ddl() == second.ddl()


def test_schema_feature_tags_are_in_the_vocabulary(turso_caps):
    schema = S.random_schema(random.Random(1), turso_caps)
    for tag in schema.feature_tags():
        assert TAG_RE.match(tag), tag


# --- data -----------------------------------------------------------------


@pytest.mark.parametrize(
    "label,low,high",
    [
        ("tinyint", -128, 127),
        ("utinyint", 0, 255),
        ("integer", -2147483648, 2147483647),
        ("ubigint", 0, 9223372036854775807),
    ],
)
def test_integer_values_respect_the_declared_range(label, low, high):
    """Out-of-range values are a guaranteed error, not a test."""
    for value in D.integers_for(label):
        assert low <= value <= high
    assert low in D.integers_for(label)
    assert high in D.integers_for(label)


def test_real_values_respect_the_declared_limit():
    """DECIMAL(10,2) holds at most 99999999.99; 1e308 is a range error."""
    for value in D.reals_for("decimal"):
        assert abs(value) <= D.REAL_LIMITS["decimal"]
    assert 1e308 in D.reals_for("double")
    assert 1e308 not in D.reals_for("decimal")


def test_nan_is_never_put_in_a_decimal_column(turso_caps):
    """DECIMAL is exact and rejects NaN, however well the engine preserves it."""
    column = S.Column(name="c", label="decimal", declared="DECIMAL(10,2)", kind=S.KIND_REAL)
    rng = random.Random(0)
    for _ in range(200):
        assert "NaN" not in D.random_value(column, rng, turso_caps)


def test_nan_is_not_offered_where_it_is_not_preserved(sqlite_caps):
    """SQLite turns CAST('NaN' AS DOUBLE) into 0.0, so it is not that test."""
    column = S.Column(name="c", label="real", declared="REAL", kind=S.KIND_REAL)
    rng = random.Random(0)
    values = {D.random_value(column, rng, sqlite_caps) for _ in range(300)}
    assert not any("NaN" in value for value in values)


def test_nulls_appear_about_a_fifth_of_the_time(turso_caps):
    column = S.Column(name="c", label="integer", declared="INTEGER", kind=S.KIND_INT)
    rng = random.Random(3)
    values = [D.random_value(column, rng, turso_caps) for _ in range(2000)]
    share = values.count("NULL") / len(values)
    assert 0.12 < share < 0.30, share


def test_a_not_null_column_never_gets_null(turso_caps):
    column = S.Column(
        name="c", label="integer", declared="INTEGER", kind=S.KIND_INT, nullable=False
    )
    rng = random.Random(3)
    assert all(
        D.random_value(column, rng, turso_caps) != "NULL" for _ in range(300)
    )


def test_data_generation_is_deterministic(turso_caps):
    def once():
        rng = random.Random(11)
        schema = S.random_schema(rng, turso_caps)
        return D.insert_statements(schema, rng, turso_caps)

    assert once() == once()


# --- expressions ----------------------------------------------------------


def _context(caps, seed=0):
    schema = S.random_schema(random.Random(seed), caps, tables=1)
    table = schema.tables[0]
    columns = tuple((f"{table.name}.{c.name}", c) for c in table.columns)
    return E.ExprContext(random.Random(seed), caps, columns)


@pytest.mark.parametrize("kind", [S.KIND_INT, S.KIND_REAL, S.KIND_TEXT, S.KIND_BOOL])
def test_an_expression_has_the_requested_kind(turso_caps, kind):
    ctx = _context(turso_caps)
    for _ in range(40):
        built = E.build(kind, ctx)
        assert built.kind == kind
        assert built.sql.strip()


def test_expressions_respect_the_depth_limit(turso_caps):
    ctx = _context(turso_caps)
    ctx.max_depth = 1
    for _ in range(50):
        built = E.build(S.KIND_BOOL, ctx)
        # depth 1 means at most one level of nesting, so no deep parens nest
        assert built.sql.count("(") <= 6


def test_expression_tags_are_in_the_vocabulary(turso_caps):
    ctx = _context(turso_caps)
    for _ in range(100):
        for tag in E.build(S.KIND_BOOL, ctx).tags:
            assert TAG_RE.match(tag), tag


def test_expressions_never_call_a_function_the_engine_lacks(sqlite_caps):
    """SQLite lacks 62 of the 123 probed functions."""
    ctx = _context(sqlite_caps)
    missing = ("strpos", "lpad", "rpad", "left(", "right(", "repeat", "reverse")
    for _ in range(300):
        sql = E.build(random.choice(
            [S.KIND_INT, S.KIND_TEXT, S.KIND_BOOL]
        ), ctx).sql
        for name in missing:
            assert name not in sql, sql


def test_expression_generation_is_deterministic(turso_caps):
    first = [E.build(S.KIND_BOOL, _context(turso_caps, 9)).sql for _ in range(5)]
    second = [E.build(S.KIND_BOOL, _context(turso_caps, 9)).sql for _ in range(5)]
    assert first == second


# --- queries --------------------------------------------------------------


def test_query_tags_are_in_the_vocabulary(turso_caps):
    for seed in range(30):
        rng = random.Random(seed)
        _, query, _ = Q.generate_case(rng, turso_caps)
        assert query.tags
        for tag in query.tags:
            assert TAG_RE.match(tag), tag


def test_queries_never_use_a_clause_the_engine_lacks(sqlite_caps):
    """SQLite and Turso have no LATERAL, QUALIFY, ROLLUP, CUBE, GROUPING SETS,
    DISTINCT ON, or derived-table column alias list."""
    forbidden = ("LATERAL", "QUALIFY", "ROLLUP", "CUBE", "GROUPING SETS", "DISTINCT ON")
    for seed in range(60):
        rng = random.Random(seed)
        _, query, _ = Q.generate_case(rng, sqlite_caps)
        upper = query.sql.upper()
        for word in forbidden:
            assert word not in upper, (word, query.sql)


def test_query_generation_is_deterministic(turso_caps):
    """P07 acceptance: the same seed gives identical output."""
    def once():
        rng = random.Random(4242)
        setup, query, _ = Q.generate_case(rng, turso_caps)
        return setup, query.sql, query.tags, query.tlp_predicate

    assert once() == once()


def test_a_full_run_is_deterministic_across_many_seeds(turso_caps):
    def run():
        out = []
        for index in range(25):
            rng = random.Random(index)
            setup, query, _ = Q.generate_case(rng, turso_caps)
            out.append((tuple(setup), query.sql))
        return out

    assert run() == run()


def test_the_tlp_predicate_is_a_boolean_expression(turso_caps):
    found = 0
    for seed in range(40):
        rng = random.Random(seed)
        _, query, _ = Q.generate_case(rng, turso_caps)
        if query.tlp_predicate:
            found += 1
            assert query.tlp_predicate.strip()
    assert found > 10, "almost no TLP predicates were produced"


# --- templates ------------------------------------------------------------


def test_templatize_replaces_literals_and_nothing_else():
    query, holes = T.templatize("SELECT a FROM t WHERE a > 5 AND b = 'x'")
    assert "{n" in query and "{s" in query
    assert "SELECT a FROM t WHERE a >" in query
    assert [kind for _, kind in holes] == ["int", "text"]


def test_a_number_inside_a_string_is_not_templatized():
    query, holes = T.templatize("SELECT 'a1b' FROM t")
    assert [kind for _, kind in holes] == ["text"]
    assert query.count("{") == 1


def test_reals_and_integers_are_distinguished():
    _, holes = T.templatize("SELECT 1.5, 2")
    kinds = [kind for _, kind in holes]
    assert "real" in kinds and "int" in kinds


def test_instantiate_fills_every_hole():
    template = T.Template(
        setup=("CREATE TABLE t(a INTEGER)",),
        query="SELECT a FROM t WHERE a > {n0} AND a < {n1}",
        holes=(("n0", "int"), ("n1", "int")),
        source=T.SOURCE_REPRO,
    )
    setup, filled = T.instantiate(template, random.Random(1))
    assert setup == ["CREATE TABLE t(a INTEGER)"]
    assert "{" not in filled
    assert "SELECT a FROM t WHERE a >" in filled


def test_instantiation_is_deterministic():
    template = T.Template(
        setup=(), query="SELECT {n0}, {s1}", holes=(("n0", "int"), ("s1", "text")),
        source=T.SOURCE_REPRO,
    )
    assert T.instantiate(template, random.Random(2)) == T.instantiate(
        template, random.Random(2)
    )


def test_a_finding_template_outweighs_a_repro_template():
    weights = T.load_weights()
    assert weights[T.SOURCE_FINDING] > weights[T.SOURCE_REPRO]


def test_weighted_choice_respects_the_weights():
    light = T.Template(setup=(), query="light", holes=(), source="repro", weight=1.0)
    heavy = T.Template(setup=(), query="heavy", holes=(), source="finding", weight=99.0)
    rng = random.Random(0)
    picks = [T.weighted_choice([light, heavy], rng).query for _ in range(200)]
    assert picks.count("heavy") > picks.count("light") * 5


def test_weighted_choice_handles_an_empty_list():
    assert T.weighted_choice([], random.Random(0)) is None


def test_templates_load_from_the_mined_corpus():
    templates = T.load_templates(limit_per_target=20)
    assert len(templates) >= 20
    assert all(t.query.strip() for t in templates)
    assert any(t.hole_count for t in templates)


def test_the_weights_file_only_uses_vocabulary_tags():
    weights = T.load_weights()
    for key in weights:
        if key in (T.SOURCE_FINDING, T.SOURCE_REPRO):
            continue
        assert TAG_RE.match(key), key


# --- the validity gates P07 sets ------------------------------------------




# --- indexes and constraints (the NoREC precondition) ----------------------


def test_an_index_is_generated_when_the_engine_supports_one(sqlite_caps):
    """NoREC is vacuous without an alternative access path.

    docs/FUZZER_AUDIT.md: norec returned 0 hits in 48,484 checks because the
    generator never created an index, so both sides of every pair ran the same
    sequential scan and the optimiser had nothing to get wrong.
    """
    found = []
    for seed in range(40):
        schema = S.random_schema(random.Random(seed), sqlite_caps)
        found += [s for s in schema.ddl() if "CREATE INDEX" in s or "UNIQUE INDEX" in s]
    assert found, "no index generated in 40 schemas"



def test_indexes_are_created_after_every_table(sqlite_caps):
    """An index on a table declared later would fail the setup."""
    for seed in range(40):
        schema = S.random_schema(random.Random(seed), sqlite_caps)
        statements = schema.ddl()
        last_create = max(
            (i for i, s in enumerate(statements) if s.startswith("CREATE TABLE")),
            default=-1,
        )
        for position, statement in enumerate(statements):
            if "INDEX" in statement:
                assert position > last_create, statement


def test_a_table_never_gets_two_primary_keys(sqlite_caps):
    """Two PRIMARY KEY columns is a syntax error, not an interesting case."""
    for seed in range(60):
        schema = S.random_schema(random.Random(seed), sqlite_caps)
        for statement in schema.ddl():
            assert statement.count("PRIMARY KEY") <= 1, statement


def test_a_primary_key_column_is_never_nullable(sqlite_caps):
    """Otherwise the NULL-heavy data generator fails every insert."""
    for seed in range(60):
        schema = S.random_schema(random.Random(seed), sqlite_caps)
        for table in schema.tables:
            for column in table.columns:
                if column.constraint == "primary_key":
                    assert not column.nullable


def test_unique_columns_get_distinct_values(sqlite_caps):
    """The regression that cost 47 percent of all cases.

    The data generator deliberately repeats rows so DISTINCT and GROUP BY get
    exercised. With UNIQUE and PRIMARY KEY columns now generated, that bias
    collides with the constraint by construction: measured against turso,
    setup success fell to 53 percent, every failure a "UNIQUE constraint
    failed" before the query ever ran. It is 100 percent with this bookkeeping.
    """
    from graft.generate.data import insert_statements, unique_columns

    checked = 0
    for seed in range(120):
        rng = random.Random(seed)
        schema = S.random_schema(rng, sqlite_caps)
        statements = insert_statements(schema, rng, sqlite_caps)
        by_table = {s.split()[2]: s for s in statements}
        for table in schema.tables:
            names = unique_columns(table)
            if not names or table.name not in by_table:
                continue
            rows = _parse_rows(by_table[table.name])
            for name in names:
                position = [c.name for c in table.columns].index(name)
                values = [r[position] for r in rows if r[position] != "NULL"]
                assert len(values) == len(set(values)), (table.name, name, values)
                checked += 1
    assert checked, "no unique column was exercised"


def _parse_rows(insert: str) -> list[list[str]]:
    """Split ``INSERT INTO t VALUES (..), (..)`` into per-row cell lists."""
    body = insert.split(" VALUES ", 1)[1]
    rows: list[list[str]] = []
    depth = 0
    cell = ""
    row: list[str] = []
    in_string = False
    for char in body:
        if char == "'":
            in_string = not in_string
            cell += char
        elif in_string:
            cell += char
        elif char == "(":
            depth += 1
            if depth == 1:
                row, cell = [], ""
            else:
                cell += char
        elif char == ")":
            depth -= 1
            if depth == 0:
                row.append(cell.strip())
                rows.append(row)
            else:
                cell += char
        elif char == "," and depth == 1:
            row.append(cell.strip())
            cell = ""
        elif depth >= 1:
            cell += char
    return rows


def test_a_unique_multi_column_index_is_never_emitted(sqlite_caps):
    """Only the tuple must be distinct, which per-column bookkeeping cannot
    guarantee - and it was the sole remaining cause of setup deaths."""
    for seed in range(80):
        schema = S.random_schema(random.Random(seed), sqlite_caps)
        for table in schema.tables:
            for index in table.indexes:
                if index.unique:
                    assert len(index.keys) == 1, index.create()


def test_index_feature_tags_describe_the_index(sqlite_caps):
    """The signature and the scheduler both read these tags."""
    from graft.generate.schema import Index

    plain = Index(name="i", table="t", keys=("a",))
    assert plain.feature_tags() == ("ddl:index",)
    rich = Index(
        name="i", table="t", keys=("a", "b"), unique=False, predicate="a > 0"
    )
    assert set(rich.feature_tags()) == {
        "ddl:index",
        "ddl:multi_column_index",
        "ddl:partial_index",
    }
    expression = Index(name="i", table="t", keys=("abs(a)",))
    assert "ddl:expression_index" in expression.feature_tags()


# --- the scalar-function surface, our one measured advantage ---------------


def test_the_functions_that_found_both_merged_bugs_are_generated(sqlite_caps):
    """lpad, rpad and repeat were NOT generated until 2026-11-02.

    Both bugs merged upstream came from those three sizing an allocation from
    an integer argument - and they reached the engine via mined templates, not
    via this generator. docs/FUZZER_AUDIT.md section 1: Turso's own fuzzer
    draws integers uniformly from [-1e6, 1e6], so it cannot produce
    9223372036854775807 and cannot reach either bug. Pairing our boundary
    literals with every size/count/index argument is the whole edge.
    """
    from graft.generate.expr import _FUNCTIONS

    generated = {entry[0] for entries in _FUNCTIONS.values() for entry in entries}
    for tag in ("fn:lpad", "fn:rpad", "fn:repeat"):
        assert tag in generated, tag


def test_substr_takes_generated_indices_not_hardcoded_ones(sqlite_caps):
    """substr's offset and length were literally `1, 2`.

    It is the one string function here taking two indices, and it never saw a
    boundary value.
    """
    from graft.generate.expr import _FUNCTIONS

    forms = [e for e in _FUNCTIONS["text"] if e[0] == "fn:substr"]
    assert forms, "substr is not generated"
    for _, template, argument_kinds in forms:
        assert "{1}" in template, template
        # the index arguments must be generated ints, not constants
        assert any(k == "int" for k in argument_kinds[1:]), argument_kinds


def test_a_size_argument_can_receive_a_boundary_integer(sqlite_caps):
    """The pairing that matters: a size argument fed an extreme literal."""
    import random as _random

    from graft.generate.expr import INT_LITERALS

    assert 9223372036854775807 in INT_LITERALS
    assert -9223372036854775808 in INT_LITERALS

    # And it actually happens: generate until a size-taking function appears
    # with a literal argument, over a fixed seed range so this cannot flake.
    from graft.generate.query import generate_case

    seen = False
    for seed in range(600):
        rng = _random.Random(seed)
        _, generated, _ = generate_case(rng, sqlite_caps, rng.random() < 0.5)
        if any(
            f"{name}(" in generated.sql
            for name in ("repeat", "lpad", "rpad", "substr", "substring")
        ):
            seen = True
            break
    assert seen, "no size-taking function generated in 600 cases"


def test_no_non_deterministic_function_is_generated(sqlite_caps):
    """current_date and friends would make TLP, NoREC and diff_sqlite compare
    two different questions rather than two phrasings of one."""
    from graft.generate.expr import _FUNCTIONS

    generated = {entry[0] for entries in _FUNCTIONS.values() for entry in entries}
    for tag in (
        "fn:current_date",
        "fn:current_timestamp",
        "fn:random",
        "fn:now",
        "fn:uuid",
    ):
        assert tag not in generated, tag


def test_every_generated_function_is_capability_gated(sqlite_caps):
    """An ungated function would be emitted at an engine that lacks it, which
    is how a validity rate collapses from 75 percent to 20."""
    from graft.generate.expr import _FUNCTIONS

    for kind, entries in _FUNCTIONS.items():
        for tag, template, argument_kinds in entries:
            assert tag.startswith("fn:"), (kind, tag)
            # the template must consume exactly its declared arguments
            for position in range(len(argument_kinds)):
                assert f"{{{position}}}" in template, (tag, template)


# --- sargable predicates: making NoREC's comparison mean something ---------


def test_a_sargable_term_names_an_indexed_column(sqlite_caps):
    """Generating indexes was necessary but nowhere near sufficient.

    Measured over 200 NoREC-eligible queries on indexed schemas: only 1.0% of
    them produced a SEARCH (an index seek). 63.5% were plain table scans and
    35.5% read the index as a covering SCAN - and a covering scan reads every
    row anyway, so the optimised and unoptimised forms cannot disagree. That
    is why NoREC still found nothing after the indexes landed. With this bias
    the seek rate is 35.0%.
    """
    import random as _random

    from graft.generate.query import QueryGenerator

    found = 0
    for seed in range(200):
        rng = _random.Random(seed)
        schema = S.random_schema(rng, sqlite_caps)
        generator = QueryGenerator(schema, sqlite_caps, rng)
        indexed = generator._indexed_columns()
        if not indexed:
            continue
        table = schema.tables[0]
        if table.name not in indexed:
            continue
        scope = tuple((f"{table.name}.{c.name}", c) for c in table.columns)
        term = generator._sargable_term(scope)
        if term is None:
            continue
        text, tags = term
        # it must name a column this table actually has an index on
        assert any(
            f"{table.name}.{name}" in text for name in indexed[table.name]
        ), (text, indexed[table.name])
        assert tags, text
        found += 1
    assert found, "no sargable term produced over 200 schemas"


def test_only_a_seekable_index_prefix_is_used(sqlite_caps):
    """A composite index can only be sought on a PREFIX, and an expression key
    like abs(c0) cannot be used by a predicate on the bare column."""
    import random as _random

    from graft.generate.query import QueryGenerator
    from graft.generate.schema import Column, Index, Table

    column_a = Column(name="a", label="integer", declared="INTEGER", kind="int")
    column_b = Column(name="b", label="integer", declared="INTEGER", kind="int")
    table = Table(
        name="t",
        columns=(column_a, column_b),
        indexes=(
            Index(name="i1", table="t", keys=("a", "b")),
            Index(name="i2", table="t", keys=("abs(b)",)),
        ),
    )
    schema = S.Schema(tables=(table,))
    generator = QueryGenerator(schema, sqlite_caps, _random.Random(0))
    indexed = generator._indexed_columns()
    # "a" is the composite prefix, so it is seekable
    assert "a" in indexed["t"]
    # "b" is only the SECOND key, and abs(b) is an expression key
    assert "b" not in indexed["t"]


def test_a_schema_with_no_index_yields_no_sargable_term(sqlite_caps):
    """DataFusion has no indexes at all; the bias must be a no-op there."""
    import random as _random

    from graft.generate.query import QueryGenerator
    from graft.generate.schema import Column, Table

    column = Column(name="a", label="integer", declared="INTEGER", kind="int")
    schema = S.Schema(tables=(Table(name="t", columns=(column,)),))
    generator = QueryGenerator(schema, sqlite_caps, _random.Random(0))
    assert generator._indexed_columns() == {}
    assert generator._sargable_term((("t.a", column),)) is None


# --- affinity-mismatched subquery comparisons -----------------------------


def test_an_affinity_subquery_mismatches_the_two_sides(sqlite_caps):
    """docs/TURSO_ATTACK_SURFACE.md 3.1: four of Turso's last 120 core commits
    fix scalar-subquery AFFINITY in one narrow area.

    The pre-existing "in" style compares a NUMERIC column against a NUMERIC
    subquery column, which is the case where affinity has nothing to convert.
    These forms deliberately mismatch the declared affinity of the two sides,
    which is what forces the conversion the fixes are about.
    """
    import random as _random

    from graft.generate.query import QueryGenerator
    from graft.generate.schema import Column, Table

    text_column = Column(name="c0", label="text", declared="TEXT", kind="text")
    int_column = Column(name="c0", label="integer", declared="INTEGER", kind="int")
    other = Table(name="t1", columns=(int_column,))
    schema = S.Schema(tables=(Table(name="t0", columns=(text_column,)), other))
    generator = QueryGenerator(schema, sqlite_caps, _random.Random(0))
    scope = (("t0.c0", text_column),)

    for style in ("affinity_in", "affinity_eq"):
        built = generator._affinity_subquery(scope, other, style)
        assert built is not None, style
        text, tags = built
        assert "t0.c0" in text and "t1" in text, text
        assert "clause:subquery" in tags, tags


def test_an_affinity_subquery_is_refused_when_kinds_cannot_differ(sqlite_caps):
    """A table of one kind cannot supply a mismatched column; return None
    rather than emit a same-affinity comparison dressed up as a mismatch."""
    import random as _random

    from graft.generate.query import QueryGenerator
    from graft.generate.schema import Column, Table

    int_column = Column(name="c0", label="integer", declared="INTEGER", kind="int")
    other = Table(name="t1", columns=(int_column,))
    schema = S.Schema(tables=(Table(name="t0", columns=(int_column,)), other))
    generator = QueryGenerator(schema, sqlite_caps, _random.Random(0))
    # scope is INT and the other table is INT, so no mismatch is available
    assert (
        generator._affinity_subquery((("t0.c0", int_column),), other, "affinity_in")
        is None
    )


# --- the deep data profile: reaching the storage layer --------------------




def test_the_default_profile_is_unchanged(sqlite_caps):
    """The deep profile must be opt-in; nothing changes silently."""
    from graft.generate.data import DEFAULT_PROFILE, MAX_ROWS, MIN_ROWS

    assert DEFAULT_PROFILE.min_rows == MIN_ROWS
    assert DEFAULT_PROFILE.max_rows == MAX_ROWS
    assert DEFAULT_PROFILE.overflow_probability == 0.0



# --- ALTER TABLE ADD COLUMN ----------------------------------------------


class _WithAlter:
    """Real capabilities plus the ALTER features, for tests that need them."""

    _EXTRA = frozenset(
        {
            "ddl:add_column",
            "ddl:add_column_default",
            "ddl:add_column_not_null",
            "ddl:add_generated_column",
        }
    )

    def __init__(self, inner):
        self._inner = inner

    def has_feature(self, feature):
        return feature in self._EXTRA or self._inner.has_feature(feature)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_alter_adds_columns_and_widens_the_schema(sqlite_caps):
    """Four of Turso's last 120 core commits touch ALTER, and the generator
    emitted none at all - docs/TURSO_ATTACK_SURFACE.md 3.2.

    The returned schema must be WIDENED, or the ALTER is executed and never
    exercised by any query.
    """
    import random as _random

    from graft.generate.schema import alter_statements

    caps = _WithAlter(sqlite_caps)
    found = False
    for seed in range(60):
        rng = _random.Random(seed)
        schema = S.random_schema(rng, sqlite_caps)
        statements, widened = alter_statements(schema, rng, caps)
        if not statements:
            continue
        found = True
        assert all(s.startswith("ALTER TABLE ") for s in statements), statements
        before = sum(len(t.columns) for t in schema.tables)
        after = sum(len(t.columns) for t in widened.tables)
        assert after == before + len(statements), (before, after)
    assert found, "no ALTER generated over 60 schemas"


def test_alter_only_ever_adds_never_drops_or_renames(sqlite_caps):
    """DROP COLUMN and RENAME COLUMN are both supported by Turso, but a query
    generated against the original schema would then name a column that no
    longer exists, so the case would die in setup instead of testing
    anything."""
    import random as _random

    from graft.generate.schema import alter_statements

    caps = _WithAlter(sqlite_caps)
    for seed in range(80):
        rng = _random.Random(seed)
        schema = S.random_schema(rng, sqlite_caps)
        statements, _ = alter_statements(schema, rng, caps)
        for statement in statements:
            assert "ADD COLUMN" in statement, statement
            assert "DROP" not in statement, statement
            assert "RENAME" not in statement, statement


def test_a_not_null_added_column_always_carries_a_default(sqlite_caps):
    """The existing rows need a value; without a DEFAULT the statement is
    rejected rather than interesting."""
    import random as _random

    from graft.generate.schema import alter_statements

    caps = _WithAlter(sqlite_caps)
    for seed in range(80):
        rng = _random.Random(seed)
        schema = S.random_schema(rng, sqlite_caps)
        statements, _ = alter_statements(schema, rng, caps)
        for statement in statements:
            if "NOT NULL" in statement:
                assert "DEFAULT" in statement, statement


