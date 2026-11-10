<!-- READY TO FILE. Title and body below are what to paste into `gh issue create`. -->

## Title

    lpad/rpad with a multi-byte fill build the whole string before the byte limit rejects it

## Body

`lpad` and `rpad` bound the requested length in **characters**, but
`Value::MAX_BLOB_LENGTH` is a limit on **bytes**. A fill whose characters are
multi-byte therefore passes the length check and the entire string is built
before the downstream byte check rejects it.

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

One billion characters is inside the character limit, but a 4-byte fill makes
that about 4 GB of string. Measured on a debug build: roughly 34 s and an
11.7 GB peak before the error comes back, or, under `ulimit -v 6000000`:

```
memory allocation of 3999999996 bytes failed
Aborted
```

A one-byte fill at the same length is legal and does succeed, so the limit
itself is not the problem — only that it is checked in the wrong unit.

### Expected

`string or blob too big (18)`, promptly and without allocating, which is what
the same statement already produces for a single-byte fill past the limit.

### Relation to #11219

#11219 (fixed by #11225) moved the length check in front of the allocation, so
`lpad('a', 9223372036854775807, 'b')` no longer aborts. That check compares a
character count against the byte limit, so this case slips through it. It is
not a regression from #11225 — the same statement behaves the same way on the
commit before it — which is why it was kept out of that pull request and
recorded there as a separate follow-up.

The byte length is exact arithmetic: the padding is a cycle of the fill's
characters, so it can be computed without building the string, and an exact
check keeps the legal one-byte 1e9 case working where a
`target_len * max_char_width` estimate would wrongly reject mixed-width fills.
I have that fix written and tested and will open it as a pull request against
this issue.

### Environment

- `tursodb` built from source, release profile
- Linux x86_64

---

Found with a differential/metamorphic SQL testing tool I am building for a
university software-engineering course. This report was prepared with AI
assistance (Claude); I have read it, I understand it, and I am happy to defend
or revise anything in it.

## Duplicate search performed

GitHub issue search, open and closed:

- `repo:tursodatabase/turso lpad multi-byte` -> 1 hit, #11225 (our own, merged)
- `repo:tursodatabase/turso lpad rpad fill bytes` -> 1 hit, #11225
- `repo:tursodatabase/turso MAX_BLOB_LENGTH` -> 3 hits: #11225; #10240, the
  merged PR that introduced the length limit itself; #8071, unrelated
  (sync_server Content-Length)
- `repo:tursodatabase/turso pad character byte limit` -> #11225 and #10297,
  unrelated (postgres LIKE)

No existing issue covers this. #10240 is the origin of `check_length_limit`
and is worth citing as context, not as a duplicate.

## Still to do before the PR, and why it is not done yet

The fix in reports/patches/turso-followup-multibyte-fill-byte-limit.md was
written and verified on the 11225 branch, which is now merged. Before opening
the PR it must be rebased onto current upstream main and re-verified there
(tests fail without it, full turso_core suite passes). That needs a rebuild of
~/targets/turso, which CLAUDE.md rules 4 and 5 forbid while a campaign is
using the binary. The campaign runs to ~19:13.
