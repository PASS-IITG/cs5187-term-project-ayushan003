"""Searching upstream for an existing report of a finding.

CLAUDE.md hard rule 9 requires a candidate to be "not matched by an open or
closed upstream issue", and hard rule 1 means this can only ever read: every
request goes through :class:`miner.github.GitHubClient`, which has no code path
that writes.

The search has to be *specific to the likely defect*, which P09 states and the
first real campaign demonstrated. Searching the raw error text of
``memory allocation of 9223372036854775806 bytes failed`` finds nothing, because
no human writes that number in a title; searching ``lpad`` finds the function,
and searching ``index out of bounds`` finds the symptom. So queries are built
from the *feature* (the function or clause the defect is in) crossed with the
*symptom* (panic, crash, wrong results), not from the SQL.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from graft.corpus.github import GitHubClient, GitHubError

#: how many hits to record per finding, per P09
MAX_CANDIDATES = 5
#: the search endpoint is limited to 30 requests a minute authenticated and 10
#: unauthenticated, so a pause between queries is not optional
SEARCH_PAUSE_S = 2.5

STATUS_DUPLICATE_SUSPECT = "duplicate_suspect"
STATUS_CANDIDATE_READY = "candidate_ready"

#: symptom words worth crossing with a feature name
_SYMPTOMS = {
    "panic": ("panic", "index out of bounds"),
    "crash": ("crash", "abort", "segfault"),
    "internal_error": ("internal error", "assertion"),
    "timeout": ("hang", "infinite"),
    "none": ("wrong results", "incorrect"),
}
#: words that are too common in SQL to search for on their own
_STOPWORDS = frozenset(
    {
        "select", "from", "where", "table", "create", "insert", "values", "and",
        "or", "not", "null", "true", "false", "order", "group", "by", "limit",
        "join", "on", "as", "case", "when", "then", "else", "end", "cast",
        "with", "union", "all", "distinct", "count", "sum", "min", "max",
        "avg", "into", "set", "is", "in", "exists", "like", "between", "int",
        "integer", "text", "varchar", "real", "double", "blob", "boolean",
    }
)


@dataclass
class DuplicateReport:
    """What the search found."""

    candidates: list[dict[str, Any]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    searched: bool = False
    detail: str = ""

    @property
    def suspect(self) -> bool:
        """True if any hit is plausible enough to withhold the finding."""
        return bool(self.candidates)


def feature_terms(
    feature_tags: Sequence[str], sql: str, limit: int = 3
) -> list[str]:
    """Return the function and clause names worth searching for.

    Tags come first, because they were derived structurally; identifiers from
    the SQL are a fallback, with SQL keywords and type names removed so the
    search is not swamped.
    """
    terms: list[str] = []
    for tag in feature_tags:
        if ":" not in tag:
            continue
        prefix, name = tag.split(":", 1)
        if prefix in ("fn", "agg", "win") and name not in _STOPWORDS:
            terms.append(name)
    for tag in feature_tags:
        if tag.startswith(("clause:", "op:", "join:", "set:", "cte:")):
            name = tag.split(":", 1)[1]
            if name not in _STOPWORDS and len(name) > 3:
                terms.append(name.replace("_", " "))
    for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", sql or ""):
        lowered = word.lower()
        if lowered not in _STOPWORDS and lowered not in terms:
            terms.append(lowered)
    seen: list[str] = []
    for term in terms:
        if term not in seen:
            seen.append(term)
    return seen[:limit]


def build_queries(
    repo: str,
    feature_tags: Sequence[str],
    sql: str,
    error_class: str = "none",
    error_text: str | None = None,
) -> list[str]:
    """Return the search queries for one finding, most specific first."""
    queries: list[str] = []
    symptoms = _SYMPTOMS.get(error_class, _SYMPTOMS["none"])
    terms = feature_terms(feature_tags, sql)

    for term in terms:
        queries.append(f'repo:{repo} "{term}" {symptoms[0]}')
    # the symptom alone, which catches a report filed without the feature name
    queries.append(f'repo:{repo} "{symptoms[0]}" in:title')
    # a distinctive phrase from the error, if it has one that a human might type
    if error_text:
        phrase = _distinctive_phrase(error_text)
        if phrase:
            queries.append(f'repo:{repo} "{phrase}"')
    return queries[:4]


def _distinctive_phrase(error_text: str) -> str | None:
    """Return a searchable phrase from an error, or None.

    Numbers, paths and addresses are stripped: no upstream title contains
    ``9223372036854775806``, so searching for it guarantees a false negative.
    """
    first = (error_text or "").strip().splitlines()
    if not first:
        return None
    text = first[0]
    text = re.sub(r"[\w./\\-]*[/\\][\w./\\:-]*", " ", text)   # paths, with line:col
    text = re.sub(r"\b[\w.]*\d[\w.]*\b", " ", text)            # anything with a digit
    text = re.sub(r"[^A-Za-z' ]+", " ", text)                   # leftover punctuation
    text = re.sub(r"\s+", " ", text).strip(" :,-'")
    words = [w for w in text.split() if len(w) > 2 and w.isalpha()]
    return " ".join(words[:5]) if len(words) >= 2 else None


def search(
    client: GitHubClient,
    repo: str,
    feature_tags: Sequence[str],
    sql: str,
    error_class: str = "none",
    error_text: str | None = None,
    pause_s: float = SEARCH_PAUSE_S,
) -> DuplicateReport:
    """Search ``repo`` for an existing report. Read-only GETs only."""
    report = DuplicateReport()
    if not repo:
        report.detail = "no repo configured for this target"
        return report

    queries = build_queries(repo, feature_tags, sql, error_class, error_text)
    report.queries = list(queries)
    seen: set[int] = set()
    for index, query in enumerate(queries):
        if len(report.candidates) >= MAX_CANDIDATES:
            break
        try:
            body = client.get(
                "/search/issues", params={"q": query, "per_page": MAX_CANDIDATES}
            ).body or {}
        except GitHubError as error:
            report.detail = f"search failed: {str(error)[:120]}"
            continue
        report.searched = True
        for item in body.get("items") or []:
            number = item.get("number")
            if number in seen:
                continue
            seen.add(number)
            report.candidates.append(
                {
                    "number": number,
                    "title": (item.get("title") or "")[:160],
                    "state": item.get("state"),
                    "url": item.get("html_url"),
                    "matched_query": query,
                }
            )
            if len(report.candidates) >= MAX_CANDIDATES:
                break
        if index + 1 < len(queries) and pause_s:
            time.sleep(pause_s)

    if not report.searched and not report.detail:
        report.detail = "no search was performed"
    return report
