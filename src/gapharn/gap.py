"""Stage 0: which exported API functions do the existing harnesses never execute?

    API      = functions declared in the public headers (api.json from gapharn.apimodel)
               ∩ global text symbols defined in the built library (nm)
    reached  = API functions with execution count > 0 in any llvm-cov export JSON
    GAP      = API − reached

Usage:
    python -m gapharn.gap <lib>                    # default paths for a configured target
    python -m gapharn.gap --api api.json --lib libx.a --cov a.json b.json --out gap.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

# nm symbol types that denote a defined global function (T = text, W = weak).
_FUNC_TYPES = {"T", "W"}


def parse_nm(text: str) -> set[str]:
    """Defined global function symbols from `nm` output (works for .a and .so)."""
    names = set()
    for line in text.splitlines():
        parts = line.split()
        # "<addr> <type> <name>"; archive member headers ("foo.o:") and blank lines are skipped
        if len(parts) == 3 and parts[1] in _FUNC_TYPES:
            names.add(parts[2].split("@")[0])  # drop symbol versions (foo@@VER_1)
    return names


def exported_functions(lib: Path) -> set[str]:
    cmd = ["nm", "--defined-only"]
    cmd += ["-D"] if ".so" in lib.name else ["-g"]
    res = subprocess.run(cmd + [str(lib)], capture_output=True, text=True, check=True)
    return parse_nm(res.stdout)


def executed_functions(cov_jsons: list[Path]) -> Counter:
    """Max execution count per function name over llvm-cov `export -format=text` files."""
    counts: Counter = Counter()
    for path in cov_jsons:
        data = json.loads(Path(path).read_text())
        for export in data.get("data", []):
            for fn in export.get("functions", []):
                name = fn["name"]
                counts[name] = max(counts[name], fn.get("count", 0))
    return counts


def api_prefix(name: str) -> str:
    """Group key for summaries: the first two '_'-separated parts (archive_write_open -> archive_write)."""
    parts = name.split("_")
    return "_".join(parts[:2]) if len(parts) > 2 else parts[0]


def compute_gap(
    api_functions: list[dict],
    exported: set[str] | None,
    executed: Counter,
    include_deprecated: bool = False,
) -> dict:
    api = [
        f for f in api_functions
        if (exported is None or f["name"] in exported) and (include_deprecated or not f.get("deprecated"))
    ]
    reached = sorted(f["name"] for f in api if executed.get(f["name"], 0) > 0)
    reached_set = set(reached)
    gap = [f for f in api if f["name"] not in reached_set]

    def group(key) -> dict[str, dict[str, int]]:
        rows: dict[str, dict[str, int]] = {}
        for f in api:
            row = rows.setdefault(key(f), {"api": 0, "reached": 0, "gap": 0})
            row["api"] += 1
            row["reached" if f["name"] in reached_set else "gap"] += 1
        return dict(sorted(rows.items(), key=lambda kv: -kv[1]["gap"]))

    declared_not_exported = sorted(
        f["name"] for f in api_functions if exported is not None and f["name"] not in exported
    )
    return {
        "api_total": len(api),
        "reached_total": len(reached),
        "gap_total": len(gap),
        "gap_ratio": round(len(gap) / len(api), 4) if api else 0.0,
        "by_prefix": group(lambda f: api_prefix(f["name"])),
        "by_header": group(lambda f: f.get("header") or "?"),
        "reached": reached,
        "gap": [
            {"name": f["name"], "header": f.get("header"), "line": f.get("line"),
             "signature": signature(f)}
            for f in gap
        ],
        "declared_not_exported": declared_not_exported,
    }


def signature(f: dict) -> str:
    params = ", ".join(f"{p['type']} {p['name']}".strip() for p in f.get("params", []))
    if f.get("variadic"):
        params = f"{params}, ..." if params else "..."
    return f"{f['return']['type']} {f['name']}({params or 'void'})"


def format_summary(name: str, g: dict, top: int = 12) -> str:
    # Per-header grouping reads better for camelCase APIs (libxml2, libtiff);
    # with only a few headers (libarchive), name prefixes are more informative.
    key = "by_header" if len(g["by_header"]) > 3 else "by_prefix"
    label = "header" if key == "by_header" else "prefix"
    meta = g.get("measurement") or {}
    lines = [
        f"{name}: {g['api_total']} API functions, {g['reached_total']} reached by existing harnesses, "
        f"gap = {g['gap_total']} ({100 * g['gap_ratio']:.1f}%)",
    ]
    if meta:
        lines.append(f"  measured {meta.get('harnesses_measured')}/{meta.get('harnesses_total')} harnesses, "
                     f"{meta.get('fuzz_seconds_per_harness')} s fuzzing each")
    lines.append(f"  {label:<34}{'api':>6}{'reached':>9}{'gap':>6}")
    for k, row in list(g[key].items())[:top]:
        lines.append(f"  {k:<34}{row['api']:>6}{row['reached']:>9}{row['gap']:>6}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lib_name", nargs="?", help="target name (uses default paths)")
    ap.add_argument("--api", type=Path, help="api.json from gapharn.apimodel")
    ap.add_argument("--lib", type=Path, help="built library (.a or .so) for the exported-symbol filter")
    ap.add_argument("--cov", type=Path, nargs="*", help="llvm-cov export JSON files of existing harnesses")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--include-deprecated", action="store_true")
    a = ap.parse_args(argv)

    name = a.lib_name or "library"
    measurement = None
    if a.lib_name:
        from gapharn.config import load_target

        t = load_target(a.lib_name)
        a.api = a.api or t.results_dir / "api.json"
        a.lib = a.lib or (t.build_dir("cov") / t.static_lib if t.static_lib else None)
        if a.cov is None:
            a.cov = sorted((t.work_dir / "existing-cov" / "json").glob("*.json"))
        a.out = a.out or t.results_dir / "gap.json"
        meta = t.work_dir / "existing-cov" / "META.json"
        if meta.is_file():
            measurement = json.loads(meta.read_text())
    if not a.api or a.cov is None or not a.out:
        ap.error("give a target name, or --api, --cov and --out")
    if not a.cov:
        print("[gap] warning: no coverage JSON given -> every API function counts as gap", file=sys.stderr)

    model = json.loads(a.api.read_text())
    exported = exported_functions(a.lib) if a.lib else None
    g = compute_gap(model["functions"], exported, executed_functions(a.cov), a.include_deprecated)
    g = {"library": name, "coverage_files": [p.name for p in a.cov], "measurement": measurement, **g}
    if measurement and measurement["harnesses_measured"] < measurement["harnesses_total"]:
        print(f"[gap] WARNING: only {measurement['harnesses_measured']}/{measurement['harnesses_total']} "
              "existing harnesses were measured; the gap is overestimated", file=sys.stderr)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(g, indent=1) + "\n")
    print(format_summary(name, g))
    print(f"[gap] -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
