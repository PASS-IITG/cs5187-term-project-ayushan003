# GapHarn — Project Goals
## CS 5187 · Software Analysis · IIT Guwahati · Monsoon 2026
## Topic A1: Automated Fuzz-Harness Generation for C Libraries

---

## NORTH STAR GOAL

> **Automatically generate fuzz harnesses for the API functions that existing harnesses never test, and use them to find and report at least one real, previously unknown bug in libarchive — accepted by upstream maintainers before November 13, 2026.**

---

## TIER 1 — MUST ACHIEVE (Minimum for passing)

| # | Goal | Done when | Deadline |
|---|------|-----------|----------|
| G1 | Build libarchive with ASAN + coverage instrumentation | `libarchive.a` exists in `build-asan/` and `build-cov/` | Oct 15 |
| G2 | Measure the API gap | `gap.json` exists with real numbers: api_total, reached_total, gap_total | Oct 15 |
| G3 | Build the API model | `api.json` exists with function signatures, roles, producer→consumer edges | Oct 15 |
| G4 | At least one harness compiles and runs | `./harness_write` runs for 30 seconds without crashing on valid seeds | Oct 15 |
| G5 | G1 rule-based generator works | `make gen` produces ≥ 10 harness `.c` files | Oct 18 |
| G6 | Oracle pipeline works | `oracle_log.csv` populated; ≥ 1 harness passes O1–O4 | Oct 18 |
| G7 | Fuzzing campaign starts | AFL++ running on ≥ 1 lab PC with ≥ 1 harness | Oct 20 |
| G8 | Paper exists | All sections drafted (even if rough) in `paper/sections/` | Nov 7 |
| G9 | Repository is clean | `make repro LIB=libarchive` succeeds on clean checkout | Nov 12 |
| G10 | Submission complete | PDF + GitHub link + Overleaf link submitted via Teams | Nov 13 23:59 IST |

---

## TIER 2 — SHOULD ACHIEVE (Full marks on implementation)

| # | Goal | Done when | Deadline |
|---|------|-----------|----------|
| G11 | ≥ 20 validated harnesses | 20+ entries with `accepted=true` in `oracle_log.csv` | Oct 26 (PM2) |
| G12 | Coverage delta measured | Branch % before/after adding GapHarn harnesses, from 3×1h runs | Oct 30 |
| G13 | G2 LLM generator works | `gen_llm.py` generates candidates for G1-failed functions; all responses cached | Oct 20 |
| G14 | Parametrization works | Hardcoded constants replaced with fuzz-derived values in LLM harnesses | Oct 22 |
| G15 | Greedy selection works | `select.py` picks K harnesses maximizing API reach | Oct 22 |
| G16 | ≥ 1 upstream bug report filed | GitHub issue link added to `UPSTREAM.md` with date | Oct 25 |
| G17 | +15% branch coverage | GapHarn harnesses achieve ≥ 15 percentage-point improvement over OSS-Fuzz baseline | Nov 3 |
| G18 | RQ1–RQ5 data collected | All five research questions have numeric answers | Nov 6 |
| G19 | Coverage-over-time plot | Figure showing baseline vs GapHarn coverage across 24h | Nov 7 |
| G20 | Case study written | One bug described end-to-end in paper §6: harness → crash → root cause → patch | Nov 9 |

---

## TIER 3 — STRETCH GOALS (Distinction / extra credit)

| # | Goal | Done when | Deadline |
|---|------|-----------|----------|
| G21 | ≥ 1 bug acknowledged by upstream | libarchive maintainer replies "confirmed" or applies fix | Nov 10 |
| G22 | ≥ 1 CVE assigned | CVE ID appears in UPSTREAM.md | Nov 13 |
| G23 | Round-trip oracle finds a bug | Logic bug found via write-then-read-back oracle (not just memory safety) | Nov 5 |
| G24 | libtiff secondary target results | Gap + harnesses + coverage delta for libtiff as a second data point | Nov 6 |
| G25 | `make check` passes on paper | Zero `\focus{}`, zero `\todo`, zero undefined refs, within page limit | Nov 11 |
| G26 | Both members file ≥ 1 upstream report each | 2 entries in `UPSTREAM.md` with different author names | Nov 6 |
| G27 | CI green across all commits | GitHub Actions passes on every PR since Day 1 | Nov 13 |

---

## GOALS BY MILESTONE

### Progress Meeting 1 (Oct 11–16) — "Scope firmed, target accessible, pipeline running"

**Must show:**
- [ ] Gap table with real numbers (G2 complete)
- [ ] `python -m gapharn.apimodel libarchive` produces api.json (G3 complete)
- [ ] At least one harness compiles and runs 30 seconds (G4 complete)
- [ ] Architecture diagram explained clearly

**Goal state: G1–G4 complete**

---

### Progress Meeting 2 (Oct 26–30) — "First end-to-end results, first bug candidates, evaluation plan"

**Must show:**
- [ ] ≥ 20 validated harnesses with oracle_log.csv (G11 complete)
- [ ] Coverage delta chart: before vs after (G12 partial — 1h runs OK)
- [ ] At least 1 crash candidate triaged and classified (Real or Misuse)
- [ ] At least 1 upstream report filed (G16 complete)
- [ ] Evaluation plan for RQ1–RQ5 locked down

**Goal state: G1–G16 complete**

---

### Progress Meeting 3 (Nov 2–6) — "Upstream reports filed, results consolidated, report draft"

**Must show:**
- [ ] All planned upstream reports filed with links
- [ ] RQ1–RQ5 data collected (G18 complete)
- [ ] Paper draft: all sections present, most content written (G8 partial)
- [ ] Coverage plot (G19 complete)

**Goal state: G1–G20 complete**

---

### Final Presentation (Nov 12) — 10–15 min talk + Q&A

**Must present:**
- [ ] Problem motivation with real gap numbers
- [ ] GapHarn pipeline with architecture diagram
- [ ] RQ1–RQ5 results with tables and figures
- [ ] Bug case study end-to-end
- [ ] Both members speak roughly equal time
- [ ] Both members can answer any question about any part

**Goal state: G1–G25 as many as possible**

---

### Submission (Nov 13, 23:59 IST)

**Must submit:**
- [ ] Paper PDF (from Overleaf)
- [ ] Overleaf project link
- [ ] Public GitHub repo link
- [ ] `make repro LIB=libarchive` verified on clean VM (G9 complete)
- [ ] `CONTRIBUTIONS.md` signed by both members
- [ ] `UPSTREAM.md` with final status of all reports
- [ ] `README.md` with complete reproducibility instructions

---

## INDIVIDUAL ACCOUNTABILITY GOALS

Each team member must independently achieve:

| Member A | Member B |
|---------|---------|
| Can explain the API model (Stage 1) in detail | Can explain the gap analysis (Stage 0) in detail |
| Can explain G1 rule-based generation (Stage 2) | Can explain the oracles O1–O4 (Stage 3) |
| Can explain G2 LLM generation | Can explain the distributed fuzzing setup (Stage 6) |
| Files ≥ 1 upstream bug report | Files ≥ 1 upstream bug report |
| Writes ≥ 50% of the paper | Writes ≥ 50% of the paper |
| Has ≥ 4 meaningful commits per week | Has ≥ 4 meaningful commits per week |
| Can answer any question about any part of the project | Can answer any question about any part of the project |

> **Rule:** "That was X's part" is not an acceptable answer at any meeting.

---

## QUANTITATIVE TARGETS

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Gap functions targeted | All in gap.json | All in gap.json | All + libtiff |
| Harnesses generated (G1+G2) | 10 | 30 | 50 |
| Harnesses passing O1–O4 | 5 | 20 | 35 |
| Branch coverage improvement | +5% | +15% | +25% |
| API symbols covered | 30% | 60% | 80% |
| Upstream bug reports filed | 1 | 2 | 4 |
| Upstream bugs confirmed | 0 | 1 | 3 |
| Oracle false-positive rate | < 30% | < 10% | 0% |
| Fuzzing campaign duration | 12h | 24h | 48h |
| Paper length | 8 pages | 10 pages | 10 pages (at limit) |

---

## WHAT SUCCESS LOOKS LIKE

### Minimum success (passing):
You ran the pipeline, measured the gap, generated some harnesses, ran fuzzing, and wrote a paper about it. No confirmed bugs, but the coverage improvement is documented.

### Good success (strong marks):
You generated 20+ harnesses covering the write-path gap, achieved +15% coverage, filed at least 1 upstream bug report, and the paper clearly describes RQ1–RQ5 with real data.

### Excellent success (full marks + recognition):
You confirmed ≥ 1 upstream bug (acknowledged or fixed by maintainers), demonstrated the round-trip oracle finding a logic bug, compared G1 vs G1+G2 in an ablation study, and produced a paper that could plausibly be submitted to a venue.

### The one thing that matters most:
**Every number in the paper must come from a real run of your code.** No invented numbers. No rounded-from-memory figures. Every claim must be reproducible with `make repro`.

---

## ANTI-GOALS (Things we explicitly will NOT do)

- Run OSS-Fuzz Docker infrastructure (too heavy; use manual coverage fallback)
- Use a paid LLM API (zero-cost local model only; reproducibility requirement)
- Fuzz without ASAN (ASAN is mandatory — without it crashes go undetected)
- Commit crash files before upstream fix is public (security disclosure ethics)
- Report API-misuse crashes as bugs (Oracle O2 prevents this)
- Generate harnesses for already-covered functions (gap-directed only)
- Use `git commit --amend` or force-push on main (commit history integrity)
- Submit without running `make repro` on a clean machine (reproducibility is non-negotiable)

---

*Goals last updated: Oct 15, 2026*
*Review at every progress meeting and update checkboxes.*
