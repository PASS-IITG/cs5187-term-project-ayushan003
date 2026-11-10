"""``python -m graft.reproduce`` — replay the chain that produced defect #1.

This is the headline result, start to finish, in one command. It is deterministic:
every random choice comes from a seeded generator, so the output is the same on
every run and on every machine.

The chain has five steps:

1. the source — a closed, reproduced DuckDB bug report that uses ``LPAD``;
2. the template — that report's query with its literals replaced by typed holes;
3. the fill — boundary values substituted back in, at a fixed seed;
4. the blind spot — why Turso's own fuzzer cannot produce this statement;
5. the defect — the statement run against a pre-fix and a post-fix build.

Steps 1–4 need nothing but this repository. Step 5 needs two ``tursodb``
binaries and is skipped, with a message, when they are absent: it never reports
an engine result it did not obtain.

    python -m graft.reproduce                      # all five steps
    python -m graft.reproduce --skip-engines       # steps 1-4 only

Binary locations, overridable:

    GRAFT_TURSO_BIN          post-fix build (default: configs/targets.yaml)
    GRAFT_TURSO_PREFIX_BIN   pre-fix build at b588fbdc6
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from graft.config import REPO_ROOT, get_target
from graft.corpus.templates import instantiate, load_templates

#: the DuckDB report the shape comes from. Its fix test carried four
#: statements, all of which became templates; this is the one that reached
#: defect #1.
SOURCE_ISSUE = 27846
SOURCE_REPO = "duckdb/duckdb"
SOURCE_QUERY = (
    "select LPAD('x', 10, '0'), LPAD('x', 4, '\U0001F600'), "
    "LPAD('x', 3, '\U0001F600ab'), RPAD('x', 3, '\U0001F600ab')"
)

#: defect #1, as filed
FINDING_1 = "select LPAD('a', 9223372036854775807, 'b');"

#: the seed whose fill reaches the defect
SEED = 0

#: how many seeds to sample when reporting the fill rate
SAMPLE = 4000

#: the boundary value the defect needs
BOUNDARY = "9223372036854775807"

#: Turso's own literal policy, quoted from its source. Verify with:
#:   grep -A16 'impl Default for LiteralConfig' \
#:     testing/differential-oracle/sql_gen/src/policy.rs
TURSO_LITERAL_POLICY = """    int_min: -1_000_000,
    int_max:  1_000_000,
    string_min_len: 1,
    string_max_len: 50,
    string_charset: StringCharset::Alphanumeric,"""

PREFIX_BIN_ENV = "GRAFT_TURSO_PREFIX_BIN"
DEFAULT_PREFIX_BIN = "~/targets/turso-prefix/target/release/tursodb"


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def source_repro() -> dict:
    """Return the mined DuckDB report whose shape the first defects came from."""
    path = REPO_ROOT / "data" / "repros" / "duckdb.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("query") == SOURCE_QUERY:
            return record
    raise SystemExit(f"{path}: no record matching the source query")


def step_1_source() -> dict:
    rule("STEP 1  The source: someone else's bug report")
    record = source_repro()
    print(f"  {SOURCE_REPO}#{record['number']}, closed and labelled reproduced.")
    print(f"  Extracted from: {record.get('source')}  (the fix's own test)")
    print("\n  Its query:\n")
    print(f"    {record['query']}")
    print("\n  A DuckDB maintainer wrote this to reproduce a real DuckDB defect:")
    print("  pad functions mishandling multi-byte fill characters. Nothing about")
    print("  it mentions Turso.")
    return record


def step_2_template(record: dict):
    rule("STEP 2  The template: keep the shape, forget the values")
    templates = load_templates()
    from_issue = [t for t in templates if str(t.origin_issue) == str(SOURCE_ISSUE)]
    match = next(t for t in templates if t.query.startswith("select LPAD({s4}"))
    print(f"  Corpus loaded: {len(templates)} templates from {SOURCE_REPO}.")
    print(f"  Of those, {len(from_issue)} came from issue #{SOURCE_ISSUE} alone.")
    print("\n  This report became:\n")
    print(f"    {match.query}")
    print("\n  Holes, by type:")
    for name, kind in match.holes:
        print(f"    {name:<4} {kind}")
    print("\n  Only literals are replaced. The call structure a maintainer chose")
    print("  is left exactly as it was, so every instantiation is valid SQL.")
    return match


def step_3_fill(template) -> str:
    rule("STEP 3  The fill: boundary values, at a fixed seed")
    _setup, query = instantiate(template, random.Random(SEED))
    print(f"  seed={SEED}:\n")
    print(f"    {query}")
    hits = sum(
        1 for s in range(SAMPLE)
        if BOUNDARY in instantiate(template, random.Random(s))[1]
    )
    pct = 100.0 * hits / SAMPLE
    print(f"\n  Over seeds 0-{SAMPLE - 1}, this one template places {BOUNDARY}")
    print(f"  into a pad call in {hits}/{SAMPLE} of them ({pct:.1f}%).")
    print("\n  The engine's own limit is supposed to reject an oversized request")
    print("  with a SQL error. Step 5 shows what happened instead.")
    return query


def step_4_blind_spot() -> None:
    rule("STEP 4  The blind spot: why Turso's own fuzzer cannot write this")
    print("  Turso ships a differential fuzzer of its own, in CI. Its literal")
    print("  policy (testing/differential-oracle/sql_gen/src/policy.rs,")
    print("  impl Default for LiteralConfig) reads:\n")
    print(TURSO_LITERAL_POLICY)
    print("\n  Four independent limits, each checkable in one command:\n")
    print("   1. vocabulary   grep -ri 'lpad\\|rpad' testing/differential-oracle/")
    print("                   -> no matches. It never calls these functions.")
    print(f"   2. integers     int_max is 1_000_000. {BOUNDARY}")
    print("                   is outside the domain it draws from.")
    print("   3. strings      StringCharset::Alphanumeric cannot produce the")
    print("                   multi-byte fill Defect #4 needs.")
    print("   4. oracle       SQLite has no LPAD, so a SQLite-differential")
    print("                   oracle could not judge the answer even if the")
    print("                   statement were generated.")
    print("\n  Hence the method: take the statement from a project whose")
    print("  maintainers already wrote it.")


def engine_run(binary: str, sql: str, mem_kb: int | None = None) -> tuple[int, str, float]:
    """Run one statement and return (exit status, first output line, seconds)."""
    argv: list[str] = []
    if mem_kb is not None:
        argv += ["bash", "-c", f"ulimit -v {mem_kb}; exec \"$0\" :memory:", binary]
    else:
        argv += [binary, ":memory:"]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, input=sql, capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT after 180 s", time.monotonic() - start
    elapsed = time.monotonic() - start
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, (out[0] if out else ""), elapsed


def resolve_binaries() -> tuple[str | None, str | None]:
    post = os.environ.get("GRAFT_TURSO_BIN") or get_target("turso").binary
    post_path = Path(os.path.expanduser(post)) if post else None
    pre = os.environ.get(PREFIX_BIN_ENV, DEFAULT_PREFIX_BIN)
    pre_path = Path(os.path.expanduser(pre))
    return (
        str(pre_path) if pre_path.is_file() else None,
        str(post_path) if post_path and post_path.is_file() else None,
    )


def step_5_defect(query: str) -> None:
    rule("STEP 5  The defect, before and after our patch")
    pre, post = resolve_binaries()
    if pre is None:
        print(f"  SKIPPED: no pre-fix binary at {DEFAULT_PREFIX_BIN}")
        print(f"  Build one and set {PREFIX_BIN_ENV}:")
        print("    git -C ~/targets/turso worktree add ~/targets/turso-prefix b588fbdc6")
        print("    cd ~/targets/turso-prefix && cargo build --release --bin tursodb")
    if post is None:
        print("  SKIPPED: no post-fix binary. Set GRAFT_TURSO_BIN.")
    if pre is None or post is None:
        print("\n  Steps 1-4 above need no engine and are unaffected.")
        return

    print(f"  pre-fix  b588fbdc6  {pre}")
    print(f"  post-fix 7f0a9e6f4  {post}")

    for label, sql, mem_kb in (
        ("defect #1  select LPAD('a', 9223372036854775807, 'b')", FINDING_1, None),
        (
            "defect #4  SELECT length(LPAD('a', 1000000000, '\U0001F600'))",
            "SELECT length(LPAD('a', 1000000000, '\U0001F600'));",
            6_000_000,
        ),
    ):
        print(f"\n  --- {label}")
        if mem_kb:
            print(f"      (memory capped at {mem_kb // 1_000_000} GB so the pre-fix case fails fast)")
        for which, binary in (("before", pre), ("after ", post)):
            code, line, elapsed = engine_run(binary, sql, mem_kb)
            verdict = (
                "process ABORTED" if code in (134, -6)
                else "SQL error" if code == 1
                else f"exit {code}"
            )
            shown = 134 if code == -6 else code
            print(f"      {which}: exit={shown:<4} {elapsed:5.2f}s  {verdict:<16} {line}")


def step_6_upstream() -> None:
    rule("UPSTREAM")
    print("  defect #1  issue  tursodatabase/turso#11219   closed")
    print("             our PR tursodatabase/turso#11225   MERGED  ec86571642a6  +138/-41")
    print("  defect #4  issue  tursodatabase/turso#11260   closed")
    print("             our PR tursodatabase/turso#11259   MERGED  121cd0a8c80b  +54/-5")
    print("\n  defect #4 was not found by GRAFT. We found it by reading the function")
    print("  we were patching for defect #1, recorded it as harmless, and an")
    print("  automated reviewer on our own pull request corrected that judgement")
    print("  before we diagnosed and fixed it. See docs/provenance.md.")
    print("\n  Full write-ups: findings/README.md -- one page per defect")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay the chain that produced defect #1."
    )
    parser.add_argument(
        "--skip-engines", action="store_true",
        help="run steps 1-4 only; do not execute any engine",
    )
    args = parser.parse_args(argv)

    print("GRAFT — grafting a known defect's shape onto another engine")
    record = step_1_source()
    template = step_2_template(record)
    query = step_3_fill(template)
    step_4_blind_spot()
    if args.skip_engines:
        rule("STEP 5  The defect, before and after our patch")
        print("  SKIPPED: --skip-engines")
    else:
        step_5_defect(query)
    step_6_upstream()
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
