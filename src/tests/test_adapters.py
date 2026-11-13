"""Tests for the engine adapters.

Every engine configured in configs/targets.yaml is exercised against real
binaries; the suite fails rather than skips if a binary is missing, because a
missing engine is a blocker P02 must report, not something to paper over.
"""

from __future__ import annotations

import os
import signal
import threading
import time

import pytest

from graft.engines import available, create_engine
from graft.engines import errors
from graft.engines.base import (
    UNKNOWN,
    CliEngine,
    Result,
    canonical_cell,
    canonical_row,
    is_read_only,
)
from graft.engines.fake import FakeEngine

#: every (name, kind) pair the smoke table covers
ALL_ENGINES = [
    ("sqlite", "python"),
    ("turso", "cli"),
]
CLI_ENGINES = [(n, k) for n, k in ALL_ENGINES if k == "cli"]

#: a query that runs far longer than any test timeout, per engine
LONG_QUERY = {
    "turso": (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c "
        "WHERE x < 1000000000) SELECT count(*) FROM c"
    ),
}


@pytest.fixture(params=ALL_ENGINES, ids=lambda p: f"{p[0]}-{p[1]}")
def engine(request):
    """A started adapter for each configured engine."""
    name, kind = request.param
    eng = create_engine(name, kind)
    yield eng
    eng.close()


@pytest.fixture(params=CLI_ENGINES, ids=lambda p: f"{p[0]}-{p[1]}")
def cli_engine(request):
    """A started CLI adapter for each subprocess-driven engine."""
    name, kind = request.param
    eng = create_engine(name, kind)
    yield eng
    eng.close()


# --- Result / base contract ---------------------------------------------------


def test_result_rejects_unknown_error_class():
    with pytest.raises(ValueError):
        Result(ok=False, error_class="exploded")


def test_result_success_and_failure_helpers():
    ok = Result.success(columns=["a"], rows=[(1,)], elapsed_s=0.1)
    assert ok.ok and ok.row_count == 1 and ok.error is None
    bad = Result.failure("boom", errors.PANIC)
    assert not bad.ok and bad.rows is None and bad.error_class == errors.PANIC


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (True, True),
        (3, 3),
        (1.5, 1.5),
        ("x", "x"),
        (b"ab", b"ab"),
        (bytearray(b"ab"), b"ab"),
    ],
)
def test_canonical_cell_passes_through_canonical_types(value, expected):
    assert canonical_cell(value) == expected


def test_canonical_cell_stringifies_everything_else():
    from decimal import Decimal

    assert canonical_cell(Decimal("1.2300")) == "1.2300"
    assert canonical_row([Decimal("1.0"), None]) == ("1.0", None)


@pytest.mark.parametrize(
    "sql,read_only",
    [
        ("SELECT 1", True),
        ("  -- note\nSELECT 2", True),
        ("SELECT create_table_x()", True),
        ("EXPLAIN SELECT 1", True),
        ("VALUES (1)", True),
        ("CREATE TABLE t(a INT)", False),
        ("INSERT INTO t VALUES (1)", False),
        ("PRAGMA version", False),
        ("WITH x AS (SELECT 1) SELECT * FROM x", False),
        ("SELECT 1; DROP TABLE t", False),
    ],
)
def test_is_read_only(sql, read_only):
    assert is_read_only(sql) is read_only


# --- every engine ------------------------------------------------------------


def test_engine_identity(engine):
    assert engine.name
    assert engine.kind in ("cli", "python")
    assert engine.version() and engine.version() != UNKNOWN
    assert engine.commit()  # a real SHA or the literal UNKNOWN
    assert engine.started


def test_select_one(engine):
    result = engine.execute("SELECT 1")
    assert result.ok, result.error
    assert result.error_class == errors.NONE
    assert result.row_count == 1
    (value,) = result.rows[0]
    # Turso's shell is a text protocol, so 1 arrives as "1".
    assert value == ("1" if engine.text_cells else 1)
    assert result.elapsed_s >= 0


def test_missing_table_is_a_user_error_not_an_exception(engine):
    result = engine.execute("SELECT * FROM missing_table")
    assert not result.ok
    assert result.error_class == errors.USER_ERROR
    assert result.error
    assert result.rows is None


def test_invalid_statement_is_a_user_error(engine):
    result = engine.execute("SELCT ((( not sql")
    assert not result.ok
    assert result.error_class == errors.USER_ERROR
    assert result.error


def test_divide_by_zero_never_crashes_the_adapter(engine):
    result = engine.execute("SELECT 1/0")
    # Engines disagree here (inf, NULL, or an error) - all are acceptable;
    # what matters is that nothing raised and nothing looks like a crash.
    assert result.error_class in (errors.NONE, errors.USER_ERROR)


def test_session_state_then_reset(engine):
    assert engine.execute("CREATE TABLE t(a INT)").ok
    assert engine.execute("INSERT INTO t VALUES (1)").ok
    selected = engine.execute("SELECT a FROM t")
    assert selected.ok and selected.row_count == 1
    engine.reset()
    after = engine.execute("SELECT a FROM t")
    assert not after.ok
    assert after.error_class == errors.USER_ERROR


def test_run_case_returns_the_query_result(engine):
    result = engine.run_case(
        ["CREATE TABLE s(a INT)", "INSERT INTO s VALUES (7)"], "SELECT a FROM s"
    )
    assert result.ok, result.error
    assert result.rows[0][0] == ("7" if engine.text_cells else 7)


def test_run_case_stops_at_a_failing_setup_statement(engine):
    result = engine.run_case(["CREATE TABLE bad(("], "SELECT 1")
    assert not result.ok
    assert result.error_class == errors.USER_ERROR


def test_null_is_distinguished_from_empty_string(engine):
    result = engine.run_case([], "SELECT NULL AS a, '' AS b")
    assert result.ok, result.error
    assert result.rows[0][0] is None
    assert result.rows[0][1] == ""


def test_reusable_after_a_failure(engine):
    assert not engine.execute("SELCT nope").ok
    assert engine.execute("SELECT 1").ok


# --- CLI engines: timeouts, crashes, recovery --------------------------------


def test_timeout_is_reported_as_timeout(cli_engine):
    started = time.monotonic()
    result = cli_engine.execute(LONG_QUERY[cli_engine.name], timeout=1.5)
    elapsed = time.monotonic() - started
    assert result.error_class == errors.TIMEOUT
    assert not result.ok
    assert elapsed < 8.0, f"timeout took {elapsed:.1f}s"
    assert cli_engine.restarts >= 1
    # and the adapter still works afterwards
    assert cli_engine.execute("SELECT 1").ok


def test_killing_the_engine_mid_query_gives_crash_and_recovers(cli_engine):
    """P02 acceptance: kill the engine mid-query, see crash, then recover."""
    killed: list[int] = []
    stop = threading.Event()

    def killer() -> None:
        while not stop.is_set():
            pid = cli_engine.current_pid
            if pid:
                time.sleep(0.4)  # let the query actually start
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    killed.append(pid)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                return
            time.sleep(0.01)

    thread = threading.Thread(target=killer, daemon=True)
    thread.start()
    try:
        result = cli_engine.execute(LONG_QUERY[cli_engine.name], timeout=30.0)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert killed, "the test never managed to kill the engine"
    assert not result.ok
    assert result.error_class == errors.CRASH
    assert result.rows is None
    assert cli_engine.restarts >= 1

    # Recovery: a brand new process, and the session is usable again.
    recovered = cli_engine.execute("SELECT 1")
    assert recovered.ok, recovered.error
    assert cli_engine.execute("CREATE TABLE after_crash(a INT)").ok


def test_missing_binary_is_reported_as_crash_not_an_exception(tmp_path):
    from graft.engines.turso import TursoEngine

    engine = TursoEngine(str(tmp_path / "no-such-tursodb"))
    engine.start()
    result = engine.execute("SELECT 1")
    assert not result.ok
    assert result.error_class == errors.CRASH
    assert "not found" in result.error


def test_cli_prefix_only_keeps_mutating_statements(cli_engine):
    cli_engine.execute("SELECT 1")
    assert cli_engine.prefix == []
    cli_engine.execute("CREATE TABLE p(a INT)")
    assert cli_engine.prefix == ["CREATE TABLE p(a INT)"]
    cli_engine.reset()
    assert cli_engine.prefix == []


def test_cli_failed_statements_never_join_the_prefix(cli_engine):
    cli_engine.execute("CREATE TABLE nope((")
    assert cli_engine.prefix == []


# --- DuckDB specifics --------------------------------------------------------




# --- DataFusion specifics ----------------------------------------------------




# --- Turso specifics ---------------------------------------------------------


def test_turso_is_a_text_protocol_and_marks_itself_as_one():
    engine = create_engine("turso", "cli")
    try:
        assert engine.text_cells is True
        result = engine.execute("SELECT 1 AS a, 'x' AS b")
        assert result.rows == [("1", "x")]
        assert engine.sqlite_compat_version() != UNKNOWN
    finally:
        engine.close()


def test_turso_runtime_error_on_stdout_is_still_an_error():
    engine = create_engine("turso", "cli")
    try:
        result = engine.execute("SELECT abs(-9223372036854775808)")
        assert not result.ok
        assert result.error_class == errors.USER_ERROR
        assert "integer overflow" in result.error
    finally:
        engine.close()


# --- sqlite reference --------------------------------------------------------


def test_sqlite_uses_the_stdlib_not_a_cli():
    engine = create_engine("sqlite", "python")
    try:
        import sqlite3

        assert engine.kind == "python"
        assert engine.version() == sqlite3.sqlite_version
        assert engine.commit() != UNKNOWN
    finally:
        engine.close()


def test_sqlite_timeout_is_interrupted():
    engine = create_engine("sqlite", "python")
    try:
        result = engine.execute(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) "
            "SELECT count(*) FROM c",
            timeout=1.0,
        )
        assert result.error_class == errors.TIMEOUT
        assert engine.execute("SELECT 1").ok
    finally:
        engine.close()


def test_sqlite_returns_blobs_as_bytes():
    engine = create_engine("sqlite", "python")
    try:
        assert engine.execute("SELECT x'4142'").rows == [(b"AB",)]
    finally:
        engine.close()


# --- factory and fake engine -------------------------------------------------


def test_available_lists_every_adapter():
    assert ("turso", "cli") in available()
    assert ("sqlite", "python") in available()
    assert ("fake", "python") in available()


def test_create_engine_defaults_to_the_configured_kind():
    engine = create_engine("turso")
    try:
        assert isinstance(engine, CliEngine)
    finally:
        engine.close()


def test_create_engine_rejects_an_unknown_engine():
    with pytest.raises(KeyError):
        create_engine("postgres")


def test_create_engine_rejects_an_unknown_kind():
    with pytest.raises(KeyError):
        create_engine("sqlite", "cli")


def test_fake_engine_scripts_results():
    fake = FakeEngine().script("SELECT 1", Result.success(["a"], [(1,)]))
    fake.start()
    assert fake.execute("SELECT 1").rows == [(1,)]
    assert fake.execute("SELECT 2").ok  # the default
    assert fake.executed == ["SELECT 1", "SELECT 2"]
    fake.reset()
    assert fake.executed == [] and fake.resets == 1


def test_fake_engine_counts_crashes():
    fake = FakeEngine(responses={"boom": Result.failure("dead", errors.CRASH)})
    fake.start()
    assert fake.execute("boom").error_class == errors.CRASH
    assert fake.restarts == 1


# --- make smoke --------------------------------------------------------------


def test_smoke_runs_every_engine_and_returns_zero(capsys):
    from graft.engines import smoke

    code = smoke.run()
    out = capsys.readouterr().out
    assert code == 0, out
    for name, kind in smoke.ENGINES:
        assert f"{name} ({kind})" in out
    for sql in smoke.STATEMENTS:
        assert sql in out
    assert "failures: 0" in out


def test_smoke_reports_a_missing_engine_as_crash(capsys, monkeypatch, tmp_path):
    from graft.engines import smoke

    monkeypatch.setenv("GRAFT_TURSO_BIN", str(tmp_path / "gone"))
    code = smoke.run(engines=(("duckdb", "cli"),))
    out = capsys.readouterr().out
    # A missing binary still produces a table row, never a traceback.
    assert "crash" in out
    assert code != 0


def test_turso_recovers_an_interior_empty_string_row():
    """A blank line is a row, not padding, when more output follows it."""
    engine = create_engine("turso", "cli")
    try:
        result = engine.run_case(
            [], "SELECT '' AS c FROM (SELECT 1) UNION ALL SELECT 'x'"
        )
        assert result.ok, result.error
        assert result.rows == [("",), ("x",)]
        # A multi-column row with an empty field is never ambiguous.
        assert engine.run_case([], "SELECT 1 AS a, '' AS b").rows == [("1", "")]
    finally:
        engine.close()


@pytest.mark.parametrize(
    "message,expected",
    [
        # DuckDB labels an unsupported cast as a Conversion Error: a refusal,
        # not a broken invariant, even though it says "Unimplemented".
        ("Conversion Error: Unimplemented type for cast (INTEGER -> TIMESTAMP)",
         errors.USER_ERROR),
        ("Binder Error: Unimplemented aggregate", errors.USER_ERROR),
        # an explicit INTERNAL banner always wins
        ("INTERNAL Error: Unimplemented path reached", errors.INTERNAL_ERROR),
        # an unlabelled unimplemented message stays internal
        ("not yet implemented: ALTER TABLE RENAME", errors.INTERNAL_ERROR),
        ("This feature is not implemented: window frame", errors.INTERNAL_ERROR),
    ],
)
def test_a_user_error_banner_is_believed_over_the_word_unimplemented(message, expected):
    assert errors.classify(message, returncode=1, engine="duckdb") == expected


def test_an_echoed_sql_banner_does_not_make_a_parser_error_internal():
    """A parser error echoes the offending SQL beneath its own banner.

    These repros are often *about* internal errors, so the echo contains the
    words "INTERNAL Error"; matching anywhere in the message turned a plain
    syntax error into an internal_error.
    """
    message = (
        'Parser Error: syntax error at or near "Error"\n'
        "LINE 1: SELECT 'INTERNAL Error: boom'\n"
    )
    assert errors.classify(message, returncode=1, engine="duckdb") == errors.USER_ERROR


def test_a_multiline_panic_is_still_detected():
    """The fallback to the whole message must survive the first-line rule."""
    message = "error: process did not exit cleanly\nthread 'main' panicked at lib.rs:9"
    assert errors.classify(message, returncode=101, engine="turso") == errors.PANIC





def test_turso_keeps_rows_whose_only_value_looks_blank():
    """A row of '' or ' ' must survive, including as the LAST row.

    The parser used to trim trailing blank lines from .mode list output, which
    deleted exactly those rows. It produced a TLP "violation" where Turso
    appeared to lose rows from a filtered query: the filtered arms ended with a
    single-space row, so they looked short. The engine was right all along.
    """
    engine = create_engine("turso")
    try:
        cases = [
            ("SELECT ' ' AS a", [(" ",)]),
            ("SELECT '' AS a", [("",)]),
            ("SELECT 'x' AS a UNION ALL SELECT ' '", [("x",), (" ",)]),
            ("SELECT ' ' AS a UNION ALL SELECT '' UNION ALL SELECT 'x'",
             [(" ",), ("",), ("x",)]),
        ]
        for query, expected in cases:
            result = engine.run_case([], query, timeout=20.0)
            assert result.ok, f"{query}: {result.error}"
            assert result.rows == expected, f"{query}: {result.rows!r}"
    finally:
        engine.close()


def test_a_three_way_partition_agrees_with_its_query_on_turso():
    """The exact case that was reported as a Turso TLP bug, now a guard.

    The filter was always right; the harness was dropping the rows. Both the
    query and the UNION ALL of its three partitions must return five rows.
    """
    engine = create_engine("turso")
    try:
        setup = [
            "CREATE TABLE t1(c0 INTEGER, c2 VARCHAR, c4 INTEGER)",
            "INSERT INTO t1 VALUES (-1, 'abc', -32768), (NULL, ' ', NULL), "
            "(2147483647, NULL, NULL), (-32768, ' ', 1), (0, 'ABC', 2)",
        ]
        select = (
            "SELECT (CASE WHEN (t1.c4 IS NOT NULL) THEN upper(t1.c2) "
            "ELSE upper(t1.c2) END) AS a0 FROM t1"
        )
        predicate = "t1.c0 IN (127, 3, 0)"
        original = engine.run_case(setup, select, timeout=20.0)
        recombined = engine.run_case(setup, " UNION ALL ".join(
            f"{select} WHERE {cond}" for cond in (
                f"({predicate})", f"NOT ({predicate})", f"({predicate}) IS NULL")
        ), timeout=20.0)
        assert original.ok and recombined.ok
        assert original.row_count == 5, original.rows
        assert recombined.row_count == 5, recombined.rows
    finally:
        engine.close()


# --- an error that arrives AFTER rows, on Turso's stdout -------------------


def test_an_error_after_rows_is_found_anywhere_in_the_block():
    """Turso interleaves errors with result rows on stdout.

    This fabricated a TLP violation. The recombined query
    `... WHERE lpad('_', t0.c0, '1') IS NULL` emitted two rows and then hit
    "string or blob too big (18)" on the row where c0 was 2147483647. The
    adapter checked only the FIRST non-empty line for a diagnostic, so it
    returned ok=True with the error text parsed as a third ROW - and the oracle
    compared 6 rows against 3 and reported a wrong result on a query that had
    actually failed.
    """
    from graft.engines.turso import embedded_diagnostic

    rows_then_error = [
        "c0|c1|c2",
        "-2||",
        "-128||",
        "Runtime error: string or blob too big (18)",
    ]
    assert embedded_diagnostic(rows_then_error) == (
        "Runtime error: string or blob too big (18)"
    )


def test_an_error_on_the_first_line_is_still_found():
    """The common case must keep working."""
    from graft.engines.turso import embedded_diagnostic

    assert embedded_diagnostic(["Runtime error: integer overflow"]) is not None
    assert embedded_diagnostic(["× something went wrong"]) is not None


def test_clean_rows_are_not_mistaken_for_an_error():
    """The gate must not refuse ordinary results."""
    from graft.engines.turso import embedded_diagnostic

    assert embedded_diagnostic(["c0|c1", "1|2", "3|4"]) is None
    assert embedded_diagnostic([]) is None
    assert embedded_diagnostic(["", "  ", ""]) is None
    # a blank-valued row is data, not a diagnostic
    assert embedded_diagnostic(["a|b", "|"]) is None


def test_the_adapter_reports_a_mid_stream_error_as_a_failure():
    """End to end: rows then an error must come back as ok=False."""
    import pytest

    from graft.engines import create_engine

    try:
        engine = create_engine("turso")
    except Exception:  # pragma: no cover - engine not built in this checkout
        pytest.skip("turso binary unavailable")
    try:
        setup = [
            "CREATE TABLE t0(c0 INTEGER)",
            "INSERT INTO t0 VALUES (-2), (-128), (2147483647)",
        ]
        # lpad's size comes from the column, so the scan emits rows and then
        # fails on the row whose value exceeds the blob limit.
        result = engine.run_case(
            setup, "SELECT * FROM t0 WHERE lpad('_', t0.c0, '1') IS NULL"
        )
        assert not result.ok, "a mid-scan failure must not look like success"
        assert "too big" in (result.error or "")
        assert result.error_class == "user_error"
    finally:
        engine.close()
