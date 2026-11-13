# Experiments

Three experiments, each with its raw output committed next to this file. They
back Table 2 of the report (`GRAFT-report.pdf`, Section 5.6).

| File | What it holds |
|---|---|
| [`ablation.json`](ablation.json) | transfer vs. generation, equal budget, on the pre-fix build |
| [`ablation-patch1.json`](ablation-patch1.json) | the same three arms on the build with only our first patch |
| [`defect4-reach.json`](defect4-reach.json) | whether any generated case can reach defect #4, from [`defect4_reach.py`](defect4_reach.py) |

## 1. Transfer versus generation, at equal budget

GRAFT draws its test cases from two sources — mined bug-report templates and a
typed generator — and the campaign driver can run either alone or both
together. This experiment measures what each is worth.

### Method

Three arms, one per source, against the **pre-fix** build of Turso
(`b588fbdc6`, containing none of our patches). Every arm gets the same case
budget and the same seed range, so the comparison is like for like. The crash
oracle is the only oracle in either arm.

```bash
export GRAFT_TURSO_BIN=~/targets/turso-prefix/target/release/tursodb

python -m graft.campaign --target turso --mode variants --max-cases 20000 --seed-start 0
python -m graft.campaign --target turso --mode fuzzer   --max-cases 20000 --seed-start 0
python -m graft.campaign --target turso --mode both     --max-cases 20000 --seed-start 0
```

Each run writes its hits under `findings/turso/<signature>/`. Distinct
`coarse_signature` values are counted, but a signature is a hash of the error,
not a defect: one cause can have two signatures and two causes can share one.
Which defect a signature is was decided by running its statement on the build
with only our first patch (experiment 2).

### Results

| arm | source | executions | valid rate | hits | distinct signatures |
|---|---|---|---|---|---|
| `variants` | mined templates only | 19,169 | 8.9% | 8 | 2 |
| `fuzzer` | typed generator only | 20,000 | 98.3% | 12 | 3 |
| `both` | either, per case | 19,759 | 71.1% | 15 | 3 |

Which signature each arm reached, and at which seed:

| signature | defect | `variants` | `fuzzer` | `both` |
|---|---|---|---|---|
| `64513113c675` | #1, #2 — `lpad`/`rpad` allocate before validating (first hit: `rpad` in every arm) | seed 914 | seed 762 | **seed 101** |
| `40d2c580f251` | #3 — `repeat` size computation overflows | seed 10180 | seed 5367 | **seed 5080** |
| `541c97fa5dd9` | #1 again — `lpad` with a length above 10⁹ computed from table data | not reached | seed 6376 | seed 5429 |

### What this shows

**Both sources reach both root causes of defects #1–#3** — the shared `pad()`
body and `repeat`. The generator reaches one more signature, but it is defect #1
again, not a new defect.

**No arm reaches defect #4.** Experiments 2 and 3 show why.

**Transfer is more efficient per executed case than its validity rate
suggests.** Only 8.9% of mined cases run at all — most carry DuckDB syntax Turso
rejects — yet that arm still reached both root causes. The mined shapes are
concentrated where defects are; they are simply expensive to land.

**Running both is better than either alone** in time to first defect: seed 101
against 762 and 914, and 15 hits against 12 and 8.

### What this does not show

The generator's vocabulary **now** contains `lpad`, `rpad` and `repeat`. They
were added to it after the first defects had already been reported, so this
experiment measures the tool as it currently stands, not as it stood when those
defects were first seen. It does not establish which source would have found
them first at that time.

The arms are one run each, not repeated trials, so the seed at which a given
arm first reaches a signature is a single sample and should not be read as a
rate.

## 2. The same arms on the build with only our first patch

`ec86571642a6` is the merge of PR #11225: defects #1–#3 are fixed and defect #4
is still present (it aborts on `SELECT length(LPAD('a', 1000000000, '😀'))`).
Any hit here would be defect #4.

```bash
git -C ~/targets/turso worktree add ~/targets/turso-patch1 ec86571642a6
cd ~/targets/turso-patch1 && cargo build --release --bin tursodb
export GRAFT_TURSO_BIN=~/targets/turso-patch1/target/release/tursodb
# then the same three commands as in experiment 1
```

| arm | executions | hits |
|---|---|---|
| `variants` | 19,169 | 0 |
| `fuzzer` | 20,000 | 0 |
| `both` | 19,759 | 0 |

Every hit in experiment 1 disappears once defects #1–#3 are fixed. The seeds of
`541c97fa5dd9` (6376, 5429) return `string or blob too big (18)` on this build.
The three arms ran in parallel here, so their timings are not comparable with
experiment 1.

## 3. Can any generated case reach defect #4?

Defect #4 needs a pad length of at most 10⁹ characters, so the first patch's
character check passes, whose result is more than 10⁹ bytes. With a fill of at
most four bytes per character, that is a length between 2.5 × 10⁸ and 10⁹.

```bash
python -m experiments.defect4_reach 2>/dev/null > experiments/defect4-reach.json
```

Over seeds 0–19999 in each of the three modes, **0 of 60,000 cases** contain an
integer literal in that range. The integer pools hold the limits of machine
integers and jump from 32,767 to 2,147,483,647, so they never produce a length
near the engine's own limit. The mined templates are further blocked because
their string fills are ASCII. Adding each engine's own limits to the pools is
future work.
