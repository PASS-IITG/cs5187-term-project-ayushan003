# Finding 2 — `lpad`/`rpad` bound the length in characters against a limit in bytes

**Status: fixed upstream.** Reported as
[tursodatabase/turso#11260](https://github.com/tursodatabase/turso/issues/11260),
fixed by our pull request
[#11259](https://github.com/tursodatabase/turso/pull/11259), merged
2026-11-02 as `121cd0a8c80b` (+54 / −5) by maintainer `jussisaurio`.

## How this finding was discovered — read this first

**Not by GRAFT.** This defect came out of our own code review, while writing the
fix for [Finding 1](../bug-01/README.md) — not from the tool, and not from a
campaign.

The sequence is public and timestamped on PR #11225:

1. **2026-11-01 16:54:43Z — we raise it.** The pull-request description we
   submitted for Finding 1 says, in a section headed "one thing worth a
   reviewer's eye":

   > The `pad` guard compares a character count against a byte limit, so for
   > multi-byte fill characters it is **conservative by up to 4x**.

   So the mismatch between the two units was *ours*, found by reading the code
   we were patching. **But we had the direction backwards.** "Conservative"
   would mean the guard rejects too much — harmless. We therefore filed it as a
   limitation worth a reviewer's note, not as a defect.

2. **17:09:50Z — upstream's automated reviewer corrects the direction.**
   `turso-github-handyman[bot]` ("Autofixer") replied under *"Also wrong on
   main: 1 input this pull request doesn't fix"*, showing the guard is
   **permissive**, not conservative — a 4-byte fill passes a billion-character
   check and still builds ~4 GB — and supplying a demonstrating input:

   ```sql
   SELECT length(LPAD('a', 1000000000, '😀'));
   ```

3. **18:09Z — we accept the correction.** Our reply: *"you're right, and I've
   corrected the description. I had the direction backwards."*

4. We then analysed the root cause properly, wrote the exact byte-length
   precheck, verified it, and filed this issue and the pull request that fixed
   it.

**What is ours:** finding the code path and the unit mismatch; the root-cause
analysis; the exact precheck that fixes it without rejecting legal wide fills;
the verification; the merged patch. **What is not ours:** correctly judging that
it was a defect rather than harmless conservatism, and the demonstrating input.
A reviewer caught our misdiagnosis of our own observation.

The reason this is set out at length rather than summarised: GRAFT discovered
**one** of this project's two defects. The second is real work of ours, but it
is work of code review, not of the tool, and the repository should not let those
blur. [`docs/provenance.md`](../../docs/provenance.md) carries the same account.

## Affected engine and version

| | |
|---|---|
| Engine | Turso (`tursodb`), version string `Turso 0.9.0-pre.2` |
| Present on | `b588fbdc6` (2026-11-01) and on `main` before it |
| Fixed on | `7f0a9e6f4` (2026-11-02) and later |
| Component | `core/functions/string.rs` — `pad()` |
| Platform | Linux x86_64, release build from source |

## Background

After [Finding 1](../bug-01/README.md) was fixed, `pad()` compares the requested
length against `Value::MAX_BLOB_LENGTH` *before* allocating. The requested
length is a count of **characters**. `Value::MAX_BLOB_LENGTH` is a limit on
**bytes**.

## Expected behaviour

`string or blob too big (18)` — promptly, and without allocating — which is
exactly what the same statement already produces for a single-byte fill past the
limit. A one-byte fill at the same length is legal and does succeed, so the
limit itself is not the problem; only the unit it is checked in.

## Observed behaviour

A fill whose characters are multi-byte passes a check expressed in characters
and the entire string is built before the downstream byte check rejects it. One
billion characters is inside the character limit, but a 4-byte fill makes that
about 4 GB of string.

## Minimal reproduction

[`reproduce.sql`](reproduce.sql):

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

## Actual execution output

Captured on this machine, 2026-11-03, with memory capped at 6 GB
(`ulimit -v 6000000`) so the pre-fix case fails quickly instead of consuming
~11.7 GB.

**Before the fix** (`b588fbdc6`):

```
$ echo "SELECT length(LPAD('a', 1000000000, '😀'));" | tursodb :memory:
memory allocation of 3999999996 bytes failed
Aborted
```

Exit status **134** (128 + SIGABRT), after 3.92 s. Note `3999999996` bytes:
that is 10⁹ characters × 4 bytes, minus the one-character input — the arithmetic
of the root cause, visible in the failure message.

Without the memory cap the same statement completes in about 34 s with an
11.7 GB peak and then returns the error, which is a denial-of-service shape
rather than an abort.

**After the fix** (`7f0a9e6f4`, containing `121cd0a8c80b`):

```
$ echo "SELECT length(LPAD('a', 1000000000, '😀'));" | tursodb :memory:
Runtime error: string or blob too big (18)
```

Exit status **1**, after 0.02 s.

## Technical explanation

The padding is a cycle of the fill's characters, so the exact byte length of the
result can be computed without building it: whole repetitions of the fill
contribute `fill.len()` bytes each, and the remainder contributes the UTF-8
widths of the leading characters of the fill. The fix computes that total with
checked arithmetic and compares it against `Value::MAX_BLOB_LENGTH` before
allocating.

An exact computation matters rather than an estimate. A conservative bound such
as `target_len × max_char_width` would wrongly reject the legal one-byte
`lpad('a', 1000000000, '0')` case, which must keep working.

## Why the behaviour is incorrect

The engine enforces a byte limit, and the guard that is supposed to enforce it
measures characters. For any fill wider than one byte the guard is therefore
permissive by a factor of up to four, and the limit it exists to enforce is
reached only after the memory has already been committed.

## Relationship to Finding 1

It is not a regression from our first patch: the same statement behaves the same
way on `main` before that patch, which is why it was deliberately kept out of
PR #11225 and filed separately once that one merged. The two defects are
siblings — the same function, the same class of unchecked size, in two different
units — and the second became visible only because the first was fixed.
`docs/evaluation.md` treats that as a result in its own right.

## Validation methodology

1. **Reproduced on a fresh instance**, from the input as the bot reported it.
2. **Confirmed present on `main`**, not only on our PR branch, which is what
   established it as a separate defect rather than a regression.
3. **Cost measured both ways**: unbounded (~34 s, ~11.7 GB peak) and under
   `ulimit -v 6000000` (SIGABRT in 3.92 s).
4. **Fix verified to preserve the legal case**: the one-byte fill at the same
   length still succeeds, and the engine's own test suite passes.
5. **Before/after captured on one machine on the same day**, from two builds of
   the same source tree differing only by the merge commit.

## Files here

| File | What it is |
|---|---|
| [`reproduce.sql`](reproduce.sql) | the minimal reproduction |
| [`analysis.md`](analysis.md) | the contemporaneous root-cause note, written before the fix was filed |
| [`pull-request.md`](pull-request.md) | the pull request description |
| [`upstream-issue.md`](upstream-issue.md) | the issue report as filed |
