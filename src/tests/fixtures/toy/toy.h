/* A tiny library used by the tests: covers every construct apimodel extracts. */
#ifndef TOY_H
#define TOY_H

#include <stddef.h>

#define TOY_OK 0
#define TOY_ERR (-30)
#define TOY_MODE_BASE 0x10
#define TOY_MODE_FAST (TOY_MODE_BASE | 1)
#define TOY_MODE_SLOW (TOY_MODE_BASE | 2U)
#define TOY_PERM 0644
#define TOY_MAX(a, b) ((a) > (b) ? (a) : (b))
#define TOY_NAME "toy"

struct toy;                                   /* opaque handle */
typedef struct toy_opts { int level; } toy_opts;
enum toy_kind { TOY_KIND_A = 1, TOY_KIND_B = 2 };

/** Create a new toy handle. Returns NULL on failure. */
struct toy *toy_new(void);
int toy_feed(struct toy *t, const void *buf, size_t len);
int toy_set_kind(struct toy *t, enum toy_kind k);
int toy_set_opts(struct toy *t, const toy_opts *o);
int toy_log(struct toy *t, const char *fmt, ...);
__attribute__((deprecated)) int toy_old(struct toy *t);
void toy_free(struct toy *t);

#endif
