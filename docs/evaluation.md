# Evaluation

Every number here was produced on the machine described below, on the date
given. Nothing is estimated. Where a measurement was not taken, it says so
rather than guessing.

---

## 1. Environment

| | |
|---|---|
| OS | Ubuntu, Linux 7.0.0-42-generic, x86_64 |
| Python | 3.14 |
| Rust toolchain | 1.88 (`cargo build --release`) |
| Engine under test | `tursodb`, version string `Turso 0.9.0-pre.2` |
| "before" build | `b588fbdc6` (2026-11-01), no fixes of ours |
| "after" build | `7f0a9e6f4` (2026-11-02), contains `ec86571642a6` and `121cd0a8c80b` |
| Corpus source | `duckdb/duckdb` issue tracker, mined `2026-10-28T17:09:08Z` |
| Dependencies | `requirements.txt`, pinned |

Both Turso builds come from the same source tree via `git worktree`, so they
differ only by the commits between them.

## 2. Research questions

### RQ1 — Can a fuzzer's blind spot be measured in advance, from its own source?

**Yes, for this target, and in four independent ways.** Turso ships its own
differential fuzzer in CI: `testing/differential-oracle/`, which generates SQL
and compares Turso against SQLite. Its literal policy
(`sql_gen/src/policy.rs`, `impl Default for LiteralConfig`) reads:

```rust
int_min: -1_000_000,
int_max:  1_000_000,
string_min_len: 1,
string_max_len: 50,
string_charset: StringCharset::Alphanumeric,
```

| # | Limit | How to verify | Blocks |
|---|---|---|---|
| 1 | `lpad`/`rpad` are not in its vocabulary | `grep -ri 'lpad\|rpad' testing/differential-oracle/` → no matches | all four |
| 2 | integer literals stop at 10⁶ | `int_max: 1_000_000` | Defect #1 needs `9223372036854775807` |
| 3 | strings are alphanumeric, ≤ 50 chars | `string_charset: StringCharset::Alphanumeric` | Defect #4 needs a 4-byte character |
| 4 | its oracle is SQLite, which has no `LPAD` | `SELECT lpad('a',2,'b')` in `sqlite3` → no such function | all four, even if generated |

Each is one command. **Turso's own fuzzer cannot produce either statement, and
its oracle could not adjudicate either answer if it did.** This is the measured
claim the project rests on, and `make reproduce` prints it as step 4.

**Scope.** This is one measurement of one fuzzer. It shows the blind spot is
*measurable*, not that every project has one this wide.

### RQ2 — Does transferring a defect's shape across implementations expose new defects?

**Yes — one defect, by this mechanism, accepted upstream.**

| | |
|---|---|
| Source | `duckdb/duckdb#27846`, closed, labelled `reproduced` |
| Its reproduction | `select LPAD('x', 10, '0'), LPAD('x', 4, '😀'), LPAD('x', 3, '😀ab'), RPAD('x', 3, '😀ab')` |
| Templated to | four integer holes, eight text holes |
| Instantiated at | seed 6948 |
| First observed | `2026-10-28T19:58:43Z`, 8 s into the first campaign |
| Oracle | crash |
| Reduced to | `select LPAD('a', 9223372036854775807, 'b');` (ratio 0.939, 5 executions) |
| Filed | `tursodatabase/turso#11219` |
| Fixed by | our PR `#11225`, **merged** `ec86571642a6`, +138 / −41 |

The fill is not a lucky accident. Instantiating that single template over seeds
0–3999 places `9223372036854775807` into a pad call in **1,054 of them
(26.4%)** — seed 0 included. So the defect is reachable in roughly one attempt
in four from one mined report, against a fuzzer that cannot reach it at all.

Measured end to end by `make reproduce`:

| | exit | elapsed | output |
|---|---|---|---|
| before `b588fbdc6` | **134** (SIGABRT) | 1.11 s | `memory allocation of 9223372036854775806 bytes failed` |
| after `7f0a9e6f4` | 1 | 0.01 s | `Runtime error: string or blob too big (18)` |

### RQ3 — What does each input source contribute, at equal budget?

**Measured.** Three arms, 20,000 cases each, identical seed range (0–19999),
against the pre-fix build `b588fbdc6` so that all four defects are present. The
crash oracle is the only oracle in every arm. Full method, commands and raw
results: [`experiments/README.md`](../experiments/README.md) and
[`experiments/ablation.json`](../experiments/ablation.json).

| arm | source | executions | valid rate | hits | **distinct signatures** |
|---|---|---|---|---|---|
| `variants` | mined templates only | 19,169 | 8.9% | 8 | **2** |
| `fuzzer` | typed generator only | 20,000 | 98.3% | 12 | **3** |
| `both` | either, per case | 19,759 | 71.1% | 15 | **3** |

Which signature each arm reached, and at which seed:

| signature | defect | `variants` | `fuzzer` | `both` |
|---|---|---|---|---|
| `64513113c675` | #1, #2 | 914 | 762 | **101** |
| `40d2c580f251` | #3 | 10180 | 5367 | **5080** |
| `541c97fa5dd9` | #4 | not reached | 6376 | **5429** |

Four things follow, and the first is the one that matters most.

**Every defect this project reported is rediscoverable from a seed.** All four
come back on the pre-fix build with no manual input. That is the strongest
reproducibility claim available here, and it is why the arms were run.

**The generator reaches more than transfer does on this target** — three
signatures against two. The gap is structural rather than a matter of budget:
templating replaces a mined report's literals with typed holes, and the text
fill pool is ASCII-only, so no instantiation can produce the multi-byte fill
defect #4 requires. The generator reaches it through `EDGE_STRINGS`, which
carries real multi-byte values.

**Transfer is more efficient per executed case than its validity rate
suggests.** Only 8.9% of mined cases run at all — most carry DuckDB syntax
Turso rejects — yet that arm still reached two of three signatures. The mined
shapes are concentrated where defects are; they are simply expensive to land.

**Both together is the best configuration**, not in signature count, which ties
at three, but in time to first defect: seed 101 against 762 and 914, and 15
hits against 12 and 8. The two sources fail differently.

One honest limit on all of this: the generator's vocabulary now contains
`lpad`, `rpad` and `repeat`, which were added to it after the first defects had
already been reported. The experiment therefore measures the tool as it now
stands, not as it stood at discovery, and it does not establish which source
would have reached them first at that time.

### RQ4 — What does the oracle cost in false positives?

**Zero false positives, by construction, and the oracle is validated against a
planted defect.**

The crash oracle asks whether a single statement terminated the process. There
is no judgement to get wrong, so there is no dialect difference or type-width
disagreement to misreport — the dominant false-positive source in cross-engine
SQL testing.

That a silent oracle is working is checked rather than assumed:
`tests/test_oracle_planted.py` runs it against `graft/engines/fake.py`, a
deliberately defective engine, and asserts it catches the planted crash, stays
silent on a correct engine, and stays silent on invalid SQL.

**The cost is coverage, not precision.** A wrong answer that does not crash is
invisible. This project reports no wrong-result defects and claims none.

## 3. Results

| | |
|---|---|
| **Defect instances found and corrected** | **4** — `lpad`, `rpad`, `repeat`, multi-byte fill |
| Distinct code paths | 2 — `pad()` (shared by `lpad`/`rpad`) and `exec_repeat()` |
| Upstream issues / pull requests | 2 / 2, **both merged** |
| Independently reproducible from a single statement | **4** |
| Replayable from a committed seed in this repository | 2 cases (seeds 6948, 261377) covering #1–#3 |
| Wrong-result defects found | 0 |
| Tests | **514, all passing** |

`docs/provenance.md` states the defect #4 attribution in full. In short: we
spotted the unit mismatch ourselves while writing the #1–#3 patch and wrote
it into that pull request at 16:54 — but judged it *conservative*, i.e.
harmless. Upstream's automated reviewer corrected the direction 15 minutes later
and supplied a demonstrating input; we then diagnosed it, fixed it and landed
the patch. It is our finding and our fix, arrived at by code review rather than
by the tool, with our misdiagnosis caught by a reviewer. The whole exchange is
public on PR #11225.

### The second finding as a result in its own right

Fixing #1–#3 made #4 visible. #1–#3 were *allocate before
checking*; the fix put the check first; and that check compared a **character**
count against a **byte** limit. The same function, the same class of unchecked
size, in two different units — the second reachable only once the first was
closed.

The evidence is in the failure message. `SELECT length(LPAD('a', 1000000000,
'😀'))` fails with `memory allocation of 3999999996 bytes failed`: that is
10⁹ characters × 4 bytes, minus the one-character input. The arithmetic of the
root cause is visible in the abort.

| | exit | elapsed | output |
|---|---|---|---|
| before `b588fbdc6`, `ulimit -v 6000000` | **134** (SIGABRT) | 2.86 s | `memory allocation of 3999999996 bytes failed` |
| before, uncapped | — | ~34 s | returns the error after an ~11.7 GB peak |
| after `7f0a9e6f4` | 1 | 0.01 s | `Runtime error: string or blob too big (18)` |

That fixing one defect brings an adjacent one into view is a genuine
software-engineering observation, and it is reported as what it is: the
*mechanism* was reading the patched function, not running the tool. The
cautionary half is equally real — we saw the mismatch and misjudged its
direction, and it took an automated reviewer to turn our own observation into a
defect report.

## 4. What the corpus actually yields

From `data/classified/duckdb.jsonl`, 60 issues classified by hand:

| judgement | count |
|---|---|
| plausibly transferable to another engine | **16** |
| DuckDB-specific (nested types, DuckDB-only syntax, optimizer internals) | 44 |

**Most of a mined corpus does not transfer**, and the method does not depend on
a high per-report hit rate. It depends on the corpus being large, free, and
concentrated in awkward corners. 659 reproductions were extracted from 200
issues at the cost of 387 API requests.

## 5. Reproducibility

| Claim | How to check | Needs |
|---|---|---|
| the full chain for defect #1 | `make reproduce` | both Turso builds; steps 1–4 need neither |
| the 26.4% fill rate | `make reproduce`, step 3 | nothing but this repository |
| the four blind-spot limits | `make reproduce`, step 4, then the four commands in §2 | a Turso checkout |
| both before/after behaviours | `make reproduce`, step 5 | both builds |
| the implementation | `make test` → 514 passed | `requirements.txt` |
| the corpus | `data/` is committed | nothing |

The corpus is committed rather than re-mined on demand: re-mining hits the
GitHub API, needs a token, and returns different results as upstream closes more
issues. `make corpus` re-runs it for inspection, and its recorded provenance is
in `data/mined/duckdb/meta.json`.

## 6. Limitations

1. **Four defects in one engine, in one family of functions.** All four are in
   Turso's string functions and three share a root cause. An existence result,
   not a rate, and nothing here shows the method generalises to another engine
   pair.
2. **The ablation is one run per arm**, not repeated trials, so the seed at
   which an arm first reaches a signature is a single sample and not a rate.
3. **Crash-class defects only.** A wrong answer that does not crash is outside
   what this oracle can see.
4. **The structure space is bounded by the corpus.** Templating fixes the call
   structure, so GRAFT cannot explore a shape no upstream report exhibits.
5. **Depends on a rich upstream tracker.** The method needs a second engine with
   many closed, labelled, reproduction-carrying reports. It would work far less
   well where that does not exist.
6. **Most reports do not transfer** — 16 of 60 judged transferable, and that
   judgement was made by hand, by us, and is not independently validated.
7. **The blind-spot measurement is of one fuzzer at one commit.** Turso could
   widen its literal domains tomorrow and limits 2 and 3 would close.
8. **No coverage feedback and no grammar learning.** The sampling bias in
   `configs/weights.yaml` is static and hand-set.
9. **The hypothesis was formed after the first finding, not before it.** The
   blind-spot measurement (§2, RQ1) was made *after* the first defects were discovered
   and filed, as an explanation of why the defect had survived. It is reported
   as a hypothesis that the evidence supports and that RQ3 would test — not as
   the project's original design intent. `docs/provenance.md` gives the dates.

## 7. Threats to validity

**Internal.** The before/after measurements come from two builds of one source
tree on one machine on one day; they are not averaged over repetitions, and
timings will vary with hardware and load. The exit statuses and error strings,
which carry the actual claim, will not.

**External.** One engine, one corpus source, one defect class. Nothing here
shows the method generalises to another engine pair, and §6 items 5 and 6 are
the specific reasons to doubt that it generalises cheaply.

**Construct.** "Blind spot" is operationalised as one configuration struct plus
one `grep` over one tree. That is a sound lower bound on what the fuzzer cannot
express — a generator cannot emit a literal outside its configured domain — but
it is not a proof that no other code path in that project could reach the
statement.

**Reproducibility.** AI assistance (Claude) was used throughout the
implementation, the analysis and the drafting of the upstream reports; it is
disclosed in the body of both upstream issues and in `CONTRIBUTIONS.md`. All
randomness is seeded, all engine revisions are pinned, and the corpus is
committed, so the chain in `make reproduce` is deterministic.
