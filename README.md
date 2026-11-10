# GRAFT

**Grafting known defects across SQL engine implementations.**

CS 5187 — Software Analysis, Monsoon 2026, Dept. of CSE, IIT Guwahati.
Term project. Authors: see [`CONTRIBUTIONS.md`](CONTRIBUTIONS.md).

GRAFT takes a bug report that another SQL engine's maintainers already wrote,
strips it to its structure, refills it with boundary values, and runs it against
a different engine. One previously-unknown defect found this way in
[Turso](https://github.com/tursodatabase/turso) was fixed by our patch upstream.

> **Status, stated precisely.** **Four defect instances** found and corrected
> across four function/argument paths in Turso's string functions — `lpad`,
> `rpad`, `repeat` and the multi-byte fill case. Upstream consolidated them into
> **two issues and two pull requests, both merged**, because the first three
> share one root cause and one fix.
>
> Each of the first three aborts the process independently, from a single
> statement. [`findings/README.md`](findings/README.md) maps all four to their
> code paths, seeds and upstream links.

---

## The problem

A fuzzer finds only what its generator can express and its oracle can judge.
Turso already fuzzes itself hard — `testing/differential-oracle/` generates SQL
and compares it against SQLite, in CI — so the easy defects are gone. Where are
the rest?

Reading that fuzzer's own source answers it. Its literal policy
(`sql_gen/src/policy.rs`) sets `int_max: 1_000_000` and
`string_charset: StringCharset::Alphanumeric`, and `lpad`/`rpad` appear nowhere
in its vocabulary. SQLite has no `LPAD` at all, so a SQLite-differential oracle
could not judge the answer even if the statement were generated.

**Four limits, each checkable in one command, and every one of them blocks the
defects reported here.**

## The research question

> Can a defect that is already known in one SQL engine be used as a probe for a
> second, independently implemented engine — and does that reach defects the
> second engine's own fuzzer structurally cannot express?

Four sub-questions, answered in [`docs/evaluation.md`](docs/evaluation.md):

| | Question | Answer |
|---|---|---|
| RQ1 | Can a fuzzer's blind spot be measured in advance, from its source? | Yes — four verifiable limits |
| RQ2 | Does cross-implementation defect transfer expose new defects? | Yes — one, merged upstream |
| RQ3 | Does transfer beat generation per unit of effort? | **Not measured. No claim.** |
| RQ4 | What does the oracle cost in false positives? | Zero, by construction |

## Objectives

1. Measure what the target engine's own fuzzer cannot express, before testing.
2. Build a corpus of reproductions from a *different* engine's closed defects.
3. Transfer their structure, not their values, to the engine under test.
4. Detect with an oracle that needs no reference implementation.
5. Reduce, re-verify and report anything found, and get the fix accepted.

## How it works

```
duckdb/duckdb closed issues  ──mine──▶  659 reproductions
                                            │
                        literals ──▶ typed holes   (keep the maintainer's shape)
                                            │
                        boundary values ──▶ filled statements
                                            │
                                      run on tursodb
                                            │
                        crash oracle: did one statement kill the process?
                                            │
                        reduce ─▶ re-verify on current main ─▶ duplicate search ─▶ report
```

The source report, `duckdb/duckdb#27846`, is about pad functions mishandling
multi-byte fills. Templated, it becomes:

```sql
select LPAD({s4}, {n0}, {s5}), LPAD({s6}, {n1}, {s7}),
       LPAD({s8}, {n2}, {s9}), RPAD({s10}, {n3}, {s11})
```

Over seeds 0–3999, that one template places `9223372036854775807` into a pad
call in **1,054 of them (26.4%)**. Turso's own fuzzer cannot place it even once.

Full design in [`docs/design.md`](docs/design.md); the reasoning in order in
[`docs/methodology.md`](docs/methodology.md).

## The findings

Four defect instances, two upstream patches. The mapping — which SQL path,
which code path, which seed, which issue — is in
[`findings/README.md`](findings/README.md). The two patches:

### [Finding 1](findings/bug-01/README.md) — allocation before the length check

```sql
select LPAD('a', 9223372036854775807, 'b');
```

Three functions, two code paths, one fix. `pad()` — shared by `lpad` and
`rpad` — and `exec_repeat()` both sized and allocated their result from the
caller's argument before comparing anything against `Value::MAX_BLOB_LENGTH`,
so a length near `i64::MAX` reached the allocator and aborted the process.
`repeat` additionally multiplies, so it needed `checked_mul`.

The fuzzer flagged both code paths from independent cases: seed 6948 for
`lpad`/`rpad`, seed 261377 for `repeat`
(`SELECT repeat('a''b', 9223372036854775807) LIKE repeat('_', 1)`).

| | exit | time | output |
|---|---|---|---|
| before `b588fbdc6` | **134** (SIGABRT) | 1.11 s | `memory allocation of 9223372036854775806 bytes failed` |
| after `7f0a9e6f4` | 1 | 0.01 s | `Runtime error: string or blob too big (18)` |

**Found by GRAFT**, 8 s into the first campaign; `repeat` independently
confirmed at seed 261377. Covers defects #1–#3 of four. Issue
[#11219](https://github.com/tursodatabase/turso/issues/11219) · our PR
[#11225](https://github.com/tursodatabase/turso/pull/11225) **merged**
`ec86571642a6` (+138 / −41).

### [Finding 2](findings/bug-02/README.md) — a character count against a byte limit

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

After Finding 1 was fixed, the check ran first — but it compared a count of
**characters** against `Value::MAX_BLOB_LENGTH`, a limit in **bytes**. A 4-byte
fill passes a billion-character check and builds about 4 GB.

| | exit | time | output |
|---|---|---|---|
| before, `ulimit -v 6000000` | **134** (SIGABRT) | 2.86 s | `memory allocation of 3999999996 bytes failed` |
| after | 1 | 0.01 s | `Runtime error: string or blob too big (18)` |

`3999999996` is 10⁹ characters × 4 bytes: the root cause is visible in the
failure message. **Found by our own review of the Finding 1 patch**, where we
first recorded the unit mismatch as "conservative" — an automated reviewer on
PR #11225 showed it was permissive, and we then diagnosed and fixed it. Issue
[#11260](https://github.com/tursodatabase/turso/issues/11260) · our PR
[#11259](https://github.com/tursodatabase/turso/pull/11259) **merged**
`121cd0a8c80b` (+54 / −5).

The two are siblings: the same function, the same class of unchecked size, in
two different units — the second visible only once the first was closed.

## Reproducing

```bash
git clone <this repository> && cd graft
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

make reproduce      # the headline result, end to end
make test           # 514 tests
```

`make reproduce` prints the source report, the template, the seeded fill, the
26.4% rate, the four blind-spot limits, and both findings before and after our
patches. Steps 1–4 need nothing but this repository. Step 5 needs two `tursodb`
builds and is **skipped with instructions** if either is missing — it never
reports an engine result it did not obtain.

<details>
<summary>Building the two <code>tursodb</code> binaries</summary>

```bash
# "after" — current main, contains both merged fixes
git clone https://github.com/tursodatabase/turso ~/targets/turso
cd ~/targets/turso && cargo build --release --bin tursodb

# "before" — a separate worktree, so the above is not overwritten
git -C ~/targets/turso worktree add ~/targets/turso-prefix b588fbdc6
cd ~/targets/turso-prefix && cargo build --release --bin tursodb

export GRAFT_TURSO_BIN=~/targets/turso/target/release/tursodb
export GRAFT_TURSO_PREFIX_BIN=~/targets/turso-prefix/target/release/tursodb
```
</details>

## Experimental setup

| | |
|---|---|
| Engine under test | `tursodb`, `Turso 0.9.0-pre.2` |
| Revisions | `730017f96` (discovery), `b588fbdc6` (before), `7f0a9e6f4` (after) — roles in [`configs/engine-revisions.yaml`](configs/engine-revisions.yaml) |
| Corpus | `duckdb/duckdb`, mined `2026-10-28`: 600 candidates → 200 issues → 659 reproductions |
| Platform | Ubuntu, Linux 7.0.0-42-generic x86_64, Python 3.14, Rust 1.88 |
| Oracle | crash only — a single statement must not kill the process |
| Determinism | every random choice from a seeded generator; the seed is stored in each finding |

## Dependencies

`PyYAML`, `requests`, `sqlglot`, `pytest`, pinned in
[`requirements.txt`](requirements.txt). A Rust toolchain is needed only to build
the engine. `GITHUB_TOKEN` is needed only to re-mine the corpus, which is
committed and not required for reproduction; it is read from the environment and
never written anywhere.

## Repository layout

```
graft/
├── graft/
│   ├── config.py log.py proc.py     targets, JSONL logging, subprocess control
│   ├── engines/                     the tursodb adapter; a planted-bug engine for tests
│   ├── corpus/                      mining, extraction, classification, TEMPLATING
│   ├── generate/                    capability probing and the typed SQL generator
│   ├── oracle/crash.py              the only oracle
│   ├── triage/                      reduce, re-verify, duplicate search, report
│   ├── campaign.py                  the seeded, resumable loop
│   └── reproduce.py                 the five-step replay
├── configs/                         targets, engine revisions, probed capabilities, weights
├── data/                            the mined corpus, committed
├── findings/bug-01/ bug-02/         reproduction, patch, pull request, upstream state
├── docs/                            motivation, related work, methodology, design,
│                                    implementation, evaluation, provenance, references
└── tests/                           514 tests
```

## Limitations

The honest short list; the full one is
[`docs/evaluation.md`](docs/evaluation.md) §6.

- **One defect found by the tool, in one engine.** An existence result. It
  establishes no rate and no comparison against another technique.
- **RQ3 is unmeasured.** That transfer beats generation per unit of effort is
  *not* established here. Both arms exist; the experiment was not run.
- **Crash-class defects only.** A wrong answer that does not crash is invisible
  to this oracle. No wrong-result defect is reported or claimed.
- **Bounded by the corpus.** Templating fixes the call structure, so GRAFT
  cannot explore a shape no upstream report exhibits.
- **Most reports do not transfer** — 16 of 60 classified were judged plausibly
  transferable, by us, unvalidated.
- **The hypothesis came after the first finding.** The blind-spot measurement
  explains why the defect survived; it is not the design intent that produced
  it. Dates in [`docs/provenance.md`](docs/provenance.md).

## References

[`docs/references.md`](docs/references.md). Twelve works, with access depth
marked: two read in full, ten at abstract depth. Nothing is summarised beyond
the depth it was read at.

## Licence

[MIT](LICENSE).
