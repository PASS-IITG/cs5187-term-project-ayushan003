"""Re-run a reduced case twice on a fresh engine, and record what happened.

CLAUDE.md hard rule 9 makes this the gate: a result is only a bug candidate
when it is "reproduced twice on a fresh instance". Twice, because an engine
carrying state from an earlier statement can produce a one-off that never comes
back, and *fresh*, because a defect that needs a dirty session is a different
(and much weaker) claim than one that does not.

A case that does not reproduce identically both times is marked ``flaky`` rather
than quietly dropped: a flaky finding is still information, and SPEC gives it a
status of its own.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from graft.engines.base import Engine, Result, TestCase

STATUS_VERIFIED = "verified"
STATUS_FLAKY = "flaky"


@dataclass
class VerificationResult:
    """The outcome of verifying one finding."""

    status: str
    runs: tuple[str, ...]
    engine_version: str
    engine_commit: str
    detail: str = ""

    @property
    def verified(self) -> bool:
        """True if both runs produced the identical violation."""
        return self.status == STATUS_VERIFIED


def verify(
    case: TestCase,
    engine_factory: Callable[[], Engine],
    expected_kind: str,
    runs: int = 2,
) -> VerificationResult:
    """Run ``case`` ``runs`` times, each on a newly created engine.

    Args:
        case: the reduced case.
        engine_factory: returns a *new* started engine each call, so no state
            carries between runs.
        expected_kind: the violation shape from ``triage.reduce.violation_kind``.
        runs: how many times; SPEC asks for two.

    Returns:
        A :class:`VerificationResult` whose status is ``verified`` only when
        every run produced exactly ``expected_kind``.
    """
    from graft.triage.reduce import violation_kind

    observed: list[str] = []
    version = "UNKNOWN"
    commit = "UNKNOWN"
    for _ in range(runs):
        engine = engine_factory()
        try:
            version = engine.version()
            commit = engine.commit()
            result = engine.run_case(list(case.setup), case.query)
            observed.append(violation_kind(result))
        finally:
            engine.close()

    matched = all(kind == expected_kind for kind in observed)
    return VerificationResult(
        status=STATUS_VERIFIED if matched else STATUS_FLAKY,
        runs=tuple(observed),
        engine_version=version,
        engine_commit=commit,
        detail=(
            f"{len(observed)} fresh runs all produced {expected_kind!r}"
            if matched
            else f"expected {expected_kind!r}, observed {observed}"
        ),
    )


def history_entry(result: VerificationResult, run_index: int = 0) -> dict[str, Any]:
    """Return a verification_history entry for a finding."""
    return {
        "commit": result.engine_commit,
        "result": result.runs[run_index] if run_index < len(result.runs) else result.status,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "detail": result.detail,
    }
