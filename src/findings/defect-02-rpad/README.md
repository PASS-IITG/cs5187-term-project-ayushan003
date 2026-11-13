# Defect #2 — `rpad` allocates its result before validating the length

| | |
|---|---|
| Engine | Turso (`tursodb`), version string `Turso 0.9.0-pre.2` |
| Component | `core/functions/string.rs` — `pad()`, reached via `exec_rpad` |
| Present on | `b588fbdc6` and earlier |
| Fixed on | `7f0a9e6f4` and later |
| Upstream issue | [#11219](https://github.com/tursodatabase/turso/issues/11219) |
| Our pull request | [#11225](https://github.com/tursodatabase/turso/pull/11225) — **merged** as `ec86571642a6` (+138 / −41) |

## Reproduction

[`reproduce.sql`](reproduce.sql):

```sql
select RPAD('a', 9223372036854775807, 'b');
```

## Measured, before and after

```
before  b588fbdc6   exit 134  2.24s   memory allocation of 9223372036854775806 bytes failed
after   7f0a9e6f4   exit 1    0.02s   Runtime error: string or blob too big (18)
```

The measurement above involved no `lpad` call: this entry point aborts the
engine on its own.

## Why this is listed separately from defect #1

Because a user who never calls `lpad` is still exposed, and the statement above
kills the process by itself.

It is **not** a separate root cause. `exec_lpad` and `exec_rpad` both delegate
to the shared `pad()` body, so one fix closes both — which is why upstream
carries one issue and one patch for defects #1–#3, and why the oracle groups
them under a single signature. [`../README.md`](../README.md) sets that
relationship out. Nothing here claims two independent bugs in two independent
functions.

## Expected behaviour, root cause and fix

Identical to [defect #1](../defect-01-lpad/README.md): the result is sized from
the caller's argument and allocated before `Value::MAX_BLOB_LENGTH` is
consulted, so a length near `i64::MAX` reaches the allocator, which cannot fail
gracefully at that size. The fix moves the check in front of the allocation.

Patch: [`../patches/pr-11225.patch`](../patches/pr-11225.patch).

## Rediscovering it with the tool

The campaign reaches this entry point under signature `64513113c675`, shared
with defect #1 because the oracle groups by the abort. The mined-template arm's
hit at seed 914 is an `rpad` call specifically:

```bash
export GRAFT_TURSO_BIN=~/targets/turso-prefix/target/release/tursodb
python -m graft.campaign --target turso --mode variants --seed-start 914 --max-cases 1
# select RPAD(' ', 9223372036854775807, 'a''b')
```
