# Provenance

What was done, when, by what, and which parts of this repository are a later
reorganisation of it. The purpose of this file is that a reader does not have to
take the rest of the documentation on trust.

---

## 1. How this repository relates to the work

The investigation ran from **28 October to 10 November 2026**. The code, the
corpus, the findings and the measurements in this repository are the originals.
Its commit history records the order in which they were **assembled into a
coherent project**, which is not the order in which every line was first
written — an early commit may carry code that was drafted, used and revised
before it was committed here in its finished form.

That distinction is stated rather than glossed, for two reasons:

- **No commit carries a false timestamp.** Nothing is backdated. Every commit is
  dated when it was made.
- **No commit claims an experiment finished before it did.** The dates of the
  actual work are in §3 below, and the ones that matter are independently
  checkable against the upstream issues and pull requests, which are public and
  timestamped.

## 2. What the tool found, and what it got wrong

**Defects #1–#3 — discovered by GRAFT.** A mined DuckDB reproduction, templated,
re-literalised with boundary values, executed against Turso, caught by the crash
oracle. First observed `2026-10-28T19:58:43Z`, eight seconds into the first
campaign. `make reproduce` replays the whole chain, and
[`../findings/README.md`](../findings/README.md) gives the seeds that regenerate
each defect in a single case against a pre-fix build.

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
| 2026-11-01 | re-verified on a fresh build of `b588fbdc6`; duplicate search run | `findings/defect-01-lpad/README.md` |
| 2026-11-01 11:44 UTC | issue #11219 filed | github.com |
| 2026-11-01 16:54 UTC | pull request #11225 opened | github.com |
| 2026-11-01 17:09:50 UTC | **Autofixer surfaces defect #4's input** on #11225 | github.com |
| 2026-11-02 03:10 UTC | **PR #11225 merged** as `ec86571642a6` | github.com |
| 2026-11-02 08:27 UTC | issue #11260 filed | github.com |
| 2026-11-02 08:42 UTC | **PR #11259 merged** as `121cd0a8c80b` | github.com |
| 2026-11-03 | pre-fix binary built at `b588fbdc6`; before/after captured | `docs/evaluation.md` §2 |
| 2026-11-03 | repository assembled and documented | this history |
| 2026-11-10 | transfer-versus-generation ablation run; all four defects rediscovered from seeds | `experiments/ablation.json` |

## 4. The hypothesis came after the first finding

The blind-spot argument in `docs/evaluation.md` §2 (RQ1) — that Turso's own
fuzzer cannot express these statements, with four verifiable limits — was
established **after** defect #1 was discovered and filed, as an explanation of
why a well-fuzzed engine still had the defect.

It is reported throughout as a hypothesis the evidence supports, never as the
design intent that produced the result. The honest sequence was: the transfer
mechanism produced a crash; we then asked why nobody else had found it; reading
the incumbent fuzzer's source answered that; and the answer suggested the
general method.

One detail a reviewer should have, because it bounds what the ablation in
`experiments/` can claim: `lpad`, `rpad` and `repeat` were added to the
*generator's* vocabulary **after** the first defects had already been filed. The
ablation therefore measures the tool as it now stands. At discovery time the
generator could not have produced those statements, and the mined templates
could; that is consistent with everything stated here, and it is why the
experiment is reported as a comparison of the two sources today rather than as
a reconstruction of what happened.

## 5. What is not claimed here

**A third defect was filed during the investigation**, in Apache DataFusion:
issue [#27001](https://github.com/apache/datafusion/issues/27001) with our pull
request [#27002](https://github.com/apache/datafusion/pull/27002). It is
**open and unreviewed**, it is not in Turso, and it is not claimed as a result
of this project. It is mentioned so that a reader who finds it under the same
GitHub account knows why it is absent from the four.

**Other approaches were tried during the investigation and are not in this
repository** — among them metamorphic oracles and several probe harnesses.
They produced no defect that was filed. They are not described further here
because their code and measurements are not in this repository, and an
unverifiable claim is worth less than no claim.

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
2. **The Autofixer comment on #11225**, dated `2026-11-01T17:09:50Z`, and our
   reply at `18:09Z`, which together show that we had defect #4's direction
   backwards and that a reviewer corrected it. Public, and §2 says the same.
3. **`make reproduce`.** Deterministic; prints the source report, the template,
   the seeded fill, the 26.4% rate, the four limits, and the before/after
   behaviour on two builds.
4. **The four blind-spot commands** in `docs/evaluation.md` §2, against a Turso
   checkout.
5. **The seeds in [`../findings/README.md`](../findings/README.md)**, each of
   which regenerates its defect in one case against a pre-fix build.
6. **`make test`** — 514 tests.
7. **`data/mined/duckdb/meta.json`**, which records the mining run's own
   counters rather than our description of them.
