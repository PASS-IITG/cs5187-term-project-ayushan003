"""Engine adapters. Use :func:`create_engine` rather than importing a class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from graft.engines.base import Engine
    from graft.config import Target

#: engine name -> the adapter class path for each kind
_REGISTRY: dict[tuple[str, str], tuple[str, str]] = {
    ("sqlite", "python"): ("graft.engines.sqlite", "SqliteEngine"),
    ("turso", "cli"): ("graft.engines.turso", "TursoEngine"),
    ("fake", "python"): ("graft.engines.fake", "FakeEngine"),
}


def available() -> list[tuple[str, str]]:
    """Return every (engine name, kind) pair that has an adapter."""
    return sorted(_REGISTRY)


def create_engine(
    name: str,
    kind: str | None = None,
    target: "Target | None" = None,
    start: bool = True,
    **kwargs: Any,
) -> "Engine":
    """Build the adapter for ``name``, configured from configs/targets.yaml.

    Args:
        name: engine name, e.g. ``duckdb``.
        kind: ``cli`` or ``python``; defaults to the target's kind
            resolves to the CLI build that campaigns use. Pass ``"python"`` to
            get the wheel instead.
        target: an already-loaded Target, to avoid re-reading the config.
        start: call ``start()`` before returning.
        **kwargs: passed through to the adapter constructor.

    Returns:
        A started :class:`adapters.base.Engine`.

    Raises:
        KeyError: if no adapter exists for that engine and kind.
    """
    import importlib

    from graft.config import get_target

    if target is None and name != "fake":
        target = get_target(name)
    resolved_kind = kind or (target.kind if target is not None else "python")

    try:
        module_name, class_name = _REGISTRY[(name, resolved_kind)]
    except KeyError:
        raise KeyError(
            f"no adapter for engine {name!r} of kind {resolved_kind!r}; "
            f"have {available()}"
        ) from None

    cls = getattr(importlib.import_module(module_name), class_name)

    if resolved_kind == "cli":
        if target is None or not target.binary:
            raise KeyError(f"engine {name!r} has no binary configured")
        engine = cls(
            target.binary,
            timeout_s=target.timeout_s,
            configured_commit=target.pinned_commit,
            **kwargs,
        )
    elif target is not None and name != "fake":
        # pinned_commit describes the ~/targets checkout, so it only applies to
        # the build made from it. The duckdb wheel is separate software and
        # must not inherit the CLI's SHA.
        engine = cls(
            timeout_s=target.timeout_s,
            configured_commit=(
                target.pinned_commit if resolved_kind == target.kind else None
            ),
            **kwargs,
        )
    else:
        engine = cls(**kwargs)

    if start:
        engine.start()
    return engine
