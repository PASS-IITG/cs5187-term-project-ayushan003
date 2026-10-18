/* Minimal reproducer: a compression filter that enters the FATAL state is never
 * closed, so the compressor library's internal state is leaked.
 *
 * Only documented public API is used. The sink is a deliberately small memory
 * buffer, which is a legitimate way to use archive_write_open_memory(); the same
 * failure happens with any sink that fails mid-write (full disk, failing
 * write callback).
 *
 * Build:
 *   clang -g -fsanitize=address -I<inc> leak_repro.c libarchive.a <libs> -o leak_repro
 * Expect: LeakSanitizer reports a leak allocated in
 *   archive_compressor_xz_init_stream  (liblzma, ~9 MB), or
 *   archive_compressor_gzip_open       (zlib, ~260 KB).
 */
#include <archive.h>
#include <archive_entry.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char sink[1024];           /* too small to hold the compressed result */
static unsigned char payload[256 * 1024];

static int one(const char *name, int (*add_filter)(struct archive *)) {
    size_t used = 0;
    struct archive *a = archive_write_new();
    archive_write_set_format_ustar(a);
    add_filter(a);
    if (archive_write_open_memory(a, sink, sizeof sink, &used) != ARCHIVE_OK) {
        printf("  %-6s open failed: %s\n", name, archive_error_string(a));
        archive_write_free(a);
        return 1;
    }
    struct archive_entry *e = archive_entry_new();
    archive_entry_set_pathname(e, "f");
    archive_entry_set_filetype(e, AE_IFREG);
    archive_entry_set_perm(e, 0644);
    archive_entry_set_size(e, (la_int64_t)sizeof payload);
    int rh = archive_write_header(a, e);
    la_ssize_t rd = -1;
    if (rh == ARCHIVE_OK)
        rd = archive_write_data(a, payload, sizeof payload);   /* overflows the sink */
    archive_entry_free(e);
    int rc = archive_write_close(a);                            /* filter is FATAL -> close skipped */
    int rf = archive_write_free(a);                             /* free does not release lzma/zlib state */
    printf("  %-6s header=%d data=%zd close=%d free=%d\n", name, rh, (ssize_t)rd, rc, rf);
    return 0;
}

int main(int argc, char **argv) {
    /* Incompressible payload so the compressed output exceeds the 1 KB sink. */
    unsigned s = 12345;
    for (size_t i = 0; i < sizeof payload; i++) { s = s * 1103515245u + 12345u; payload[i] = (unsigned char)(s >> 16); }

    const char *which = argc > 1 ? argv[1] : "xz";
    if (!strcmp(which, "xz"))   return one("xz", archive_write_add_filter_xz);
    if (!strcmp(which, "gzip")) return one("gzip", archive_write_add_filter_gzip);
    if (!strcmp(which, "bzip2"))return one("bzip2", archive_write_add_filter_bzip2);
    one("xz", archive_write_add_filter_xz);
    return 0;
}
