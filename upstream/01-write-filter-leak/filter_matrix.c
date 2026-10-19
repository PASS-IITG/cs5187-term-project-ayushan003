/* Which write filters leak their compressor state when the filter is left in
 * the FATAL state?
 *
 * For each filter: build an archive into a 1 KB memory sink and write 256 KB of
 * incompressible data. The sink overflows, the write fails, the filter is marked
 * ARCHIVE_WRITE_FILTER_STATE_FATAL, and __archive_write_filters_close() then
 * skips its close() callback. Each case runs in a child so one leak report does
 * not hide the next.
 *
 * Build:
 *   clang -g -O1 -fsanitize=address -I<src>/libarchive filter_matrix.c \
 *         <build>/libarchive/libarchive.a -lz -llzma -lxml2 -lzstd -llz4 -lbz2 \
 *         -o filter_matrix
 * Run: ./filter_matrix            (prints one line per filter)
 */
#include <archive.h>
#include <archive_entry.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static unsigned char payload[256 * 1024];
static char sink[1024];

static int child(int (*add_filter)(struct archive *)) {
    size_t used = 0;
    struct archive *a = archive_write_new();
    archive_write_set_format_ustar(a);
    if (add_filter(a) != ARCHIVE_OK) return 77;      /* filter not built in */
    if (archive_write_open_memory(a, sink, sizeof sink, &used) != ARCHIVE_OK) {
        archive_write_free(a);
        return 78;
    }
    struct archive_entry *e = archive_entry_new();
    archive_entry_set_pathname(e, "f");
    archive_entry_set_filetype(e, AE_IFREG);
    archive_entry_set_perm(e, 0644);
    archive_entry_set_size(e, (la_int64_t)sizeof payload);
    archive_write_header(a, e);
    archive_write_data(a, payload, sizeof payload);
    archive_entry_free(e);
    archive_write_close(a);
    archive_write_free(a);
    return 0;
}

int main(void) {
    unsigned s = 12345;
    for (size_t i = 0; i < sizeof payload; i++) {
        s = s * 1103515245u + 12345u;
        payload[i] = (unsigned char)(s >> 16);
    }
    struct { const char *name; int (*f)(struct archive *); } F[] = {
        {"none",      archive_write_add_filter_none},
        {"gzip",      archive_write_add_filter_gzip},
        {"bzip2",     archive_write_add_filter_bzip2},
        {"xz",        archive_write_add_filter_xz},
        {"lzma",      archive_write_add_filter_lzma},
        {"lzip",      archive_write_add_filter_lzip},
        {"zstd",      archive_write_add_filter_zstd},
        {"lz4",       archive_write_add_filter_lz4},
        {"compress",  archive_write_add_filter_compress},
        {"uuencode",  archive_write_add_filter_uuencode},
        {"b64encode", archive_write_add_filter_b64encode},
    };
    for (size_t i = 0; i < sizeof F / sizeof F[0]; i++) {
        int fds[2];
        if (pipe(fds)) return 1;
        fflush(stdout);                    /* so the child does not re-flush it */
        pid_t p = fork();
        if (p == 0) {
            close(fds[0]);
            dup2(fds[1], 2);               /* sanitizer report -> pipe */
            /* exit(), not _exit(): LeakSanitizer's check runs from an atexit
             * handler, and _exit() would skip it and report nothing. */
            exit(child(F[i].f));
        }
        close(fds[1]);
        char buf[65536]; size_t n = 0; ssize_t r;
        while (n < sizeof buf - 1 && (r = read(fds[0], buf + n, sizeof buf - 1 - n)) > 0) n += (size_t)r;
        buf[n] = 0;
        close(fds[0]);
        int st; waitpid(p, &st, 0);

        const char *tag = "no leak";
        char detail[160] = "";
        char *m = strstr(buf, "byte(s) leaked");
        if (m) {
            /* walk back to the byte count on the SUMMARY line */
            char *line = m;
            while (line > buf && *line != '\n') line--;
            long bytes = 0;
            sscanf(line, "%*[^0-9]%ld", &bytes);
            tag = "LEAK";
            char *site = strstr(buf, "archive_compressor_");
            if (!site) site = strstr(buf, "archive_write_");
            char fn[80] = "?";
            if (site) sscanf(site, "%79[a-zA-Z0-9_]", fn);
            snprintf(detail, sizeof detail, "  %8ld bytes  in %s", bytes, fn);
        }
        if (WEXITSTATUS(st) == 77) { tag = "n/a (not built in)"; detail[0] = 0; }
        printf("  %-10s %-20s%s\n", F[i].name, tag, detail);
    }
    return 0;
}
