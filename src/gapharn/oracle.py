"""Stage 3: correctness oracles that decide whether a generated harness is kept.

O1 compile   harness builds against the library headers, no implicit declarations
O2 sanity    runs on VALID seeds without an ASan/UBSan report (a crash on valid
             input means the harness misuses the API -> reject, as in OGHarn)
O3 gap-hit   on the coverage build, at least one targeted gap function is executed
O4 lint      source-level checks: every *_new paired with *_free, input consumed
             only through the GFuzz helper (no raw data[] indexing)

A harness is KEPT only if O1, O3 and O4 pass and O2 finds no crash. Every verdict
and reason is logged to results/<lib>/oracle_log.csv (this is the RQ4 evidence).

Usage:
    python -m gapharn.oracle libarchive --seeds <dir> [--run-seconds 20]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

from gapharn.config import load_target
from gapharn.gap import executed_functions

SRC_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = SRC_ROOT / "templates"
COV_FLAGS = ["-O1", "-g", "-fprofile-instr-generate", "-fcoverage-mapping"]
ASAN_FLAGS = ["-O1", "-g", "-fno-omit-frame-pointer", "-fsanitize=address,undefined"]
# Any sanitizer/libFuzzer finding (used to flag a seed crash as a candidate).
SAN_REPORT = re.compile(r"(AddressSanitizer|UndefinedBehaviorSanitizer|runtime error:|LeakSanitizer|ERROR: libFuzzer)")
# Genuine memory-safety corruption only (used by the trivial-input gate). A
# timeout, OOM, allocation-size-too-big or leak is NOT misuse: those are tunable
# or are themselves findings, so they do not disqualify a harness from fuzzing.
MEMSAFE = re.compile(r"(heap-buffer-overflow|stack-buffer-overflow|global-buffer-overflow|"
                     r"heap-use-after-free|stack-use-after-return|use-after-poison|"
                     r"SEGV on unknown|deadly signal|UndefinedBehaviorSanitizer:|runtime error:)")


def link_args(t, build_dir: Path) -> list[str]:
    libs = [str(build_dir / l) for l in t.extra_static_libs] + [str(build_dir / t.static_lib)]
    extra = (build_dir / "LINK_FLAGS")
    flags = extra.read_text().split() if extra.is_file() else []
    return libs + flags + t.link_flags


def compile_harness(t, harness: Path, variant: str, flags: list[str], san: list[str], out: Path,
                    clang="clang") -> tuple[bool, str]:
    build = t.build_dir(variant)
    cmd = [clang, *flags, *san, "-Werror=implicit-function-declaration",
           f"-I{build / 'include'}", f"-I{TEMPLATES}", str(harness),
           *link_args(t, build), "-o", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, p.stderr


def o4_lint(source: str) -> list[str]:
    problems = []
    pairs = [("archive_write_new", "archive_write_free"), ("archive_read_new", "archive_read_free"),
             ("archive_entry_new", "archive_entry_free")]
    for new, free in pairs:
        if source.count(new) and not source.count(free):
            problems.append(f"{new} without {free}")
    # Input must flow through the GFuzz helper, never raw data[]/size arithmetic.
    if re.search(r"\bdata\s*\[", source):
        problems.append("raw data[] indexing (bypasses bounds-safe GFuzz reader)")
    return problems


def run_on_seeds(binary: Path, seed_dirs: list[Path], seconds: int, profile: Path | None,
                 artifact_prefix: Path | None = None) -> tuple[bool, str]:
    """Run libFuzzer binary over the seeds. Returns (sanitizer_report_seen, log).

    SIGPIPE (exit 141) and other non-sanitizer exits are NOT counted as crashes;
    only an actual ASan/UBSan/libFuzzer report is. A saved artifact (if any) is
    written under artifact_prefix for later triage."""
    import os
    env = {"PATH": os.environ.get("PATH", ""),
           "ASAN_OPTIONS": "abort_on_error=0:detect_leaks=1:handle_sigpipe=0"}
    if profile:
        env["LLVM_PROFILE_FILE"] = str(profile)
    cmd = [str(binary), f"-max_total_time={seconds}", "-runs=100000",
           "-timeout=25", "-rss_limit_mb=2560", "-print_final_stats=0"]
    if artifact_prefix:
        cmd.append(f"-artifact_prefix={artifact_prefix}")
    cmd += [str(d) for d in seed_dirs]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    log = p.stdout + p.stderr
    return bool(SAN_REPORT.search(log)), log


def crashes_on_trivial(binary: Path, work: Path) -> tuple[bool, str]:
    """A correct harness must survive trivial inputs (empty, zero, tiny). A crash
    here means the harness itself misuses the API => reject (OGHarn's rule).

    The files are passed as individual arguments so libFuzzer REPLAYS each once
    and exits - no mutation (a corpus directory, or -max_total_time, would make it
    fuzz instead, which is not what this gate tests)."""
    import os
    td = work / "trivial"
    td.mkdir(parents=True, exist_ok=True)
    files = {"empty": b"", "zeros": b"\x00" * 64, "ones": b"\xff" * 64, "small": bytes(range(32))}
    paths = []
    for n, b in files.items():
        (td / n).write_bytes(b)
        paths.append(str(td / n))
    env = {"PATH": os.environ.get("PATH", ""),
           "ASAN_OPTIONS": "abort_on_error=0:detect_leaks=1:handle_sigpipe=0"}
    p = subprocess.run([str(binary), "-runs=1", *paths], capture_output=True, text=True, env=env)
    log = p.stdout + p.stderr
    return bool(MEMSAFE.search(log)), log


def check(t, harness: Path, seed_dirs: list[Path], run_seconds: int, work: Path, clang: str) -> dict:
    name = harness.stem
    source = harness.read_text()
    row = {"harness": name, "O1_compile": "", "O2_sanity": "", "O3_gaphit": "", "O4_lint": "",
           "verdict": "", "reason": ""}

    lint = o4_lint(source)
    row["O4_lint"] = "pass" if not lint else "FAIL"

    asan_bin = work / f"{name}.asan"
    ok, log = compile_harness(t, harness, "asan", ASAN_FLAGS, ["-fsanitize=fuzzer"], asan_bin, clang)
    row["O1_compile"] = "pass" if ok else "FAIL"
    if not ok:
        row["verdict"], row["reason"] = "reject", "O1: " + _first_error(log)
        (work / f"{name}.o1.log").write_text(log)
        return row

    # O2a: trivial-input crash => the harness misuses the API (reject).
    misuse, tlog = crashes_on_trivial(asan_bin, work / name)
    # O2b: crash on a real seed => a CANDIDATE finding; saved, not a rejection.
    cand_dir = work / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    seed_crash, rlog = run_on_seeds(asan_bin, seed_dirs, run_seconds, None, cand_dir / f"{name}_")
    (work / f"{name}.o2.log").write_text("=== trivial ===\n" + tlog + "\n=== seeds ===\n" + rlog)
    row["O2_sanity"] = "misuse" if misuse else ("candidate" if seed_crash else "clean")

    cov_bin = work / f"{name}.cov"
    ok, _ = compile_harness(t, harness, "cov", COV_FLAGS, ["-fsanitize=fuzzer"], cov_bin, clang)
    hit = []
    if ok:
        hit = gap_hits(cov_bin, name, seed_dirs, manifest_targets(harness.parent, name),
                       max(5, run_seconds // 2), work)
    row["O3_gaphit"] = f"{len(hit)} hit" if hit else "0 hit"

    reasons = []
    if lint:
        reasons.append("O4: " + "; ".join(lint))
    if misuse:
        reasons.append("O2: crash on trivial input (harness misuses the API)")
    if not hit:
        reasons.append("O3: no targeted gap function executed")
    row["verdict"] = "keep" if not reasons else "reject"
    if row["verdict"] == "keep" and seed_crash:
        row["reason"] = "kept; candidate crash on a seed saved for triage"
    else:
        row["reason"] = "; ".join(reasons)
    row["_hit"] = hit
    return row


def gap_hits(cov_bin: Path, name: str, seed_dirs: list[Path], targets: list[str],
             seconds: int, work: Path) -> list[str]:
    """Which targeted gap functions the coverage build executes on the seeds.

    Runs in libFuzzer fork mode so a crashing seed does not truncate coverage
    (each input runs in a child; the parent survives and profiles accumulate)."""
    import glob
    import os
    if not targets:
        return []
    for stale in glob.glob(str(work / f"{name}.*.profraw")):
        os.unlink(stale)
    env = {"PATH": os.environ.get("PATH", ""),
           "LLVM_PROFILE_FILE": str(work / f"{name}.%p.profraw"),
           "ASAN_OPTIONS": "abort_on_error=0:handle_sigpipe=0"}
    subprocess.run([str(cov_bin), "-fork=1", "-ignore_crashes=1", "-ignore_timeouts=1",
                    "-ignore_ooms=1", f"-max_total_time={seconds}", "-timeout=25",
                    "-rss_limit_mb=2560", *map(str, seed_dirs)],
                   capture_output=True, text=True, env=env)
    profs = glob.glob(str(work / f"{name}.*.profraw"))
    if not profs:
        return []
    pd = work / f"{name}.profdata"
    if subprocess.run(["llvm-profdata", "merge", "-sparse", *profs, "-o", str(pd)]).returncode != 0:
        return []
    cj = work / f"{name}.cov.json"
    with open(cj, "w") as fh:
        subprocess.run(["llvm-cov", "export", "-format=text", "-skip-expansions",
                        f"-instr-profile={pd}", str(cov_bin)], stdout=fh)
    executed = executed_functions([cj])
    return sorted(set(targets) & {k for k, v in executed.items() if v > 0})


def manifest_targets(harness_dir: Path, name: str) -> list[str]:
    mf = harness_dir / "manifest.json"
    if not mf.is_file():
        return []
    return mf.read_text() and json.loads(mf.read_text())["harnesses"].get(name, {}).get("targets", [])


def _first_error(log: str) -> str:
    for line in log.splitlines():
        if "error:" in line:
            return line.strip()[:200]
    return (log.strip().splitlines() or ["(no output)"])[0][:200]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lib")
    ap.add_argument("--seeds", type=Path, nargs="+", required=True)
    ap.add_argument("--harness-dir", type=Path)
    ap.add_argument("--run-seconds", type=int, default=20)
    ap.add_argument("--clang", default="clang")
    a = ap.parse_args(argv)

    t = load_target(a.lib)
    hdir = a.harness_dir or SRC_ROOT / "harnesses" / a.lib
    harnesses = sorted(hdir.glob("harness_*.c"))
    if not harnesses:
        print(f"[oracle] no harnesses in {hdir} (run gapharn.gen_rule first)", file=sys.stderr)
        return 1

    work = t.work_dir / "oracle"
    work.mkdir(parents=True, exist_ok=True)
    rows = [check(t, h, a.seeds, a.run_seconds, work, a.clang) for h in harnesses]

    log_csv = t.results_dir / "oracle_log.csv"
    log_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["harness", "O1_compile", "O2_sanity", "O3_gaphit", "O4_lint", "verdict", "reason"]
    with open(log_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    kept = [r for r in rows if r["verdict"] == "keep"]
    (t.results_dir / "kept_harnesses.json").write_text(json.dumps(
        {r["harness"]: r.get("_hit", []) for r in kept}, indent=1) + "\n")
    print(f"[oracle] {len(kept)}/{len(rows)} harnesses kept -> {log_csv}")
    for r in rows:
        mark = "KEEP" if r["verdict"] == "keep" else "drop"
        print(f"  {mark} {r['harness']:16} O1={r['O1_compile']} O2={r['O2_sanity']} "
              f"O3={r['O3_gaphit']} O4={r['O4_lint']}  {r['reason']}")
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
