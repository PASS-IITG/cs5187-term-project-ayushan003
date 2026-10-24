# libarchive: write-side compressor state is leaked when cleanup is skipped after a fatal error

**Upstream:** issue [#3715](https://github.com/libarchive/libarchive/issues/3715),
PR [#3716](https://github.com/libarchive/libarchive/pull/3716)
**Found by:** GapHarn `harness_write` (gap-directed harness for `archive_write_*`)
**Affected:** `master` @ `7f149b10` (2026-10-18), `bd7a249e`, `16e67684`. Long-standing, not a regression.
**Type:** memory / resource leak, reachable from the documented public API

## Two distinct defects, same shape

`close()` is the only place that releases the compression library's state, and
it is skipped once the write side goes fatal. `free()` does not compensate.

| # | Where | `close()` releases | `free()` releases | Leak |
|---|---|---|---|---|
| 1 | `archive_write_add_filter_gzip.c` | `deflateEnd()` | no | 268,096 B |
| 1 | `archive_write_add_filter_xz.c` (xz/lzma/lzip) | `lzma_end()` | no | **~97 MB** |
| 2 | `archive_write_set_format_zip.c` | `deflateEnd()` in `finish_entry()` only | no | 268,096 B |

For (1), `__archive_write_filters_close()` only closes filters whose state is
`ARCHIVE_WRITE_FILTER_STATE_OPEN`; a filter marked `FATAL` by a failed write
(`archive_write.c:294`, `:328`, `:356`) never gets its `close()`.

For (2), the zip writer calls `deflateInit2()` per entry in
`archive_write_zip_header()` (line 1365) and ends it only in
`archive_write_zip_finish_entry()`. If the archive goes fatal first,
`archive_write_zip_free()` never releases it - even though that function already
uses `tctx_valid` / `cctx_valid` / `hctx_valid` flags for its other resources.

## Evidence

### It is libarchive's error path, not caller error

`differential_test.c` runs the **identical** call sequence twice, changing only
the sink capacity:

| sink | outcome | leak |
|---|---|---|
| 1 KB (write fails) | `close=-30 free=0` | **268,096 bytes** |
| 2 MB (write succeeds) | `close=0 free=0` | none |

If the caller had forgotten a `free()`, both runs would leak. Only the error
path does.

### Scope: 33 leaking format x filter combinations

`filter_matrix.c` and the format x filter sweep on unpatched `7f149b10`:

| filter | formats affected | leak each |
|---|---|---|
| xz / lzma / lzip | ustar, pax, zip, cpio, warc, shar, raw, gnutar | ~97 MB |
| gzip | same 8 formats | 268,096 B |
| any compressing filter + `zip` format | - | +268,096 B (defect 2) |

**After the patch: 0 of 33 combinations leak.**

### Other verification

| check | result |
|---|---|
| 33 format x filter combinations, after the patch | 0 leak |
| normal successful archive | **byte-identical output** (105 bytes, same SHA-256 as pristine), no crash from the second `deflateEnd()` |
| patch applies cleanly to pristine `origin/master` `7f149b10` | yes |
| `libarchive_test` on the revised patch | **945 run, 0 failed**, 31,835,086 assertions, 0 failed, 220 skipped |
| patch applies to current master `8509b99a` | yes |

## Fix (revised after maintainer review)

`fix-write-filter-leak.patch` - 3 files, 40 insertions, **no new struct fields**.

The first version added a `stream_valid` flag. Reviewer `stoeckmann` asked to
avoid it: *"We can avoid a new flag if we release the compressed memory in case
of initialization error in the open function. Then we can check in free if
compressed is NULL or not."* That invariant holds, because the compression
buffer is allocated **before** the stream is initialized:

* gzip / xz: on an initialization failure, `open()` now releases `compressed`
  and NULLs it. A non-NULL buffer therefore means the stream is live, and
  `free()` ends it.
* zip: the buffer is not tied to the stream's lifetime, but the existing
  `entry_compression` field already says whether the entry used deflate, so no
  flag is needed there either. The struct is `calloc()`ed and zlib clears the
  stream state in `deflateEnd()`, so the check is safe either way.

It does not change *when* `close()` is called, so it cannot introduce extra
writes down an already-failing filter chain. The extra `deflateEnd()` /
`lzma_end()` that `free()` performs after a successful `close()` is harmless:
both libraries clear their stream state and return an error code.

## Relationship to other upstream work

* `4f9783c8` (2016, *"Close filters before freeing"*, OSS-Fuzz 227/230/239)
  fixed this exact bug class on the **read** side. The write side was never done.
* PR [#3471](https://github.com/libarchive/libarchive/pull/3471) (draft) covers
  the same general area - cleanup skipped when the write side is fatal - but its
  examples are **file descriptors** (`file_free()` not closing `mine->fd`) and
  **program-filter child pipes**, not the compressor state. It is also described
  as a master-only regression from #3354, whereas this leak is long-standing.
  In that thread the maintainer wrote: *"formats and filters should properly
  release resources in their free routines ... IMHO, this would be the better
  approach here"* - which is the approach this patch takes.

## Files here

| file | purpose |
|---|---|
| `fix-write-filter-leak.patch` | the fix (gzip, xz, zip writer) |
| `repro.c` | minimal standalone reproducer |
| `differential_test.c` | same sequence, only sink size differs - proves it is the error path |
| `filter_matrix.c` | per-filter leak matrix |
| `FILING.md`, `issue-body.md` | the text filed upstream |


## Still outstanding: a third, separate defect (not in this PR)

Replaying all 202 write-harness leak inputs against the patched tree leaves 44
leaking - from a completely different site:

```
isoent_new   archive_write_set_format_iso9660.c:5106    240 bytes per directory entry
```

The iso9660 writer leaks its own `isoent` bookkeeping objects when the archive
goes fatal. This is unrelated to compressor state and is deliberately **not**
included in PR #3716, to keep that review focused. To be filed separately.

### Correction to an earlier claim in this file

An earlier revision recorded "187 leak inputs -> 0 still leak". That measurement
was unreliable (a stale replay binary). The dependable results are the
33-combination matrix and the per-filter matrix, both clean after the patch; the
44 iso9660 leaks above were always a separate defect.
