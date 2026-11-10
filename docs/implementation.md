# Implementation notes

Details a reader needs in order to work on the code, or to judge whether the
claims in `docs/evaluation.md` are supported by it. Only components that exist
in this repository are described.

---

## Running things

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

make reproduce      # the headline result, end to end
make test           # 514 tests
make campaign       # a bounded campaign against Turso
make probe          # re-probe Turso's capabilities
make corpus         # re-mine the DuckDB corpus (needs GITHUB_TOKEN)
```

Nothing needs installing as a package: `graft/` is a plain package at the
repository root, so `python -m graft.reproduce` works from a fresh clone.

Engine binaries resolve from `configs/targets.yaml`, overridable per session:

| variable | what it points at |
|---|---|
| `GRAFT_TURSO_BIN` | the post-fix `tursodb` ("after") |
| `GRAFT_TURSO_PREFIX_BIN` | the pre-fix `tursodb` at `b588fbdc6` ("before") |
| `GRAFT_CONFIG_DIR` | an alternative `configs/` directory |
| `GRAFT_LOG_DIR` | where JSONL logs are written |
| `GRAFT_CACHE_DIR` | the corpus miner's HTTP cache |
| `GITHUB_TOKEN` | read-only GitHub API access, for `make corpus` only |

`GITHUB_TOKEN` is read from the environment and never written anywhere. It is
not needed to reproduce anything: the corpus is committed.

## Building the two engine binaries

The "after" build is an ordinary checkout of current `main`. The "before" build
must not overwrite it, so use a separate worktree with its own `target/`:

```bash
git -C ~/targets/turso worktree add ~/targets/turso-prefix b588fbdc6
cd ~/targets/turso-prefix && cargo build --release --bin tursodb
export GRAFT_TURSO_PREFIX_BIN=~/targets/turso-prefix/target/release/tursodb
```

`make reproduce` skips step 5 with these instructions when either binary is
missing, rather than reporting a result it did not obtain.

## Module map

| Path | Notes |
|---|---|
| `graft/config.py` | loads `configs/targets.yaml`, expands `~`, applies env overrides. Raises `ConfigError` on a target missing required keys rather than defaulting silently. |
| `graft/log.py` | JSONL only, one object per line, to `logs/`. Never more than one line per event so logs stay greppable. |
| `graft/proc.py` | subprocess execution with a timeout and a memory cap; returns the signal, which is what the oracle needs. |
| `graft/engines/base.py` | the `Engine` interface, `Result`, `TestCase`. `run_case` replays the whole setup for each execution, so no state leaks between cases. |
| `graft/engines/turso.py` | the CLI adapter. See `embedded_diagnostic` below. |
| `graft/engines/errors.py` | maps engine output to an error class: `none`, `user_error`, `internal_error`, `panic`, `crash`, `timeout`. The oracle fires only on the last four. |
| `graft/engines/sqlite.py` | stdlib `sqlite3`. **Test infrastructure only** — the backing store for the planted-bug engine. Not a reference oracle; neither finding used it. |
| `graft/engines/fake.py` | `PlantedBugEngine`: an engine with deliberate defects, used to prove the oracle fires. |
| `graft/corpus/github.py` | cached, rate-limit-aware, read-only GitHub client. |
| `graft/corpus/fetch.py` | closed issues carrying the target repository's confirmed-bug label. |
| `graft/corpus/extract.py` | pulls runnable SQL from fix tests and issue bodies. |
| `graft/corpus/classify.py` | labels a subset by defect class and transferability. |
| `graft/corpus/templates.py` | **the transfer mechanism.** `templatize` replaces literals with typed holes; `instantiate` fills them from boundary-weighted pools; `load_templates` reads the corpus and applies `configs/weights.yaml`. |
| `graft/generate/capabilities.py` | probes the real binary; writes `configs/capabilities.yaml`. |
| `graft/generate/{schema,data,expr,query}.py` | the typed generator: schema, rows, expressions, queries. The comparison arm. |
| `graft/oracle/crash.py` | the only oracle. ~145 lines. |
| `graft/triage/reduce.py` | shrink while preserving the violation; `sqlglot` where it parses, text where it does not. |
| `graft/triage/verify.py` | re-run on a fresh process, then on current `main`. |
| `graft/triage/dupecheck.py` | upstream duplicate search; **records the queries it ran** into the finding. |
| `graft/triage/report.py` | drafts a report for a human to read and file. |
| `graft/triage/upstream.py` | the upstream ledger. Cannot set a human-only state such as `merged` itself. |
| `graft/triage/finding.py` | builds the finding record. |
| `graft/campaign.py` | the seeded, resumable loop; `--mode {variants,fuzzer,both}`. |
| `graft/reproduce.py` | the five-step replay. |

## Three implementation details worth knowing

### `embedded_diagnostic` — a diagnostic can arrive anywhere

Turso's CLI writes errors to **stdout, interleaved with result rows**. An early
version of the adapter checked only the first non-empty line of an output block
to decide whether the block was a diagnostic. A query that emitted two rows and
*then* failed therefore came back as three rows of data, the third being the
error text.

`embedded_diagnostic` scans the whole block and returns a failure if an error
line appears anywhere in it. Covered by
`tests/test_adapters.py::test_an_error_after_rows_is_found_anywhere_in_the_block`.

### The seed is the state

`random.Random(seed)` drives every choice. The last seed is written to
`workdir/<target>.seed` on each statistics tick, so `--seed-start` resumes a
campaign exactly. Any case can be replayed from its seed alone, which is what
makes `make reproduce` deterministic across machines.

### `workdir/STOP`

A campaign checks for `workdir/STOP` between cases and exits cleanly if it
exists, preserving its seed. This is how a long run is stopped without losing
its resume point, and it is also a safety catch: if the file is present, a
campaign refuses to start.

## Test suite

**514 tests, all passing**, in ~31 s. Highlights:

| File | Covers |
|---|---|
| `test_adapters.py` | engine lifecycle, error classification, timeouts, recovery after a crash, the interleaved-diagnostic case |
| `test_gen.py` | the generator: schemas, data, expressions, queries, determinism across seeds, capability gating |
| `test_oracle_planted.py` | the crash oracle fires on a planted defect and is silent on a correct engine |
| `test_miner.py`, `test_extract.py`, `test_classify.py` | the corpus pipeline, plus schema validity of the committed data |
| `test_triage.py`, `test_report.py` | reduction, verification, duplicate search, report drafting |
| `test_fuzz_runner.py` | campaign modes, the statistics line, resumption |
| `test_capabilities.py` | the probed profiles, including measured facts that contradicted our assumptions |

Determinism is asserted, not assumed: several tests generate with a fixed seed
twice and require identical output.
