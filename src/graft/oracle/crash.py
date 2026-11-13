"""The crash oracle: an engine must not fall over on SQL it accepted.

SPEC: "crash: internal_error/panic/crash/timeout on VALID sql. user_error on
invalid sql is not a violation. error-on-one-engine vs success-on-another is
reported only by diff_sqlite."

So a ``user_error`` is never a violation here, however odd the message. What is
a violation is the engine breaking its own invariants: a Rust panic, an INTERNAL
Error banner, a failed assertion, a segfault, or a hang.

One calibration from the P04 corpus: DataFusion reports well-formed but
unsupported SQL as ``This feature is not implemented: ...``, and
``adapters/errors.py`` classifies that as ``internal_error`` because P02 asked
for "not implemented" to be an internal_error pattern. That is a deliberate,
user-facing refusal rather than a broken invariant, so this oracle separates the
two: an unimplemented-feature message is reported with ``questionable`` status
and an explicit note, while a panic or an assertion failure is reported as
``new``. Neither is discarded, because an engine refusing SQL that every other
engine runs is exactly the ``panic_unimplemented`` bug class - it just is not a
crash, and must not be filed as one.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

from graft.engines import errors
from graft.engines.base import Engine, Result, TestCase
from graft.oracle.base import STATUS_NEW, STATUS_QUESTIONABLE, Oracle, Violation, result_summary

#: error classes that mean the engine broke rather than refused
CRASH_CLASSES = frozenset(
    {errors.INTERNAL_ERROR, errors.PANIC, errors.CRASH, errors.TIMEOUT}
)

#: a deliberate "we do not support this" refusal, not a broken invariant
_UNIMPLEMENTED_RE = re.compile(
    r"\bnot yet implemented\b|\bnot implemented\b|\bunimplemented\b|"
    r"\bThis feature is not implemented\b",
    re.IGNORECASE,
)


def is_unimplemented(result: Result) -> bool:
    """True if the failure is an unsupported-feature refusal."""
    return bool(result.error and _UNIMPLEMENTED_RE.search(result.error))


#: Signals that mean something OUTSIDE the engine killed it, so the death
#: cannot be attributed to the statement. SIGKILL is what the OOM killer
#: sends, and it picks a victim by the machine's overall memory pressure, not
#: by whose allocation was unreasonable: a campaign running three engines at
#: once had DuckDB killed on `LPAD(' ', 2147483647, 'A')`, which on a quiet
#: machine allocates its 2 GB and returns the right answer. SIGTERM/SIGINT/
#: SIGHUP are a person or a supervisor stopping the process.
#:
#: An engine that really does allocate without bound dies by SIGABRT with
#: "memory allocation of N bytes failed", which is attributable and stays a
#: crash - that is how turso #11219 was found.
_EXTERNAL_KILL_RE = re.compile(r"\bexit status -(?:9|15|2|1)\b")


def is_externally_killed(result: Result) -> bool:
    """True if the process was killed from outside rather than faulting."""
    return bool(result.error and _EXTERNAL_KILL_RE.search(result.error))


class CrashOracle(Oracle):
    """Flags internal errors, panics, crashes and timeouts."""

    name = "crash"

    def applies_to(self, engine: Engine) -> bool:
        """Applies to every engine."""
        return True

    def check(
        self, engines: Mapping[str, Engine], test_case: TestCase
    ) -> list[Violation]:
        """Run the case on each engine and flag any that broke."""
        violations: list[Violation] = []
        for name, engine in engines.items():
            result = engine.run_case(test_case.setup, test_case.query)
            violations.extend(self.inspect(name, result, test_case))
        return violations

    def inspect(
        self, engine_name: str, result: Result, test_case: TestCase
    ) -> list[Violation]:
        """Judge an already-executed result. Used by the runner, which
        executes once and shows the same result to several oracles."""
        if result.error_class not in CRASH_CLASSES:
            return []
        unimplemented = is_unimplemented(result)
        killed = is_externally_killed(result)
        # A timeout on its own says the query did not finish inside the limit,
        # which a correct engine may legitimately do on a large input. Without
        # a second data point - the same case finishing on another engine, or
        # on smaller input - it cannot be claimed as a hang, so it is reported
        # as questionable rather than new. P09's verifier is where a timeout
        # earns promotion.
        timed_out = result.error_class == errors.TIMEOUT
        detail = (result.error or "").strip().splitlines()
        return [
            Violation(
                oracle=self.name,
                engine=engine_name,
                test_case=test_case,
                expected="a result, or a user_error",
                actual=f"{result.error_class}: {detail[0] if detail else '(no message)'}",
                detail=(
                    "the engine reported an unsupported feature rather than a "
                    "broken invariant, so this is a panic_unimplemented "
                    "candidate, not a crash"
                    if unimplemented
                    else (
                        "did not finish inside the timeout; a slow query and a "
                        "hang look the same from here, so this needs a second "
                        "data point before it is a crash"
                        if timed_out
                        else (
                            "killed from outside the engine, almost always the "
                            "OOM killer, which picks its victim by the whole "
                            "machine's memory pressure rather than by this "
                            "statement; re-run it alone on a quiet machine "
                            "before treating it as a crash"
                            if killed
                            else f"{result.error_class} on SQL the engine accepted"
                        )
                    )
                ),
                suggested_status=(
                    STATUS_QUESTIONABLE
                    if (unimplemented or timed_out or killed)
                    else STATUS_NEW
                ),
                context={
                    "result": result_summary(result),
                    "unimplemented_refusal": unimplemented,
                    "timed_out": timed_out,
                    "externally_killed": killed,
                },
            )
        ]
