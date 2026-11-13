# GRAFT

**Grafting known defects across SQL engine implementations.**

CS 5187 — Software Analysis, Monsoon 2026, Dept. of CSE, IIT Guwahati.
Term project by Ayush Singh Chauhan (240101117) and Mehul Raj (240101125); see
[`CONTRIBUTIONS.md`](CONTRIBUTIONS.md).

GRAFT tests a SQL engine with two sources of input: bug reports that *another*
engine's maintainers already wrote, stripped to their structure and refilled
with boundary values, and a typed generator of its own. It found three
previously unknown defects in [Turso](https://github.com/tursodatabase/turso).
Our patch for them exposed a fourth, which GRAFT did not find. Our two patches,
which fix all four, are merged upstream.

> **Status, stated precisely.** GRAFT found **three defects** in Turso's string
> functions — `lpad`, `rpad` and `repeat` each abort the process from a single
> statement — and regenerates each from a recorded seed on a pre-fix build.
> Moving the length check in front of the allocation exposed a **fourth**: the
> check counted characters against a limit in bytes. We noticed it while
> writing the first patch but misjudged it as harmless; Turso's automated
> reviewer showed it was not, and we fixed it. Upstream consolidated the four
> into **two issues and two pull requests, both merged**.
> [`findings/README.md`](findings/README.md) maps each defect to its code path,
> seed and upstream links.

| Deliverable | Where |
|---|---|
| Report (FSE format, ACM `sigconf`) | [`GRAFT-report.pdf`](GRAFT-report.pdf), source in [`paper/`](paper/) |
| Source code, MIT licence | [`graft/`](graft/), [`LICENSE`](LICENSE) |
| One-command reproduction | `make reproduce` — see [Reproducing](#reproducing) |
| Upstream issues and pull requests, with state | [Upstream submissions](#upstream-submissions) |
| Signed contribution statement | [`CONTRIBUTIONS.md`](CONTRIBUTIONS.md) and the report's *Author Contributions* |

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

## Research questions

Answered in Section 5 of the report and in [`experiments/`](experiments/README.md):

| | Question | Answer |
|---|---|---|
| RQ1 | Does GRAFT find previously unknown defects, accepted upstream? | Yes — three found by GRAFT, a fourth exposed by our fix; all four fixed by our two merged PRs |
| RQ2 | At an equal budget, which defects does each input source reach? | Mined templates and the generator each reach both root causes of #1–#3; neither reaches #4; both together find the first defect fastest |
| RQ3 | Could Turso's own fuzzers have found them? | No — the functions are outside their vocabulary, the values outside their domain, and SQLite lacks the functions |
| RQ4 | What does GRAFT cost? | Under eight minutes per 20,000-case run on a laptop |

## How it works

```
 duckdb/duckdb closed issues ──mine──▶ 659 reproductions
                                            │
                    literals ──▶ typed holes  (keep the maintainer's shape)
                                            │
                    boundary values ──▶ filled statements ─┐
                                                           │
 capability probe ──▶ typed generator ──▶ schema, data, ───┤  --mode picks
                      queries, boundary literals           │  either or both
                                                           │
                                      run on tursodb ◀─────┘
                                            │
                        crash oracle: did one statement kill the process?
                                            │
            reduce ─▶ re-verify on current main ─▶ duplicate search ─▶ report
```

The source report, `duckdb/duckdb#27846`, is a performance regression in
`lpad` and `rpad`. Its fix added a regression test with four pad calls, three of
them using multi-byte fills. Templated, that test query becomes:

```sql
select LPAD({s4}, {n0}, {s5}), LPAD({s6}, {n1}, {s7}),
       LPAD({s8}, {n2}, {s9}), RPAD({s10}, {n3}, {s11})
```

Over seeds 0–3999, that one template places `9223372036854775807` into a pad
call in **1,054 of them (26.4%)**. Turso's own fuzzer cannot place it even once.

The technique is described in Section 3 of the report; the code layout and
design decisions are in [`docs/design.md`](docs/design.md) and
[`docs/implementation.md`](docs/implementation.md).

## The findings

Each defect has its own page with the root cause and the measured
before-and-after: [`findings/README.md`](findings/README.md). Grouped by patch:

### Defects [#1](findings/defect-01-lpad/README.md), [#2](findings/defect-02-rpad/README.md), [#3](findings/defect-03-repeat/README.md) — allocation before the length check (found by GRAFT)

```sql
select LPAD('a', 9223372036854775807, 'b');
```

Three functions, two code paths, one fix. `pad()` — shared by `lpad` and
`rpad` — and `exec_repeat()` both sized and allocated their result from the
caller's argument before comparing anything against `Value::MAX_BLOB_LENGTH`,
so a length near `i64::MAX` reached the allocator and aborted the process.
`repeat` additionally multiplies, so it needed `checked_mul`.

Each aborts on its own, from its own single statement, in its own process:

| | exit | time | output |
|---|---|---|---|
| `lpad`, before `b588fbdc6` | **134** (SIGABRT) | 2.28 s | `memory allocation of 9223372036854775806 bytes failed` |
| `rpad`, before | **134** | 2.24 s | `memory allocation of 9223372036854775806 bytes failed` |
| `repeat`, before | **134** | 2.27 s | `memory allocation of 9223372036854775807 bytes failed` |
| all three, after `7f0a9e6f4` | 1 | 0.02 s | `Runtime error: string or blob too big (18)` |

Found by GRAFT 8 s into the first campaign, and regenerated from seeds 4553
(`lpad`), 101 (`rpad`) and 5080 (`repeat`). Issue
[#11219](https://github.com/tursodatabase/turso/issues/11219) · our PR
[#11225](https://github.com/tursodatabase/turso/pull/11225) **merged**
`ec86571642a6` (+138 / −41).

### [Defect #4](findings/defect-04-multibyte-fill/README.md) — a character count against a byte limit (exposed by our fix)

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

After #1–#3 were fixed, the check ran first — but it compared a count of
**characters** against `Value::MAX_BLOB_LENGTH`, a limit in **bytes**. A 4-byte
fill passes a billion-character check and builds about 4 GB.

| | exit | time | output |
|---|---|---|---|
| before, `ulimit -v 6000000` | **134** (SIGABRT) | 3.96 s | `memory allocation of 3999999996 bytes failed` |
| after | 1 | 0.02 s | `Runtime error: string or blob too big (18)` |

`3999999996` is 10⁹ characters × 4 bytes: the root cause is visible in the
failure message. GRAFT did not find this one. We noticed the unit mismatch while
writing PR #11225 and called it "conservative"; Turso's automated reviewer on
that PR showed it was permissive, and we then diagnosed and fixed it. GRAFT
cannot reach it: it needs a length between 2.5 × 10⁸ and 10⁹, and no boundary
pool holds one ([`experiments/`](experiments/README.md), experiments 2 and 3).
Issue [#11260](https://github.com/tursodatabase/turso/issues/11260) · our PR
[#11259](https://github.com/tursodatabase/turso/pull/11259) **merged**
`121cd0a8c80b` (+54 / −5).

## Upstream submissions

State checked on the GitHub API on 2026-11-13.

| Project | Submission | What | State |
|---|---|---|---|
| Turso | issue [#11219](https://github.com/tursodatabase/turso/issues/11219) | defects #1–#3 | **closed, fixed** by #11225 |
| Turso | PR [#11225](https://github.com/tursodatabase/turso/pull/11225) | patch for #1–#3 | **merged** as `ec86571642a6` |
| Turso | issue [#11260](https://github.com/tursodatabase/turso/issues/11260) | defect #4 | **closed, fixed** by #11259 |
| Turso | PR [#11259](https://github.com/tursodatabase/turso/pull/11259) | patch for #4 | **merged** as `121cd0a8c80b` |
| Apache DataFusion | issue [#27001](https://github.com/apache/datafusion/issues/27001) | `A * 0` simplification drops the result type | open |
| Apache DataFusion | PR [#27002](https://github.com/apache/datafusion/pull/27002) | its fix | open |

The DataFusion pair was filed during the investigation. It is not in Turso, it
is not a GRAFT result, and it is not claimed in the report; it is listed because
it was filed upstream from the same account. The text of the Turso issues and PRs as submitted, and both
patches, are in [`findings/patches/`](findings/patches/README.md).

## The report

The FSE-format paper is [`GRAFT-report.pdf`](GRAFT-report.pdf), written in the
ACM Primary Article Template (`acmart`, `sigconf`). Its sections are
Introduction, Motivation, Technique, Implementation, Evaluation, Related Work,
Threats to Validity and Conclusion, followed by Data Availability, Use of
Generative AI, and the signed Author Contributions.

Its LaTeX source is in [`paper/`](paper/), one file per section under
`paper/sections/`. [`paper/overleaf.zip`](paper/overleaf.zip) holds the same
sources, ready to upload to Overleaf as a new project. To build locally, run
`make` inside `paper/` (needs a TeX Live with `acmart`), and `make check` for
the submission checks.

## Reproducing

```bash
git clone https://github.com/PASS-IITG/cs5187-term-project-ayushan003
cd cs5187-term-project-ayushan003
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

make reproduce      # the headline result, end to end
make test           # 514 tests
```

`make reproduce` prints the source report, the template, the seeded fill, the
26.4% rate, the four blind-spot limits, and defects #1 and #4 before and after
our patches. Steps 1–4 need nothing but this repository. Step 5 needs two
`tursodb` builds and is **skipped with instructions** if either is missing — it
never reports an engine result it did not obtain.

To rediscover defects #1–#3 from scratch rather than replay them, run the arms
in [`experiments/README.md`](experiments/README.md) against the pre-fix build,
or a single seed from [`findings/README.md`](findings/README.md).

<details>
<summary>Building the <code>tursodb</code> binaries</summary>

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

Experiment 2 also needs the build with only our first patch, `ec86571642a6`;
the commands are in [`experiments/README.md`](experiments/README.md).
</details>

## Experimental setup

| | |
|---|---|
| Engine under test | `tursodb`, `Turso 0.9.0-pre.2` |
| Revisions | `730017f96` (discovery), `b588fbdc6` (before), `ec86571642a6` (first patch only), `7f0a9e6f4` (after) — roles in [`configs/engine-revisions.yaml`](configs/engine-revisions.yaml) |
| Corpus | `duckdb/duckdb`, mined `2026-10-28`: 600 candidates → 200 issues → 659 reproductions |
| Platform | Intel Core i7-1360P, 16 logical CPUs, 15 GB; Ubuntu, Linux 7.0 x86_64; Python 3.14.4; Rust 1.88.0 |
| Oracle | crash only — a single statement must not kill the process |
| Determinism | every random choice from a seeded generator; the seed is stored in each finding |

## Dependencies

Exact versions, pinned in [`requirements.txt`](requirements.txt):
`PyYAML==6.0.3`, `requests==2.34.2`, `sqlglot==30.27.0`, `pytest==9.2.0`, and
their transitive dependencies. Python 3.10 or later; results were produced with
Python 3.14.4. A Rust toolchain (1.88.0) is needed only to build the engine.
`GITHUB_TOKEN` is needed only to re-mine the corpus, which is committed and not
required for reproduction; it is read from the environment and never written
anywhere.

## Repository layout

```
.
├── graft/                           the tool
│   ├── config.py log.py proc.py     targets, JSONL logging, subprocess control
│   ├── engines/                     the tursodb adapter; a planted-bug engine for tests
│   ├── corpus/                      mining, extraction, classification, templating
│   ├── generate/                    capability probing and the typed SQL generator
│   ├── oracle/crash.py              the only oracle
│   ├── triage/                      reduce, re-verify, duplicate search, report
│   ├── campaign.py                  the seeded, resumable loop
│   └── reproduce.py                 the five-step replay behind `make reproduce`
├── tests/                           514 tests
├── configs/                         targets, engine revisions, probed capabilities, weights
├── data/                            the mined DuckDB corpus, committed
├── experiments/                     the three experiments and their raw results
├── findings/                        one directory per defect, plus patches/
├── docs/                            design, implementation notes, internal spec
├── paper/                           the report's LaTeX source and overleaf.zip
├── GRAFT-report.pdf                 the report
├── CONTRIBUTIONS.md                 the signed contribution statement
├── LICENSE                          MIT
├── Makefile                         reproduce, test, campaign, probe, smoke, corpus
└── requirements.txt                 pinned dependencies
```

## Limitations

- **Four defects in one engine, in one family of functions.** All four are in
  Turso's string functions and three share a root cause. This is an existence
  result, not a rate, and nothing here shows the method generalises to another
  engine pair.
- **GRAFT did not find defect #4.** Its boundary pools hold machine-integer
  limits, not the limits an engine sets itself, so no case has a length between
  2.5 × 10⁸ and 10⁹.
- **Crash-class defects only.** A wrong answer that does not crash is invisible
  to this oracle. No wrong-result defect is reported or claimed.
- **Transfer is bounded by the corpus and the fill pool.** Templating fixes the
  call structure, so no shape absent from an upstream report can be explored,
  and the text fill pool is ASCII-only.
- **Most mined reports do not transfer** — 16 of 60 classified were judged
  plausibly transferable, by us, unvalidated.
- **The ablation is one run per arm**, not repeated trials, so a first-hit seed
  is a single sample.
- **The blind-spot argument came after the first defect**, as an explanation of
  why it had survived, not as the design intent that produced it.

## References

The report's bibliography, [`paper/references.bib`](paper/references.bib).
Every entry was checked against its DOI record or publisher page.

## Licence

[MIT](LICENSE).
