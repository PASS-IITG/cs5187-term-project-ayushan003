# Handoff: GRAFT: Cross-Engine Bug-Report-Guided Fuzzing of a SQLite-Compatible Database — state on 2026-11-11

Read this first in a new session. Keep it current: it replaces the conversation
history.

## 1. The project in one paragraph

GRAFT tests Turso, a SQLite-compatible engine in Rust, with two sources of SQL:
the structure of closed, reproduced DuckDB bug reports refilled with boundary
values, and a typed SQL generator. Its only oracle is a crash oracle, which needs
no reference engine. It found four previously unknown defects in Turso's string
functions (`lpad`, `rpad`, `repeat`, and a character-versus-byte length check);
our two patches fixing all four were merged upstream. None of Turso's own
fuzzers can express the four, because `lpad`, `rpad` and `repeat` are absent
from all of them.

## 2. Venue

| Item | Value |
|---|---|
| Venue / track | FSE format (CS-5187 Term Project) |
| CFP | https://conf.researchr.org/home/fse-2026 (checked 2026-09-30) |
| Format | `acmart` `acmsmall,screen,review` — `anonymous` dropped, see §5 |
| Page limit | 12 pages main text (`paper.conf`); references start on page 13 |
| Deadline | Fri 13 Nov 2026, 23:59 IST (course page, checked 2026-11-11) |
| Submission | Overleaf link, PDF, public repository, reproduction instructions, via Teams |

## 3. Where things are

* Paper: this directory (`sections/`, `macros.tex`, `figures/`).
* Tool: the repository root (`graft/`, `tests/`, `Makefile`).
* Raw data behind every number: the paths in §4.

## 4. Numbers in the paper and their sources

Reference environment: Intel Core i7-1360P, 16 logical CPUs, 15 GB, Linux 7.0,
Python 3.14.4, Rust 1.88.0, Turso 0.9.0-pre.2.

| Claim | Value | Source |
|---|---|---|
| Turso differential fuzzer size | 41,205 lines, 57 Rust files | `find testing/differential-oracle -name '*.rs' \| xargs cat \| wc -l` at b588fbdc6 |
| Turso fuzzer scalar vocabulary | 47 functions; no LPAD, RPAD, REPEAT | `SCALAR_FUNCTIONS` in `testing/differential-oracle/sql_gen/src/functions.rs` at b588fbdc6 |
| Turso fuzzer literal policy | int −1,000,000..1,000,000; strings alphanumeric, ≤50 | `LiteralConfig::default` in `sql_gen/src/policy.rs` at b588fbdc6 |
| No Turso fuzzer calls the three functions | 0 files | `grep -rliE 'lpad\|rpad\|\brepeat\b' fuzz/fuzz_targets/` and over `testing/` (Rust and non-Rust) |
| Defects found | 4 | `findings/README.md` |
| Before / after per defect | 134 in 2.28 / 2.24 / 2.27 / 3.96 s; 1 in 0.02 s | measured 2026-11-10 on b588fbdc6 and 7f0a9e6f4; `findings/defect-0N-*/README.md` |
| Defect 4 uncapped, debug build | ~34 s, 11.7 GB peak | `findings/patches/issue-11260.md` |
| Seeds regenerating each defect | 4553, 101, 5080, 5429 (mode `both`) | each run with `--max-cases 1`; `findings/README.md` |
| Upstream | #11219/#11225 merged ec86571642a6 +138/−41; #11260/#11259 merged 121cd0a8c80b +54/−5 | `findings/patches/` and the GitHub record |
| Corpus | 600 candidates → 200 issues → 659 repros (526 fix tests, 133 bodies); 387 requests, 354 cached | `data/mined/duckdb/meta.json`, `data/repros/duckdb.jsonl` |
| Transferable share | 16 of 60 classified | `data/classified/duckdb.jsonl` |
| Template fill rate | 1,054 of 4,000 seeds, 26.4% | `make reproduce`, step 3 |
| Ablation (Tables 2 and 4) | executed, valid, hits, distinct, first seeds, time, rate | `experiments/ablation.json` |
| Implementation size | 10,038 lines, 32 modules; 514 tests in 5,041 lines | `git ls-files graft` / `tests`; `make test` |

## 5. Open decisions (ask the authors)

1. **Anonymity.** The FSE CFP asks for double-anonymous review, but the course
   requires a signed contribution statement in the report. The paper keeps the
   authors' names and drops only the `anonymous` option. Confirm with the
   instructor if in doubt.
2. **Generative-AI disclosure.** The section must be confirmed by both authors
   before submission (STYLE.md §4).
3. **The repository must be public** before submission: the Data Availability
   section cites it as public.

## 6. Paper TODOs

None. `make check` passes with zero `\todo` and zero `\num`.

## 7. Lessons / pitfalls

* Defects 1 and 2 share one oracle signature. In every ablation run the first hit
  on it was an `rpad` call, so the seed for defect 1 had to be found separately
  (4553) and checked to call `lpad`.
* The reduction record of five executions belongs to defect 3; defect 1 was
  reduced by hand.
* DuckDB #27846 is a performance regression, not a correctness bug; the
  multi-byte queries come from its fix's regression test.
* `acmart` defines `\Ref`; the reference-engine macro is `\Rengine`.
* To build locally without system packages: put `acmart`, `libertine`, `newtx`
  and `inconsolata` in a private `TEXMFHOME`. Overleaf has all of them.
