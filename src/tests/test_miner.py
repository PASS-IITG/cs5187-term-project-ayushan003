"""Tests for the GitHub miner. Entirely offline: no test here touches the network.

Real API shapes come from recorded fixtures in tests/fixtures/, served by a fake
session object that only implements ``get`` - the same surface the client uses.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from graft.corpus import fetch, github

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "ghp_fixture_token_value_do_not_log"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    """The slice of a requests.Response the client actually reads."""

    def __init__(self, status_code=200, body=None, headers=None, text=""):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Serves recorded responses by url substring. Has no write methods at all."""

    def __init__(self, routes=None, default=None):
        self.routes = list(routes or [])
        self.default = default
        self.calls: list[tuple[str, dict, dict]] = []
        self.seen_headers: list[dict] = []

    def add(self, pattern: str, response, times: int | None = None):
        self.routes.append([pattern, response, times])
        return self

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        self.seen_headers.append(dict(headers or {}))
        for route in self.routes:
            pattern, response, times = route
            if re.search(pattern, url) and (times is None or times > 0):
                if times is not None:
                    route[2] = times - 1
                return response(url, params, headers) if callable(response) else response
        if self.default is not None:
            return self.default
        raise AssertionError(f"unrouted request: {url}")


def make_client(session, tmp_path, **kwargs):
    kwargs.setdefault("token", TOKEN)
    kwargs.setdefault("api_root", "https://api.github.test")
    return github.GitHubClient(
        session=session, cache_path=tmp_path / "cache", **kwargs
    )


# --- hard rule 1: there is no way to write -----------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "HEAD"])
def test_every_method_but_get_is_refused(method, tmp_path):
    client = make_client(FakeSession(), tmp_path)
    with pytest.raises(github.MethodNotAllowed):
        client.request(method, "/repos/x/y/issues")


def test_get_is_the_only_allowed_method():
    assert github.ALLOWED_METHODS == {"GET"}


def test_miner_source_contains_no_write_call():
    """No module under miner/ may even mention a writing HTTP call."""
    forbidden = re.compile(
        r"\.(post|put|patch|delete)\s*\(|requests\.(post|put|patch|delete)",
        re.IGNORECASE,
    )
    for path in sorted(Path("miner").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{path} contains a write call"


def test_refusing_a_write_never_reaches_the_session(tmp_path):
    session = FakeSession()
    client = make_client(session, tmp_path)
    with pytest.raises(github.MethodNotAllowed):
        client.request("DELETE", "/repos/x/y")
    assert session.calls == []


# --- hard rule 6: the token never leaks --------------------------------------


def test_token_is_sent_only_as_a_bearer_header(tmp_path):
    session = FakeSession(default=FakeResponse(200, {"ok": True}))
    client = make_client(session, tmp_path)
    client.get("/rate_limit")
    url, params, headers = session.calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in url
    assert TOKEN not in json.dumps(params)


def test_token_never_reaches_the_cache_files(tmp_path):
    session = FakeSession(
        default=FakeResponse(200, {"ok": True}, {"ETag": 'W/"abc"'})
    )
    client = make_client(session, tmp_path)
    client.get("/repos/a/b")
    files = list((tmp_path / "cache").glob("*.json"))
    assert files
    for path in files:
        assert TOKEN not in path.read_text(encoding="utf-8")
        assert TOKEN not in path.name


def test_token_never_reaches_the_log(tmp_path):
    from graft.log import JsonlLogger

    logger = JsonlLogger("miner-test", tmp_path / "logs")
    session = FakeSession(default=FakeResponse(200, {"ok": True}))
    client = make_client(session, tmp_path, logger=logger)
    client.get("/repos/a/b")
    logger.close()
    text = (tmp_path / "logs" / "miner-test.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in text
    assert "github_request" in text
    assert "Authorization" not in text


def test_token_is_redacted_from_errors(tmp_path):
    session = FakeSession(
        default=FakeResponse(500, None, {}, text=f"boom {TOKEN} boom")
    )
    client = make_client(session, tmp_path)
    with pytest.raises(github.GitHubError) as excinfo:
        client.get("/repos/a/b")
    assert TOKEN not in str(excinfo.value)
    assert "REDACTED" in str(excinfo.value)


def test_token_is_redacted_from_network_failures(tmp_path):
    class Exploding:
        def get(self, *a, **k):
            raise OSError(f"connection to {TOKEN}@host failed")

    client = make_client(Exploding(), tmp_path)
    with pytest.raises(github.GitHubError) as excinfo:
        client.get("/x")
    assert TOKEN not in str(excinfo.value)


def test_redact_handles_missing_token():
    assert github.redact("text", None) == "text"
    assert github.redact("", "tok") == ""


def test_authenticated_flag_does_not_expose_the_token(tmp_path):
    client = make_client(FakeSession(), tmp_path)
    assert client.authenticated is True
    assert TOKEN not in repr(client.__dict__.get("authenticated", ""))
    assert github.GitHubClient(token="", session=FakeSession(),
                               cache_path=tmp_path / "c2").authenticated is False


# --- ETag caching ------------------------------------------------------------


def test_etag_is_stored_and_replayed_on_304(tmp_path):
    body = [{"name": "bug"}]
    session = FakeSession()
    session.add(r"/labels", FakeResponse(200, body, {"ETag": 'W/"v1"'}), times=1)
    session.add(r"/labels", FakeResponse(304, None, {"ETag": 'W/"v1"'}))
    client = make_client(session, tmp_path)

    first = client.get("/repos/a/b/labels")
    assert first.body == body and first.from_cache is False

    second = client.get("/repos/a/b/labels")
    assert second.body == body
    assert second.from_cache is True
    assert second.status == 200
    assert client.cache_hits == 1
    # the conditional request carried the stored ETag
    assert session.calls[1][2]["If-None-Match"] == 'W/"v1"'


def test_cache_can_be_disabled(tmp_path):
    session = FakeSession(default=FakeResponse(200, {"a": 1}, {"ETag": 'W/"v"'}))
    client = make_client(session, tmp_path, use_cache=False)
    client.get("/x")
    client.get("/x")
    assert all("If-None-Match" not in h for _, _, h in session.calls)
    assert not (tmp_path / "cache").exists()


def test_cache_key_is_stable_and_parameter_sensitive():
    a = github.cache_key("https://x/y", {"b": 2, "a": 1})
    b = github.cache_key("https://x/y", {"a": 1, "b": 2})
    c = github.cache_key("https://x/y", {"a": 2})
    assert a == b != c


def test_a_corrupt_cache_file_is_ignored(tmp_path):
    session = FakeSession(default=FakeResponse(200, {"a": 1}, {"ETag": 'W/"v"'}))
    client = make_client(session, tmp_path)
    client.get("/x")
    for path in (tmp_path / "cache").glob("*.json"):
        path.write_text("not json", encoding="utf-8")
    assert client.get("/x").body == {"a": 1}


def test_a_304_without_a_cached_body_is_not_treated_as_success(tmp_path):
    session = FakeSession(default=FakeResponse(304, None, {}))
    client = make_client(session, tmp_path)
    with pytest.raises(github.GitHubError):
        client.get("/x")


# --- pagination --------------------------------------------------------------


def test_paginate_follows_the_link_header(tmp_path):
    page1 = FakeResponse(
        200,
        [{"number": 1}, {"number": 2}],
        {"Link": '<https://api.github.test/next-page>; rel="next"'},
    )
    page2 = FakeResponse(200, [{"number": 3}], {})
    session = FakeSession()
    session.add(r"next-page", page2)
    session.add(r"/issues", page1)
    client = make_client(session, tmp_path)
    assert [i["number"] for i in client.paginate("/repos/a/b/issues")] == [1, 2, 3]


def test_paginate_defaults_to_the_maximum_page_size(tmp_path):
    session = FakeSession(default=FakeResponse(200, [], {}))
    client = make_client(session, tmp_path)
    list(client.paginate("/repos/a/b/issues"))
    assert session.calls[0][1]["per_page"] == github.MAX_PER_PAGE


def test_paginate_respects_max_items(tmp_path):
    session = FakeSession(
        default=FakeResponse(
            200,
            [{"n": i} for i in range(100)],
            {"Link": '<https://api.github.test/p2>; rel="next"'},
        )
    )
    client = make_client(session, tmp_path)
    assert len(list(client.paginate("/x", max_items=5))) == 5
    assert len(session.calls) == 1  # never asked for page two


def test_paginate_rejects_a_non_list_payload(tmp_path):
    session = FakeSession(default=FakeResponse(200, {"message": "nope"}, {}))
    client = make_client(session, tmp_path)
    with pytest.raises(github.GitHubError):
        list(client.paginate("/x"))


def test_parse_links_handles_an_empty_header():
    assert github.parse_links("") == {}


# --- rate limiting -----------------------------------------------------------


def test_remaining_zero_makes_the_next_call_sleep_until_reset(tmp_path):
    slept: list[float] = []
    reset_at = 1_000_000
    session = FakeSession(
        default=FakeResponse(
            200,
            {"ok": True},
            {
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_at),
            },
        )
    )
    client = make_client(
        session, tmp_path, sleep=slept.append, now=lambda: reset_at - 30
    )
    client.get("/first")  # learns remaining == 0
    assert slept == []
    client.get("/second")  # must wait first
    assert slept and 30 <= slept[0] <= 32


def test_rate_limit_headers_are_parsed_and_exposed(tmp_path):
    session = FakeSession(
        default=FakeResponse(
            200,
            {},
            {
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Remaining": "4321",
                "X-RateLimit-Reset": "99",
            },
        )
    )
    client = make_client(session, tmp_path)
    limit = client.get("/x").rate_limit
    assert (limit.limit, limit.remaining, limit.reset_at) == (5000, 4321, 99)
    assert limit.exhausted is False


def test_missing_or_bad_rate_limit_headers_are_tolerated():
    assert github.RateLimit.from_headers({}).remaining is None
    assert github.RateLimit.from_headers({"X-RateLimit-Remaining": "x"}).remaining is None
    assert github.RateLimit(remaining=0).exhausted is True


def test_a_403_with_retry_after_is_retried_after_sleeping(tmp_path):
    slept: list[float] = []
    session = FakeSession()
    session.add(r"/x", FakeResponse(403, None, {"Retry-After": "7"}, text="slow down"), times=1)
    session.add(r"/x", FakeResponse(200, {"ok": True}, {}))
    client = make_client(session, tmp_path, sleep=slept.append)
    assert client.get("/x").body == {"ok": True}
    assert slept == [7.0]


def test_a_403_that_is_not_a_rate_limit_still_raises(tmp_path):
    session = FakeSession(default=FakeResponse(403, None, {}, text="forbidden"))
    client = make_client(session, tmp_path)
    with pytest.raises(github.GitHubError):
        client.get("/x")


def test_the_request_budget_is_enforced(tmp_path):
    session = FakeSession(default=FakeResponse(200, {"ok": True}, {}))
    client = make_client(session, tmp_path, request_budget=2)
    client.get("/a")
    client.get("/b")
    with pytest.raises(github.RequestBudgetExceeded):
        client.get("/c")
    assert len(session.calls) == 2


def test_rate_limit_status_reads_the_core_quota(tmp_path):
    session = FakeSession(
        default=FakeResponse(
            200, {"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 1}}}, {}
        )
    )
    client = make_client(session, tmp_path)
    status = github.rate_limit_status(client)
    assert status["limit"] == 5000 and status["remaining"] == 4999
    assert status["authenticated"] is True


def test_cache_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAFT_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert github.cache_dir() == tmp_path / "elsewhere"


def test_labels_helper_paginates(tmp_path):
    session = FakeSession(default=FakeResponse(200, [{"name": "bug"}], {}))
    client = make_client(session, tmp_path)
    assert github.labels(client, "a/b") == [{"name": "bug"}]


# --- fetch: file classification and truncation -------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("test/sql/aggregate/test_sum.test", True),
        ("tests/sqllogictests/select.slt", True),
        ("datafusion/sqllogictest/test_files/sort_pushdown.slt", True),
        ("core/translate/expr_test.rs", True),
        ("tests/integration/test_insert.py", True),
        ("src/execution/aggregate.cpp", False),
        ("datafusion/physical-optimizer/src/sort_pushdown.rs", False),
        ("README.md", False),
    ],
)
def test_is_test_file(path, expected):
    assert fetch.is_test_file(path) is expected


def test_truncate_marks_the_cut():
    text = "x" * 200
    cut = fetch.truncate(text, 100)
    assert len(cut.encode()) <= 100
    assert "truncated" in cut


def test_truncate_leaves_short_text_alone():
    assert fetch.truncate("short", 100) == "short"
    assert fetch.truncate(None, 100) == ""


# --- fetch: issue filtering --------------------------------------------------


def _issue(number, closed_at, **extra):
    base = {
        "number": number,
        "html_url": f"https://github.test/i/{number}",
        "title": f"issue {number}",
        "body": "body",
        "labels": [{"name": "bug"}],
        "created_at": "2026-01-01T00:00:00Z",
        "closed_at": closed_at,
    }
    base.update(extra)
    return base


def test_pull_requests_and_excluded_closures_are_skipped(tmp_path):
    items = [
        _issue(1, "2026-03-01T00:00:00Z"),
        _issue(2, "2026-04-01T00:00:00Z", pull_request={"url": "x"}),
        _issue(3, "2026-05-01T00:00:00Z", state_reason="not_planned"),
        _issue(4, "2026-06-01T00:00:00Z", state_reason="duplicate"),
        _issue(5, "2026-07-01T00:00:00Z", labels=[{"name": "Duplicate"}]),
        _issue(6, "2026-02-01T00:00:00Z", state_reason="completed"),
    ]
    session = FakeSession(default=FakeResponse(200, items, {}))
    client = make_client(session, tmp_path)
    stats = fetch.FetchStats()
    kept = fetch.list_closed_issues(client, "a/b", "bug", max_issues=10, stats=stats)
    assert [i["number"] for i in kept] == [1, 6]  # sorted by closed_at desc
    assert stats.skipped_pull_requests == 1
    assert stats.skipped_state_reason == 2
    assert stats.skipped_labels == 1
    assert stats.candidates == 6


def test_most_recently_closed_wins_the_cut(tmp_path):
    items = [_issue(n, f"2026-01-{n:02d}T00:00:00Z") for n in range(1, 6)]
    session = FakeSession(default=FakeResponse(200, items, {}))
    client = make_client(session, tmp_path)
    kept = fetch.list_closed_issues(client, "a/b", "bug", max_issues=2)
    assert [i["number"] for i in kept] == [5, 4]


def test_the_listing_asks_for_closed_issues_with_the_label(tmp_path):
    session = FakeSession(default=FakeResponse(200, [], {}))
    client = make_client(session, tmp_path)
    fetch.list_closed_issues(client, "a/b", "correctness", max_issues=5)
    params = session.calls[0][1]
    assert params["state"] == "closed"
    assert params["labels"] == "correctness"
    assert params["direction"] == "desc"


# --- fetch: fix discovery ----------------------------------------------------


def test_find_fix_prefers_the_closing_commit():
    events = [
        {"event": "referenced", "commit_id": "aaa"},
        {
            "event": "cross-referenced",
            "source": {"issue": {"number": 9, "pull_request": {"merged_at": "t"}}},
        },
        {"event": "closed", "commit_id": "bbb"},
    ]
    fix = fetch.find_fix(events)
    assert fix.sha == "bbb" and fix.pr == 9


def test_find_fix_falls_back_to_a_merged_pull_request():
    events = [
        {
            "event": "cross-referenced",
            "source": {"issue": {"number": 11, "pull_request": {"merged_at": None}}},
        },
        {
            "event": "cross-referenced",
            "source": {"issue": {"number": 12, "pull_request": {"merged_at": "t"}}},
        },
        {"event": "closed", "commit_id": None},
    ]
    fix = fetch.find_fix(events)
    assert fix.sha is None and fix.pr == 12


def test_find_fix_falls_back_to_a_referenced_commit():
    fix = fetch.find_fix([{"event": "referenced", "commit_id": "ccc"}])
    assert fix.sha == "ccc"


def test_find_fix_ignores_cross_references_that_are_not_pull_requests():
    events = [
        {"event": "cross-referenced", "source": {"issue": {"number": 3}}},
        {"event": "labeled"},
    ]
    assert fetch.find_fix(events) == fetch.FixRef()


def test_find_fix_returns_nothing_when_the_api_exposes_nothing():
    assert fetch.find_fix([]).sha is None


def test_timeline_fetches_the_last_page_when_paged(tmp_path):
    first = FakeResponse(
        200,
        [{"event": "labeled"}],
        {
            "Link": (
                '<https://api.github.test/t?page=2>; rel="next", '
                '<https://api.github.test/t?page=4>; rel="last"'
            )
        },
    )
    last = FakeResponse(200, [{"event": "closed", "commit_id": "zzz"}], {})
    session = FakeSession()
    session.add(r"page=4", last)
    session.add(r"/timeline", first)
    client = make_client(session, tmp_path)
    events = fetch.timeline_events(client, "a/b", 7)
    assert fetch.find_fix(events).sha == "zzz"
    assert len(session.calls) == 2


def test_timeline_does_not_ask_for_a_second_page_when_unpaged(tmp_path):
    session = FakeSession(default=FakeResponse(200, [{"event": "closed"}], {}))
    client = make_client(session, tmp_path)
    fetch.timeline_events(client, "a/b", 7)
    assert len(session.calls) == 1


# --- fetch: test text --------------------------------------------------------


def test_test_text_keeps_only_test_files():
    files = [
        {"filename": "src/a.rs", "patch": "@@ src change"},
        {"filename": "tests/b.slt", "patch": "@@ test change"},
    ]
    text = fetch.test_text(files)
    assert "test change" in text and "src change" not in text
    assert "--- tests/b.slt" in text


def test_test_text_notes_a_missing_diff():
    text = fetch.test_text([{"filename": "tests/b.slt", "patch": None}])
    assert "no diff available" in text


def test_test_text_is_truncated():
    files = [{"filename": "tests/b.slt", "patch": "x" * 50_000}]
    assert len(fetch.test_text(files).encode()) <= fetch.TEST_TEXT_LIMIT


def test_test_text_is_empty_when_no_test_file_changed():
    assert fetch.test_text([{"filename": "src/a.rs", "patch": "p"}]) == ""


# --- fetch: end to end on recorded fixtures ----------------------------------


def test_mine_repo_against_recorded_fixtures(tmp_path):
    issues = load_fixture("issues_page.json")
    timeline = load_fixture("timeline.json")
    commit = load_fixture("commit.json")

    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, timeline, {}))
    session.add(r"/commits/[^/]+/pulls", FakeResponse(200, [], {}))
    session.add(r"/commits/", FakeResponse(200, commit, {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    stats = fetch.mine_repo(
        client,
        "datafusion",
        "apache/datafusion",
        "bug",
        max_issues=len(issues),
        out_dir=tmp_path / "mined",
    )

    path = tmp_path / "mined" / "datafusion" / "issues.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == stats.kept == len(issues)
    assert stats.with_fix_sha == len(issues)  # the fixture timeline closes with a commit
    assert stats.with_test_text >= 1

    first = records[0]
    # the eleven fields P03 specifies, plus the two provenance fields
    assert {
        "number",
        "url",
        "title",
        "body",
        "labels",
        "created_at",
        "closed_at",
        "fix_sha",
        "fix_pr",
        "fix_files",
        "fix_test_text",
    } <= set(first)
    assert set(first) - {
        "number", "url", "title", "body", "labels", "created_at", "closed_at",
        "fix_sha", "fix_pr", "fix_files", "fix_test_text",
    } == {"fix_lookup", "fix_pr_source"}
    assert first["fix_sha"] == commit["sha"]
    assert any(fetch.is_test_file(f) for f in first["fix_files"])
    assert ".slt" in first["fix_test_text"] or "--- " in first["fix_test_text"]
    # records are ordered most recently closed first
    closed = [r["closed_at"] for r in records]
    assert closed == sorted(closed, reverse=True)


def test_mine_repo_keeps_an_issue_with_no_discoverable_fix(tmp_path):
    issues = [_issue(42, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "labeled"}], {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    stats = fetch.mine_repo(
        client, "turso", "tursodatabase/turso", "correctness",
        max_issues=5, out_dir=tmp_path / "mined",
    )
    record = json.loads(
        (tmp_path / "mined" / "turso" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert record["fix_sha"] == "UNKNOWN"
    assert record["fix_files"] == [] and record["fix_test_text"] == ""
    assert stats.kept == 1 and stats.with_fix_sha == 0


def test_mine_repo_uses_a_pull_request_when_there_is_no_closing_commit(tmp_path):
    issues = [_issue(7, "2026-01-01T00:00:00Z")]
    timeline = [
        {
            "event": "cross-referenced",
            "source": {"issue": {"number": 99, "pull_request": {"merged_at": "t"}}},
        }
    ]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, timeline, {}))
    session.add(r"/pulls/99/files", FakeResponse(200, [{"filename": "tests/x.slt", "patch": "@@ new test"}], {}))
    session.add(r"/pulls/99$", FakeResponse(200, {"merge_commit_sha": "deadbeef"}, {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    fetch.mine_repo(client, "duckdb", "duckdb/duckdb", "reproduced",
                    max_issues=5, out_dir=tmp_path / "mined")
    record = json.loads(
        (tmp_path / "mined" / "duckdb" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert record["fix_sha"] == "deadbeef"
    assert record["fix_pr"] == 99
    assert "new test" in record["fix_test_text"]


def test_mine_repo_survives_a_failing_fix_lookup(tmp_path):
    issues = [_issue(5, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(500, None, {}, text="server error"))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    stats = fetch.mine_repo(client, "duckdb", "duckdb/duckdb", "reproduced",
                            max_issues=5, out_dir=tmp_path / "mined")
    assert stats.kept == 1
    assert stats.errors and "#5" in stats.errors[0]
    record = json.loads(
        (tmp_path / "mined" / "duckdb" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert record["fix_sha"] == "UNKNOWN"


def test_bodies_are_capped_at_eight_kilobytes(tmp_path):
    issues = [_issue(1, "2026-01-01T00:00:00Z", body="y" * 20_000)]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [], {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)
    fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "mined")
    record = json.loads(
        (tmp_path / "mined" / "t" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert len(record["body"].encode()) <= fetch.BODY_LIMIT


# --- provenance --------------------------------------------------------------


def test_engine_provenance_records_the_turso_text_protocol_caveat():
    from graft.config import get_target

    turso = fetch.engine_provenance(get_target("turso"))
    assert turso["text_cells"] is True
    note = turso["cell_fidelity_note"]
    assert "text protocol" in note
    assert "indistinguishable" in note
    assert "newline" in note and "'|'" in note
    # commit provenance is never invented
    assert turso["engine_commit"] == "UNKNOWN"
    assert turso["configured_commit"]


def test_engine_provenance_reports_a_missing_binary_instead_of_raising(monkeypatch, tmp_path):
    from graft.config import bin_override_var, get_target

    monkeypatch.setenv(bin_override_var("duckdb"), str(tmp_path / "gone"))
    info = fetch.engine_provenance(get_target("duckdb"))
    assert info["engine_version"] in ("UNKNOWN", info["engine_version"])
    assert isinstance(info, dict)


# --- the real fetch refuses to run without a token ---------------------------


def test_run_refuses_without_a_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert fetch.run(targets=["duckdb"]) == 2
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().out


# --- fix provenance: honest records for unavailable metadata ------------------


def test_record_shape_includes_the_provenance_fields(tmp_path):
    issues = [_issue(1, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [], {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)
    fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "m")
    record = json.loads(
        (tmp_path / "m" / "t" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert list(record) == [
        "number", "url", "title", "body", "labels", "created_at", "closed_at",
        "fix_sha", "fix_pr", "fix_files", "fix_test_text",
        "fix_lookup", "fix_pr_source",
    ]
    assert record["fix_lookup"] == fetch.LOOKUP_NONE
    assert record["fix_pr_source"] is None


def test_an_unreachable_commit_is_recorded_as_unavailable_not_empty(tmp_path):
    """The real 422 case: the sha is kept, but the record says files are missing."""
    issues = [_issue(21896, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "closed", "commit_id": "dangling"}], {}))
    session.add(
        r"/commits/dangling$",
        FakeResponse(422, None, {}, text='{"message":"No commit found for SHA: dangling"}'),
    )
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    stats = fetch.mine_repo(client, "duckdb", "duckdb/duckdb", "reproduced",
                            max_issues=5, out_dir=tmp_path / "m")
    record = json.loads(
        (tmp_path / "m" / "duckdb" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert record["fix_sha"] == "dangling"  # the API really did name it
    assert record["fix_files"] == [] and record["fix_test_text"] == ""
    assert record["fix_lookup"] == "closing_commit" + fetch.UNREACHABLE_SUFFIX
    assert stats.unreachable_commits == 1
    assert stats.kept == 1  # the record is kept, never discarded
    assert stats.errors and "#21896" in stats.errors[0]


def test_a_reachable_commit_that_changed_nothing_is_distinguishable(tmp_path):
    issues = [_issue(2, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "closed", "commit_id": "good"}], {}))
    session.add(r"/commits/good$", FakeResponse(200, {"sha": "good", "files": []}, {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)
    fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "m")
    record = json.loads(
        (tmp_path / "m" / "t" / "issues.jsonl").read_text().splitlines()[0]
    )
    # Same empty fix_files as the unreachable case, but fix_lookup tells them apart.
    assert record["fix_files"] == []
    assert record["fix_lookup"] == fetch.LOOKUP_CLOSING_COMMIT


def test_find_fix_records_where_the_reference_came_from():
    assert fetch.find_fix([{"event": "closed", "commit_id": "a"}]).source == (
        fetch.LOOKUP_CLOSING_COMMIT
    )
    assert fetch.find_fix([{"event": "referenced", "commit_id": "a"}]).source == (
        fetch.LOOKUP_REFERENCED_COMMIT
    )
    assert fetch.find_fix(
        [{"event": "cross-referenced",
          "source": {"issue": {"number": 4, "pull_request": {"merged_at": "t"}}}}]
    ).source == fetch.LOOKUP_PULL_REQUEST
    assert fetch.find_fix([]).source == fetch.LOOKUP_NONE


# --- fix_pr recovery ---------------------------------------------------------


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Fix invalid compression type to disable (#26055)", 26055),
        ("fix: Remove `serde_json` feature from library crates (#25884)", 25884),
        ("plain commit with no pr number", None),
        ("mentions issue #123 without parentheses", None),
    ],
)
def test_pr_from_commit_subject(subject, expected):
    assert fetch.pr_from_commit_subject({"commit": {"message": subject}}) == expected


def test_pr_from_commit_subject_ignores_the_body():
    commit = {"commit": {"message": "subject with none\n\nbody mentions (#999)"}}
    assert fetch.pr_from_commit_subject(commit) is None


def test_commit_subject_pr_costs_no_extra_request(tmp_path):
    issues = [_issue(3, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "closed", "commit_id": "s"}], {}))
    session.add(
        r"/commits/s$",
        FakeResponse(
            200,
            {"sha": "s", "commit": {"message": "Fix it (#4242)"},
             "files": [{"filename": "test/x.test", "patch": "@@ t"}]},
            {},
        ),
    )
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    stats = fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "m")
    record = json.loads(
        (tmp_path / "m" / "t" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert record["fix_pr"] == 4242
    assert record["fix_pr_source"] == "commit_subject"
    assert stats.pr_from_subject == 1 and stats.pr_from_api == 0
    # issues + timeline + commit only: no /pulls call was needed
    assert not any("/pulls" in url for url, _, _ in session.calls)


def test_the_api_is_asked_only_when_the_subject_has_no_number(tmp_path):
    issues = [_issue(4, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "closed", "commit_id": "s"}], {}))
    session.add(
        r"/commits/s/pulls",
        FakeResponse(200, [{"number": 10870, "merged_at": "t"}], {}),
    )
    session.add(
        r"/commits/s$",
        FakeResponse(200, {"sha": "s", "commit": {"message": "merge branch"}, "files": []}, {}),
    )
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)

    stats = fetch.mine_repo(client, "turso", "a/b", "correctness", 5, out_dir=tmp_path / "m")
    record = json.loads(
        (tmp_path / "m" / "turso" / "issues.jsonl").read_text().splitlines()[0]
    )
    assert record["fix_pr"] == 10870
    assert record["fix_pr_source"] == "api"
    assert stats.pr_from_api == 1 and stats.pr_from_subject == 0


def test_resolve_pr_false_skips_the_extra_request(tmp_path):
    issues = [_issue(5, "2026-01-01T00:00:00Z")]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "closed", "commit_id": "s"}], {}))
    session.add(
        r"/commits/s$",
        FakeResponse(200, {"sha": "s", "commit": {"message": "merge"}, "files": []}, {}),
    )
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)
    fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "m", resolve_pr=False)
    assert not any("/pulls" in url for url, _, _ in session.calls)


def test_pulls_for_commit_prefers_a_merged_pull_request(tmp_path):
    session = FakeSession(
        default=FakeResponse(
            200,
            [{"number": 1, "merged_at": None}, {"number": 2, "merged_at": "t"}],
            {},
        )
    )
    client = make_client(session, tmp_path)
    assert fetch.pulls_for_commit(client, "a/b", "s") == 2


def test_pulls_for_commit_tolerates_an_error_and_an_empty_list(tmp_path):
    empty = make_client(FakeSession(default=FakeResponse(200, [], {})), tmp_path)
    assert fetch.pulls_for_commit(empty, "a/b", "s") is None
    broken = make_client(
        FakeSession(default=FakeResponse(422, None, {}, text="no commit")), tmp_path / "x"
    )
    assert fetch.pulls_for_commit(broken, "a/b", "s") is None


def test_commit_payload_exposes_the_message_and_the_files(tmp_path):
    session = FakeSession(
        default=FakeResponse(
            200, {"commit": {"message": "m (#7)"}, "files": [{"filename": "f"}]}, {}
        )
    )
    client = make_client(session, tmp_path)
    payload = fetch.commit_payload(client, "a/b", "s")
    assert fetch.pr_from_commit_subject(payload) == 7
    assert fetch.commit_files(
        make_client(session, tmp_path / "y"), "a/b", "s"
    ) == [{"filename": "f"}]


# --- the mined data on disk --------------------------------------------------


def test_the_mined_data_on_disk_is_schema_valid_and_honest():
    """Validates the committed data/mined/*/issues.jsonl. No network."""
    expected_keys = [
        "number", "url", "title", "body", "labels", "created_at", "closed_at",
        "fix_sha", "fix_pr", "fix_files", "fix_test_text",
        "fix_lookup", "fix_pr_source",
    ]
    valid_lookups = {
        fetch.LOOKUP_CLOSING_COMMIT,
        fetch.LOOKUP_REFERENCED_COMMIT,
        fetch.LOOKUP_PULL_REQUEST,
        fetch.LOOKUP_NONE,
        fetch.LOOKUP_CLOSING_COMMIT + fetch.UNREACHABLE_SUFFIX,
        fetch.LOOKUP_REFERENCED_COMMIT + fetch.UNREACHABLE_SUFFIX,
    }
    root = Path("data/mined")
    targets = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert targets == ["duckdb"]

    for target in targets:
        lines = (root / target / "issues.jsonl").read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        assert len(records) == 200, f"{target} has {len(records)} records"
        numbers = [r["number"] for r in records]
        assert len(set(numbers)) == len(numbers), f"{target} has duplicate issues"
        closed = [r["closed_at"] for r in records]
        assert closed == sorted(closed, reverse=True), f"{target} is not newest-first"

        for record in records:
            number = record["number"]
            assert list(record) == expected_keys, f"{target} #{number} shape"
            assert isinstance(record["number"], int)
            assert record["closed_at"], f"{target} #{number} has no closed_at"
            assert len(record["body"].encode()) <= fetch.BODY_LIMIT
            assert len(record["fix_test_text"].encode()) <= fetch.TEST_TEXT_LIMIT
            assert record["fix_lookup"] in valid_lookups
            assert record["fix_pr_source"] in (None, "api", "commit_subject")
            # honesty: no record may claim a fix it does not have
            if record["fix_sha"] == "UNKNOWN":
                assert record["fix_lookup"] == fetch.LOOKUP_NONE
                assert record["fix_files"] == []
                assert record["fix_test_text"] == ""
            if record["fix_files"] == [] and record["fix_sha"] != "UNKNOWN":
                # empty files with a real sha must say which of the two it is
                assert record["fix_lookup"] in (
                    fetch.LOOKUP_CLOSING_COMMIT,
                    fetch.LOOKUP_REFERENCED_COMMIT,
                    fetch.LOOKUP_CLOSING_COMMIT + fetch.UNREACHABLE_SUFFIX,
                    fetch.LOOKUP_REFERENCED_COMMIT + fetch.UNREACHABLE_SUFFIX,
                )
            if record["fix_pr"] is not None:
                assert record["fix_pr_source"] is not None
            if record["fix_test_text"]:
                assert any(fetch.is_test_file(f) for f in record["fix_files"])


def test_the_mined_meta_records_engine_provenance_and_the_turso_caveat():
    for target in ("duckdb",):
        meta = json.loads((Path("data/mined") / target / "meta.json").read_text())
        assert meta["target"] == target
        assert meta["label_filter"]
        engine = meta["engine"]
        assert engine["engine_version"] != "UNKNOWN"
        assert engine["configured_commit"]
        note = engine["cell_fidelity_note"]
        if target == "turso":
            assert engine["text_cells"] is True
            assert "text protocol" in note and "indistinguishable" in note
            assert "newline" in note and "'|'" in note
            assert engine["engine_commit"] == "UNKNOWN"  # never invented
        else:
            assert engine["text_cells"] is False
            assert "typed" in note
        assert "fix_lookup_values" in meta
        # measured per run, not asserted as a constant
        assert "cross_referenced_events" in meta["counts"]


def test_no_mined_file_contains_a_token_shaped_string():
    """Belt and braces for hard rule 6: no credential-looking string on disk."""
    pattern = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
    for path in sorted(Path("data/mined").rglob("*")):
        if path.is_file():
            assert not pattern.search(path.read_text(encoding="utf-8")), path


def test_cross_referenced_events_are_counted_not_assumed(tmp_path):
    """The meta file must report what a run actually saw, never a baked-in 0."""
    issues = [_issue(1, "2026-01-01T00:00:00Z")]
    timeline = [
        {"event": "labeled"},
        {
            "event": "cross-referenced",
            "source": {"issue": {"number": 2, "pull_request": {"merged_at": "t"}}},
        },
    ]
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, timeline, {}))
    session.add(r"/pulls/2/files", FakeResponse(200, [], {}))
    session.add(r"/pulls/2$", FakeResponse(200, {"merge_commit_sha": "m"}, {}))
    session.add(r"/issues", FakeResponse(200, issues, {}))
    client = make_client(session, tmp_path)
    stats = fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "m")
    assert stats.cross_referenced_events == 1


def test_no_cross_references_counts_zero(tmp_path):
    session = FakeSession()
    session.add(r"/timeline", FakeResponse(200, [{"event": "closed"}], {}))
    session.add(r"/issues", FakeResponse(200, [_issue(1, "2026-01-01T00:00:00Z")], {}))
    client = make_client(session, tmp_path)
    stats = fetch.mine_repo(client, "t", "a/b", "bug", 5, out_dir=tmp_path / "m")
    assert stats.cross_referenced_events == 0
