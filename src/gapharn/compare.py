"""Compare candidate targets from their Stage-0 results to choose the primary target.

For each library it reports:
  * api / reached / gap / gap%        from results/<lib>/gap.json
  * data gap: gap functions taking a buffer, string or FILE* argument.
    Bugs are most likely where untrusted data enters, so this is the main ranking metric.
  * existing harnesses measured        (how much fuzzing already exists)
  * commits in the last 90 days, and fuzz/crash-related fix commits in the last
    365 days, from the pinned checkout's git history (maintainer activity proxy)
  * build time of the cov variant

Decision rule (also printed): drop libraries with < MIN_COMMITS_90D commits in 90 days,
then rank by data gap; the first is the primary target and the second the backup.
Maintainer response time on issues must still be checked by hand.

Usage:
    python -m gapharn.compare libarchive libtiff libxml2 [--out results/target_selection.md]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from gapharn.config import RESULTS_DIR, load_target

MIN_COMMITS_90D = 10
_DATA_BASE = re.compile(r"^(const\s+)?(signed\s+|unsigned\s+)?(char|void)\s*\*$|_IO_FILE\s*\*$")


def takes_data(fn: dict) -> bool:
    """True if any parameter is a byte/char buffer, a string or a FILE*."""
    for p in fn.get("params", []):
        canonical = re.sub(r"\s+", " ", p.get("canonical", "")).strip()
        if p.get("pointer_depth") == 1 and _DATA_BASE.search(canonical):
            return True
    return False


def git_count(src: Path, *args: str) -> int | None:
    try:
        res = subprocess.run(["git", "-C", str(src), "rev-list", "--count", "HEAD", *args],
                             capture_output=True, text=True, check=True)
        return int(res.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def build_seconds(build_info: Path) -> int | None:
    if not build_info.is_file():
        return None
    for line in build_info.read_text().splitlines():
        if line.startswith("seconds="):
            return int(line.split("=", 1)[1])
    return None


def metrics(lib: str) -> dict:
    t = load_target(lib)
    gap = json.loads((t.results_dir / "gap.json").read_text())
    api = {f["name"]: f for f in json.loads((t.results_dir / "api.json").read_text())["functions"]}
    gap_names = [g["name"] for g in gap["gap"]]
    head = head_timestamp(t.src_dir)
    meta = gap.get("measurement") or {}
    return {
        "lib": lib,
        "measured": f"{meta.get('harnesses_measured', '?')}/{meta.get('harnesses_total', '?')}",
        "complete": bool(meta) and meta.get("harnesses_total", 0) > 0
                    and meta.get("harnesses_measured") == meta.get("harnesses_total"),
        "fuzz_s": meta.get("fuzz_seconds_per_harness"),
        "api": gap["api_total"],
        "reached": gap["reached_total"],
        "gap": gap["gap_total"],
        "gap_pct": 100 * gap["gap_ratio"],
        "data_gap": sum(takes_data(api[n]) for n in gap_names if n in api),
        "commits_90d": commits_before(t.src_dir, head, 90),
        "fuzz_fixes_365d": commits_before(t.src_dir, head, 365, "-i", "-E",
                                          "--grep=fuzz|oss-fuzz|ossfuzz|crash|overflow|heap|use.after"),
        "build_s": build_seconds(t.build_dir("cov") / "BUILD_INFO"),
    }


def head_timestamp(src: Path) -> int | None:
    res = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%ct"], capture_output=True, text=True)
    try:
        return int(res.stdout.strip())
    except ValueError:
        return None


def commits_before(src: Path, head_ts: int | None, days: int, *extra: str) -> int | None:
    """Commits in the `days` before the pinned commit (not before "now"), so the
    numbers stay the same when the table is regenerated later."""
    if head_ts is None:
        return None
    return git_count(src, f"--since={head_ts - days * 86400}", *extra)


def rank(rows: list[dict]) -> list[dict]:
    """Only libraries whose existing harnesses were ALL measured (a missing harness
    inflates the gap) and that are actively maintained are ranked."""
    eligible = [r for r in rows if r.get("complete", True) and (r["commits_90d"] or 0) >= MIN_COMMITS_90D]
    return sorted(eligible, key=lambda r: (-r["data_gap"], -r["gap"]))


def fmt(v) -> str:
    return "n/a" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def table(rows: list[dict]) -> str:
    cols = [("lib", "library"), ("api", "API"), ("reached", "reached"), ("gap", "gap"),
            ("gap_pct", "gap %"), ("data_gap", "data gap"), ("measured", "harnesses measured"),
            ("fuzz_s", "fuzz s/harness"),
            ("commits_90d", "commits 90d"), ("fuzz_fixes_365d", "fuzz/crash fixes 365d"),
            ("build_s", "build s")]
    lines = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[k]) for k, _ in cols) + " |" for r in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("libs", nargs="+")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "target_selection.md")
    a = ap.parse_args(argv)

    rows, missing = [], []
    for lib in a.libs:
        try:
            rows.append(metrics(lib))
        except FileNotFoundError:
            missing.append(lib)
    if missing:
        print(f"[compare] no Stage-0 results yet for: {', '.join(missing)} (run make stage0 LIB=...)", file=sys.stderr)
    if not rows:
        return 1

    ranked = rank(rows)
    text = [
        "# Target selection (generated by `python -m gapharn.compare`)", "",
        table(rows), "",
        f"Rule: drop libraries where not every existing harness was measured, or with < {MIN_COMMITS_90D} "
        "commits in the 90 days before the pinned commit; rank the rest by data gap "
        "(gap functions taking a buffer, string or FILE*).", "",
        "Ranking: " + (" > ".join(r["lib"] for r in ranked) if ranked else "no library passes the activity filter"),
        "", "Still check by hand: maintainer response time on the 5 most recent bug reports.",
    ]
    if ranked:
        text.insert(-2, f"Primary: **{ranked[0]['lib']}**" + (f", backup: **{ranked[1]['lib']}**" if len(ranked) > 1 else ""))
        text.insert(-2, "")
    out = "\n".join(text) + "\n"
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(out)
    print(out)
    print(f"[compare] -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
