/* Differential test: the SAME call sequence, changing only the sink capacity.
 *
 *   small sink -> the write fails, the filter is left FATAL, and the compressor
 *                 state leaks (268,096 bytes for gzip)
 *   big sink   -> the write succeeds, close() runs, nothing leaks
 *
 * This is the evidence that the leak belongs to libarchive's error path and is
 * not a missing free() in the caller: the caller code is byte-for-byte identical
 * in both runs.
 *
 * Build:
 *   clang -g -O1 -fsanitize=address -I<src>/libarchive differential_test.c \
 *         <build>/libarchive/libarchive.a -lz -llzma -lxml2 -lzstd -llz4 -lbz2 -o dt
 * Run:
 *   ./dt small
 *   ./dt big
 */
#include <archive.h>
#include <archive_entry.h>
#include <stdio.h>
#include <string.h>

static unsigned char payload[256 * 1024];   /* incompressible */

static void run(char *sink, size_t cap, const char *label) {
    size_t used = 0;
    struct archive *a = archive_write_new();
    archive_write_set_format_ustar(a);
    archive_write_add_filter_gzip(a);
    archive_write_open_memory(a, sink, cap, &used);

    struct archive_entry *e = archive_entry_new();
    archive_entry_set_pathname(e, "f");
    archive_entry_set_filetype(e, AE_IFREG);
    archive_entry_set_perm(e, 0644);
    archive_entry_set_size(e, (la_int64_t)sizeof payload);
    int rh = archive_write_header(a, e);
    la_ssize_t rd = archive_write_data(a, payload, sizeof payload);
    archive_entry_free(e);

    int rc = archive_write_close(a);
    int rf = archive_write_free(a);
    printf("%s: cap=%zu header=%d data=%zd close=%d free=%d\n",
           label, cap, rh, (ssize_t)rd, rc, rf);
}

int main(int argc, char **argv) {
    unsigned s = 12345;
    for (size_t i = 0; i < sizeof payload; i++) {
        s = s * 1103515245u + 12345u;
        payload[i] = (unsigned char)(s >> 16);
    }
    static char small[1024];
    static char big[2 * 1024 * 1024];
    if (argc > 1 && argv[1][0] == 'b')
        run(big, sizeof big, "BIG sink (write succeeds)");
    else
        run(small, sizeof small, "SMALL sink (write fails)");
    return 0;
}
