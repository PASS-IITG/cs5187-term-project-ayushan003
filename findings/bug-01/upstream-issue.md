<!-- filed as https://github.com/tursodatabase/turso/issues/11219 (filed) on 2026-11-01 -->
# LPAD/RPAD with a length argument near i64::MAX aborts the process instead of raising 'string or blob too big'

> DRAFT - not filed. A human must read docs/PROJECT_POLICIES.md, reproduce this on latest main, re-run the duplicate search, and only then decide whether to submit it.

## Environment

- Engine: turso
- Version: Turso 0.9.0-pre.2
- Commit: b588fbdc6c016cb3626d0ab464a7c45a54bc2b86
- OS: Linux-7.0.0-42-generic-x86_64-with-glibc2.43
- Build: release build from source, compiled locally

## What happens

The process aborts with SIGABRT. Rust's allocator error reaches stderr first: `memory allocation of 9223372036854775806 bytes failed`, then the shell reports the abort. No SQL error is returned and the session is gone.

## To reproduce

```sql
select LPAD('a', 9223372036854775807, 'b');
```

Run with:

```
tursodb :memory:
```

## Expected behaviour, and why

The statement should either return a result or be rejected with a normal user-facing error. A single SQL statement should not abort the process: a server embedding this engine would lose the whole process, and the condition is reachable from plain SQL with no special configuration.

This expectation does not rest on any other engine's behaviour, and no claim about the SQL standard is made here.

## How this was found

Found by a crash oracle: the engine was asked to run a single statement and the process died or reported an internal error instead of answering or rejecting it. No comparison against another engine is involved, so this finding does not depend on any assumption about matching semantics.

- Seed: 6948 (every random choice in this tool comes from a seeded generator, so the case is reproducible)
- Coarse signature: 64513113c675c2daccf9ef2e57c9b333dd646af5

### Verification history

- 2026-10-28T19:58:43Z on commit UNKNOWN: crash: memory allocation of 9223372036854775804 bytes failed
- 2026-11-01T10:06:27Z on commit UNKNOWN: reproduced twice by hand on a fresh :memory: instance
- 2026-11-01T10:54:55Z on commit b588fbdc6c016cb3626d0ab464a7c45a54bc2b86: STILL CRASHES: SIGABRT (rc=-6), 'memory allocation of 9223372036854775806 bytes failed'

### Notes recorded during triage

CONFIRMED on current main. Re-tested after pulling and rebuilding tursodb at b588fbdc6 (Turso 0.9.0-pre.2, built 2026-11-01): both LPAD and RPAD still abort the process with SIGABRT and 'memory allocation of 9223372036854775806 bytes failed'. The length guard is present and works - LPAD('a', 4294967296, 'b') returns 'string or blob too big (18)' cleanly - but is bypassed at i64::MAX, so the size computation overflows before the check is reached. SQLite has no LPAD, so no differential oracle could have found this; only the crash oracle did.

## Duplicate search

Search queries used (GitHub issue search, open and closed):

- `repo:tursodatabase/turso "lpad" crash`
- `repo:tursodatabase/turso "rpad" crash`
- `repo:tursodatabase/turso "abc" crash`
- `repo:tursodatabase/turso "crash" in:title`

Nearest existing issues found, and why each is different:

- #11175 (open): core/dialect: reject wrong argument counts instead of panicking
- #10520 (open): postgres: reject int8 casts of numeric literals outside the bigint range
- #10193 (open): core/incremental: maintain a run of inner joins as one delta join, with key lookups
- #10728 (closed): core/ext: NUL-terminate text values for the C API
- #10920 (closed): core/mvcc: keep no RowID in a row version

The tool did not conclude these are the same defect; a human must read each one. If any is the same, do not file.

## Disclosure

DISCLOSE AI ASSISTANCE HERE IF THE PROJECT REQUIRES IT - see docs/PROJECT_POLICIES.md for what each project says, verbatim.

## Checklist for the human filing this

- [ ] Reproduce on latest `main`, rebuilt today, and paste that commit here
- [ ] Read the project's CONTRIBUTING and issue template (configs/report_templates/) and this project's policy summary (docs/PROJECT_POLICIES.md)
- [ ] Re-run the duplicate search by hand, including closed issues
- [ ] Fill in any template field this draft leaves UNKNOWN, including real name and affiliation where the template demands them
- [ ] Decide on the disclosure line above
- [ ] Confirm nothing in the repro depends on this machine or on generated data
