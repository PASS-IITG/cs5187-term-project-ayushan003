# GapHarn ? Ideation & Design Decisions
## Architectural Rationale, Conceptual Hypotheses, and Trade-off Analysis

---

## 1. Formulation of the Core Thesis

### The Fundamental Flaw of Greybox Library Fuzzing
In contemporary security research, significant effort is devoted to algorithmic enhancements: power schedules (AFLFast), mutation dictionaries, grammar inference, and constraint solving (QSYM). However, applying state-of-the-art fuzzers directly to modern C shared libraries reveals a stark reality:

$$\text{Fuzzing Coverage} \le \text{Reachability}(\text{Harness Suite})$$

If an exported function is not invoked anywhere within a fuzz driver, its mutation engine cannot reach it regardless of compute scale. Most libraries rely on ad-hoc harnesses written by external contributors. The resulting coverage distribution is severely skewed toward simple decompressors and standard file readers.

### The Gap-Directed Thesis
**Hypothesis:** By statically identifying the unexercised API surface (the *API Gap*) and selectively synthesizing harnesses *only* for symbols within this gap, we can maximize code coverage velocity and expose vulnerabilities in complex, under-audited stateful subsystems.

---

## 2. Target Library Evaluation & Selection Matrix

We evaluated potential candidate targets against five strict criteria:
1. Security criticality and real-world usage footprint.
2. Presence of a substantial, measurable API gap in OSS-Fuzz.
3. C codebase with stateful handle interactions.
4. Active upstream maintainer responsiveness.
5. Feasible build compilation time on available test hardware.

| Candidate Library | Pros | Cons | Final Decision |
| :--- | :--- | :--- | :--- |
| **libarchive** | Used in macOS, FreeBSD, CMake; ~250 public APIs; OSS-Fuzz exercises only 4 read harnesses; entire write/ACL path untouched. | Highly stateful API requiring precise sequence ordering. | **SELECTED (Primary Target)** |
| **libtiff** | Ubiquitous image dependency; documented history of deep API vulnerabilities (libErator found 6 bugs). | Complex custom tag dictionaries; build system sensitive. | **SELECTED (Secondary Target)** |
| **libxml2** | Critical parser; huge symbol table. | Main parser routines already saturated by intensive OSS-Fuzz targets. | **Retained as Backup** |
| **OpenSSL** | Massive security impact. | Saturated by thousands of continuous fuzzing hours; maintainers reject reports lacking full crypto exploitability. | **REJECTED** |
| **FFmpeg Demuxers** | Rich parsing attack surface. | Compilation times exceed 20 minutes on dual-core/quad-core machines; risks compute bottleneck. | **REJECTED** |

---

## 3. Comparative Literature Synthesis & Methodological Heritage

### 3.1 Why OGHarn (ICSE 2025) as the G1 Backbone?
- **Zero False-Positive Rate:** Hopper and earlier tools suffered from 40%?100% false-positive crash rates due to illegal API misuse. OGHarn achieved 0% false positives across 20 libraries by coupling return-value status assertions with a 60-second sanity execution on valid seeds.
- **Resource Efficiency:** Operates purely on C headers via AST parsing; does not require GPU infrastructure or proprietary cloud LLM tokens.

### 3.2 Why PromptFuzz (CCS 2024) for G2?
- **Contextual Synthesis:** Rule-based generators struggle with multi-struct initialization, custom allocators, or nested callback structures. PromptFuzz demonstrated that LLMs excel at stitching together multi-object dependencies when provided with adjacent API signatures and docstrings.
- **Cost Minimization Adaptation:** Rather than spending hundreds of dollars on GPT-4 APIs, we adapted PromptFuzz principles to run against local, quantized open-weight models (`Qwen2.5-Coder-7B-Instruct` via `llama.cpp`) at zero cost.

### 3.3 Why libErator (FSE 2025) for Driver Selection?
- **Preventing Fuzzing Dilution:** Generating 100 individual harnesses fragments fuzzing compute time. libErator introduced diversity-based driver selection to balance generation time ($t_{\text{gen}}$) against test execution time ($t_{\text{test}}$). We adapted this as a fast **Greedy Set-Cover** algorithm.

### 3.4 Why Reject UTopia (S&P 2023)?
- UTopia transforms existing unit tests into fuzz drivers via def-use variable slicing. However, UTopia is tightly bound to GoogleTest and Boost.Test C++ frameworks. Mature C libraries like `libarchive` use custom test frameworks (`libarchive_test.h`), rendering UTopia's LLVM slicing pipeline inapplicable. We borrow only UTopia's high-level concept: mining test files for code snippets to seed LLM prompts.

---

## 4. Why Four Correctness Oracles?

In automated harness generation, the primary hazard is **API Misuse**:
- Calling a processor function on a NULL handle.
- Failing to call format initializers before writing headers.
- Passing buffer lengths inconsistent with allocated memory.

Such harnesses trigger assertion failures or segfaults that maintainers will promptly dismiss as invalid. To ensure academic rigor and practical utility, every candidate harness must clear four oracles:

1. **O1 (Compile):** Ensures lexical, syntactic, and typing validity.
2. **O2 (Sanity on Clean Seeds):** If a harness crashes while executing valid, well-formed archive seeds, it is guilty of API misuse and is rejected.
3. **O3 (Gap Hit):** Confirms via dynamic coverage profiling that the harness actually executes the designated target gap function.
4. **O4 (Lifecycle Linting):** Confirms that every allocated handle has a corresponding destructor call, preventing spurious leak reports under LeakSanitizer.
