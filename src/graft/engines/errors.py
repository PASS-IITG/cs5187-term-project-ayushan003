"""Classifying an engine's failure output into the SPEC error classes.

SPEC (`Adapter interface`) fixes six classes::

    none            the statement succeeded
    user_error      the engine rejected the SQL (bad syntax, missing table,
                    type error, constraint violation, ...). Never a bug by itself.
    internal_error  the engine reported an internal failure, a failed assertion,
                    or an unimplemented path. "INTERNAL Error", "Assertion
                    failed", "not yet implemented", "unreachable".
    panic           a Rust panic ("panicked at", exit status 101).
    crash           the process died: SIGSEGV/SIGABRT/SIGKILL, exit status
                    139/134/137, or a negative return code.
    timeout         the wall-clock limit was hit.

A user_error is never upgraded to internal_error and an internal_error is never
downgraded to user_error: the crash oracle keys off exactly this distinction.

Known noise: DataFusion reports unsupported-but-well-formed SQL as
"This feature is not implemented: ...". P02 asks for "not implemented" to be an
internal_error pattern, so it is classified that way here. That is deliberate -
the crash oracle only fires on SQL that is *valid* (accepted elsewhere), so a
plain unsupported-feature rejection does not become a finding on its own.
"""

from __future__ import annotations

import re
import signal
from typing import Final, Pattern

#: the six SPEC error classes
NONE: Final = "none"
USER_ERROR: Final = "user_error"
INTERNAL_ERROR: Final = "internal_error"
PANIC: Final = "panic"
CRASH: Final = "crash"
TIMEOUT: Final = "timeout"

ERROR_CLASSES: Final = frozenset(
    {NONE, USER_ERROR, INTERNAL_ERROR, PANIC, CRASH, TIMEOUT}
)

#: Rust's exit status for an unwinding panic.
RUST_PANIC_RETURNCODE: Final = 101
#: Return codes that mean the process was killed by a signal. Python reports a
#: signal death as a negative return code; a shell reports 128 + signal number.
CRASH_RETURNCODES: Final = {
    134: "SIGABRT",
    137: "SIGKILL",
    139: "SIGSEGV",
    -int(signal.SIGABRT): "SIGABRT",  # -6
    -int(signal.SIGKILL): "SIGKILL",  # -9
    -int(signal.SIGSEGV): "SIGSEGV",  # -11
    -int(signal.SIGBUS): "SIGBUS",
    -int(signal.SIGILL): "SIGILL",
    -int(signal.SIGFPE): "SIGFPE",
}

#: Text that means the process died, whatever the return code says.
CRASH_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bSIGSEGV\b",
        r"\bSIGABRT\b",
        r"\bsegmentation fault\b",
        r"\bcore dumped\b",
        r"\bsignal:\s*\d+\b",
        r"\bfatal runtime error\b",
    )
)

#: Text that means a Rust panic.
PANIC_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bpanicked at\b",
        r"\bthread '.*' panicked\b",
        r"\brust_panic\b",
    )
)

#: Banners an engine puts on a message it means as "you asked for something I
#: do not support". DuckDB's "Conversion Error: Unimplemented type for cast
#: (INTEGER -> TIMESTAMP)" contains the word unimplemented but is an ordinary
#: user-facing conversion error, and classifying it as internal_error made the
#: crash oracle fire on every type mutation.
USER_ERROR_BANNERS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*Conversion Error\b",
        r"^\s*Binder Error\b",
        r"^\s*Catalog Error\b",
        r"^\s*Parser Error\b",
        r"^\s*Out of Range Error\b",
        r"^\s*Invalid Input Error\b",
        r"^\s*Constraint Error\b",
        r"^\s*Syntax Error\b",
        r"^\s*Type Error\b",
    )
)

#: Text that means the engine broke its own invariants or hit a TODO path.
INTERNAL_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bINTERNAL Error\b",
        r"\bInternalError\b",
        r"\bInternal error\b",
        r"\bAssertion failed\b",
        r"\bassert(?:ion)? .*failed\b",
        r"\bnot yet implemented\b",
        r"\bnot implemented\b",
        r"\bunimplemented\b",
        r"\bunreachable\b",
        r"\bAttempted to dereference unique_ptr that is NULL\b",
    )
)

#: Text that only ever means "you wrote bad SQL". Checked last.
USER_ERROR_PATTERNS: Final[tuple[Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:Parser|Parse|Syntax|Catalog|Binder|Conversion|Constraint|"
        r"Permission|Out of Range|Invalid Input|Type|Value) Error\b",
        r"\bsyntax error\b",
        r"\bno such (?:table|column|function|module|index)\b",
        r"\bError during planning\b",
        r"\bSchema error\b",
        r"\bExecution error\b",
        r"\bArrow error\b",
        r"\bRuntime error\b",
        r"\bSQL error\b",
    )
)

#: Per-engine extra patterns, merged with the shared ones above.
ENGINE_PATTERNS: Final[dict[str, dict[str, tuple[Pattern[str], ...]]]] = {
    "duckdb": {
        INTERNAL_ERROR: (
            re.compile(r"\bINTERNAL Error\b"),
            re.compile(r"\bThis error signals an assertion failure\b", re.IGNORECASE),
        ),
    },
    "turso": {
        PANIC: (re.compile(r"\bpanicked at\b", re.IGNORECASE),),
        INTERNAL_ERROR: (
            re.compile(r"\bCorrupt(?:ed)? database\b", re.IGNORECASE),
        ),
    },
    "datafusion": {
        INTERNAL_ERROR: (
            re.compile(r"\bInternal error\b", re.IGNORECASE),
            re.compile(r"\bThis feature is not implemented\b", re.IGNORECASE),
        ),
    },
    "sqlite": {},
}


#: a line of an engine diagnostic that merely quotes the user's SQL back
_ECHO_LINE_RE = re.compile(r"^\s*(?:LINE\s+\d+\s*:|\^+\s*$|[\s^~]+$)", re.IGNORECASE)


def _scrub_echo(text: str) -> str:
    """Remove echoed SQL and string literals from a diagnostic.

    What remains is the engine's own words, which is the only part that should
    decide the error class.
    """
    kept: list[str] = []
    for line in (text or "").splitlines():
        if _ECHO_LINE_RE.match(line):
            continue
        kept.append(re.sub(r"'[^']*'", "''", line))
    return "\n".join(kept)


def _starts_a_line(text: str, engine: str) -> bool:
    """True if an internal-error marker begins one of ``text``'s lines."""
    patterns = INTERNAL_PATTERNS + _engine_patterns(engine, INTERNAL_ERROR)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in patterns:
            found = pattern.search(stripped)
            if found and found.start() == 0:
                return True
    return False


def _matches(text: str, patterns: tuple[Pattern[str], ...]) -> str | None:
    """Return the first matching pattern's matched text, or None."""
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found.group(0)
    return None


def _engine_patterns(engine: str, error_class: str) -> tuple[Pattern[str], ...]:
    return ENGINE_PATTERNS.get(engine, {}).get(error_class, ())


def classify(
    text: str,
    returncode: int | None = 0,
    timed_out: bool = False,
    engine: str = "",
    failed: bool | None = None,
) -> str:
    """Classify one failed statement into a SPEC error class.

    Precedence is fixed and deliberate: timeout, then a dead process, then a
    panic, then an internal error, then user error. Signal deaths and panics
    outrank any text that also looks like a user error, because an engine often
    prints a plausible-looking message on its way down.

    Args:
        text: everything the engine printed for this statement (stdout+stderr).
        returncode: the process exit status, or None if it could not be reaped.
            Use 0 for in-process engines that raised a Python exception.
        timed_out: True if the wall-clock limit was hit.
        engine: engine name, to add that engine's extra patterns.
        failed: whether the statement failed at all. ``None`` means "infer":
            failed if there is text, a non-zero return code, or a timeout.

    Returns:
        One of :data:`ERROR_CLASSES`.
    """
    if timed_out:
        return TIMEOUT
    if returncode is None:
        return CRASH
    if returncode in CRASH_RETURNCODES:
        return CRASH
    if returncode < 0:
        return CRASH

    if _matches(text, CRASH_PATTERNS):
        return CRASH
    if _matches(text, PANIC_PATTERNS) or _matches(text, _engine_patterns(engine, PANIC)):
        return PANIC
    if returncode == RUST_PANIC_RETURNCODE:
        return PANIC
    # A parser error prints its own banner and then ECHOES the offending SQL
    # ("LINE 1: SELECT 'INTERNAL Error'"), and these repros are often about
    # internal errors, so the echo can contain the internal banner verbatim.
    # Matching anywhere in the message turned a plain syntax error into an
    # internal_error. The echo is therefore removed, and string literals
    # blanked, before looking for an internal banner.
    scrubbed = _scrub_echo(text)
    # An internal marker that STARTS a line is the engine's own banner and wins
    # outright: "Binder Error: ...\nINTERNAL Error: invariant broken" is
    # internal. A marker merely mentioned inside a line is not, which is what
    # keeps "Conversion Error: Unimplemented type for cast" a user error.
    if _starts_a_line(scrubbed, engine):
        return INTERNAL_ERROR
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if not any(b.search(first_line) for b in USER_ERROR_BANNERS):
        # Nothing labelled it either way; fall back to the raw message, which
        # is where a multi-line panic or assertion dump lives.
        if _matches(scrubbed, INTERNAL_PATTERNS) or _matches(
            scrubbed, _engine_patterns(engine, INTERNAL_ERROR)
        ):
            return INTERNAL_ERROR

    if failed is None:
        failed = bool(text.strip()) or returncode != 0
    if not failed:
        return NONE
    return USER_ERROR


def looks_like_error(text: str, engine: str = "") -> bool:
    """True if ``text`` matches any known error pattern for ``engine``.

    Used by the CLI adapters whose engines print errors on stdout mixed in with
    result rows (Turso), where the stream alone does not say which is which.
    """
    groups = (
        CRASH_PATTERNS,
        PANIC_PATTERNS,
        INTERNAL_PATTERNS,
        USER_ERROR_PATTERNS,
        _engine_patterns(engine, PANIC),
        _engine_patterns(engine, INTERNAL_ERROR),
        _engine_patterns(engine, USER_ERROR),
    )
    return any(_matches(text, group) for group in groups)
