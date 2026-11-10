"""Building the SPEC ``finding.json`` record for an oracle violation.

This lived in ``variants/run.py``, the driver for the variant-hunter stage that
is no longer part of the tool (see docs/RESEARCH_AUDIT.md §7). The record format
is defined by docs/SPEC.md and is written by the campaign runner, so the builder
belongs with the rest of triage.

Only the SPEC's tool-settable fields are filled here. ``status`` comes from the
oracle's own suggestion and never from a human-only value, and ``reduced_sql``
is left ``None`` for ``triage.reduce`` to fill.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from graft.engines.base import Engine, Result
from graft.oracle.base import Violation, result_summary


def jsonable(value: Any) -> Any:
    """Make a value JSON-serialisable without losing what it was."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return str(value)


def build_finding(
    violation: Violation,
    target: str,
    engine: Engine,
    signature: str,
    tags: Sequence[str],
    seed: int,
    origin_issue: Any,
    raw_result: Result | None,
) -> dict[str, Any]:
    """Build the SPEC finding.json record."""
    case = violation.test_case
    return {
        "id": f"{target}-{signature[:12]}",
        "coarse_signature": signature,
        "feature_tags": list(tags),
        "target": target,
        "engine_version": engine.version(),
        "engine_commit": engine.commit(),
        "configured_commit": engine.configured_commit,
        "oracle": violation.oracle,
        "origin": case.origin,
        "origin_issue": (
            f"https://github.com/{origin_issue}" if isinstance(origin_issue, str)
            else origin_issue
        ),
        "seed": seed,
        "setup": list(case.setup),
        "original_sql": list(case.meta.get("original_sql") or [case.query]),
        "reduced_sql": None,
        "raw_expected": jsonable(violation.expected),
        "raw_actual": jsonable(violation.actual),
        "normalized_expected": jsonable(
            violation.context.get("normalized_expected")
        ),
        "normalized_actual": jsonable(violation.context.get("normalized_actual")),
        "status": violation.suggested_status,
        "duplicate_candidates": [],
        "verification_history": [
            {
                "commit": engine.commit(),
                "result": violation.actual if isinstance(violation.actual, str) else "hit",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        ],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "notes": violation.detail,
        "strategy": case.meta.get("strategy"),
        "translated_from": case.meta.get("translated_from"),
        "caveats": case.meta.get("caveats") or [],
        "context": jsonable(violation.context),
        "query": case.query,
        "raw_result": result_summary(raw_result) if raw_result else None,
    }
