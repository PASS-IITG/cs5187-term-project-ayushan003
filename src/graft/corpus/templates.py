"""Turning mined repros and confirmed findings into reusable templates.

P07 task 5: "load every reduced finding and every classified repro, turn each
into a template by replacing names and literals with placeholders, and
instantiate with valid fill."

What is replaced, and what is not
---------------------------------
Only **literals** are turned into placeholders - integers, reals and strings -
and the repro's own setup is kept intact. That is a deliberate limit. Re-pointing
a mined query at a freshly generated schema would need the column types and
arities to line up, and the mined corpus is full of engine-specific shapes
(generated columns, PRAGMA settings, custom types) where a wrong substitution
produces SQL that merely looks plausible. Replaying a maintainer's own
reproduction with different boundary values is both sound and the point: these
repros already encode a defect's shape, and the values are what the fix was
tested against.

Weighting
---------
``configs/weights.yaml`` sets a template's sampling weight. A template taken
from a finding - a hit this project confirmed - defaults high; one from a mined
repro defaults to 1. P10 can tune the file without touching code.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

from graft.config import REPO_ROOT, config_dir, load_yaml

SOURCE_REPRO = "repro"
SOURCE_FINDING = "finding"

#: default sampling weights, overridable in configs/weights.yaml
DEFAULT_WEIGHTS = {SOURCE_FINDING: 8.0, SOURCE_REPRO: 1.0}

_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_REAL_RE = re.compile(r"(?<![\w.])-?\d+\.\d+(?:[eE][+-]?\d+)?(?![\w.])")
_INT_RE = re.compile(r"(?<![\w.'])-?\d+(?![\w.'])")

#: literals worth filling a placeholder with, by kind
FILL_INTEGERS = (0, 1, -1, 2, -2, 127, -128, 255, 32767, -32768,
                 2147483647, -2147483648, 9223372036854775807,
                 -9223372036854775808)
FILL_REALS = (0.0, -0.0, 0.5, -0.5, 1.0, -1.0, 1e-3, 2.5)
FILL_STRINGS = ("", " ", "a", "A", "abc", "ABC", "0", "%", "_", "a'b")


@dataclass(frozen=True)
class Template:
    """A mined case with its literals replaced by placeholders."""

    setup: tuple[str, ...]
    query: str
    #: placeholder name -> kind, one of "int", "real", "text"
    holes: tuple[tuple[str, str], ...]
    source: str
    origin_issue: object = None
    weight: float = 1.0

    @property
    def hole_count(self) -> int:
        """How many placeholders the template has."""
        return len(self.holes)


def _mask_strings(sql: str) -> tuple[str, list[str]]:
    """Replace string literals with markers, returning the originals."""
    found: list[str] = []

    def take(match: re.Match[str]) -> str:
        found.append(match.group(0))
        return f"\x00S{len(found) - 1}\x00"

    return _STRING_RE.sub(take, sql), found


def templatize(query: str, start: int = 0) -> tuple[str, list[tuple[str, str]]]:
    """Replace the literals in ``query`` with ``{name}`` placeholders.

    Strings are masked first so a number inside a string literal is never
    mistaken for a numeric literal.

    Returns:
        The templated SQL and the (placeholder, kind) pairs, in order.
    """
    masked, strings = _mask_strings(query)
    holes: list[tuple[str, str]] = []

    def replace_real(match: re.Match[str]) -> str:
        name = f"r{start + len(holes)}"
        holes.append((name, "real"))
        return "{" + name + "}"

    def replace_int(match: re.Match[str]) -> str:
        name = f"n{start + len(holes)}"
        holes.append((name, "int"))
        return "{" + name + "}"

    masked = _REAL_RE.sub(replace_real, masked)
    masked = _INT_RE.sub(replace_int, masked)

    for index, original in enumerate(strings):
        name = f"s{start + len(holes)}"
        holes.append((name, "text"))
        masked = masked.replace(f"\x00S{index}\x00", "{" + name + "}")
    return masked, holes


def load_weights(path: str | Path | None = None) -> dict[str, float]:
    """Load sampling weights, falling back to :data:`DEFAULT_WEIGHTS`."""
    target = Path(path) if path is not None else config_dir() / "weights.yaml"
    doc = load_yaml(target)
    raw = (doc or {}).get("weights") or {}
    weights = dict(DEFAULT_WEIGHTS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                weights[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return weights


def load_templates(
    targets: Sequence[str] = ("duckdb",),
    repros_root: Path | None = None,
    findings_root: Path | None = None,
    weights: dict[str, float] | None = None,
    limit_per_target: int | None = None,
) -> list[Template]:
    """Load every mined repro and every reduced finding as a template."""
    weights = weights if weights is not None else load_weights()
    repros_dir = repros_root or REPO_ROOT / "data" / "repros"
    findings_dir = findings_root or REPO_ROOT / "findings"
    out: list[Template] = []

    for target in targets:
        path = repros_dir / f"{target}.jsonl"
        if not path.exists():
            continue
        kept = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            query, holes = templatize(record["query"])
            out.append(
                Template(
                    setup=tuple(record["setup"]),
                    query=query,
                    holes=tuple(holes),
                    source=SOURCE_REPRO,
                    origin_issue=record.get("number"),
                    weight=weights.get(SOURCE_REPRO, 1.0),
                )
            )
            kept += 1
            if limit_per_target is not None and kept >= limit_per_target:
                break

    if findings_dir.exists():
        for finding in sorted(findings_dir.rglob("finding.json")):
            record = json.loads(finding.read_text(encoding="utf-8"))
            # a reduced finding is the better template; fall back to the query
            sql = record.get("reduced_sql") or record.get("query")
            if not sql:
                continue
            if isinstance(sql, list):
                sql = sql[-1]
            query, holes = templatize(str(sql))
            out.append(
                Template(
                    setup=tuple(record.get("setup") or ()),
                    query=query,
                    holes=tuple(holes),
                    source=SOURCE_FINDING,
                    origin_issue=record.get("origin_issue"),
                    weight=weights.get(SOURCE_FINDING, 8.0),
                )
            )
    return out


def _fill(kind: str, rng: random.Random) -> str:
    if kind == "int":
        return str(rng.choice(FILL_INTEGERS))
    if kind == "real":
        return repr(rng.choice(FILL_REALS))
    text = rng.choice(FILL_STRINGS)
    return "'" + text.replace("'", "''") + "'"


def instantiate(template: Template, rng: random.Random) -> tuple[list[str], str]:
    """Fill a template's placeholders with fresh literals.

    Returns:
        The setup statements, unchanged, and the filled query.
    """
    filled = template.query
    for name, kind in template.holes:
        filled = filled.replace("{" + name + "}", _fill(kind, rng), 1)
    return list(template.setup), filled


def weighted_choice(
    templates: Sequence[Template], rng: random.Random
) -> Template | None:
    """Pick a template in proportion to its weight."""
    if not templates:
        return None
    total = sum(max(t.weight, 0.0) for t in templates)
    if total <= 0:
        return rng.choice(list(templates))
    threshold = rng.random() * total
    running = 0.0
    for template in templates:
        running += max(template.weight, 0.0)
        if running >= threshold:
            return template
    return templates[-1]
