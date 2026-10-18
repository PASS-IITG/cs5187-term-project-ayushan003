# libarchive: compression filters leak the compressor state when left in the FATAL state

**Status:** candidate, not yet filed upstream (see "Before filing" below)
**Found by:** GapHarn `harness_write` (gap-directed harness for `archive_write_*`)
**Affected:** `master` @ `bd7a249e` (2026-10-17) and `16e67684` (2026-10-03). Not version-specific.
**Type:** memory / resource leak, reachable from the documented public API
**Impact:** ~262 KB leaked per failed gzip archive, ~9 MB per failed xz archive.
A long-running process that creates compressed archives and hits write errors
(full disk, failing write callback, undersized memory sink) leaks that much each
time, so repeated failures exhaust memory.

## Root cause

`__archive_write_filters_close()` (`libarchive/archive_write.c`) only closes
filters whose state is `OPEN`:

```c
/* Do not close filters that are not open */
if (f->state == ARCHIVE_WRITE_FILTER_STATE_OPEN) {
        if (f->close != NULL) {
                ret1 = (f->close)(f);
```

A filter is set to `ARCHIVE_WRITE_FILTER_STATE_FATAL` when a write through it
fails (`archive_write.c:294`, `:328`, `:356`). The compressor's `close` callback
is the **only** place that releases the compression library's internal state:

| filter | `close()` releases | `free()` releases |
|---|---|---|
| gzip (`archive_write_add_filter_gzip.c`) | `deflateEnd(&gzip->stream)` | **no** - only `free_data()` |
| xz/lzma/lzip (`archive_write_add_filter_xz.c`) | `lzma_end(&xz->stream)` | **no** - only `free_data()` |

So once a filter goes FATAL, `close` is skipped, `free` does not compensate, and
zlib's deflate state (~262 KB) or liblzma's encoder state (~9 MB) is leaked -
even though the caller correctly called `archive_write_close()` and
`archive_write_free()`.

## Reproducer

`repro.c` uses only documented public API. The sink is a deliberately small
memory buffer (a legitimate use of `archive_write_open_memory()`); the same
failure occurs with any sink that fails mid-write.

```sh
clang -g -fsanitize=address -I<libarchive-src>/libarchive repro.c \
      <build>/libarchive/libarchive.a -lz -llzma -lbz2 -lzstd -llz4 -lxml2 -o repro
./repro gzip     # LeakSanitizer: 268096 bytes in archive_compressor_gzip_open
./repro xz       # liblzma encoder state
```

Observed on unpatched master:

```
Direct leak of 5952 byte(s) in 1 object(s) allocated from:
    #1 deflateInit2_ (libz.so.1)
    #2 archive_compressor_gzip_open archive_write_add_filter_gzip.c:278
Indirect leak of 65536 byte(s) in 1 object(s)  x4
SUMMARY: AddressSanitizer: 268096 byte(s) leaked in 5 allocation(s).
```

## Proposed fix

`fix-write-filter-leak.patch` (28 insertions, 1 deletion) adds a `stream_valid`
flag to the gzip and xz filter state, set when the compressor is successfully
initialized and cleared by `close()`. Each filter's `free()` callback releases
the compressor state if `close()` never ran. This is deliberately narrow: it does
not change when `close()` is called, so it cannot introduce extra writes on an
error path.

### Verification

| check | result |
|---|---|
| `repro.c` gzip / xz / bzip2 on patched master | no leak |
| **187** leak-triggering inputs from the full campaign, replayed on patched master (46 reached it via gzip, 97 via xz) | **0 still leak** |
| normal successful gzip archive (`close=0 free=0`, 87 bytes written) | works, no double-free |
| patch applies cleanly to pristine `origin/master` (`bd7a249e`) | yes (`git apply --check`) |
| libarchive's own `libarchive_test` suite on the patched tree | **945 run, 0 failed**, 31,835,086 assertions, 0 failed, 220 skipped |

An alternative fix would be to close FATAL filters in
`__archive_write_filters_close()`, but that risks issuing further writes down a
filter chain that is already failing, so the per-filter cleanup is safer.

## Before filing (checklist from ../../UPSTREAM.md)

- [x] Reproduces on latest `master` (`bd7a249e`)
- [x] Uses only documented public API, correct call sequence (close then free)
- [x] Not already reported - searched issues and PRs for `deflateEnd`,
      `lzma_end`, `filters_close`, `WRITE_FILTER_STATE_FATAL`, and
      "leak + gzip/filter": no match. Closest existing reports are different
      leaks: #1302 (`archive_write_client_open`, fixed) and #1740
      (`archive_write_zip_finish_entry`, fixed).
- [x] Minimal standalone reproducer
- [x] Root cause located, patch written
- [x] libarchive test suite green on the patched tree (945/945)
- [ ] **Filed upstream** - public issue + PR. This is a resource leak, not memory
      corruption, so it does not need private disclosure. Ready-to-paste issue
      and PR text is in `FILING.md`.
