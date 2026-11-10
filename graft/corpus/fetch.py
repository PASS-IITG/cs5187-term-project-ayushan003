"""Mining recently fixed upstream bugs, and the tests that were added to fix them.

For each target repo this collects the most recently *closed* issues carrying
that repo's confirmed-bug label, finds the fix (a commit sha, a pull request, or
neither), and records the diff of the test files the fix touched. Those test
files are the raw material for the variant hunter: they are the maintainers' own
minimal reproductions.

Everything here is read-only: it uses :class:`miner.github.GitHubClient`, which
has no code path that can write to GitHub (CLAUDE.md hard rule 1).

Output, one JSON object per line, in ``data/mined/<target>/issues.jsonl``:
number, url, title, body (<=8 KB), labels, created_at, closed_at, fix_sha,
fix_pr, fix_files, fix_test_text - the fields P03 specifies - plus two that say
how trustworthy those are:

``fix_lookup``
    How the fix metadata was obtained, or why it is missing. One of
    ``closing_commit``, ``referenced_commit``, ``pull_request``,
    ``closing_commit_unreachable``, ``referenced_commit_unreachable``, ``none``.
    The ``*_unreachable`` values matter: the API names a commit that is no
    longer in the repo (rebased, force-pushed, or in a fork), so ``fix_sha`` is
    real but ``fix_files`` and ``fix_test_text`` are *unavailable* rather than
    empty. Without this field those two cases are indistinguishable, which
    would be a quietly dishonest record.
``fix_pr_source``
    ``api`` when GitHub itself linked the pull request, ``commit_subject`` when
    the number was parsed from the squash-merge ``(#N)`` convention in the
    commit subject and is therefore a convention, not a confirmation, or
    ``null`` when there is no PR number.

``fix_sha`` is the literal string ``UNKNOWN`` when the API never exposes one;
the issue is kept either way, because the issue text alone often carries a
usable repro.

Why ``cross-referenced`` is not the PR route: across 600 timeline responses
fetched for the three repos (none of which filled a 100-event page, so no
middle page was ever skipped), the timeline API returned **zero**
``cross-referenced`` events. These projects close issues by commit reference,
so pull requests are recovered from the closing commit instead - first from the
``(#N)`` in its subject, which costs nothing, and only then from
``/commits/{sha}/pulls``.

A sibling ``meta.json`` records provenance: the repo, the label, the counts, and
the exact engine version and commit the mined set is meant to be tested against,
read from the running binaries rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from graft.config import REPO_ROOT, Target, load_targets
from graft.log import JsonlLogger
from graft.corpus.github import GitHubClient, GitHubError, rate_limit_status

#: how many issues to keep per repo
DEFAULT_MAX_ISSUES = 200
#: how many candidates to pull before sorting by closed_at; the issues API
#: cannot sort by closed date, so we over-fetch and sort locally
CANDIDATE_MULTIPLIER = 3
#: per-issue cap on recorded test diff text
TEST_TEXT_LIMIT = 20 * 1024
#: per-issue cap on recorded issue body
BODY_LIMIT = 8 * 1024

#: state_reason values that mean "this was not a real, fixed bug"
EXCLUDED_STATE_REASONS = frozenset({"not_planned", "duplicate"})
#: labels that say the same thing on repos that pre-date state_reason
EXCLUDED_LABELS = frozenset({"duplicate", "invalid"})

#: how a fix reference was obtained; recorded in ``fix_lookup``
LOOKUP_CLOSING_COMMIT = "closing_commit"
LOOKUP_REFERENCED_COMMIT = "referenced_commit"
LOOKUP_PULL_REQUEST = "pull_request"
LOOKUP_NONE = "none"
#: suffix added when a named commit turned out not to exist in the repo
UNREACHABLE_SUFFIX = "_unreachable"

#: the squash-merge convention GitHub writes into a commit subject
_PR_IN_SUBJECT = re.compile(r"\(#(\d+)\)")


#: path fragments and suffixes that mark a test file
TEST_DIR_PARTS = ("test", "tests", "testing", "sqllogictest", "slt")
TEST_SUFFIXES = (".slt", ".test")


def is_test_file(path: str) -> bool:
    """True if ``path`` looks like a test file.

    P03's rule: under ``test/`` or ``tests/``, or ending ``.slt`` or ``.test``,
    or a name ending ``_test``. The last case is read as the file *stem* ending
    in ``_test`` so Rust's ``foo_test.rs`` and Go's ``foo_test.go`` match, and
    the mirror-image convention ``test_foo.py`` is matched too.
    """
    lowered = path.lower()
    parts = lowered.split("/")
    if any(part in TEST_DIR_PARTS for part in parts[:-1]):
        return True
    if lowered.endswith(TEST_SUFFIXES):
        return True
    name = parts[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.endswith("_test") or stem.startswith("test_") or stem == "test"


@dataclass
class MinedIssue:
    """One mined issue, exactly the SPEC/P03 record shape."""

    number: int
    url: str
    title: str
    body: str
    labels: list[str]
    created_at: str | None
    closed_at: str | None
    fix_sha: str
    fix_pr: int | None
    fix_files: list[str]
    fix_test_text: str
    fix_lookup: str = LOOKUP_NONE
    fix_pr_source: str | None = None

    def to_json(self) -> str:
        """Render as one JSONL line."""
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class FetchStats:
    """Counters for one repo, printed at the end of a run."""

    target: str = ""
    repo: str = ""
    label: str = ""
    candidates: int = 0
    skipped_pull_requests: int = 0
    skipped_state_reason: int = 0
    skipped_labels: int = 0
    kept: int = 0
    with_fix_sha: int = 0
    with_fix_pr: int = 0
    with_test_text: int = 0
    unreachable_commits: int = 0
    pr_from_subject: int = 0
    pr_from_api: int = 0
    #: how many ``cross-referenced`` timeline events were seen at all. Measured
    #: per run rather than assumed: it was 0 for all three repos on 2026-10-28,
    #: which is why pull requests are recovered from the closing commit.
    cross_referenced_events: int = 0
    requests_made: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)


def truncate(text: str | None, limit: int) -> str:
    """Return ``text`` cut to ``limit`` bytes, with a visible marker."""
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = f"\n... [truncated at {limit} bytes]"
    room = limit - len(marker.encode("utf-8"))
    if room <= 0:  # a limit too small for the marker: cut hard
        return encoded[:limit].decode("utf-8", errors="ignore")
    return encoded[:room].decode("utf-8", errors="ignore") + marker


# --- issue listing -----------------------------------------------------------


def list_closed_issues(
    client: GitHubClient,
    repo: str,
    label: str,
    max_issues: int = DEFAULT_MAX_ISSUES,
    stats: FetchStats | None = None,
) -> list[dict[str, Any]]:
    """Return the ``max_issues`` most recently closed non-PR issues with ``label``.

    The issues API cannot sort by closed date, so this pulls a larger window
    sorted by last update and then sorts locally by ``closed_at``.
    """
    stats = stats or FetchStats()
    wanted = max_issues * CANDIDATE_MULTIPLIER
    kept: list[dict[str, Any]] = []

    for item in client.paginate(
        f"/repos/{repo}/issues",
        params={
            "state": "closed",
            "labels": label,
            "sort": "updated",
            "direction": "desc",
        },
        max_items=wanted,
    ):
        stats.candidates += 1
        if "pull_request" in item:
            stats.skipped_pull_requests += 1
            continue
        if (item.get("state_reason") or "") in EXCLUDED_STATE_REASONS:
            stats.skipped_state_reason += 1
            continue
        names = {str(l.get("name", "")).lower() for l in item.get("labels") or []}
        if names & EXCLUDED_LABELS:
            stats.skipped_labels += 1
            continue
        kept.append(item)

    kept.sort(key=lambda i: i.get("closed_at") or "", reverse=True)
    return kept[:max_issues]


# --- fix discovery -----------------------------------------------------------


@dataclass
class FixRef:
    """What the API could tell us about an issue's fix."""

    sha: str | None = None
    pr: int | None = None
    source: str = LOOKUP_NONE


def timeline_events(
    client: GitHubClient, repo: str, number: int
) -> list[dict[str, Any]]:
    """Return an issue's timeline: the first page and, if paged, the last one.

    Timelines are chronological, so the closing event is on the last page. Only
    those two pages are fetched; the middle of a long timeline holds label and
    comment churn that cannot name a fix.
    """
    response = client.get(
        f"/repos/{repo}/issues/{number}/timeline", params={"per_page": 100}
    )
    events = list(response.body or [])
    last = response.links.get("last")
    if last and last != response.links.get("first"):
        try:
            events.extend(client.get(last).body or [])
        except GitHubError:
            pass
    return events


def find_fix(events: Sequence[Mapping[str, Any]]) -> FixRef:
    """Pick the most trustworthy fix reference out of timeline events.

    Preference order:

    1. the ``closed`` event's ``commit_id`` - the commit GitHub itself credits
       with closing the issue;
    2. a merged pull request that cross-references the issue;
    3. any ``referenced`` commit, which only says a commit message mentioned
       the issue.
    """
    closed_sha: str | None = None
    merged_pr: int | None = None
    referenced_sha: str | None = None
    open_pr: int | None = None

    for event in events:
        kind = event.get("event")
        if kind == "closed" and event.get("commit_id"):
            closed_sha = str(event["commit_id"])
        elif kind == "referenced" and event.get("commit_id"):
            referenced_sha = referenced_sha or str(event["commit_id"])
        elif kind == "cross-referenced":
            source_issue = (event.get("source") or {}).get("issue") or {}
            pull = source_issue.get("pull_request")
            if not pull:
                continue
            number = source_issue.get("number")
            if number is None:
                continue
            if pull.get("merged_at"):
                merged_pr = merged_pr or int(number)
            else:
                open_pr = open_pr or int(number)

    if closed_sha:
        return FixRef(sha=closed_sha, pr=merged_pr, source=LOOKUP_CLOSING_COMMIT)
    if merged_pr:
        return FixRef(sha=None, pr=merged_pr, source=LOOKUP_PULL_REQUEST)
    if referenced_sha:
        return FixRef(
            sha=referenced_sha, pr=open_pr, source=LOOKUP_REFERENCED_COMMIT
        )
    return FixRef()


def commit_payload(client: GitHubClient, repo: str, sha: str) -> dict[str, Any]:
    """Return a commit's full payload: its message and its changed files.

    Raises:
        GitHubError: notably HTTP 422 "No commit found for SHA", which is how
            GitHub reports a commit that the API named but that is no longer
            reachable in the repo.
    """
    return dict(client.get(f"/repos/{repo}/commits/{sha}").body or {})


def commit_files(
    client: GitHubClient, repo: str, sha: str
) -> list[dict[str, Any]]:
    """Return the files a commit changed, with their patches."""
    return list(commit_payload(client, repo, sha).get("files") or [])


def pr_from_commit_subject(commit: Mapping[str, Any]) -> int | None:
    """Return the PR number in a commit subject's ``(#N)``, or None.

    This is GitHub's squash-merge convention, so it is a strong hint but not a
    confirmation; callers record it as ``fix_pr_source: commit_subject``.
    """
    message = ((commit.get("commit") or {}).get("message") or "")
    subject = message.splitlines()[0] if message else ""
    found = _PR_IN_SUBJECT.search(subject)
    return int(found.group(1)) if found else None


def pulls_for_commit(client: GitHubClient, repo: str, sha: str) -> int | None:
    """Return the pull request GitHub associates with ``sha``, or None.

    Costs one request, so callers only reach here when the commit subject held
    no ``(#N)``. A merged PR wins over any other.
    """
    try:
        body = client.get(f"/repos/{repo}/commits/{sha}/pulls").body or []
    except GitHubError:
        return None
    if not isinstance(body, list):  # defensive: the endpoint returns a list
        return None
    merged = [p for p in body if isinstance(p, dict) and p.get("merged_at")]
    chosen = merged or [p for p in body if isinstance(p, dict)]
    if not chosen:
        return None
    number = chosen[0].get("number")
    return int(number) if number is not None else None


def pull_request_files(
    client: GitHubClient, repo: str, number: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Return a PR's changed files and its merge commit sha."""
    pull = client.get(f"/repos/{repo}/pulls/{number}").body or {}
    files = list(client.paginate(f"/repos/{repo}/pulls/{number}/files"))
    return files, pull.get("merge_commit_sha")


def test_text(files: Iterable[Mapping[str, Any]], limit: int = TEST_TEXT_LIMIT) -> str:
    """Join the diffs of the changed *test* files, truncated to ``limit``.

    The recorded text is the unified diff the API returns for each file, not the
    file's full contents: the added lines are the new regression test, which is
    exactly what the variant hunter wants, and it costs no extra API calls. A
    file whose diff GitHub omitted (too large) is recorded as such.
    """
    chunks: list[str] = []
    for item in files:
        name = str(item.get("filename", ""))
        if not is_test_file(name):
            continue
        patch = item.get("patch")
        if patch:
            chunks.append(f"--- {name}\n{patch}")
        else:
            chunks.append(f"--- {name}\n[no diff available from the API]")
    return truncate("\n".join(chunks), limit)


# --- one repo ----------------------------------------------------------------

def mine_repo(
    client: GitHubClient,
    target: str,
    repo: str,
    label: str,
    max_issues: int = DEFAULT_MAX_ISSUES,
    out_dir: Path | None = None,
    logger: JsonlLogger | None = None,
    resolve_pr: bool = True,
) -> FetchStats:
    """Mine one repo and write ``data/mined/<target>/issues.jsonl``.

    Args:
        resolve_pr: when True, spend one extra request per issue whose closing
            commit subject carried no ``(#N)`` to ask GitHub which pull request
            the commit belongs to. Set False to keep the run to the minimum
            number of requests.

    Returns:
        The :class:`FetchStats` for the run. Per-issue failures are collected in
        ``stats.errors`` and never abort the run.
    """
    stats = FetchStats(target=target, repo=repo, label=label)
    issues = list_closed_issues(client, repo, label, max_issues, stats)

    records: list[MinedIssue] = []
    for issue in issues:
        number = int(issue["number"])
        fix = FixRef()
        files: list[dict[str, Any]] = []
        lookup = LOOKUP_NONE
        pr_source: str | None = None
        try:
            events = timeline_events(client, repo, number)
            stats.cross_referenced_events += sum(
                1 for e in events if e.get("event") == "cross-referenced"
            )
            fix = find_fix(events)
            lookup = fix.source
            if fix.sha:
                try:
                    commit = commit_payload(client, repo, fix.sha)
                except GitHubError as exc:
                    # The API named a commit that is not in the repo any more.
                    # Keep the sha, but say the files could not be read rather
                    # than letting an empty list read as "changed nothing".
                    lookup = fix.source + UNREACHABLE_SUFFIX
                    stats.unreachable_commits += 1
                    stats.errors.append(f"#{number}: {exc}")
                else:
                    files = list(commit.get("files") or [])
                    if fix.pr is None:
                        subject_pr = pr_from_commit_subject(commit)
                        if subject_pr is not None:
                            fix.pr = subject_pr
                            pr_source = "commit_subject"
                            stats.pr_from_subject += 1
                        elif resolve_pr:
                            api_pr = pulls_for_commit(client, repo, fix.sha)
                            if api_pr is not None:
                                fix.pr = api_pr
                                pr_source = "api"
                                stats.pr_from_api += 1
                    else:
                        pr_source = "api"
            elif fix.pr:
                files, merge_sha = pull_request_files(client, repo, fix.pr)
                fix = FixRef(sha=merge_sha, pr=fix.pr, source=LOOKUP_PULL_REQUEST)
                lookup = LOOKUP_PULL_REQUEST
                pr_source = "api"
        except GitHubError as exc:
            stats.errors.append(f"#{number}: {exc}")

        record = MinedIssue(
            number=number,
            url=str(issue.get("html_url", "")),
            title=str(issue.get("title", "")),
            body=truncate(issue.get("body"), BODY_LIMIT),
            labels=[str(l.get("name", "")) for l in issue.get("labels") or []],
            created_at=issue.get("created_at"),
            closed_at=issue.get("closed_at"),
            fix_sha=fix.sha or "UNKNOWN",
            fix_pr=fix.pr,
            fix_files=[str(f.get("filename", "")) for f in files],
            fix_test_text=test_text(files),
            fix_lookup=lookup,
            fix_pr_source=pr_source,
        )
        records.append(record)
        stats.kept += 1
        if record.fix_sha != "UNKNOWN":
            stats.with_fix_sha += 1
        if record.fix_pr:
            stats.with_fix_pr += 1
        if record.fix_test_text:
            stats.with_test_text += 1
        if logger is not None:
            logger.log(
                "mined_issue",
                target=target,
                number=number,
                fix_sha=record.fix_sha,
                fix_pr=record.fix_pr,
                fix_lookup=record.fix_lookup,
                test_files=sum(1 for f in record.fix_files if is_test_file(f)),
            )

    stats.requests_made = client.requests_made
    stats.cache_hits = client.cache_hits

    directory = (out_dir or REPO_ROOT / "data" / "mined") / target
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "issues.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.to_json() + "\n")
    return stats


def engine_provenance(target: Target) -> dict[str, Any]:
    """Return the exact version and commit of the engine this set targets.

    Read from the running binary, never assumed: ``engine_commit`` is what the
    build itself reports and is UNKNOWN when it reports nothing, while
    ``configured_commit`` is the ~/targets SHA recorded in configs/targets.yaml
    (CLAUDE.md hard rule 7). ``text_cells`` carries the adapter's fidelity
    warning into the data: a text-protocol engine (Turso) cannot tell ``1`` from
    ``'1'``, and a value holding a newline or a ``|`` cannot be recovered at
    all, so results from it are never trustworthy typed values.
    """
    from graft.engines import create_engine

    info: dict[str, Any] = {
        "engine_version": "UNKNOWN",
        "engine_commit": "UNKNOWN",
        "configured_commit": target.pinned_commit,
        "kind": target.kind,
        "text_cells": None,
        "cell_fidelity_note": None,
    }
    try:
        engine = create_engine(target.name, target.kind)
    except Exception as exc:  # a missing binary must not stop mining
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    try:
        info["engine_version"] = engine.version()
        info["engine_commit"] = engine.commit()
        info["text_cells"] = engine.text_cells
        info["cell_fidelity_note"] = (
            "text protocol: every cell arrives as str, '1' and 1 are "
            "indistinguishable, and a value containing a newline or a '|' "
            "outside the last column cannot be recovered. Never treat these "
            "as typed values; see adapters/turso_engine.py."
            if engine.text_cells
            else "typed protocol: cells carry their JSON/driver types."
        )
    finally:
        engine.close()
    return info


# --- entry point -------------------------------------------------------------


def run(
    targets: Sequence[str] | None = None,
    max_issues: int = DEFAULT_MAX_ISSUES,
    request_budget: int | None = None,
    out_dir: Path | None = None,
    resolve_pr: bool = True,
) -> int:
    """Mine every requested target and print a summary. Returns an exit code."""
    configured = load_targets()
    names = list(targets) if targets else [
        name for name, t in configured.items() if t.repo
    ]

    logger = JsonlLogger("miner")
    client_kwargs: dict[str, Any] = {"logger": logger}
    if request_budget is not None:
        client_kwargs["request_budget"] = request_budget
    client = GitHubClient(**client_kwargs)

    if not client.authenticated:
        print("GITHUB_TOKEN is not set: refusing to run unauthenticated", flush=True)
        return 2

    started = time.time()
    quota = rate_limit_status(client)
    print(
        f"rate limit before: {quota['remaining']}/{quota['limit']} "
        f"(authenticated={quota['authenticated']})",
        flush=True,
    )

    all_stats: list[FetchStats] = []
    for name in names:
        target = configured.get(name)
        if target is None or not target.repo:
            print(f"{name}: no repo configured, skipping", flush=True)
            continue
        label = target.label_filter
        if not label or label == "TODO":
            print(f"{name}: label_filter is {label!r}, skipping", flush=True)
            continue
        print(f"== {name} ({target.repo}, label={label!r})", flush=True)
        stats = mine_repo(
            client,
            name,
            target.repo,
            label,
            max_issues,
            out_dir,
            logger,
            resolve_pr=resolve_pr,
        )
        meta = {
            "target": name,
            "repo": target.repo,
            "label_filter": label,
            "mined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "max_issues": max_issues,
            "counts": {
                k: v for k, v in asdict(stats).items() if isinstance(v, int)
            },
            "engine": engine_provenance(target),
            "fix_test_text": (
                "unified diff of the changed test files, from the GitHub API; "
                f"truncated to {TEST_TEXT_LIMIT} bytes per issue"
            ),
            "fix_lookup_values": {
                LOOKUP_CLOSING_COMMIT: "sha came from the issue's closed event",
                LOOKUP_REFERENCED_COMMIT: "sha came from a referenced event only",
                LOOKUP_PULL_REQUEST: "fix found through a pull request",
                LOOKUP_CLOSING_COMMIT + UNREACHABLE_SUFFIX: (
                    "sha named by the API but not reachable in the repo: "
                    "fix_files and fix_test_text are UNAVAILABLE, not empty"
                ),
                LOOKUP_REFERENCED_COMMIT + UNREACHABLE_SUFFIX: (
                    "referenced sha not reachable in the repo: "
                    "fix_files and fix_test_text are UNAVAILABLE, not empty"
                ),
                LOOKUP_NONE: "the API exposed no fix at all; fix_sha is UNKNOWN",
            },
            "fix_pr_source_values": {
                "api": "GitHub linked the pull request itself",
                "commit_subject": (
                    "parsed from the squash-merge '(#N)' convention in the "
                    "commit subject: a convention, not a confirmation"
                ),
            },

        }
        directory = (out_dir or REPO_ROOT / "data" / "mined") / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        all_stats.append(stats)
        print(
            f"   kept={stats.kept} fix_sha={stats.with_fix_sha} "
            f"fix_pr={stats.with_fix_pr} (subject={stats.pr_from_subject} "
            f"api={stats.pr_from_api}) test_text={stats.with_test_text} "
            f"unreachable_commits={stats.unreachable_commits} "
            f"errors={len(stats.errors)}",
            flush=True,
        )
        for problem in stats.errors[:5]:
            print(f"   ! {problem}", flush=True)

    print(
        f"\nrequests made: {client.requests_made}, cache hits: {client.cache_hits}, "
        f"rate limit remaining: {client.rate_limit.remaining}, "
        f"elapsed: {time.time() - started:.0f}s",
        flush=True,
    )
    logger.close()
    return 0 if all_stats else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point for ``python -m miner.fetch``."""
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--max-issues", type=int, default=DEFAULT_MAX_ISSUES)
    parser.add_argument("--request-budget", type=int, default=None)
    parser.add_argument(
        "--no-resolve-pr",
        action="store_false",
        dest="resolve_pr",
        help="skip the extra request that maps a closing commit to its PR",
    )
    args = parser.parse_args(argv)
    return run(
        targets=args.targets,
        max_issues=args.max_issues,
        request_budget=args.request_budget,
        resolve_pr=args.resolve_pr,
    )


if __name__ == "__main__":
    sys.exit(main())
