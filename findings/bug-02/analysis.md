# Follow-up (NOT in PR 11225): lpad/rpad with a multi-byte fill

Deliberately left out of PR 11225 to keep that PR to the reported bug. Open this
separately, after 11225 merges.

## The defect

Autofixer surfaced it on PR 11225 under "Also wrong on main: 1 input this pull
request doesn't fix", with this repro:

```sql
SELECT length(LPAD('a', 1000000000, '😀'));
```

`pad()`'s length check bounds the result in **characters**, but
`Value::MAX_BLOB_LENGTH` is a limit on **bytes**. A fill whose characters are
multi-byte therefore passes the check and the whole string is built before the
downstream byte check rejects it. Measured on the PR branch: about 34 s and an
11.7 GB peak, or `memory allocation of 3999999996 bytes failed` and SIGABRT
under `ulimit -v 6000000`.

Present on `main` too, so it is not a regression from 11225 — which is why it
was not blocking and why it does not belong in that PR.

## The fix, written and verified, then withheld

The padding is a cycle of `fill_chars`, so its byte length is exact arithmetic;
there is no need to build it to find out. Insert after the empty-fill early
return in `pad()`:

```rust
    // The check above bounds the result in CHARACTERS, but the limit is on
    // bytes, so a multi-byte fill could still build a multi-gigabyte string and
    // abort before the downstream byte check ran: `lpad('a', 1000000000, '😀')`
    // reached about 12 GB. The padding is a cycle of `fill_chars`, so its byte
    // length is exact arithmetic - there is no need to build it to find out.
    let pad_chars = target_len - char_count;
    let pad_bytes = (pad_chars / fill_chars.len())
        .checked_mul(fill_str.len())
        .and_then(|whole| {
            fill_chars[..pad_chars % fill_chars.len()]
                .iter()
                .map(|c| c.len_utf8())
                .try_fold(whole, usize::checked_add)
        })
        .and_then(|bytes| bytes.checked_add(s.len()));
    if pad_bytes.is_none_or(|bytes| bytes > Value::MAX_BLOB_LENGTH as usize) {
        return Err(LimboError::TooBig);
    }
```

with this test:

```rust
    #[test]
    fn pad_rejects_a_multi_byte_fill_over_the_byte_limit() {
        // Within the limit in characters, about 4 GB in bytes. This was built
        // in full and only then rejected, or aborted the process outright.
        assert!(matches!(
            exec_lpad(&t("a"), &i(Value::MAX_BLOB_LENGTH), Some(&t("\u{1F600}"))),
            Err(LimboError::TooBig)
        ));
        assert!(matches!(
            exec_rpad(&t("a"), &i(Value::MAX_BLOB_LENGTH), Some(&t("\u{1F600}"))),
            Err(LimboError::TooBig)
        ));
        // A multi-byte fill that stays inside the limit must still work.
        assert_eq!(
            exec_lpad(&t("a"), &i(3), Some(&t("\u{1F600}"))).unwrap(),
            t("\u{1F600}\u{1F600}a")
        );
    }
```

## Verification already performed on this code

It was implemented on the branch, verified, and then removed. Measured on a
debug build:

| statement | with the precheck | without |
| --- | --- | --- |
| `lpad('a', 1e9, '😀')` | `too big (18)` in 0.02 s, 51 MB | ~34 s, 11.7 GB, or SIGABRT |
| `rpad('a', 1e9, '😀')` | `too big (18)` in 0.01 s, 51 MB | same |
| `lpad('a', 1e9)` 1-byte fill | **succeeds** (1e9 bytes, legal) | succeeds |
| `lpad('a', 1e9 + 1)` | `too big (18)` | `too big (18)` |
| `lpad('a', 3, '😀')` | `😀😀a` | `😀😀a` |

The arithmetic is exact rather than conservative, which is why the legal 1e9
case is still accepted: a `target_len * max_char_width` estimate would have
wrongly rejected mixed-width fills whose true byte length fits.

## Why it was withheld from 11225

Autofixer's verdict on 11225 was effectively a conditional approval - "The fix
is correct and checks the length before allocating, like zeroblob and
randomblob already do" - with a three-item mechanical checklist (fmt, clippy,
drop the 2 GB assertion). Turso's CONTRIBUTING asks contributors to keep a
change small and focused, Autofixer allows only three reviews per pull request,
and this is the most intricate code in the diff. Adding it to a near-approved
pull request risked turning a merge into a design discussion.
