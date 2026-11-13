# Internal specification

The data contracts the modules share: what an error class is, what a status may
be, what a finding record contains, how findings are grouped, and what a log
line looks like. Several modules cite this document in their docstrings.

It is written down because these are the points where a mistake is invisible
until it corrupts a result. A silently mis-classified error turns a rejected
statement into a reported defect; a status set by the tool where only a human
may set it turns an unreviewed hit into a claim.

---

## 1. Error classes

Every execution is classified into exactly one of six values
(`graft/engines/errors.py`):

| class | meaning | a violation? |
|---|---|---|
| `none` | the statement ran and returned a result | no |
| `user_error` | the engine rejected the statement and said why — bad syntax, unknown table, a documented limit such as "string or blob too big" | **no** |
| `internal_error` | the engine reports a fault in itself rather than in the input | yes |
| `panic` | a Rust panic, including exit code 101 | yes |
| `crash` | death by signal, or a failure to start | yes |
| `timeout` | no answer inside the per-target `timeout_s` | yes |

**The `user_error` boundary is the one that matters.** A fuzzer that counts
rejections as defects reports noise, and the single most common way to do that
accidentally is to read an error *banner* as a fault. `errors.py` therefore
matches the engine's own error vocabulary rather than guessing from exit status
alone, and `tests/test_errors.py` pins 44 cases against it.

## 2. Statuses

A finding's status records how far it has been taken, and **the tool may only
set the first group**:

| set by the tool | meaning |
|---|---|
| `new` | an oracle fired; nothing has been checked |
| `verified` | reproduced on a freshly started engine process |
| `reduced` | shrunk while preserving the same violation |
| `duplicate_suspect` | the upstream search found a plausible existing report |
| `questionable` | reproduced, but we are not convinced it is a defect |
| `flaky` | did not reproduce on re-run |
| `candidate_ready` | passed every check and is ready for a human to read |
| `drafted` | a report has been written for a human to review |

| set by a human only | meaning |
|---|---|
| `filed` · `acknowledged` · `accepted` · `fixed` · `rejected` · `duplicate_confirmed` | the upstream lifecycle |

`graft/triage/upstream.py` is the only writer of the second group, and it writes
them only from an explicit human command. **No code path promotes a finding to
`filed` or beyond.** That separation is why a campaign running unattended for
hours cannot manufacture a result.

## 3. The finding record

One directory per coarse signature under `findings/<target>/<signature>/`,
containing `finding.json`. Built by `graft/triage/finding.py`.

| field | what it holds |
|---|---|
| `id`, `coarse_signature` | identity; see §4 |
| `target`, `engine_version`, `engine_commit`, `configured_commit` | which build produced it |
| `oracle` | which oracle fired |
| `origin`, `origin_issue` | `fuzzer` or a mined report, and the upstream issue it derives from |
| `seed` | **the one field that makes a finding reproducible** |
| `setup`, `original_sql`, `reduced_sql`, `query` | the case, before and after reduction |
| `raw_expected`, `raw_actual`, `normalized_*` | what should have happened and what did |
| `status` | §2 |
| `duplicate_candidates` | upstream reports the search surfaced, with the queries used |
| `verification_history` | every re-run, with commit and timestamp |
| `feature_tags` | §5 |
| `created_at`, `notes`, `caveats`, `context`, `raw_result` | provenance and raw evidence |

Raw output is kept alongside the normalised form. When a comparison turns out to
be wrong, the raw text is what shows whether the engine or the harness was at
fault, and discarding it makes that question unanswerable.

## 4. Grouping: the coarse signature

```
coarse_signature = sha1( target + oracle + error_class + normalised error text )
```

where normalisation replaces literals, numbers and paths with placeholders, so
that the same defect reached by different values lands in one group.

**A signature is a grouping hint, never a duplicate decision.** Two findings
sharing one mean "look at these together"; whether they are one defect is
decided by reading the code. This project's defects #1 and #2 share signature
`64513113c675` — the same `pad()` body reached through `lpad` and `rpad` — and
are nevertheless listed as two defects, because each aborts the engine on its
own. Defect #3 has its own signature because the abort arrives from a different
call site. `findings/README.md` sets out that reasoning.

## 5. Feature tags

Derived from the SQL text by `graft/triage/features.py`, in nine namespaces:

```
fn:  op:  clause:  type:  agg:  join:  cte:  set:  win:
```

They drive the capability gate — the generator refuses to emit a tag the target
has not been probed for — and they let a campaign report what it actually
exercised rather than what it was intended to.

## 6. Logs, and the stats line

Logs are JSON Lines: one object per line, one line per event, under `logs/`.
The format is chosen so a multi-hour run stays greppable and so a partial file
is still parseable.

A campaign emits a stats line on every tick carrying at least: `qps`,
`valid_rate`, `cases`, `skipped`, `executions`, `hits`, `hits_by_oracle`,
`unique_coarse_signatures`, `findings_written`, `restarts`, `errors`,
`last_seed`, `elapsed_s`.

`last_seed` is the resumption point. The seed is the campaign's entire state:
`--seed-start` restores a run exactly, and any single case can be replayed from
its seed alone, which is what makes every defect in `findings/` re-findable
rather than merely recorded.

## 7. Determinism

Every random choice comes from a seeded `random.Random`. Nothing reads the
clock, the process id, or an unseeded global source to make a decision that
affects a generated case. `tests/test_gen.py` asserts this directly by
generating twice from one seed and requiring identical output.

This is not a convenience. A defect that cannot be regenerated is a screenshot;
a defect that can is evidence.
