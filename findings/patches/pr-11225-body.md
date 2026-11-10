> **As submitted upstream.** This is the text of [tursodatabase/turso#11225](https://github.com/tursodatabase/turso/pull/11225), reproduced here
> verbatim from the upstream record. **State: **merged** 2026-11-02 as `ec86571642a6`, +138/−41.**
> Covers defect(s) #1, #2 and #3 — see [`../README.md`](../README.md).

---

Fixes #11219.

`lpad`, `rpad` and `repeat` build their result from the length the caller asks
for and allocate it straight away, so a length near `i64::MAX` kills the
process in the allocator before any check runs:

```
$ tursodb :memory: "select lpad('a', 9223372036854775807, 'b')"
memory allocation of 9223372036854775806 bytes failed
Aborted
```

The length limit was already there, but it only ran *after* the result existed
(`Value::check_length_limit`). That is why `lpad('a', 4294967296, 'b')` reports
`string or blob too big (18)` correctly — a 4 GB allocation fails gracefully and
the check gets to run — while anything much larger aborts instead.

This moves the check in front of the allocation. All three functions now compare
the requested length against `Value::MAX_BLOB_LENGTH` and return
`LimboError::TooBig`, which is the same error and the same code 18 the existing
check produces, so the user-visible behaviour is the one that was already
intended. `repeat` multiplies, so it uses `checked_mul` and refuses on overflow.

The functions return `Result<Value>` now and the three call sites in
`core/vdbe/execute.rs` use `?`, which is how `exec_gcd` and `exec_lcm` are
already called a few lines above.

**`repeat` is included even though the issue only mentions `lpad`/`rpad`.**
`select repeat('a', 9223372036854775807)` aborts in exactly the same way — it is
the same mistake in the same file, so it seemed wrong to leave it. Happy to
split it out if you would rather keep the PR to the reported functions.

### Tests

Three tests added in `core/functions/string.rs`. They do not just fail without
the fix — with the two guards removed the test binary dies:

```
running 10 tests
memory allocation of 9223372036854775807 bytes failed
error: test failed ... (signal: 6, SIGABRT: process abort signal)
```

With the fix, the full `turso_core` suite passes — **2543 passed, 0 failed, 17
ignored**, plus **53 doc-tests passed, 0 failed**. That is the crate holding
both changed files.

Two notes on the wider workspace, in case you run it: `cargo test --workspace`
does not build cleanly in my checkout, for two reasons that have nothing to do
with this change and that I confirmed reproduce on unpatched `main` —
`turso_sqlite3`'s `compat` test fails to link (`cannot find -lturso_sqlite3`,
the cdylib isn't built), and `memory-benchmark` hits a `#[global_allocator]`
conflict with `turso`. Neither crate is touched here.

Behaviour I checked by hand on a release build, all unchanged apart from the
three aborts becoming errors:

| statement | before | after |
| --- | --- | --- |
| `lpad('a', 9223372036854775807, 'b')` | abort | `string or blob too big (18)` |
| `rpad('a', 9223372036854775807, 'b')` | abort | `string or blob too big (18)` |
| `repeat('a', 9223372036854775807)` | abort | `string or blob too big (18)` |
| `lpad('a', 4294967296, 'b')` | `too big (18)` | unchanged |
| `lpad('abc', 6, 'xy')` | `xyxabc` | unchanged |
| `rpad('abc', 6, 'xy')` | `abcxyx` | unchanged |
| `repeat('ab', 3)` | `ababab` | unchanged |
| `lpad('abcdef', 3)` | `abc` | unchanged |
| `lpad('aéc', 4)` | `' aéc'` | unchanged |
| `lpad('abc', 10, '')` | `abc` | unchanged |

### One thing worth a reviewer's eye

The check in `pad` compares a character count against a byte limit, so for
multi-byte fill characters it is permissive, not conservative: a 4-byte fill
passes a check of 1,000,000,000 characters and can still build about 4 GB,
which the existing byte check then rejects. So this change does not close that
case — `lpad('a', 1000000000, '😀')` still builds the whole string before
failing, as it does on main. I have the exact byte-length precheck for it
written and tested and will open it as a separate pull request, since it is not
a regression from this one and is better reviewed on its own.

I have not benchmarked this. It is two comparisons on a path that was already
allocating, so I would not expect a measurable difference, but I did not measure
it.

---

Found with a differential/metamorphic SQL testing tool I am building for a
university software-engineering course; the crash oracle caught this one. The
patch was written with AI assistance (Claude), which is also recorded as a
`Co-Authored-By` trailer on the commit. I have read the change, I understand it,
and I am happy to defend or revise it.

