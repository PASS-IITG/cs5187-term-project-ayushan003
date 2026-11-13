"""Turso adapter, driving the ``tursodb`` shell as a subprocess.

Verified shell behaviour (Turso 0.9.0-pre.1, probed, not assumed):

* the database argument defaults to ``:memory:``; it is passed explicitly.
* output modes are only ``list``, ``pretty`` and ``line`` - there is no JSON
  mode. ``list`` is the only one that can distinguish NULL from the empty
  string, via ``.nullvalue``, so the adapter sets
  ``.mode list`` / ``.headers on`` / ``.nullvalue <token>``.
* **errors are printed on stdout**, not stderr, and the shell keeps going after
  one. The process exits 1 if any statement in the session failed.
* error text comes in two shapes: a miette diagnostic whose first line starts
  with ``  x `` (U+00D7), and plain lines such as
  ``Runtime error: integer overflow``.
* the exit status is 1 if *any* statement in the session failed, so it cannot by
  itself say whether the final statement was the one that failed.

Because errors share stdout with result rows, the adapter brackets the final
statement with a unique marker and reads only what follows it; the exit status
then says whether that statement failed, since every replayed prefix statement
succeeded when it was first run.

Known blind spot: a column *name* can contain the delimiter - the header for
``SELECT 'a' || 'b'`` is literally ``'a' || 'b'`` - so the number of header
fields is unreliable. The parser therefore takes the field count from the first
data row and uses the header only for names, filling in ``columnN`` when the
header is short. Values are right; names may be wrong for an unaliased
expression containing ``|``.

Known blind spot: a row whose only column is the empty string prints as a blank
line, which is also what the shell's trailing newline produces, so a *final*
all-empty row cannot be told from "no rows". An interior one can, and is
recovered (``SELECT '' UNION ALL SELECT 'x'`` returns both rows). This is why
``configs/capabilities.yaml`` records ``empty_string`` as not preserved for
Turso, and why the mutator never emits an empty-string boundary value there.

Cell types: list mode is a *text* protocol, so :attr:`text_cells` is True and
every non-NULL cell comes back as ``str`` - ``1`` and ``'1'`` are
indistinguishable. The normalizer (P06) must check ``text_cells`` before
comparing Turso against the typed sqlite3 reference. A value containing a
newline, or a ``|`` outside the last column, cannot be recovered from this
format; rows are split with a bounded split so the field count always matches
the header.
"""

from __future__ import annotations

from typing import Any, Sequence

from graft.engines import errors
from graft.engines.base import UNKNOWN, CliEngine, Result
from graft.proc import ProcResult

#: printed in place of NULL in list mode; chosen so no real value collides
NULL_TOKEN = "__GRAFT_NULL__"

#: dot commands that put the shell into a parseable mode
SHELL_SETUP: tuple[str, ...] = (
    ".mode list",
    ".headers on",
    f".nullvalue {NULL_TOKEN}",
)

#: first characters of Turso's miette-style diagnostics
_DIAGNOSTIC_PREFIX = "×"


class TursoEngine(CliEngine):
    """Turso driven as a subprocess, one process per statement."""

    name = "turso"
    text_cells = True

    def build_argv(self, statements: Sequence[str], marker: str) -> list[str]:
        """Return the shell argv; the SQL itself is piped in on stdin."""
        return [self.binary, "-q", ":memory:"]

    def build_stdin(self, statements: Sequence[str], marker: str) -> str:
        """Return the script: shell setup, a prefix marker, the prefix, a query
        marker, then the final statement.

        Two markers, not one: the shell keeps going after an error and only
        reports it through the final exit status, so the prefix region has to be
        inspectable on its own to tell "the setup failed" from "the query
        failed".
        """
        body = list(SHELL_SETUP)
        body.append(f"SELECT '{marker}S';")
        body.extend(_terminate(s) for s in statements[:-1])
        body.append(f"SELECT '{marker}';")
        body.append(_terminate(statements[-1]))
        return "\n".join(body) + "\n"

    def parse(self, proc: ProcResult, marker: str) -> Result:
        """Split the final statement's output off at the marker and parse it."""
        lines = proc.stdout.splitlines()
        cut = _last_index(lines, marker)
        if cut is None:
            text = "\n".join(
                part for part in (proc.stdout.strip(), proc.stderr.strip()) if part
            )
            return Result.failure(
                text or "engine produced no output",
                errors.classify(text, proc.returncode, engine=self.name, failed=True),
                elapsed_s=proc.elapsed_s,
            )

        tail = [line for line in lines[cut + 1 :]]
        text = "\n".join(tail).strip()
        stderr = proc.stderr.strip()

        # The shell does not stop at an error and exits 1 if *anything* in the
        # session failed, so a non-zero status may belong to a prefix statement
        # while the final query succeeded. Judge the final statement by what it
        # printed; fall back to the exit status only when it printed nothing.
        if _looks_like_diagnostic(tail):
            failed = True
        elif text:
            failed = False
        else:
            failed = proc.returncode != 0
        if failed:
            message = text or stderr or f"exit status {proc.returncode}"
            return Result.failure(
                message,
                errors.classify(
                    "\n".join(filter(None, (text, stderr))),
                    proc.returncode,
                    engine=self.name,
                    failed=True,
                ),
                elapsed_s=proc.elapsed_s,
            )

        prefix_cut = _last_index(lines, f"{marker}S")
        if prefix_cut is not None:
            prefix_block = lines[prefix_cut + 1 : cut]
            if _looks_like_diagnostic(prefix_block):
                message = "\n".join(prefix_block).strip()
                return Result.failure(
                    message or f"a setup statement failed (exit {proc.returncode})",
                    errors.classify(
                        message, proc.returncode, engine=self.name, failed=True
                    ),
                    elapsed_s=proc.elapsed_s,
                )

        # The query's own output block was never checked for an error. Turso
        # interleaves errors with rows on stdout, so a statement that emitted
        # rows and then failed was being read as rows plus one more row.
        embedded = embedded_diagnostic(tail)
        if embedded is not None:
            return Result.failure(
                embedded,
                errors.classify(
                    embedded, proc.returncode, engine=self.name, failed=True
                ),
                elapsed_s=proc.elapsed_s,
            )

        columns, rows = _parse_list_mode(tail)
        return Result.success(columns=columns, rows=rows, elapsed_s=proc.elapsed_s)

    # --- identity ------------------------------------------------------------

    def version(self) -> str:
        """Return the shell's version line, e.g. ``Turso 0.9.0-pre.1``."""
        if self._version is None:
            out = self._probe([self.binary, "--version"]).strip()
            self._version = out.splitlines()[0].strip() if out else UNKNOWN
        return self._version

    def commit(self) -> str:
        """Always UNKNOWN: tursodb reports no source commit.

        ``--version`` gives only the release name and ``sqlite_version()``
        reports the SQLite level Turso emulates (3.50.4), not its own build.
        The checkout SHA lives in configs/targets.yaml as ``pinned_commit`` and
        is exposed as :attr:`configured_commit`.
        """
        return UNKNOWN

    def sqlite_compat_version(self) -> str:
        """Return the SQLite version Turso claims compatibility with, or UNKNOWN.

        Useful for diff_sqlite: it says which SQLite the comparison is against.
        """
        result = self.run_case([], "SELECT sqlite_version()")
        if result.ok and result.rows and result.rows[0]:
            return str(result.rows[0][0])
        return UNKNOWN


def _terminate(sql: str) -> str:
    """Return ``sql`` with exactly one trailing semicolon."""
    stripped = sql.strip()
    return stripped if stripped.endswith(";") else stripped + ";"


def _last_index(lines: Sequence[str], marker: str) -> int | None:
    """Return the index of the last line that is exactly ``marker``."""
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() == marker:
            return index
    return None


def _looks_like_diagnostic(lines: Sequence[str]) -> bool:
    """True if the output block STARTS as an error report rather than rows."""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_DIAGNOSTIC_PREFIX):
            return True
        return errors.looks_like_error(stripped, engine="turso")
    return False


def embedded_diagnostic(lines: Sequence[str]) -> str | None:
    """Return an error line found ANYWHERE in ``lines``, or None.

    Turso prints errors on stdout, interleaved with result rows, so a statement
    that emits some rows and then fails mid-scan looks exactly like a statement
    that returned those rows plus one more. :func:`_looks_like_diagnostic` only
    inspects the FIRST non-empty line, which is right for deciding whether a
    block is a diagnostic but blind to an error that arrives after data.

    That blindness fabricated a TLP violation: the recombined query
    ``... WHERE lpad('_', t0.c0, '1') IS NULL`` emitted two rows and then hit
    "string or blob too big (18)" on the row where c0 was 2147483647. The
    adapter returned ok=True with the error text parsed as a third row, so the
    oracle compared 6 rows against 3 and reported a wrong result on a query
    that had actually FAILED.

    A mid-stream error means the rows are partial, so the only sound answer is
    that the statement failed. Deliberately conservative: a TEXT value that
    happened to read like an error message would also be refused, which costs
    one case and prevents a false bug report.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_DIAGNOSTIC_PREFIX) or errors.looks_like_error(
            stripped, engine="turso"
        ):
            return stripped
    return None


def _parse_list_mode(
    lines: Sequence[str],
) -> tuple[list[str] | None, list[tuple[Any, ...]]]:
    """Parse ``.mode list`` output with headers into columns and rows.

    Nothing is trimmed. ``lines`` is the exact block between this session's
    output markers, and measurement shows the CLI adds no blank line of its
    own: ``SELECT 'x'`` yields exactly ``['a', 'x']``. Trimming trailing
    blank lines therefore removed real data, because a single-column row whose
    value is ``''`` or ``' '`` prints as a line that looks blank. That cost a
    whole investigation: a TLP "violation" where Turso appeared to lose rows
    from a filtered query was this parser dropping every row whose value was a
    single space, and the engine was right all along.
    """
    body = list(lines)
    if not body:
        return None, []

    # The header is only a source of NAMES. The field count comes from the
    # first data row, because a column name can itself contain "|" - the header
    # for `SELECT 'a' || 'b'` is `'a' || 'b'`, which would otherwise be read as
    # three columns and pad every data row to match. Getting the row data right
    # matters more than the names: an oracle compares values, not headers.
    header = body[0].split("|")
    data_lines = body[1:]
    width = len(data_lines[0].split("|")) if data_lines else len(header)
    columns = header[:width] + [
        f"column{index + 1}" for index in range(len(header), width)
    ]
    rows: list[tuple[Any, ...]] = []
    for line in data_lines:
        # Bounded split: a stray "|" inside the final column cannot change the
        # field count, so rows always line up with the header.
        fields = line.split("|", width - 1) if width > 1 else [line]
        if len(fields) < width:
            fields = fields + [""] * (width - len(fields))
        rows.append(tuple(None if f == NULL_TOKEN else f for f in fields))
    return columns, rows
