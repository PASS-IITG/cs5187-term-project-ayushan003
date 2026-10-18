# GapHarn ? Cross-Examination & Viva Master Q&A
## Comprehensive Question Bank for Progress Meetings & Final Defense
### CS 5187 Software Analysis ? Monsoon 2026 ? IIT Guwahati

> **Mandatory Policy:** Per course guidelines, each team member must be prepared to answer questions on any aspect of the project. Responses like "That was my partner's part" result in an immediate penalty.

---

## 1. Problem Statement & Motivation

#### Q1: What is a fuzz driver/harness? Why is it necessary?
**Answer:** A fuzz harness is a dedicated C/C++ interface that ingests an arbitrary sequence of raw bytes provided by a fuzzing engine (`const uint8_t *data, size_t size`), converts those bytes into structured parameters or file representations, and passes them to target library APIs. It is strictly required because greybox fuzzers like AFL++ or libFuzzer mutate flat byte arrays; without a harness orchestrating the initialization, execution, and cleanup of the library's state, the fuzzer cannot execute the target code.

#### Q2: What is the "API Gap", and how is it calculated?
**Answer:** The API Gap consists of all public functions declared in a library's headers and exported as defined text symbols in its binary (`nm` symbol types `T` or `W`) that have an execution count of zero when existing fuzz targets are run against their comprehensive test corpus. Mathematically:
$$\mathbf{GAP} = (\text{Exported Symbols} \cap \text{Declared Symbols}) \setminus \text{Executed Symbols}$$

#### Q3: Why does Google OSS-Fuzz only have 4 harnesses for libarchive?
**Answer:** Writing fuzz harnesses for complex libraries requires extensive manual domain expertise. Developers must understand state machine preconditions, configuration orders, valid argument ranges, and teardown contracts to avoid spurious false-positive crashes. Consequently, OSS-Fuzz contributors wrote harnesses covering only the most obvious entry points?namely, reading and decompressing archive files (`archive_read_*`). The complex archive generation subsystem (`archive_write_*`) was left unexercised due to the engineering effort required to handcraft valid state lifecycles.

#### Q4: Why is the write path in libarchive security-critical if attackers usually submit read files?
**Answer:** First, many applications accept untrusted inputs that dictate how archives are created or compressed (e.g., untrusted filenames, permissions, custom extended attributes, and format types in backup tools, package managers, and server-side archive generators). Second, writing archives involves complex integer arithmetic for block alignments, central directory records, and stream filters where integer overflows lead directly to heap buffer corruptions. Third, archive generation logic bugs can output corrupt or malformed archives that exploit downstream parsers.

#### Q5: What is the fundamental difference between a unit test and a fuzz harness?
**Answer:** A unit test executes an API with deterministic, static, known-valid inputs and asserts functional correctness against expected return values (`assert(f(2) == 4)`). A fuzz harness accepts non-deterministic, potentially adversarial byte streams, executes the API under runtime sanitizers (ASan/UBSan), and verifies safety invariants (i.e., that no sequence of inputs causes memory corruption, undefined behavior, or resource exhaustion), without asserting specific output values.

---

## 2. Pipeline Design & Mechanics

#### Q6: Explain the role classification heuristics in `apimodel.py`.
**Answer:** We categorize API functions into five functional roles:
1. **Initializer:** Returns an opaque handle pointer ($T^*$), accepts no handle as input, and matches naming patterns like `*_new` or `*_open`.
2. **Data-Entry:** Ingests a handle alongside a contiguous buffer pointer (`void*`, `char*`) and length (`size_t`), feeding fuzzer data into the library.
3. **Destructor:** Ingests a handle and returns `void` or `int`, matching `*_free` or `*_close`.
4. **Processor:** Ingests an initialized handle, mutates internal state or configures options, and returns a status code.
5. **Auxiliary:** Utility functions, stateless getters, or format converters.

#### Q7: How does GapHarn prevent API Misuse and false-positive crashes?
**Answer:** Earlier tools suffered from high false-positive rates because they called functions out of order or with NULL handles. GapHarn enforces three layers of defense:
1. **Producer-Consumer Ordering:** Functions are only invoked in sequences derived from the reachability path in the API call graph (Initializer $\to$ Processor $\to$ Data-Entry $\to$ Destructor).
2. **Defensive Status Checks:** Following OGHarn, every pointer return is verified (`if (!ptr) goto cleanup;`) and every integer status code is checked (`if (status != ARCHIVE_OK) goto cleanup;`).
3. **Oracle O2 (Sanity Run):** Every synthesized harness is executed for 60 seconds against known-valid seeds. If it crashes on valid input, it is definitively flagged as API misuse and discarded.

#### Q8: How does the Greedy Set-Cover selection algorithm work?
**Answer:** Fuzzing 100 individual harnesses concurrently dilutes compute power. Stage 5 takes all oracle-validated candidate harnesses and iteratively selects the harness that yields the largest marginal increase in newly reached gap functions. This continues until all gap functions are covered or a predefined cluster capacity $K$ (e.g., 12?16 harnesses) is reached, ensuring maximal API diversity with minimal compute fragmentation.

#### Q9: What is dynamic argument parametrization (Stage 4)?
**Answer:** Initial LLM or template harnesses frequently hardcode literal values (e.g., always passing `"zip"` or fixed integer flags). Parametrization parses the AST to identify literal call arguments and rewrites them to derive their values dynamically from the fuzzer byte stream via modulo indexing into mined enum/constant arrays (`FMTS[d[0] % N(FMTS)]`), converting a single-path harness into a multipath explorer.

---

## 3. Related Work & Research Grounding

#### Q10: How does GapHarn differ from OGHarn (ICSE 2025)?
**Answer:** OGHarn synthesizes harnesses uniformly across the entire library header without measuring what is already tested. GapHarn introduces **gap-directed targeting**: we explicitly measure the unreached API surface of existing harnesses and target only the gap. Additionally, GapHarn introduces an LLM fallback generator (G2) for complex structs and uses diversity-based set-cover selection, whereas OGHarn relies solely on static header rules and manual harness grouping.

#### Q11: Why didn't we use UTopia (S&P 2023)?
**Answer:** UTopia generates fuzz drivers by applying program slicing and def-use analysis to existing C++ unit tests. However, UTopia is fundamentally dependent on GoogleTest and Boost.Test frameworks. C libraries like `libarchive` and `libtiff` use custom C test suites (`libarchive_test.h`), making UTopia's LLVM slicing pipeline technically incompatible.

#### Q12: How does GapHarn relate to PromptFuzz (CCS 2024)?
**Answer:** PromptFuzz uses an evolutionary algorithm to mutate LLM prompts to maximize code coverage, calling commercial cloud LLMs (GPT-3.5/4). GapHarn adapts PromptFuzz's contextual prompt design and repair loop, but runs on a local, quantized open-weight model (`Qwen2.5-Coder-7B`) at zero cost, and pairs it with strict static analysis grounding to eliminate hallucinations.

#### Q13: What did libErator (FSE 2025) demonstrate regarding driver selection?
**Answer:** libErator demonstrated the critical trade-off between generation time ($t_{\text{gen}}$) and testing time ($t_{\text{test}}$). Generating too many redundant drivers starves the fuzzer of the execution time needed to uncover deep stateful bugs. Their discovery of CVE-2024-8006 in `libpcap` proved that targeting uncalled API functions (`pcap_findalldevs_ex`) yields high-severity 0-day bugs. GapHarn directly builds on this insight.

---

## 4. Compilers, Sanitizers & Fuzzing Mechanics

#### Q14: What is AddressSanitizer (ASan) and what specific vulnerabilities does it detect?
**Answer:** ASan is a compiler-based instrumentation tool that tracks memory access via shadow memory (mapping 8 bytes of application memory to 1 shadow byte). It detects:
- Out-of-bounds heap, stack, and global buffer overflows.
- Use-after-free (dangling pointer dereferences).
- Use-after-return and use-after-scope.
- Double-free and invalid free operations.

#### Q15: Why run both ASan and non-ASan fuzzer instances in AFL++?
**Answer:** ASan introduces approximately $2\times$ CPU overhead and up to $3\times$ virtual memory overhead. In a distributed multi-core campaign, running one master ASan instance (`-M main`) allows accurate detection of memory corruptions, while running auxiliary non-sanitized or CMPLOG instances (`-S s1`) maximizes overall executions per second, rapidly exploring branch coverage and passing seeds back to the ASan instance.

#### Q16: How do we measure branch coverage using LLVM tools?
**Answer:** We compile the target with `-fprofile-instr-generate -fcoverage-mapping`. When the harness executes inputs, it dumps `.profraw` coverage traces. We merge these traces using `llvm-profdata merge` and generate line/branch coverage summaries or JSON exports via `llvm-cov export` or `llvm-cov report`.

#### Q17: What is `afl-tmin` and why is minimization essential before reporting upstream?
**Answer:** `afl-tmin` is AFL++'s test-case minimizer. A crashing input discovered during fuzzing may be tens of kilobytes in length, containing mostly irrelevant mutation artifacts. `afl-tmin` iteratively removes bytes while ensuring the crash stack trace remains identical, shrinking the input down to the minimal sequence of bytes necessary to trigger the fault. Upstream maintainers demand minimal inputs to facilitate root-cause debugging.

---

## 5. Statistical Rigor & Threats to Validity

#### Q18: Why is a single 24-hour fuzzing run insufficient for scientific evaluation?
**Answer:** Fuzzing is an inherently stochastic process governed by pseudo-random mutation choices and initial scheduling nondeterminism. A fuzzer may achieve high coverage in one run purely due to luck. Standard scientific evaluation (Klees et al., *Evaluating Fuzz Testing*) mandates running at least 3 to 5 independent repetitions with distinct seeds and evaluating differences using non-parametric statistical tests like the **Mann-Whitney U test**.

#### Q19: What are the main threats to the validity of GapHarn?
**Answer:**
1. **Internal Validity:** Nondeterminism of fuzzing seeds and potential subjectivity in manual crash triage (mitigated by independent multi-member triage and strict adherence to documented preconditions).
2. **External Validity:** Evaluating primarily on `libarchive` may not generalize to all C libraries (mitigated by evaluating secondary targets like `libtiff`).
3. **Construct Validity:** Branch coverage is a proxy, not a direct guarantee of vulnerability detection (mitigated by prioritizing upstream-accepted bug reports).
