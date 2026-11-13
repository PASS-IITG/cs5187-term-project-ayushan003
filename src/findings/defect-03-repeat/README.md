# Defect #3 — `repeat` overflows its size computation and allocates before validating

| | |
|---|---|
| Engine | Turso (`tursodb`), version string `Turso 0.9.0-pre.2` |
| Component | `core/functions/string.rs` — `exec_repeat()` |
| Present on | `b588fbdc6` and earlier |
| Fixed on | `7f0a9e6f4` and later |
| Upstream issue | [#11219](https://github.com/tursodatabase/turso/issues/11219) |
| Our pull request | [#11225](https://github.com/tursodatabase/turso/pull/11225) — **merged** as `ec86571642a6` (+138 / −41) |

## Reproduction

[`reproduce.sql`](reproduce.sql):

```sql
select repeat('a', 9223372036854775807);
```

## Measured, before and after

```
before  b588fbdc6   exit 134  2.27s   memory allocation of 9223372036854775807 bytes failed
after   7f0a9e6f4   exit 1    0.02s   Runtime error: string or blob too big (18)
```

## Why this is a separate defect from #1 and #2

`exec_repeat` is a **different function** from `pad()`, with its own code path,
its own oracle signature and its own regression test in the patch. It shares the
class — size computed from the caller's argument, allocated before the limit is
consulted — but adds a failure mode the pad path does not have: it
**multiplies** the input length by the repeat count, so the size computation can
overflow before any allocation is attempted.

That is why the fix needs two parts rather than one, and why the patch carries
`repeat_rejects_a_count_that_overflows_the_limit` alongside
`pad_rejects_a_length_over_the_limit`.

## The fix

`checked_mul` for the size computation, refusing the statement on overflow, and
the limit check moved in front of the allocation as for the pad path. Patch:
[`../patches/pr-11225.patch`](../patches/pr-11225.patch).

## Rediscovering it with the tool

Against a pre-fix build, from a recorded seed, in one case:

```bash
export GRAFT_TURSO_BIN=~/targets/turso-prefix/target/release/tursodb
python -m graft.campaign --target turso --mode both --seed-start 5080 --max-cases 1
```

Signature `40d2c580f251` — distinct from the pad signature, because the abort
arrives from a different call site. The mined-template arm reaches it at seed
10180 and the generator arm at 5367.

A note on ordering, for accuracy: `repeat` was already included in the patch by
the time the campaign produced a case for it, because the shared class had been
recognised by reading the code. The seeded case is independent confirmation, not
the trigger for the fix.
