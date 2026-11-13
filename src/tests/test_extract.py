"""Tests for miner.extract: SQL detection, sqllogictest parsing, drop reasons."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graft.corpus import extract

FIXTURES = Path(__file__).parent / "fixtures"


# --- looks_like_sql ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "SELECT 1",
        "  -- a comment\nCREATE TABLE t(a INT);",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "insert into t values (1)",
        "PRAGMA version",
    ],
)
def test_looks_like_sql_accepts_sql(text):
    assert extract.looks_like_sql(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Traceback (most recent call last):\n  File x",
        "thread 'main' panicked at src/lib.rs:1",
        "INTERNAL Error: Assertion triggered",
        "$ duckdb -c 'SELECT 1'",
        "error: could not compile",
        "pip install duckdb",
        "just some prose about SELECT statements",
        "0 rows returned",
    ],
)
def test_looks_like_sql_rejects_output_and_prose(text):
    assert not extract.looks_like_sql(text)


# --- statement splitting -----------------------------------------------------


def test_split_statements_basic():
    assert extract.split_statements("CREATE TABLE t(a); SELECT 1;") == [
        "CREATE TABLE t(a)",
        "SELECT 1",
    ]


def test_split_statements_keeps_semicolons_inside_literals():
    assert extract.split_statements("SELECT ';'; SELECT 2") == [
        "SELECT ';'",
        "SELECT 2",
    ]


def test_split_statements_handles_escaped_quotes():
    assert extract.split_statements("SELECT 'it''s; fine'") == ["SELECT 'it''s; fine'"]


def test_split_statements_ignores_semicolons_in_comments():
    script = "SELECT 1 -- a; comment\n;\n/* b; */ SELECT 2"
    assert extract.split_statements(script) == [
        "SELECT 1 -- a; comment",
        "/* b; */ SELECT 2",
    ]


def test_split_statements_drops_comment_only_statements():
    assert extract.split_statements("-- nothing here\n;") == []


def test_is_query():
    assert extract.is_query("SELECT 1")
    assert extract.is_query("  WITH x AS (SELECT 1) SELECT * FROM x")
    assert not extract.is_query("CREATE TABLE t(a INT)")
    assert not extract.is_query("INSERT INTO t VALUES (1)")


# --- issue bodies ------------------------------------------------------------


def test_sql_blocks_takes_tagged_and_plausible_untagged_fences():
    body = (
        "text\n```sql\nSELECT 1;\n```\n"
        "```\nCREATE TABLE t(a INT);\n```\n"
        "```\nINTERNAL Error: boom\n```\n"
        "```rust\nfn main() {}\n```\n"
    )
    blocks = extract.sql_blocks(body)
    assert len(blocks) == 2
    assert "SELECT 1" in blocks[0]
    assert "CREATE TABLE" in blocks[1]


def test_sql_blocks_handles_an_unterminated_fence():
    assert extract.sql_blocks("```sql\nSELECT 1;") == ["SELECT 1;"]


def test_repros_from_body_uses_the_last_query_as_the_query():
    body = "```sql\nCREATE TABLE t(a INT);\nINSERT INTO t VALUES (1);\nSELECT a FROM t;\n```"
    (repro, _), = list(extract.repros_from_body(7, body))
    assert repro.number == 7
    assert repro.setup == ["CREATE TABLE t(a INT)", "INSERT INTO t VALUES (1)"]
    assert repro.query == "SELECT a FROM t"
    assert repro.source == extract.SOURCE_ISSUE_BODY
    assert repro.expected is None


def test_repros_from_body_skips_a_block_with_no_query():
    assert list(extract.repros_from_body(1, "```sql\nCREATE TABLE t(a INT);\n```")) == []


# --- sqllogictest ------------------------------------------------------------


def test_parse_sqllogictest_sample_fixture():
    text = (FIXTURES / "sample.slt").read_text(encoding="utf-8")
    triples = extract.parse_sqllogictest(text)
    assert len(triples) == 3

    setup, query, expected = triples[0]
    assert setup == [
        "CREATE TABLE t0(c0 INTEGER, c1 VARCHAR)",
        "INSERT INTO t0 VALUES (1, 'a'), (NULL, 'b')",
    ]
    assert query == "SELECT c0, c1 FROM t0 ORDER BY c1"
    assert expected == ["1\ta", "NULL\tb"]

    # the onlyif conditional is skipped but its query is still read
    assert triples[1][1] == "SELECT count(*) FROM t0 WHERE c0 IS NULL"
    assert triples[1][2] == ["1"]
    # "require tpch" is a control directive, not SQL
    assert triples[2][1] == "SELECT 1 + 1"
    assert triples[2][2] == ["2"]


def test_an_expected_error_statement_never_becomes_setup():
    text = "statement error\nSELECT * FROM nope;\n\nquery I\nSELECT 1;\n----\n1\n"
    (setup, query, expected), = extract.parse_sqllogictest(text)
    assert setup == []
    assert query == "SELECT 1" and expected == ["1"]


def test_expected_rows_never_leak_into_setup():
    """The real bug: a query whose directive line was lost to the diff."""
    text = (
        "select map_extract(MAP {'a': 1}, 'a')\n"
        "----\n"
        "[1]\n"
        "\n"
        "query I\n"
        "SELECT 2;\n"
        "----\n"
        "2\n"
    )
    triples = extract.parse_sqllogictest(text)
    assert triples[0][1] == "select map_extract(MAP {'a': 1}, 'a')"
    assert triples[0][2] == ["[1]"]
    for setup, query, _ in triples:
        joined = "\n".join([*setup, query])
        assert extract.RESULT_SEPARATOR not in joined.splitlines()
        assert "[1]" not in setup


def test_a_stray_separator_does_not_hang_the_parser():
    assert extract.parse_sqllogictest("----\n----\n\n----\n") == []


def test_parse_sqllogictest_ignores_comments_and_blank_lines():
    assert extract.parse_sqllogictest("# just a comment\n\n\n") == []


# --- diff reconstruction -----------------------------------------------------


def test_diff_files_splits_on_the_recorded_headers():
    text = "--- a/tests/x.slt\n@@ -1 +1 @@\n+SELECT 1;\n--- src/y.rs\n+fn f() {}\n"
    files = extract.diff_files(text)
    assert [name for name, _ in files] == ["a/tests/x.slt", "src/y.rs"]


def test_applied_text_keeps_added_and_context_drops_removed():
    patch = "@@ -1,3 +1,3 @@\n context\n-removed\n+added\n"
    assert extract.applied_text(patch) == "context\nadded"


def test_repros_from_fix_test_only_reads_test_files():
    text = (
        "--- src/engine.rs\n+SELECT 'not a test file';\n"
        "--- test/sql/x.test\n@@ -0,0 +1,5 @@\n"
        "+statement ok\n+CREATE TABLE t(a INT);\n+\n+query I\n+SELECT a FROM t;\n+----\n+1\n"
    )
    repros = [r for r, _ in extract.repros_from_fix_test(9, text)]
    assert len(repros) == 1
    assert repros[0].setup == ["CREATE TABLE t(a INT)"]
    assert repros[0].query == "SELECT a FROM t"
    assert repros[0].expected == ["1"]
    assert repros[0].source == extract.SOURCE_FIX_TEST


# --- self-containment --------------------------------------------------------


def test_a_repro_that_creates_its_tables_is_self_contained():
    assert extract.self_contained_reason(["CREATE TABLE t(a INT)"], "SELECT a FROM t") is None


def test_a_constants_only_query_is_self_contained():
    assert extract.self_contained_reason([], "SELECT 1 + 1") is None
    assert extract.self_contained_reason([], "SELECT * FROM (VALUES (1), (2)) v(a)") is None
    assert extract.self_contained_reason([], "SELECT count(*) FROM range(10)") is None


def test_a_cte_is_not_an_unknown_table():
    assert extract.self_contained_reason(
        [], "WITH x AS (SELECT 1 AS a) SELECT a FROM x"
    ) is None


@pytest.mark.parametrize(
    "setup,query,reason",
    [
        ([], "SELECT * FROM lineitem", "unknown_table"),
        ([], "SELECT * FROM read_csv('x.csv')", "external_file"),
        ([], "SELECT * FROM read_parquet('/tmp/a.parquet')", "external_file"),
        ([], "COPY t TO 'out.csv'", "external_file"),
        ([], "SELECT * FROM 'https://example.com/a.csv'", "network"),
        (["INSTALL spatial"], "SELECT st_point(1,2)", "extension"),
        ([], "SELECT * FROM {table}", "test_template"),
        ([], "SELECT * FROM t WHERE x = '${var}'", "test_template"),
        (["CREATE TABLE t(a INT)"], "SELECT * FROM t\n----\n1", "test_result_leak"),
    ],
)
def test_drop_reasons(setup, query, reason):
    assert extract.self_contained_reason(setup, query) == reason


def test_a_struct_literal_is_not_mistaken_for_a_template():
    assert extract.self_contained_reason([], "SELECT {'x': 1}") is None
    assert extract.self_contained_reason([], "SELECT MAP {'a': 1}") is None


def test_setup_that_is_too_long_is_dropped():
    setup = [f"CREATE TABLE t{i}(a INT)" for i in range(extract.MAX_SETUP_STATEMENTS + 1)]
    assert extract.self_contained_reason(setup, "SELECT 1") == "setup_too_long"


def test_unknown_tables_ignores_table_functions_and_pseudo_sources():
    assert extract.unknown_tables(["SELECT * FROM generate_series(1, 5)"]) == set()
    assert extract.unknown_tables(["SELECT * FROM my_udtf(3)"]) == set()
    assert extract.unknown_tables(["SELECT * FROM real_table"]) == {"real_table"}


# --- end to end --------------------------------------------------------------


def test_extract_issue_reports_a_drop_reason_for_every_candidate():
    issue = {
        "number": 5,
        "body": "```sql\nSELECT * FROM lineitem;\n```\n```sql\nSELECT 1;\n```",
        "fix_test_text": "",
    }
    results = list(extract.extract_issue(issue))
    reasons = {reason for _, reason in results}
    assert "unknown_table" in reasons
    assert None in reasons  # the constants-only repro survives


def test_extract_issue_reports_no_sql_found():
    issue = {"number": 6, "body": "no code here", "fix_test_text": ""}
    (repro, reason), = list(extract.extract_issue(issue))
    assert reason == "no_sql_found" and repro.number == 6


def test_extract_target_writes_jsonl_and_meta(tmp_path):
    mined = tmp_path / "mined" / "demo"
    mined.mkdir(parents=True)
    issues = [
        {
            "number": 2,
            "body": "```sql\nCREATE TABLE t(a INT);\nSELECT a FROM t;\n```",
            "fix_test_text": "",
        },
        {"number": 1, "body": "```sql\nSELECT * FROM nope;\n```", "fix_test_text": ""},
    ]
    mined.joinpath("issues.jsonl").write_text(
        "\n".join(json.dumps(i) for i in issues) + "\n", encoding="utf-8"
    )

    stats = extract.extract_target(
        "demo", mined_dir=tmp_path / "mined", out_dir=tmp_path / "repros"
    )
    assert stats.issues == 2 and stats.kept == 1
    assert stats.drops["unknown_table"] == 1
    records = [
        json.loads(l)
        for l in (tmp_path / "repros" / "demo.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    assert list(records[0]) == ["number", "setup", "query", "expected", "source"]
    meta = json.loads((tmp_path / "repros" / "demo.meta.json").read_text())
    assert meta["kept"] == 1 and meta["drops"]["unknown_table"] == 1


# --- the extracted data on disk ----------------------------------------------


def test_the_extracted_repros_on_disk_are_well_formed():
    root = Path("data/repros")
    for target in ("duckdb",):
        records = [
            json.loads(l)
            for l in (root / f"{target}.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert records, f"{target} has no repros"
        for record in records:
            assert list(record) == ["number", "setup", "query", "expected", "source"]
            assert isinstance(record["number"], int)
            assert isinstance(record["setup"], list)
            assert record["query"].strip(), f"{target} #{record['number']} empty query"
            assert record["source"] in (
                extract.SOURCE_ISSUE_BODY,
                extract.SOURCE_FIX_TEST,
            )
            assert record["expected"] is None or isinstance(record["expected"], list)
            script = "\n".join([*record["setup"], record["query"]])
            assert extract.self_contained_reason(record["setup"], record["query"]) is None
            assert extract.RESULT_SEPARATOR not in script.splitlines()
        meta = json.loads((root / f"{target}.meta.json").read_text())
        assert meta["kept"] == len(records)


def test_expected_error_messages_never_become_statements():
    """Prose after `statement error ----` is not SQL, even if it starts "Table"."""
    text = (
        "statement ok\n"
        "CREATE TABLE w (i INT UNIQUE);\n"
        "\n"
        "statement error\n"
        "INSERT INTO w (zz) VALUES (1);\n"
        "----\n"
        'Table "w" does not have a column with name "zz"\n'
        "\n"
        "query I\n"
        "SELECT i FROM w;\n"
        "----\n"
        "1\n"
    )
    (setup, query, expected), = extract.parse_sqllogictest(text)
    assert setup == ["CREATE TABLE w (i INT UNIQUE)"]
    assert query == "SELECT i FROM w"
    assert not any("does not have" in s for s in setup)


def test_prose_beginning_with_table_is_not_sql():
    assert not extract.looks_like_sql('Table "w" does not have a column named "zz"')
    assert not extract.looks_like_sql("table w has 2 columns but 3 values were supplied")
    assert extract.looks_like_sql("TABLE t")  # the DuckDB shorthand still counts


def test_repl_prompts_are_stripped_not_dropped():
    body = "```sql\nturso> CREATE TABLE t(a);\nturso> SELECT a FROM t;\n```"
    (repro, _), = list(extract.repros_from_body(1, body))
    assert repro.setup == ["CREATE TABLE t(a)"]
    assert repro.query == "SELECT a FROM t"


def test_strip_prompts_leaves_plain_sql_alone():
    assert extract.strip_prompts("SELECT 1;") == "SELECT 1;"
    assert extract.strip_prompts("sqlite> SELECT 1;") == "SELECT 1;"
    assert extract.strip_prompts("D SELECT 1;") == "D SELECT 1;"


@pytest.mark.parametrize(
    "statement,balanced",
    [
        ("SELECT (1 + 2)", True),
        ("with data as (", False),
        ("SELECT ')'", True),
        ("SELECT [1, 2]", True),
        ("SELECT 'unterminated", False),
        ("SELECT 1) extra", False),
        ("SELECT 1 -- (unclosed in comment", True),
    ],
)
def test_is_balanced(statement, balanced):
    assert extract.is_balanced(statement) is balanced


def test_a_trigger_body_is_not_split_at_its_inner_semicolons():
    """A CREATE TRIGGER body is one statement, END included.

    Splitting it left a trigger with no END, which SQLite rejects as
    "incomplete input" while Turso tolerated it - a divergence that looked like
    an engine bug and was entirely self-inflicted.
    """
    script = (
        "CREATE TABLE log(v);\n"
        "CREATE TRIGGER trig AFTER INSERT ON dst BEGIN\n"
        "  INSERT INTO log SELECT a FROM src ORDER BY b;\n"
        "END;\n"
        "SELECT * FROM log;"
    )
    statements = extract.split_statements(script)
    assert len(statements) == 3
    assert statements[0] == "CREATE TABLE log(v)"
    assert statements[1].startswith("CREATE TRIGGER")
    assert statements[1].rstrip().endswith("END")
    assert "INSERT INTO log" in statements[1]
    assert statements[2] == "SELECT * FROM log"


def test_a_transaction_begin_is_still_its_own_statement():
    """Only a trigger's BEGIN opens a body; a transaction BEGIN does not."""
    statements = extract.split_statements("BEGIN; INSERT INTO t VALUES (1); COMMIT;")
    assert statements == ["BEGIN", "INSERT INTO t VALUES (1)", "COMMIT"]
