#include <stdarg.h>
#include <stdlib.h>
#include "toy.h"

struct toy { int kind; int level; size_t fed; };

/* Internal helper: global but not in the public header, so not part of the API. */
int toy_internal_helper(struct toy *t) { return t->kind; }

struct toy *toy_new(void) { return calloc(1, sizeof(struct toy)); }

int toy_feed(struct toy *t, const void *buf, size_t len) {
  if (!t || !buf) return TOY_ERR;
  t->fed += len;
  return TOY_OK;
}

int toy_set_kind(struct toy *t, enum toy_kind k) { t->kind = k; return TOY_OK; }

int toy_set_opts(struct toy *t, const toy_opts *o) { t->level = o->level; return TOY_OK; }

int toy_log(struct toy *t, const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  va_end(ap);
  return t && fmt ? TOY_OK : TOY_ERR;
}

int toy_old(struct toy *t) { return toy_internal_helper(t); }

void toy_free(struct toy *t) { free(t); }
