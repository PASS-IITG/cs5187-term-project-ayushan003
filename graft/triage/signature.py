"""The two-level signature from SPEC, used to group findings.

SPEC:

    coarse_signature = sha1 of: target + oracle + error_class + (errors) error
    text with literals/numbers/paths placeheld + (wrong-result) bucketed set of
    operator/function tags of the REDUCED query. Group findings by
    coarse_signature for triage; a human confirms whether a group is one defect.
    Store coarse_signature AND full feature_tags.

The signature is a *grouping hint*, never a duplicate decision. Two findings
sharing one say "look at these together"; it is a human who decides they are one
defect.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Sequence

#: tag prefixes that go into the bucketed set for a wrong-result finding.
#: Clause and type tags are deliberately excluded: they vary with the shape of
#: the repro rather than with the defect, and including them splinters one bug
#: across many signatures.
BUCKETED_PREFIXES = ("op:", "fn:", "agg:", "win:", "join:", "set:", "cte:")

_PLACEHOLDERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"'[^']*'"), "'S'"),
    (re.compile(r'"[^"]*"'), '"I"'),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "N"),
    (re.compile(r"\b\d+\.\d+\b"), "N"),
    (re.compile(r"\b\d+\b"), "N"),
    (re.compile(r"(/[\w.\-]+){2,}"), "PATH"),
    (re.compile(r"\b[A-Za-z]:\\[\w\\.\-]+"), "PATH"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "HASH"),
    (re.compile(r"\s+"), " "),
)


def placehold(text: str) -> str:
    """Replace literals, numbers, paths and hashes in an error message.

    Two runs of the same defect differ only in the values they mention, so the
    message has to be reduced to its shape before it can group them.
    """
    out = text.strip()
    for pattern, replacement in _PLACEHOLDERS:
        out = pattern.sub(replacement, out)
    return out.strip()


def bucketed_tags(feature_tags: Iterable[str]) -> tuple[str, ...]:
    """Return the operator and function tags, sorted, for a wrong-result bug."""
    return tuple(
        sorted(
            tag
            for tag in feature_tags
            if tag.startswith(BUCKETED_PREFIXES)
        )
    )


def coarse_signature(
    target: str,
    oracle: str,
    error_class: str,
    error_text: str | None = None,
    feature_tags: Sequence[str] = (),
) -> str:
    """Return the SPEC coarse signature.

    Args:
        target: engine name.
        oracle: oracle name.
        error_class: the SPEC error class.
        error_text: the engine's message, for an error-shaped finding.
        feature_tags: the case's tags, for a wrong-result finding.

    Returns:
        A 40-character sha1 hex digest.
    """
    parts = [target, oracle, error_class]
    if error_text:
        parts.append(placehold(error_text))
    else:
        parts.append("|".join(bucketed_tags(feature_tags)))
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
