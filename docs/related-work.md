# Related work

This chapter states, for each body of work, what it does and where GRAFT sits
relative to it. Two conventions, so the reader can calibrate:

- **Access is marked.** Papers read in full are marked *(full text)*; papers
  read at abstract and publisher-page depth are marked *(abstract)*. Nothing is
  summarised beyond the depth it was read at, and no citation is included that
  was not retrieved.
- **Influence is marked.** A paper is marked **[design influence]** only where
  it changed a decision in this repository, with the decision named. The rest are
  positioning: honest context, not claimed inspiration.

---

## 1. Structure-aware input generation

### Grammar-based Whitebox Fuzzing — PLDI 2008 *(abstract)*

Godefroid, Kiezun, Levin. Byte-level symbolic execution wastes its search on
inputs the lexer rejects. Lifting constraints to the *token* level lets a
grammar-based solver turn a partial constraint set into a valid input and prune
the entire subtree of non-parsable completions. On Internet Explorer 7's
JavaScript interpreter, coverage of the code-generation module rose from 53% to
81% using three times fewer tests.

This is the origin of the premise that structured targets need structurally
valid inputs. GRAFT implements no symbolic execution; it is cited for the
premise only.

### Efficiently Detecting DBMS Bugs through Bottom-up Syntax-based SQL Generation — NDSS 2026 *(full text)*

Liang and Liu. The closest work to this project, and its most useful comparison.
Every existing syntax-based SQL generator is *top-down*: it starts at the
grammar root and expands downward, which concentrates effort on shallow grammar
near the root and neglects the feature-rich rules deeper in the space. SQLBull
instead begins at a chosen interesting rule and **backtracks to the root**,
building a syntax path guaranteed to contain that feature, then merges several
such paths into one query. It found **63 unique zero-day bugs** across MySQL,
MariaDB, CockroachDB, DuckDB and PostgreSQL — 15 crashes, 25 internal errors and
23 assertion failures, of which 34 were fixed.

Two things follow for GRAFT.

**[design influence] — the oracle.** SQLBull's 63 findings are all crashes,
internal errors and assertion failures. It uses no differential or metamorphic
oracle. That is direct evidence that a crash-class oracle is sufficient to find
real DBMS defects, and it is why GRAFT ships exactly one oracle
(`graft/oracle/crash.py`) rather than carrying a metamorphic suite it does not
need.

**The delta.** SQLBull aims testing effort at grammar rules that are *deep and
feature-rich* — a property of the grammar. GRAFT aims at constructs and literal
values that a *specific incumbent fuzzer* demonstrably cannot express — a
property of the competition. The two are complementary and the second is, as far
as we found, not otherwise studied. SQLBull's authors also note a limitation
that applies to any rule-driven approach: starting points must be chosen by the
tester, and coverage of the full grammar is not guaranteed. GRAFT's starting
points are chosen for it, by whoever filed the upstream bug report.

### Directed Grammar-Based Test Generation — TSE / FSE 2026 *(full text)*

Kirschner and Soremekun. Grammar-based generators produce valid inputs
*randomly*, so they are unlikely to hit any particular testing goal. FDLoop
learns a mapping between input structures and a goal from test feedback,
combining evolutionary testing with probabilistic-grammar learning, and samples
from the learned distribution. Across JSON, CSS and JavaScript and 20 programs it
beats five baselines in 86% of settings and is up to 2× as effective as EvoGFuzz
at inducing erroneous behaviour; an ablation shows the input mutator, grammar
mutator and feedback each contribute.

**Relationship, stated precisely.** `configs/weights.yaml` biases sampling
toward templates derived from sources that have produced findings before. That is
a hand-set, one-step version of FDLoop's idea. It is **not** grammar learning:
the weights are static and written by us, there is no feedback loop, and nothing
is learned at run time. FDLoop is the principled form of a thing GRAFT does
crudely, and is cited to make that limitation explicit rather than to claim the
technique.

### Fuzzilli — NDSS 2023 *(abstract)*

Groß, Koch, Bernhard, Holz, Johns. JIT compilers need semantically valid
programs, not merely syntactically valid ones. FuzzIL is an intermediate
language whose programs are lists of instructions; mutations apply to the IL and
a lifter emits JavaScript, so every corpus sample stays semantically valid and a
mutation rarely invalidates it. 17 confirmed vulnerabilities over six months.

**[design influence] — what GRAFT mutates.** Fuzzilli's argument is that you
should mutate a representation in which validity is preserved by construction.
GRAFT takes the cheap version of that: it replaces **only literals** with typed
holes and leaves the mined query's call structure untouched, so every
instantiation is valid without a repair step or a lifter. The limitation is
recorded in `docs/design.md`: GRAFT cannot explore structures that no upstream
report exhibits.

### Repair-Driven Greybox Fuzzing — ISSTA 2026 *(abstract)*

Bendrissou, Donaldson, Cadar. The alternative answer to the same validity
problem: mutate freely at the byte level, then *repair* the result back to valid
syntax with an error-recovery algorithm (CPCT+), on top of AFL++. Across six
targets in Lua, PHP, JavaScript and Ruby it found 13 previously-unknown bugs, 9
of which AFL++, Grammarinator and Nautilus did not.

Cited as the road not taken. GRAFT avoids invalidity rather than repairing it;
no repair algorithm is implemented.

### No Grammar, No Problem — NDSS 2023 *(abstract)*

Bulekov, Das, Hajnoczi, Egele. FuzzNG fuzzes Linux system calls without
Syzkaller's hand-written per-call descriptions, reshaping the input space using
the kernel's own syscall-handling APIs. Configs under 1.7% the size of
Syzkaller's grammars reached ~102.5% of its coverage and found 9 new bugs — **5
of them in components Syzkaller had already fuzzed for years.**

The best available precedent for this project's situation. It establishes that
"a mature fuzzer has had this target for a long time" is a reason to look for
that fuzzer's structural blind spot, not a reason to pick a different target.

---

## 2. Learning from known defects

GRAFT's research question comes from this literature, and its instrument does
not. Everything below is static analysis of source or binaries; GRAFT is dynamic
testing. The question is shared; the method is not.

### VulPA — FSE 2025 *(abstract)*

Cao et al. Vulnerabilities that recur with the same root cause but differ
syntactically escape clone detectors. VulPA expresses a defect as a rule in a
Vulnerability Pattern Description Language and detects violations with
multi-object typestate analysis on the IFDS framework. On 26 Java applications,
with rules from 34 CVEs: 90 new vulnerabilities at a 23.7% false-positive rate,
against 13 found collectively by seven existing tools.

**The clearest statement of where GRAFT sits.** VulPA turns one patch into a
*rule* and searches statically for code that violates it, producing warnings
that need triage. GRAFT turns one patch's *reproducing query* into a template and
searches dynamically for an engine that mishandles it, producing a witness that
either aborts the process or does not. Static rule with a false-positive rate,
versus dynamic witness without one. GRAFT implements no typestate analysis.

### Accurate and Efficient Recurring Vulnerability Detection for IoT Firmware — CCS 2024 *(abstract)*

Xiao, Zhang, Shen, Lin, Zhang, Liu, Yang. FirmRec builds vulnerability
signatures from semantic features of the *exploitation procedure* rather than
from code syntax, extracted by concolic execution from a vulnerability report,
and detects recurrences in a two-stage pipeline. 642 vulnerabilities across 320
firmware images; 53 CVEs assigned.

The closest structural analogue to GRAFT's mechanism, and prior art for its key
assumption: **a known defect's signature is its triggering behaviour, not its
code.** FirmRec derives that signature by concolic execution; GRAFT takes it
from the maintainer's own reproduction, which is far cheaper and, for this
setting, sufficient.

### Recurring Vulnerability Detection: How Far Are We? — ISSTA 2025 *(abstract)*

Cao, Chen et al. A dataset of 4,569 recurring vulnerabilities, a systematic
evaluation of existing detectors with an analysis of why they produce false
positives and negatives, and a tool, AntMan, reporting 4,593 detections, 307
developer-confirmed, and 73 new zero-days across 15 projects.

**This paper marks the boundary of GRAFT's claim, which is why it matters.** Its
scope is recurrence **through code reuse** — a vulnerability inherited by
copying. Turso and DuckDB share no code; they share a *specification*. GRAFT's
setting is therefore a different and less-studied form of recurrence, and this
citation is what allows that to be said precisely rather than vaguely.

*(Note: the course page lists this as FSE 2025. The DOI resolves inside
Proc. ACM Softw. Eng. vol. 2, but in the ISSTA issue. Cited here as ISSTA 2025.)*

### Similar but Patched Code Considered Harmful — ICSE 2025 *(abstract)*

Tan, Zhou, Hu, Pan, Liu, Xia. Clone-based detectors flag code that *resembles*
known-vulnerable code but has already been fixed — "similar but patched" code —
producing many false alarms. Their Fixed Vulnerability Filter post-processes a
detector's output, using code change history to decide whether a flagged
snippet is already patched.

**[design influence] — the triage gate.** This is the exact failure mode a
transfer-based method invites: a mined report describes a defect that the second
engine may already handle correctly, or that upstream fixed last week. GRAFT's
pipeline answers it in two places, and we hit the problem in practice before
reading this paper rather than after. `graft/triage/dupecheck.py` searches the
upstream tracker and records the queries it ran, and `graft/triage/verify.py`
re-runs every candidate on a freshly built current `main` before anything is
filed. One early candidate was withheld on exactly these grounds: an upstream
pull request touching that code had merged after our pinned build, so it was
marked a duplicate suspect and never reported.

### Mystique — FSE 2025 *(abstract)*

Wu, Wang, Cao, Chen. Porting a vulnerability patch across branches fails when
code has diverged. Mystique isolates the semantically related statements while
keeping them syntactically valid, producing signatures for the patched and
vulnerable functions, then uses a fine-tuned LLM to generate and iteratively
refine the fixed function. Reported 0.954 function-level and 0.924 CVE-level
success.

Cited for a duality worth one sentence: Mystique ports a *fix* across branches,
GRAFT ports a *failing input* across engines. GRAFT calls no language model and
makes no LLM claim.

### Accurate Identification of the Vulnerability-Introducing Commit — NDSS 2026 *(abstract)*

Guo and He. VicDiff classifies a fix into patching patterns, extracts a
vulnerability-critical statement sequence, and matches it against earlier
commits to locate the introducing commit. Reported 94.94% accuracy and 86.92%
recall over 6,920 CVEs and 5.9M commits.

Cited for its patching-pattern idea, which is the closest published analogue to
what `graft/corpus/classify.py` does when it labels a mined fix so the
reproduction it ships can be reused. GRAFT does no commit archaeology.

---

## 3. Summary of the position

| | Unit of reuse | Instrument | Needs a reference implementation? |
|---|---|---|---|
| SQLBull, FDLoop, Fuzzilli, RepairFuzz | the grammar | generation | no |
| VulPA, FirmRec, AntMan | the known defect | static analysis | no |
| Differential SQL testing (incl. Turso's own) | — | generation + comparison | **yes** |
| **GRAFT** | **the known defect** | **generation** | **no** |

GRAFT occupies the empty cell: it takes the *unit of reuse* from the
recurring-defect literature and the *instrument* from the grammar-fuzzing
literature. The practical consequence is the one that mattered here — because it
needs no reference implementation, it can test a function that the reference
engine does not have.

One caveat belongs in this table rather than after it. GRAFT carries a typed
generator alongside the transfer path, and measured at equal budget on this
target the generator reaches *more* defect signatures than the transfer path
does (`experiments/README.md`). So the empty cell is where GRAFT's **novelty**
sits, not where all of its results came from. Occupying it is what let the
project test `lpad` at all — no differential oracle can, because SQLite has no
`lpad` to compare against — but the comparison between the two sources is
reported as it came out, not as the positioning would prefer.
