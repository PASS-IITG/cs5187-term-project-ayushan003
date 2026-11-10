# References

Only works that were actually retrieved are listed. Access depth is marked:
**[full text]** means the paper was read in full; **[abstract]** means the
abstract and publisher or author page were read and nothing beyond that depth is
summarised anywhere in this repository.

`docs/related-work.md` marks which of these changed a decision here, with the
decision named. The rest are positioning.

---

## Structure-aware input generation

1. **Godefroid, P., Kiezun, A., Levin, M. Y.** Grammar-based Whitebox Fuzzing.
   *PLDI 2008*, 206–215. DOI [10.1145/1375581.1375607](https://doi.org/10.1145/1375581.1375607).
   **[abstract]**

2. **Liang, Y., Liu, P.** Efficiently Detecting DBMS Bugs through Bottom-up
   Syntax-based SQL Generation. *NDSS 2026*.
   DOI [10.14722/ndss.2026.24198](https://doi.org/10.14722/ndss.2026.24198).
   **[full text]** — the closest prior work; see `docs/related-work.md` §1.

3. **Kirschner, L., Soremekun, E.** Directed Grammar-Based Test Generation.
   *IEEE TSE / FSE 2026*. DOI [10.1109/tse.2025.3627220](https://doi.org/10.1109/tse.2025.3627220);
   preprint [arXiv:2508.01472](https://arxiv.org/abs/2508.01472). **[full text]**

4. **Groß, S., Koch, S., Bernhard, L., Holz, T., Johns, M.** Fuzzilli: Fuzzing
   for JavaScript JIT Compiler Vulnerabilities. *NDSS 2023*.
   DOI [10.14722/ndss.2023.24290](https://doi.org/10.14722/ndss.2023.24290).
   **[abstract]**

5. **Bendrissou, B., Donaldson, A. F., Cadar, C.** Repair-Driven Greybox
   Fuzzing. *ISSTA 2026*. DOI [10.1145/3832229](https://doi.org/10.1145/3832229).
   **[abstract]**

6. **Bulekov, A., Das, B., Hajnoczi, S., Egele, M.** No Grammar, No Problem:
   Towards Fuzzing the Linux Kernel without System-Call Descriptions.
   *NDSS 2023*. DOI [10.14722/ndss.2023.24688](https://doi.org/10.14722/ndss.2023.24688).
   **[abstract]**

## Learning from known defects

7. **Cao, L., et al.** VulPA: Detecting Semantically Recurring Vulnerabilities
   with Multi-Object Typestate Analysis. *FSE 2025*. *Proc. ACM Softw. Eng.*
   2(FSE), 2430–2453. DOI [10.1145/3729378](https://doi.org/10.1145/3729378).
   **[abstract]**

8. **Xiao, H., Zhang, Y., Shen, M., Lin, C., Zhang, C., Liu, S., Yang, M.**
   Accurate and Efficient Recurring Vulnerability Detection for IoT Firmware.
   *CCS 2024*. DOI [10.1145/3658644.3670275](https://doi.org/10.1145/3658644.3670275).
   **[abstract]**

9. **Cao, Y., Chen, B., et al.** Recurring Vulnerability Detection: How Far Are
   We? *ISSTA 2025*. DOI [10.1145/3728901](https://doi.org/10.1145/3728901).
   **[abstract]**
   *Venue note: some listings place this at FSE 2025. The DOI resolves inside*
   Proc. ACM Softw. Eng. *vol. 2, but in the ISSTA issue; cited here as ISSTA.*

10. **Tan, Z., Zhou, J., Hu, X., Pan, S., Liu, K., Xia, X.** Similar but Patched
    Code Considered Harmful — The Impact of Similar but Patched Code on
    Recurring Vulnerability Detection and How to Remove Them. *ICSE 2025*.
    DOI [10.1109/icse55347.2025.00110](https://doi.org/10.1109/icse55347.2025.00110);
    preprint [arXiv:2412.20740](https://arxiv.org/abs/2412.20740). **[abstract]**

11. **Wu, S., Wang, R., Cao, Y., Chen, B.** Mystique: Automated Vulnerability
    Patch Porting with Semantic and Syntactic-Enhanced LLM. *FSE 2025*.
    DOI [10.1145/3715718](https://doi.org/10.1145/3715718). **[abstract]**

12. **Guo, Q., He, Y.** Accurate Identification of the Vulnerability-Introducing
    Commit based on Differential Analysis of Patching Patterns. *NDSS 2026*.
    DOI [10.14722/ndss.2026.230140](https://doi.org/10.14722/ndss.2026.230140).
    **[abstract]**

---

## Software and artefacts

- **Turso** — `tursodatabase/turso`. The engine under test.
  <https://github.com/tursodatabase/turso>. Version `Turso 0.9.0-pre.2`;
  revisions `730017f96`, `b588fbdc6`, `7f0a9e6f4` (see
  `configs/engine-revisions.yaml`).
- **Turso's own differential fuzzer** — `testing/differential-oracle/` in that
  repository. The subject of the blind-spot measurement in
  `docs/evaluation.md` §2; the literal policy quoted is
  `sql_gen/src/policy.rs`, `impl Default for LiteralConfig`.
- **DuckDB** — `duckdb/duckdb`. The corpus source; never executed here.
  <https://github.com/duckdb/duckdb>. The report both findings' shape came from
  is [duckdb/duckdb#27846](https://github.com/duckdb/duckdb/issues/27846).
- **SQLite** — stdlib `sqlite3` 3.46.1, used as the backing store for the
  planted-bug test engine only.
- **sqlglot** 30.27.0 — SQL parsing for AST-aware statement reduction.

## This project's upstream contributions

| | Issue | Our pull request | State |
|---|---|---|---|
| Finding 1 | [turso#11219](https://github.com/tursodatabase/turso/issues/11219) | [turso#11225](https://github.com/tursodatabase/turso/pull/11225) | **merged**, `ec86571642a6` |
| Finding 2 | [turso#11260](https://github.com/tursodatabase/turso/issues/11260) | [turso#11259](https://github.com/tursodatabase/turso/pull/11259) | **merged**, `121cd0a8c80b` |

A third report, [apache/datafusion#27001](https://github.com/apache/datafusion/issues/27001)
with our pull request [#27002](https://github.com/apache/datafusion/pull/27002),
is **open and unreviewed**. It was not produced by the mechanism this project is
about and is not claimed as a result; see `docs/provenance.md` §5.
