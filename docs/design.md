# Design and architecture

## The pipeline

```
   duckdb/duckdb issue tracker
            │
            │  graft/corpus/fetch.py      closed issues labelled "reproduced"
            │  graft/corpus/github.py     cached, rate-limited GitHub client
            ▼
   data/mined/duckdb/issues.jsonl         200 issues
            │
            │  graft/corpus/extract.py    pull runnable SQL out of fix tests
            │                             and issue bodies
            ▼
   data/repros/duckdb.jsonl               659 reproductions
            │
            │  graft/corpus/classify.py   label a subset by defect class
            │  graft/corpus/templates.py  literals -> typed holes
            ▼
   templates ──────────────┐
                           │              graft/generate/*  (the comparison arm)
   schema + data + query ──┤              capabilities-gated typed SQL
                           │
                           ▼
                  graft/campaign.py       seeded, resumable; --mode picks the source
                           │
                           │  graft/engines/turso.py   subprocess, timeout, memory cap
                           ▼
                  graft/oracle/crash.py   did a single statement kill the process?
                           │
                           ▼ violation
                  graft/triage/
                    reduce.py    shrink, preserving the violation
                    verify.py    fresh instance, then current main
                    dupecheck.py search upstream, record the queries
                    report.py    draft for a human to read and file
                           │
                           ▼
                  findings/bug-0N/         reproduction, patch, upstream links
```

## Components

| Module | Lines | Responsibility |
|---|---|---|
| `graft/` (config, log, proc) | 1,216 | target resolution, JSONL logging, subprocess control with timeouts |
| `graft/engines/` | 1,619 | one interface over `tursodb`; error classification; a planted-bug engine for tests |
| `graft/corpus/` | 2,399 | mining, extraction, classification, templating — **the transfer mechanism** |
| `graft/generate/` | 2,529 | capability probing and the typed SQL generator — the comparison arm |
| `graft/oracle/` | 230 | the crash oracle and the violation type |
| `graft/triage/` | 2,044 | reduction, re-verification, duplicate search, report drafting, upstream ledger |
| `graft/campaign.py` | — | the seeded campaign loop |
| `graft/reproduce.py` | — | the one-command replay of the headline result |
| `tests/` | 5,041 | 514 tests |

Total: **10,037 lines** of implementation, 5,041 of tests.

## Design decisions, and what each one costs

### A subprocess per engine, not a library binding

`tursodb` is driven as a child process over stdin/stdout, with a timeout and a
memory cap. A library binding would be faster and would give typed cells instead
of text.

The reason is that the defects being hunted *kill the process*. An in-process
binding would take the test harness down with the engine, and the abort — the
exact signal the oracle looks for — would be unobservable. A subprocess makes
process death a first-class, catchable outcome.

**The cost**, recorded because it caused a real harness defect: the CLI returns
every cell as text, so types must be inferred from the text, and Turso writes
errors to **stdout interleaved with result rows**. An early version checked only
the first line of an output block for a diagnostic, so a query that emitted two
rows and *then* failed was read as three rows of data. The fix
(`embedded_diagnostic` in `graft/engines/turso.py`) scans the whole block. The
lesson generalises: with a text protocol, a diagnostic can arrive anywhere.

### Literals only in templates

Covered in `docs/methodology.md` §3. Validity by construction, at the cost of
never exploring a structure no upstream report exhibits.

### One oracle

Covered in `docs/methodology.md` §5. No reference implementation needed, no
dialect false positives, a structurally zero false-positive rate — and complete
blindness to a wrong answer that does not crash.

### The seed is the state

Every random choice comes from a seeded `random.Random`. The seed is stored in
each finding and the last seed used is written to `workdir/<target>.seed` on
every statistics tick, so a campaign resumes where it stopped and any case can
be replayed from its seed alone. This is what makes `make reproduce`
deterministic.

### Capabilities are probed, not assumed

`graft/generate/capabilities.py` asks the real binary what it supports —
which types it distinguishes, which functions exist, which clauses parse — and
writes the answer to `configs/capabilities.yaml`. The generator then refuses to
emit anything outside that set.

This exists because assumption was wrong in practice. Turso and SQLite resolve
any unrecognised type name to an affinity, so declaring `DATE` there really
stores an integer: a generator that "supports DATE" on the basis of the
declaration parsing would be producing integer columns while believing it had
achieved type coverage.

### Engine revisions are pinned, with roles

Three Turso revisions matter and they are not interchangeable
(`configs/engine-revisions.yaml`):

| commit | contains our fixes | role |
|---|---|---|
| `730017f96` | no | the revision the discovery campaign ran against |
| `b588fbdc6` | no | re-verification before filing; the pull-request base; the **"before"** binary |
| `7f0a9e6f4` | **yes** | contains both merged fixes; the **"after"** binary |

A before/after demonstration needs two builds from the same source tree
differing only by the merge commit. `make reproduce` uses both and skips step 5,
with instructions, when either is missing — it never reports an engine result it
did not obtain.

## What is deliberately absent

| Not here | Why |
|---|---|
| Differential or metamorphic oracles | Neither finding needed one, and SQLite — the only sane reference for a SQLite-compatible engine — has no `lpad`, so a differential oracle is blind to both defects by construction. |
| Any engine but Turso at run time | GRAFT tests one engine. DuckDB is a corpus source and is never executed. |
| Coverage-guided feedback | Not implemented. `configs/weights.yaml` is a static, hand-set bias, not a feedback loop. Stated as a limitation in `docs/evaluation.md`. |
| Grammar learning | Not implemented. See the FDLoop entry in `docs/related-work.md` for what the principled version would be. |
| Any language-model call | Nothing in the pipeline calls one. |
