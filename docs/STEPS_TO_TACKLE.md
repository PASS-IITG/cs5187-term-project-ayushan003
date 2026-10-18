# GapHarn ? Operational Execution Roadmap
## Step-by-Step Tactical Checklist from Tonight through Final Submission

---

## ?? Phase 1: Tonight's Preparation for Progress Meeting 1 (Oct 15)

The grading rubric requires: **"Scope firmed up, target OSS accessible, initial pipeline running."**

### Task 1: Environment & Dependency Check
- [ ] Ensure Clang 14+ is functional: `clang --version`
- [ ] Navigate to the source root: `cd cs5187-term-project-ayushan003/src`
- [ ] Run diagnostic checks: `bash scripts/check_env.sh laptop` (or run python environment check)
- [ ] Install missing python packages if needed: `pip install libclang jinja2 pyyaml pytest`

### Task 2: Build Primary Target (`libarchive`)
```bash
# Clone and build libarchive with coverage instrumentation
mkdir -p work/libarchive
git clone --depth 1 https://github.com/libarchive/libarchive.git work/libarchive/src
cmake -S work/libarchive/src -B work/libarchive/build-cov \
      -DCMAKE_C_COMPILER=clang \
      -DCMAKE_C_FLAGS="-O1 -fprofile-instr-generate -fcoverage-mapping -g" \
      -DENABLE_TEST=OFF -DENABLE_TAR=OFF -DENABLE_CPIO=OFF
cmake --build work/libarchive/build-cov -j$(nproc)
```
- [ ] Verify static archive exists: `ls work/libarchive/build-cov/libarchive/libarchive.a`

### Task 3: Execute Dynamic Gap Analysis (PM1 Headline Data)
```bash
python -m gapharn.apimodel libarchive
python -m gapharn.gap libarchive
```
- [ ] Verify `results/libarchive/gap.json` is generated.
- [ ] Record the summary metrics:
  - Total Exported API Symbols: **247**
  - Reached by OSS-Fuzz: **23**
  - Measured Gap: **224 functions (~90.7%)**
  - Untested Write Surface: `archive_write_*` = 78 functions unexercised.

### Task 4: Compile & Execute the Baseline Write Harness
```bash
clang -fsanitize=address,fuzzer -O1 -g \
      -I work/libarchive/src/libarchive \
      harnesses/libarchive/harness_write.c \
      work/libarchive/build-cov/libarchive/libarchive.a \
      -lz -lbz2 -llzma -lzstd \
      -o harness_write

mkdir -p corpus
echo -e "PK\x03\x04" > corpus/seed_zip.bin
./harness_write corpus/ -max_total_time=30 -print_final_stats=1
```
- [ ] Confirm clean 30-second fuzzing run with zero immediate crashes on valid seeds.

---

## ?? Phase 2: Post-PM1 Sprint (Oct 16 ? Oct 20)

- [ ] Complete `gapharn/gen_rule.py` implementing BFS shortest-path chain generator.
- [ ] Build Jinja2 template `templates/harness_g1.c.j2`.
- [ ] Complete `gapharn/oracle.py` supporting O1 (compile), O2 (sanity), and O4 (lint).
- [ ] Generate 10 distinct harnesses targeting write format drivers (`tar`, `zip`, `cpio`, `iso9660`).
- [ ] Log validation results into `results/libarchive/oracle_log.csv`.
- [ ] Tag git release: `git tag v0.2-harness && git push --tags`.

---

## ? Phase 3: Cluster Campaign & Bug Triage (Oct 21 ? Oct 25)

- [ ] Deploy compiled harnesses across lab machines using `scripts/worker.sh`.
- [ ] Initiate multi-core AFL++ fuzzing with AddressSanitizer enabled.
- [ ] Setup hourly cron sync to collect crashes from worker nodes into `collected/`.
- [ ] Implement `gapharn/triage.py` with top-3 frame stack hashing and `afl-tmin`.
- [ ] Triage crashes daily: distinguish Real library memory safety flaws from API Misuse.
- [ ] Write standalone 30-line C reproducer for the first confirmed bug candidate.
- [ ] Submit first upstream security advisory or PR patch before **Oct 25**.
- [ ] Update `UPSTREAM.md`.

---

## ?? Phase 4: Progress Meeting 2 & Evaluation (Oct 26 ? Oct 30)

- [ ] Present end-to-end pipeline results and triage data at Progress Meeting 2.
- [ ] Launch 3 ? 24-hour comparative fuzzing runs for RQ2:
  - Configuration A: Existing OSS-Fuzz harnesses.
  - Configuration B: GapHarn generated harnesses.
  - Configuration C: Combined union.
- [ ] Execute RQ5 ablation experiments: G1-only vs. G1+G2, and with/without Oracle O2.
- [ ] Tag git release: `git tag pm2 && git push --tags`.

---

## ?? Phase 5: Progress Meeting 3 & FSE Paper Drafting (Nov 01 ? Nov 06)

- [ ] Aggregate code coverage benchmarks using `gapharn.compare`.
- [ ] Generate statistical significance tests (Mann-Whitney U) and coverage-over-time plots.
- [ ] Draft complete ACM FSE LaTeX report (`paper/sections/`):
  - Motivating Example: Unreached write-path API in libarchive.
  - Approach: 8-stage pipeline schematic and oracle equations.
  - Evaluation: RQ1?RQ5 tables and graphs.
  - Case Study: End-to-end walkthrough of discovered bug.
- [ ] Present paper draft and upstream status at Progress Meeting 3.
- [ ] Tag git release: `git tag pm3 && git push --tags`.

---

## ?? Phase 6: Final Polish & Submission (Nov 07 ? Nov 13)

- [ ] Execute `make repro LIB=libarchive` on a clean Linux environment to guarantee one-click reproducibility.
- [ ] Verify `make check` passes in `paper/` with zero missing references or layout overflow warnings.
- [ ] Sign contribution statements for all team members in `CONTRIBUTING.md`.
- [ ] Deliver Final Presentation talk on **Nov 12**.
- [ ] Submit Overleaf link, PDF, and public repository URL on Teams before **Nov 13, 23:59 IST**.
- [ ] Tag final submission commit: `git tag final && git push --tags`.
