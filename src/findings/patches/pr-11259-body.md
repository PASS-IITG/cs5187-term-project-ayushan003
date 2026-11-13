> **As submitted upstream.** This is the text of [tursodatabase/turso#11259](https://github.com/tursodatabase/turso/pull/11259), reproduced here
> verbatim from the upstream record. **State: **merged** 2026-11-02 as `121cd0a8c80b`, +54/−5.**
> Covers defect(s) #4 — see [`../README.md`](../README.md).

---

Fixes #11260.

Follow-up to #11225, which flagged this case as not fixed by it and said a
separate pull request would follow. This is that pull request.

`lpad` and `rpad` bound the requested length in **characters**, but
`Value::MAX_BLOB_LENGTH` is a limit on **bytes**. A multi-byte fill therefore
passes the length check and the whole string is built before the downstream
byte check rejects it:

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

One billion characters is inside the character limit, but a 4-byte fill makes
that about 4 GB. On a debug build that is roughly 34 s and an 11.7 GB peak
before the error comes back, or, under `ulimit -v 6000000`:

```
memory allocation of 3999999996 bytes failed
Aborted
```

This is the case I flagged in #11225 as not fixed by it and promised to send
separately. It is not a regression from that change — it behaves the same way
on the commit before it.

### The fix

The padding is a cycle of the fill's characters, so its byte length is exact
arithmetic: `whole_cycles * fill_bytes`, plus the widths of the characters in
the part-cycle at the end, plus the input. There is no need to build the string
to find out how big it will be. The check goes in front of the allocation, next
to the character check already there, and returns the same `LimboError::TooBig`
and the same code 18.

The arithmetic is exact rather than conservative on purpose. A
`target_len * max_char_width` estimate would be simpler but would wrongly
refuse a mixed-width fill whose true byte length fits, and would refuse the
legal single-byte `lpad('a', 1000000000)` outright.

`pad_chars` also replaces the `target_len - char_count` that the padding loop
recomputed a few lines below, so the subtraction happens once.

### Tests

One test added next to the existing limit tests in `core/functions/string.rs`.
It covers both directions of the refusal, that an in-limit multi-byte fill
still works, and a mixed-width fill, which is what exercises the remainder
term rather than the whole-cycle one.

Without the fix the test does not merely fail — on this machine
`exec_lpad(&t("a"), &i(Value::MAX_BLOB_LENGTH), Some(&t("😀")))` builds about
4 GB before returning, so the test binary either hangs for tens of seconds or
aborts in the allocator.

Behaviour checked by hand on a release build:

| statement | before | after |
| --- | --- | --- |
| `lpad('a', 1000000000, '😀')` | ~34 s, 11.7 GB, or abort | `string or blob too big (18)`, immediate |
| `rpad('a', 1000000000, '😀')` | same | same |
| `lpad('a', 1000000000)` (1-byte fill, legal) | succeeds | unchanged, still succeeds |
| `lpad('a', 3, '😀')` | `😀😀a` | unchanged |
| `lpad('abc', 6, 'xy')` | `xyxabc` | unchanged |
| `lpad('abc', 10, '')` | `abc` | unchanged |
| `lpad('abcdef', 3)` | `abc` | unchanged |

`sqlite/conformance/turso-sqltests/gcd_lcm_repeat_pad.sqltest` pins the
unchanged rows above and passes untouched.

---

Found with a differential/metamorphic SQL testing tool I am building for a
university software-engineering course. The patch was written with AI
assistance (Claude), which is also recorded as a `Co-Authored-By` trailer on
the commit. I have read the change, I understand it, and I am happy to defend
or revise it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
