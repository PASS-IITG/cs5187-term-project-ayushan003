"""Deriving SPEC feature tags from SQL text.

SPEC "Feature tags": lower-case ``fn:``, ``op:``, ``clause:``, ``type:``,
``agg:``, ``join:``, ``cte:``, ``set:``, ``win:``, shared by the generator, the
mutator and the signature. This module is the shared implementation, so a tag
means the same thing wherever it appears.

It is a lexical tagger, not a parser. It masks string literals first so a tag is
never produced from inside one, and it errs toward missing a tag rather than
inventing one: a wrong tag would mis-route a case into the diff_sqlite common
subset, which is the one place an unsound comparison could become a false
finding.
"""

from __future__ import annotations

import re
from typing import Iterable

#: (tag, pattern). Order is irrelevant; every match contributes.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # clauses
    ("clause:select", re.compile(r"^\s*SELECT\b", re.IGNORECASE)),
    ("clause:where", re.compile(r"\bWHERE\b", re.IGNORECASE)),
    ("clause:group_by", re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)),
    ("clause:group_by_rollup", re.compile(r"\bROLLUP\b", re.IGNORECASE)),
    ("clause:group_by_cube", re.compile(r"\bCUBE\b", re.IGNORECASE)),
    ("clause:grouping_sets", re.compile(r"\bGROUPING\s+SETS\b", re.IGNORECASE)),
    ("clause:having", re.compile(r"\bHAVING\b", re.IGNORECASE)),
    ("clause:order_by", re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)),
    ("clause:limit", re.compile(r"\bLIMIT\b", re.IGNORECASE)),
    ("clause:offset", re.compile(r"\bOFFSET\b", re.IGNORECASE)),
    ("clause:distinct", re.compile(r"\bDISTINCT\b", re.IGNORECASE)),
    ("clause:distinct_on", re.compile(r"\bDISTINCT\s+ON\b", re.IGNORECASE)),
    ("clause:case", re.compile(r"\bCASE\b", re.IGNORECASE)),
    ("clause:cast", re.compile(r"\bCAST\s*\(|::", re.IGNORECASE)),
    ("clause:subquery", re.compile(r"\(\s*SELECT\b", re.IGNORECASE)),
    ("clause:filter", re.compile(r"\bFILTER\s*\(", re.IGNORECASE)),
    ("clause:window", re.compile(r"\bWINDOW\b", re.IGNORECASE)),
    ("clause:window_frame_rows", re.compile(r"\bROWS\s+BETWEEN\b", re.IGNORECASE)),
    ("clause:window_frame_range", re.compile(r"\bRANGE\s+BETWEEN\b", re.IGNORECASE)),
    ("clause:qualify", re.compile(r"\bQUALIFY\b", re.IGNORECASE)),
    ("clause:values", re.compile(r"\bVALUES\b", re.IGNORECASE)),
    ("clause:nulls_first", re.compile(r"\bNULLS\s+(FIRST|LAST)\b", re.IGNORECASE)),
    ("clause:collate", re.compile(r"\bCOLLATE\b", re.IGNORECASE)),
    ("clause:primary_key", re.compile(r"\bPRIMARY\s+KEY\b", re.IGNORECASE)),
    ("clause:unique_constraint", re.compile(r"\bUNIQUE\b", re.IGNORECASE)),
    ("clause:check_constraint", re.compile(r"\bCHECK\s*\(", re.IGNORECASE)),
    ("clause:foreign_key", re.compile(r"\bREFERENCES\b", re.IGNORECASE)),
    ("clause:not_null", re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)),
    ("clause:default", re.compile(r"\bDEFAULT\b", re.IGNORECASE)),
    ("clause:generated_column", re.compile(r"\bGENERATED\b|\bVIRTUAL\b|\bSTORED\b", re.IGNORECASE)),
    # joins
    ("join:inner", re.compile(r"\b(INNER\s+)?JOIN\b", re.IGNORECASE)),
    ("join:left", re.compile(r"\bLEFT\s+(OUTER\s+)?JOIN\b", re.IGNORECASE)),
    ("join:right", re.compile(r"\bRIGHT\s+(OUTER\s+)?JOIN\b", re.IGNORECASE)),
    ("join:full", re.compile(r"\bFULL\s+(OUTER\s+)?JOIN\b", re.IGNORECASE)),
    ("join:cross", re.compile(r"\bCROSS\s+JOIN\b", re.IGNORECASE)),
    ("join:natural", re.compile(r"\bNATURAL\s+JOIN\b", re.IGNORECASE)),
    ("join:using", re.compile(r"\bUSING\s*\(", re.IGNORECASE)),
    ("join:lateral", re.compile(r"\bLATERAL\b", re.IGNORECASE)),
    # set operations
    ("set:union_all", re.compile(r"\bUNION\s+ALL\b", re.IGNORECASE)),
    ("set:union", re.compile(r"\bUNION\b(?!\s+ALL)", re.IGNORECASE)),
    ("set:except", re.compile(r"\bEXCEPT\b", re.IGNORECASE)),
    ("set:intersect", re.compile(r"\bINTERSECT\b", re.IGNORECASE)),
    # CTEs
    ("cte:recursive", re.compile(r"\bWITH\s+RECURSIVE\b", re.IGNORECASE)),
    ("cte:non_recursive", re.compile(r"\bWITH\b(?!\s+RECURSIVE)", re.IGNORECASE)),
    # operators
    ("op:is_null", re.compile(r"\bIS\s+NULL\b", re.IGNORECASE)),
    ("op:is_not_null", re.compile(r"\bIS\s+NOT\s+NULL\b", re.IGNORECASE)),
    ("op:is_distinct_from", re.compile(r"\bIS\s+(NOT\s+)?DISTINCT\s+FROM\b", re.IGNORECASE)),
    ("op:in", re.compile(r"\bIN\s*\(", re.IGNORECASE)),
    ("op:not_in", re.compile(r"\bNOT\s+IN\s*\(", re.IGNORECASE)),
    ("op:exists", re.compile(r"\bEXISTS\b", re.IGNORECASE)),
    ("op:like", re.compile(r"\bLIKE\b", re.IGNORECASE)),
    ("op:ilike", re.compile(r"\bILIKE\b", re.IGNORECASE)),
    ("op:between", re.compile(r"\bBETWEEN\b", re.IGNORECASE)),
    ("op:not", re.compile(r"\bNOT\b", re.IGNORECASE)),
    ("op:and", re.compile(r"\bAND\b", re.IGNORECASE)),
    ("op:or", re.compile(r"\bOR\b", re.IGNORECASE)),
    ("op:eq", re.compile(r"(?<![<>!])=(?!=)")),
    ("op:ne", re.compile(r"<>|!=")),
    ("op:lt", re.compile(r"<(?![=>])")),
    ("op:gt", re.compile(r"(?<!<)>(?!=)")),
    ("op:lte", re.compile(r"<=")),
    ("op:gte", re.compile(r">=")),
    ("op:plus", re.compile(r"(?<![\w)])\+")),
    ("op:minus", re.compile(r"(?<![\w)])-(?!-)")),
    ("op:multiply", re.compile(r"\*(?!/)")),
    ("op:divide", re.compile(r"/(?!\*)")),
    ("op:modulo", re.compile(r"%")),
    ("op:concat", re.compile(r"\|\|")),
    ("op:row_value", re.compile(r"\(\s*\w+\s*,\s*\w+\s*\)\s*(=|<|>|IN)", re.IGNORECASE)),
)

#: aggregate names that also exist as window functions
_AGGREGATES = (
    "count", "sum", "avg", "min", "max", "total", "group_concat", "string_agg",
    "array_agg", "bit_and", "bit_or", "bit_xor", "bool_and", "bool_or",
    "stddev_pop", "stddev_samp", "stddev", "var_pop", "var_samp", "variance",
    "median", "mode", "corr", "covar_pop", "covar_samp",
)
_WINDOW_ONLY = (
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
    "lead", "lag", "first_value", "last_value", "nth_value",
)
#: type names worth tagging when they appear in a declaration or a cast
_TYPES = (
    "tinyint", "smallint", "integer", "int", "bigint", "hugeint", "decimal",
    "numeric", "real", "float", "double", "boolean", "varchar", "text", "char",
    "blob", "date", "time", "timestamp", "interval", "uuid", "json", "struct",
    "map", "list", "array",
)
_FUNCTION_CALL_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
_OVER_RE = re.compile(r"\bOVER\b", re.IGNORECASE)

#: a table name introduced by DDL/DML, which is not a function call
_DECLARED_NAME_RE = re.compile(
    r"\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?(?:TABLE|VIEW)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?|INSERT\s+INTO\s+|UPDATE\s+|DELETE\s+FROM\s+|"
    r"REFERENCES\s+)(?P<name>[\"'`\w.]+)\s*(?=\()",
    re.IGNORECASE,
)
#: a CTE name with a column list
_CTE_NAME_RE = re.compile(
    r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)(?P<name>[\"'`\w]+)\s*\([^()]*\)\s*AS\s*\(",
    re.IGNORECASE,
)
#: a projection star or an aggregate's ``(*)``, neither of which is multiplication
_STAR_RE = re.compile(r"\(\s*\*\s*\)|(?<=SELECT)\s+\*|,\s*\*", re.IGNORECASE)

#: words that precede "(" without being a function call
_NON_FUNCTIONS = frozenset(
    {
        "select", "from", "where", "in", "exists", "values", "and", "or", "not",
        "on", "using", "cast", "case", "when", "then", "else", "end", "over",
        "partition", "filter", "rollup", "cube", "grouping", "sets", "table",
        "view", "if", "primary", "key", "unique", "check", "references", "with",
        "recursive", "order", "group", "by", "having", "limit", "offset",
        "union", "except", "intersect", "lateral", "qualify", "window", "rows",
        "range", "between", "insert", "into", "update", "delete", "create",
        "as", "all", "any", "some", "distinct", "null", "true", "false",
        "returning", "conflict", "do", "nothing", "set", "collate", "escape",
        *(_TYPES),
    }
)


def mask_literals(sql: str) -> str:
    """Blank string literals, keeping length, so tags never come from inside one."""
    out = list(sql)
    quote: str | None = None
    for index, char in enumerate(sql):
        if quote:
            out[index] = " "
            if char == quote:
                quote = None
                out[index] = char
            continue
        if char in "'\"":
            quote = char
    return "".join(out)


def _mask_names_and_stars(masked: str) -> str:
    """Blank table names before a parenthesis, and projection stars.

    ``CREATE TABLE t(a INT)`` would otherwise be tagged ``fn:t``, and the star
    in ``count(*)`` or ``SELECT *`` would be tagged ``op:multiply``.
    """
    out = list(masked)
    for pattern in (_DECLARED_NAME_RE, _CTE_NAME_RE):
        for match in pattern.finditer(masked):
            for index in range(*match.span("name")):
                out[index] = " "
    for match in _STAR_RE.finditer(masked):
        for index in range(*match.span()):
            if out[index] == "*":
                out[index] = " "
    return "".join(out)


#: sqlglot expression type -> SPEC tag. Only mappings that are unambiguous are
#: listed; anything else falls through to the lexical tagger, which is still run
#: on every statement so a tag is never lost by switching parsers.
_SQLGLOT_TAGS: dict[str, str] = {
    "Select": "clause:select",
    "Where": "clause:where",
    "Group": "clause:group_by",
    "Having": "clause:having",
    "Order": "clause:order_by",
    "Limit": "clause:limit",
    "Offset": "clause:offset",
    "Distinct": "clause:distinct",
    "Case": "clause:case",
    "Cast": "clause:cast",
    "TryCast": "clause:cast",
    "Window": "clause:window",
    "Subquery": "clause:subquery",
    "Exists": "op:exists",
    "In": "op:in",
    "Between": "op:between",
    "Is": "op:is_null",
    "Like": "op:like",
    "ILike": "op:ilike",
    "Not": "op:not",
    "And": "op:and",
    "Or": "op:or",
    "EQ": "op:eq",
    "NEQ": "op:ne",
    "LT": "op:lt",
    "LTE": "op:lte",
    "GT": "op:gt",
    "GTE": "op:gte",
    "Add": "op:plus",
    "Sub": "op:minus",
    "Mul": "op:multiply",
    "Div": "op:divide",
    "Mod": "op:modulo",
    "DPipe": "op:concat",
    "Union": "set:union",
    "Except": "set:except",
    "Intersect": "set:intersect",
    "With": "cte:non_recursive",
    "Join": "join:inner",
    "Count": "agg:count",
    "Sum": "agg:sum",
    "Avg": "agg:avg",
    "Min": "agg:min",
    "Max": "agg:max",
}

#: aggregate names that become window functions under OVER
_WINDOW_FUNCTIONS = frozenset(
    {
        "RowNumber", "Rank", "DenseRank", "PercentRank", "CumeDist", "NTile",
        "Lead", "Lag", "FirstValue", "LastValue", "NthValue",
    }
)


def sqlglot_tags(statements: Iterable[str]) -> tuple[str, ...]:
    """Return tags derived by parsing, or () if nothing parses.

    P09 asks for sqlglot with a regex fallback. Parsing catches structure the
    lexical tagger cannot see - a JOIN's kind, an aggregate used as a window
    function, an ORDER BY that belongs to a window rather than the query - but
    a generated or mined statement often will not parse at all, so the result
    is unioned with the lexical tags rather than replacing them.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:  # pragma: no cover - sqlglot is a declared dependency
        return ()

    tags: set[str] = set()
    for statement in statements:
        try:
            trees = sqlglot.parse(statement, error_level=None)
        except Exception:
            continue
        for tree in trees:
            if tree is None:
                continue
            for node in tree.walk():
                name = type(node).__name__
                tag = _SQLGLOT_TAGS.get(name)
                if tag:
                    tags.add(tag)
                if name == "Join":
                    side = (node.args.get("side") or "").upper()
                    kind = (node.args.get("kind") or "").upper()
                    if side == "LEFT":
                        tags.add("join:left")
                    elif side == "RIGHT":
                        tags.add("join:right")
                    elif side == "FULL":
                        tags.add("join:full")
                    elif kind == "CROSS":
                        tags.add("join:cross")
                if name == "Window":
                    function = node.args.get("this")
                    if function is not None:
                        inner = type(function).__name__
                        tags.add(f"win:{_snake(inner)}")
                if name in _WINDOW_FUNCTIONS:
                    tags.add(f"win:{_snake(name)}")
                if name == "Anonymous":
                    called = str(node.args.get("this") or "").lower()
                    if called.isidentifier():
                        tags.add(f"fn:{called}")
                if name == "With" and node.args.get("recursive"):
                    tags.add("cte:recursive")
                if name == "Union" and node.args.get("distinct") is False:
                    tags.add("set:union_all")
    return tuple(sorted(tags))


def _snake(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


def feature_tags(statements: Iterable[str]) -> tuple[str, ...]:
    """Return the sorted SPEC feature tags for a set of statements.

    Args:
        statements: setup statements and the query; all are considered, because
            a declared type in the setup is as much a feature as one in a cast.

    Returns:
        Sorted unique tags.
    """
    sql = "\n".join(statements)
    masked = _mask_names_and_stars(mask_literals(sql))
    tags: set[str] = set()

    for tag, pattern in _PATTERNS:
        if pattern.search(masked):
            tags.add(tag)

    # a plain JOIN tag is implied by the qualified forms; keep both, since the
    # signature buckets on the set and a human reads it
    for name in _TYPES:
        if re.search(rf"\b{name}\b", masked, re.IGNORECASE):
            tags.add(f"type:{name}")

    windowed = bool(_OVER_RE.search(masked))
    for match in _FUNCTION_CALL_RE.finditer(masked):
        name = match.group(1).lower()
        if name in _NON_FUNCTIONS:
            continue
        if name in _WINDOW_ONLY:
            tags.add(f"win:{name}")
        elif name in _AGGREGATES:
            tags.add(f"win:{name}" if windowed else f"agg:{name}")
        else:
            tags.add(f"fn:{name}")

    # structure the lexical pass cannot see, where the statement parses
    tags.update(sqlglot_tags(statements if isinstance(statements, (list, tuple)) else [sql]))
    return tuple(sorted(tags))
