"""Tests for adapters.errors: one case per pattern P02 names, plus precedence."""

from __future__ import annotations

import pytest

from graft.engines import errors


def test_error_classes_match_spec():
    assert errors.ERROR_CLASSES == {
        "none",
        "user_error",
        "internal_error",
        "panic",
        "crash",
        "timeout",
    }


# --- the exact patterns P02 task 6 lists -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "thread 'main' panicked at src/core/mod.rs:412:9:\nindex out of bounds",
        "panicked at 'called Option::unwrap() on a None value'",
    ],
)
def test_panicked_at_is_panic(text):
    assert errors.classify(text, returncode=101, engine="turso") == errors.PANIC


@pytest.mark.parametrize(
    "text",
    [
        "INTERNAL Error: Attempted to access index 5 within vector of size 3",
        "Assertion failed: (idx < size), function operator[]",
        "Error: Internal error: Optimizer rule failed",
        "This feature is not implemented: window frame RANGE",
        "not yet implemented: ALTER TABLE ... RENAME",
        "entered unreachable code",
    ],
)
def test_internal_error_patterns(text):
    assert errors.classify(text, returncode=1, engine="duckdb") == errors.INTERNAL_ERROR


@pytest.mark.parametrize(
    "text",
    [
        "Process terminated by SIGSEGV",
        "Aborted (SIGABRT)",
        "Segmentation fault (core dumped)",
    ],
)
def test_crash_text_patterns(text):
    assert errors.classify(text, returncode=1, engine="duckdb") == errors.CRASH


@pytest.mark.parametrize("returncode", [134, 139, -6, -11, -9, 137])
def test_crash_return_codes(returncode):
    assert errors.classify("", returncode=returncode) == errors.CRASH


def test_rust_panic_return_code():
    assert errors.classify("some output", returncode=101) == errors.PANIC


def test_timeout_wins_over_everything():
    assert (
        errors.classify("panicked at x", returncode=101, timed_out=True)
        == errors.TIMEOUT
    )


def test_unreapable_process_is_a_crash():
    assert errors.classify("", returncode=None) == errors.CRASH


# --- user errors must never be upgraded --------------------------------------


@pytest.mark.parametrize(
    "text,engine",
    [
        ("Catalog Error: Table with name t does not exist!", "duckdb"),
        ('Parser Error: syntax error at or near "SELCT"', "duckdb"),
        ("Error during planning: table 'public.t' not found", "datafusion"),
        ('SQL error: ParserError("Expected: an SQL statement")', "datafusion"),
        ("Arrow error: Divide by zero error", "datafusion"),
        ("× Parse error: no such table: nope", "turso"),
        ("Runtime error: integer overflow", "turso"),
        ("OperationalError: no such table: missing_table", "sqlite"),
        ("Binder Error: Referenced column not found", "duckdb"),
    ],
)
def test_user_errors_stay_user_errors(text, engine):
    assert errors.classify(text, returncode=1, engine=engine) == errors.USER_ERROR


def test_crash_outranks_user_error_text():
    text = "Catalog Error: ...\nSegmentation fault (core dumped)"
    assert errors.classify(text, returncode=1, engine="duckdb") == errors.CRASH


def test_panic_outranks_user_error_text():
    text = "Parse error: bad\nthread 'tokio' panicked at lib.rs:1"
    assert errors.classify(text, returncode=1, engine="turso") == errors.PANIC


def test_internal_error_outranks_user_error_text():
    text = "Binder Error: whatever\nINTERNAL Error: invariant broken"
    assert errors.classify(text, returncode=1, engine="duckdb") == errors.INTERNAL_ERROR


# --- "nothing happened" ------------------------------------------------------


def test_clean_run_is_none():
    assert errors.classify("", returncode=0) == errors.NONE


def test_explicit_failed_flag_forces_user_error():
    assert errors.classify("", returncode=0, failed=True) == errors.USER_ERROR


def test_explicit_failed_false_stays_none():
    assert errors.classify("some rows", returncode=0, failed=False) == errors.NONE


# --- looks_like_error --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Runtime error: integer overflow",
        "Parse error: no such table: nope",
        "panicked at lib.rs:9",
        "INTERNAL Error: boom",
    ],
)
def test_looks_like_error_true(text):
    assert errors.looks_like_error(text, engine="turso")


@pytest.mark.parametrize("text", ["1|x|y", "a = 1", "", "42"])
def test_looks_like_error_false_for_data(text):
    assert not errors.looks_like_error(text, engine="turso")
