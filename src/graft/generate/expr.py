"""A typed expression generator with a depth limit.

Validity is the whole design constraint. An untyped generator that combines
whatever it likes produces mostly rejected SQL, and a rejected query tests
nothing - P07 asks for a 70 percent validity rate, and the way to get there is
to track the type of every node and only combine compatible ones.

So each builder is asked for a *kind* (``int``, ``real``, ``text`` or ``bool``)
and returns an expression of that kind, together with the SPEC feature tags it
used. Functions are only emitted when ``configs/capabilities.yaml`` says the
target has them, which is what keeps the generator honest across four engines
with very different function sets - of the 123 probed, 49 exist everywhere while
SQLite lacks 62.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from graft.generate.schema import KIND_BOOL, KIND_INT, KIND_REAL, KIND_TEXT, Column, Table
from graft.generate.capabilities import EngineCapabilities

MAX_DEPTH = 3


@dataclass(frozen=True)
class Expr:
    """A generated expression: its text, its kind, and the tags it used."""

    sql: str
    kind: str
    tags: tuple[str, ...] = ()

    def tagged(self, *extra: str) -> "Expr":
        """Return a copy with more tags."""
        return Expr(self.sql, self.kind, tuple(dict.fromkeys(self.tags + extra)))


@dataclass
class ExprContext:
    """What the generator may use: the columns in scope and the engine."""

    rng: random.Random
    capabilities: EngineCapabilities
    columns: tuple[tuple[str, Column], ...] = ()
    max_depth: int = MAX_DEPTH

    def has(self, tag: str) -> bool:
        """True if the engine has this function."""
        return self.capabilities.has_function(tag)

    def of_kind(self, *kinds: str) -> list[tuple[str, Column]]:
        """Return in-scope columns of the given kinds, as (qualified, column)."""
        return [pair for pair in self.columns if pair[1].kind in kinds]


#: integer literals worth generating
INT_LITERALS = (0, 1, -1, 2, 3, -2, 127, -128, 2147483647, -2147483648,
                9223372036854775807, -9223372036854775808)
REAL_LITERALS = (0.0, -0.0, 0.5, -0.5, 1.0, -1.0, 2.5, 1e-3)
TEXT_LITERALS = ("", " ", "a", "A", "abc", "ABC", "0", "1", "%", "_", "a'b")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def literal(kind: str, ctx: ExprContext) -> Expr:
    """Return a literal of ``kind``."""
    rng = ctx.rng
    if kind == KIND_INT:
        return Expr(str(rng.choice(INT_LITERALS)), KIND_INT, ("type:integer",))
    if kind == KIND_REAL:
        return Expr(repr(rng.choice(REAL_LITERALS)), KIND_REAL, ("type:double",))
    if kind == KIND_TEXT:
        return Expr(_quote(rng.choice(TEXT_LITERALS)), KIND_TEXT, ("type:varchar",))
    return Expr(rng.choice(("TRUE", "FALSE")), KIND_BOOL, ("type:boolean",))


def column_ref(kind: str, ctx: ExprContext) -> Expr | None:
    """Return a reference to an in-scope column of ``kind``, if one exists."""
    candidates = ctx.of_kind(kind)
    if not candidates:
        return None
    qualified, column = ctx.rng.choice(candidates)
    return Expr(qualified, kind, (f"type:{column.label}",))


def _arith(kind: str, ctx: ExprContext, depth: int) -> Expr | None:
    if kind not in (KIND_INT, KIND_REAL):
        return None
    operator = ctx.rng.choice(("+", "-", "*"))
    left = build(kind, ctx, depth + 1)
    right = build(kind, ctx, depth + 1)
    tag = {"+": "op:plus", "-": "op:minus", "*": "op:multiply"}[operator]
    return Expr(
        f"({left.sql} {operator} {right.sql})",
        kind,
        left.tags + right.tags + (tag,),
    )


def _comparison(ctx: ExprContext, depth: int) -> Expr:
    kind = ctx.rng.choice((KIND_INT, KIND_REAL, KIND_TEXT))
    operator = ctx.rng.choice(("=", "<>", "<", "<=", ">", ">="))
    left = build(kind, ctx, depth + 1)
    right = build(kind, ctx, depth + 1)
    tags = {
        "=": "op:eq", "<>": "op:ne", "<": "op:lt", "<=": "op:lte",
        ">": "op:gt", ">=": "op:gte",
    }[operator]
    return Expr(
        f"({left.sql} {operator} {right.sql})",
        KIND_BOOL,
        left.tags + right.tags + (tags,),
    )


def _is_null(ctx: ExprContext, depth: int) -> Expr:
    kind = ctx.rng.choice((KIND_INT, KIND_REAL, KIND_TEXT))
    inner = build(kind, ctx, depth + 1)
    negated = ctx.rng.random() < 0.5
    operator = "IS NOT NULL" if negated else "IS NULL"
    tag = "op:is_not_null" if negated else "op:is_null"
    return Expr(f"({inner.sql} {operator})", KIND_BOOL, inner.tags + (tag,))


def _boolean_logic(ctx: ExprContext, depth: int) -> Expr:
    if ctx.rng.random() < 0.25:
        inner = build(KIND_BOOL, ctx, depth + 1)
        return Expr(f"(NOT {inner.sql})", KIND_BOOL, inner.tags + ("op:not",))
    operator = ctx.rng.choice(("AND", "OR"))
    left = build(KIND_BOOL, ctx, depth + 1)
    right = build(KIND_BOOL, ctx, depth + 1)
    tag = "op:and" if operator == "AND" else "op:or"
    return Expr(
        f"({left.sql} {operator} {right.sql})",
        KIND_BOOL,
        left.tags + right.tags + (tag,),
    )


def _between(ctx: ExprContext, depth: int) -> Expr:
    kind = ctx.rng.choice((KIND_INT, KIND_REAL))
    value = build(kind, ctx, depth + 1)
    low = literal(kind, ctx)
    high = literal(kind, ctx)
    return Expr(
        f"({value.sql} BETWEEN {low.sql} AND {high.sql})",
        KIND_BOOL,
        value.tags + ("op:between",),
    )


def _in_list(ctx: ExprContext, depth: int) -> Expr:
    kind = ctx.rng.choice((KIND_INT, KIND_TEXT))
    value = build(kind, ctx, depth + 1)
    items = ", ".join(literal(kind, ctx).sql for _ in range(ctx.rng.randint(1, 3)))
    negated = ctx.rng.random() < 0.4
    operator = "NOT IN" if negated else "IN"
    tag = "op:not_in" if negated else "op:in"
    return Expr(f"({value.sql} {operator} ({items}))", KIND_BOOL, value.tags + (tag,))


def _case(kind: str, ctx: ExprContext, depth: int) -> Expr:
    condition = build(KIND_BOOL, ctx, depth + 1)
    then = build(kind, ctx, depth + 1)
    otherwise = build(kind, ctx, depth + 1)
    return Expr(
        f"(CASE WHEN {condition.sql} THEN {then.sql} ELSE {otherwise.sql} END)",
        kind,
        condition.tags + then.tags + otherwise.tags + ("clause:case",),
    )


def _coalesce(kind: str, ctx: ExprContext, depth: int) -> Expr | None:
    if not ctx.has("fn:coalesce"):
        return None
    first = build(kind, ctx, depth + 1)
    second = build(kind, ctx, depth + 1)
    return Expr(
        f"coalesce({first.sql}, {second.sql})",
        kind,
        first.tags + second.tags + ("fn:coalesce",),
    )


def _cast(kind: str, ctx: ExprContext, depth: int) -> Expr | None:
    """Cast to ``kind``, but only from a source that cannot fail.

    Casting arbitrary text to a number errors on DuckDB and DataFusion while
    SQLite returns 0, so only widening and number-to-text casts are generated.
    """
    target = {
        KIND_INT: "BIGINT", KIND_REAL: "DOUBLE", KIND_TEXT: "VARCHAR",
    }.get(kind)
    if target is None:
        return None
    if kind == KIND_TEXT:
        source_kind = ctx.rng.choice((KIND_INT, KIND_REAL))
    else:
        source_kind = KIND_INT if kind == KIND_REAL else KIND_INT
    inner = build(source_kind, ctx, depth + 1)
    if kind == KIND_INT and source_kind == KIND_INT:
        target = "BIGINT"
    declared = ctx.capabilities.declared_type(
        {"BIGINT": "bigint", "DOUBLE": "double", "VARCHAR": "varchar"}[target]
    )
    if declared is None:
        return None
    return Expr(
        f"CAST({inner.sql} AS {declared})", kind, inner.tags + ("clause:cast",)
    )


#: Scalar functions by result kind, each gated on its capability tag so an
#: engine never sees a function it does not have.
#:
#: Why this table is large and skewed toward size and domain arguments.
#: docs/FUZZER_AUDIT.md section 1 measured our one proven advantage: Turso's own
#: differential fuzzer draws integer literals uniformly from [-1e6, 1e6]
#: (sql_gen/src/policy.rs), so it CANNOT generate 9223372036854775807, and both
#: bugs we got merged needed exactly that - lpad/rpad/repeat sizing an
#: allocation from an integer argument. Yet the generator emitted only 12 of
#: Turso's 48 working scalar functions, and lpad, rpad and repeat were not among
#: them: those two bugs came from mined templates, not from here.
#:
#: So the rule is: pair our boundary-value literals with every function that
#: takes a SIZE, COUNT, INDEX or DOMAIN-limited argument, because that pairing
#: is the thing the incumbent fuzzer structurally cannot produce.
#:
#: Deliberately excluded: current_date/current_timestamp and other
#: non-deterministic functions, which would make TLP, NoREC and diff_sqlite
#: compare two different questions; and typeof, whose result is dialect-specific
#: and would be pure diff_sqlite noise.
_FUNCTIONS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    KIND_INT: (
        ("fn:abs", "abs({0})", (KIND_INT,)),
        ("fn:length", "length({0})", (KIND_TEXT,)),
        ("fn:instr", "instr({0}, {1})", (KIND_TEXT, KIND_TEXT)),
        ("fn:strpos", "strpos({0}, {1})", (KIND_TEXT, KIND_TEXT)),
        ("fn:char_length", "char_length({0})", (KIND_TEXT,)),
        ("fn:octet_length", "octet_length({0})", (KIND_TEXT,)),
        ("fn:sign", "sign({0})", (KIND_INT,)),
        # A zero divisor is the point, not an accident.
        ("fn:mod", "mod({0}, {1})", (KIND_INT, KIND_INT)),
        ("fn:json_array_length", "json_array_length({0})", (KIND_TEXT,)),
    ),
    KIND_REAL: (
        ("fn:abs", "abs({0})", (KIND_REAL,)),
        ("fn:round", "round({0})", (KIND_REAL,)),
        # The second argument is a DIGIT COUNT, so a boundary integer there is
        # an allocation-shaped question, not just an odd number.
        ("fn:round", "round({0}, {1})", (KIND_REAL, KIND_INT)),
        ("fn:sqrt", "sqrt(abs({0}))", (KIND_REAL,)),
        ("fn:ceil", "ceil({0})", (KIND_REAL,)),
        ("fn:ceiling", "ceiling({0})", (KIND_REAL,)),
        ("fn:floor", "floor({0})", (KIND_REAL,)),
        ("fn:trunc", "trunc({0})", (KIND_REAL,)),
        # Domain and overflow edges: ln(0), log(0, x), exp(1000), pow(1e308, 2).
        ("fn:exp", "exp({0})", (KIND_REAL,)),
        ("fn:ln", "ln({0})", (KIND_REAL,)),
        ("fn:log", "log({0})", (KIND_REAL,)),
        ("fn:log", "log({0}, {1})", (KIND_REAL, KIND_REAL)),
        ("fn:log2", "log2({0})", (KIND_REAL,)),
        ("fn:log10", "log10({0})", (KIND_REAL,)),
        ("fn:pow", "pow({0}, {1})", (KIND_REAL, KIND_REAL)),
        ("fn:power", "power({0}, {1})", (KIND_REAL, KIND_REAL)),
    ),
    KIND_TEXT: (
        ("fn:upper", "upper({0})", (KIND_TEXT,)),
        ("fn:lower", "lower({0})", (KIND_TEXT,)),
        ("fn:trim", "trim({0})", (KIND_TEXT,)),
        ("fn:btrim", "btrim({0}, {1})", (KIND_TEXT, KIND_TEXT)),
        ("fn:ltrim", "ltrim({0}, {1})", (KIND_TEXT, KIND_TEXT)),
        ("fn:rtrim", "rtrim({0}, {1})", (KIND_TEXT, KIND_TEXT)),
        ("fn:replace", "replace({0}, {1}, {2})", (KIND_TEXT, KIND_TEXT, KIND_TEXT)),
        ("fn:reverse", "reverse({0})", (KIND_TEXT,)),
        ("fn:concat", "concat({0}, {1})", (KIND_TEXT, KIND_TEXT)),
        ("fn:concat_ws", "concat_ws({0}, {1}, {2})",
         (KIND_TEXT, KIND_TEXT, KIND_TEXT)),
        # substr's offset and length were hardcoded to 1 and 2, so the one
        # function here that takes two indices never saw a boundary value.
        # substr('abc', -9223372036854775808, 9223372036854775807) is the
        # question that was being thrown away.
        ("fn:substr", "substr({0}, {1}, {2})", (KIND_TEXT, KIND_INT, KIND_INT)),
        ("fn:substr", "substr({0}, {1})", (KIND_TEXT, KIND_INT)),
        ("fn:substring", "substring({0}, {1}, {2})",
         (KIND_TEXT, KIND_INT, KIND_INT)),
        # The three that produced both merged bugs, finally generated here.
        # Their integer argument sizes an allocation directly.
        ("fn:repeat", "repeat({0}, {1})", (KIND_TEXT, KIND_INT)),
        ("fn:lpad", "lpad({0}, {1})", (KIND_TEXT, KIND_INT)),
        ("fn:lpad", "lpad({0}, {1}, {2})", (KIND_TEXT, KIND_INT, KIND_TEXT)),
        ("fn:rpad", "rpad({0}, {1})", (KIND_TEXT, KIND_INT)),
        ("fn:rpad", "rpad({0}, {1}, {2})", (KIND_TEXT, KIND_INT, KIND_TEXT)),
        ("fn:json_extract", "json_extract({0}, {1})", (KIND_TEXT, KIND_TEXT)),
    ),
}


def _function(kind: str, ctx: ExprContext, depth: int) -> Expr | None:
    options = [
        entry for entry in _FUNCTIONS.get(kind, ()) if ctx.has(entry[0])
    ]
    if not options:
        return None
    tag, template, argument_kinds = ctx.rng.choice(options)
    arguments = [build(k, ctx, depth + 1) for k in argument_kinds]
    tags: tuple[str, ...] = (tag,)
    for argument in arguments:
        tags += argument.tags
    return Expr(template.format(*(a.sql for a in arguments)), kind, tags)


def build(kind: str, ctx: ExprContext, depth: int = 0) -> Expr:
    """Return an expression of ``kind``, never deeper than ``ctx.max_depth``.

    At the depth limit only leaves are produced, so the generator always
    terminates.
    """
    rng = ctx.rng
    if depth >= ctx.max_depth:
        leaf = column_ref(kind, ctx) if rng.random() < 0.6 else None
        return leaf or literal(kind, ctx)

    builders: list[Callable[[], Expr | None]] = [
        lambda: column_ref(kind, ctx),
        lambda: literal(kind, ctx),
        lambda: _case(kind, ctx, depth),
        lambda: _coalesce(kind, ctx, depth),
        lambda: _cast(kind, ctx, depth),
        lambda: _function(kind, ctx, depth),
    ]
    if kind == KIND_BOOL:
        builders.extend(
            [
                lambda: _comparison(ctx, depth),
                lambda: _is_null(ctx, depth),
                lambda: _boolean_logic(ctx, depth),
                lambda: _between(ctx, depth),
                lambda: _in_list(ctx, depth),
            ]
        )
    else:
        builders.append(lambda: _arith(kind, ctx, depth))

    rng.shuffle(builders)
    for builder in builders:
        built = builder()
        if built is not None:
            return built
    return literal(kind, ctx)


def predicate(ctx: ExprContext, depth: int = 0) -> Expr:
    """Return a boolean expression, for a WHERE or a TLP partition."""
    return build(KIND_BOOL, ctx, depth)
