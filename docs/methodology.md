# Methodology

This chapter is written in the order the decisions were taken: target, then
corpus, then transfer, then oracle, then triage. Each section says what was
decided and why, and marks anything that is a limitation rather than a choice.

Everything described here was performed. Where a step was done by hand rather
than by the tool, it says so.

---

## 1. Choosing the target and the source

Two engines are needed: one to **test**, and one to **take test material from**.
They have different requirements.

**The engine under test** must be actively maintained, so that a report gets a
response within the project's timescale; must be reachable from a scriptable
CLI; and must be buildable from source at an arbitrary commit, so that a defect
can be confirmed present on current `main` and absent after a fix. Turso meets
all three: `tursodb` takes SQL on standard input, the repository builds with
`cargo build --release`, and the project merges outside contributions.

**The engine to mine** must have a large, public, *labelled* history of closed
defects with reproductions attached. DuckDB meets this unusually well: its
maintainers add a `reproduced` label once they have confirmed a report, and
fixes usually land with a regression test containing the triggering query. That
label is the filter `graft/corpus/fetch.py` uses — DuckDB has no `bug` label, so
`reproduced` is the marker of a confirmed defect.

The two also had to be *dialect-compatible enough*. Turso targets SQLite
compatibility; DuckDB's dialect is broader but overlaps substantially on scalar
functions, which is where the mined corpus is richest. A mined query that Turso
cannot parse is simply rejected as a user error and costs nothing.

**GRAFT never executes DuckDB.** It reads DuckDB's issue tracker. The DuckDB
entry in `configs/targets.yaml` exists only to give the miner a repository and a
label, and has an empty `oracles` list.

## 2. Building the corpus

`graft/corpus/` mines closed, labelled issues through the GitHub API and
extracts runnable reproductions. The recorded run
(`data/mined/duckdb/meta.json`, mined `2026-10-28T17:09:08Z`, `max_issues: 200`)
reports:

| | |
|---|---|
| candidate issues examined | 600 |
| skipped: not closed as completed | 101 |
| skipped: pull requests | 1 |
| **kept** | **200** |
| with a fix commit identified | 140 |
| with a fix pull request identified | 118 |
| with fix-test text retrieved | 126 |
| API requests made / cache hits | 387 / 354 |

From those 200 issues, `graft/corpus/extract.py` produced **659 reproductions**
(`data/repros/duckdb.jsonl`):

- **526 from the fix's own test.** The strongest source: a maintainer wrote this
  query specifically to pin the defect, and it is the exact input the fix was
  validated against.
- **133 from the issue body.** Fenced SQL blocks in the report itself.
- **501 of the 659 carry setup statements** (`CREATE TABLE`, `INSERT`), so they
  bring their own schema and data rather than needing one invented.

A 60-issue subset was classified by hand (`data/classified/duckdb.jsonl`) to see
what the corpus actually contains: `other` 20, `optimizer_rewrite` 11,
`join_semantics` 7, `cast` 4, `nested_type` 4, `window_frame` 3,
`null_handling` 3, `aggregate_semantics` 2, and one each of `integer_overflow`,
`datetime`, `type_coercion`, `string_function`, `float_precision`,
`sorting_collation`. Of those 60, only **16 were judged plausibly transferable**
to another engine; 44 were DuckDB-specific (nested types, DuckDB-only syntax,
optimizer internals).

That ratio is worth stating plainly: **most of a mined corpus does not
transfer.** The method does not depend on a high hit rate per report — it
depends on the corpus being large and free.

## 3. Transfer: keeping the shape, regenerating the values

A mined reproduction cannot usually be replayed as-is. Its literal values were
chosen for the *first* engine's defect, which the second engine has probably
never had. What is reusable is the structure: which functions, nested how, with
which kinds of argument.

So `graft/corpus/templates.py` replaces **only literals** — integers, reals and
strings — with typed holes, and leaves everything else exactly as the maintainer
wrote it. The example that matters here, from `duckdb/duckdb#27846`:

```
mined:     select LPAD('x', 10, '0'), LPAD('x', 4, '😀'),
                  LPAD('x', 3, '😀ab'), RPAD('x', 3, '😀ab')

template:  select LPAD({s4}, {n0}, {s5}), LPAD({s6}, {n1}, {s7}),
                  LPAD({s8}, {n2}, {s9}), RPAD({s10}, {n3}, {s11})
```

Four integer holes, eight text holes. Instantiation fills each from a
type-appropriate pool that is deliberately weighted toward boundaries: for
integers, zero, ±1, the 8/16/32-bit limits, and `i64::MAX` and `i64::MIN`.

**Why literals only.** Re-pointing a mined query at a freshly generated schema
would require column types and arities to line up, and the corpus is full of
engine-specific shapes where a wrong substitution produces SQL that merely looks
plausible. Keeping the structure fixed means every instantiation is valid by
construction, with no repair step. The cost, stated as a limitation rather than
a feature: **GRAFT cannot explore any structure that no upstream report
exhibits.** `docs/related-work.md` places this against Fuzzilli and RepairFuzz,
which answer the same validity problem differently.

## 4. The second generator, and why it exists

`graft/generate/` is an independent, typed SQL generator: it builds a schema,
populates it, and composes expressions and queries from a vocabulary gated by
what the target engine actually supports (`configs/capabilities.yaml`, probed
from the real binary rather than assumed).

It is in the repository for two reasons. It supplies the comparison arm —
without a generator there is nothing to compare transfer *against* — and it
makes the campaign driver able to run either source, selected by `--mode`:

| mode | cases come from |
|---|---|
| `variants` | mined templates only |
| `fuzzer` | the generator only |
| `both` | either, per case |

The defects reported here arrived in `both` mode. Which source reaches which
defect, at equal budget, is measured in `experiments/README.md` and reported
under RQ3 in `docs/evaluation.md`.

## 5. The oracle

One oracle: **a single SQL statement must not terminate the process.** If
`tursodb` exits on a signal, or panics, or reports an internal error, that is a
violation. A user error — bad syntax, unknown table, a documented limit — is not.

This is the whole of `graft/oracle/crash.py`, and the choice is deliberate.

- **It needs no reference implementation.** The defects reported here are in
  `lpad`, which SQLite does not implement. Any differential oracle, including
  Turso's own, is silent on them by construction. An oracle that compares the
  engine against *itself* — does it survive? — has no such blind spot.
- **It cannot report a dialect difference as a defect**, which is the dominant
  false-positive source in cross-engine SQL testing.
- **Its false-positive rate is structurally zero.** The engine either aborted or
  it did not; there is no judgement to get wrong.
- **It is sufficient in practice.** SQLBull's 63 zero-day DBMS bugs (NDSS 2026)
  are all crashes, internal errors and assertion failures, found without any
  differential or metamorphic oracle.

The limitation is equally clear and is recorded in `docs/evaluation.md`: a
**wrong answer** that does not crash is invisible to GRAFT. This project reports
no wrong-result defects and claims none.

**Validating the oracle.** An oracle that reports nothing is worthless evidence
unless it is known to fire when a defect is present. `tests/test_oracle_planted.py`
runs the crash oracle against `graft/engines/fake.py`, a deliberately defective
engine, and asserts that it catches the planted crash and stays silent on a
correct engine and on invalid SQL.

## 6. From a hit to a report

A violation is not a finding. `graft/triage/` takes it through four steps, all
of which were applied to defect #1:

1. **Reduce** (`reduce.py`). Shrink the statement while preserving the same
   violation, AST-aware through `sqlglot` where it parses and textual where it
   does not. defect #1 went from a four-call generated query to a single
   statement, ratio 0.939, in five executions.
2. **Re-verify** (`verify.py`). Replay on a *freshly started* engine process to
   exclude accumulated state, and then on a build of current upstream `main` to
   exclude "already fixed". defect #1 was re-confirmed on `b588fbdc6`, built
   2026-11-01, before it was filed.
3. **Duplicate search** (`dupecheck.py`). Query the upstream tracker and
   **record the queries used**, so the search is auditable rather than asserted.
   For defect #1: `repo:tursodatabase/turso "lpad" crash`, `"rpad" crash`,
   `"abc" crash`, `"crash" in:title`. The nearest existing issue, #11175, is
   about wrong argument counts panicking — a different defect.
4. **Draft** (`report.py`), then a human reads it, reproduces it, and decides
   whether to file. Step 3 is the one that stopped a report: an early candidate
   (`select ''->>''`, a VDBE panic) was withheld because an upstream pull
   request touching that code had merged after our pinned build, making it
   likely already fixed. It was marked a duplicate suspect and never filed.
   `docs/related-work.md` relates this to the Fixed Vulnerability Filter of
   Tan et al. (ICSE 2025).

## 7. Measuring the blind spot

The claim that these defects lie outside Turso's own fuzzer's reach is checked
against that fuzzer's source, not inferred. Its literal policy
(`testing/differential-oracle/sql_gen/src/policy.rs`, `impl Default for
LiteralConfig`) sets `int_max: 1_000_000` and
`string_charset: StringCharset::Alphanumeric`, and `grep -ri 'lpad\|rpad'` over
the whole differential-oracle tree returns no matches.

`docs/evaluation.md` gives the four limits and how to verify each in one
command. `make reproduce` prints them as step 4.
