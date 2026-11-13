"""Tests for the campaign runner: robustness, STOP, stats, and memory growth."""

from __future__ import annotations

import json
import re
import resource
import time

import pytest

from graft.engines import errors
from graft.engines.base import Result, TestCase
from graft.engines.fake import FakeEngine, PlantedBugEngine
from graft import campaign as F


@pytest.fixture
def no_stop(tmp_path, monkeypatch):
    """Point workdir at a temp directory so STOP cannot leak between tests."""
    monkeypatch.setattr(F, "workdir", lambda: tmp_path)
    return tmp_path


def campaign(tmp_path, engine, **kwargs):
    kwargs.setdefault("mode", F.MODE_FUZZER)
    kwargs.setdefault("templates", [])
    return F.Campaign(
        "turso", findings_root=tmp_path / "findings",
        engines={"turso": engine, "sqlite": engine}, **kwargs
    )


# --- the unrunnable filter -------------------------------------------------


@pytest.mark.parametrize(
    "query,runnable",
    [
        ("SELECT a FROM t", True),
        ("SELECT $customer", False),
        ("SELECT * FROM generate_series(-9223372036854775808,-2)", False),
        ("SELECT * FROM generate_series(1,3)", True),
        ("SELECT * FROM range(10)", True),
    ],
)
def test_unrunnable_cases_are_skipped(query, runnable):
    assert F.is_runnable([], query) is runnable



# --- mode and configuration ----------------------------------------------


def test_an_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="mode must be one of"):
        F.Campaign("turso", mode="sideways", engines={}, templates=[])


def test_every_mode_is_accepted(tmp_path):
    for mode in F.MODES:
        assert F.Campaign(
            "turso", mode=mode, engines={}, templates=[],
            findings_root=tmp_path / mode,
        ).mode == mode


# --- robustness -----------------------------------------------------------


def test_a_failing_case_does_not_stop_the_loop(no_stop, tmp_path, monkeypatch):
    """P08 task 3: catch every per-case exception, log it, continue."""
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)

    calls = {"n": 0}

    def exploding(rng):
        calls["n"] += 1
        if calls["n"] % 2:
            raise RuntimeError("planted generator failure")
        return ["CREATE TABLE t(a INTEGER)"], "SELECT count(*) FROM t", None, ("agg:count",)

    monkeypatch.setattr(run, "next_case", exploding)
    try:
        stats = run.run(budget_min=0, max_cases=10)
    finally:
        engine.close()
    assert stats.cases == 10
    assert stats.errors == 5          # every other case raised
    assert stats.executions > 0       # and the others still ran


def test_a_crashed_engine_is_dropped_and_rebuilt(no_stop, tmp_path):
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    case = TestCase(setup=[], query="SELECT 1")
    crashed = Result.failure("segfault", errors.CRASH)
    run.stats.last_seed = 1
    for violation in run.crash.inspect("turso", crashed, case):
        run.record(violation, engine, ("clause:select",), crashed, None)
    before = run.stats.restarts
    run.drop_engine("turso")
    assert run.stats.restarts == before + 1
    # the next call rebuilds rather than raising
    assert run.engine("turso") is not None
    run.close()


def test_the_stop_file_ends_the_run_between_cases(no_stop, tmp_path):
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    (no_stop / F.STOP_FILENAME).touch()
    try:
        stats = run.run(budget_min=0, max_cases=1000)
    finally:
        engine.close()
    assert stats.cases == 0, "STOP was not honoured before the first case"
    assert F.stop_requested()


def test_the_budget_ends_the_run(no_stop, tmp_path):
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    started = time.monotonic()
    try:
        run.run(budget_min=0.01, max_cases=10**9)
    finally:
        engine.close()
    assert time.monotonic() - started < 20


# --- findings and signatures ---------------------------------------------


def test_a_repeated_signature_is_counted_not_rewritten(no_stop, tmp_path):
    """P08 task 1: never write the same coarse signature twice."""
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    case = TestCase(setup=[], query="SELECT 1")
    result = Result.failure("INTERNAL Error: boom", errors.INTERNAL_ERROR)
    try:
        for _ in range(4):
            for violation in run.crash.inspect("turso", result, case):
                run.record(violation, engine, ("clause:select",), result, None)
    finally:
        engine.close()
    findings = list((tmp_path / "findings").rglob("finding.json"))
    assert len(findings) == 1
    assert run.stats.findings_written == 1
    assert run.stats.hits["crash"] == 4
    repeats = findings[0].parent / "repeats.txt"
    assert repeats.exists()
    assert int(repeats.read_text().strip()) == 3


def test_a_finding_records_the_pinned_commit(no_stop, tmp_path):
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    case = TestCase(setup=[], query="SELECT 1")
    result = Result.failure("INTERNAL Error: boom", errors.INTERNAL_ERROR)
    try:
        for violation in run.crash.inspect("turso", result, case):
            run.record(violation, engine, ("clause:select",), result, None)
    finally:
        engine.close()
    record = json.loads(
        next((tmp_path / "findings").rglob("finding.json")).read_text()
    )
    assert "pinned_commit" in record
    assert record["campaign_mode"] == F.MODE_FUZZER
    assert record["engine_commit"]


# --- stats ----------------------------------------------------------------


def test_the_stats_line_has_everything_spec_asks_for(no_stop, tmp_path):
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    try:
        run.run(budget_min=0, max_cases=3)
    finally:
        engine.close()
    tick = run.stats.tick()
    for key in (
        "qps", "valid_rate", "hits", "unique_coarse_signatures", "restarts",
        "cases", "executions",
        "skipped", "errors", "last_seed",
    ):
        assert key in tick, key
    assert json.dumps(tick)  # it has to be one JSON line


def test_a_stats_line_is_written_and_the_seed_persisted(no_stop, tmp_path):
    engine = PlantedBugEngine()
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine, seed_start=100)
    stats_path = tmp_path / "stats.jsonl"
    heartbeat = tmp_path / "beat"
    try:
        run.run(budget_min=0, max_cases=2, stats_path=stats_path,
                heartbeat_path=heartbeat)
    finally:
        engine.close()
    lines = stats_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    assert json.loads(lines[-1])["target"] == "turso"
    assert int((no_stop / "turso.seed").read_text().strip()) == 102


# --- memory ---------------------------------------------------------------


def test_five_thousand_iterations_grow_memory_by_under_fifty_megabytes(
    no_stop, tmp_path, monkeypatch
):
    """P08 task 3, verbatim: 5000 iterations must grow memory by under 50 MB.

    A campaign is meant to run for days, so a per-case leak of even a few
    kilobytes would end it. The fake engine keeps the loop itself under test
    rather than a real engine's allocator.
    """
    engine = FakeEngine(default=Result.success(rows=[(1,)]))
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)

    def cheap(rng):
        return ["CREATE TABLE t(a INTEGER)"], "SELECT count(*) FROM t", "a > 0", ("agg:count",)

    monkeypatch.setattr(run, "next_case", cheap)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        stats = run.run(budget_min=0, max_cases=5000)
    finally:
        engine.close()
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    grown_mb = (after - before) / 1024  # ru_maxrss is KiB on Linux
    assert stats.cases == 5000
    assert grown_mb < 50, f"grew {grown_mb:.1f} MB over 5000 iterations"


def test_the_fake_engine_loop_executes_every_case(no_stop, tmp_path, monkeypatch):
    engine = FakeEngine(default=Result.success(rows=[(1,)]))
    engine.name = "turso"
    engine.start()
    run = campaign(tmp_path, engine)
    monkeypatch.setattr(
        run, "next_case",
        lambda rng: ([], "SELECT count(*) FROM t", None, ("agg:count",)),
    )
    try:
        stats = run.run(budget_min=0, max_cases=50)
    finally:
        engine.close()
    assert stats.cases == 50
    assert stats.valid == 50
    assert stats.errors == 0
