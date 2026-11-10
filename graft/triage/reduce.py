"""Delta-debugging reducer: shrink a case while keeping the same violation.

SPEC's quality bar is a repro of at most ten statements with no random data, and
a maintainer reading a report should not have to work out which part matters. So
every candidate reduction is applied and then *re-run*: it is kept only if the
same oracle still fires with the same kind of mismatch. That check is what makes
the result trustworthy rather than merely shorter - a reducer that stops
checking is just a text mangler.

Steps, in the order P09 lists them: drop statements, drop inserted rows, drop
clauses (WHERE, ORDER BY, LIMIT, DISTINCT, GROUP BY, HAVING), replace
subexpressions with NULL, and shorten literals. Structure-aware steps use
sqlglot where the statement parses and fall back to text where it does not.

The budget is 200 executions, as P09 requires, so a reduction can never turn
into an unbounded search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from graft.engines.base import Engine, Result, TestCase

MAX_EXECUTIONS = 200

#: a trailing clause that can be removed without changing what is scanned
_CLAUSE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("limit", re.compile(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", re.IGNORECASE)),
    ("offset", re.compile(r"\s+OFFSET\s+\d+\s*$", re.IGNORECASE)),
    # a trailing clause body can itself contain parentheses (ORDER BY count(*)),
    # so these run to the end of the statement and the result is checked for
    # balanced parentheses before it is offered - otherwise a window's own
    # ORDER BY would be torn out of `OVER (ORDER BY a)`.
    ("order_by", re.compile(r"\s+ORDER\s+BY\s+.*$", re.IGNORECASE | re.DOTALL)),
    ("having", re.compile(r"\s+HAVING\s+.*$", re.IGNORECASE | re.DOTALL)),
    ("distinct", re.compile(r"(?<=SELECT)\s+DISTINCT\b", re.IGNORECASE)),
)
_INSERT_RE = re.compile(r"^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\b", re.IGNORECASE)
_ROW_RE = re.compile(r"\(([^()]*)\)")
_LONG_STRING_RE = re.compile(r"'([^']{4,})'")


@dataclass
class ReductionResult:
    """What a reduction achieved."""

    case: TestCase
    executions: int = 0
    steps_kept: list[str] = field(default_factory=list)
    original_length: int = 0
    reduced_length: int = 0

    @property
    def ratio(self) -> float:
        """Reduced characters over original characters."""
        if not self.original_length:
            return 1.0
        return self.reduced_length / self.original_length


def case_text(case: TestCase) -> str:
    """Return the whole case as one string, for measuring size."""
    return "\n".join([*case.setup, case.query])


def _statements_without(setup: Sequence[str], index: int) -> list[str]:
    return [s for position, s in enumerate(setup) if position != index]


def _drop_rows(statement: str) -> Iterable[str]:
    """Yield the statement with one VALUES row removed, longest first."""
    if not _INSERT_RE.match(statement):
        return
    rows = _ROW_RE.findall(statement)
    if len(rows) <= 1:
        return
    for row in rows:
        candidate = statement.replace(f"({row})", "", 1)
        candidate = re.sub(r",\s*,", ",", candidate)
        candidate = re.sub(r"VALUES\s*,", "VALUES ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r",\s*$", "", candidate.strip())
        if candidate != statement and "VALUES" in candidate.upper():
            yield candidate


def _balanced(text: str) -> bool:
    """True if parentheses balance outside string literals."""
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and quote is None


def _drop_clauses(query: str) -> Iterable[tuple[str, str]]:
    for name, pattern in _CLAUSE_PATTERNS:
        reduced = pattern.sub("", query).strip()
        if reduced and reduced != query.strip() and _balanced(reduced):
            yield name, reduced


def _nullify_subexpressions(query: str) -> Iterable[str]:
    """Replace a parsed subexpression with NULL, simplest candidates first."""
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:  # pragma: no cover
        return
    try:
        tree = sqlglot.parse_one(query, error_level=None)
    except Exception:
        return
    if tree is None:
        return
    for node in list(tree.walk()):
        if not isinstance(node, (exp.Func, exp.Case, exp.Binary)):
            continue
        if node.parent is None:
            continue
        original = node.sql()
        if original.strip().upper() == "NULL":
            continue
        candidate = query.replace(original, "NULL", 1)
        if candidate != query:
            yield candidate


def _shorten_literals(text: str) -> Iterable[str]:
    for match in _LONG_STRING_RE.finditer(text):
        body = match.group(1)
        yield text.replace(f"'{body}'", f"'{body[0]}'", 1)
    for match in re.finditer(r"(?<![\w.])(-?\d{3,})(?![\w.])", text):
        yield text.replace(match.group(1), "1", 1)


def reduce_case(
    case: TestCase,
    still_fires: Callable[[TestCase], bool],
    max_executions: int = MAX_EXECUTIONS,
) -> ReductionResult:
    """Shrink ``case`` while ``still_fires`` keeps returning True.

    Args:
        case: the case to reduce.
        still_fires: re-runs the case and returns True if the same oracle still
            fires with the same kind of mismatch. Every candidate reduction is
            checked through this, which is what makes the output trustworthy.
        max_executions: hard budget, so reduction terminates.

    Returns:
        The smallest case found, with the number of executions used.
    """
    result = ReductionResult(
        case=case, original_length=len(case_text(case)),
    )
    current = case
    budget = max_executions

    def attempt(candidate: TestCase, label: str) -> bool:
        nonlocal current, budget
        if budget <= 0:
            return False
        budget -= 1
        result.executions += 1
        if still_fires(candidate):
            current = candidate
            result.steps_kept.append(label)
            return True
        return False

    changed = True
    while changed and budget > 0:
        changed = False

        # 1. drop whole setup statements, last first (later ones are usually
        #    the incidental ones)
        for index in range(len(current.setup) - 1, -1, -1):
            candidate = TestCase(
                setup=_statements_without(current.setup, index),
                query=current.query,
                origin=current.origin, seed=current.seed, meta=dict(current.meta),
            )
            if attempt(candidate, f"drop setup[{index}]"):
                changed = True
                break
        if changed:
            continue

        # 2. drop inserted rows
        for index, statement in enumerate(current.setup):
            for reduced in _drop_rows(statement):
                setup = list(current.setup)
                setup[index] = reduced
                candidate = TestCase(
                    setup=setup, query=current.query, origin=current.origin,
                    seed=current.seed, meta=dict(current.meta),
                )
                if attempt(candidate, f"drop row in setup[{index}]"):
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue

        # 3. drop trailing clauses from the query
        for name, reduced in _drop_clauses(current.query):
            candidate = TestCase(
                setup=list(current.setup), query=reduced, origin=current.origin,
                seed=current.seed, meta=dict(current.meta),
            )
            if attempt(candidate, f"drop {name}"):
                changed = True
                break
        if changed:
            continue

        # 4. replace a subexpression with NULL
        for reduced in _nullify_subexpressions(current.query):
            candidate = TestCase(
                setup=list(current.setup), query=reduced, origin=current.origin,
                seed=current.seed, meta=dict(current.meta),
            )
            if attempt(candidate, "nullify subexpression"):
                changed = True
                break
        if changed:
            continue

        # 5. shorten literals, in the query and then in the setup
        for reduced in _shorten_literals(current.query):
            candidate = TestCase(
                setup=list(current.setup), query=reduced, origin=current.origin,
                seed=current.seed, meta=dict(current.meta),
            )
            if attempt(candidate, "shorten literal"):
                changed = True
                break
        if changed:
            continue
        for index, statement in enumerate(current.setup):
            for reduced in _shorten_literals(statement):
                setup = list(current.setup)
                setup[index] = reduced
                candidate = TestCase(
                    setup=setup, query=current.query, origin=current.origin,
                    seed=current.seed, meta=dict(current.meta),
                )
                if attempt(candidate, f"shorten literal in setup[{index}]"):
                    changed = True
                    break
            if changed:
                break

    result.case = current
    result.reduced_length = len(case_text(current))
    return result


def violation_kind(result: Result) -> str:
    """Return the comparable shape of an outcome, for "the same violation".

    Reduction must preserve *the same* problem, so two outcomes count as the
    same only when the error class matches and, for an error, its placeheld
    message does too. Comparing raw messages would reject every reduction that
    changed a literal, which is most of them.
    """
    from graft.triage.signature import placehold

    if result.ok:
        return f"ok:{result.row_count}"
    return f"{result.error_class}:{placehold(result.error or '')[:120]}"
