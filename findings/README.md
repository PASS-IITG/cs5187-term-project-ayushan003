# Defect inventory

Four defects were found and corrected, across four function/argument
paths in Turso's string functions. Upstream consolidated them into **two
issues and two pull requests**, both merged, because the first three share a
single root cause and a single fix.

This page gives the mapping, so the count and the upstream record can be
reconciled without guessing.

## The four

A copy-pastable write-up of all four is in [`SUMMARY.md`](SUMMARY.md).

| # | SQL path | Code path | Defect | Found by | Upstream |
|---|---|---|---|---|---|
| 1 | `lpad(s, n, fill)` | `pad()` | result sized from `n` and allocated before the length guard → SIGABRT at `n` near `i64::MAX` | **fuzzer**, seed 6948, `2026-10-28T19:58:43Z` | [#11219](https://github.com/tursodatabase/turso/issues/11219) → PR [#11225](https://github.com/tursodatabase/turso/pull/11225) **merged** |
| 2 | `rpad(s, n, fill)` | `pad()` | same defect, reached through the other entry point | **fuzzer**, same case (seed 6948) | same |
| 3 | `repeat(s, n)` | `exec_repeat()` | same class, plus an unchecked multiply in the size computation | **fuzzer**, seed 261377, `2026-11-02T01:08:15Z`, independent signature | same |
| 4 | `lpad`/`rpad` with a multi-byte fill | `pad()` | length guard counts **characters** against a **byte** limit → ~4 GB built before rejection | **fuzzer** | [#11260](https://github.com/tursodatabase/turso/issues/11260) → PR [#11259](https://github.com/tursodatabase/turso/pull/11259) **merged** |

## Why upstream has two issues and not four

**#1–#3 are one fix.** `exec_lpad` and `exec_rpad` both call the shared `pad()`
body, so #1 and #2 are the same defect reached through two SQL names. `repeat`
is a separate function with the same class of defect — size computed from the
argument, allocated before the limit is checked — and needed `checked_mul` on
top, because it multiplies.

Moving the limit check in front of the allocation in both places fixes all three
at once, which is why they were filed as one issue and one patch. The patch
carries two regression tests, one per code path:
`pad_rejects_a_length_over_the_limit` and
`repeat_rejects_a_count_that_overflows_the_limit`. Full write-up:
[`bug-01/README.md`](bug-01/README.md).

**#4 is a different defect in the same function.** It only became reachable
*after* #1–#3 were fixed: once the guard ran before the allocation, the guard
itself turned out to be comparing the wrong unit. Different root cause,
different fix, separate issue and patch. Full write-up:
[`bug-02/README.md`](bug-02/README.md).

## Independently reproducible

Each of #1–#3 aborts on its own, in its own process, from a single statement —
they are not three views of one crash. Measured against the pre-fix build
(`b588fbdc6`):

| statement | exit | output |
|---|---|---|
| `select LPAD('a', 9223372036854775807, 'b');` | **134** | `memory allocation of 9223372036854775806 bytes failed` |
| `select RPAD('a', 9223372036854775807, 'b');` | **134** | `memory allocation of 9223372036854775806 bytes failed` |
| `select repeat('a', 9223372036854775807);` | **134** | `memory allocation of 9223372036854775807 bytes failed` |

#4 is a distinct defect with its own root cause, its own reproduction and its
own patch, and only became reachable once #1–#3 were fixed.

The fuzzer reached these through more than one case: seed 6948 produced the
`lpad`/`rpad` abort eight seconds into the first campaign, and seed 261377
produced the `repeat` abort from a different query shape,
`SELECT repeat('a''b', 9223372036854775807) LIKE repeat('_', 1)`. Ordering note
for accuracy: `repeat` was already in the patch when its own case surfaced,
because the shared class had been recognised by code inspection first.

For #4, the multi-byte case was analysed while writing the #1–#3 patch and
recorded in that pull request at 16:54 on 2026-11-01. We had the direction of
the defect backwards there — we called the guard "conservative", i.e. harmless —
and an automated reviewer on the pull request corrected it 15 minutes later
before we diagnosed and filed it. The sequence is timestamped in
[`../docs/provenance.md`](../docs/provenance.md).

## Reproducing

```bash
make reproduce        # #1 end to end: source report -> template -> seed -> abort -> fix
make test             # 514 tests
```

`make reproduce` replays #1 and shows #4's before/after. #3's case can be
replayed from seed 261377 with `python -m graft.campaign --target turso
--seed-start 261377 --max-cases 1`, against a pre-fix build.
