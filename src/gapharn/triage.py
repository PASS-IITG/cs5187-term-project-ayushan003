"""Stage 7: triage crash inputs found by a fuzzing campaign.

For every crash artifact (libFuzzer `crash_*` files, or AFL++ `sync_*/*/crashes/id:*`):
  1. re-run on the ASan harness and capture the sanitizer report
  2. cluster by a signature = (bug type, top 3 library stack frames)
  3. keep one representative per cluster, with its stack and the triggering harness

This groups duplicates and gives each unique crash a stable id. It does NOT decide
whether a crash is a real bug vs. API misuse - that judgement is manual (compare
the harness's call sequence against the documented preconditions). The report marks
every cluster "UNREVIEWED"; you set real/misuse by hand (see docs and plan §Stage 7).

Usage:
    python -m gapharn.triage libarchive <campaign_dir> [--minimize]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from gapharn.config import load_target

BUG_RE = re.compile(r"(heap-buffer-overflow|stack-buffer-overflow|global-buffer-overflow|"
                    r"heap-use-after-free|stack-overflow|SEGV|FPE|"
                    r"allocation-size-too-big|out-of-memory|memory leaks?|"
                    r"runtime error: [a-z ]+|LeakSanitizer)", re.I)
FRAME_RE = re.compile(r"#\d+\s+0x[0-9a-f]+\s+in\s+(\S+)\s+([^\s:]+?\.(?:c|h|cc|cpp)):(\d+)")


def find_crashes(campaign: Path) -> list[Path]:
    found = list(campaign.glob("crash_*"))                     # libFuzzer
    found += [p for p in campaign.glob("sync_*/*/crashes/id:*") if p.is_file()]  # AFL++
    found += [p for p in campaign.glob("**/crash-*") if p.is_file()]
    return sorted(p for p in found if p.is_file() and not p.name.endswith(".log"))


def harness_for(crash: Path, bins: dict[str, Path]) -> Path | None:
    # libFuzzer: crash_<harness>_<hash>; AFL++: sync_<harness>/...
    name = crash.name
    for h, b in bins.items():
        if name.startswith(f"crash_{h}_") or f"sync_{h}" in str(crash):
            return b
    return next(iter(bins.values()), None)  # single-harness campaigns


def reproduce(binary: Path, crash: Path) -> str:
    env = {"PATH": __import__("os").environ.get("PATH", ""),
           "ASAN_OPTIONS": "abort_on_error=0:detect_leaks=1:symbolize=1",
           "UBSAN_OPTIONS": "print_stacktrace=1"}
    p = subprocess.run([str(binary), str(crash)], capture_output=True, text=True, timeout=120, env=env)
    return p.stdout + p.stderr


def signature(report: str) -> tuple[str, tuple]:
    bug = (BUG_RE.search(report) or [None])
    bug = bug.group(1).lower() if hasattr(bug, "group") else "unknown"
    frames = []
    for m in FRAME_RE.finditer(report):
        fn, file, line = m.group(1), m.group(2), m.group(3)
        if "gapharn_fuzz" in file or fn.startswith("LLVMFuzzer") or "compiler-rt" in file:
            continue  # skip harness + sanitizer frames; keep library frames
        frames.append(f"{fn}@{Path(file).name}:{line}")
        if len(frames) == 3:
            break
    return bug, tuple(frames)


def minimize(binary: Path, crash: Path, out: Path) -> Path:
    """afl-tmin if available, else return the original."""
    import shutil
    if not shutil.which("afl-tmin"):
        return crash
    env = {"PATH": __import__("os").environ.get("PATH", ""), "AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES": "1"}
    r = subprocess.run(["afl-tmin", "-i", str(crash), "-o", str(out), "--", str(binary)],
                       capture_output=True, text=True, env=env)
    return out if out.exists() and r.returncode == 0 else crash


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lib")
    ap.add_argument("campaign", type=Path)
    ap.add_argument("--minimize", action="store_true", help="shrink each representative with afl-tmin")
    a = ap.parse_args(argv)

    t = load_target(a.lib)
    bins = {p.stem: p for p in a.campaign.glob("*.asan")}
    bins.update({p.name: p for p in a.campaign.glob("harness_*") if p.is_file() and p.suffix == ""})
    crashes = find_crashes(a.campaign)
    if not crashes:
        print(f"[triage] no crash artifacts under {a.campaign}. "
              "If a campaign ran clean, that is a real (negative) result - record it.")
        return 0
    if not bins:
        print(f"[triage] no harness binaries in {a.campaign}; run scripts/fuzz.sh first", file=sys.stderr)
        return 1

    out_dir = t.results_dir / "triage"
    (out_dir / "repro").mkdir(parents=True, exist_ok=True)
    clusters: dict[str, dict] = {}
    for crash in crashes:
        binary = harness_for(crash, bins)
        try:
            report = reproduce(binary, crash)
        except subprocess.TimeoutExpired:
            report = "TIMEOUT (possible hang/infinite loop)"
        bug, frames = signature(report)
        sig = hashlib.sha1(("|".join((bug, *frames))).encode()).hexdigest()[:12]
        if sig not in clusters:
            rep = crash
            if a.minimize:
                rep = minimize(binary, crash, out_dir / "repro" / f"{sig}.min")
            else:
                (out_dir / "repro" / f"{sig}.bin").write_bytes(crash.read_bytes())
                rep = out_dir / "repro" / f"{sig}.bin"
            (out_dir / "repro" / f"{sig}.txt").write_text(report)
            clusters[sig] = {"id": sig, "bug_type": bug, "top_frames": list(frames),
                             "harness": Path(binary).stem, "reproducer": str(rep),
                             "example_input": str(crash), "count": 0, "status": "UNREVIEWED"}
        clusters[sig]["count"] += 1

    report_json = out_dir / "clusters.json"
    report_json.write_text(json.dumps(list(clusters.values()), indent=1) + "\n")
    print(f"[triage] {len(crashes)} crashes -> {len(clusters)} unique clusters -> {report_json}\n")
    for c in clusters.values():
        print(f"  [{c['id']}] {c['bug_type']:28} x{c['count']:<4} {c['harness']}")
        for fr in c["top_frames"]:
            print(f"       {fr}")
    print("\nNext: for each cluster decide real-bug vs API-misuse by checking the harness's")
    print("call sequence against the documented preconditions, then write it up in UPSTREAM.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
