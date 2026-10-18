"""Collect seed inputs for a target from files in its source tree.

Files matched by `seed_globs` (relative to the checkout, `**` allowed) are copied
into the corpus directory, named by content hash so duplicates collapse.
Uuencoded files (*.uu, used by libarchive's test suite) are decoded first.

Usage:
    python -m gapharn.seeds <lib> [--out DIR] [--max-size BYTES]
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import sys
from pathlib import Path


def uudecode(text: str) -> bytes:
    """Decode the first `begin ... end` block of a uuencoded file."""
    out = bytearray()
    started = False
    for line in text.splitlines():
        if not started:
            started = line.startswith("begin ")
            continue
        if line.strip() == "end":
            break
        if not line.strip():
            continue
        try:
            out += binascii.a2b_uu(line)
        except binascii.Error:
            # Some encoders pad lines with garbage; keep only the declared bytes
            # (same workaround as CPython's former `uu` module).
            nbytes = (((ord(line[0]) - 32) & 63) * 4 + 5) // 3
            out += binascii.a2b_uu(line[:nbytes])
    return bytes(out)


def collect(src_dir: Path, globs: list[str], out_dir: Path, max_size: int) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for pattern in globs:
        for path in sorted(src_dir.glob(pattern)):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if path.suffix == ".uu":
                data = uudecode(data.decode("latin-1"))
            if not data or len(data) > max_size:
                skipped += 1
                continue
            dest = out_dir / hashlib.sha1(data).hexdigest()[:16]
            if not dest.exists():
                dest.write_bytes(data)
                written += 1
    return written, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lib")
    ap.add_argument("--out", type=Path, help="default: work/<lib>/corpus/seeds")
    ap.add_argument("--max-size", type=int, default=1 << 20)
    a = ap.parse_args(argv)

    from gapharn.config import load_target

    t = load_target(a.lib)
    if not t.seed_globs:
        print(f"[seeds] {a.lib}: no seed_globs in config", file=sys.stderr)
        return 1
    out = a.out or t.work_dir / "corpus" / "seeds"
    written, skipped = collect(t.src_dir, t.seed_globs, out, a.max_size)
    total = sum(1 for _ in out.iterdir())
    print(f"[seeds] {a.lib}: +{written} new, {skipped} skipped (empty/too big), {total} total in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
