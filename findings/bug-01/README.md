# Defects #1–#3 — `lpad`, `rpad` and `repeat` allocate before checking the length limit

**Status: fixed upstream.** Reported as
[tursodatabase/turso#11219](https://github.com/tursodatabase/turso/issues/11219),
fixed by our pull request
[#11225](https://github.com/tursodatabase/turso/pull/11225), merged
2026-11-02 as `ec86571642a6` (+138 / −41) by maintainer `jussisaurio`.

## Affected engine and version

| | |
|---|---|
| Engine | Turso (`tursodb`), version string `Turso 0.9.0-pre.2` |
| Present on | `730017f96` (2026-10-28), `b588fbdc6` (2026-11-01) |
| Fixed on | `7f0a9e6f4` (2026-11-02) and later |
| Component | `core/functions/string.rs` — `pad()` and `repeat()` |
| Platform | Linux x86_64, release build from source |

## Background

`lpad(s, n, fill)` returns `s` padded to `n` characters. Turso bounds the result
with `Value::MAX_BLOB_LENGTH` and returns `LimboError::TooBig` —
`string or blob too big`, SQLite error code 18 — when a result would exceed it.
That limit was already enforced before this finding, but only *after* the result
had been built.

## Expected behaviour

A single SQL statement should either return a result or be rejected with a
normal user-facing error. It should not terminate the process. The condition is
reachable from plain SQL with no special configuration, so a server embedding
the engine would lose the whole process.

The expectation does not rest on any other engine's behaviour, and no claim
about the SQL standard is made: the engine's *own* limit is the specification
being violated.

## Observed behaviour

The size of the result was computed from the caller's argument and allocated
directly, so a length near `i64::MAX` reached the allocator before the limit was
checked. Rust's allocation-failure handler aborts the process.

## Minimal reproduction

[`reproduce.sql`](reproduce.sql) — one statement, no table, no data:

```sql
select LPAD('a', 9223372036854775807, 'b');
```

`rpad` is identical, and `repeat('a', 9223372036854775807)` fails the same way
through the same path.

## Actual execution output

Captured on this machine, 2026-11-03, with both binaries built from source.

**Before the fix** (`b588fbdc6`):

```
$ echo "select LPAD('a', 9223372036854775807, 'b');" | tursodb :memory:
memory allocation of 9223372036854775806 bytes failed
Aborted
```

Exit status **134** (128 + SIGABRT), after 2.27 s. No SQL error is returned and
the session is gone.

**After the fix** (`7f0a9e6f4`, containing `ec86571642a6`):

```
$ echo "select LPAD('a', 9223372036854775807, 'b');" | tursodb :memory:
Runtime error: string or blob too big (18)
```

Exit status **1**, after 0.02 s.

## Technical explanation

`pad()` sized its output buffer from the `n` argument and allocated it before
comparing anything against `Value::MAX_BLOB_LENGTH`. The existing limit check
therefore ran only on a result that already existed, which is why the defect has
a window rather than applying to every oversized call:

- `lpad('a', 4294967296, 'b')` — the allocation of ~4 GB fails *gracefully*, the
  check is reached, and `string or blob too big (18)` is returned correctly.
- `lpad('a', 9223372036854775807, 'b')` — the allocator is asked for
  ~9.2 × 10¹⁸ bytes, cannot fail gracefully, and aborts.

`repeat()` has the same shape and additionally multiplies, so it can overflow
the size computation itself.

## Why the behaviour is incorrect

The engine defines a limit for exactly this condition and reports it as a
recoverable SQL error. Reaching the allocator first makes that limit
unenforceable for the largest inputs, turning a defined error into process
termination. The fix moves the comparison in front of the allocation, so the
same error is produced by the same code path for every oversized request;
`repeat` uses `checked_mul` and refuses on overflow.

## Validation methodology

1. **Reproduced twice on a fresh instance.** Each run starts a new `tursodb`
   process against `:memory:`, so no state carries over.
2. **Reduced to one statement** with no schema and no data, from a generated
   four-call query (reduction ratio 0.939, five executions).
3. **Re-verified on current upstream `main`** before filing: rebuilt at
   `b588fbdc6` on 2026-11-01 and confirmed still present.
4. **Duplicate search run and recorded.** Queries used:
   `repo:tursodatabase/turso "lpad" crash`, `"rpad" crash`, `"abc" crash`,
   `"crash" in:title`. The nearest existing issue, #11175, is about wrong
   argument counts panicking — a different defect.
5. **Fix verified against the engine's own test suite** before the pull request
   was opened, and the patch carries a regression test.
6. **Before/after captured on one machine on the same day**, from two builds of
   the same source tree differing only by the merge commit.

## How it was found

By this project's transfer mechanism, not by hand. A closed, reproduced DuckDB
bug report using `LPAD`/`RPAD` was reduced to a template, its literals were
replaced with typed holes, and the integer holes were filled from a
boundary-value pool that includes `i64::MAX`. The resulting statement was
executed against Turso and the crash oracle caught the abort.

- Seed `6948`; every random choice comes from a seeded generator, so the case is
  reproducible.
- First observed `2026-10-28T19:58:43Z`, eight seconds into the first campaign.
- Coarse signature `64513113c675c2daccf9ef2e57c9b333dd646af5`.

`docs/methodology.md` describes the mechanism and `make reproduce` replays this
exact chain. Turso's own differential fuzzer cannot produce this statement:
`docs/evaluation.md` gives the four reasons, each verifiable in its source.

## Files here

| File | What it is |
|---|---|
| [`reproduce.sql`](reproduce.sql) | the minimal reproduction |
| [`fix.patch`](fix.patch) | the patch as submitted, with its regression test |
| [`pull-request.md`](pull-request.md) | the pull request description |
| [`upstream-issue.md`](upstream-issue.md) | the issue report as filed |
