### Summary

When a compression write filter fails a write, it is put in the
`ARCHIVE_WRITE_FILTER_STATE_FATAL` state. `__archive_write_filters_close()`
only closes filters whose state is `OPEN`, so the filter's `close()` callback is
skipped - and that callback is the only place that calls `deflateEnd()` (gzip) or
`lzma_end()` (xz/lzma/lzip). The `free()` callback does not release it either, so
the compression library's internal state is leaked.

This happens even though the caller correctly calls `archive_write_close()`
followed by `archive_write_free()`.

Leaked per failed archive:

* gzip: **268,096 bytes** (zlib deflate state)
* xz: **~9 MB** (liblzma encoder state)

A long-running process that creates compressed archives and encounters write
failures (full disk, a failing write callback, or an undersized memory sink)
leaks that much on every failure, so repeated failures exhaust memory.

### Version

Reproduced on `master` at bd7a249e, and on 16e67684. Not version-specific -
the code paths involved are long-standing.

### Relevant code

`libarchive/archive_write.c`, `__archive_write_filters_close()`:

```c
/* Do not close filters that are not open */
if (f->state == ARCHIVE_WRITE_FILTER_STATE_OPEN) {
        if (f->close != NULL) {
                ret1 = (f->close)(f);
```

The filter is set to `ARCHIVE_WRITE_FILTER_STATE_FATAL` on a failed write
(`archive_write.c:294`, `:328`, `:356`).

`archive_compressor_gzip_close()` is what calls `deflateEnd()`:

```c
switch (deflateEnd(&(gzip->stream))) {
```

while `archive_compressor_gzip_free()` only does:

```c
free_data(f->data);
f->data = NULL;
```

`archive_write_add_filter_xz.c` has the identical asymmetry with `lzma_end()`.

### Reproducer

Uses only public API. The sink is a deliberately small memory buffer, which is a
legitimate use of `archive_write_open_memory()`; the same failure occurs with any
sink that fails mid-write.

```c
/* see repro.c in the linked report */
static char sink[1024];                  /* too small for the compressed result */
static unsigned char payload[256 * 1024];/* incompressible */

struct archive *a = archive_write_new();
archive_write_set_format_ustar(a);
archive_write_add_filter_gzip(a);
archive_write_open_memory(a, sink, sizeof sink, &used);

struct archive_entry *e = archive_entry_new();
archive_entry_set_pathname(e, "f");
archive_entry_set_filetype(e, AE_IFREG);
archive_entry_set_perm(e, 0644);
archive_entry_set_size(e, (la_int64_t)sizeof payload);
archive_write_header(a, e);
archive_write_data(a, payload, sizeof payload);   /* overflows the sink */
archive_entry_free(e);

archive_write_close(a);   /* filter is FATAL -> close skipped */
archive_write_free(a);    /* free does not release the zlib state */
```

Build and run:

```sh
clang -g -fsanitize=address -I<src>/libarchive repro.c \
      <build>/libarchive/libarchive.a -lz -llzma -lbz2 -lzstd -llz4 -lxml2 -o repro
./repro gzip
```

Output on unpatched master:

```
Direct leak of 5952 byte(s) in 1 object(s) allocated from:
    #1 deflateInit2_ (libz.so.1)
    #2 archive_compressor_gzip_open archive_write_add_filter_gzip.c:278
Indirect leak of 65536 byte(s) in 1 object(s)   [x4]
SUMMARY: AddressSanitizer: 268096 byte(s) leaked in 5 allocation(s).
```

### Suggested fix

Track whether the compressor was initialized, and release it in `free()` when
`close()` was skipped. PR to follow. I deliberately did not change *when*
`close()` is called, since closing a FATAL filter could issue further writes down
a chain that is already failing.

### How it was found

An automated fuzz harness for libarchive's `archive_write_*` API.