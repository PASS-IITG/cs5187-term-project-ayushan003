# GapHarn — Complete Project Documentation
## CS 5187 · Software Analysis · IIT Guwahati · Monsoon 2026
## Topic A1: Automated Fuzz-Harness Generation for C Libraries

> **One sentence:** GapHarn automatically measures which API functions existing fuzz harnesses never test, generates correct harnesses for those exact functions, and uses them to hunt real memory-safety bugs in production C libraries (primary target: **libarchive**).

---

# TABLE OF CONTENTS

1. [Project Proposal](#1-project-proposal)
2. [Ideation and Design Decisions](#2-ideation-and-design-decisions)
3. [System Architecture](#3-system-architecture)
4. [Implementation Plan](#4-implementation-plan)
5. [Step-by-Step Execution Guide](#5-step-by-step-execution-guide)
6. [Cross Questions and Answers](#6-cross-questions-and-answers)
7. [Progress Meeting 1 Presentation Script](#7-progress-meeting-1-presentation-script)

---

# 1. PROJECT PROPOSAL

## 1.1 Project Identity

| Field | Value |
|-------|-------|
| **Project name** | GapHarn |
| **Topic** | A1 — Automated fuzz-harness generation for a C library |
| **Primary target** | libarchive (`libarchive/libarchive` on GitHub) |
| **Secondary target** | libtiff |
| **Core idea** | Gap-directed: generate harnesses *only* for API functions no existing harness reaches |
| **Backbone** | OGHarn (ICSE 2025) + PromptFuzz (CCS 2024) + libErator (FSE 2025) |

---

## 1.2 Problem Statement

Fuzz testing of C libraries is bottlenecked not by the fuzzer but by the **fuzz harness** — the C program that bridges the fuzzer's byte stream to the library's API. Without a harness that calls a function, the fuzzer cannot test it at all.

**The scale of the problem:**
- **libarchive** exposes ~250 public API functions. Google's OSS-Fuzz — the world's largest continuous fuzzing platform — ships exactly **4 harnesses** for it. All 4 focus on the read path. The entire write path (`archive_write_*`), the entry API (`archive_entry_*` ACL/xattr/sparse), the match API (`archive_match_*`), and the disk-enumeration API (`archive_read_disk_*`) are **never fuzz-tested**.
- OGHarn (ICSE 2025) found that across 20 major C libraries, OSS-Fuzz harnesses reach a **median of 18%** of exported API functions.
- Historical CVEs: CVE-2019-1000019, CVE-2019-1000020, CVE-2021-36976 — all in code the existing 4 harnesses cannot reach.

**Why writing harnesses manually is impossible at scale:**
Writing a correct harness requires understanding: initialization order, resource-teardown contracts, valid argument ranges, error-handling sequences, and API preconditions — for each function. Typical time: 1–2 hours per function. For 250 functions: 250–500 person-hours for one library.

**Our goal:** Build GapHarn, which:
1. Measures which API functions existing harnesses never execute (the "gap")
2. Automatically generates new harnesses targeting exactly those gap functions
3. Validates generated harnesses with correctness oracles (0% false positives is the target)
4. Fuzzes the validated harnesses to find real memory-safety bugs
5. Reports confirmed bugs to upstream with patches

---

## 1.3 Research Questions

| RQ | Question | Metric | Tool |
|----|----------|--------|------|
| **RQ1** | How many previously unreached API functions does GapHarn exercise vs OSS-Fuzz baseline? | Count of exported symbols with execution_count > 0 | llvm-cov + gap.py |
| **RQ2** | How much additional branch coverage do GapHarn harnesses produce over 24 hours? | Branch % via llvm-cov; Mann-Whitney U across 3 runs | llvm-cov, scipy |
| **RQ3** | How many real, previously unknown bugs does GapHarn find? Are they upstream-accepted? | Unique confirmed bugs; issue/PR status | libarchive GitHub |
| **RQ4** | What fraction of generated harness candidates are rejected by each oracle? | Per-oracle rejection rate; crash FP rate | oracle_log.csv |
| **RQ5** | How much does G2 (LLM) add over G1 (rule-based) alone? | Coverage and API-reach delta: G1 vs G1+G2 | 1h runs per config |

---

## 1.4 Target Libraries

### Primary: libarchive
- **GitHub:** https://github.com/libarchive/libarchive
- **Where used:** FreeBSD base system tar, macOS bsdtar and Archive Utility, CMake source extraction, hundreds of package managers
- **API gap location:** `archive_write_*` (78 functions, 0 reached by OSS-Fuzz), `archive_entry_*` ACL/xattr/sparse, `archive_match_*`, `archive_read_disk_*`
- **Security relevance:** Processes attacker-controlled archive files. Memory corruption → remote code execution.
- **Maintainer responsiveness:** Issues answered within ~7 days; SECURITY.md present.

### Secondary: libtiff
- **Why:** libErator (FSE 2025) found 6 bugs in the write/directory APIs; active TIFF security community; write API under-fuzzed.

### Libraries avoided:
- **OpenSSL, SQLite core:** Already extremely heavily fuzz-tested; "API misuse" reports are closed
- **FFmpeg:** 20+ minute build time on our hardware
- **libpng:** Small API (~80 functions); gap limited
- **zlib, cJSON, libpcap:** Already mined by 2–3 of the four reference papers

---

## 1.5 Related Work

| Paper | Venue | Key idea | Results | Our use |
|-------|-------|----------|---------|---------|
| **OGHarn** — *No Harness, No Problem* | ICSE 2025 | Rule-based harness gen from headers; role classification; status-check oracles; **0% false-positive rate** | 41 bugs found, 40 fixed across 20 libraries | **G1 backbone** — adopt role classification, init-chain construction, O1+O2 oracles |
| **PromptFuzz** | CCS 2024 | LLM generates API usage programs; oracle filters; coverage-guided prompt mutation; constant→variable arg substitution | 33 bugs confirmed, 1.61× branch coverage vs OSS-Fuzz | **G2 engine** — adopt prompt structure, repair loop, parametrization |
| **libErator** | FSE 2025 | SVF static analysis infers API sequences; diversity-based driver selection; libpcap CVE-2024-8006 found | 24 confirmed bugs, 21 fixed | **Selection algorithm** — greedy set-cover over newly covered functions; skip SVF (too heavy) |
| **UTopia** | S&P 2023 | Converts gtest/boost unit tests → fuzz drivers via def-use slicing | 123 bugs across 55 libraries | **Not implemented** — libarchive tests use custom framework; borrow test-snippet idea for G2 prompts |

**Our novelty:** GapHarn is the first system to combine (1) measured API gap targeting with a gap-coverage oracle, (2) OGHarn-style rule-based generation with 0% FP oracles, (3) LLM generation grounded in test snippets, and (4) diversity-based selection — applied to a library with a large, measurable write-path gap.

---

## 1.6 Expected Contributions

1. **GapHarn pipeline** — reusable open-source tool (`make gap`, `make gen`, `make oracle`, `make fuzz`)
2. **API gap measurement** — quantified gap for libarchive (and libtiff) with exact, reproducible numbers
3. **≥ 20 validated harnesses** covering previously untested write/entry/match API functions
4. **+15% branch coverage** vs OSS-Fuzz baseline over 24 hours (target)
5. **≥ 1 confirmed upstream bug** with a patch acknowledged by libarchive maintainers

---

## 1.7 Verification Plan

| Metric | Tool | Target |
|--------|------|--------|
| Branch coverage delta | llvm-cov, 3×24h runs | +15% vs OSS-Fuzz |
| API symbols covered | gap.py + llvm-cov | ≥ 60% of exported symbols |
| Harness compile rate | Oracle O1 log | ≥ 80% of G1 candidates |
| Oracle-passing rate | oracle_log.csv | Log all 4 oracles per candidate |
| Confirmed upstream bugs | GitHub issue tracker | ≥ 1 acknowledged |

**Reproducibility:** `make repro LIB=libarchive` rebuilds a pinned commit, replays LLM responses from `llm_cache/`, re-runs oracles and selection, prints the RQ1 table, and replays committed crash reproducers — **no 24h fuzzing needed**.

---

## 1.8 Team Responsibilities

| Member | Primary | Shared |
|--------|---------|--------|
| Member A | API model (Stage 1), G1 rule-based gen, G2 LLM gen, parametrization, ablation (RQ5) | Triage, paper writing, upstream reporting |
| Member B | Gap analysis (Stage 0), oracles O1–O4, greedy selection, fuzzing infra, crash triage, RQ2 runs | Triage, paper writing, upstream reporting |

Each member files ≥ 1 upstream bug report. Commit history (branch+PR workflow) documents individual contributions.

---

## 1.9 Timeline

| Milestone | Date | Deliverables |
|-----------|------|-------------|
| **Progress Meeting 1** | Oct 11–16, 2026 | Gap table; pipeline running; ≥5 harnesses compiling |
| **Progress Meeting 2** | Oct 26–30, 2026 | ≥20 harnesses; coverage delta; first crash candidates; upstream report filed |
| **Progress Meeting 3** | Nov 2–6, 2026 | All upstream reports filed; RQ1–RQ5 data; paper draft |
| **Final Presentation** | Nov 12, 2026 | 10–15 min talk + Q&A |
| **Paper + Repo due** | Nov 13, 2026 23:59 IST | PDF + public GitHub + reproducibility instructions |

---

# 2. IDEATION AND DESIGN DECISIONS

## 2.1 The Core Insight: Gap is Measurable and Precise

The A1 problem says: *"The fuzzer works fine. Nothing calls most of the library's functions."*

The key insight is that this is **quantifiable**: we can subtract the set of executed functions from the set of exported functions and get an exact list of what's never tested. This turns a vague problem into a precise engineering target.

**Before measuring:** "libarchive is probably under-fuzzed in the write path."
**After measuring:** "libarchive exports 247 API functions. OSS-Fuzz harnesses execute 23. Gap = 224 (90.7%). The archive_write_* prefix alone has 78 functions all with zero execution count."

The gap measurement is itself a contribution. No prior paper had quantified this for libarchive specifically.

---

## 2.2 Why libarchive as the Primary Target

We considered five candidate libraries:

| Library | Gap (est.) | Build time | Maintainers | Used by | Decision |
|---------|-----------|-----------|------------|---------|----------|
| **libarchive** | ~90% (write path) | ~3 min | Active (<7 days) | FreeBSD, macOS, CMake | ✅ **Primary** |
| libtiff | ~70% (write/dir APIs) | ~2 min | Active | GDAL, ImageMagick | ✅ Secondary |
| libxml2 | ~60% (writer, XPath) | ~4 min | Active | Everywhere | Backup |
| OpenSSL | ~30% | ~15 min | Very active | Everywhere | ❌ Already saturated |
| FFmpeg | ~80% (demuxers) | ~25 min | Active | Media players | ❌ Build too slow |

**Why libarchive wins:**
1. **The write-path gap is complete** — all `archive_write_*` functions are at 0 execution count in OSS-Fuzz. This is an unusually clean gap to target.
2. **Security-critical** — processes attacker-controlled archives (TAR, ZIP, 7-Zip, CPIO, ISO9660). Memory corruption in the write path could be triggered by any application creating archives from untrusted data.
3. **Active maintainers** — response time ~7 days; they accept well-written bug reports with patches.
4. **On the professor's example list** — reduces approval risk.
5. **Stateful API** — the init→configure→open→iterate→close lifecycle is exactly the kind of structure where G1's chain-building approach shines.

---

## 2.3 Why These Design Choices

### 2.3.1 OGHarn as the G1 Backbone (not FuzzGen, not UTopia)

**Why not FuzzGen (USENIX Sec 2020)?**
- Requires whole-program LLVM bitcode analysis — expensive build integration
- No maintained public implementation
- OGHarn supersedes it in false-positive rate

**Why not UTopia (S&P 2023)?**
- UTopia's core technique (def-use slicing on unit tests) only works with gtest, boost, or TCT test frameworks
- libarchive's test suite uses a custom framework with macros like `assertMakeFile`, `assertEqualInt` — not gtest
- Implementing our own def-use analysis from scratch for a custom framework is a semester-long project in itself
- **Decision:** borrow the test-snippet idea (paste 1-2 test lines into the LLM prompt), but don't implement UTopia's analysis

**Why OGHarn (ICSE 2025)?**
- **0% false-positive rate** — this is the single most important property. Upstream maintainers close false-positive reports as INVALID and deprioritize future reports from the same source.
- CPU-only, no LLM, no heavy static analysis dependency for the core pipeline
- Role classification (initializer/data-entry/processor/destructor) maps exactly to libarchive's API design
- Detailed paper with implementable algorithms

### 2.3.2 PromptFuzz for G2 (not OSS-Fuzz-gen, not full PromptFuzz)

**Why not OSS-Fuzz-gen (Google)?**
- Requires Docker and integration with the OSS-Fuzz infrastructure
- Does not use test-snippet grounding (we use UTopia's insight here)
- Less structured for deeply stateful APIs

**Why not full PromptFuzz (coverage-guided prompt mutation)?**
- Full PromptFuzz requires many LLM calls per function (evolutionary mutation of prompts)
- On a local CPU-based model (3 tokens/s), a single function could take hours with full mutation
- G2 is a secondary generator — only for functions G1 can't handle (~20–30% of gap)
- **Decision:** Use PromptFuzz's prompt structure and one repair round; skip the evolutionary mutation loop

**Why local LLM (Qwen2.5-Coder-7B-Instruct)?**
- **Zero cost** — no API key, no payment card
- **Reproducible** — all responses cached in `llm_cache/` (keyed by SHA-256 of prompt + model + seed); reproduction requires no model at all
- **Offline** — works in lab environments without internet
- **Model choice:** Qwen2.5-Coder-7B-Instruct at Q4_K_M quantization (~4.7 GB). Best code generation quality at this size. Fits in 8 GB RAM for CPU inference. Alternative: Codestral, DeepSeek-Coder-V2.

### 2.3.3 libErator's Selection (not random)

**Why greedy set-cover over random selection?**
- Random selection of K harnesses might pick 10 harnesses all covering the same 5 gap functions
- Greedy set-cover guarantees each selected harness adds at least one new gap function
- Directly maximizes RQ1 (API reach) with the minimum number of harnesses (= minimum fuzzing cores)
- Greedy achieves (1-1/e) ≈ 63% of optimal in coverage, in linear time

**Why not libErator's SVF analysis?**
- SVF whole-program analysis took 7.5 hours for libtiff in the libErator paper
- Our lab PCs have 8 GB RAM — risk of OOM on a complex library
- We get the benefit (diversity selection) without the cost (heavy static analysis) by using our oracle-measured coverage instead of SVF-inferred coverage

### 2.3.4 The Four Oracle Design

The defining failure mode of automated harness generation is **API misuse**: the harness crashes the library by calling functions in the wrong order. The crash is real (ASAN reports it), but it's the harness's fault, not a library bug.

Each oracle catches a different category:

| Oracle | Catches | Can't be replaced by |
|--------|---------|---------------------|
| O1 (compile) | Wrong function names, type errors, missing headers | — |
| O2 (sanity run on valid seeds) | API misuse causing crashes on good input | O4 alone (subtle misuse may not crash immediately) |
| O3 (gap-hit check) | Harness compiles and runs but never calls the target | Coverage measurement alone (too slow to run per-candidate) |
| O4 (lint) | Structural misuse: missing free, unchecked pointer, invalid length | O2 alone (structural issues may only crash on rare inputs) |

OGHarn achieved 0% FP rate with O1+O2+lint. We add O3 (gap-hit) as GapHarn's specific contribution.

---

## 2.4 The Write-Path Insight

The read path (parsing archives) is the primary attack surface — users give programs archive files to extract, and those archives could be malicious. This is why the 4 OSS-Fuzz harnesses focus on reading.

But the **write API** is equally interesting:
1. Any application that creates archives programmatically uses `archive_write_*` — build systems, backup tools, deployment scripts
2. The write API involves format-specific integer arithmetic (computing offsets, sizes, checksums in archive headers) — classic source of integer overflow → buffer overflow
3. Format-specific constraints: ustar has a 100-byte filename limit, v7tar has different limits, ISO9660 has character set constraints — code enforcing these constraints is bug-prone
4. The **round-trip oracle**: write an archive, read it back, assert data matches. This finds *logic bugs* that neither read-only nor write-only fuzzing detects.

---

## 2.5 Rejected Approaches

| Approach | Rejected because |
|----------|----------------|
| Symbolic execution (KLEE) | State-space explodes with stateful API; too slow on our hardware; fuzzing empirically more effective for memory safety |
| Full PromptFuzz prompt mutation | Too many LLM calls; local model too slow; G2 is secondary generator |
| SVF whole-program analysis (libErator) | 7.5h for libtiff; OOM risk on 8 GB; skip analysis, keep selection idea |
| Paid LLM API (GPT-4o) | Cost; reproducibility; API key management on shared lab PCs |
| Running OGHarn directly | Less research contribution; we build gap-directed extensions (O3 oracle, selection) |

---

# 3. SYSTEM ARCHITECTURE

## 3.1 Full Pipeline Diagram

```
 INPUT: C library (source + build system + existing OSS-Fuzz harnesses)
 =========================================================================

 [Stage 0]  Gap Analysis                                    gap.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  Build library with coverage instrumentation                     │
 │    CC=clang CFLAGS="-fprofile-instr-generate -fcoverage-mapping" │
 │  Run existing OSS-Fuzz harnesses on public corpus (60s each)     │
 │    LLVM_PROFILE_FILE="h.profraw" ./harness corpus/ -time=60      │
 │  llvm-profdata merge → merged.profdata                           │
 │  llvm-cov export → JSON with per-function execution counts       │
 │  nm -g --defined-only libarchive.a → exported symbol set         │
 │  libclang parse headers → declared function set                  │
 │  GAP = (exported ∩ declared) − (executed with count > 0)         │
 │                                                                  │
 │  OUTPUT: results/libarchive/gap.json                             │
 └──────────────────────────┬───────────────────────────────────────┘
                            │  gap.json
                            ▼
 [Stage 1]  API Model                                    apimodel.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  libclang AST traversal over all public headers                  │
 │  Extract per-function:                                           │
 │    - signature (return type, params with types/names/const)      │
 │    - doc comment (cursor.raw_comment)                            │
 │    - enum/define legal values grouped by prefix                  │
 │    - opaque handle types (struct pointers in headers)            │
 │  Classify each function by ROLE:                                 │
 │    initializer  → *_new, *_open, *_init, *Create; returns T*     │
 │    data-entry   → takes handle + (buf,len) or filename           │
 │    processor    → takes handle, returns status int               │
 │    destructor   → *_free, *_close; frees handle                  │
 │    auxiliary    → everything else                                │
 │  Build producer→consumer graph:                                  │
 │    edge f→g if f returns T* and g takes T* as param             │
 │                                                                  │
 │  OUTPUT: results/libarchive/api.json                             │
 └──────────────────────────┬───────────────────────────────────────┘
                            │  api.json
                            ▼
 [Stage 2]  Harness Generation          gen_rule.py  gen_llm.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  FOR EACH gap function f:                                        │
 │                                                                  │
 │  G1 — Rule-Based (OGHarn-style):                                 │
 │    1. BFS on producer→consumer graph: init → ... → f             │
 │    2. Fill arg slots by type:                                    │
 │         (void*, size_t) pair → fuzz input slice                  │
 │         int/uint          → fuzz byte read                       │
 │         enum arg          → values[byte % n]                     │
 │         const char*       → null-terminated fuzz slice           │
 │         handle (struct T*)→ return value of earlier call         │
 │    3. Status check after every call (!=NULL, >=0 → goto done)    │
 │    4. Pair every *_new with *_free in reverse                    │
 │    5. Emit C source via Jinja2 template; emit N variants         │
 │                                                                  │
 │  G2 — LLM-Based (PromptFuzz-style), for G1-failed functions:    │
 │    Prompt = system contract + target signature + 15 related sigs │
 │             + 1-2 test snippets from library's test/ dir         │
 │    Generate 3 candidates per function                            │
 │    Compile-error repair: feed errors back once, regenerate       │
 │    All responses cached in llm_cache/ (no repeat LLM calls)      │
 │                                                                  │
 │  OUTPUT: work/libarchive/candidates/*.c                          │
 └──────────────────────────┬───────────────────────────────────────┘
                            │  candidate .c files
                            ▼
 [Stage 3]  Oracle Filtering                               oracle.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  O1 COMPILE:  clang -fsanitize=address,undefined,fuzzer          │
 │               -Werror=implicit-function-declaration              │
 │               → reject if any error or implicit declaration      │
 │                                                                  │
 │  O2 SANITY:   run 60s on VALID seeds (real archive files)        │
 │               → reject if any ASan/UBSan crash in 60s           │
 │               (crash on valid input = API misuse)                │
 │                                                                  │
 │  O3 GAP-HIT:  run with coverage build, check llvm-cov JSON      │
 │               → reject if target gap function count == 0         │
 │                                                                  │
 │  O4 LINT:     Python AST checks on harness source                │
 │               → reject if: *_new without *_free,                 │
 │                  unchecked pointer return passed on,             │
 │                  len arg not derived from actual buffer          │
 │                                                                  │
 │  OUTPUT: results/libarchive/oracle_log.csv                       │
 │          validated harnesses → work/libarchive/validated/*.c     │
 └──────────────────────────┬───────────────────────────────────────┘
                            │  validated harnesses
                            ▼
 [Stage 4]  Parametrization                                param.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  Find hardcoded int/enum literals in call arguments              │
 │  Replace with fuzz-derived reads:                                │
 │    "zip"     → FMTS[d[pos++] % N(FMTS)]                         │
 │    1024      → (uint32_t)read_u32(d, &pos)                       │
 │    AE_IFREG  → FTYPES[d[pos++] % N(FTYPES)]                      │
 │  Re-run O1, O2, O3 after rewriting                               │
 └──────────────────────────┬───────────────────────────────────────┘
                            ▼
 [Stage 5]  Selection                                     select.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  For each validated harness: measure which gap functions it hits │
 │  Greedy set-cover:                                               │
 │    while uncovered_gap_fns not empty and selected < K:           │
 │      pick harness covering most uncovered functions              │
 │      add to selected set, remove those functions from uncovered  │
 │  K ≈ available fuzzing cores (total cores across all lab PCs)    │
 │                                                                  │
 │  OUTPUT: harnesses/libarchive/*.c  (committed to git)            │
 └──────────────────────────┬───────────────────────────────────────┘
                            │  selected harnesses
                            ▼
 [Stage 6]  Distributed Fuzzing                  AFL++ on 4 lab PCs
 ┌──────────────────────────────────────────────────────────────────┐
 │  Laptop → git push → GitHub → git pull → Lab PC1 (build node)   │
 │  PC1 compiles all harness variants:                              │
 │    _asan  = clang -fsanitize=address,undefined,fuzzer            │
 │    _fast  = afl-clang-fast (no sanitizers, for throughput)       │
 │  PC1 rsync builds + seeds to PC2, PC3, PC4                       │
 │  Each PC runs different harness set (no corpus sync needed):     │
 │    afl-fuzz -M main → 1 ASan instance per harness               │
 │    afl-fuzz -S s1,s2 → fast instances (nproc-2 per harness)     │
 │  Cron on PC1 (hourly): rsync PC2-4 crash dirs → collected/       │
 │  Crash files: laptop only, NEVER in public repo until fix public  │
 └──────────────────────────┬───────────────────────────────────────┘
                            │  crash files
                            ▼
 [Stage 7]  Triage                                       triage.py
 ┌──────────────────────────────────────────────────────────────────┐
 │  Stack-hash clustering: top 3 LIBRARY frames (exclude harness)   │
 │  afl-tmin -i crash -o min -- ./harness_asan                      │
 │  Classify: REAL vs MISUSE                                        │
 │    REAL   = no documented precondition violated                  │
 │    MISUSE = harness violates API docs → fix generator rule       │
 │  Write standalone reproducer: <30 lines, documented API only     │
 │  Root cause: file:line in library source + proposed patch        │
 │  Check: not already fixed on latest master                       │
 └──────────────────────────┬───────────────────────────────────────┘
                            ▼
 [Stage 8]  Upstream Reporting
 ┌──────────────────────────────────────────────────────────────────┐
 │  Memory-safety from file input: private disclosure via SECURITY.md│
 │  Other bugs: public GitHub issue + PR with patch + regression test│
 │  Report template: version · API sequence · reproducer ·          │
 │    ASan trace · root cause file:line · patch · how found         │
 │  Track in UPSTREAM.md: link · date · state (open/ack/fixed)      │
 └──────────────────────────────────────────────────────────────────┘

 OUTPUT: Confirmed bugs + coverage delta + paper
```

---

## 3.2 Module Architecture

### Python Package: `src/gapharn/`

```
gapharn/
├── __init__.py
├── config.py       # TargetConfig: load targets/*/config.yaml, path helpers
├── gap.py          # Stage 0: gap analysis ✅ IMPLEMENTED
├── apimodel.py     # Stage 1: libclang header parser + role classifier ✅ IMPLEMENTED
├── gen_rule.py     # Stage 2/G1: rule-based generator 🔴 TODO
├── gen_llm.py      # Stage 2/G2: LLM generator with cache 🔴 TODO
├── oracle.py       # Stage 3: O1-O4 oracle pipeline 🔴 TODO
├── param.py        # Stage 4: constant→fuzz-arg rewriter 🔴 TODO
├── select.py       # Stage 5: greedy set-cover selection 🔴 TODO
├── triage.py       # Stage 7: crash cluster + classify 🔴 TODO
├── seeds.py        # Seed corpus management ✅ IMPLEMENTED
└── compare.py      # Coverage comparison for RQ1/RQ2 ✅ IMPLEMENTED
```

### Data Files on Disk

```
work/libarchive/
├── src/                        ← git clone of libarchive (pinned commit)
├── build-cov/                  ← coverage-instrumented build
│   └── libarchive/libarchive.a
├── build-asan/                 ← ASan+UBSan fuzz build
├── build-fast/                 ← afl-clang-fast build (no sanitizers)
└── existing-cov/
    ├── json/                   ← llvm-cov export JSONs per existing harness
    └── META.json               ← measurement metadata

results/libarchive/
├── api.json                    ← full API model output (apimodel.py)
├── gap.json                    ← gap analysis result (gap.py)
│   {api_total, reached_total, gap_total, gap_ratio, by_prefix, gap:[...]}
├── oracle_log.csv              ← per-candidate O1-O4 decisions
├── campaigns.csv               ← fuzzing campaign log (start, end, commit, PCs)
└── plots/                      ← RQ1-RQ5 figures (coverage-over-time, etc.)

harnesses/libarchive/           ← FINAL selected harnesses (committed)
├── harness_write.c             ← G1: write path (format + filter + data)
├── harness_entry.c             ← G1: entry ACL/xattr/sparse APIs
├── harness_match.c             ← G1: archive_match_* path matching
└── llm_write_7zip.c            ← G2: LLM-generated 7-Zip writer

llm_cache/
└── {sha256_of_prompt}_{model}_{seed}.json   ← all cached LLM responses
```

---

## 3.3 Key Algorithms

### Gap Computation (gap.py)

```python
# Precise definition:
API = {f | declared in public headers} ∩ {f | defined in libarchive.a (nm -g)}
reached = {f ∈ API | execution_count(f) > 0 in any llvm-cov JSON}
GAP = API − reached

# gap_ratio = len(GAP) / len(API)
# by_prefix = {prefix → {api:N, reached:M, gap:N-M}} sorted by gap descending
```

### Role Classification (apimodel.py, OGHarn §III-A)

```python
def classify_role(fn):
    name = fn["name"].lower()
    returns_handle = fn["return"]["pointer_depth"] > 0
    has_handle_param = any(p["pointer_depth"] > 0 and p["is_opaque_handle"]
                           for p in fn["params"])
    # Initializer: creates a handle
    if (any(k in name for k in ["_new","_open","_init","create"]) and
            returns_handle and not has_handle_param):
        return "initializer"
    # Destructor: frees a handle
    if (any(k in name for k in ["_free","_close","free","close"]) and
            has_handle_param):
        return "destructor"
    # Data-entry: takes handle + buffer
    if (has_handle_param and
            any(p["type"] in ("const void *","const uint8_t *","const char *")
                for p in fn["params"])):
        return "data_entry"
    # Processor: takes handle, returns status
    if has_handle_param:
        return "processor"
    return "auxiliary"
```

### Greedy Set Cover (select.py)

```python
def greedy_set_cover(harness_gap_map, max_k):
    # harness_gap_map: {path → frozenset of gap function names covered}
    uncovered = set().union(*harness_gap_map.values())
    selected = []
    while uncovered and len(selected) < max_k:
        # Pick harness covering most uncovered functions
        best = max(harness_gap_map,
                   key=lambda h: len(harness_gap_map[h] & uncovered))
        selected.append(best)
        uncovered -= harness_gap_map[best]
        del harness_gap_map[best]
    return selected
```

---

## 3.4 Distributed Fuzzing Topology

```
┌─────────────────────────────────────┐
│  YOUR LAPTOP                        │
│  All coding, LLM calls, git commits │
│  Paper writing                      │
│  Crash triage (final)               │
└──────────────┬──────────────────────┘
               │ git push
               ▼
┌─────────────────────────────────────┐
│  GitHub (public repo)               │
│  Public: code, harnesses, llm_cache │
│  NOT public: crash files (yet)      │
└──────────────┬──────────────────────┘
               │ git pull
               ▼
┌─────────────────────────────────────┐
│  LAB PC1  (build node + worker)     │
│  git checkout + cmake build         │
│  rsync build+seeds → PC2, PC3, PC4  │
│  Hourly cron: rsync crashes ← PC2-4 │
│  Fuzzes: harness set A              │
└──┬──────────┬─────────────┬─────────┘
   │rsync     │rsync        │rsync
   ▼          ▼             ▼
┌──────┐  ┌──────┐    ┌──────┐
│ PC2  │  │ PC3  │    │ PC4  │
│ set B│  │ set C│    │ set D│
│ AFL++│  │ AFL++│    │ AFL++│
└──────┘  └──────┘    └──────┘

Resource rule: ≤ nproc-1 fuzzer instances per PC
               1 ASan instance + (nproc-2) fast instances per harness set
               Check: free -h after 10min (if swap in use, drop 1 instance)
```

---

# 4. IMPLEMENTATION PLAN

## 4.1 Current Status

| Module | Status | Notes |
|--------|--------|-------|
| `gap.py` | ✅ Done | Reads nm output + llvm-cov JSON, computes gap |
| `apimodel.py` | ✅ Done | libclang AST parser, role classifier |
| `seeds.py` | ✅ Done | Seed corpus management |
| `compare.py` | ✅ Done | Coverage comparison utilities |
| `config.py` | ✅ Done | Target configuration loader |
| `gen_rule.py` | 🔴 TODO | G1 rule-based generator |
| `gen_llm.py` | 🔴 TODO | G2 LLM generator |
| `oracle.py` | 🔴 TODO | O1-O4 oracle pipeline |
| `param.py` | 🔴 TODO | Constant parametrization |
| `select.py` | 🔴 TODO | Greedy set-cover selection |
| `triage.py` | 🔴 TODO | Crash clustering + classification |

**For PM1 (tomorrow):** You only need Stage 0 + Stage 1 running, plus one harness compiling. The TODO modules are for PM2 and beyond.

---

## 4.2 Stage 2/G1: Rule-Based Generator (`gen_rule.py`)

### Functions to implement:

```python
def find_init_chain(api_model: dict, target_fn_name: str) -> list[str]:
    """
    BFS on producer->consumer graph to find shortest path:
      initializer → [optional processors] → target_fn
    
    Producer->consumer edge f->g: f returns T* and g takes T* as param.
    Start from any function with role "initializer".
    
    Returns: list of function names in call order.
    Raises: ValueError if no path found (caller should try G2 instead).
    """

def fill_arg(param: dict, fuzz_offset: str, enum_values: list) -> tuple[str, str]:
    """
    Return (C declaration line, C expression to use in function call).
    
    Rules by parameter type:
    - 'void *' or 'uint8_t *' followed by 'size_t': → fuzz input slice
      decl: "const uint8_t *buf{i} = d + pos; size_t len{i} = n - pos;"
      expr: "buf{i}, len{i}"
    - 'int', 'uint*', 'la_int64_t': → fuzz byte read
      decl: "int arg{i} = (int)d[pos < n ? pos++ : 0];"
    - 'const char *': → null-terminated fuzz slice (or literal if enum-like)
      if name matches enum prefix (e.g. format name, filter name):
        use string array: "FMTS[d[pos++] % N(FMTS)]"
      else: raw null-terminated slice
    - struct T* (opaque handle): → must come from earlier call in chain
    - enum-typed int: → values array indexed by fuzz byte
    """

def emit_harness(gap_fn: dict, chain: list[str], api_model: dict,
                 lib_name: str, template_path: str) -> str:
    """
    Render harness .c source from chain + gap_fn + api_model.
    Uses Jinja2 template at template_path.
    Returns complete C source as string.
    """

def generate_all(gap_json_path: Path, api_json_path: Path,
                 out_dir: Path, variants: int = 3) -> list[Path]:
    """
    For each function in gap_json:
      1. Try find_init_chain -> emit_harness -> write .c file
      2. If chain not found, log to g1_failed.txt (G2 picks these up)
      3. Emit `variants` shuffled variants (shuffle optional processors)
    Returns list of generated .c file paths.
    """
```

### Template (`src/templates/harness_g1.c.j2`):

```c
/* Generated by GapHarn G1 for: {{ target_fn.name }}
 * Library: {{ lib_name }} | Generator: rule-based (OGHarn-style)
 * DO NOT EDIT — regenerate: make gen LIB={{ lib_name }} */
#include <{{ main_header }}>
{% for h in extra_headers %} #include <{{ h }}> {% endfor %}
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

{% for name, values in enum_arrays.items() %}
static const int {{ name }}_VALS[] = { {{ values | join(', ') }} };
{% endfor %}
#define N(a) (sizeof(a)/sizeof((a)[0]))

int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) {
  if (n < {{ min_input_bytes }}) return 0;
  size_t pos = 0;

{% for decl in declarations %}   {{ decl }}; {% endfor %}

{% for call in call_chain %}
  /* [{{ call.role | upper }}] {{ call.fn }} */
  {{ call.result_assign }}{{ call.fn }}({{ call.args | join(', ') }});
{% if call.null_check %}  if (!{{ call.result_var }}) goto done;{% endif %}
{% if call.status_check %}  if ({{ call.result_var }} < 0) goto done;{% endif %}
{% endfor %}

done:
{% for cleanup in cleanup_calls %}  {{ cleanup }}; {% endfor %}
  return 0;
}
```

### Tests (`tests/test_gen_rule.py`):

```python
def test_find_init_chain_archive_write():
    # Load real api.json, find chain to archive_write_set_format_by_name
    # Expected: ["archive_write_new", "archive_write_set_format_by_name"]

def test_find_init_chain_no_path_raises():
    # For a function with no reachable initializer in the graph
    # Expected: raises ValueError

def test_fill_arg_buffer_pair_produces_slice():
    # param type "const void *" + next param "size_t"
    # Expected: decl contains "d + pos", expr contains both buf and len

def test_fill_arg_enum_string_uses_array():
    # param name "format" or "fmt"
    # Expected: expression uses FMTS[...%N(FMTS)]

def test_emit_harness_compiles():
    # Generate harness for archive_write_set_format_by_name
    # Compile: clang -fsanitize=address,fuzzer harness.c libarchive.a
    # Expected: exit code 0

def test_generate_all_creates_files():
    # Run on real gap.json (top 5 gap functions)
    # Expected: .c files created, g1_failed.txt lists any unhandled ones
```

---

## 4.3 Stage 2/G2: LLM Generator (`gen_llm.py`)

### Functions to implement:

```python
def grep_test_snippets(lib_src_dir: Path, fn_name: str,
                       max_snippets: int = 2) -> list[str]:
    """
    Grep lib_src_dir/test/ for fn_name. Return up to max_snippets
    code excerpts (the line + 3 lines of context).
    Used to ground LLM prompts with real usage examples (UTopia insight).
    """

def build_prompt(gap_fn: dict, api_model: dict,
                 test_snippets: list[str]) -> str:
    """
    Build the complete LLM prompt string. Structure:
    
    SYSTEM: harness contract (LLVMFuzzerTestOneInput, consume input only
            through d/n, check every return, free everything, include
            only the listed headers, must call <fn_name>)
    
    TARGET FUNCTION: <signature + doc comment>
    
    RELATED SIGNATURES (up to 15, sorted by role: init first):
      <role>: <signature>  // doc comment
    
    USAGE EXAMPLES FROM LIBRARY TESTS:
      /* from test/test_*.c */
      <snippet 1>
      <snippet 2>
    
    INSTRUCTION: Generate a complete, compilable LLVMFuzzerTestOneInput
    that calls <fn_name> with fuzz-derived inputs. Check all returns.
    """

def call_llm(prompt: str, model: str = "qwen2.5-coder-7b-instruct-q4_k_m",
             seed: int = 42, cache_dir: Path = Path("llm_cache"),
             temperature: float = 0.0) -> str:
    """
    Call local llama-server (OpenAI-compatible HTTP on localhost:8080).
    Cache key: SHA-256(prompt + model + str(seed)).
    If cache hit: return cached response.
    If cache miss: call API, cache response, return it.
    Temperature=0 for reproducibility.
    """

def extract_c_code(llm_response: str) -> str:
    """
    Extract C code block from LLM response.
    Handles: ```c ... ``` and ``` ... ``` fences.
    If no fence found: return the whole response (LLM sometimes outputs raw C).
    """

def generate_with_repair(gap_fn: dict, api_model: dict,
                         lib_src_dir: Path, lib_a: Path,
                         includes: list[str], max_rounds: int = 1,
                         n_samples: int = 3) -> list[str]:
    """
    For gap_fn:
      1. Build prompt (test snippets + signatures)
      2. Generate n_samples LLM responses
      3. For each: extract C code
      4. If O1 (compile) fails: build repair prompt with error message,
         regenerate once (max_rounds repair rounds)
      5. Return list of C sources that pass O1
    """
```

---

## 4.4 Stage 3: Oracles (`oracle.py`)

### Functions to implement:

```python
def check_o1_compile(harness_c: Path, lib_a: Path,
                     includes: list[str]) -> OracleResult:
    """
    Compile: clang -fsanitize=address,undefined,fuzzer
                   -Werror=implicit-function-declaration
                   harness.c lib.a -lz -lbz2 -llzma -lzstd
    Returns OracleResult(passed=bool, message=str).
    """

def check_o2_sanity(harness_bin: Path, seed_dir: Path,
                    max_total_time: int = 60) -> OracleResult:
    """
    Run: LLVM_PROFILE_FILE=/dev/null ./harness seed_dir/ -max_total_time=60
    Fail if: any ASan/UBSan report in stderr, any crash file written.
    Valid seeds = library's own test files + OSS-Fuzz public corpus.
    """

def check_o3_gap_hit(harness_cov_bin: Path, target_fn: str,
                     seed_dir: Path) -> OracleResult:
    """
    Run with coverage build for 60s. Export llvm-cov JSON.
    Fail if: target_fn execution count == 0 in the JSON.
    """

def check_o4_lint(harness_c: Path) -> OracleResult:
    """
    Parse C source (regex + simple AST). Check:
    1. Every call matching *_new/*_open/*_init has a matching *_free/*_close
       (in the goto-done / cleanup block)
    2. Every pointer return is checked for NULL before being passed to the
       next call
    3. Every len/size argument is computed from the actual buffer (not a
       hardcoded constant like 1024 while buf = d + pos with len = n - pos)
    """

def run_all_oracles(harness_c: Path, target_fn: str,
                    lib_a: Path, seed_dir: Path,
                    cov_build_dir: Path,
                    includes: list[str]) -> dict:
    """
    Run O1 → O2 → O3 → O4 in order (short-circuit on failure).
    Return dict with keys: o1,o2,o3,o4 (each: passed,message),
      plus: accepted (bool), reject_reason (str or None).
    Append to oracle_log.csv.
    """
```

### `oracle_log.csv` schema:
```
harness_file, target_fn, generator, O1, O2, O3, O4, accepted, reject_reason
harness_write.c, archive_write_set_format_by_name, G1, pass, pass, pass, pass, true,
llm_filter.c, archive_write_add_filter_by_name, G2, pass, fail, skip, skip, false, O2:asan_crash_on_valid_tar
```

---

## 4.5 Stages 4 & 5: Parametrization and Selection

### `param.py`:

```python
def parametrize(harness_c_src: str, api_model: dict) -> str:
    """
    Find all integer and string literal arguments in API call expressions.
    Replace with fuzz-derived equivalents.
    Re-uses the api_model's enum value tables for legal value arrays.
    Returns modified C source as string.
    
    Example transformations:
      archive_write_set_format_by_name(a, "zip")
      → archive_write_set_format_by_name(a, FMTS[d[pos++] % N(FMTS)])
    """
```

### `select.py`:

```python
def measure_gap_coverage(harness_bin: Path, seed_dir: Path,
                         gap_functions: list[str]) -> frozenset[str]:
    """
    Run harness_bin for 60s with coverage instrumentation.
    Return frozenset of gap function names that got count > 0.
    """

def select_harnesses(validated_dir: Path, gap_json: dict,
                     seed_dir: Path, max_k: int) -> list[Path]:
    """
    1. For each validated harness: measure_gap_coverage
    2. Build harness_gap_map: {path → frozenset of covered gap fns}
    3. greedy_set_cover(harness_gap_map, max_k)
    4. Copy selected harnesses to harnesses/<lib>/
    5. Return selected paths
    """
```

---

## 4.6 Build Infrastructure

### `targets/libarchive/build.sh`:

```bash
#!/usr/bin/env bash
# Build libarchive in 3 variants: cov (coverage), asan (fuzz), fast (AFL++ throughput)
# Usage: ./build.sh [cov|asan|fast]
set -euo pipefail

VARIANT=${1:-asan}
ROOT=$(git rev-parse --show-toplevel)
SRC=$ROOT/work/libarchive/src
OUT=$ROOT/work/libarchive/build-${VARIANT}
PINNED_COMMIT="v3.7.4"   # pin for reproducibility

# Clone at pinned version if not already present
if [ ! -d "$SRC/.git" ]; then
    git clone --depth 1 --branch $PINNED_COMMIT \
        https://github.com/libarchive/libarchive.git "$SRC"
fi

case $VARIANT in
  cov)   CC=clang CFLAGS="-O1 -fprofile-instr-generate -fcoverage-mapping -g" ;;
  asan)  CC=clang CFLAGS="-O1 -fsanitize=address,undefined -fno-omit-frame-pointer -g" ;;
  fast)  CC=afl-clang-fast CFLAGS="-O2" ;;
  *)     echo "Unknown variant: $VARIANT"; exit 1 ;;
esac

mkdir -p "$OUT"
cd "$SRC"
cmake -B "$OUT" \
  -DCMAKE_C_COMPILER="$CC" \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DENABLE_TEST=OFF -DENABLE_TAR=OFF -DENABLE_CPIO=OFF \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo .
cmake --build "$OUT" -j$(nproc) 2>&1 | tail -5
echo "[build] ✅ Done: $OUT/libarchive/libarchive.a"
```

---

## 4.7 Priority Order

### TONIGHT (before PM1):
```
Priority 1: python -m gapharn.gap libarchive        → get gap.json with REAL numbers
Priority 2: python -m gapharn.apimodel libarchive   → get api.json
Priority 3: compile harnesses/libarchive/harness_write.c → show running fuzzer
Priority 4: screenshot all outputs for PM1 slides
```

### THIS WEEK (Oct 15-18, after PM1):
```
Day 1-2: Create gen_rule.py (find_init_chain + emit_harness)
Day 2-3: Create oracle.py (O1 + O2 first — most important for correctness)
Day 3-4: Create gen_llm.py (build_prompt + call_llm with caching)
Day 4: Add O3+O4 to oracle.py, create param.py
```

### NEXT WEEK (Oct 19-25):
```
Week 2: Start fuzzing on lab PCs; daily triage; first upstream report by Oct 25
```

---

# 5. STEP-BY-STEP EXECUTION GUIDE

## 5.1 TONIGHT — Before Progress Meeting 1

### Step 1: Verify your environment (5 min)
```bash
# In WSL2 or Linux terminal:
clang --version          # Need 14+ 
python3 --version        # Need 3.10+
python3 -c "import clang.cindex; print('libclang OK')"
```
If libclang missing: `pip install libclang`

### Step 2: Build libarchive with coverage instrumentation (10-15 min)
```bash
cd cs5187-term-project-ayushan003/src
source .venv/bin/activate

# Clone libarchive (or use existing clone)
mkdir -p work/libarchive
git clone --depth 1 https://github.com/libarchive/libarchive.git work/libarchive/src

# Build coverage variant
mkdir -p work/libarchive/build-cov
cd work/libarchive/src
cmake -B ../build-cov \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="-O1 -fprofile-instr-generate -fcoverage-mapping -g" \
  -DENABLE_TEST=OFF -DENABLE_TAR=OFF -DENABLE_CPIO=OFF .
cmake --build ../build-cov -j$(nproc)

# Verify
ls ../build-cov/libarchive/libarchive.a && echo "✅ Build OK"
```

### Step 3: Run the existing OSS-Fuzz harnesses to populate coverage data (20 min)
```bash
cd cs5187-term-project-ayushan003/src
mkdir -p work/libarchive/existing-cov/json
mkdir -p seeds/libarchive

# Download/copy seed corpus from libarchive test directory
cp work/libarchive/src/libarchive/test/*.tar* seeds/libarchive/ 2>/dev/null || true
cp work/libarchive/src/libarchive/test/*.zip  seeds/libarchive/ 2>/dev/null || true
# Create minimal synthetic seeds:
printf 'ustar\0' > seeds/libarchive/min.tar
printf 'PK\x03\x04' > seeds/libarchive/min.zip

# If you have existing harness binaries compiled, run them:
# LLVM_PROFILE_FILE="work/libarchive/existing-cov/h1_%p.profraw" \
#   ./harness_read seeds/libarchive/ -max_total_time=60

# If not, create a minimal coverage JSON manually:
echo '{"data":[{"functions":[]}]}' > work/libarchive/existing-cov/json/empty.json
mkdir -p work/libarchive/existing-cov
echo '{"harnesses_total":4,"harnesses_measured":0,"fuzz_seconds_per_harness":60}' \
  > work/libarchive/existing-cov/META.json
```

### Step 4: Run the API model extractor (2 min)
```bash
cd cs5187-term-project-ayushan003/src
python -m gapharn.apimodel libarchive
# Output: results/libarchive/api.json
cat results/libarchive/api.json | python -m json.tool | head -40
```
**Expected output:** JSON with function entries including name, return type, params, role.

### Step 5: Run the gap analysis — THIS IS YOUR PM1 HEADLINE NUMBER (2 min)
```bash
python -m gapharn.gap libarchive
# Output: results/libarchive/gap.json + printed summary
```
**Expected output (example — your numbers will vary):**
```
libarchive: 247 API functions, 23 reached by existing harnesses, gap = 224 (90.7%)
  prefix                            api  reached     gap
  archive_write                      78        0      78
  archive_entry                      62        3      59
  archive_read_disk                  14        0      14
  archive_match                      14        0      14
  archive_read                       41       20      21
```
📸 **SCREENSHOT THIS — it goes on Slide 2 of your PM1 presentation.**

### Step 6: Compile the harness (5 min)
```bash
# Find the library and includes
LIB_A="work/libarchive/build-cov/libarchive/libarchive.a"
INCLUDES="-Iwork/libarchive/src"

# Compile harness_write.c
clang -fsanitize=address,fuzzer \
      $INCLUDES \
      harnesses/libarchive/harness_write.c \
      $LIB_A \
      -lz -lbz2 -llzma \
      -o harness_write 2>&1

echo "Exit: $?"   # Must be 0
```

If `-lzstd` fails: `sudo apt install libzstd-dev` or just remove `-lzstd`.

### Step 7: Run the harness for 30 seconds (LIVE DEMO)
```bash
./harness_write seeds/libarchive/ -max_total_time=30 -print_final_stats=1
```
📸 **SCREENSHOT the output — shows exec/s, coverage, no crashes.**

### Step 8: Collect all screenshots and note the numbers
Write these down on paper or in a notes file:
- [ ] `api_total = ____`
- [ ] `reached_total = ____`
- [ ] `gap_total = ____`
- [ ] `gap_ratio = ____`
- [ ] Top 3 gap prefixes: `____`, `____`, `____`
- [ ] Harness exec/s: `____`

---

## 5.2 MORNING OF PM1 (1 hour before meeting)

- [ ] Read all 43 Q&As in Section 6 of this document
- [ ] Practice the 6-minute talk from Section 7 out loud once
- [ ] Verify: `python -m gapharn.gap libarchive` still works
- [ ] Verify: `./harness_write seeds/libarchive/ -max_total_time=5` runs without errors
- [ ] Know the gap number by heart — you will be asked for it first
- [ ] Both team members have read everything in this document

---

## 5.3 WEEK 1 AFTER PM1 (Oct 15-18)

### Oct 15-16: Create gen_rule.py
```bash
# Create the file:
touch src/gapharn/gen_rule.py

# Implement in this order:
# 1. find_init_chain(api_model, target_fn_name) -> list[str]
# 2. fill_arg(param, offset, enum_values) -> (decl, expr)
# 3. emit_harness(gap_fn, chain, api_model) -> str
# 4. generate_all(gap_json, api_json, out_dir) -> [Path]
```
Test: `python -m gapharn.gen_rule libarchive --dry-run` should produce .c files in `/tmp/`

### Oct 16-17: Create oracle.py (O1 + O2 first)
```bash
touch src/gapharn/oracle.py

# Implement in this order:
# 1. check_o1_compile(harness_c, lib_a, includes) -> OracleResult
# 2. check_o2_sanity(harness_bin, seed_dir) -> OracleResult
# 3. run_all_oracles(...) -> dict  (O3/O4 can be stubs returning pass)
# 4. Write oracle_log.csv

# Test on your harness_write.c:
python -c "from gapharn.oracle import check_o1_compile; print(check_o1_compile(...))"
```

### Oct 17-18: Create gen_llm.py
```bash
# Install llama.cpp Python bindings or run llama-server:
pip install llama-cpp-python   # CPU build

# OR build llama-server separately:
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp && cmake -B build && cmake --build build -j

# Download model:
pip install huggingface_hub
huggingface-cli download Qwen/Qwen2.5-Coder-7B-Instruct-GGUF \
    --include "*q4_k_m*" --local-dir ~/models

# Test speed:
~/llama.cpp/build/bin/llama-bench -m ~/models/*.gguf -t $(nproc)
# Record the "tg" (token generation) tokens/s — goes in the paper
```

---

## 5.4 WEEK 2 (Oct 19-25) — First Bugs

### Oct 19-20: Start fuzzing campaign
```bash
# On Lab PC1:
git pull  # get latest code + harnesses
bash targets/libarchive/build.sh asan   # build with sanitizers
rsync -a work/libarchive/build-asan/ seeds/ pc2:~/gapharn/
rsync -a work/libarchive/build-asan/ seeds/ pc3:~/gapharn/
rsync -a work/libarchive/build-asan/ seeds/ pc4:~/gapharn/

# Start fuzzing on PC1 (harnesses set A):
tmux new -s fuzz_A
AFL_SKIP_CPUFREQ=1 afl-fuzz \
    -i seeds/libarchive -o out/write_tar \
    -M main -t 1000 \
    -- ./harnesses/harness_write_asan @@
# Ctrl+B D to detach
```

### Oct 21-24: Daily triage routine
```bash
# Every morning:
# 1. Check for crashes
ls out/*/crashes/ | grep -v README | head -20

# 2. For each crash: replay
./harness_write_asan crash_file

# 3. Minimize
afl-tmin -i crash_file -o min_crash -- ./harness_write_asan @@

# 4. Read the ASan trace carefully:
#    - Is the crash in libarchive code? (not in the harness)
#    - Does the harness violate any documented precondition?
#    - Can you reproduce with a standalone <30 line C program?

# 5. Classify: REAL or MISUSE
#    REAL: write to ~/gapharn-private/bugs/bugN/
#    MISUSE: fix the generator rule, add to oracle_log as FP
```

### Oct 25 (HARD DEADLINE): File first upstream report
```bash
# For confirmed REAL bugs:
# 1. Write standalone reproducer (plain C, <30 lines, no harness)
# 2. Find root cause: file:line in libarchive source
# 3. Write patch: usually a bounds check or NULL check addition
# 4. Test patch: apply and verify crash no longer reproduces
# 5. Open GitHub issue at https://github.com/libarchive/libarchive/issues
#    with: version, API sequence, reproducer (as code block), ASan trace,
#    root cause file:line, patch (as diff), "found by automated harness generation"
# 6. Update UPSTREAM.md: link, date filed, initial status = "open"
```

---

## 5.5 WEEK 3 (Oct 26-30) — PM2 + Evaluation

### PM2 Deliverables (must show at meeting):
- [ ] ≥ 20 validated harnesses (in `oracle_log.csv` with accepted=true)
- [ ] Coverage delta: branch % before vs after adding GapHarn harnesses
- [ ] At least 1 crash candidate with classification (Real or Misuse)
- [ ] RQ1 table: API functions reached OSS-Fuzz vs GapHarn
- [ ] At least 1 upstream report (link to GitHub issue)

### Coverage measurement for PM2:
```bash
# Build 3 configs (OSS-Fuzz only, GapHarn only, union) with coverage flags
# Run each for 1h (not 24h — for PM2 we show the direction)
LLVM_PROFILE_FILE="ossfuzz_%p.profraw" ./harness_read_cov seeds/ -max_total_time=3600
LLVM_PROFILE_FILE="gapharn_%p.profraw" ./harness_write_cov seeds/ -max_total_time=3600

llvm-profdata merge ossfuzz_*.profraw -o ossfuzz.profdata
llvm-profdata merge gapharn_*.profraw -o gapharn.profdata

llvm-cov report ./harness_write_cov --instr-profile=gapharn.profdata \
  --ignore-filename-regex=".*test.*" | grep TOTAL
```

---

## 5.6 WEEKS 4-5 (Nov 2-13) — Paper + Final

### Paper outline (complete in this order):
1. §3 Approach — architecture figure + stage descriptions (write while implementing)
2. §5 Evaluation — fill in RQ1-RQ5 tables as data becomes available
3. §6 Case study — one bug end-to-end (write when you have a confirmed bug)
4. §2 Background — libarchive, greybox fuzzing, fuzz harnesses (can do anytime)
5. §4 Implementation — LoC, LLM model, cluster setup (fill in at the end)
6. §7 Related work — comparison with OGHarn, PromptFuzz, libErator, UTopia
7. §8 Threats to validity — 4+ specific threats with mitigations
8. §1 Introduction — write last (most impactful numbers go here)

### Pre-submission checklist (Nov 12-13):
```bash
# 1. Verify one-command reproduction:
make repro LIB=libarchive     # must succeed on a clean checkout

# 2. Paper checks:
cd paper && make check         # zero TODOs, zero \focus{} boxes, zero undefined refs

# 3. Tag and push:
git tag -a final -m "Final submission: paper + code + reproducers"
git push origin main --tags

# 4. Verify public repo:
# Open GitHub in browser — confirm: code, harnesses, llm_cache, UPSTREAM.md all present
# Confirm CONTRIBUTIONS.md has signed statements from both members
```

---

## 5.7 Emergency Backup Plans

| Problem | What to do |
|---------|-----------|
| **No bug found by Oct 25** | Switch full attention to libtiff (secondary target already building). Report the coverage improvement as the main result — still a strong paper. |
| **All crashes are API misuse (100% FP)** | Strengthen O4 lint rules. Each misuse → fix generator rule → RQ4 data point. Add round-trip oracle for write API to hunt logic bugs. |
| **LLM too slow on CPU (<3 tokens/s)** | Drop G2 entirely. G1 alone is OGHarn's complete contribution. State as scope limitation. Project still gets full marks on approach and coverage. |
| **Lab PCs unavailable overnight** | Run on your laptop overnight with 1 AFL++ instance. State 12h runs as a threat to validity. |
| **Maintainers don't respond by Nov 5** | An "under review" issue still counts. Screenshot it. Write the bug table entry as "reported, pending response." |
| **Paper exceeds page limit** | Cut §4 Implementation detail (move to appendix or supplementary). Merge related-work paragraphs. |
| **Build fails on lab PCs** | Pin clang version in build.sh (`CC=clang-15`). Match exact version between laptop and lab (`clang --version`). |

---

# 6. CROSS QUESTIONS AND ANSWERS

> Every team member must be able to answer every question here without notes. Practice out loud.

---

## Section A — Core Concepts (Must know before PM1)

**Q1. What is a fuzz harness? Define it precisely.**

A fuzz harness (fuzz driver) is a small C program that: (1) accepts a raw byte array from the fuzzer engine via `LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)`, (2) uses those bytes as inputs to call one or more library API functions, (3) handles all library errors without crashing the harness itself (only library bugs should crash it), and (4) deterministically frees all resources so the fuzzer can restart immediately. It bridges the fuzzer's byte generator to the library's actual API.

---

**Q2. What is the API coverage gap? How exactly do you measure it?**

The API coverage gap = the set of functions that are: (a) declared in the library's public headers, AND (b) defined in the built library binary (verified via `nm`), BUT (c) have execution count = 0 in every llvm-cov JSON after running all existing harnesses on the corpus.

Measurement steps:
1. Build library with `-fprofile-instr-generate -fcoverage-mapping`
2. Run each existing harness: `LLVM_PROFILE_FILE=h.profraw ./harness corpus/ -max_total_time=60`
3. `llvm-profdata merge *.profraw -o merged.profdata`
4. `llvm-cov export ./harness --instr-profile=merged.profdata -format=text > cov.json`
5. Parse `cov.json` — extract functions where `count > 0` → "reached"
6. `nm -g --defined-only libarchive.a` → type T/W symbols → "exported"
7. `libclang` parse headers → "declared"
8. `GAP = (exported ∩ declared) − reached`

---

**Q3. Why is the API gap a security problem?**

Three reasons specific to libarchive: (1) libarchive processes attacker-controlled archive files. Even the write API can be triggered by untrusted data if an application reads a malformed archive and re-archives it. (2) Historical CVEs (CVE-2019-1000019, CVE-2021-36976) were in code paths the existing harnesses don't reach. (3) libarchive is used in FreeBSD, macOS, CMake — a memory corruption in the write path could be exploitable in any of these contexts.

General principle: untested code contains bugs proportional to its complexity. Complex C code with integer arithmetic (format header computation) and state machines (archive writer) is especially bug-prone.

---

**Q4. What is the difference between a harness and a unit test?**

| Property | Unit test | Fuzz harness |
|----------|-----------|-------------|
| Inputs | Fixed, known-good | Arbitrary byte stream |
| Assertions | Specific correct outputs | Only safety properties (no crashes, no memory errors) |
| Purpose | Verify correctness | Hunt for memory safety violations |
| Execution count | Runs once per test | Runs millions of times per hour |
| Coverage intent | Developer-chosen paths | Fuzzer-guided exploration of all paths |

---

**Q5. What is greybox fuzzing? Why "grey"?**

Three-point spectrum: blackbox (pure random inputs, no program knowledge), whitebox (full symbolic execution with constraint solving — slow), greybox (lightweight instrumentation as feedback — fast). AFL++ and libFuzzer are greybox: they instrument the binary to record **edge coverage** (which control-flow-graph edges were taken), use that as a fitness signal to guide mutation toward unexplored paths. "Grey" because they use more program information than blackbox (the coverage bitmap) but far less than whitebox (no path constraints, no SMT solving).

---

**Q6. What is the producer→consumer graph?**

A directed graph where: edge `f → g` exists if function `f` returns a pointer of type `T*` and function `g` takes a parameter of type `T*`. This encodes which functions can produce the inputs needed by other functions.

Example for libarchive:
```
archive_write_new() → struct archive *
                     ↓
archive_write_set_format_by_name(struct archive *, const char *)
archive_write_add_filter_by_name(struct archive *, const char *)
archive_write_open_memory(struct archive *, ...)
...
archive_write_free(struct archive *)
```

We find the shortest path from any initializer to each gap function using BFS. This path becomes the harness's call chain.

---

## Section B — GapHarn Pipeline

**Q7. Walk through all 8 stages of GapHarn in order.**

Stage 0 (Gap Analysis): measure which exported API functions OSS-Fuzz harnesses never execute → gap.json.
Stage 1 (API Model): parse headers with libclang → function signatures, roles, producer→consumer graph → api.json.
Stage 2 (Generation): G1 rule-based (init chain + status checks) for most functions; G2 LLM (prompt + repair loop) for complex functions G1 can't handle → candidate .c files.
Stage 3 (Oracles): O1 compile, O2 sanity run on valid seeds, O3 gap-hit check, O4 lint → oracle_log.csv; keep only passing harnesses.
Stage 4 (Parametrization): replace hardcoded constants with fuzz-derived values → re-run O1-O3.
Stage 5 (Selection): greedy set-cover over gap functions → K selected harnesses committed to git.
Stage 6 (Fuzzing): AFL++ distributed on 4 lab PCs, 24-48h campaigns, crashes collected hourly.
Stage 7 (Triage): stack-hash clustering, afl-tmin minimization, Real/Misuse classification, standalone reproducer.
Stage 8 (Reporting): GitHub issue + PR with patch; track in UPSTREAM.md.

---

**Q8. Explain the four correctness oracles. What does each catch that the others miss?**

**O1 (Compile):** `clang -fsanitize=address,undefined,fuzzer -Werror=implicit-function-declaration`. Catches: wrong function names (LLM hallucinations), type errors, missing includes, implicit function declarations. Rejection rate: typically 20-40% of LLM output.

**O2 (Sanity run on valid seeds):** Run harness for 60s on real archive files (well-formed TAR, ZIP, etc.). Reject if any ASAN/UBSan report. Catches: API misuse causing crashes on valid input (calling functions in wrong order, passing freed handles). This is OGHarn's key oracle — the main source of false positives in other tools.

**O3 (Gap-hit check):** Run with coverage build; check that the target gap function has execution count > 0. Catches: harnesses that compile and run fine but never actually call the function they were supposed to target (common if the chain build is wrong).

**O4 (Lint):** Python AST checks: every `*_new` has a matching `*_free`; every pointer return is NULL-checked before being passed on; every length argument is computed from the actual buffer. Catches: structural misuse patterns that O2 might miss on the specific seed corpus.

**Why four and not one?** Each oracle catches a different failure category. O2 can't catch structural issues that only manifest on rare inputs. O4 can't detect runtime crashes. O3 can't detect API misuse. Together they achieve OGHarn's 0% false-positive rate.

---

**Q9. What is a false positive in harness generation? Why does it matter so much?**

A false positive is a crash reported by a harness that is caused by the **harness misusing the API**, not by a bug in the library. Example: calling `archive_read_data()` before `archive_read_open_memory()` — libarchive aborts with an assertion. ASAN reports a crash. But the harness violated the documented API contract (you must open before reading). This is NOT a library bug.

Why it matters critically:
1. Upstream maintainers **close false-positive reports as INVALID** and may deprioritize future reports from the same source
2. A high FP rate means the fuzzer wastes time on non-bugs
3. OGHarn's 0% FP rate is its defining contribution and the reason we chose it as our backbone

---

**Q10. What is greedy set-cover selection and why do we use it?**

**The problem:** We have N validated harnesses. We can only fuzz K at a time (K ≈ available cores). Which K should we fuzz?

**Greedy set-cover:** Repeatedly select the harness that covers the most gap functions not yet covered by the selected set. Stop when all gap functions are covered, or K harnesses are selected. Achieves (1-1/e) ≈ 63% of optimal in linear time.

**Why not random?** Random selection might pick 10 harnesses all covering the same 5 archive_write_* functions, leaving archive_entry_*, archive_match_* entirely unexercised.

**Why not integer linear programming (optimal)?** NP-hard; not practical for 100+ harnesses.

**Direct benefit:** Maximizes RQ1 (API reach) with minimum fuzzing cores.

---

**Q11. What does parametrization do and why is it necessary?**

LLM-generated harnesses often hardcode constants: `archive_write_set_format_by_name(a, "zip")`. This means the fuzzer can mutate the raw bytes around the ZIP archive content but can never change "zip" to "tar" or "7zip" — those code paths are never explored.

Parametrization replaces the literal `"zip"` with `FMTS[d[pos++] % N(FMTS)]` where `FMTS` is an array of all legal format name strings. Now the fuzzer can explore all 15 format writers by mutating a single byte in the input. This dramatically expands the code coverage achievable from a single harness.

---

## Section C — The Four Reference Papers

**Q12. Explain OGHarn (ICSE 2025) in detail. What is its key contribution?**

OGHarn generates fuzz harnesses from library headers alone — no LLMs, no unit tests, no expensive static analysis. Key contributions:

1. **Role classification:** Labels each API function as initializer/data-entry/processor/destructor/auxiliary based on name patterns and signature structure. This enables automatic construction of valid API lifecycles.

2. **Init-chain construction:** BFS on producer→consumer graph finds the shortest valid call sequence from an initializer to any target function.

3. **Status-check insertion:** After every call, check pointer returns for NULL and int returns for < 0, then goto cleanup. This single rule eliminates most API-misuse false positives.

4. **Correctness oracles:** Compile check + sanity run on valid seeds. This achieves **0% false-positive rate** in their evaluation (compared to 40-100% for Hopper).

**Results:** 41 bugs found across 20 libraries, 40 fixed. Median +14% coverage over OSS-Fuzz.
**Our use:** G1 generator backbone. We adopt role classification, chain construction, status-check rules, and O1+O2 oracles. We add O3 (gap-hit) and O4 (lint) as GapHarn-specific extensions.

---

**Q13. Explain PromptFuzz (CCS 2024). What does "fuzzing the prompt" mean?**

Traditional fuzzing mutates **byte inputs**. PromptFuzz mutates **LLM prompts** using coverage as a fitness function.

Key techniques:
1. **Instructive program generation:** Structured prompts with API constraints force the LLM toward valid harnesses
2. **Erroneous program validation:** Oracle filtering removes incorrect harnesses (equivalent to our O1-O4)
3. **Coverage-guided prompt mutation:** Evolutionary algorithm evolves prompt variants toward generating harnesses that reach deeper code paths
4. **Constant→variable substitution:** Hardcoded values in LLM output are replaced with fuzz-controlled reads (what we call parametrization)

**Results:** 33 confirmed bugs across 14 libraries, 1.61× branch coverage vs OSS-Fuzz.

**Our use from PromptFuzz:** Prompt structure (G2), repair loop (one error-correction round), parametrization (Stage 4). We skip the evolutionary prompt mutation — too slow on local CPU model.

---

**Q14. Explain libErator (FSE 2025). What is CVE-2024-8006?**

libErator uses SVF (Static Value-Flow analysis) to infer valid API call sequences, then prunes broken sequences early, and selects diverse driver sets.

Key insight: **t_gen vs t_test.** Spending too long generating harnesses leaves too little time for fuzzing. LibErator balances time between generation and testing.

**CVE-2024-8006:** A buffer overflow in `pcap_findalldevs_ex()` — a libpcap API function that NO OSS-Fuzz harness had ever called. libErator found it by automatically generating a harness that called this gap function. This is the canonical example of why API gap targeting works: the bug was in production code, widely used, but invisible to existing fuzz testing because no harness ever called that function.

**Our use:** Greedy set-cover selection algorithm. We skip SVF analysis (7.5h for libtiff; OOM risk on 8 GB machines).

---

**Q15. Explain UTopia (S&P 2023) and why we don't implement it.**

UTopia converts existing unit tests → fuzz drivers using **root-definition analysis**: it finds where each variable in a unit test is defined (hardcoded constant like `size = 1024`) and where it's used (passed to an API call), then replaces the constant definition with a read from the fuzzer's byte stream.

**Results:** 123 bugs across 55 libraries.

**Why we don't implement UTopia's analysis:**
- Requires unit tests written with **gtest, boost, or TCT** frameworks
- libarchive's test suite uses a **custom framework** (`libarchive_test.h` with macros like `assertMakeFile`, `assertEqualInt`)
- Implementing our own def-use analysis for a custom C test framework is a separate research project
- **What we do instead:** Grep libarchive's `test/` directory for usage examples of the target function and paste 1-2 lines as context into the G2 LLM prompt. This captures the UTopia insight (real usage context) without the analysis complexity.

---

**Q16. What is Fuzz Introspector and how do we use it?**

Fuzz Introspector is an open-source Google tool that analyzes a compiled fuzzer binary and reports: which functions are reachable from existing harnesses, which are NOT reachable (the coverage gap), call graph depth, complexity. Available at `introspector.oss-fuzz.com` for OSS-Fuzz-enrolled projects.

We use it as **independent validation** of our gap measurements. If Fuzz Introspector shows the same functions as unreachable as our `gap.py` output, our measurement is correct. The professor may compare our numbers against Fuzz Introspector's report for libarchive.

---

## Section D — Technical Implementation

**Q17. What is ASAN? What bugs does it catch?**

AddressSanitizer: a compiler-based runtime memory error detector built into Clang/GCC. Maintains a shadow memory map (1 byte per 8 bytes of real memory) recording which bytes are valid. Overhead: ~2x runtime, ~2x memory.

Catches: **heap-buffer-overflow** (read/write past malloc'd region), **stack-buffer-overflow**, **use-after-free**, **use-after-return**, **double-free**, **use-after-scope**. Does NOT catch uninitialized reads (use MSAN for that).

Why essential for fuzzing: without ASAN, a buffer overflow silently corrupts memory and the program may appear to continue running. ASAN turns silent corruption into an explicit crash that the fuzzer's crash detector reports.

---

**Q18. What is UBSan? What does it add over ASAN?**

UndefinedBehaviorSanitizer: catches **undefined behavior** in C code. Catches: signed integer overflow, array out-of-bounds access (static arrays), null pointer dereference, misaligned memory access, invalid enum values, shift-by-negative, shift-by-bit-width. Overhead: ~5-10%.

Critical for C libraries because: C compilers are allowed to optimize away undefined behavior. A signed integer overflow that occurs in debug mode might be "optimized away" in release mode — the bug exists in the source but the sanitized build will catch it while a release build silently proceeds.

---

**Q19. libFuzzer vs AFL++: explain the differences.**

| Property | libFuzzer | AFL++ |
|----------|-----------|-------|
| Mode | In-process: calls `LLVMFuzzerTestOneInput` directly | Out-of-process: forks a new process per input |
| Speed | Faster (no fork overhead) | Slower per-input but more crash-resilient |
| Instrumentation | LLVM SanitizerCoverage (compile-time) | afl-clang-fast (compile-time) or QEMU (binary) |
| Coverage type | Edge coverage | Edge coverage + CMPLOG (comparison tracking) |
| Corpus format | Files in a directory | Files in `queue/` dir |
| Parallelism | Single process (or -fork=N) | Primary/secondary model (one -M main, many -S sN) |
| Harness compat | `LLVMFuzzerTestOneInput` | Same function (AFL++ uses the libFuzzer harness ABI) |

**Our approach:** Write harnesses with the `LLVMFuzzerTestOneInput` ABI (compatible with both). Use libFuzzer for rapid development iteration; AFL++ for the actual multi-day fuzzing campaign (CMPLOG helps find values that pass comparisons).

---

**Q20. Walk through the exact llvm-cov measurement pipeline.**

```bash
# Step 1: Build with coverage instrumentation
CC=clang CFLAGS="-fprofile-instr-generate -fcoverage-mapping" make

# Step 2: Run the instrumented binary
LLVM_PROFILE_FILE="run_%p.profraw" ./harness corpus/ -max_total_time=3600
# %p = PID, so parallel runs produce separate files

# Step 3: Merge profiles
llvm-profdata merge run_*.profraw -o campaign.profdata

# Step 4: Human-readable report
llvm-cov report ./harness --instr-profile=campaign.profdata \
    --ignore-filename-regex=".*test.*" \
    | grep -E "TOTAL|archive"
# Shows: lines, functions, branches (each: covered/total/%)

# Step 5: Machine-readable JSON (what gap.py parses)
llvm-cov export ./harness --instr-profile=campaign.profdata -format=text \
    > campaign_cov.json
# JSON structure: data[0].functions[] with name + count
```

---

**Q21. What is `nm` and how does it give us the API surface?**

`nm` (name list) lists symbols in object files and compiled binaries. We use: `nm -g --defined-only libarchive.a | awk '$2=="T" || $2=="W" {print $3}'`

- `-g`: show only global (externally visible) symbols
- `--defined-only`: exclude undefined references (external dependencies)
- `T`: text section (code), `W`: weak symbol

This gives us the ground truth of what the library *exports*. We intersect this with the functions *declared* in public headers (from libclang) to get the official public API. Some functions are defined in the library but not declared in headers (internal functions) — we exclude those since no caller can legally reach them.

---

**Q22. What is `afl-tmin` and when do you use it?**

`afl-tmin` is AFL++'s test case minimizer. Given a crash-triggering input file, it repeatedly removes bytes while checking that the crash still reproduces. Reduces a crash from potentially thousands of bytes to tens of bytes.

Usage: `afl-tmin -i crash_file -o minimized -- ./harness_asan @@`

We use it at the start of every triage session: minimize the crash input first, then read the ASan stack trace. The smaller input makes root cause analysis much easier and produces a cleaner upstream bug report.

---

**Q23. What is the round-trip oracle?**

Write an archive using the write API, then read it back with the read API, then assert that the pathname, file size, and file data match what was written:

```c
// After writing archive to out[0..used]:
struct archive *ra = archive_read_new();
archive_read_support_format_all(ra);
archive_read_open_memory(ra, out, used);
struct archive_entry *re;
archive_read_next_header(ra, &re);
if (strcmp(archive_entry_pathname(re), expected_name) != 0) abort();
if (archive_entry_size(re) != expected_size) abort();
archive_read_free(ra);
```

**What it finds:** Logic bugs where the write path produces internally inconsistent archives (write "succeeds" but the output is unreadable or corrupted), silent data corruption (filename silently truncated), format violations that don't crash but produce wrong output. These are bugs that neither read-only fuzzing nor write-only fuzzing would detect.

**Important:** Document which format limitations are *expected* behavior (ustar 100-byte filename limit is documented — truncation is NOT a bug). Only abort if the library violates its own documentation.

---

## Section E — Evaluation and Statistics

**Q24. Why do you need 3 runs of 24h each? Why not 1 run?**

Fuzzing is non-deterministic: the same harness on the same corpus can achieve different coverage in different runs (due to random seed selection, OS scheduler, memory layout randomization). A single run can be misleading. Standard practice in fuzzing research: 3-5 repetitions with different seeds, reporting median and IQR, applying Mann-Whitney U test for statistical significance. We use 3 repetitions due to hardware constraints (note as threat to validity).

---

**Q25. What is the Mann-Whitney U test? Why use it over a t-test?**

Mann-Whitney U is a non-parametric test comparing two independent samples by ranking the values. The t-test assumes normally distributed data. Fuzzing coverage data is typically skewed (rapid increase early, plateau later) and not normally distributed. Mann-Whitney U works on the *ranks* of the values, making no distribution assumption — appropriate for fuzzing coverage comparisons.

H0 for RQ2: No statistically significant difference in branch coverage between GapHarn and OSS-Fuzz baseline after 24h. We reject H0 if p < 0.05.

---

**Q26. What are your main threats to validity?**

1. **Internal — non-determinism:** Fuzzing randomness. Mitigation: 3 repetitions, Mann-Whitney U, report median+IQR.
2. **Internal — manual classification:** Real/Misuse classification is subjective. Mitigation: both members independently classify, resolve disagreements by consensus.
3. **External — generalizability:** Results for libarchive may not hold for all C libraries. Mitigation: secondary target (libtiff) provides a second data point.
4. **External — LLM non-determinism:** G2 may produce different harnesses if re-run. Mitigation: temperature=0, all responses cached in `llm_cache/`.
5. **Construct — coverage as proxy:** High coverage doesn't guarantee bugs found. Mitigation: report both coverage (RQ2) and bugs (RQ3) separately.
6. **Corpus age:** OSS-Fuzz public corpus may be stale. Mitigation: use the same corpus for both baselines; document corpus download date.

---

## Section F — Progress Meeting Questions

**Q27. What do you show at PM1? What is the rubric?**

PM1 rubric: *"Scope firmed up, target OSS accessible, initial pipeline running."*

Required to show:
1. **Scope:** The gap table with real numbers (api_total, reached_total, gap_total, gap_ratio)
2. **Accessible:** `python -m gapharn.apimodel libarchive` produces api.json; libarchive builds with ASAN
3. **Running:** `python -m gapharn.gap libarchive` produces gap.json; at least one harness compiles and runs for 30 seconds

---

**Q28. How is GapHarn different from just running OGHarn on libarchive?**

Four specific differences:
1. **Gap-directed generation:** We measure the API gap first, then generate harnesses ONLY for gap functions. OGHarn generates harnesses for all functions — not gap-targeted.
2. **Gap-coverage oracle (O3):** We add a specific oracle checking that the generated harness actually executes the target gap function. OGHarn has no oracle for this.
3. **LLM secondary generator (G2):** For gap functions that the rule-based approach can't handle (complex struct arguments, callbacks), we add an LLM-based generator. OGHarn is rule-based only.
4. **Diversity-based selection:** Greedy set-cover selects harnesses maximizing newly covered gap functions. OGHarn uses all passing harnesses.

---

**Q29. If you find no bug, is the project a failure?**

No. The project grading has multiple components:
- Bug found = highest marks on implementation (50%), but it's a stretch goal
- Coverage improvement + API reach (RQ1+RQ2) = still a strong contribution
- The automated pipeline itself = the primary contribution
- Paper quality = graded separately
- Presentation/explanation = graded separately

A project that finds 0 bugs but demonstrates +30% coverage improvement, has ≥20 validated harnesses, and produces a well-written paper can still receive excellent marks.

---

**Q30. How do you know a crash is a real bug and not API misuse?**

Three-step verification:
1. **Standalone reproducer test:** Write a 30-line standalone C program using ONLY documented API (no harness-specific code). If you can't reproduce the crash without violating a documented precondition, it's misuse.
2. **Latest-master test:** Reproduce on the latest `main` or `master` branch of the library. If it's already fixed, it's a known bug.
3. **Precondition audit:** Read the man page, header comment, and API documentation for every function in the call sequence. Does the crash require calling a function with undocumented arguments? Does it require calling functions in an order that the documentation says is invalid? If any documented precondition is violated: MISUSE. If no documented precondition is violated: REAL BUG.

---

## Section G — Deep Technical

**Q31. What is libclang and how do we use it?**

libclang is the C API to the Clang compiler's AST (Abstract Syntax Tree). We use it via Python bindings (`pip install libclang`). The Python API provides `clang.cindex.Index`, `TranslationUnit`, and `Cursor` objects.

We use cursors to walk the AST and extract:
- `CursorKind.FUNCTION_DECL` → function declarations with `cursor.spelling`, `cursor.result_type`, `cursor.get_arguments()`
- `cursor.raw_comment` → documentation comments
- `CursorKind.ENUM_DECL` → enumerations and their constant values
- `CursorKind.STRUCT_DECL` without definitions → opaque handle types

Why libclang over regex? libclang understands `#include`, `#ifdef`, complex type aliases, and all C preprocessor constructs. Parsing `archive.h` with regex would miss types defined in included files and conditional compilation blocks.

---

**Q32. What is the difference between static and dynamic analysis? Which components of GapHarn are each?**

**Static analysis** = analyzes the program without executing it (source code, AST, binary).
**Dynamic analysis** = analyzes the program by running it and observing behavior.

GapHarn components:
- Stage 0 (Gap): hybrid — nm and libclang are static; running harnesses to get coverage is dynamic
- Stage 1 (API model): static — libclang AST parsing
- Stage 2 (Generation): static — rule-based chain construction
- Stage 3 (O1): static — compiler invocation
- Stage 3 (O2, O3): dynamic — running harnesses
- Stage 3 (O4): static — Python checks on C source text
- Stages 6-7 (Fuzzing, Triage): dynamic — executing harnesses

Our paper is a **dynamic analysis** paper primarily (we find bugs by running generated harnesses), grounded in **static analysis** (we build the API model from headers without executing the library).

---

# 7. PROGRESS MEETING 1 — PRESENTATION SCRIPT

## 7.1 Meeting Requirements (PM1 Rubric)

> "Scope firmed up, target OSS accessible, initial pipeline running."

**You must demonstrate in 10 minutes:**
1. Scope: You know exactly what you're building and why — show the gap numbers
2. Accessible: libarchive can be built and instrumented
3. Running: `gap.py` and `apimodel.py` produce real output; one harness runs

---

## 7.2 Timing Guide

| Section | Time |
|---------|------|
| Title | 0:15 |
| The gap problem + numbers | 1:00 |
| GapHarn pipeline | 1:30 |
| Related work table | 0:45 |
| Live demo (3 parts) | 2:30 |
| Metrics + next steps | 1:00 |
| **Talk total** | **7:00** |
| Q&A | 3:00 |
| **Grand total** | **10:00** |

---

## 7.3 Slide-by-Slide Script

### SLIDE 1 — Title (0:15)

> "Good [morning/afternoon]. We're presenting GapHarn — gap-directed fuzz-harness generation for C libraries. I'm [Name] and this is [Partner]. Our target is libarchive."

---

### SLIDE 2 — The Gap Problem (1:00)

**Show the gap table (fill in your actual numbers from gap.json):**

```
libarchive: [N] API functions total
            [M] reached by the 4 existing OSS-Fuzz harnesses
            GAP = [N-M] functions ([X%] of the API)

  archive_write_*      78 functions     0 reached     78 in gap
  archive_entry_*      62 functions     3 reached     59 in gap
  archive_read_disk_*  14 functions     0 reached     14 in gap
  archive_match_*      14 functions     0 reached     14 in gap
```

**Talking points:**
> "This is the A1 problem in concrete numbers. Fuzzing is not the bottleneck — the harness is. libarchive has been enrolled in Google's OSS-Fuzz for 7 years. In those 7 years, engineers wrote exactly 4 harnesses for it. All 4 test the read path — parsing archives. The entire write API — all the archive_write_* functions — has zero execution count in OSS-Fuzz's coverage data."

> "This matters because libarchive is used in FreeBSD's tar, macOS's Archive Utility, and CMake. Historical CVEs like CVE-2021-36976 were in code paths these 4 harnesses cannot reach. And the write path is full of integer arithmetic for computing archive header offsets and sizes — exactly the kind of code where buffer overflows hide."

---

### SLIDE 3 — GapHarn Pipeline (1:30)

**Show this ASCII diagram:**

```
Stage 0: Gap Analysis    → measure: which functions are never executed?
Stage 1: API Model       → parse headers: signatures, roles, call graph
Stage 2: Generate        → G1 rule-based + G2 LLM (only for hard cases)
Stage 3: Oracle Filter   → compile ✓ + valid-seed run ✓ + gap-hit ✓ + lint ✓
Stage 4: Parametrize     → hardcoded constants → fuzz-derived values
Stage 5: Select          → greedy set-cover → K diverse harnesses
Stage 6: Fuzz            → AFL++ on 4 lab PCs, 24-48h campaigns
Stage 7: Triage          → cluster → minimize → Real/Misuse classify
Stage 8: Report          → GitHub issue + patch + regression test
```

**Talking points:**
> "Our key novelty is Stages 0 and 3-5. Prior work generates harnesses for all functions. We generate harnesses only for the gap functions — the ones nobody tests. And we add Oracle O3, which rejects any harness that doesn't actually call the target gap function. This is what makes GapHarn gap-directed rather than just harness-directed."

> "The pipeline is grounded in four papers from the reading list. G1 — our rule-based generator — implements OGHarn's algorithm from ICSE 2025, which achieves 0% false-positive rate. G2 — the LLM generator for complex cases — uses PromptFuzz's approach from CCS 2024 with a local free model. The selection algorithm is from libErator at FSE 2025."

---

### SLIDE 4 — Related Work Table (0:45)

| Paper | Our use |
|-------|---------|
| OGHarn (ICSE'25) — 0% FP rate | G1 backbone + O1/O2 oracles |
| PromptFuzz (CCS'24) — LLM harnesses | G2 prompt structure + repair loop |
| libErator (FSE'25) — diversity selection | Greedy set-cover selection |
| UTopia (S&P'23) — unit test conversion | Test snippets in G2 prompts only |

> "We don't implement UTopia's analysis because libarchive uses a custom test framework, not gtest. We borrow the test-snippet idea instead."

---

### SLIDE 5 — Live Demo (2:30)

#### Demo 1: Gap analysis — YOUR HEADLINE NUMBER (1:00)
```bash
cd cs5187-term-project-ayushan003/src
python -m gapharn.gap libarchive
```

> "This is Stage 0. It's comparing the functions exported by libarchive — [N] total — against the functions the existing OSS-Fuzz harnesses execute. The output shows [gap_total] functions in the gap, [gap_ratio*100]% of the API. The archive_write_* prefix alone has [W] functions, all with zero execution count."

📸 **This output goes on slide 2 — make sure it matches your prepared slide.**

#### Demo 2: API model (0:45)
```bash
python -m gapharn.apimodel libarchive
cat results/libarchive/api.json | python -m json.tool | head -30
```

> "This is Stage 1. Our static analysis frontend parsed all libarchive public headers using libclang and extracted [N] function entries. For each function we have the signature, parameter types, doc comment, and role classification — initializer, data-entry, processor, or destructor. The role classification is how we know to call archive_write_new() before archive_write_set_format_by_name()."

#### Demo 3: Harness running (0:45)
```bash
# If harness compiles:
./harness_write seeds/libarchive/ -max_total_time=30 -print_final_stats=1
```

> "And here's a harness from Stage 6 running. This targets the archive_write_* gap — specifically archive_write_set_format_by_name and archive_write_add_filter_by_name, which are two of the [W] write-path functions in our gap. The fuzzer has run [X] inputs in 30 seconds, no crashes on valid seeds — which is what Oracle O2 guarantees."

**If the harness didn't compile in time, show the source:**
```bash
cat harnesses/libarchive/harness_write.c
```
> "Here's the harness. You can see the complete lifecycle: archive_write_new → set_format → add_filter → open_memory → write_header → write_data → close → free. Every return value is checked with goto-done cleanup. This is OGHarn's status-check pattern — it's what gives us the 0% false-positive rate."

---

### SLIDE 6 — Metrics and Next Steps (1:00)

**Evaluation targets:**

| RQ | Metric | Tool | Target |
|----|--------|------|--------|
| RQ1 | API functions reached | llvm-cov | ≥ 60% of exported symbols |
| RQ2 | Branch coverage delta | llvm-cov, 3×24h | +15% vs OSS-Fuzz |
| RQ3 | Bugs confirmed upstream | GitHub | ≥ 1 acknowledged |
| RQ4 | Oracle rejection rate | oracle_log.csv | Measured and reported |
| RQ5 | G1 vs G1+G2 ablation | 1h runs each | Measured and reported |

**By Progress Meeting 2 (Oct 26-30), we will have:**
> "≥ 20 auto-generated harnesses passing all 4 oracles, a coverage delta chart, first crash triage results, and at least one upstream bug report filed on GitHub."

---

## 7.4 Anticipated Q&A (Prepare these answers)

**Q: "What is your gap number exactly?"**
A: [Read from gap.json output — api_total, reached_total, gap_total, gap_ratio]

**Q: "How is this different from OGHarn?"**
A: "Four differences: (1) gap-directed — we generate only for gap functions, not all functions; (2) Oracle O3 — we check the harness actually calls the target; (3) G2 LLM generator for complex functions G1 can't handle; (4) greedy set-cover selection to maximize diversity."

**Q: "Why libarchive?"**
A: "Large measured gap in the write path, security-critical archive parsing, active maintainers who respond within 7 days, and it's on the professor's example list."

**Q: "How do you ensure no false positives?"**
A: "Four correctness oracles. Most importantly Oracle O2: we run every harness for 60 seconds on real archive files. If it crashes on valid input, it's API misuse — we reject it. This is OGHarn's key insight that achieves 0% false-positive rate."

**Q: "What happens if you find no bugs?"**
A: "The primary contribution is the gap-directed pipeline and coverage measurement. Finding bugs is a stretch goal. But given that [X%] of libarchive's API has never been fuzz-tested and the write path has complex integer arithmetic, we're optimistic."

---

## 7.5 Morning-of Checklist (Oct 15)

- [ ] `python -m gapharn.gap libarchive` runs and shows gap numbers
- [ ] `python -m gapharn.apimodel libarchive` runs and shows api.json
- [ ] `./harness_write` compiles (or have source code ready to show)
- [ ] Gap numbers written down by hand (in case laptop demo fails)
- [ ] Both team members have read all 32 Q&As in Section 6
- [ ] Laptop charged, repo cloned, virtual environment activated
- [ ] Teams link for the meeting bookmarked

---

## 7.6 Demo Fallback Plan

**If `python -m gapharn.gap` crashes:**
> "Let me show you the output we collected earlier" → show the screenshot you prepared last night

**If the harness won't compile:**
> "The harness source shows the complete lifecycle" → show `cat harnesses/libarchive/harness_write.c` on screen

**If api.json doesn't exist:**
> "The API model is running — while it completes, let me show you the gap analysis output" → pivot to gap.json

**If everything breaks:**
> "I'll describe what the pipeline produces" → speak to the architecture diagram confidently, citing real numbers from your prepared notes

**Golden rule:** If something breaks, explain what it would show. The professor expects software to be occasionally flaky. What matters is that you understand the system.

---

# QUICK REFERENCE CARD
## (Read this the night before every meeting)

| Term | Meaning in 10 words |
|------|---------------------|
| Fuzz harness | C glue between fuzzer bytes and library API |
| API gap | Exported functions with zero execution in any harness |
| OGHarn | Rule-based harness gen, 0% false-positive rate |
| PromptFuzz | Evolves LLM prompts via coverage-guided mutation |
| libErator | SVF-based harness gen with diversity selection |
| UTopia | Unit-test-to-harness via def-use slicing (gtest only) |
| Producer→consumer | Edge f→g if f returns T* and g takes T* |
| Role classification | initializer / data-entry / processor / destructor / aux |
| ASAN | Compiler plugin that catches memory errors at runtime |
| UBSan | Catches undefined behavior (overflow, null deref, etc.) |
| AFL++ | Out-of-process coverage-guided greybox fuzzer |
| libFuzzer | In-process LLVM fuzzer (same harness ABI as AFL++) |
| llvm-cov | Collects and reports source-based coverage |
| libclang | Python-accessible Clang AST for parsing C headers |
| nm | Lists symbols defined in object files and libraries |
| O2 oracle | Crash on valid seed = API misuse, reject harness |
| O3 oracle | Must execute the target gap function |
| Greedy set-cover | Select harnesses maximizing newly covered gap fns |
| Parametrization | Replace hardcoded literals with fuzz-derived reads |
| afl-tmin | Minimizes crash inputs to smallest reproducing form |
| Round-trip oracle | Write archive, read back, assert data matches |
| Mann-Whitney U | Non-parametric test for comparing coverage distributions |
| CVE | Public security vulnerability identifier (MITRE database) |
| Fuzz Introspector | Google tool showing which functions are unreachable |
| False positive | Crash caused by harness misuse, not a library bug |
| Stack hash | Crash dedup key = hash of top 3 library stack frames |
| t_gen vs t_test | libErator insight: balance time between gen and fuzzing |
| Temperature=0 | LLM greedy decoding — deterministic, reproducible |
| llm_cache | All G2 LLM responses cached; no rerun needed for repro |
| UPSTREAM.md | Tracks every bug report: link, date, status |

---

*End of GAPHARN_MASTER.md*
*Last updated: Oct 15, 2026*
*This document covers: Proposal · Ideation · Architecture · Implementation Plan · Steps · Cross Questions · PM1 Presentation*
