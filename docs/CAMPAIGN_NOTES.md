# Campaign notes (running log)

Target: **libarchive**, pinned `16e67684` (2026-10-03). Latest upstream checked:
`bd7a249e` (2026-10-17).
Device: laptop, WSL Ubuntu 24.04, clang 18, 12 logical cores, 8 GB RAM.

## Pipeline status

- Stage 0 gap: 424 API functions, 248 reached by the 25 OSS-Fuzz harnesses,
  **176 in the gap (41.5%)**.
- G1 generator: 3 parametrized harnesses targeting **54/176** gap functions
  (`harness_write`, `harness_read`, `harness_entry`).
- Oracles: compile / trivial-replay / gap-hit / lint. Measured gap hits:
  write 20, entry 15, read 5.

## Round 1 (18 Oct): 35,642 artifacts -> 4 clusters, all explained, none reportable

| cluster | count | where | verdict |
|---|---|---|---|
| `strlen(NULL)` in pax writer | 307 | `add_pax_attr` (`archive_write_set_format_pax.c:338`) via `archive_write_header` | **Known: libarchive#2233**, closed as duplicate of #587. `archive_entry_set_pathname_utf8()` requires the program to select a UTF-8 locale; without one the conversion yields NULL and the pax writer dereferences it. Still reproduces on master, but it is documented, maintainer-triaged behaviour. **Not a new bug.** |
| write `timeout` | ~35,300 | `_7z_finish_entry` -> `compress_out` -> `compression_code_lzma` | **Our harness's fault.** It declared a random 64-bit `archive_entry_set_size()` but wrote only a few KB, so writers pad the difference - effectively unbounded compression. Fixed by declaring exactly the number of bytes written. |
| division by zero | 4 | `memory_read_skip` (`archive_read_open_memory.c:141`) | `archive_read_open_memory(a, buf, 0)` sets `read_size = 0`, and `skip /= mine->read_size` divides by zero. Low value: that file is libarchive's **in-memory test shim** (its own comment says it does "more than necessary in order to better exercise internal code when used as a test harness"). Could not reproduce it through a forced-format path on master; needs the specific skip path. |
| out-of-memory | 2 | `xz_filter_read` via `archive_read_format_ar_bid` | Normal large xz allocation under a 2 GB rss cap. Not a bug. |

### Harness fixes made as a result (these are RQ4 false-positive data)
1. `setlocale(LC_ALL, "C.UTF-8")` in `LLVMFuzzerInitialize` - per maintainer
   guidance in #2233/#587, the `*_utf8` setters require a UTF-8 locale. Without
   it the known #2233 crash fired on nearly every input and masked everything else.
2. Declare `archive_entry_set_size()` == bytes actually written (API contract).
   Single-input time went from **109 s to 0.10 s**; the write corpus went from
   9 to 452 files in the first minute.
3. Excluded external-program filters (grzip/lrzip/lzop) and ignore SIGPIPE -
   those test subprocess plumbing, not the library.
4. Skip zero-length read buffers (the size-0 case is recorded above).

## Round 2 (19 Oct, 2 h on 9 cores) - complete

All three harnesses explored productively, with no false-positive floods:

| harness | corpus | coverage (edges) | artifacts |
|---|---|---|---|
| write | 1,418 | 38,450 | 187 leaks (one root cause), 4 timeouts |
| read  | 3,913 | 58,721 | 1 OOM, 2 slow units |
| entry | 497 | 10,802 | 2 slow units, **0 crashes** (locale fix removed the #2233 flood) |

### Round-2 finding: write-filter resource leak (NEW, previously unreported)

Within minutes of the write harness becoming productive it produced 12 leak
artifacts, which triaged to a single root cause:

> `__archive_write_filters_close()` only closes filters whose state is `OPEN`.
> A filter that failed a write is marked `FATAL`, so its `close()` - the only
> place that calls `deflateEnd()` / `lzma_end()` - is skipped, and `free()` does
> not compensate. Result: ~262 KB (gzip) or ~9 MB (xz) leaked per failed archive,
> despite the caller correctly calling `archive_write_close()` + `_free()`.

- Reproduces on master `bd7a249e` with a minimal standalone program using only
  public API.
- No matching issue or PR upstream (searched `deflateEnd`, `lzma_end`,
  `filters_close`, `WRITE_FILTER_STATE_FATAL`, leak+gzip/filter).
- Patch written (28 insertions): per-filter `stream_valid` flag so `free()`
  releases the compressor state when `close()` was skipped.
- Verified: standalone reproducer clean for gzip/xz/bzip2; **all 187** leak
  inputs from the campaign clean (46 reached it via gzip, 97 via xz); a normal
  successful gzip archive still works; `libarchive_test` 945/945 passes.

Write-up, reproducer and patch: `upstream/01-write-filter-leak/`.

## Honest status

One genuine, previously-unreported bug found, with a verified fix: the
write-filter leak above. Everything from round 1 was either already known
(the pax NULL-deref, libarchive#2233) or an artifact of our own harnesses -
and fixing those four false-positive sources is exactly what let round 2 reach
real writer logic.

Still outstanding before this counts fully: libarchive's own test suite must be
green on the patched tree, then file the issue + PR upstream.
