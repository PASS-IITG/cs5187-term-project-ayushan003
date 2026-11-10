"""Draft maintainer-ready reports for ``candidate_ready`` findings.

Nothing here files anything. Each draft lands in ``reports/draft/<id>.md`` for a
human to read, check against the project's policy, and submit by hand.

Two rules shape every line of output:

* **Never claim what the finding does not record.** A field that is UNKNOWN in
  ``finding.json`` is printed as UNKNOWN. An expectation is justified only from
  the reference engine's recorded result or from an explicitly verified fact,
  never from an appeal to "the SQL standard" that nobody checked.
* **The human owns the disclosure.** Every draft carries the AI-assistance line
  and a checklist, because ``docs/PROJECT_POLICIES.md`` records real policies
  that differ per project - DuckDB forbids AI-generated pull requests, and
  Turso's guide explicitly prefers a bug report to an AI-generated fix.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from graft.config import REPO_ROOT

DRAFT_DIR = REPO_ROOT / "reports" / "draft"
INDEX_PATH = REPO_ROOT / "reports" / "INDEX.md"
STATUS_DRAFTED = "drafted"

DISCLOSURE_LINE = (
    "DISCLOSE AI ASSISTANCE HERE IF THE PROJECT REQUIRES IT - see "
    "docs/PROJECT_POLICIES.md for what each project says, verbatim."
)

ORACLE_BLURB = {
    "crash": (
        "a crash oracle: the engine was asked to run a single statement and the "
        "process died or reported an internal error instead of answering or "
        "rejecting it. No comparison against another engine is involved, so this "
        "finding does not depend on any assumption about matching semantics."
    ),
    "diff_sqlite": (
        "a differential oracle against SQLite, restricted to a verified common "
        "subset of syntax and types (configs/common_subset.yaml). Both engines "
        "were given byte-identical SQL."
    ),
    "tlp": (
        "ternary logic partitioning (TLP), a metamorphic oracle: a query's rows "
        "must equal the UNION ALL of the same query filtered by p, NOT p, and "
        "p IS NULL. Both forms run on the SAME engine, so this is not a "
        "cross-engine semantics claim."
    ),
    "norec": (
        "NoREC, a metamorphic oracle: an aggregate over a filtered table must "
        "equal the same aggregate computed without letting the optimiser use the "
        "filter. Both forms run on the SAME engine."
    ),
}


def _repo_relative(path: Path) -> str:
    """Path relative to the repo when it is inside it, else as given."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _unknown(value: Any) -> str:
    """Render a missing value as UNKNOWN rather than as an empty string."""
    if value is None:
        return "UNKNOWN"
    text = str(value).strip()
    return text if text else "UNKNOWN"


def _repro_block(record: dict[str, Any], target: str) -> str:
    """The minimal repro as a pasteable shell block."""
    statements = record.get("reduced_sql") or record.get("original_sql") or []
    if not statements:
        query = record.get("query")
        statements = [query] if query else []
    sql = "\n".join(
        s.rstrip().rstrip(";") + ";" for s in statements if str(s).strip()
    )
    invocation = {
        "turso": "tursodb :memory:",
        "duckdb": "duckdb -no-agent",
        "datafusion": "datafusion-cli",
        "sqlite": "sqlite3 :memory:",
    }.get(target, target)
    return f"```sql\n{sql}\n```\n\nRun with:\n\n```\n{invocation}\n```"


def _expectation(record: dict[str, Any]) -> str:
    """State the expected behaviour, justified only from recorded evidence."""
    oracle = str(record.get("oracle") or "")
    expected = record.get("raw_expected")
    if oracle == "crash":
        # The crash oracle fires on a dead process AND on an internal error, and
        # the two need different words. Saying "should not abort the process"
        # about a clean internal error is simply wrong, and a maintainer would
        # notice immediately.
        actual = str(record.get("raw_actual") or "")
        internal = "Internal error" in actual or "internal_error" in str(
            record.get("error_class") or ""
        )
        if internal and "abort" not in actual.lower():
            return (
                "The statement should either return a result or be rejected "
                "with a normal user-facing error. An internal error is by "
                "definition not either of those: the engine's own message says "
                "it was likely caused by a bug in its code and invites a "
                "report, and the input here is plain SQL with no special "
                "configuration.\n\n"
                "This expectation does not rest on any other engine's "
                "behaviour, and no claim about the SQL standard is made here."
            )
        return (
            "The statement should either return a result or be rejected with a "
            "normal user-facing error. A single SQL statement should not abort "
            "the process: a server embedding this engine would lose the whole "
            "process, and the condition is reachable from plain SQL with no "
            "special configuration.\n\n"
            "This expectation does not rest on any other engine's behaviour, and "
            "no claim about the SQL standard is made here."
        )
    if oracle in ("tlp", "norec"):
        return (
            "Both forms of the query are evaluated by this engine alone, and the "
            "transformation between them preserves meaning, so the two results "
            "must agree. The reasoning does not depend on any other engine.\n\n"
            f"Recorded result of the original form: {_unknown(expected)}"
        )
    if oracle == "diff_sqlite":
        return (
            "SQLite, used as the reference engine, returns the result recorded "
            "below for byte-identical SQL, and the constructs used were verified "
            "to lie in the common subset both engines support "
            "(configs/common_subset.yaml). Whether SQLite or this engine is right "
            "is a judgement for the maintainers; the disagreement itself is the "
            f"report.\n\nSQLite returned: {_unknown(expected)}"
        )
    return f"Expected: {_unknown(expected)}"


def _duplicate_section(record: dict[str, Any]) -> str:
    """The duplicate search, queries and all, so a maintainer can judge it."""
    search = record.get("duplicate_search") or {}
    queries: Sequence[str] = search.get("queries") or ()
    candidates = record.get("duplicate_candidates") or []
    lines: list[str] = []
    if queries:
        lines.append("Search queries used (GitHub issue search, open and closed):")
        lines.append("")
        lines += [f"- `{q}`" for q in queries]
        lines.append("")
    else:
        lines += [
            "No duplicate search is recorded for this finding, so a human MUST "
            "search before filing.",
            "",
        ]
    if candidates:
        lines.append("Nearest existing issues found, and why each is different:")
        lines.append("")
        for hit in candidates[:5]:
            number = hit.get("number", "UNKNOWN")
            lines.append(
                f"- #{number} ({_unknown(hit.get('state'))}): "
                f"{_unknown(hit.get('title'))}"
            )
        lines.append("")
        lines.append(
            "The tool did not conclude these are the same defect; a human must "
            "read each one. If any is the same, do not file."
        )
    elif queries:
        lines.append("Those searches returned no plausible match.")
    return "\n".join(lines)


@dataclass
class Draft:
    """A written draft and where it went."""

    path: Path
    finding_id: str
    target: str
    oracle: str


def render(record: dict[str, Any]) -> str:
    """Return the full Markdown draft for one finding."""
    target = str(record.get("target") or "UNKNOWN")
    oracle = str(record.get("oracle") or "UNKNOWN")
    history = record.get("verification_history") or []
    notes = record.get("notes")

    title = record.get("report_title") or (
        f"{target}: {record.get('bug_class') or oracle} in "
        f"{', '.join(record.get('feature_tags') or ['UNKNOWN'])}"
    )

    body = [
        f"# {title}",
        "",
        "> DRAFT - not filed. A human must read "
        "docs/PROJECT_POLICIES.md, reproduce this on latest main, re-run the "
        "duplicate search, and only then decide whether to submit it.",
        "",
        "## Environment",
        "",
        f"- Engine: {target}",
        f"- Version: {_unknown(record.get('engine_version'))}",
        f"- Commit: {_unknown(record.get('engine_commit'))}",
        f"- OS: {platform.platform()}",
        f"- Build: release build from source, compiled locally",
        "",
        "## What happens",
        "",
        f"{_unknown(record.get('raw_actual'))}",
        "",
        "## To reproduce",
        "",
        _repro_block(record, target),
        "",
        "## Expected behaviour, and why",
        "",
        _expectation(record),
        "",
        "## How this was found",
        "",
        f"Found by {ORACLE_BLURB.get(oracle, f'the {oracle} oracle')}",
        "",
        f"- Seed: {_unknown(record.get('seed'))} (every random choice in this "
        "tool comes from a seeded generator, so the case is reproducible)",
        f"- Coarse signature: {_unknown(record.get('coarse_signature'))}",
    ]
    reduction = record.get("reduction") or {}
    if reduction:
        body.append(
            f"- Reduced automatically from the generated case: "
            f"{reduction.get('statements', 'UNKNOWN')} statements, "
            f"{reduction.get('executions', 'UNKNOWN')} executions, ratio "
            f"{reduction.get('ratio', 'UNKNOWN')}"
        )
    if history:
        body += ["", "### Verification history", ""]
        for entry in history:
            body.append(
                f"- {_unknown(entry.get('timestamp'))} on commit "
                f"{_unknown(entry.get('commit'))}: {_unknown(entry.get('result'))}"
            )
    if notes:
        body += ["", "### Notes recorded during triage", "", str(notes)]

    body += [
        "",
        "## Duplicate search",
        "",
        _duplicate_section(record),
        "",
        "## Disclosure",
        "",
        DISCLOSURE_LINE,
        "",
        "## Checklist for the human filing this",
        "",
        "- [ ] Reproduce on latest `main`, rebuilt today, and paste that commit here",
        "- [ ] Read the project's CONTRIBUTING and issue template "
        "(configs/report_templates/) and this project's policy summary "
        "(docs/PROJECT_POLICIES.md)",
        "- [ ] Re-run the duplicate search by hand, including closed issues",
        "- [ ] Fill in any template field this draft leaves UNKNOWN, including "
        "real name and affiliation where the template demands them",
        "- [ ] Decide on the disclosure line above",
        "- [ ] Confirm nothing in the repro depends on this machine or on "
        "generated data",
        "",
    ]
    return "\n".join(body)


def draft_all(
    findings_root: Path | None = None, draft_dir: Path | None = None
) -> list[Draft]:
    """Write a draft for every candidate_ready finding and set it to drafted."""
    from graft.triage.run import load_findings

    out_dir = draft_dir or DRAFT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    drafts: list[Draft] = []
    for path, record in load_findings(findings_root):
        if str(record.get("status")) != "candidate_ready":
            continue
        finding_id = str(record.get("id") or path.parent.name)
        target = str(record.get("target") or "UNKNOWN")
        slug = record.get("report_slug") or finding_id
        destination = out_dir / f"{slug}.md"
        destination.write_text(render(record), encoding="utf-8")
        record["status"] = STATUS_DRAFTED
        record["draft_path"] = _repo_relative(destination)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        drafts.append(
            Draft(destination, finding_id, target, str(record.get("oracle") or ""))
        )
    return drafts


def write_index(
    findings_root: Path | None = None, index_path: Path | None = None
) -> Path:
    """Write reports/INDEX.md: one row per draft."""
    from graft.triage.run import load_findings

    target_path = index_path or INDEX_PATH
    rows: list[tuple[str, str, str, str, str]] = []
    for _, record in load_findings(findings_root):
        status = str(record.get("status") or "")
        if status not in ("candidate_ready", "drafted", "filed", "duplicate_suspect"):
            continue
        draft = record.get("draft_path") or ""
        rows.append((
            str(record.get("id") or "UNKNOWN"),
            str(record.get("target") or "UNKNOWN"),
            str(record.get("oracle") or "UNKNOWN"),
            status,
            f"[draft]({Path(draft).name})" if draft else "-",
        ))
    rows.sort()
    lines = [
        "# Report drafts",
        "",
        "Written by `python -m triage.report`. Nothing here has been filed; this",
        "tool never files anything. `status` uses the vocabulary in docs/SPEC.md,",
        "and only a human sets `filed` or anything after it, through",
        "`python -m triage.upstream add`.",
        "",
        "| id | target | oracle | status | draft |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    if not rows:
        lines.append("| - | - | - | - | no drafts yet |")
    lines.append("")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(lines), encoding="utf-8")
    return target_path


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point."""
    drafts = draft_all()
    index = write_index()
    for draft in drafts:
        print(f"wrote {_repo_relative(draft.path)} ({draft.target}/{draft.oracle})")
    print(f"wrote {_repo_relative(index)} ({len(drafts)} new draft(s))")
    if not drafts:
        print("no candidate_ready findings; nothing to draft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
