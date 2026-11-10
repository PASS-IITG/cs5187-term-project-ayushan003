"""Tests for the report drafter and the upstream tracker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graft.triage import report as R
from graft.triage import upstream as U

FIXTURES = Path(__file__).parent / "fixtures"


def _candidate(root: Path, **overrides) -> Path:
    """Write a candidate_ready finding, the drafter's only input."""
    record = {
        "id": "turso-deadbeef",
        "coarse_signature": "d" * 40,
        "feature_tags": ["fn:lpad", "clause:select"],
        "target": "turso",
        "engine_version": "Turso 0.9.0-pre.2",
        "engine_commit": "b588fbdc6c016cb3626d0ab464a7c45a54bc2b86",
        "oracle": "crash",
        "origin": "fuzzer",
        "seed": 6948,
        "setup": [],
        "query": "select LPAD('a', 9223372036854775807, 'b')",
        "reduced_sql": ["select LPAD('a', 9223372036854775807, 'b');"],
        "raw_expected": "a result, or a user_error",
        "raw_actual": "The process aborts with SIGABRT.",
        "status": "candidate_ready",
        "report_title": "LPAD with a huge length aborts the process",
        "report_slug": "turso-lpad",
        "duplicate_candidates": [
            {"number": 11175, "title": "reject wrong argument counts", "state": "open"}
        ],
        "duplicate_search": {
            "queries": ['repo:tursodatabase/turso "lpad" crash'],
            "searched": True,
            "detail": "",
        },
        "verification_history": [
            {"commit": "b588fbdc6", "result": "STILL CRASHES", "timestamp": "2026-11-01T10:54:55Z"}
        ],
        "reduction": {"executions": 6, "ratio": 0.2, "statements": 1, "steps_kept": []},
        "notes": "confirmed on current main",
    }
    record.update(overrides)
    directory = root / "turso" / ("d" * 8)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "finding.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


# --- the drafter ----------------------------------------------------------


def test_a_candidate_produces_a_complete_draft(tmp_path):
    """P11 acceptance: complete, with no placeholder but the disclosure line."""
    path = _candidate(tmp_path)
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")

    assert len(drafts) == 1
    text = drafts[0].path.read_text(encoding="utf-8")

    # every section a maintainer needs
    for heading in (
        "## Environment", "## What happens", "## To reproduce",
        "## Expected behaviour, and why", "## How this was found",
        "## Duplicate search", "## Disclosure", "## Checklist",
    ):
        assert heading in text, heading

    # the facts, straight from the finding
    assert "Turso 0.9.0-pre.2" in text
    assert "b588fbdc6c016cb3626d0ab464a7c45a54bc2b86" in text
    assert "select LPAD('a', 9223372036854775807, 'b');" in text
    assert "#11175" in text
    assert 'repo:tursodatabase/turso "lpad" crash' in text
    assert "6948" in text  # the seed

    # The ONLY placeholder permitted is the disclosure line. The checklist is
    # excluded because it tells the human to fill in whatever is UNKNOWN, so the
    # word appears there as instruction rather than as a gap in the report.
    assert R.DISCLOSURE_LINE in text
    body = text.split("## Checklist")[0]
    assert "UNKNOWN" not in body.replace(R.DISCLOSURE_LINE, "")
    assert "TODO" not in text and "FIXME" not in text and "XXX" not in text

    # and the finding has advanced exactly one step
    record = json.loads(path.read_text())
    assert record["status"] == R.STATUS_DRAFTED
    assert record["draft_path"].endswith("turso-lpad.md")


def test_a_missing_field_stays_unknown_and_is_not_invented(tmp_path):
    """Rule 7 of CLAUDE.md, enforced in the output."""
    _candidate(tmp_path, engine_commit=None, seed=None)
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "Commit: UNKNOWN" in text
    assert "Seed: UNKNOWN" in text


def test_only_candidate_ready_findings_are_drafted(tmp_path):
    for status in ("new", "reduced", "verified", "duplicate_suspect",
                   "known_difference", "flaky", "questionable", "drafted"):
        root = tmp_path / status
        _candidate(root, status=status)
        drafts = R.draft_all(findings_root=root, draft_dir=tmp_path / "out")
        assert drafts == [], f"{status} should not be drafted"


def test_a_crash_report_claims_nothing_about_other_engines(tmp_path):
    """A crash needs no cross-engine or standards claim, and must make none."""
    _candidate(tmp_path)
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "no claim about the SQL standard is made here" in text
    assert "does not rest on any other engine" in text


def test_a_differential_report_cites_the_reference_engine(tmp_path):
    _candidate(tmp_path, oracle="diff_sqlite", raw_expected="[(1,)]")
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "SQLite returned: [(1,)]" in text
    assert "common subset" in text


def test_a_metamorphic_report_says_both_forms_ran_on_one_engine(tmp_path):
    _candidate(tmp_path, oracle="tlp")
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "SAME engine" in text


def test_a_finding_with_no_duplicate_search_says_so_loudly(tmp_path):
    _candidate(tmp_path, duplicate_search=None, duplicate_candidates=[])
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "a human MUST" in text


def test_the_index_lists_the_draft(tmp_path):
    _candidate(tmp_path)
    R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    index = R.write_index(findings_root=tmp_path, index_path=tmp_path / "INDEX.md")
    text = index.read_text(encoding="utf-8")
    assert "| turso-deadbeef | turso | crash | drafted |" in text


# --- the upstream tracker -------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://github.com/tursodatabase/turso/issues/12345",
    "https://github.com/tursodatabase/turso/pull/9917",
])
def test_issue_urls_are_parsed(url):
    repo, number = U.parse_issue_url(url)
    assert repo == "tursodatabase/turso"
    assert number in ("12345", "9917")


@pytest.mark.parametrize("bad", [
    "not a url", "https://example.com/x/y/issues/1",
    "https://github.com/owner/repo", "https://github.com/o/r/issues/abc",
])
def test_a_non_issue_url_is_refused(bad):
    with pytest.raises(ValueError):
        U.parse_issue_url(bad)


def test_only_human_states_are_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    for refused in ("new", "verified", "candidate_ready", "drafted", "made_up"):
        with pytest.raises(ValueError):
            U.add("turso-deadbeef", "https://github.com/o/r/issues/1", refused)


def test_recording_a_filed_issue_moves_the_draft(tmp_path, monkeypatch):
    findings = tmp_path / "findings"
    path = _candidate(findings)
    draft_dir = tmp_path / "draft"
    filed_dir = tmp_path / "filed"
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    monkeypatch.setattr(U, "FILED_DIR", filed_dir)
    monkeypatch.setattr(R, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(U, "REPO_ROOT", tmp_path)
    R.draft_all(findings_root=findings, draft_dir=draft_dir)
    monkeypatch.setattr(U, "_find_finding", lambda i: (path, json.loads(path.read_text())))

    entry = U.add("turso-deadbeef", "https://github.com/tursodatabase/turso/issues/12345", "filed")
    assert entry.state == "filed"
    assert (filed_dir / "turso-lpad.md").exists()
    assert not (draft_dir / "turso-lpad.md").exists()
    assert "filed as https://github.com/tursodatabase/turso/issues/12345" in (
        filed_dir / "turso-lpad.md").read_text()
    table = (tmp_path / "UPSTREAM.md").read_text()
    assert "turso-deadbeef" in table and "12345" in table


def test_recording_a_pull_request_keeps_the_issue_row(tmp_path, monkeypatch):
    """A bug has both an issue and a PR, and the paper needs both recorded.

    De-duplicating on finding_id alone dropped the issue row the moment the
    pull request was recorded, which is how the 11219 row vanished from
    UPSTREAM.md when turso #11225 was merged.
    """
    findings = tmp_path / "findings"
    path = _candidate(findings)
    draft_dir = tmp_path / "draft"
    filed_dir = tmp_path / "filed"
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    monkeypatch.setattr(U, "FILED_DIR", filed_dir)
    monkeypatch.setattr(R, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(U, "REPO_ROOT", tmp_path)
    R.draft_all(findings_root=findings, draft_dir=draft_dir)
    monkeypatch.setattr(U, "_find_finding", lambda i: (path, json.loads(path.read_text())))

    U.add("turso-deadbeef", "https://github.com/tursodatabase/turso/issues/11219", "filed")
    U.add("turso-deadbeef", "https://github.com/tursodatabase/turso/pull/11225", "fixed")
    urls = {e.url for e in U.load_entries()}
    assert urls == {
        "https://github.com/tursodatabase/turso/issues/11219",
        "https://github.com/tursodatabase/turso/pull/11225",
    }
    # Re-recording the SAME url updates in place rather than duplicating it.
    U.add("turso-deadbeef", "https://github.com/tursodatabase/turso/pull/11225", "fixed")
    assert len(U.load_entries()) == 2


def test_a_second_recording_does_not_delete_the_filed_report(tmp_path, monkeypatch):
    """The draft is already in reports/filed/ by the time the PR is recorded.

    source and destination were then the same path, so add() wrote the file and
    unlinked it again - which really did delete
    reports/filed/turso-lpad-rpad-length-overflow.md.
    """
    findings = tmp_path / "findings"
    path = _candidate(findings)
    draft_dir = tmp_path / "draft"
    filed_dir = tmp_path / "filed"
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    monkeypatch.setattr(U, "FILED_DIR", filed_dir)
    monkeypatch.setattr(R, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(U, "REPO_ROOT", tmp_path)
    R.draft_all(findings_root=findings, draft_dir=draft_dir)
    monkeypatch.setattr(U, "_find_finding", lambda i: (path, json.loads(path.read_text())))

    U.add("turso-deadbeef", "https://github.com/tursodatabase/turso/issues/11219", "filed")
    filed = filed_dir / "turso-lpad.md"
    assert filed.exists()
    body = filed.read_text()

    U.add("turso-deadbeef", "https://github.com/tursodatabase/turso/pull/11225", "fixed")
    assert filed.exists(), "the second add() deleted the filed report"
    # Left exactly as it was: the header still names the issue it was filed as.
    assert filed.read_text() == body


def test_the_table_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    entries = [U.Entry("a-1", "https://github.com/o/r/issues/1", "filed", "2026-11-01", "turso"),
               U.Entry("b-2", "https://github.com/o/r/issues/2", "accepted", "2026-11-01", "duckdb")]
    U.write_entries(entries)
    back = U.load_entries()
    assert [e.finding_id for e in back] == ["a-1", "b-2"]
    assert [e.state for e in back] == ["filed", "accepted"]
    assert back[0].url.endswith("/issues/1")


def test_refresh_records_only_the_observed_github_state(tmp_path, monkeypatch):
    """It must not promote filed to accepted on its own: that is a judgement."""
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    U.write_entries([U.Entry("a-1", "https://github.com/o/r/issues/1", "filed",
                             "2026-11-01", "turso")])

    class FakeClient:
        def get(self, path, params=None):
            class Response:
                body = {"state": "closed", "state_reason": "completed"}
            return Response()

    entries = U.refresh(FakeClient())
    assert entries[0].state == "filed / github:closed:completed"
    assert "accepted" not in entries[0].state


def test_a_refresh_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "UPSTREAM_PATH", tmp_path / "UPSTREAM.md")
    U.write_entries([U.Entry("a-1", "https://github.com/o/r/issues/1", "filed",
                             "2026-11-01", "turso")])

    class Broken:
        def get(self, path, params=None):
            raise RuntimeError("network down")

    entries = U.refresh(Broken())
    assert "refresh failed" in entries[0].state


def test_the_tracker_has_no_way_to_create_an_issue():
    """Hard rule 1: nothing in this module may write to GitHub."""
    source = Path(U.__file__).read_text(encoding="utf-8")
    for forbidden in ("gh issue", "gh pr", '"POST"', "'POST'", ".post(", "requests.post"):
        assert forbidden not in source, forbidden


def test_an_internal_error_is_not_described_as_a_process_abort(tmp_path):
    """The crash oracle fires on a dead process and on an internal error.

    Telling a maintainer their clean internal error "should not abort the
    process" is wrong on its face, and they would notice.
    """
    _candidate(tmp_path, oracle="crash",
               raw_actual="Error: Internal error: Assertion failed: lhs_type == rhs_type")
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "An internal error is by definition not either of those" in text
    assert "should not abort the process" not in text
    assert "invites a" in text


def test_a_real_process_abort_still_says_so(tmp_path):
    _candidate(tmp_path, oracle="crash",
               raw_actual="The process aborts with SIGABRT, memory allocation failed")
    drafts = R.draft_all(findings_root=tmp_path, draft_dir=tmp_path / "draft")
    text = drafts[0].path.read_text(encoding="utf-8")
    assert "should not abort the process" in text
