# Experiments

## Transfer versus generation, at equal budget

GRAFT draws its test cases from two sources — mined bug-report templates and a
typed generator — and the campaign driver can run either alone or both
together. This experiment measures what each is worth.

Results: [`ablation.json`](ablation.json).

### Method

Three arms, one per source, against the **pre-fix** build of Turso
(`b588fbdc6`, containing none of our patches) so that all four defects are
present and findable. Every arm gets the same case budget and the same seed
range, so the comparison is like for like. The crash oracle is the only oracle
in either arm.

```bash
export GRAFT_TURSO_BIN=~/targets/turso-prefix/target/release/tursodb

python -m graft.campaign --target turso --mode variants --max-cases 20000 --seed-start 0
python -m graft.campaign --target turso --mode fuzzer   --max-cases 20000 --seed-start 0
python -m graft.campaign --target turso --mode both     --max-cases 20000 --seed-start 0
```

Each run writes its hits under `findings/turso/<signature>/`. Distinct
`coarse_signature` values are the unit of comparison: eight hits on one
signature is one defect found eight times, not eight defects.

### Results

| arm | source | executions | valid rate | hits | **distinct signatures** |
|---|---|---|---|---|---|
| `variants` | mined templates only | 19,169 | 8.9% | 8 | **2** |
| `fuzzer` | typed generator only | 20,000 | 98.3% | 12 | **3** |
| `both` | either, per case | 19,759 | 71.1% | 15 | **3** |

Which signature each arm reached, and at which seed:

| signature | defect | `variants` | `fuzzer` | `both` |
|---|---|---|---|---|
| `64513113c675` | #1, #2 — `lpad`/`rpad` allocate before validating (first hit: `rpad` in every arm) | seed 914 | seed 762 | **seed 101** |
| `40d2c580f251` | #3 — `repeat` size computation overflows | seed 10180 | seed 5367 | **seed 5080** |
| `541c97fa5dd9` | #4 — character count checked against a byte limit | not reached | seed 6376 | **seed 5429** |

### What this shows

**All four defects are reachable from a seed.** Every defect this project
reported upstream is rediscovered by the tool on the pre-fix build, from a
recorded seed, with no manual input. That is the strongest reproducibility claim
available and it is what the three arms were run to establish.

**The generator reaches more than transfer does, on this target.** Three
signatures against two. Mined templates never reached defect #4, and the reason
is structural rather than a matter of budget: templating replaces a mined
report's literals with typed holes, and the fill pool for text holes is
ASCII-only, so no instantiation can produce the multi-byte fill that defect
requires. The generator reaches it through `EDGE_STRINGS`, which carries real
multi-byte values.

**Transfer is far more efficient per executed case than its validity rate
suggests.** Only 8.9% of mined cases run at all — most carry DuckDB syntax
Turso rejects — yet that arm still reached two of the three signatures. The
mined shapes are concentrated where defects are; they are simply expensive to
land.

**Running both is better than either alone.** Not in signature count, which
ties with the generator at three, but in time to first defect: seed 101 against
762 and 914, and 15 hits against 12 and 8. The two sources fail differently, so
drawing from both shortens the path to the first one.

### What this does not show

The generator's vocabulary **now** contains `lpad`, `rpad` and `repeat`. They
were added to it after the first defects had already been reported, so this
experiment measures the tool as it currently stands, not as it stood when those
defects were first seen. It does not establish which source would have found
them first at that time, and no claim of that kind is made anywhere in this
repository.

The arms are one run each, not repeated trials, so the seed at which a given
arm first reaches a signature is a single sample and should not be read as a
rate.
