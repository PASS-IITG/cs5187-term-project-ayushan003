# Upstream bug reports

Every crash GapHarn finds that survives triage as a **real bug** (not API misuse)
is reported here with its state. Required deliverable (see the term-project plan).

Target: **libarchive** (`libarchive/libarchive`), pinned commit
`16e67684befa5a78ec2121702fa1e95fa755714e`.

| # | Date | Area / API | Bug type | Reproducer | Report | State |
|---|------|-----------|----------|-----------|--------|-------|
| 1 | 2026-10-19 | `archive_write_add_filter_gzip` / `_xz` + `__archive_write_filters_close` | memory leak (268,096 B gzip / ~9 MB xz per failed archive) | [`repro.c`](upstream/01-write-filter-leak/repro.c), [`differential_test.c`](upstream/01-write-filter-leak/differential_test.c) | **Issue [libarchive#3715](https://github.com/libarchive/libarchive/issues/3715)** · [write-up + patch](upstream/01-write-filter-leak/) | **OPEN** (filed 2026-10-19). PR branch pushed: [`mehul-v0:fix-write-filter-compressor-leak`](https://github.com/mehul-v0/libarchive/tree/fix-write-filter-compressor-leak) — PR to be opened |

Evidence that finding #1 is genuine and not caller error:

* Reproduces on current master `7f149b10` (2026-10-18), and on `bd7a249e` / `16e67684`.
* `differential_test.c`: the identical call sequence with a 2 MB sink (write
  succeeds) leaks nothing; with a 1 KB sink (write fails) leaks 268,096 bytes.
  So the leak is on libarchive's error path, not a missing `free()` in the caller.
* 187 independent inputs from the campaign reached it, through two different
  filters (46 gzip, 97 xz). All 187 are clean after the patch.
* `libarchive_test` on the patched tree: 945 run, 0 failed.
* Precedent: commit `4f9783c8` (2016) fixed this exact bug class on the **read**
  side ("Close filters before freeing", OSS-Fuzz 227/230/239). The write side
  was never fixed.

### Not reportable (investigated and ruled out)

| finding | why not |
|---|---|
| `strlen(NULL)` in pax writer via `archive_entry_set_pathname_utf8` | **Already known**: libarchive#2233, closed as a duplicate of #587. The `*_utf8` setters require the program to select a UTF-8 locale. Reproduces on master but is documented, maintainer-triaged behaviour. |
| write-path timeouts in `_7z_finish_entry` | **Our harness's fault**: it declared a random 64-bit entry size but wrote only a few KB, so writers padded the difference. Fixed by declaring exactly the bytes written. |
| division by zero in `memory_read_skip` | `archive_read_open_memory(a, buf, 0)` gives `read_size == 0`. Low value: that file is libarchive's in-memory **test shim**, and it could not be reproduced through a forced-format path on master. |
| OOM in `xz_filter_read` | Normal large xz allocation under our 2 GB rss cap. Not a bug. |

## Reporting checklist (per bug, before filing)

1. Reproduces on the latest `master` (not only the pinned commit).
2. Not already reported — search the issue tracker.
3. **Not API misuse** — the harness's call sequence obeys documented preconditions
   (man pages, header comments). If it violates one, it is a harness bug: fix the
   generator rule instead and record it as a false positive (RQ4).
4. Minimal standalone reproducer in plain C using only documented API
   (`src/results/libarchive/triage/repro/<id>.bin` + a short driver).
5. ASan/UBSan trace with the library stack frames.
6. Root cause located (file:line) and, where possible, a patch + a regression test.

## Disclosure

- **Memory-safety bug reachable from file/archive input:** follow the project's
  `SECURITY.md` (private disclosure). An acknowledged advisory already counts for
  the course's "implementation" marks.
- **Other crashes (NULL deref in the write/option API, UB, logic/round-trip):**
  open a public issue **and** a PR with the fix and a regression test.
