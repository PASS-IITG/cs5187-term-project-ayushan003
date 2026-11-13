# Contribution statement

**CS 5187 — Software Analysis, Monsoon 2026 · Dept. of CSE, IIT Guwahati**

| Member | Roll number |
|---|---|
| Ayush Singh Chauhan | 240101117 |
| Mehul Raj | 240101125 |

## Anonymity and signing

FSE reviews papers double-anonymously, so an FSE submission would leave out the
authors' names. The course instead requires each member to justify their
contribution through a signed statement in the report and through the commit
history of this repository. The report (`GRAFT-report.pdf`, section *Author
Contributions*) therefore names both authors and carries this statement for
both to sign. That is its only departure from the FSE format.

## How the work was carried out

This project was built in shared working sessions. Both members were present for
the design, the implementation, the campaigns, the triage of every candidate and
the preparation of both upstream patches. We worked from whichever of our two
machines was in use at the time, so a commit's author field records **which
machine a session ran on**, not which of us did the work. Most commits made
from Ayush's account therefore carry a `Co-authored-by` trailer naming Mehul.

Both upstream pull requests were submitted from Ayush's GitHub account, which is
why both show that handle on github.com. The patches themselves were written and
reviewed by both of us.

Neither of us worked on any part of this project alone, and both of us can
explain and defend any part of it.

## Division of leadership

Each area below was *led* by the named member — they drove its design and wrote
the first implementation — and was reviewed, debugged and extended by the other.

### Ayush Singh Chauhan (240101117)

- **Corpus pipeline.** Mining closed, reproduced defects from DuckDB's issue
  tracker, and extracting runnable reproductions from fix tests and issue bodies
  (`src/graft/corpus/github.py`, `fetch.py`, `extract.py`).
- **The transfer mechanism.** Templating a mined reproduction so its structure is
  kept and its literals are regenerated from boundary-weighted pools
  (`src/graft/corpus/templates.py`) — the component the `lpad`, `rpad` and `repeat`
  defects all came through.
- **Engine interface.** Driving `tursodb` as a subprocess with timeouts and
  memory caps, and the error classification that keeps a rejected statement from
  being read as a defect (`src/graft/engines/`), including the interleaved-diagnostic
  fix in `embedded_diagnostic`.
- **Campaign driver.** The seeded, resumable loop and its two case sources
  (`src/graft/campaign.py`).
- **Upstream submission.** Filing issues #11219 and #11260, and handling review
  on both pull requests.

### Mehul Raj (240101125)

- **Capability probing.** Establishing what the target engine actually supports
  by probing the real binary instead of assuming, including the measured results
  that corrected our assumptions (`src/graft/generate/capabilities.py`).
- **The typed SQL generator.** Schemas, data, expressions and queries, gated by
  the probed capability set (`src/graft/generate/`).
- **The oracle and its validation.** The crash oracle, and the planted-bug engine
  that proves it fires on a real defect and stays silent on a correct engine
  (`src/graft/oracle/`, `src/graft/engines/fake.py`, `src/tests/test_oracle_planted.py`).
- **Triage pipeline.** Statement reduction that preserves the violation,
  re-verification on a freshly built current `main`, the upstream duplicate
  search with its queries recorded, and report drafting (`src/graft/triage/`).
- **Evaluation.** The blind-spot measurement against Turso's own fuzzer
  configuration, the before/after capture on the pre-fix and post-fix builds, and
  the reproducibility checks (`src/experiments/`, `src/graft/reproduce.py`).

### Joint

- Choosing the target and the corpus source.
- Root-cause analysis of all four defects, and both patches submitted upstream.
- The test suite (514 tests).
- The report, the final presentation, and this statement.

## Disclosure of AI assistance

AI assistance (Claude) was used throughout this project: in the implementation,
in the analysis of findings, and in drafting the upstream reports and this
repository's documentation. It is disclosed in the body of both upstream issues
filed with the Turso maintainers. Every claim in this repository was checked by
us against the code, the logs or the public upstream record before it was
written down, and both of us understand and can defend the whole of it.

## Signatures

By signing below, each member confirms that the division above is accurate and
that they can explain and defend any part of this project. The same statement,
with space for both signatures, is in the report's *Author Contributions*
section.

| Member | Signature | Date |
|---|---|---|
| Ayush Singh Chauhan (240101117) | | |
| Mehul Raj (240101125) | | |
