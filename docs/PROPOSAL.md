# CS 5187 ? Term Project Proposal
## GapHarn: Gap-Directed Automated Fuzz-Harness Generation for C Libraries

**Course:** CS 5187 ? Software Analysis ? Monsoon 2026 ? Dept. of CSE, IIT Guwahati  
**Project Track:** A1 ? Fuzzing & Greybox Testing (Topic 2 ? L14?L16)  
**Target Submission Date:** October 2026 (Monsoon Semester)  

---

## 1. Problem Statement & Motivation

Modern software security relies heavily on automated coverage-guided greybox fuzzing (e.g., AFL++, libFuzzer, Honggfuzz) integrated into continuous fuzzing ecosystems like Google OSS-Fuzz. While the backend mutation schedulers and feedback engines are highly optimized, fuzz testing of C/C++ libraries remains fundamentally crippled by the **Fuzz Driver Bottleneck**. 

A fuzz fuzzer cannot execute code that no entry-point driver invokes. In practice, writing fuzz targets requires extensive manual developer effort to understand:
1. Multi-stage initialization sequences (e.g., handle creation, configuration flags),
2. Strict type contracts and opaque handle propagation,
3. Deterministic teardown and memory-cleanup routines to prevent spurious leak reports,
4. Defensive argument validation to avoid trivial assertion aborts.

Consequently, open-source libraries maintain only a tiny handful of hand-crafted harnesses. In **libarchive**?a security-critical parsing library underpinning FreeBSD base utilities, macOS `bsdtar`, package managers, and CMake?Google OSS-Fuzz deploys **only 4 harnesses**, all strictly confined to archive decompress/read pathways (`archive_read_*`). Over **90% of exported symbols**, including the entire archive generation and compression subsystem (`archive_write_*`), extended attribute handlers (`archive_entry_xattr_*`), access control lists (`archive_entry_acl_*`), and disk traversal engines (`archive_read_disk_*`), are **never exercised by any fuzzer**.

Historical high-severity vulnerabilities (such as CVE-2019-1000019, CVE-2019-1000020, and CVE-2021-36976) occurred in complex, stateful routines. When stateful functions are never invoked by fuzzers, latent memory-corruption bugs (heap buffer overflows, integer wraparounds, use-after-free) persist indefinitely.

---

## 2. Research Questions (RQs)

To rigorously evaluate GapHarn, our investigation is guided by five primary research questions:

- **RQ1 (API Reach):** How many previously unexercised exported API functions does GapHarn successfully invoke compared to the baseline OSS-Fuzz harness suite?
- **RQ2 (Code Coverage):** What cumulative line and branch coverage gain does GapHarn achieve over 24-hour continuous fuzzing campaigns against the baseline?
- **RQ3 (Vulnerability Discovery):** Does GapHarn uncover previously unknown zero-day vulnerabilities or logic bugs, and can these be confirmed and upstreamed to maintainers?
- **RQ4 (Oracle Precision & False-Positive Rate):** What proportion of synthesized harness candidates are rejected at each oracle stage (compilation, sanitizer sanity, gap-hit verification, linting), and does the pipeline maintain a 0% false-positive crash report rate?
- **RQ5 (Ablation Analysis):** What is the marginal contribution of rule-based synthesis (G1) versus LLM-assisted synthesis (G2), and what is the individual efficacy of the sanitizer sanity oracle (O2)?

---

## 3. Target Systems

### Primary Target: `libarchive`
- **Repository:** `https://github.com/libarchive/libarchive`
- **Characteristics:** Written in clean C; extensive format support (TAR, PAX, CPIO, ISO9660, ZIP, 7-Zip); complex stateful lifecycle.
- **Identified Gap Area:** Archive creation pipeline (`archive_write_*`), stream filter chains, format-specific header encoders, and ACL/xattr metadata APIs.
- **Maintainer Responsiveness:** Active upstream issue triage and GitHub pull request reviews (typically under 7 days).

### Secondary Target: `libtiff`
- **Repository:** `https://gitlab.com/libtiff/libtiff`
- **Characteristics:** 196+ exported functions, ubiquitous image processing dependency (ImageMagick, GDAL). Known history of deep API bugs (e.g., 6 bugs uncovered by libErator in FSE 2025).

---

## 4. Proposed Technical Approach: GapHarn

GapHarn is structured as an end-to-end, gap-directed harness synthesis pipeline comprising 8 sequential phases:

```
[Stage 0: Gap Analysis]
  Exported Symbols (nm) ? Public Headers (libclang) \ Executed Symbols (llvm-cov) = GAP LIST
         ?
         ?
[Stage 1: API Type & Dependency Modeling]
  Extract parameter types, docstrings, enums, opaque handles ? Classify functional roles
         ?
         ?
[Stage 2: Hybrid Harness Generation]
  ??? G1: Rule-Based (Producer-Consumer call graph chains + type-based fuzz data mapping)
  ??? G2: LLM-Assisted (PromptFuzz-style contextual prompts + unit-test usage mining)
         ?
         ?
[Stage 3: Multi-Stage Correctness Oracles]
  O1 (Compile -Werror) ??? O2 (60s Sanity on Valid Seeds) ??? O3 (Gap Hit) ??? O4 (Lint)
         ?
         ?
[Stage 4: Fuzz-Argument Parametrization]
  Rewrite hardcoded literals into dynamic fuzz reads (bitmasks, enum arrays)
         ?
         ?
[Stage 5: Diversity-Driven Greedy Selection]
  Greedy Set-Cover: Select minimal subset of K harnesses maximizing unique gap API coverage
         ?
         ?
[Stage 6: Distributed Campaign Execution]
  AFL++ with ASan/UBSan across multi-core nodes; corpus initialization
         ?
         ?
[Stage 7: Triage, Minimization & Reproduction]
  Stack hashing (top-3 frames) ??? afl-tmin minimization ??? Standalone 30-line C reproducers
         ?
         ?
[Stage 8: Upstream Disclosure & Patching]
  Report to maintainers with root-cause analysis, proof-of-concept, and proposed patches
```

---

## 5. Comparative Grounding with Related Literature

| Framework | Conference | Mechanism | Limitations Addressed by GapHarn |
| :--- | :--- | :--- | :--- |
| **OGHarn** | ICSE 2025 | Header-driven rule synthesis with return status checks and sanity oracles. | Operates uniformly across the entire library without prioritizing unreached API gaps; lacks contextual synthesis for complex multi-handle callbacks. |
| **PromptFuzz** | CCS 2024 | Coverage-guided evolutionary mutation of LLM prompts. | High monetary/API latency cost; susceptible to generating non-compilable or hallucinated API invocations without AST grounding. |
| **libErator** | FSE 2025 | SVF-based whole-program interprocedural analysis and diversity clustering. | Static pointer analysis incurs extreme memory overhead and multi-hour execution times on consumer hardware (e.g., 7.5h for libtiff). |
| **UTopia** | S&P 2023 | Slicing and def-use analysis on existing unit test suites. | Restricted exclusively to C++ GoogleTest and Boost.Test frameworks; cannot operate on custom C test suites like libarchive's. |

---

## 6. Verification and Evaluation Plan

| Phase | Metric | Tool / Instrumentation | Target Threshold |
| :--- | :--- | :--- | :--- |
| **API Coverage** | Unreached exported symbols executed | `llvm-cov export`, `gapharn.gap` | $\ge 60\%$ of exported API exercised |
| **Code Coverage** | Cumulative branch coverage delta | `llvm-cov report` over 24-hour campaign | $+15\%$ increase over OSS-Fuzz baseline |
| **Sanitizer Sanity** | False-positive crash rate | ASan/UBSan traces on clean corpus | $0\%$ spurious API misuse crashes |
| **Upstream Impact** | Accepted bug reports / security CVEs | Upstream issue tracker / GitHub PRs | $\ge 1$ confirmed upstream bug or merged PR |

---

## 7. Team Division of Labor

- **Member A (Generation & AST Modeling):**
  - Development of `apimodel.py` (libclang header parsing, role classification).
  - Implementation of `gen_rule.py` (producer-consumer path synthesis).
  - Implementation of `gen_llm.py` and local `llama.cpp` pipeline integration.
  - Evaluation of RQ1 and RQ5.
- **Member B (Infrastructure, Oracles & Triage):**
  - Development of `gap.py` (symbol extraction and llvm-cov JSON analysis).
  - Development of `oracle.py` (compilation, ASan sanity filter, AST linting).
  - Fuzzing harness cluster setup, campaign orchestration, and `triage.py`.
  - Evaluation of RQ2, RQ3, and upstream PR management.
- **Joint Responsibilities:**
  - Daily crash triage review (independent Real vs. Misuse classification).
  - Report preparation (ACM FSE LaTeX format) and oral defense presentations.

---

## 8. Milestone Timeline

- **Progress Meeting 1 (Oct 11?16, 2026):** Baseline coverage established, API gap quantified, initial synthesis pipeline running, $\ge 1$ compiled functional write-harness demonstrated.
- **Progress Meeting 2 (Oct 26?30, 2026):** End-to-end generation running, $\ge 20$ validated harnesses, initial 24h fuzzing results, first crash triage and bug candidates.
- **Progress Meeting 3 (Nov 02?06, 2026):** Upstream bug reports filed with PR patches, evaluation figures consolidated, complete draft of FSE paper.
- **Final Presentation & Submission (Nov 12?13, 2026):** Public repository, one-click `make repro` verification, final ACM paper PDF submitted.
