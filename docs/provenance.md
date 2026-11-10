# Provenance

What was done, when, by what, and which parts of this repository are a later
reorganisation of it. The purpose of this file is that a reader does not have to
take the rest of the documentation on trust.

---

## 1. How this repository relates to the work

The investigation ran from **28 October to 3 November 2026**. This repository
was **organised on 3 November 2026** from that work: the code, the corpus, the
findings and the measurements are the originals, and the commit history here
records the order in which they were *assembled into a coherent project*, not
the order in which they were originally written.

That distinction is deliberate and is stated for three reasons:

- **No commit here carries a false timestamp.** Nothing is backdated. Every
  commit in this repository is dated when it was made.
- **No commit claims an experiment was finished before it was.** The dates of
  the actual experiments are in §3 below and are independently checkable against
  the upstream issues and pull requests, which are public and timestamped.
- **The predecessor repository retains the original chronology**, including the
  work that was tried and abandoned. GRAFT is the final project, pruned to what
  the findings rest on; it is not a record of everything attempted.

## 2. Which finding was discovered by this tool

**Defects #1–#3 — discovered by GRAFT.** A mined DuckDB reproduction, templated,
re-literalised with boundary values, executed against Turso, caught by the crash
oracle. Seed 6948, first observed `2026-10-28T19:58:43Z`, eight seconds into the
first campaign. The chain is replayable with `make reproduce`.

**Defect #4 — the multi-byte fill.** Flagged by the campaign's generator arm,
then analysed while we were writing the patch for #1–#3. It is reproducible
from seed 6376 against the pre-fix build; see
[`../experiments/README.md`](../experiments/README.md). What the public record
adds, and what is worth stating because it is checkable, is that our first
write-up of it had the defect's direction backwards. Four timestamped steps on
PR #11225:

| Time (UTC) | Who | What |
|---|---|---|
| `2026-11-01 16:54:43` | **us** | the pull-request description for defects #1–#3 states that `pad` "compares a character count against a byte limit, so for multi-byte fill characters it is **conservative by up to 4x**" |
| `17:09:50` | Autofixer | replies under *"Also wrong on main: 1 input this pull request doesn't fix"* that the guard is **permissive**, not conservative, with the input `SELECT length(LPAD('a', 1000000000, '😀'))` |
| `18:09` | **us** | *"you're right, and I've corrected the description. I had the direction backwards."* |
| `2026-11-02 08:27` | **us** | issue #11260 filed, with the root cause and the exact precheck |

So the unit mismatch was written into our pull request fifteen minutes before
the bot commented — but with its direction backwards. "Conservative" would mean
the guard rejects too much, which is harmless; we recorded it as a limitation
worth a reviewer's note rather than as a defect. The automated reviewer
corrected that judgement and supplied an input demonstrating the harm, after
which we analysed the root cause, wrote the exact precheck, and filed it.

**Ours:** the defect, the root-cause analysis, the byte-length precheck that
fixes it without rejecting legal wide fills, the verification, and the merged
patch. **Not ours:** correctly judging the direction at first — a reviewer
caught that, and the repository says so because the exchange is public on
PR #11225.

## 3. Timeline of the actual work

| Date | Event | Checkable against |
|---|---|---|
| 2026-10-28 17:09 UTC | DuckDB corpus mined: 600 candidates → 200 issues → 659 reproductions | `data/mined/duckdb/meta.json` |
| 2026-10-28 19:58:35 UTC | first campaign starts against Turso `730017f96` | campaign log |
| 2026-10-28 19:58:43 UTC | **defect #1 first observed** (8 s in) | the finding record |
| 2026-11-01 | re-verified on a fresh build of `b588fbdc6`; duplicate search run | `findings/bug-01/README.md` |
| 2026-11-01 11:44 UTC | issue #11219 filed | github.com |
| 2026-11-01 16:54 UTC | pull request #11225 opened | github.com |
| 2026-11-01 17:09:50 UTC | **Autofixer surfaces defect #4's input** on #11225 | github.com |
| 2026-11-02 03:10 UTC | **PR #11225 merged** as `ec86571642a6` | github.com |
| 2026-11-02 08:27 UTC | issue #11260 filed | github.com |
| 2026-11-02 08:42 UTC | **PR #11259 merged** as `121cd0a8c80b` | github.com |
| 2026-11-03 | pre-fix binary built at `b588fbdc6`; before/after captured | `docs/evaluation.md` §2 |
| 2026-11-03 | this repository organised and documented | this history |

## 4. The hypothesis came after the first finding

The blind-spot argument in `docs/evaluation.md` §2 (RQ1) — that Turso's own
fuzzer cannot express these statements, with four verifiable limits — was
established **after** defect #1 was discovered and filed, as an explanation of
why a well-fuzzed engine still had the defect.

It is reported throughout as a hypothesis that the evidence supports and that
RQ3 would test, never as the design intent that produced the finding. The honest
sequence was: the transfer mechanism produced a crash; we then asked why nobody
else had found it; reading the incumbent fuzzer's source answered that; and the
answer suggested the general method.

One detail a reviewer should have, because it bounds what the ablation in
`experiments/` can claim: `lpad`, `rpad` and `repeat` were added to the
*generator's* vocabulary **after** the first defects had already been filed. The
ablation therefore measures the tool as it now stands. At discovery time the
generator could not have produced those statements, and the mined templates
could; that is consistent with everything stated here, and it is why the
experiment is reported as a comparison of the two sources today rather than as
a reconstruction of what happened.

## 5. What was pruned, and why it is not here

The predecessor repository contained substantial work that is absent from GRAFT
because it did not contribute to either finding: a variant-mutation and
cross-engine-translation stage; metamorphic oracles (ternary logic partitioning,
non-optimising reference engine construction, an index-invariance oracle);
persistence and crash-recovery probes; a deep data profile; and adapters for
engines GRAFT does not test.

Those were real experiments with real measurements, and most produced **nothing**
— which is itself a result, reported in the predecessor's records rather than
restated here. They are excluded from GRAFT under one rule: *nothing stays unless
it contributes to discovering, explaining, reproducing, fixing or evaluating the
two reported defects.*

A third defect was also filed during the investigation, in Apache DataFusion
(issue #27001, our PR #27002). It is **open, unreviewed**, was not found by the
mechanism this project is about, and is not claimed as a result here.

## 6. Tooling and assistance

AI assistance (Claude) was used throughout: in the implementation, in the
analysis, and in drafting the upstream reports. It is disclosed in the body of
both upstream issues, and in `CONTRIBUTIONS.md`.

The work was carried out jointly by both authors in shared sessions, on
whichever machine was in use at the time. A commit's author field therefore
identifies *a machine*, not an individual's contribution, and commits carry a
`Co-authored-by` trailer for that reason. `CONTRIBUTIONS.md` gives the division
of leadership, names both members and their roll numbers, and is signed by both.

## 7. What a sceptical reader should check first

In rough order of how much they constrain the claims:

1. **The two merged pull requests.** `tursodatabase/turso#11225` and `#11259`,
   with merge commits `ec86571642a6` and `121cd0a8c80b`. Public.
2. **The Autofixer comment on #11225**, dated `2026-11-01T17:09:50Z`, which
   establishes §2's attribution for defect #4. Public.
3. **`make reproduce`.** Deterministic; prints the source report, the template,
   the seeded fill, the 26.4% rate, the four limits, and the before/after
   behaviour on two builds.
4. **The four blind-spot commands** in `docs/evaluation.md` §2, against a Turso
   checkout.
5. **`make test`** — 514 tests.
6. **`data/mined/duckdb/meta.json`**, which records the mining run's own
   counters rather than our description of them.
