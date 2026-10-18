# Upstream bug reports

Every crash GapHarn finds that survives triage as a **real bug** (not API misuse)
is reported here with its state. Required deliverable (see the term-project plan).

Target: **libarchive** (`libarchive/libarchive`), pinned commit
`16e67684befa5a78ec2121702fa1e95fa755714e`.

| # | Date | Area / API | Bug type | Reproducer | Report | State |
|---|------|-----------|----------|-----------|--------|-------|
| 1 | 2026-10-19 | `archive_write_add_filter_gzip` / `_xz` + `__archive_write_filters_close` | memory leak (262 KB gzip / 9 MB xz per failed archive) | [`upstream/01-write-filter-leak/repro.c`](upstream/01-write-filter-leak/repro.c) | [write-up + patch](upstream/01-write-filter-leak/) · [ready-to-paste issue/PR](upstream/01-write-filter-leak/FILING.md) | **READY TO FILE** — all checks green (reproduces on master `bd7a249e`, patch applies cleanly, 945/945 tests pass, no duplicate found) |

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
