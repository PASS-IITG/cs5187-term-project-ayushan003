/* Stands in for an "existing harness": it only reaches toy_new, toy_feed and toy_free. */
#include "toy.h"

int main(void) {
  struct toy *t = toy_new();
  if (!t) return 1;
  toy_feed(t, "abc", 3);
  toy_free(t);
  return 0;
}
