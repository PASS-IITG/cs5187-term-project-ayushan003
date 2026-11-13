# GRAFT — Cross-Engine Bug-Report-Guided Fuzzing for SQL Engines

GRAFT finds crash defects in SQL database engines by reusing the bug reports of
*other* engines. It mines confirmed, reproduced reports from one engine's issue
tracker, keeps the structure of each reproduction, refills its literals with
boundary values, and runs the result — together with the output of a typed SQL
generator — against the engine under test. A crash oracle judges every
statement, so no reference engine is needed.

Applied to [Turso](https://github.com/tursodatabase/turso), a SQLite-compatible
engine written in Rust, GRAFT found three previously unknown defects in its
string functions. Together with a fourth, identified during upstream review of
our fix, all four are fixed in Turso by two merged pull requests.

| | |
|---|---|
| **Engine under test** | Turso 0.9.0-pre.2 (`tursodb`) |
| **Corpus** | 659 reproductions from 200 confirmed DuckDB issues |
| **Defects fixed upstream** | 4, through 2 issues and 2 merged pull requests |
| **Headline reproduction** | `make reproduce` |
| **Test suite** | 514 tests |
| **Report** | [`paper/GRAFT-report.pdf`](paper/GRAFT-report.pdf) (FSE format, ACM `sigconf`) |

## Why GRAFT

A fuzzer can only test the functions its generator emits and judge the results
its oracle understands. Turso already runs a differential fuzzer in CI that
compares its answers against SQLite. Its own source sets the limits of what it
can reach:

- integer literals are drawn from `[-1_000_000, 1_000_000]`;
- strings are alphanumeric only;
- `lpad`, `rpad` and `repeat` are not in its function vocabulary;
- SQLite, its reference engine, does not implement those functions.

GRAFT targets exactly that region. Bug reports from another engine point at
corners where someone has already been hurt, and a crash oracle can judge a
function that has no reference implementation.

## Results

### Defects

| # | Function | Root cause | Identified by | Upstream |
|---|---|---|---|---|
| [1](src/findings/defect-01-lpad/README.md) | `lpad` | result allocated before the length check | GRAFT, seed 4553 | [#11219](https://github.com/tursodatabase/turso/issues/11219) → [#11225](https://github.com/tursodatabase/turso/pull/11225) |
| [2](src/findings/defect-02-rpad/README.md) | `rpad` | result allocated before the length check | GRAFT, seed 101 | [#11219](https://github.com/tursodatabase/turso/issues/11219) → [#11225](https://github.com/tursodatabase/turso/pull/11225) |
| [3](src/findings/defect-03-repeat/README.md) | `repeat` | size computation overflows; allocated before the check | GRAFT, seed 5080 | [#11219](https://github.com/tursodatabase/turso/issues/11219) → [#11225](https://github.com/tursodatabase/turso/pull/11225) |
| [4](src/findings/defect-04-multibyte-fill/README.md) | `lpad`, `rpad` | length checked in characters against a limit in bytes | review of PR #11225 | [#11260](https://github.com/tursodatabase/turso/issues/11260) → [#11259](https://github.com/tursodatabase/turso/pull/11259) |

Each defect is a single statement that ends the engine's process. Measured on
Turso `b588fbdc6` (before our patches) and `7f0a9e6f4` (after):

| Statement | Before | After |
|---|---|---|
| `select LPAD('a', 9223372036854775807, 'b')` | exit 134 (SIGABRT), `memory allocation of 9223372036854775806 bytes failed` | exit 1, `string or blob too big (18)` |
| `select RPAD('a', 9223372036854775807, 'b')` | exit 134 (SIGABRT), `memory allocation of 9223372036854775806 bytes failed` | exit 1, `string or blob too big (18)` |
| `select repeat('a', 9223372036854775807)` | exit 134 (SIGABRT), `memory allocation of 9223372036854775807 bytes failed` | exit 1, `string or blob too big (18)` |
| `SELECT length(LPAD('a', 1000000000, '😀'))` | exit 134 (SIGABRT) under a 6 GB cap, `memory allocation of 3999999996 bytes failed` | exit 1, `string or blob too big (18)` |

Root causes, measurements and regenerating seeds are in
[`src/findings/`](src/findings/README.md).

### Upstream submissions

State as of 2026-11-13.

| Project | Submission | Subject | State |
|---|---|---|---|
| Turso | Issue [#11219](https://github.com/tursodatabase/turso/issues/11219) | defects 1–3 | Closed, fixed by #11225 |
| Turso | PR [#11225](https://github.com/tursodatabase/turso/pull/11225) | fix for defects 1–3 | **Merged** (`ec86571642a6`) |
| Turso | Issue [#11260](https://github.com/tursodatabase/turso/issues/11260) | defect 4 | Closed, fixed by #11259 |
| Turso | PR [#11259](https://github.com/tursodatabase/turso/pull/11259) | fix for defect 4 | **Merged** (`121cd0a8c80b`) |
| Apache DataFusion | Issue [#27001](https://github.com/apache/datafusion/issues/27001) | `A * 0` simplification drops the result type | Open |
| Apache DataFusion | PR [#27002](https://github.com/apache/datafusion/pull/27002) | fix for #27001 | Open |

The DataFusion submissions were made during the same investigation and are
outside the Turso evaluation. Submitted issue and pull-request texts, and both
Turso patches, are in [`src/findings/patches/`](src/findings/patches/README.md).

### Input sources at an equal budget

Three 20,000-case runs on the pre-fix build, one per input source
([`src/experiments/`](src/experiments/README.md)):

| Source | Executed | Valid | Hits | First defect (case) |
|---|---|---|---|---|
| Mined templates | 19,169 | 8.9% | 8 | 914 |
| Typed generator | 20,000 | 98.3% | 12 | 762 |
| Both | 19,759 | 71.1% | 15 | **101** |

Both sources reach the two root causes of defects 1–3. Drawing from both finds
the first defect soonest, about two seconds into the run.

## How it works

```
 DuckDB closed issues ──mine──▶ 659 reproductions
                                       │
                 literals ──▶ typed holes   (keep the reproduction's structure)
                                       │
                 boundary values ──▶ filled statements ─┐
                                                        │
 capability probe ──▶ typed generator ──▶ schema, ──────┤   --mode selects
                      data, queries                     │   either or both
                                                        ▼
                                                 run on tursodb
                                                        │
                              crash oracle: did one statement end the process?
                                                        │
                reduce ─▶ re-verify on current main ─▶ duplicate search ─▶ report
```

1. **Measure the blind spot.** Read the incumbent fuzzer's source for its
   function vocabulary, literal domain and reference engine.
2. **Mine and template.** Collect closed DuckDB issues labelled *reproduced*,
   extract the SQL from each fix's regression test and the issue body, and
   replace every literal with a typed hole.
3. **Fill and run.** Fill holes from boundary-weighted pools (including
   `i64::MAX`), add cases from a typed generator gated by the engine's probed
   capabilities, and run each case in a fresh `tursodb` process with a time and
   memory limit.
4. **Triage.** Group violations by signature, reduce, re-verify on the current
   upstream main branch, search for duplicates, and draft a report.

For example, the regression test added for DuckDB issue #27846 templates to

```sql
select LPAD({s4}, {n0}, {s5}), LPAD({s6}, {n1}, {s7}),
       LPAD({s8}, {n2}, {s9}), RPAD({s10}, {n3}, {s11})
```

and over seeds 0–3999 places `9223372036854775807` into a pad call in
1,054 cases (26.4%).

## Getting started

### Requirements

- Python 3.10 or later (results were produced with Python 3.14.4)
- The pinned packages in [`src/requirements.txt`](src/requirements.txt):
  `PyYAML 6.0.3`, `requests 2.34.2`, `sqlglot 30.27.0`, `pytest 9.2.0`
- To run against an engine: Rust 1.88.0 and Turso builds (see below)

### Install

```bash
git clone https://github.com/PASS-IITG/cs5187-term-project-ayushan003
cd cs5187-term-project-ayushan003/src
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

### Reproduce the headline result

From `src/`:

```bash
make reproduce
```

This replays the chain end to end: the source bug report, its template, the
seeded fill and its 26.4% rate, the four limits of Turso's own fuzzer, and
defects 1 and 4 before and after the patches. Steps 1–4 need only this
repository. Step 5 runs when both Turso builds are available and otherwise
prints the commands to build them.

### Build the Turso binaries

```bash
# after: current main, containing both merged fixes
git clone https://github.com/tursodatabase/turso ~/targets/turso
cd ~/targets/turso && cargo build --release --bin tursodb

# before: the last commit without our fixes, in its own worktree
git -C ~/targets/turso worktree add ~/targets/turso-prefix b588fbdc6
cd ~/targets/turso-prefix && cargo build --release --bin tursodb

export GRAFT_TURSO_BIN=~/targets/turso/target/release/tursodb
export GRAFT_TURSO_PREFIX_BIN=~/targets/turso-prefix/target/release/tursodb
```

## Usage

All commands run from `src/`.

| Command | What it does |
|---|---|
| `make reproduce` | replay the headline result |
| `make test` | run the 514 tests |
| `make campaign` | run a bounded 2,000-case campaign against Turso |
| `python -m graft.campaign --mode {variants,fuzzer,both} --max-cases N --seed-start S` | run a campaign from a chosen seed and input source |
| `make probe` | re-probe Turso's capabilities into `configs/capabilities.yaml` |
| `make smoke` | run a few statements through the engine adapter |
| `make corpus` | re-mine the DuckDB corpus (needs `GITHUB_TOKEN`; the corpus is already committed) |

Every random choice comes from a seeded generator and the seed is stored with
each finding, so any case can be regenerated from its seed alone. The commands
for each experiment are in [`src/experiments/README.md`](src/experiments/README.md).

## Repository structure

```
.
├── paper/                       the report
│   ├── GRAFT-report.pdf         the report (FSE format, ACM sigconf, anonymous)
│   └── paper-artifacts/         the complete LaTeX project
│       ├── main.tex, macros.tex, references.bib
│       ├── sections/, figures/  one file per section; the workflow figure
│       ├── Makefile, scripts/   build, submission checks
│       ├── paper.conf, .gitignore
│       └── overleaf.zip         the same sources, ready to import into Overleaf
├── src/                         the code
│   ├── graft/                   the tool
│   │   ├── engines/             tursodb adapter and a planted-defect test engine
│   │   ├── corpus/              mining, extraction, classification, templating
│   │   ├── generate/            capability probing and the typed SQL generator
│   │   ├── oracle/              the crash oracle
│   │   ├── triage/              reduction, re-verification, duplicate search, reports
│   │   ├── campaign.py          the seeded, resumable campaign loop
│   │   └── reproduce.py         the headline replay
│   ├── tests/                   514 tests
│   ├── configs/                 targets, engine revisions, capabilities, weights
│   ├── data/                    the mined DuckDB corpus
│   ├── experiments/             experiment scripts and raw results
│   ├── findings/                one page per defect, plus upstream patches
│   ├── docs/                    design, implementation notes, internal spec
│   ├── Makefile
│   └── requirements.txt
├── CONTRIBUTIONS.md             signed contribution statement
├── LICENSE
└── README.md
```

## Report

[`paper/GRAFT-report.pdf`](paper/GRAFT-report.pdf) is the full report in FSE
format, written in the ACM Primary Article Template (`acmart`, `sigconf`) and
prepared for double-anonymous review. Its sections are Motivation, Technique,
Implementation, Evaluation, Related Work and Threats to Validity, followed by
the Conclusion, Data Availability, Use of Generative AI and Author
Contributions.

The complete LaTeX project is in
[`paper/paper-artifacts/`](paper/paper-artifacts/), and
[`overleaf.zip`](paper/paper-artifacts/overleaf.zip) holds the same sources for
import into Overleaf (*New Project → Upload Project*). To build locally with a
TeX Live that includes `acmart`:

```bash
cd paper/paper-artifacts
make report     # builds main.pdf and copies it to ../GRAFT-report.pdf
make check      # submission checks: errors, citations, page count
make overleaf   # rebuilds overleaf.zip from the current sources
```

## License

[MIT](LICENSE)
