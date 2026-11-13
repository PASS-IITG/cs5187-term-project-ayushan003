"""Track what a human filed upstream. This module never files anything.

``python -m graft.triage.upstream add ID URL STATE`` is run BY A HUMAN after they have
filed an issue themselves. It records the URL and state and moves the draft from
``reports/draft/`` to ``reports/filed/``. ``refresh`` re-reads the recorded
issues with read-only GETs and updates their states.

The states here are exactly the human-only half of the status vocabulary in
docs/spec.md. The tool refuses to set them any other way, which is why this is a
separate command a person runs on purpose.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from graft.config import REPO_ROOT

UPSTREAM_PATH = REPO_ROOT / "UPSTREAM.md"
DRAFT_DIR = REPO_ROOT / "reports" / "draft"
FILED_DIR = REPO_ROOT / "reports" / "filed"

#: the half of the vocabulary only a human may set
HUMAN_STATES = (
    "filed",
    "acknowledged",
    "accepted",
    "fixed",
    "rejected",
    "duplicate_confirmed",
)

_ISSUE_URL = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/"
    r"(?:issues|pull)/(?P<number>\d+)$"
)


@dataclass
class Entry:
    """One filed issue."""

    finding_id: str
    url: str
    state: str
    updated: str
    target: str = "UNKNOWN"

    def row(self) -> str:
        """Render as a Markdown table row."""
        return (
            f"| {self.finding_id} | {self.target} | [{self.url.rsplit('/', 1)[-1]}]"
            f"({self.url}) | {self.state} | {self.updated} |"
        )


def parse_issue_url(url: str) -> tuple[str, str]:
    """Return ``(owner/repo, number)``; raise ValueError if it is not one."""
    match = _ISSUE_URL.match(url.strip())
    if match is None:
        raise ValueError(
            f"not a GitHub issue or pull request URL: {url!r}. Expected "
            "https://github.com/OWNER/REPO/issues/NUMBER"
        )
    return f"{match['owner']}/{match['repo']}", match["number"]


def load_entries(path: Path | None = None) -> list[Entry]:
    """Read UPSTREAM.md back into entries."""
    target = path or UPSTREAM_PATH
    if not target.exists():
        return []
    entries: list[Entry] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ") or line.startswith("| ---") or "| id " in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        url_match = re.search(r"\((https://[^)]+)\)", cells[2])
        if url_match is None:
            continue
        entries.append(
            Entry(cells[0], url_match.group(1), cells[3], cells[4], cells[1])
        )
    return entries


def write_entries(entries: Sequence[Entry], path: Path | None = None) -> Path:
    """Write UPSTREAM.md."""
    target = path or UPSTREAM_PATH
    lines = [
        "# Upstream issues",
        "",
        "Issues a HUMAN filed from the drafts in `reports/`. This file is written by",
        "`python -m graft.triage.upstream`, which never files anything itself: the states",
        "below are the human-only half of the status vocabulary in docs/spec.md, and",
        "the tool has no way to set them except through a person running `add`.",
        "",
        "| id | target | issue | state | updated |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [e.row() for e in sorted(entries, key=lambda e: e.finding_id)]
    if not entries:
        lines.append("| - | - | - | - | nothing filed yet |")
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _find_finding(finding_id: str) -> tuple[Path, dict[str, Any]] | None:
    """Locate a finding by id."""
    from triage.run import load_findings

    for path, record in load_findings():
        if str(record.get("id")) == finding_id or path.parent.name == finding_id:
            return path, record
    return None


def add(finding_id: str, url: str, state: str) -> Entry:
    """Record an issue a human filed, and move its draft to reports/filed/."""
    if state not in HUMAN_STATES:
        raise ValueError(
            f"{state!r} is not a human-settable state. Use one of: "
            f"{', '.join(HUMAN_STATES)}"
        )
    repo, number = parse_issue_url(url)
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    found = _find_finding(finding_id)
    target = "UNKNOWN"
    if found is not None:
        path, record = found
        target = str(record.get("target") or "UNKNOWN")
        record["status"] = state
        record["upstream_url"] = url
        record.setdefault("upstream_history", []).append(
            {"state": state, "url": url, "recorded": stamp}
        )
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        draft = record.get("draft_path")
        if draft:
            source = REPO_ROOT / draft
            destination = FILED_DIR / source.name
            # A second add() for the same finding - recording the PR after the
            # issue - finds draft_path ALREADY under reports/filed/, so source
            # and destination are the same file. Writing it and then unlinking
            # the source deleted the filed report outright. Nothing to move.
            if source.exists() and source.resolve() != destination.resolve():
                FILED_DIR.mkdir(parents=True, exist_ok=True)
                destination.write_text(
                    f"<!-- filed as {url} ({state}) on {stamp} -->\n"
                    + source.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                source.unlink()
                record["draft_path"] = str(destination.relative_to(REPO_ROOT))
                path.write_text(
                    json.dumps(record, indent=2) + "\n", encoding="utf-8"
                )
    entry = Entry(finding_id, url, state, stamp, target)
    # Key on the URL as well as the finding: a bug normally has both an
    # issue and a pull request, and de-duplicating on finding_id alone
    # silently dropped the issue row when the PR was recorded.
    entries = [
        e
        for e in load_entries()
        if not (e.finding_id == finding_id and e.url == url)
    ]
    entries.append(entry)
    write_entries(entries)
    return entry


def refresh(client: Any | None = None) -> list[Entry]:
    """Re-read every recorded issue's state with read-only GETs."""
    entries = load_entries()
    if not entries:
        return entries
    if client is None:
        from miner.github import GitHubClient

        client = GitHubClient(request_budget=len(entries) + 5)
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    for entry in entries:
        try:
            repo, number = parse_issue_url(entry.url)
            body = client.get(f"/repos/{repo}/issues/{number}").body
        except Exception as error:
            entry.state = f"{entry.state} (refresh failed: {type(error).__name__})"
            continue
        if not isinstance(body, dict):
            continue
        # Only the OBSERVED github state is recorded. Whether a closed issue was
        # accepted, fixed or rejected is a judgement, so it is left to the human
        # who reads the thread; this never promotes a state on its own.
        observed = str(body.get("state") or "UNKNOWN")
        reason = body.get("state_reason")
        entry.state = (
            f"{entry.state.split(' (')[0]} / github:{observed}"
            + (f":{reason}" if reason else "")
        )
        entry.updated = stamp
    write_entries(entries)
    return entries


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(
        description="record issues a human filed upstream (never files anything)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_parser = sub.add_parser("add", help="record an issue you filed yourself")
    add_parser.add_argument("finding_id")
    add_parser.add_argument("url")
    add_parser.add_argument("state", choices=HUMAN_STATES)
    sub.add_parser("refresh", help="update recorded issues' states (read-only)")
    sub.add_parser("list", help="show the recorded table")
    args = parser.parse_args(argv)

    if args.command == "add":
        entry = add(args.finding_id, args.url, args.state)
        print(f"recorded {entry.finding_id} -> {entry.url} ({entry.state})")
    elif args.command == "refresh":
        for entry in refresh():
            print(f"{entry.finding_id:28} {entry.state:24} {entry.url}")
    else:
        for entry in load_entries():
            print(f"{entry.finding_id:28} {entry.state:24} {entry.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
