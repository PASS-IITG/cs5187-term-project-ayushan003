# Defect #1 — `lpad` allocates its result before validating the length

| | |
|---|---|
| Engine | Turso (`tursodb`), version string `Turso 0.9.0-pre.2` |
| Component | `core/functions/string.rs` — `pad()`, reached via `exec_lpad` |
| Present on | `b588fbdc6` and earlier |
| Fixed on | `7f0a9e6f4` and later |
| Upstream issue | [#11219](https://github.com/tursodatabase/turso/issues/11219) |
| Our pull request | [#11225](https://github.com/tursodatabase/turso/pull/11225) — **merged** as `ec86571642a6` (+138 / −41) |

## Reproduction

[`reproduce.sql`](reproduce.sql) — one statement, no table, no data:

```sql
select LPAD('a', 9223372036854775807, 'b');
```

## Measured, before and after

```
before  b588fbdc6   exit 134  2.28s   memory allocation of 9223372036854775806 bytes failed
after   7f0a9e6f4   exit 1    0.02s   Runtime error: string or blob too big (18)
```

Exit 134 is 128 + SIGABRT. No SQL error is returned and the session is gone.

## Expected behaviour

A single SQL statement should return a result or be rejected with a normal
user-facing error. It must not terminate the process: the condition is reachable
from plain SQL with no special configuration, so a server embedding the engine
would lose the whole process.

This expectation rests on the engine's *own* limit, not on any other engine's
behaviour. `Value::MAX_BLOB_LENGTH` exists precisely for this case and is
reported as `LimboError::TooBig` — "string or blob too big", SQLite code 18.

## Root cause

`pad()` sized its output buffer from the `n` argument and allocated it before
comparing anything against `Value::MAX_BLOB_LENGTH`. The limit check therefore
ran only on a result that already existed, which is why the defect has a window
rather than applying to every oversized call:

- `lpad('a', 4294967296, 'b')` — the ~4 GB allocation fails *gracefully*, the
  check is reached, and the correct error is returned.
- `lpad('a', 9223372036854775807, 'b')` — the allocator is asked for ~9.2 × 10¹⁸
  bytes, cannot fail gracefully, and aborts.

## The fix

Move the comparison in front of the allocation, so an oversized request returns
the same error by the same path for every size. Patch:
[`../patches/pr-11225.patch`](../patches/pr-11225.patch), with the regression
test `pad_rejects_a_length_over_the_limit`.

## Rediscovering it with the tool

Against a pre-fix build, from a recorded seed, in one case:

```bash
export GRAFT_TURSO_BIN=~/targets/turso-prefix/target/release/tursodb
python -m graft.campaign --target turso --mode both --seed-start 4553 --max-cases 1
# select LPAD('a''b', 9223372036854775807, 'ABC'), LPAD('0', 1, 'A'),
#        LPAD('a''b', 127, 'ABC'), RPAD('%', 127, 'A')
```

That is the four-call template from the mined DuckDB test, with the huge length
in its first `lpad` and only a small length in its `rpad`. The mined-template
arm alone regenerates it at seed 7039. Signature `64513113c675`, which defect #2
shares; in every run of [`../../experiments/README.md`](../../experiments/README.md)
the *first* hit on that signature happened to be an `rpad` call.
