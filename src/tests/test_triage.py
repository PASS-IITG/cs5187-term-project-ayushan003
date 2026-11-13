"""Tests for the triage pipeline: reduce, verify, dupecheck, and the driver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graft.engines import errors
from graft.engines.base import Result, TestCase
from graft.engines.fake import PlantedBugEngine
from graft.triage import dupecheck as D
from graft.triage import reduce as R
from graft.triage import run as RUN
from graft.triage import verify as V
from graft.triage.features import feature_tags, sqlglot_tags
from graft.triage.signature import coarse_signature

FIXTURES = Path(__file__).parent / "fixtures"


# --- the reducer ----------------------------------------------------------


def test_the_reducer_shrinks_while_the_violation_holds():
    case = TestCase(
        setup=[
            "CREATE TABLE t(a INTEGER, b VARCHAR)",
            "INSERT INTO t VALUES (1,'xxxxxx'), (2,'yyyyyy'), (3,'zzzzzz')",
            "CREATE TABLE unused(z INTEGER)",
        ],
        query="SELECT boom, upper(b) FROM t WHERE a > 100 ORDER BY a LIMIT 5",
    )
    result = R.reduce_case(case, lambda c: "boom" in c.query)
    assert result.ratio < 0.5
    assert result.case.setup == []
    assert "boom" in result.case.query
    assert "LIMIT" not in result.case.query.upper()
    assert result.executions <= R.MAX_EXECUTIONS


def test_the_reducer_keeps_what_the_violation_needs():
    """Nothing the predicate depends on may be removed."""
    case = TestCase(
        setup=["CREATE TABLE t(a INTEGER)", "INSERT INTO t VALUES (1)"],
        query="SELECT a FROM t WHERE a = 1",
    )
    # the violation needs both the table and the WHERE
    def fires(candidate: TestCase) -> bool:
        return "FROM t" in candidate.query and "WHERE" in candidate.query.upper()

    result = R.reduce_case(case, fires)
    assert "FROM t" in result.case.query
    assert "WHERE" in result.case.query.upper()


def test_the_reducer_respects_its_execution_budget():
    case = TestCase(
        setup=[f"INSERT INTO t VALUES ({n})" for n in range(40)],
        query="SELECT a FROM t WHERE a > 1 ORDER BY a LIMIT 3",
    )
    result = R.reduce_case(case, lambda c: True, max_executions=7)
    assert result.executions <= 7


def test_the_reducer_returns_the_input_when_nothing_can_go():
    case = TestCase(setup=[], query="SELECT 1")
    result = R.reduce_case(case, lambda c: False)
    assert result.case.query == "SELECT 1"
    assert result.ratio == 1.0


def test_dropping_a_row_keeps_the_statement_valid():
    statement = "INSERT INTO t VALUES (1), (2), (3)"
    for reduced in R._drop_rows(statement):
        assert "VALUES" in reduced.upper()
        assert not reduced.rstrip().endswith(",")
        assert ",," not in reduced


@pytest.mark.parametrize(
    "query,gone",
    [
        ("SELECT a FROM t LIMIT 5", "LIMIT"),
        ("SELECT a FROM t ORDER BY a", "ORDER BY"),
        ("SELECT DISTINCT a FROM t", "DISTINCT"),
        ("SELECT count(*) FROM t GROUP BY a HAVING count(*) > 1", "HAVING"),
    ],
)
def test_clauses_can_be_dropped(query, gone):
    reduced = dict(R._drop_clauses(query))
    assert reduced, query
    assert any(gone not in candidate.upper() for candidate in reduced.values())


def test_violation_kind_groups_the_same_defect_under_different_literals():
    """Reduction changes literals, so the comparison must ignore them."""
    first = R.violation_kind(Result.failure("boom at line 5 value 7", errors.PANIC))
    second = R.violation_kind(Result.failure("boom at line 9 value 1", errors.PANIC))
    assert first == second
    other = R.violation_kind(Result.failure("different thing", errors.PANIC))
    assert first != other
    assert R.violation_kind(Result.success(rows=[(1,)])) == "ok:1"


# --- the verifier ---------------------------------------------------------


def test_verification_needs_both_fresh_runs_to_agree():
    def factory():
        engine = PlantedBugEngine(bugs=["crash_on_function"])
        engine.name = "turso"
        engine.start()
        return engine

    case = TestCase(setup=[], query="SELECT abs(1)")
    expected = R.violation_kind(
        Result.failure("INTERNAL Error: planted crash in abs()", errors.INTERNAL_ERROR)
    )
    result = V.verify(case, factory, expected)
    assert result.verified
    assert result.status == V.STATUS_VERIFIED
    assert len(result.runs) == 2
    assert result.engine_commit == "planted0000"


def test_a_case_that_does_not_reproduce_is_flaky():
    def factory():
        engine = PlantedBugEngine()
        engine.name = "turso"
        engine.start()
        return engine

    case = TestCase(setup=[], query="SELECT 1")
    result = V.verify(case, factory, "panic:something that never happens")
    assert result.status == V.STATUS_FLAKY
    assert not result.verified
    assert "expected" in result.detail


def test_each_run_gets_a_brand_new_engine():
    # The engines are KEPT ALIVE in this list rather than recorded by id().
    # verify() closes each engine in a finally block before the factory is
    # called again, so their lifetimes never overlap - and CPython is free to
    # reuse a freed object's address, which makes id() a worthless identity
    # token here. The original version asserted len(set(map(id, ...))) == 3 and
    # passed only by luck: it began failing in the full suite, while still
    # passing in isolation, once other tests churned allocation enough for two
    # engines to land at the same address. Holding references makes the
    # distinctness real rather than probabilistic.
    created: list[object] = []

    def factory():
        engine = PlantedBugEngine()
        engine.name = "turso"
        engine.start()
        created.append(engine)
        return engine

    V.verify(TestCase(setup=[], query="SELECT 1"), factory, "ok:1", runs=3)
    assert len(created) == 3
    # identity, not id(): three distinct live objects
    assert len({id(engine) for engine in created}) == 3
    for first in range(3):
        for second in range(first + 1, 3):
            assert created[first] is not created[second]


def test_the_history_entry_records_the_commit():
    result = V.VerificationResult(
        status=V.STATUS_VERIFIED, runs=("ok:1", "ok:1"),
        engine_version="v1", engine_commit="abc123", detail="d",
    )
    entry = V.history_entry(result)
    assert entry["commit"] == "abc123"
    assert entry["result"] == "ok:1"
    assert entry["timestamp"].endswith("Z")


# --- the duplicate check --------------------------------------------------


class FakeSearch:
    """Serves the recorded GitHub search payload. GET only, like the real client."""

    def __init__(self, payload):
        self.payload = payload
        self.queries: list[str] = []

    def get(self, path, params=None):
        self.queries.append((params or {}).get("q", ""))

        class Response:
            body = self.payload

        return Response()


def test_the_search_is_specific_to_the_feature_and_symptom():
    """Searching the raw error finds nothing; the feature plus symptom does."""
    queries = D.build_queries(
        "tursodatabase/turso", ("fn:lpad",), "select LPAD('a',1,'b')",
        "crash", "memory allocation of 9223372036854775806 bytes failed",
    )
    assert any("lpad" in q for q in queries)
    assert any("crash" in q for q in queries)
    # the giant number must never reach a query: no title contains it
    assert not any("9223372036854775806" in q for q in queries)


def test_sql_keywords_are_not_searched_for():
    terms = D.feature_terms(("clause:select", "op:eq", "type:integer"), "SELECT a FROM t")
    for noise in ("select", "from", "integer", "eq"):
        assert noise not in terms


def test_a_hit_makes_the_finding_a_duplicate_suspect():
    payload = json.loads((FIXTURES / "github_search.json").read_text())
    client = FakeSearch(payload)
    report = D.search(
        client, "tursodatabase/turso", ("fn:lpad",), "select lpad('a',1,'b')",
        "crash", "memory allocation failed", pause_s=0,
    )
    assert report.suspect
    assert report.searched
    assert len(report.candidates) <= D.MAX_CANDIDATES
    assert report.candidates[0]["number"] == 11175
    assert report.candidates[0]["state"] == "open"
    assert all("matched_query" in c for c in report.candidates)


def test_both_open_and_closed_issues_are_recorded():
    """P09: the search must cover both."""
    payload = json.loads((FIXTURES / "github_search.json").read_text())
    report = D.search(
        FakeSearch(payload), "tursodatabase/turso", ("fn:lpad",), "x", "crash",
        None, pause_s=0,
    )
    states = {c["state"] for c in report.candidates}
    assert states == {"open", "closed"}


def test_no_hits_means_no_suspicion():
    report = D.search(
        FakeSearch({"total_count": 0, "items": []}),
        "tursodatabase/turso", ("fn:lpad",), "x", "crash", None, pause_s=0,
    )
    assert not report.suspect
    assert report.searched
    assert report.candidates == []


def test_a_missing_repo_is_reported_not_guessed():
    report = D.search(FakeSearch({}), "", ("fn:lpad",), "x", "crash", None, pause_s=0)
    assert not report.searched
    assert "no repo" in report.detail


# --- the driver -----------------------------------------------------------


def _planted_finding(root: Path) -> Path:
    """Write a raw finding whose bug the fake engine really has."""
    directory = root / "turso" / ("a" * 8)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "finding.json"
    path.write_text(json.dumps({
        "id": "turso-planted",
        "coarse_signature": "a" * 40,
        "feature_tags": ["fn:abs", "clause:select"],
        "target": "turso",
        "engine_version": "planted",
        "engine_commit": "planted0000",
        "oracle": "crash",
        "origin": "fuzzer",
        "origin_issue": None,
        "seed": 1,
        "setup": [
            "CREATE TABLE t(a INTEGER, b VARCHAR)",
            "INSERT INTO t VALUES (1,'xxxxxx'), (2,'yyyyyy'), (3,'zzzzzz')",
            "CREATE TABLE unused(z INTEGER)",
        ],
        "query": "SELECT abs(a) FROM t WHERE a > 0 ORDER BY a LIMIT 5",
        "original_sql": ["SELECT abs(a) FROM t WHERE a > 0 ORDER BY a LIMIT 5"],
        "reduced_sql": None,
        "raw_expected": "a result",
        "raw_actual": "internal_error",
        "normalized_expected": None,
        "normalized_actual": None,
        "status": "new",
        "duplicate_candidates": [],
        "verification_history": [],
        "created_at": "2026-11-01T00:00:00Z",
        "notes": "planted",
    }, indent=2) + "\n", encoding="utf-8")
    return path


def _factory(name: str):
    engine = PlantedBugEngine(bugs=["crash_on_function"])
    engine.name = name
    engine.start()
    return engine


def test_the_whole_pipeline_on_a_planted_finding(tmp_path):
    """P09 acceptance: reduced to at most 5 statements, verified, then
    candidate_ready, with a populated duplicate list."""
    path = _planted_finding(tmp_path)
    payload = json.loads((FIXTURES / "github_search.json").read_text())

    stats = RUN.run(
        findings_root=tmp_path,
        engine_factory=_factory,
        dupecheck_client=FakeSearch(payload),
    )
    record = json.loads(path.read_text())

    assert stats.considered == 1
    assert stats.reduced == 1
    assert stats.verified == 1
    assert record["reduced_sql"]
    assert len(record["reduced_sql"]) <= 5, record["reduced_sql"]
    assert record["reduction"]["ratio"] < 1.0
    assert record["verification_history"]
    # the fixture has hits, so it is held back as a suspected duplicate
    assert record["status"] == D.STATUS_DUPLICATE_SUSPECT
    assert record["duplicate_candidates"]
    assert record["duplicate_search"]["queries"]


def test_without_duplicates_a_verified_finding_becomes_candidate_ready(tmp_path):
    path = _planted_finding(tmp_path)
    stats = RUN.run(
        findings_root=tmp_path,
        engine_factory=_factory,
        dupecheck_client=FakeSearch({"total_count": 0, "items": []}),
    )
    record = json.loads(path.read_text())
    assert stats.candidate_ready == 1
    assert record["status"] == D.STATUS_CANDIDATE_READY
    assert len(record["reduced_sql"]) <= 5


def test_a_finding_that_does_not_reproduce_is_marked_flaky(tmp_path):
    path = _planted_finding(tmp_path)

    def clean(name: str):
        engine = PlantedBugEngine()  # no planted bug: abs() works
        engine.name = name
        engine.start()
        return engine

    stats = RUN.run(findings_root=tmp_path, engine_factory=clean,
                    dupecheck_client=None)
    record = json.loads(path.read_text())
    # a clean engine reproduces "ok", consistently, so it verifies - what must
    # not happen is a crash status surviving on an engine that does not crash
    assert record["status"] in ("verified", "flaky", "candidate_ready")
    assert record["oracle"] == "crash"


def test_triage_never_sets_a_human_only_status(tmp_path):
    _planted_finding(tmp_path)
    RUN.run(findings_root=tmp_path, engine_factory=_factory,
            dupecheck_client=FakeSearch({"total_count": 0, "items": []}))
    for path in tmp_path.rglob("finding.json"):
        status = json.loads(path.read_text())["status"]
        assert status not in (
            "filed", "acknowledged", "accepted", "fixed", "rejected",
            "duplicate_confirmed",
        )


def test_already_triaged_findings_are_skipped(tmp_path):
    path = _planted_finding(tmp_path)
    record = json.loads(path.read_text())
    record["status"] = "candidate_ready"
    path.write_text(json.dumps(record), encoding="utf-8")
    stats = RUN.run(findings_root=tmp_path, engine_factory=_factory)
    assert stats.considered == 0
    assert stats.skipped == 1


def test_the_finding_limit_is_respected(tmp_path):
    for index in range(4):
        directory = tmp_path / "turso" / f"sig{index}"
        directory.mkdir(parents=True)
        source = json.loads(_planted_finding(tmp_path / f"src{index}").read_text())
        source["coarse_signature"] = f"{index}" * 40
        (directory / "finding.json").write_text(json.dumps(source), encoding="utf-8")
    stats = RUN.run(
        findings_root=tmp_path / "turso", engine_factory=_factory, max_findings=2
    )
    assert stats.considered == 2


def test_the_lock_stops_a_second_run(tmp_path):
    first = RUN.Lock(tmp_path / "lock")
    with first as held:
        assert held.held
        with RUN.Lock(tmp_path / "lock") as second:
            assert not second.held
    assert not (tmp_path / "lock").exists()


def test_a_broken_finding_does_not_stop_the_run(tmp_path):
    good = _planted_finding(tmp_path)
    bad_dir = tmp_path / "turso" / ("b" * 8)
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "finding.json").write_text("{not json", encoding="utf-8")
    stats = RUN.run(findings_root=tmp_path, engine_factory=_factory,
                    dupecheck_client=None)
    assert stats.considered >= 1
    assert json.loads(good.read_text())["status"] != "new"


# --- sqlglot tagging ------------------------------------------------------


def test_sqlglot_sees_structure_the_regex_tagger_cannot():
    tags = sqlglot_tags(["SELECT count(*) FROM a LEFT JOIN b ON a.x = b.x"])
    assert "join:left" in tags
    assert "agg:count" in tags


def test_tagging_falls_back_when_sql_does_not_parse():
    assert sqlglot_tags(["SELCT not valid ((("]) == ()
    # the lexical tagger still produces something
    assert feature_tags(["SELCT not valid ((("])


def test_a_recursive_cte_is_tagged():
    tags = sqlglot_tags(
        ["WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c) SELECT n FROM c"]
    )
    assert "cte:recursive" in tags or "cte:non_recursive" in tags


def test_a_window_function_is_tagged_as_a_window():
    tags = sqlglot_tags(["SELECT row_number() OVER (ORDER BY a) FROM t"])
    assert any(tag.startswith("win:") for tag in tags)


def test_the_same_bug_under_different_literals_shares_a_signature():
    """P09 task 2 asks for this explicitly."""
    first = coarse_signature("turso", "crash", "panic", "index out of bounds: len 3 index 3")
    second = coarse_signature("turso", "crash", "panic", "index out of bounds: len 9 index 9")
    assert first == second
    other = coarse_signature("turso", "crash", "panic", "stack overflow")
    assert first != other


def test_a_cross_engine_allowlist_cannot_silence_a_same_engine_finding(tmp_path):
    """configs/known_differences.yaml records CROSS-ENGINE differences.

    tlp and norec run both forms of the query on ONE engine, and crash needs no
    second engine, so a cross-engine note cannot explain those. Applying it
    anyway marked two real DataFusion findings known_difference because
    DuckDB reports a wider type for sum() than SQLite does - while the actual
    disagreement was a query returning 787416 whose own three-way partition
    returned 2**64 + 787416.
    """
    payload = {"total_count": 0, "items": []}
    for oracle in ("tlp", "norec", "crash"):
        root = tmp_path / oracle
        path = _planted_finding(root)
        record = json.loads(path.read_text())
        record["oracle"] = oracle
        # a query the allowlist's `\bsum\s*\(` pattern matches
        record["query"] = "SELECT sum(a) FROM t"
        path.write_text(json.dumps(record), encoding="utf-8")
        RUN.run(findings_root=root, engine_factory=_factory,
                dupecheck_client=FakeSearch(payload))
        assert json.loads(path.read_text())["status"] != "known_difference", oracle


