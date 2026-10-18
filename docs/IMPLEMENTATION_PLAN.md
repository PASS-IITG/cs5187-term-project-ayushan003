# GapHarn ? Implementation Plan & Engineering Roadmap
## Module-by-Module Technical Tasks, Function Signatures, and Verification Milestones

---

## 1. Codebase Organization

The GapHarn toolchain resides in `cs5187-term-project-ayushan003/src`:

```
src/
??? gapharn/                   # Core Python package
?   ??? __init__.py            # Package metadata
?   ??? config.py              # YAML target configuration parser
?   ??? gap.py                 # [COMPLETED] Stage 0: Gap analyzer
?   ??? apimodel.py            # [COMPLETED] Stage 1: libclang AST & role classifier
?   ??? gen_rule.py            # [IN PROGRESS] Stage 2: G1 Rule-based generator
?   ??? gen_llm.py             # [IN PROGRESS] Stage 2: G2 LLM generator & cache
?   ??? oracle.py              # [IN PROGRESS] Stage 3: O1-O4 verification pipeline
?   ??? param.py               # [PLANNED] Stage 4: Dynamic fuzz parametrization
?   ??? select.py              # [PLANNED] Stage 5: Greedy set-cover harness selector
?   ??? triage.py              # [PLANNED] Stage 7: Crash deduplication & minimizer
?   ??? seeds.py               # Seed corpus extractor and manager
?   ??? compare.py             # Statistical evaluation & coverage plots (RQ1/RQ2)
??? harnesses/                 # Selected, validated C fuzz harnesses
?   ??? libarchive/
?       ??? harness_write.c    # Write-path primary gap harness
?       ??? harness_entry.c    # Entry metadata/ACL gap harness
??? targets/                   # Library-specific configuration & build hooks
?   ??? libarchive/
?   ?   ??? config.yaml        # Pinned git commit, headers, prefixes
?   ?   ??? build.sh           # Builds cov, asan, and fast binaries
?   ??? libtiff/
??? scripts/                   # Cluster operations & runner scripts
?   ??? check_env.sh           # Comprehensive host diagnostic suite
?   ??? worker.sh              # AFL++ worker launch script
?   ??? collect.sh             # Cluster log and crash synchronization
??? templates/                 # Jinja2 harness synthesis templates
?   ??? harness_g1.c.j2
??? tests/                     # Automated pytest suite
    ??? test_gap.py
    ??? test_apimodel.py
    ??? test_oracles.py
```

---

## 2. Stage-by-Stage Implementation Details

### Stage 0: Gap Analysis (`gapharn/gap.py`) ? [STATUS: READY]
- **Implemented Functions:**
  - `parse_nm(text: str) -> set[str]`: Filters defined global symbols (`T`, `W`).
  - `exported_functions(lib: Path) -> set[str]`: Dynamic `.so` and static `.a` inspection.
  - `executed_functions(cov_jsons: list[Path]) -> Counter`: Ingests `llvm-cov export` JSONs.
  - `compute_gap(api_functions, exported, executed) -> dict`: Set difference logic.
- **Verification:** Unit tested against mock library fixtures.

### Stage 1: API Model (`gapharn/apimodel.py`) ? [STATUS: READY]
- **Implemented Functions:**
  - `parse_headers(headers, include_dirs)`: Builds Clang translation units.
  - Role classification rules: Initializer (`_new`, `_open`), Destructor (`_free`), Data-Entry (`void*`, `size_t`), Processor.
  - Macro and Enum evaluation: Safely extracts valid constants (e.g., `ARCHIVE_FORMAT_ZIP`).
- **Verification:** Successfully parsed `libarchive/archive.h` and `archive_entry.h`.

### Stage 2: G1 Rule-Based Generator (`gapharn/gen_rule.py`) ? [STATUS: UNDER DEVELOPMENT]
- **Function Specifications:**
  ```python
  def find_init_chain(model: dict, target_fn: str) -> list[dict]:
      """Traverse producer-consumer edges to find shortest initialization path."""
      
  def map_arguments_to_fuzz(params: list[dict], fuzz_offset: int) -> tuple[list[str], list[str]]:
      """Emit C code mapping raw fuzz bytes to parameter types (buffers, ints, enums)."""
      
  def synthesize_g1_harness(gap_fn: dict, model: dict, template_path: Path) -> str:
      """Renders Jinja2 C harness enforcing status checks and destructor cleanup."""
  ```

### Stage 3: Correctness Oracles (`gapharn/oracle.py`) ? [STATUS: UNDER DEVELOPMENT]
- **Function Specifications:**
  ```python
  def verify_o1_compile(harness_path: Path, lib_path: Path, include_dir: Path) -> tuple[bool, str]:
      """Executes clang with -fsanitize=address,undefined,fuzzer -Werror."""
      
  def verify_o2_sanity(harness_bin: Path, valid_seed_dir: Path, timeout_sec: int = 60) -> tuple[bool, str]:
      """Executes harness on valid seed inputs to ensure 0% API misuse crashes."""
      
  def verify_o3_gap_hit(harness_bin: Path, target_symbol: str, seed_dir: Path) -> bool:
      """Runs short coverage trace and confirms target symbol was executed > 0."""
      
  def verify_o4_lifecycle_lint(source_code: str) -> tuple[bool, str]:
      """Static AST linting: ensures paired free calls and non-null guards."""
  ```

---

## 3. Engineering Work Schedule (Sprints & Checkpoints)

| Sprints | Core Milestone | Deliverables & Code Artifacts | Exit Git Tag |
| :--- | :--- | :--- | :--- |
| **Day 1?3 (Oct 11?13)** | Pipeline Baseline | `gap.py`, `apimodel.py`, `check_env.sh`, Baseline `gap.json` | `v0.1-gap` |
| **Day 4?5 (Oct 14?15)** | First Compilable Harnesses | `harness_write.c`, `harness_entry.c`, PM1 Presentation Deck | `pm1` |
| **Sprint 1 (Oct 16?20)** | Full G1 & Oracles | `gen_rule.py`, `oracle.py`, `oracle_log.csv` verification | `v0.2-harness` |
| **Sprint 2 (Oct 21?25)** | G2 LLM & Cluster Fuzzing | `gen_llm.py` with `llama.cpp`, distributed `worker.sh` deployment | `v0.3-pipeline` |
| **Sprint 3 (Oct 26?30)** | Bug Triage & PM2 | `triage.py`, First CVE/bug candidate reproducer, PM2 Deck | `pm2` |
| **Sprint 4 (Nov 01?06)** | Upstream PRs & PM3 | Patch PR submitted upstream, `UPSTREAM.md` updated, FSE Draft | `pm3` |
| **Sprint 5 (Nov 07?13)** | Final Paper & Repro | `make repro` verification on clean VM, ACM paper PDF finalized | `final` |
