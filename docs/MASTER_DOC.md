# GapHarn ? Master Project Documentation Hub
## CS 5187: Software Analysis ? Monsoon 2026 ? IIT Guwahati
### Topic A1: Automated Fuzz-Harness Generation for C Libraries

> **Executive Summary:**  
> **GapHarn** is a gap-directed automated fuzz-harness synthesizer designed to solve the critical "uncalled API bottleneck" in library greybox fuzzing. Rather than generating harnesses blindly for the entire symbol table, GapHarn isolates the unexercised API surface (the *API Gap*) via dynamic source coverage of existing public fuzz corpora, builds an AST/type dependency graph using `libclang`, synthesizes memory-safe harnesses using rule-based (OGHarn-style) and LLM-assisted (PromptFuzz-style) engines, filters candidates through 4 strict correctness oracles (compilation, sanitizer sanity, gap-hit verification, and lifecycle linting), and deploys diverse harness subsets via greedy set-cover selection onto a distributed AFL++ cluster.

---

## ?? Complete Document Directory

| Document | Purpose & Scope | Target Audience & Timing |
| :--- | :--- | :--- |
| **[PROPOSAL.md](./PROPOSAL.md)** | Formal term project proposal in academic FSE format. Problem statement, RQs, related work comparison, expected contributions, evaluation plan, and timeline. | Faculty review, initial project approval |
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | Deep technical system architecture. 8-stage pipeline, ASCII schematics, data-flow models, AST role classification heuristics, oracle specifications, and distributed fuzzing topology. | Architecture reviews, engineering implementation |
| **[IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)** | Granular engineering roadmap. Python package structure (`gapharn/`), function signatures, unit test definitions, Makefile rules, and module checklists. | Pair programming, weekly sprints, git milestones |
| **[IDEATION.md](./IDEATION.md)** | Design rationale, hypothesis formulation, target library trade-off analysis (libarchive vs. others), paper synthesis (OGHarn, PromptFuzz, libErator, UTopia), and rejected alternatives. | Conceptual justification, design defense |
| **[STEPS_TO_TACKLE.md](./STEPS_TO_TACKLE.md)** | Operational day-by-day execution guide. Tonight's PM1 prep, Week 1 synthesis, Week 2 bug triage, Week 3 evaluation, and submission deadlines. | Daily standups, lab visits, triage sessions |
| **[CROSS_QUESTIONS.md](./CROSS_QUESTIONS.md)** | Exhaustive 50+ question viva & interview bank. Core concepts, pipeline mechanics, sanitizers, LLVM coverage, libarchive internals, and gotcha questions. | **Mandatory preparation for all team members** |
| **[VIVA_PREP.md](./VIVA_PREP.md)** | Advanced viva preparation. Quantitative defense templates, statistical evaluation explanations (Mann-Whitney U), counterfactual scenarios, and defense strategies. | Progress meetings 2 & 3, Final Open Presentation |
| **[PM1_PRESENTATION.md](./PM1_PRESENTATION.md)** | Step-by-step presentation script for Progress Meeting 1 (10-minute slot). Slide-by-slide talking points, live terminal demo sequence, and fallback strategies. | Progress Meeting 1 presentation delivery |

---

## ?? Project Quick Reference & Metadata

- **Course:** CS 5187 Software Analysis (Monsoon 2026), Dept. of Computer Science & Engineering, IIT Guwahati.
- **Evaluation Weightage:** Implementation & Upstream Results (50%), 3 Progress Meetings + Final Talk (25%), FSE Report (25%).
- **Primary Target Library:** `libarchive` (C archive reading/writing library used in macOS, FreeBSD, CMake).
- **Secondary / Baseline Targets:** `libtiff` and `libpng`.
- **Primary Novelty:** *Gap-Directed Synthesis* ? prioritizing API functions that OSS-Fuzz harnesses have 0% execution coverage on (notably `archive_write_*`, `archive_entry_*` ACL/xattr, and `archive_match_*`).
- **Synthesis Backbone:** 
  - **G1:** Header-driven rule-based generation with OGHarn-style status checks and lifecycle pairing.
  - **G2:** Local quantized LLM (`Qwen2.5-Coder-7B-Instruct` via `llama.cpp`) with test-suite usage extraction.
- **Verification Oracles:** 
  - **O1:** Clean compilation under `-fsanitize=address,undefined,fuzzer` with `-Werror=implicit-function-declaration`.
  - **O2:** 60-second ASan sanity run on valid corpora (zero false-positive crash filter).
  - **O3:** LLVM coverage verification proving the target gap function executed.
  - **O4:** Static lifecycle linting (pointer return checks, `*_new`/`*_free` parity).
- **Target Deliverable:** $\ge 1$ security vulnerability or memory bug with standalone reproducer, patch PR, and upstream acceptance.

---

## ? Essential Commands Cheat-Sheet

```bash
# 1. Environment & API Extraction
cd src
python -m gapharn.apimodel libarchive

# 2. Run Gap Analysis against existing OSS-Fuzz coverage
python -m gapharn.gap libarchive

# 3. Compile manual baseline write harness
clang -fsanitize=address,fuzzer -O1 -g -I work/libarchive/src \
      harnesses/libarchive/harness_write.c \
      work/libarchive/build-cov/libarchive/libarchive.a \
      -lz -lbz2 -llzma -lzstd -o harness_write

# 4. Execute 30s sanity fuzzing run
./harness_write corpus/ -max_total_time=30 -print_final_stats=1
```
