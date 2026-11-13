"""The crash oracle catches its planted bug, and is silent when the engine is clean.

This is the test that makes a zero-hit campaign mean something. A run over
hundreds of thousands of cases that reports nothing is worthless evidence unless
the oracle is known to fire when a defect really is present. So the planted
defect below is checked against the oracle that should notice it, and a clean
engine is checked against the cases that must stay silent.

``PlantedBugEngine`` is a deliberately defective engine backed by Python's
stdlib ``sqlite3``; it exists only for this test.
"""

from __future__ import annotations

import pytest

from graft.engines.base import TestCase
from graft.engines.fake import BUG_CRASH_ON_FUNCTION, PlantedBugEngine
from graft.oracle.crash import CrashOracle

SETUP = ["CREATE TABLE t(a INTEGER)", "INSERT INTO t VALUES (1),(2),(NULL)"]


@pytest.fixture
def clean():
    engine = PlantedBugEngine()
    engine.start()
    yield engine
    engine.close()


def engine_with(*bugs):
    engine = PlantedBugEngine(bugs=bugs)
    engine.start()
    return engine


def run_crash(engine, query="SELECT abs(a) FROM t"):
    result = engine.run_case(SETUP, query)
    return CrashOracle().inspect(engine.name, result, TestCase(setup=SETUP, query=query))


def test_the_crash_oracle_catches_a_planted_crash():
    engine = engine_with(BUG_CRASH_ON_FUNCTION)
    try:
        violations = run_crash(engine)
        assert len(violations) == 1
        assert violations[0].oracle == "crash"
        assert violations[0].suggested_status == "new"
        assert "planted crash" in str(violations[0].actual)
    finally:
        engine.close()


def test_the_crash_oracle_is_silent_on_a_correct_engine(clean):
    for query in ("SELECT abs(a) FROM t", "SELECT a FROM t", "SELECT count(*) FROM t"):
        assert run_crash(clean, query) == []


def test_the_crash_oracle_is_silent_on_invalid_sql(clean):
    """A user_error on invalid SQL is never a violation."""
    assert run_crash(clean, "SELECT * FROM no_such_table") == []
    assert run_crash(clean, "SELCT bad syntax") == []
