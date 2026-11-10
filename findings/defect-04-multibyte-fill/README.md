# Defect #4 — the length guard counts characters against a limit in bytes

| | |
|---|---|
| Engine | Turso (`tursodb`), version string `Turso 0.9.0-pre.2` |
| Component | `core/functions/string.rs` — `pad()` |
| Present on | `b588fbdc6` and earlier, and on `main` before our first patch |
| Fixed on | `7f0a9e6f4` and later |
| Upstream issue | [#11260](https://github.com/tursodatabase/turso/issues/11260) |
| Our pull request | [#11259](https://github.com/tursodatabase/turso/pull/11259) — **merged** as `121cd0a8c80b` (+54 / −5) |

## Reproduction

[`reproduce.sql`](reproduce.sql):

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

## Measured, before and after

With memory capped at 6 GB (`ulimit -v 6000000`) so the pre-fix case fails
quickly instead of consuming ~11.7 GB:

```
before  b588fbdc6   exit 134  3.96s   memory allocation of 3999999996 bytes failed
after   7f0a9e6f4   exit 1    0.02s   Runtime error: string or blob too big (18)
```

`3999999996` is 10⁹ characters × 4 bytes, minus the one-character input: the
arithmetic of the root cause is visible in the failure message. Uncapped, the
same statement completes in about 34 s with an 11.7 GB peak and then returns the
error — a denial-of-service shape rather than an abort.

## Root cause

The requested length is validated in **characters** against
`Value::MAX_BLOB_LENGTH`, which is a limit in **bytes**. A fill whose characters
are multi-byte therefore passes a check that a one-byte fill would have failed,
and the entire string is built before the downstream byte check rejects it.

A one-byte fill at the same length is legal and does succeed, so the limit
itself is not the problem — only the unit it is checked in.

## Why this is a different defect from #1–#3

Different root cause, different fix, its own issue and its own patch. It is not
a regression from the first patch — the same statement behaves the same way on
`main` before it — which is why it was deliberately kept out of PR #11225 and
filed separately once that merged.

It became *reachable* only once #1–#3 were fixed. With the guard finally running
before the allocation, the guard itself turned out to be comparing the wrong
unit. Fixing one defect brought the next into view.

## The fix

Compute the exact byte length of the padded result before building it — whole
repetitions of the fill contribute `fill.len()` bytes each, the remainder
contributes the UTF-8 widths of the fill's leading characters — with checked
arithmetic, and compare *that* against the byte limit.

An exact computation is required rather than an estimate: a conservative
`target_len × max_char_width` bound would wrongly reject the legal one-byte
`lpad('a', 1000000000, '0')` case, which must keep working. Patch:
[`../patches/pr-11259.patch`](../patches/pr-11259.patch). The contemporaneous
analysis is in [`analysis.md`](analysis.md).

## Rediscovering it with the tool

Against a pre-fix build, from a recorded seed, in one case:

```bash
export GRAFT_TURSO_BIN=~/targets/turso-prefix/target/release/tursodb
python -m graft.campaign --target turso --mode both --seed-start 5429 --max-cases 1
```

Signature `541c97fa5dd9`. The generator arm reaches it at seed 6376, with
`lpad('1', 2147483647, '中文')` — 2.1 billion characters at three bytes each,
which aborts the pre-fix build with `memory allocation of 4294967292 bytes
failed`.

**The mined-template arm never reaches it, and cannot.** Templating replaces a
report's literals with typed holes, and the text fill pool is ASCII-only, so no
instantiation can produce a multi-byte fill at any seed. The generator gets
there through `EDGE_STRINGS`, which carries `é` and `中文`. That asymmetry is
the clearest result in [`../../experiments/README.md`](../../experiments/README.md).

## What we got wrong, and who caught it

We raised the unit mismatch ourselves, in the PR #11225 description submitted
at `2026-11-01 16:54:43Z`, under "one thing worth a reviewer's eye" — but
described the guard as "**conservative** by up to 4x", meaning harmless. That is
backwards: a guard measuring characters against a byte limit is *permissive*.

At `17:09:50Z` Turso's automated reviewer replied under *"Also wrong on main: 1
input this pull request doesn't fix"*, showing it was permissive and supplying a
demonstrating input. Our reply at `18:09Z`: *"you're right, and I've corrected
the description. I had the direction backwards."*

Ours: the defect, the root-cause analysis, the exact precheck, the verification,
the merged patch. Not ours: judging the direction correctly at first. It is
recorded here because it is one click away on PR #11225, and a reader who finds
it there should find it here first.
