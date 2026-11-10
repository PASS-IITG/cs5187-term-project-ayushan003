"""Turning mined issues into runnable, self-contained repro records.

Two sources of SQL, per P04:

1. **Issue bodies** - fenced ```sql blocks, plus untagged fenced blocks that
   look like SQL. Bodies also carry error banners, stack traces and shell
   transcripts inside fences, so an untagged block has to earn its place:
   :func:`looks_like_sql` requires a leading SQL keyword and rejects anything
   carrying log or traceback markers. Pasted REPL prompts (``turso> ``,
   ``duckdb> ``) are stripped, since the SQL after them is still usable.
2. **fix_test_text** - the regression test the fix added, in sqllogictest form.
   P03 stored it as a unified diff, so the "after" text is reconstructed from
   the added and context lines (``+`` and `` ``), and only files named ``.slt``
   or ``.test`` are parsed. This module parses that diff mechanically; no diff
   is read by a human when classifying (P04's limit).

Only *self-contained* repros survive: a repro must create the tables it reads,
or read nothing but constants and table functions. Anything that needs a file
on disk, the network, an extension, or a test-runner template variable is
dropped with a recorded reason, and the counts land in
``data/repros/<target>.meta.json``. The drop reasons are ``external_file``,
``network``, ``extension``, ``test_template``, ``truncated_sql``,
``unknown_table``, ``setup_too_long``, ``no_query`` and ``no_sql_found``.

Output ``data/repros/<target>.jsonl``, one JSON object per line:
``number``, ``setup`` (list of statements), ``query``, ``expected`` (or null),
``source`` (``issue_body`` or ``fix_test``).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from graft.config import REPO_ROOT

#: the two sources P04 names
SOURCE_ISSUE_BODY = "issue_body"
SOURCE_FIX_TEST = "fix_test"

#: sqllogictest files worth parsing out of a fix
TEST_FILE_SUFFIXES = (".slt", ".test", ".test_slow")

#: statements may not pile up without limit; a repro this long is not a repro
MAX_SETUP_STATEMENTS = 40

#: keywords a SQL statement can legitimately start with
_SQL_START = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*"
    r"(?:(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|VALUES|"
    r"EXPLAIN|PRAGMA|SET|BEGIN|COMMIT|ROLLBACK|ATTACH|COPY|ANALYZE|"
    r"VACUUM|REINDEX|DESCRIBE|SHOW|CALL|PREPARE|EXECUTE)\b"
    # "TABLE" only starts a statement in the "TABLE <name>" shorthand. Without
    # this restriction, an expected-error message such as
    # 'Table "w" does not have a column with name "zz"' reads as SQL.
    r"|TABLE\s+[\w\".]+\s*;?\s*$)",
    re.IGNORECASE | re.DOTALL,
)

#: text that proves a fenced block is output, not SQL
_NOT_SQL_MARKERS = (
    "traceback (most recent call last)",
    "thread 'main' panicked",
    "panicked at",
    "internal error:",
    "internal error ",
    "error: ",
    "stack backtrace",
    "#0 ",
    "at /",
    "$ ",
    "warning:",
    "note:",
    "<module>",
    "cargo ",
    "pip install",
    "docker ",
)

#: things a repro cannot provide for itself, and the reason to record
_EXTERNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "network",
        re.compile(r"\b(?:https?|s3|gs|gcs|az|azure|abfss?|hf)://", re.IGNORECASE),
    ),
    (
        "external_file",
        re.compile(
            r"\b(?:read_csv|read_csv_auto|read_parquet|parquet_scan|read_json|"
            r"read_json_auto|read_ndjson|read_text|read_blob|read_xlsx|"
            r"sniff_csv|glob|delta_scan|iceberg_scan|sqlite_scan|"
            r"postgres_scan|mysql_scan)\s*\(",
            re.IGNORECASE,
        ),
    ),
    (
        "external_file",
        re.compile(
            r"\bCOPY\b[\s\S]{0,200}?\b(?:FROM|TO)\s*'|"
            r"\bATTACH\b[^;]{0,200}?'[^']*\.(?:db|sqlite|parquet|csv|duckdb)'|"
            r"'[^']*\.(?:csv|tsv|parquet|json|ndjson|xlsx|db|sqlite|duckdb|arrow)'",
            re.IGNORECASE,
        ),
    ),
    (
        "extension",
        re.compile(
            r"\b(?:INSTALL|LOAD)\s+[a-z_]+|"
            r"\b(?:st_[a-z_]+|h3_[a-z_]+|excel_[a-z_]+|vss_[a-z_]+)\s*\(|"
            r"\bCREATE\s+(?:OR\s+REPLACE\s+)?SECRET\b",
            re.IGNORECASE,
        ),
    ),
)

#: placeholders that make a sqllogictest file a *template*, not runnable SQL.
#: DuckDB's test runner expands ``${var}`` and ``{var}`` from loop/foreach
#: directives and ``__TEST_DIR__``-style tokens from the build. Note the
#: identifier-in-braces form is deliberately narrow so it cannot match a
#: DuckDB struct literal like ``{'x': 1}`` or ``MAP {'a': 1}``.
_TEMPLATE_RE = re.compile(
    r"\$\{|\{[A-Za-z_][A-Za-z_0-9]*\}|__TEST_DIR__|__WORKING_DIRECTORY__|"
    r"__BUILD_DIRECTORY__|\bBUILD_DIRECTORY\b"
)

#: names that may appear after FROM/JOIN without being a real table
_PSEUDO_SOURCES = frozenset(
    {
        "generate_series",
        "range",
        "unnest",
        "values",
        "repeat",
        "duckdb_functions",
        "duckdb_settings",
        "duckdb_types",
        "pragma_version",
        "pragma_table_info",
        "sqlite_master",
        "information_schema",
        "generate_subscripts",
        "query_range",
        "lateral",
        "json_each",
        "json_tree",
        "dual",
    }
)

_CREATE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?"
    r"(?:TABLE|VIEW|MACRO|FUNCTION|TYPE|SEQUENCE)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([\"'`\[\]\w.]+)",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([\"'`\[\]\w.]+)\s*(\()?",
    re.IGNORECASE,
)
_CTE_RE = re.compile(r"\b(?:WITH|,)\s+(?:RECURSIVE\s+)?([\w\"`]+)\s+AS\s*\(", re.IGNORECASE)
_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)(?:```|\Z)", re.DOTALL)

#: REPL prompts people paste along with their SQL. Stripped rather than used as
#: a reason to drop the block: the SQL after the prompt is still a good repro.
_PROMPT_RE = re.compile(
    r"^(?:turso|duckdb|sqlite3?|datafusion|psql|postgres|mysql)?\s*(?:>|\$)\s+",
    re.IGNORECASE,
)


def strip_prompts(text: str) -> str:
    """Remove leading REPL prompts such as ``turso> `` from each line."""
    return "\n".join(_PROMPT_RE.sub("", line) for line in (text or "").splitlines())


@dataclass
class Repro:
    """One runnable repro, exactly the P04 record shape."""

    number: int
    setup: list[str]
    query: str
    expected: list[str] | None
    source: str

    def to_json(self) -> str:
        """Render as one JSONL line."""
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class ExtractStats:
    """Counters for one target, including why repros were dropped."""

    target: str = ""
    issues: int = 0
    candidates: int = 0
    kept: int = 0
    issues_with_repro: int = 0
    from_issue_body: int = 0
    from_fix_test: int = 0
    drops: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        """JSON-ready summary."""
        return {
            "target": self.target,
            "issues": self.issues,
            "candidates": self.candidates,
            "kept": self.kept,
            "issues_with_repro": self.issues_with_repro,
            "from_issue_body": self.from_issue_body,
            "from_fix_test": self.from_fix_test,
            "drops": dict(sorted(self.drops.items())),
        }


# --- "is this SQL?" ----------------------------------------------------------


def looks_like_sql(text: str) -> bool:
    """True if ``text`` is plausibly a SQL script rather than program output.

    Requires a leading SQL keyword and rejects anything carrying a traceback,
    a panic, an error banner or a shell prompt - the things issue bodies put in
    untagged fences next to the SQL.
    """
    if not text or not text.strip():
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _NOT_SQL_MARKERS):
        return False
    return bool(_SQL_START.match(text))


def split_statements(script: str) -> list[str]:
    """Split a SQL script on semicolons that are not inside a string or comment.

    Returns statements with no trailing semicolon and no surrounding blanks.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(script)
    while index < length:
        char = script[index]
        if quote:
            current.append(char)
            if char == "\\" and quote != "'" and index + 1 < length:
                current.append(script[index + 1])
                index += 2
                continue
            if char == quote:
                # '' inside a single-quoted literal is an escaped quote
                if quote == "'" and index + 1 < length and script[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "-" and script.startswith("--", index):
            end = script.find("\n", index)
            end = length if end == -1 else end
            current.append(script[index:end])
            index = end
            continue
        if char == "/" and script.startswith("/*", index):
            end = script.find("*/", index)
            end = length if end == -1 else end + 2
            current.append(script[index:end])
            index = end
            continue
        if char == ";":
            # A CREATE TRIGGER body is `BEGIN <statements> END`, and its inner
            # semicolons are part of one statement. Splitting them produced a
            # trigger with no END, which SQLite rejects as "incomplete input"
            # while Turso tolerated it - a difference that looked like an
            # engine bug and was entirely self-inflicted.
            so_far = "".join(current)
            if _in_trigger_body(so_far):
                current.append(char)
                index += 1
                continue
            statements.append(so_far.strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s and not _is_only_comment(s)]


_TRIGGER_RE = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TRIGGER\b", re.IGNORECASE)
_BEGIN_RE = re.compile(r"\bBEGIN\b", re.IGNORECASE)
_END_RE = re.compile(r"\bEND\b", re.IGNORECASE)


def _in_trigger_body(text: str) -> bool:
    """True if ``text`` is inside an unterminated CREATE TRIGGER body."""
    if not _TRIGGER_RE.search(text):
        return False
    return len(_BEGIN_RE.findall(text)) > len(_END_RE.findall(text))


def _is_only_comment(statement: str) -> bool:
    stripped = re.sub(r"/\*.*?\*/", "", statement, flags=re.DOTALL)
    lines = [l.strip() for l in stripped.splitlines()]
    return all(not l or l.startswith("--") for l in lines)


def is_balanced(statement: str) -> bool:
    """True if brackets outside string literals are balanced.

    A diff hunk can end in the middle of a statement, leaving a fragment such as
    ``with data as (``. Such a fragment is not a runnable repro, so it is
    dropped rather than handed to an engine as a guaranteed parse error.
    """
    depth = {"(": 0, "[": 0}
    closers = {")": "(", "]": "["}
    quote: str | None = None
    index = 0
    while index < len(statement):
        char = statement[index]
        if quote:
            if char == quote:
                if quote == "'" and statement[index : index + 2] == "''":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "-" and statement.startswith("--", index):
            end = statement.find("\n", index)
            index = len(statement) if end == -1 else end
            continue
        elif char in depth:
            depth[char] += 1
        elif char in closers:
            depth[closers[char]] -= 1
            if depth[closers[char]] < 0:
                return False
        index += 1
    return all(value == 0 for value in depth.values()) and quote is None


def is_query(statement: str) -> bool:
    """True if ``statement`` returns rows, so it can be the repro's query."""
    return bool(
        re.match(
            r"^\s*(?:--[^\n]*\n|\s)*(SELECT|WITH|VALUES|TABLE|FROM|EXPLAIN|PRAGMA|"
            r"DESCRIBE|SHOW)\b",
            statement,
            re.IGNORECASE,
        )
    )


# --- self-containment --------------------------------------------------------


def _bare(name: str) -> str:
    return name.strip("\"'`[]").split(".")[-1].lower()


def external_reason(script: str) -> str | None:
    """Return the drop reason if ``script`` needs something it cannot provide."""
    for reason, pattern in _EXTERNAL_PATTERNS:
        if pattern.search(script):
            return reason
    return None


def unknown_tables(statements: Sequence[str]) -> set[str]:
    """Return table names read but never created inside this repro."""
    script = "\n".join(statements)
    created = {_bare(m.group(1)) for m in _CREATE_RE.finditer(script)}
    ctes = {_bare(m.group(1)) for m in _CTE_RE.finditer(script)}
    referenced: set[str] = set()
    for match in _REFERENCE_RE.finditer(script):
        name = _bare(match.group(1))
        if match.group(2):  # followed by "(": a table function, not a table
            continue
        if not name or name in _PSEUDO_SOURCES or name.isdigit():
            continue
        if re.fullmatch(r"(select|values|lateral|unnest|\()", name):
            continue
        referenced.add(name)
    return referenced - created - ctes


def self_contained_reason(setup: Sequence[str], query: str) -> str | None:
    """Return None if the repro can run on an empty database, else the reason.

    A repro qualifies when it creates everything it reads, or reads nothing but
    constants and table functions.
    """
    statements = [*setup, query]
    script = "\n".join(statements)
    if any(
        line.strip() == RESULT_SEPARATOR for line in script.splitlines()
    ):
        # Belt and braces for the parser: expected-result text is never SQL.
        return "test_result_leak"
    if _TEMPLATE_RE.search(script):
        return "test_template"
    reason = external_reason(script)
    if reason:
        return reason
    if len(setup) > MAX_SETUP_STATEMENTS:
        return "setup_too_long"
    if not all(is_balanced(statement) for statement in statements):
        return "truncated_sql"
    missing = unknown_tables(statements)
    if missing:
        return "unknown_table"
    return None


# --- issue bodies ------------------------------------------------------------


def sql_blocks(body: str) -> list[str]:
    """Return the SQL scripts fenced in an issue body, in order.

    A block tagged ``sql`` is taken as SQL. An untagged block has to pass
    :func:`looks_like_sql`. Blocks tagged anything else (``text``, ``console``,
    ``rust``, ...) are skipped.
    """
    blocks: list[str] = []
    for match in _FENCE_RE.finditer(body or ""):
        tag = match.group(1).strip().lower()
        content = match.group(2)
        cleaned = strip_prompts(content)
        if tag in ("sql", "sqlite", "duckdb", "postgresql", "psql"):
            blocks.append(cleaned)
        elif tag == "" and looks_like_sql(cleaned):
            blocks.append(cleaned)
    return blocks


def repros_from_body(number: int, body: str) -> Iterator[tuple[Repro, None]]:
    """Yield candidate repros from one issue body."""
    for block in sql_blocks(body):
        statements = split_statements(block)
        if not statements:
            continue
        queries = [i for i, s in enumerate(statements) if is_query(s)]
        if not queries:
            continue
        last = queries[-1]
        yield (
            Repro(
                number=number,
                setup=statements[:last],
                query=statements[last],
                expected=None,
                source=SOURCE_ISSUE_BODY,
            ),
            None,
        )


# --- sqllogictest inside a fix diff ------------------------------------------


def diff_files(fix_test_text: str) -> list[tuple[str, str]]:
    """Split P03's ``fix_test_text`` into (filename, patch) pairs."""
    parts = re.split(r"^--- (.+)$", fix_test_text or "", flags=re.MULTILINE)
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1]))
    return out


def applied_text(patch: str) -> str:
    """Reconstruct a file's post-fix text from a unified diff hunk body.

    Added (``+``) and context (`` ``) lines survive; removed (``-``) lines and
    hunk headers do not.
    """
    lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith("@@") or line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            lines.append(line[1:])
        elif not line.strip():
            lines.append("")
    return "\n".join(lines)


def parse_sqllogictest(text: str) -> list[tuple[list[str], str, list[str] | None]]:
    """Parse sqllogictest text into (setup, query, expected) triples.

    Handles ``statement ok``, ``statement error``, ``query <types>`` with a
    ``----`` separator and expected rows, ``#`` comments and the control
    directives (``require``, ``load``, ``halt``, ``mode``, ``onlyif``,
    ``skipif``, ``hash-threshold``). One triple is produced per query, with
    every ``statement ok`` seen so far as its setup, which is how a
    sqllogictest file actually runs.
    """
    lines = (text or "").splitlines()
    setup: list[str] = []
    out: list[tuple[list[str], str, list[str] | None]] = []
    index = 0
    total = len(lines)

    while index < total:
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        lowered = stripped.lower()

        # conditionals prefix the next directive; skip the marker itself
        if re.match(r"^(onlyif|skipif)\b", lowered):
            index += 1
            continue
        if re.match(r"^(require|load|mode|hash-threshold|halt|control|sleep)\b", lowered):
            index += 1
            continue

        statement_match = re.match(r"^statement\s+(ok|error|count\s+\d+)\b", lowered)
        if statement_match:
            index += 1
            body, index = _read_until_blank(lines, index)
            expects_error = statement_match.group(1).startswith("error")
            if index < total and lines[index].strip() == RESULT_SEPARATOR:
                # "statement error" may be followed by ----<expected message>.
                # That message is English prose, not SQL, and must be consumed
                # here or it is later mistaken for a statement.
                index += 1
                _, index = _read_until_blank(lines, index)
            if not body or expects_error:
                continue  # an expected-error statement is not usable setup
            setup.extend(split_statements("\n".join(body)))
            continue

        if re.match(r"^query\b", lowered):
            index += 1
            body, index = _read_until(lines, index, "----")
            expected: list[str] | None = None
            if index < total and lines[index].strip() == "----":
                index += 1
                expected, index = _read_until_blank(lines, index)
            sql = "\n".join(body).strip()
            if sql:
                statements = split_statements(sql)
                query = statements[-1] if statements else sql
                extra = statements[:-1]
                out.append(([*setup, *extra], query, expected))
            continue

        # A bare SQL block outside any directive. If a ``----`` follows it, the
        # ``query`` directive line was a diff context line that did not survive,
        # so this is a query with expected rows - not setup.
        body, index = _read_until_blank(lines, index)
        if not body:
            # Nothing consumed - a stray separator or result row. Step over it,
            # or this loop never terminates.
            index += 1
            continue
        if not looks_like_sql("\n".join(body)):
            continue
        if index < total and lines[index].strip() == RESULT_SEPARATOR:
            index += 1
            expected, index = _read_until_blank(lines, index)
            statements = split_statements("\n".join(body))
            if statements:
                out.append(([*setup, *statements[:-1]], statements[-1], expected))
            continue
        setup.extend(split_statements("\n".join(body)))
    return out


#: sqllogictest separates a query from its expected rows with this line
RESULT_SEPARATOR = "----"


def _read_until_blank(lines: Sequence[str], index: int) -> tuple[list[str], int]:
    """Read to the next blank line, stopping short of a ``----`` separator.

    Stopping at the separator matters: without it, the expected result rows that
    follow get swallowed into a SQL statement, and a repro ends up carrying
    ``----\n[1] [NULL] [3] []`` as if it were SQL.
    """
    body: list[str] = []
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped == RESULT_SEPARATOR:
            break
        body.append(lines[index])
        index += 1
    return body, index


def _read_until(
    lines: Sequence[str], index: int, sentinel: str
) -> tuple[list[str], int]:
    body: list[str] = []
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == sentinel or not stripped:
            break
        body.append(lines[index])
        index += 1
    return body, index


def repros_from_fix_test(
    number: int, fix_test_text: str
) -> Iterator[tuple[Repro, None]]:
    """Yield candidate repros from the sqllogictest files a fix touched."""
    for filename, patch in diff_files(fix_test_text):
        if not filename.lower().endswith(TEST_FILE_SUFFIXES):
            continue
        for setup, query, expected in parse_sqllogictest(applied_text(patch)):
            yield (
                Repro(
                    number=number,
                    setup=setup,
                    query=query,
                    expected=expected,
                    source=SOURCE_FIX_TEST,
                ),
                None,
            )


# --- the whole pipeline ------------------------------------------------------


def extract_issue(issue: dict) -> Iterator[tuple[Repro, str | None]]:
    """Yield (repro, drop_reason) for every candidate in one mined issue.

    ``drop_reason`` is None for a repro that should be kept.
    """
    number = int(issue["number"])
    candidates: list[Repro] = []
    for repro, _ in repros_from_fix_test(number, issue.get("fix_test_text") or ""):
        candidates.append(repro)
    for repro, _ in repros_from_body(number, issue.get("body") or ""):
        candidates.append(repro)

    if not candidates:
        yield Repro(number, [], "", None, SOURCE_ISSUE_BODY), "no_sql_found"
        return

    for repro in candidates:
        if not repro.query:
            yield repro, "no_query"
            continue
        reason = self_contained_reason(repro.setup, repro.query)
        yield repro, reason


def extract_target(
    target: str,
    mined_dir: Path | None = None,
    out_dir: Path | None = None,
) -> ExtractStats:
    """Extract every repro for one target and write ``data/repros/<target>.*``."""
    mined = (mined_dir or REPO_ROOT / "data" / "mined") / target / "issues.jsonl"
    stats = ExtractStats(target=target)
    kept: list[Repro] = []
    seen_issues: set[int] = set()

    for line in mined.read_text(encoding="utf-8").splitlines():
        issue = json.loads(line)
        stats.issues += 1
        for repro, reason in extract_issue(issue):
            if reason == "no_sql_found":
                stats.drops[reason] += 1
                continue
            stats.candidates += 1
            if reason:
                stats.drops[reason] += 1
                continue
            kept.append(repro)
            stats.kept += 1
            seen_issues.add(repro.number)
            if repro.source == SOURCE_FIX_TEST:
                stats.from_fix_test += 1
            else:
                stats.from_issue_body += 1

    stats.issues_with_repro = len(seen_issues)
    directory = out_dir or REPO_ROOT / "data" / "repros"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{target}.jsonl").open("w", encoding="utf-8") as handle:
        for repro in kept:
            handle.write(repro.to_json() + "\n")
    (directory / f"{target}.meta.json").write_text(
        json.dumps(stats.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return stats


def main(targets: Iterable[str] = ("duckdb", "datafusion", "turso")) -> int:
    """Entry point for ``python -m miner.extract``."""
    for target in targets:
        stats = extract_target(target)
        print(
            f"{target:11} issues={stats.issues} candidates={stats.candidates} "
            f"kept={stats.kept} issues_with_repro={stats.issues_with_repro} "
            f"(body={stats.from_issue_body} fix_test={stats.from_fix_test})"
        )
        print(f"{'':11} drops={dict(sorted(stats.drops.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
