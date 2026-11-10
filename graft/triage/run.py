"""``python -m triage.run``: reduce, verify, known-difference check, dupecheck.

The pipeline a raw finding walks, in the order P09 fixes:

1. **reduce** - shrink while the same violation keeps firing (``triage.reduce``).
2. **verify** - re-run the reduced case twice on a *fresh* engine
   (``triage.verify``). ``verified`` or ``flaky``.
3. **known difference** - if ``configs/known_differences.yaml`` explains it, it
   is ``known_difference`` and not a bug.
4. **dupecheck** - search upstream read-only; a plausible hit makes it
   ``duplicate_suspect``, otherwise a verified finding becomes
   ``candidate_ready`` (``triage.dupecheck``).

Two limits that keep an unattended run safe: at most 20 findings and 10 minutes
per invocation, and a lock file so two runs cannot process the same finding.
Statuses stop at ``candidate_ready``; ``filed`` and everything after it is a
human's to set, per CLAUDE.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from graft.engines.base import Engine, TestCase
from graft.config import REPO_ROOT, load_targets
from graft.log import JsonlLogger

MAX_FINDINGS = 20
MAX_SECONDS = 600
LOCK_NAME = "triage.lock"

#: statuses this pipeline will pick up
TRIAGE_INPUT_STATUSES = ("new", "reduced")


@dataclass
class TriageStats:
    """What one triage run did."""

    considered: int = 0
    reduced: int = 0
    verified: int = 0
    flaky: int = 0
    known_difference: int = 0
    duplicate_suspect: int = 0
    candidate_ready: int = 0
    skipped: int = 0
    ratios: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready summary."""
        mean = sum(self.ratios) / len(self.ratios) if self.ratios else None
        return {
            "considered": self.considered,
            "reduced": self.reduced,
            "verified": self.verified,
            "flaky": self.flaky,
            "known_difference": self.known_difference,
            "duplicate_suspect": self.duplicate_suspect,
            "candidate_ready": self.candidate_ready,
            "skipped": self.skipped,
            "mean_reduction_ratio": round(mean, 3) if mean is not None else None,
        }


def lock_path() -> Path:
    """Return the lock file path."""
    path = REPO_ROOT / "workdir"
    path.mkdir(parents=True, exist_ok=True)
    return path / LOCK_NAME


class Lock:
    """A best-effort lock so two triage runs do not collide."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or lock_path()
        self.held = False

    def __enter__(self) -> "Lock":
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self.held = False
            return self
        with os.fdopen(handle, "w") as stream:
            stream.write(f"{os.getpid()}\n")
        self.held = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:  # pragma: no cover
                pass


def load_findings(root: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    """Return every finding on disk, with its path."""
    base = root or REPO_ROOT / "findings"
    out: list[tuple[Path, dict[str, Any]]] = []
    if not base.exists():
        return out
    for path in sorted(base.rglob("finding.json")):
        try:
            out.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def case_from(record: dict[str, Any]) -> TestCase:
    """Rebuild the test case a finding describes."""
    query = record.get("query")
    if not query:
        reduced = record.get("reduced_sql")
        if isinstance(reduced, list) and reduced:
            query = reduced[-1]
        else:
            original = record.get("original_sql")
            query = original[-1] if isinstance(original, list) and original else ""
    return TestCase(
        setup=list(record.get("setup") or ()),
        query=str(query),
        origin=str(record.get("origin") or "fuzzer"),
        seed=record.get("seed"),
        meta={"feature_tags": list(record.get("feature_tags") or ())},
    )


def triage_finding(
    path: Path,
    record: dict[str, Any],
    engine_factory: Callable[[str], Engine],
    stats: TriageStats,
    dupecheck_client: Any | None = None,
    logger: JsonlLogger | None = None,
) -> dict[str, Any]:
    """Run the whole pipeline over one finding and write it back."""
    from graft.triage import dupecheck as D
    from graft.triage.reduce import reduce_case, violation_kind
    from graft.triage.verify import STATUS_VERIFIED, history_entry, verify

    target = str(record.get("target") or "")
    case = case_from(record)
    engine = engine_factory(target)
    try:
        baseline = engine.run_case(list(case.setup), case.query)
        expected = violation_kind(baseline)
    finally:
        engine.close()

    # 1. reduce, keeping the same violation
    def still_fires(candidate: TestCase) -> bool:
        probe = engine_factory(target)
        try:
            return violation_kind(
                probe.run_case(list(candidate.setup), candidate.query)
            ) == expected
        except Exception:
            return False
        finally:
            probe.close()

    reduction = reduce_case(case, still_fires)
    record["reduced_sql"] = [*reduction.case.setup, reduction.case.query]
    # Re-measure on the REDUCED case. Carrying the original's recorded values
    # over would put one query in the report beside another query's output, and
    # reduction rewrites subexpressions, so those values are usually wrong for
    # what the report now shows.
    probe = engine_factory(target)
    try:
        measured = probe.run_case(list(reduction.case.setup), reduction.case.query)
        record["raw_actual_reduced"] = (
            measured.error if not measured.ok else repr(measured.rows)
        )
        record["reduced_row_count"] = measured.row_count if measured.ok else None
    finally:
        probe.close()
    record["reduction"] = {
        "executions": reduction.executions,
        "steps_kept": reduction.steps_kept,
        "ratio": round(reduction.ratio, 3),
        "statements": len(reduction.case.setup) + 1,
    }
    record["status"] = "reduced"
    stats.reduced += 1
    stats.ratios.append(reduction.ratio)

    # 2. verify twice on a fresh instance
    verification = verify(
        reduction.case, lambda: engine_factory(target), expected
    )
    record["engine_version"] = verification.engine_version
    record["engine_commit"] = verification.engine_commit
    record.setdefault("verification_history", []).append(history_entry(verification))
    record["status"] = verification.status
    if verification.verified:
        stats.verified += 1
    else:
        stats.flaky += 1
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return record


    # 4. search upstream, read-only
    if dupecheck_client is not None:
        repo = (load_targets().get(target).repo if target in load_targets() else None)
        report = D.search(
            dupecheck_client,
            repo or "",
            tuple(record.get("feature_tags") or ()),
            reduction.case.query,
            str(baseline.error_class),
            baseline.error,
        )
        record["duplicate_candidates"] = report.candidates
        record["duplicate_search"] = {
            "queries": report.queries,
            "searched": report.searched,
            "detail": report.detail,
        }
        if report.suspect:
            record["status"] = D.STATUS_DUPLICATE_SUSPECT
            stats.duplicate_suspect += 1
        else:
            record["status"] = D.STATUS_CANDIDATE_READY
            stats.candidate_ready += 1
    else:
        record["status"] = D.STATUS_CANDIDATE_READY
        stats.candidate_ready += 1

    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if logger is not None:
        logger.log(
            "triaged",
            target=target,
            signature=record.get("coarse_signature"),
            status=record["status"],
            ratio=record["reduction"]["ratio"],
        )
    return record


def run(
    findings_root: Path | None = None,
    engine_factory: Callable[[str], Engine] | None = None,
    dupecheck_client: Any | None = None,
    max_findings: int = MAX_FINDINGS,
    max_seconds: float = MAX_SECONDS,
    logger: JsonlLogger | None = None,
) -> TriageStats:
    """Triage up to ``max_findings`` findings within ``max_seconds``."""
    from graft.engines import create_engine

    factory = engine_factory or (lambda name: create_engine(name))
    stats = TriageStats()
    started = time.monotonic()

    for path, record in load_findings(findings_root):
        if stats.considered >= max_findings:
            break
        if time.monotonic() - started >= max_seconds:
            break
        if str(record.get("status")) not in TRIAGE_INPUT_STATUSES:
            stats.skipped += 1
            continue
        stats.considered += 1
        try:
            triage_finding(
                path, record, factory, stats, dupecheck_client, logger
            )
        except Exception as error:  # one bad finding must not stop triage
            record["notes"] = (
                f"triage failed: {type(error).__name__}: {error}"
            )
            path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="triage raw findings")
    parser.add_argument("--max-findings", type=int, default=MAX_FINDINGS)
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    parser.add_argument(
        "--no-dupecheck", action="store_true", help="skip the upstream search"
    )
    args = parser.parse_args(argv)

    with Lock() as lock:
        if not lock.held:
            print(f"{lock.path} is held; another triage run is in progress.")
            return 3
        client = None
        if not args.no_dupecheck:
            from graft.corpus.github import GitHubClient

            client = GitHubClient(request_budget=60)
            if not client.authenticated:
                print(
                    "GITHUB_TOKEN is not set; duplicate search will use the "
                    "unauthenticated rate limit (10 searches/minute).",
                    flush=True,
                )
        logger = JsonlLogger("triage")
        try:
            stats = run(
                dupecheck_client=client,
                max_findings=args.max_findings,
                max_seconds=args.max_seconds,
                logger=logger,
            )
        finally:
            logger.close()

    print("=== triage summary ===")
    for key, value in stats.as_dict().items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
