/* gapharn_fuzz.h - tiny fuzz-input consumer shared by generated harnesses.
 *
 * A libFuzzer-style (const uint8_t*, size_t) input is wrapped in a GFuzz cursor.
 * Harnesses pull typed, bounded values out of it. All readers are safe when the
 * input runs out: they return 0 / empty, so a harness never reads out of bounds
 * because of a short input (that would be a harness bug, i.e. a false positive).
 *
 * This file is committed (not generated) and included by every generated harness.
 */
#ifndef GAPHARN_FUZZ_H
#define GAPHARN_FUZZ_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef struct { const uint8_t *p; size_t n; size_t off; } GFuzz;

static inline void gf_init(GFuzz *f, const uint8_t *data, size_t size) {
    f->p = data; f->n = size; f->off = 0;
}
static inline size_t gf_remaining(const GFuzz *f) { return f->n - f->off; }

/* One byte (0 when exhausted). */
static inline uint8_t gf_u8(GFuzz *f) {
    return f->off < f->n ? f->p[f->off++] : 0;
}

/* Little-endian u32 (missing bytes read as 0). */
static inline uint32_t gf_u32(GFuzz *f) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v |= (uint32_t)gf_u8(f) << (8 * i);
    return v;
}

/* Signed 64-bit, for size/offset/time arguments (covers negatives too). */
static inline int64_t gf_i64(GFuzz *f) {
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) v |= (uint64_t)gf_u8(f) << (8 * i);
    return (int64_t)v;
}

/* Index into [0,count) chosen by one byte. */
static inline unsigned gf_pick(GFuzz *f, unsigned count) {
    return count ? (unsigned)(gf_u8(f) % count) : 0;
}

/* NUL-terminated string of up to max_len bytes copied from the input into buf.
 * buf must hold max_len+1 bytes. Embedded NULs are replaced with 'x' so the
 * result is a normal C string. Returns buf. */
static inline char *gf_str(GFuzz *f, char *buf, size_t max_len) {
    size_t avail = gf_remaining(f);
    size_t len = gf_u8(f) % (max_len + 1);
    if (len > avail) len = avail;
    for (size_t i = 0; i < len; i++) {
        uint8_t c = gf_u8(f);
        buf[i] = c ? (char)c : 'x';
    }
    buf[len] = '\0';
    return buf;
}

/* Take up to `want` bytes as a blob, consuming them. *got is what was available
 * (<= want). Use this instead of gf_rest when several chunks are needed. */
static inline const uint8_t *gf_take(GFuzz *f, size_t want, size_t *got) {
    size_t avail = gf_remaining(f);
    if (want > avail) want = avail;
    const uint8_t *r = f->p + f->off;
    f->off += want;
    *got = want;
    return r;
}

/* Hand the rest of the input back as a (ptr,len) blob and consume it. */
static inline const uint8_t *gf_rest(GFuzz *f, size_t *len) {
    *len = gf_remaining(f);
    const uint8_t *r = f->p + f->off;
    f->off = f->n;
    return r;
}

#endif /* GAPHARN_FUZZ_H */
