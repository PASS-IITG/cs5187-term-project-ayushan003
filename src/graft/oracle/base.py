"""The oracle contract: ``Violation`` and ``Oracle``, exactly as SPEC fixes them.

SPEC, "Oracle interface":

    class Oracle: name; applies_to(engine)->bool;
    check(engines, test_case)->list[Violation].
    Violation: oracle, engine, test_case, expected, actual, detail.

An oracle decides whether an observation is *suspicious*; it never decides that
an engine is wrong. SPEC is explicit for diff_sqlite - "a disagreement is a
candidate, not proof an engine is wrong" - and the same caution applies to the
rest. Everything an oracle reports lands in a finding with status ``new`` and
waits for the reducer, the verifier and a human.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from graft.engines.base import Engine, Result, TestCase

#: statuses an oracle may suggest for the finding it produces. CLAUDE.md lets
#: the tool go no further than drafted / duplicate_suspect / questionable.
STATUS_NEW = "new"
STATUS_QUESTIONABLE = "questionable"


@dataclass
class Violation:
    """One suspicious observation, as SPEC defines it."""

    oracle: str
    engine: str
    test_case: TestCase
    expected: Any
    actual: Any
    detail: str
    #: what the runner should write as the finding's status
    suggested_status: str = STATUS_NEW
    #: SPEC feature tags describing the query, filled by the runner
    feature_tags: tuple[str, ...] = ()
    #: extra context that belongs in the finding but is not expected/actual
    context: dict[str, Any] = field(default_factory=dict)


class Oracle(ABC):
    """Base class for every oracle."""

    #: short name, used in the finding and in the coarse signature
    name: str = ""

    @abstractmethod
    def applies_to(self, engine: Engine) -> bool:
        """True if this oracle may be run against ``engine``."""

    @abstractmethod
    def check(
        self, engines: Mapping[str, Engine], test_case: TestCase
    ) -> list[Violation]:
        """Run the check and return any violations.

        Args:
            engines: engine name to a started engine. An oracle takes what it
                needs and ignores the rest, so the runner can hand the same
                mapping to every oracle.
            test_case: the case to examine.

        Returns:
            A list of violations, empty when nothing is suspicious. An oracle
            must not raise on a failed query; a failure is an observation.
        """


def result_summary(result: Result) -> dict[str, Any]:
    """Return a JSON-ready summary of a Result, for a finding."""
    return {
        "ok": result.ok,
        "error_class": result.error_class,
        "error": result.error,
        "columns": result.columns,
        "row_count": result.row_count,
        "elapsed_s": round(result.elapsed_s, 4),
    }
