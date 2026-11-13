"""Batching and validation for the hand-written root-cause classification.

P04 classifies repros by *judgement*, not by code and not by any external LLM
call (CLAUDE.md: "Do not call any external LLM API ... do it yourself, in small
batches, and write the result to a file"). This module does only the mechanical
parts:

* build batches of repros to look at - the N most recent issues that have a
  self-contained repro, in batches of 15;
* show, for each, exactly what P04 permits as input: the issue title, the repro
  SQL, and the *names* of the changed files. Never the diff;
* validate each hand-written record against the SPEC vocabularies before it is
  written, so a typo cannot smuggle a non-SPEC bug class or feature tag into
  ``data/classified/``;
* support resuming: :func:`classified_numbers` reports what is already done, and
  :func:`append_records` refuses to write an issue twice.

One line per issue, not per repro: an issue can yield many repros, and P04's
record is keyed by issue number, so the issue's first kept repro represents it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence

from graft.config import REPO_ROOT

#: SPEC "Bug classes" - the only permitted values
BUG_CLASSES = (
    "null_handling",
    "type_coercion",
    "integer_overflow",
    "float_precision",
    "window_frame",
    "optimizer_rewrite",
    "join_semantics",
    "aggregate_semantics",
    "nested_type",
    "string_function",
    "datetime",
    "cast",
    "sorting_collation",
    "panic_unimplemented",
    "other",
)

#: SPEC "Feature tags" - lower-case ``<prefix>:<name>``
TAG_PREFIXES = ("fn", "op", "clause", "type", "agg", "join", "cte", "set", "win")
_TAG_RE = re.compile(rf"^({'|'.join(TAG_PREFIXES)}):[a-z0-9_]+$")

#: what a variant axis may say (P04: "types, sibling functions, clauses, NULLs,
#: boundaries"); kept open-ended but lower-case and short
VARIANT_AXES = (
    "types",
    "sibling_functions",
    "clauses",
    "nulls",
    "boundaries",
    "aggregates",
    "join_types",
    "frames",
    "collations",
    "order",
    "cardinality",
    "nesting",
)

BATCH_SIZE = 15
DEFAULT_LIMIT = 60

#: how much of a repro to show, so a batch stays readable
_SETUP_BUDGET = 420
_QUERY_BUDGET = 460

REQUIRED_FIELDS = (
    "number",
    "bug_class",
    "feature_tags",
    "variant_axes",
    "cross_engine_ok",
    "one_line_root_cause",
)


class ClassificationError(ValueError):
    """Raised when a hand-written record breaks the SPEC vocabulary."""


def repros_path(target: str, root: Path | None = None) -> Path:
    """Return ``data/repros/<target>.jsonl``."""
    return (root or REPO_ROOT / "data" / "repros") / f"{target}.jsonl"


def classified_path(target: str, root: Path | None = None) -> Path:
    """Return ``data/classified/<target>.jsonl``."""
    return (root or REPO_ROOT / "data" / "classified") / f"{target}.jsonl"


def mined_path(target: str, root: Path | None = None) -> Path:
    """Return ``data/mined/<target>/issues.jsonl``."""
    return (root or REPO_ROOT / "data" / "mined") / target / "issues.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, returning [] when it does not exist."""
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]


def _truncate(text: str, budget: int) -> str:
    text = text.strip()
    return text if len(text) <= budget else text[: budget - 3] + "..."


def batch_items(
    target: str,
    limit: int = DEFAULT_LIMIT,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent issues that have a self-contained repro.

    Most recent means the order of ``data/mined``, which P03 wrote newest-closed
    first. Each item carries only what P04 allows to be read: title, repro SQL,
    and changed file names.
    """
    repros = load_jsonl(repros_path(target, root and root / "repros"))
    by_issue: dict[int, dict[str, Any]] = {}
    for repro in repros:
        by_issue.setdefault(int(repro["number"]), repro)

    issues = load_jsonl(mined_path(target, root and root / "mined"))
    items: list[dict[str, Any]] = []
    for issue in issues:
        number = int(issue["number"])
        repro = by_issue.get(number)
        if repro is None:
            continue
        items.append(
            {
                "number": number,
                "title": issue.get("title", ""),
                "setup": repro["setup"],
                "query": repro["query"],
                # names only: P04 forbids reading the diff
                "fix_files": issue.get("fix_files") or [],
                "source": repro["source"],
                "repro_count": sum(1 for r in repros if int(r["number"]) == number),
            }
        )
        if len(items) >= limit:
            break
    return items


def batches(
    target: str,
    limit: int = DEFAULT_LIMIT,
    size: int = BATCH_SIZE,
    root: Path | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Yield the batches of items to classify."""
    items = batch_items(target, limit, root)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def render_batch(items: Sequence[dict[str, Any]]) -> str:
    """Render one batch as compact text for a human to read and classify."""
    out: list[str] = []
    for item in items:
        setup = _truncate("; ".join(item["setup"]), _SETUP_BUDGET)
        query = _truncate(item["query"], _QUERY_BUDGET)
        files = [Path(f).name for f in item["fix_files"]][:6]
        out.append(f"#{item['number']} [{item['source']}] {item['title'][:110]}")
        if setup:
            out.append(f"  setup: {setup}")
        out.append(f"  query: {query}")
        out.append(f"  files: {', '.join(files) if files else '(none)'}")
    return "\n".join(out)


def validate_record(record: dict[str, Any]) -> None:
    """Check one classification record against the SPEC vocabularies.

    Raises:
        ClassificationError: on a missing field, an unknown bug class, a feature
            tag outside the SPEC prefixes, an unknown variant axis, or a
            root-cause line that is not one line.
    """
    missing = [f for f in REQUIRED_FIELDS if f not in record]
    if missing:
        raise ClassificationError(f"missing fields: {', '.join(missing)}")
    if not isinstance(record["number"], int):
        raise ClassificationError(f"number must be an int, got {record['number']!r}")
    if record["bug_class"] not in BUG_CLASSES:
        raise ClassificationError(
            f"#{record['number']}: bug_class {record['bug_class']!r} is not in the "
            "SPEC list"
        )
    tags = record["feature_tags"]
    if not isinstance(tags, list) or not tags:
        raise ClassificationError(f"#{record['number']}: feature_tags must be non-empty")
    for tag in tags:
        if not isinstance(tag, str) or not _TAG_RE.match(tag):
            raise ClassificationError(
                f"#{record['number']}: feature tag {tag!r} is not "
                f"<{'|'.join(TAG_PREFIXES)}>:<lower_snake_name>"
            )
    axes = record["variant_axes"]
    if not isinstance(axes, list) or not axes:
        raise ClassificationError(f"#{record['number']}: variant_axes must be non-empty")
    for axis in axes:
        if axis not in VARIANT_AXES:
            raise ClassificationError(
                f"#{record['number']}: variant axis {axis!r} is not one of "
                f"{', '.join(VARIANT_AXES)}"
            )
    if not isinstance(record["cross_engine_ok"], bool):
        raise ClassificationError(f"#{record['number']}: cross_engine_ok must be a bool")
    cause = record["one_line_root_cause"]
    if not isinstance(cause, str) or not cause.strip():
        raise ClassificationError(f"#{record['number']}: one_line_root_cause is empty")
    if "\n" in cause:
        raise ClassificationError(f"#{record['number']}: root cause must be one line")


def classified_numbers(target: str, root: Path | None = None) -> set[int]:
    """Return the issue numbers already classified, for resuming."""
    return {int(r["number"]) for r in load_jsonl(classified_path(target, root))}


def append_records(
    target: str,
    records: Sequence[dict[str, Any]],
    root: Path | None = None,
) -> int:
    """Validate and append records to ``data/classified/<target>.jsonl``.

    Returns:
        How many records were written. Issues already present are skipped, so
        re-running a batch is harmless.

    Raises:
        ClassificationError: if any record is invalid; nothing is written then.
    """
    for record in records:
        validate_record(record)
    path = classified_path(target, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    already = classified_numbers(target, root)
    fresh = [r for r in records if int(r["number"]) not in already]
    with path.open("a", encoding="utf-8") as handle:
        for record in fresh:
            handle.write(
                json.dumps({f: record[f] for f in REQUIRED_FIELDS}, ensure_ascii=False)
                + "\n"
            )
    return len(fresh)


def status(targets: Sequence[str] = ("duckdb", "datafusion", "turso")) -> str:
    """Return a one-line-per-target progress report, for resuming a session."""
    lines: list[str] = []
    for target in targets:
        done = classified_numbers(target)
        items = batch_items(target)
        remaining = [i["number"] for i in items if i["number"] not in done]
        lines.append(
            f"{target:11} classified={len(done)}/{len(items)} "
            f"next={remaining[0] if remaining else '-'}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print a batch to classify, or the progress so far."""
    parser = argparse.ArgumentParser(description="batching for P04 classification")
    parser.add_argument("--target")
    parser.add_argument("--batch", type=int, default=1, help="1-based batch number")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)

    if args.status or not args.target:
        print(status())
        return 0
    all_batches = list(batches(args.target, args.limit))
    if not 1 <= args.batch <= len(all_batches):
        print(f"batch must be 1..{len(all_batches)}")
        return 1
    print(render_batch(all_batches[args.batch - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
