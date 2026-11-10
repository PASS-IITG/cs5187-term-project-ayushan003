"""The unattended campaign runner: generate, execute, judge, record, repeat.

This is the step that connects the pieces. Until now the oracles were proven to
catch planted bugs (``tests/test_oracles_planted.py``) and the generator was
proven to produce near-100-percent-valid SQL (``gen/sample.py``), but nothing
fed one to the other: the removed variant-hunter stage only synthesised queries over
tables a mined repro happened to create, which is the narrow search space two
sessions of measurement found nothing in.

Design points that matter for running for days unattended:

* **Pinned commits.** The engine commit recorded in every finding is read from
  the running binary, with ``configs/targets.yaml``'s ``pinned_commit``
  alongside, so a finding always says what it was found on.
* **One finding per coarse signature.** A repeat increments a counter file
  rather than rewriting the finding, so a single noisy defect cannot fill the
  disk or drown the interesting ones.
* **Nothing kills the loop.** Every per-case failure is caught and logged; a
  dead adapter is closed and rebuilt on the next case.
* **Clean stop.** ``workdir/STOP`` makes the runner exit between cases, so a
  campaign can be stopped without losing the case in flight.
* **Resumable.** The seed is the state. ``--seed-start`` continues where a
  previous run left off, and the last seed used is written to
  ``workdir/<target>.seed`` every stats tick.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from graft.engines import create_engine
from graft.engines.base import Engine, Result, TestCase
from graft.config import REPO_ROOT, Target, load_targets
from graft.log import JsonlLogger, log_dir
from graft.generate.query import generate_case
from graft.corpus.templates import Template, instantiate, load_templates, weighted_choice
from graft.oracle.crash import CrashOracle
from graft.triage.features import feature_tags
from graft.triage.signature import coarse_signature
from graft.generate.capabilities import get_capabilities

#: how often the heartbeat file is refreshed
HEARTBEAT_INTERVAL_S = 30.0
#: how often a stats line is appended
STATS_INTERVAL_S = 60.0
#: the file whose existence asks the runner to stop
STOP_FILENAME = "STOP"

#: a template can carry a bind placeholder or an unbounded table function from
#: the mined repro it came from; neither can produce a meaningful result
_UNRUNNABLE = (
    re.compile(r"[$][A-Za-z_0-9]+"),
    re.compile(r"\b(?:generate_series|range)\s*\(\s*-?\d{10,}", re.IGNORECASE),
)


def is_runnable(setup: Sequence[str], query: str) -> bool:
    """False if the case can never produce a meaningful result."""
    text = "\n".join([*setup, query])
    return not any(pattern.search(text) for pattern in _UNRUNNABLE)


MODE_FUZZER = "fuzzer"
MODE_VARIANTS = "variants"
MODE_BOTH = "both"
MODES = (MODE_FUZZER, MODE_VARIANTS, MODE_BOTH)


def workdir() -> Path:
    """Return the git-ignored working directory."""
    path = REPO_ROOT / "workdir"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stop_requested() -> bool:
    """True once ``workdir/STOP`` exists."""
    return (workdir() / STOP_FILENAME).exists()


@dataclass
class CampaignStats:
    """Live counters, written to ``logs/<target>.stats.jsonl`` every minute."""

    target: str = ""
    cases: int = 0
    skipped: int = 0
    valid: int = 0
    executions: int = 0
    errors: int = 0
    restarts: int = 0
    hits: Counter = field(default_factory=Counter)
    signatures: set[str] = field(default_factory=set)
    findings_written: int = 0
    started: float = field(default_factory=time.monotonic)
    last_seed: int = 0

    @property
    def elapsed_s(self) -> float:
        """Seconds since the campaign started."""
        return time.monotonic() - self.started

    def tick(self) -> dict[str, Any]:
        """Return the stats line SPEC asks for."""
        elapsed = max(self.elapsed_s, 1e-9)
        return {
            "target": self.target,
            "qps": round(self.executions / elapsed, 3),
            "valid_rate": round(self.valid / self.cases, 4) if self.cases else 0.0,
            "cases": self.cases,
            "skipped": self.skipped,
            "executions": self.executions,
            "hits": sum(self.hits.values()),
            "hits_by_oracle": dict(self.hits),
            "unique_coarse_signatures": len(self.signatures),
            "findings_written": self.findings_written,
            "restarts": self.restarts,
            "errors": self.errors,
            "last_seed": self.last_seed,
            "elapsed_s": round(self.elapsed_s, 1),
        }


class Campaign:
    """One target's fuzzing campaign."""

    def __init__(
        self,
        target: str,
        seed_start: int = 0,
        mode: str = MODE_BOTH,
        findings_root: Path | None = None,
        engines: dict[str, Engine] | None = None,
        templates: Sequence[Template] | None = None,
    ) -> None:
        """Set a campaign up. Engines may be injected for tests."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.target = target
        self.seed = seed_start
        self.mode = mode
        self.findings_root = findings_root or REPO_ROOT / "findings"
        self.stats = CampaignStats(target=target)
        self.crash = CrashOracle()
        self.label = target
        self._engines: dict[str, Engine] = dict(engines or {})
        self._owns_engines = engines is None
        self.capabilities = get_capabilities(target)
        self.targets: dict[str, Target] = load_targets()
        self.templates = (
            list(templates)
            if templates is not None
            else (load_templates() if mode in (MODE_VARIANTS, MODE_BOTH) else [])
        )

    # --- engines ------------------------------------------------------------

    def engine(self, name: str) -> Engine:
        """Return a started engine, rebuilding it if it was dropped."""
        engine = self._engines.get(name)
        if engine is None:
            engine = create_engine(name)
            self._engines[name] = engine
        return engine

    def drop_engine(self, name: str) -> None:
        """Close and forget an engine, so the next case gets a fresh one."""
        engine = self._engines.pop(name, None)
        if engine is None:
            return
        self.stats.restarts += 1
        try:
            engine.close()
        except Exception:  # pragma: no cover - closing a dead engine
            pass

    def close(self) -> None:
        """Close every engine this campaign owns."""
        if not self._owns_engines:
            return
        for engine in list(self._engines.values()):
            try:
                engine.close()
            except Exception:  # pragma: no cover
                pass
        self._engines.clear()


    # --- recording ----------------------------------------------------------

    def record(
        self,
        violation: Any,
        engine: Engine,
        tags: Sequence[str],
        result: Result | None,
        logger: JsonlLogger | None,
    ) -> None:
        """Write a finding, or count a repeat of one already on disk."""
        error_text = result.error if (result is not None and not result.ok) else None
        signature = coarse_signature(
            target=engine.name,
            oracle=violation.oracle,
            error_class=(result.error_class if result is not None else "none"),
            error_text=error_text,
            feature_tags=tags,
        )
        self.stats.hits[violation.oracle] += 1
        self.stats.signatures.add(signature)
        directory = self.findings_root / engine.name / signature
        path = directory / "finding.json"
        if path.exists():
            # SPEC/P08: never write the same signature twice; count it instead.
            counter = directory / "repeats.txt"
            try:
                previous = int(counter.read_text().strip() or 0)
            except (OSError, ValueError):
                previous = 0
            counter.write_text(f"{previous + 1}\n", encoding="utf-8")
            return

        from graft.triage.finding import build_finding

        directory.mkdir(parents=True, exist_ok=True)
        record = build_finding(
            violation,
            engine.name,
            engine,
            signature,
            tags,
            self.stats.last_seed,
            violation.test_case.meta.get("origin_issue"),
            result,
        )
        record["pinned_commit"] = (
            self.targets[engine.name].pinned_commit
            if engine.name in self.targets
            else None
        )
        record["campaign_mode"] = self.mode
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.stats.findings_written += 1
        if logger is not None:
            logger.log(
                "finding",
                target=engine.name,
                oracle=violation.oracle,
                signature=signature,
                status=violation.suggested_status,
                seed=self.stats.last_seed,
            )

    # --- one case -----------------------------------------------------------

    def execute(self, engine: Engine, setup: Sequence[str], query: str) -> Result:
        """Run one statement set, counting the execution."""
        self.stats.executions += 1
        return engine.run_case(list(setup), query, timeout=engine.timeout_s)

    def run_case(
        self,
        setup: Sequence[str],
        query: str,
        tlp_predicate: str | None,
        tags: Sequence[str],
        logger: JsonlLogger | None = None,
    ) -> None:
        """Run one case against every oracle that applies."""
        engine = self.engine(self.target)
        result = self.execute(engine, setup, query)
        if result.ok:
            self.stats.valid += 1

        case = TestCase(setup=list(setup), query=query, origin="fuzzer",
                        seed=self.stats.last_seed, meta={"feature_tags": list(tags)})

        for violation in self.crash.inspect(engine.name, result, case):
            violation.feature_tags = tuple(tags)
            self.record(violation, engine, tags, result, logger)
        if result.error_class in ("crash", "panic"):
            self.drop_engine(self.target)
            return


    def next_case(self, rng: random.Random) -> tuple[list[str], str, str | None, tuple[str, ...]]:
        """Return the next case, from the generator or from a template."""
        use_template = (
            self.mode == MODE_VARIANTS
            or (self.mode == MODE_BOTH and self.templates and rng.random() < 0.3)
        )
        if use_template and self.templates:
            template = weighted_choice(self.templates, rng)
            if template is not None:
                setup, query = instantiate(template, rng)
                tags = feature_tags([*setup, query])
                return setup, query, None, tags
        # Half the generated cases are kept inside the family TLP and NoREC can
        # check. The first campaign reached a wrong-result oracle on only 31
        # percent of cases, because the richer shapes are refused by the gate -
        # and the wrong-result oracles are the ones that find wrong results.
        recombinable = rng.random() < 0.5
        setup, generated, _ = generate_case(rng, self.capabilities, recombinable)
        return setup, generated.sql, generated.tlp_predicate, generated.tags

    # --- the loop -----------------------------------------------------------

    def run(
        self,
        budget_min: float = 0.0,
        logger: JsonlLogger | None = None,
        stats_path: Path | None = None,
        heartbeat_path: Path | None = None,
        max_cases: int | None = None,
    ) -> CampaignStats:
        """Fuzz until the budget runs out, ``workdir/STOP`` appears, or forever.

        ``budget_min=0`` means no time limit, which is what a multi-day
        campaign uses.
        """
        deadline = None if budget_min <= 0 else time.monotonic() + budget_min * 60
        last_heartbeat = 0.0
        last_stats = time.monotonic()
        seed_file = workdir() / f"{self.label}.seed"

        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            if max_cases is not None and self.stats.cases >= max_cases:
                break
            if stop_requested():
                break

            self.stats.last_seed = self.seed
            rng = random.Random(self.seed)
            self.seed += 1
            self.stats.cases += 1
            try:
                setup, query, tlp_predicate, tags = self.next_case(rng)
                if not is_runnable(setup, query):
                    self.stats.skipped += 1
                    continue
                self.run_case(setup, query, tlp_predicate, tags, logger)
            except Exception as error:  # nothing may kill the loop
                self.stats.errors += 1
                if logger is not None:
                    logger.log(
                        "case_error",
                        target=self.target,
                        seed=self.stats.last_seed,
                        error=f"{type(error).__name__}: {error}",
                        traceback=traceback.format_exc(limit=4),
                    )
                self.drop_engine(self.target)

            now = time.monotonic()
            if heartbeat_path is not None and now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                heartbeat_path.write_text(
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n",
                    encoding="utf-8",
                )
                last_heartbeat = now
            if stats_path is not None and now - last_stats >= STATS_INTERVAL_S:
                with stats_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(self.stats.tick()) + "\n")
                seed_file.write_text(f"{self.seed}\n", encoding="utf-8")
                last_stats = now

        if stats_path is not None:
            with stats_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self.stats.tick()) + "\n")
        seed_file.write_text(f"{self.seed}\n", encoding="utf-8")
        return self.stats


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point for ``python -m runner.fuzz``."""
    parser = argparse.ArgumentParser(description="unattended fuzz campaign")
    parser.add_argument("--target", default="turso")
    parser.add_argument(
        "--budget-min", type=float, default=0.0, help="0 means run until stopped"
    )
    parser.add_argument("--seed-start", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--no-feedback", action="store_true")
    parser.add_argument("--mode", default=MODE_BOTH, choices=MODES)
    parser.add_argument("--max-cases", type=int, default=None)
    args = parser.parse_args(argv)

    if args.workers != 1:
        print(
            f"--workers {args.workers} requested; one campaign is single-threaded, "
            "so run one process per target instead (scripts/run_bg.sh does).",
            flush=True,
        )
    if args.no_feedback:
        print("--no-feedback: no feedback loop is implemented yet (P10).", flush=True)

    seed_file = workdir() / f"{args.target}.seed"
    if args.seed_start is not None:
        seed_start = args.seed_start
    else:
        try:
            seed_start = int(seed_file.read_text().strip())
        except (OSError, ValueError):
            seed_start = 0

    stop = workdir() / STOP_FILENAME
    if stop.exists():
        print(f"{stop} exists; remove it before starting a campaign.", flush=True)
        return 2

    label = args.target
    logger = JsonlLogger(f"{label}.fuzz")
    stats_path = log_dir() / f"{label}.stats.jsonl"
    heartbeat_path = log_dir() / f"{label}.heartbeat"
    campaign = Campaign(args.target, seed_start=seed_start, mode=args.mode)
    print(
        f"campaign: target={args.target} mode={args.mode} seed_start={seed_start} "
        f"budget={'forever' if args.budget_min <= 0 else str(args.budget_min) + 'min'} "
        f"templates={len(campaign.templates)}",
        flush=True,
    )
    try:
        stats = campaign.run(
            budget_min=args.budget_min,
            logger=logger,
            stats_path=stats_path,
            heartbeat_path=heartbeat_path,
            max_cases=args.max_cases,
        )
    finally:
        campaign.close()
        logger.close()

    summary = stats.tick()
    print("\n=== campaign summary ===")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
