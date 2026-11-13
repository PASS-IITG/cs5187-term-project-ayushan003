**GRAFT — Cross-Engine Bug-Report-Guided Fuzzing of a SQLite-Compatible Database**

GRAFT takes its test shapes from somewhere unusual: closed, reproduced bug
reports filed against a *different* engine. It mines DuckDB's tracker, strips
each report to its structure, refills it with boundary values, and replays it
against Turso — alongside a typed generator that supplies schemas, data and
queries of its own.

Both halves aim at the same place: inputs the target's own fuzzer cannot
express. Turso's differential fuzzer draws integer literals from
`[-1_000_000, 1_000_000]`, restricts strings to alphanumerics, and never calls
`lpad`/`rpad` at all — and SQLite, its reference oracle, has no `LPAD` to
compare against.

GRAFT found three defects; our patch for them exposed a fourth, which GRAFT did
not find. All four were reported and fixed upstream across two merged pull
requests.

Summary->

**1. Turso – PR #11225: LPAD Length Allocation Before Validation (Unchecked Allocation / Process Abort)**

A bug in `lpad()` caused the result buffer to be sized from the caller's length
argument and allocated before that length was checked against the engine's
`MAX_BLOB_LENGTH` limit. A length near `i64::MAX` therefore reached the
allocator, which cannot fail gracefully at that size, and the process terminated
with SIGABRT instead of returning a SQL error. The existing limit check was
never reached, so `SELECT LPAD('a', 9223372036854775807, 'b')` killed the
session outright.

- **Fix:** Moved the length comparison against `Value::MAX_BLOB_LENGTH` in front
  of the allocation, so an oversized request returns `LimboError::TooBig`
  ("string or blob too big", code 18) without allocating.
- **Status:** Our proposed fix has been merged into the upstream Turso repository.

[core/functions: check the requested length before allocating in lpad/rpad/repeat · Pull Request #11225 · tursodatabase/turso](https://github.com/tursodatabase/turso/pull/11225)

---

**2. Turso – PR #11225: RPAD Length Allocation Before Validation (Unchecked Allocation / Process Abort)**

The same defect was independently reachable through `rpad()`, which performs its
own oversized allocation before any limit check and aborts the process in the
same way. `SELECT RPAD('a', 9223372036854775807, 'b')` terminated with SIGABRT
on a fresh in-memory database, with no SQL error returned and the session lost.

- **Fix:** Applied the same pre-allocation length check on the right-padding
  path, so oversized requests are rejected with "string or blob too big" rather
  than reaching the allocator.
- **Status:** Our proposed fix has been merged into the upstream Turso repository.

[core/functions: check the requested length before allocating in lpad/rpad/repeat · Pull Request #11225 · tursodatabase/turso](https://github.com/tursodatabase/turso/pull/11225)

---

**3. Turso – PR #11225: REPEAT Size Computation Overflow (Integer Overflow / Process Abort)**

A bug in `repeat()` caused the output size to be computed by multiplying the
input length by the repeat count with unchecked arithmetic, and the result was
allocated before validation. The multiplication could overflow, and an
unvalidated large count reached the allocator directly, so
`SELECT repeat('a', 9223372036854775807)` aborted the process with
`memory allocation of 9223372036854775807 bytes failed`.

- **Fix:** Used `checked_mul` for the size computation and refused the statement
  on overflow, and moved the limit check in front of the allocation so an
  oversized count returns "string or blob too big" instead of aborting.
- **Status:** Our proposed fix has been merged into the upstream Turso repository.

[core/functions: check the requested length before allocating in lpad/rpad/repeat · Pull Request #11225 · tursodatabase/turso](https://github.com/tursodatabase/turso/pull/11225)

---

**4. Turso – PR #11259: Character-Count Length Guard Against a Byte Limit (Unit Confusion / Memory Exhaustion)**

A bug in the padding length guard caused the requested length to be validated in
**characters** against `Value::MAX_BLOB_LENGTH`, which is a limit in **bytes**.
A multi-byte fill character therefore passed a check that a one-byte fill would
have failed, and the entire string was built before the downstream byte check
rejected it. `SELECT length(LPAD('a', 1000000000, '😀'))` consumed roughly
11.7 GB over about 34 seconds before failing, and aborted outright under a
memory limit — `memory allocation of 3999999996 bytes failed`, which is
10⁹ characters × 4 bytes.

- **Fix:** Computed the exact byte length of the padded result before building
  it — whole repetitions of the fill plus the UTF-8 widths of the remainder,
  with checked arithmetic — and compared that against the byte limit. An exact
  computation rather than a `length × max_char_width` estimate was required so
  that the legal one-byte `LPAD('a', 1000000000, '0')` case keeps working.
- **Status:** Our proposed fix has been merged into the upstream Turso repository.

[core/functions: check the byte length of a multi-byte pad before building it · Pull Request #11259 · tursodatabase/turso](https://github.com/tursodatabase/turso/pull/11259)
