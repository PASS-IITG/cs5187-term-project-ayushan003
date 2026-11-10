"""Loading and validation of the YAML files under ``configs/``.

The only place engine paths live is ``configs/targets.yaml``; nothing in the
code base hard-codes one.  Each engine's binary can be overridden per session
with the environment variable ``GRAFT_<ENGINE>_BIN`` (engine name upper-cased),
for example ``GRAFT_TURSO_BIN=/path/to/tursodb``.  The directory
holding the YAML files can be overridden with ``GRAFT_CONFIG_DIR``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR_ENV = "GRAFT_CONFIG_DIR"
BIN_OVERRIDE_TEMPLATE = "GRAFT_{engine}_BIN"

VALID_KINDS = frozenset({"cli", "python"})
_REQUIRED_KEYS = (
    "name",
    "kind",
    "binary",
    "version_cmd",
    "repo",
    "label_filter",
    "dialect",
    "oracles",
    "pinned_commit",
    "timeout_s",
)


class ConfigError(ValueError):
    """Raised when a config file is missing a key or holds an unusable value."""


@dataclass(frozen=True)
class Target:
    """One engine entry from ``configs/targets.yaml``.

    Attributes mirror the SPEC field list.  ``binary`` is already expanded
    (``~`` and ``$VARS``) and env-overridden; it is ``None`` for in-process
    (``kind == "python"``) engines such as the stdlib sqlite3 reference.
    """

    name: str
    kind: str
    binary: str | None
    version_cmd: tuple[str, ...] | None
    repo: str | None
    label_filter: str | None
    dialect: str
    oracles: tuple[str, ...]
    pinned_commit: str | None
    timeout_s: float

    def version_argv(self) -> list[str]:
        """Return the version probe argv with ``{binary}`` substituted.

        Raises:
            ConfigError: if this target has no ``version_cmd`` (python kind).
        """
        if not self.version_cmd:
            raise ConfigError(f"target {self.name!r} has no version_cmd")
        binary = self.binary or ""
        return [token.replace("{binary}", binary) for token in self.version_cmd]


def config_dir() -> Path:
    """Return the directory holding the YAML configs (``GRAFT_CONFIG_DIR`` wins)."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return REPO_ROOT / "configs"


def load_yaml(path: str | os.PathLike[str]) -> Any:
    """Parse a YAML file.

    Args:
        path: file to read.

    Returns:
        The parsed document, or ``{}`` for an empty file.

    Raises:
        FileNotFoundError: if the file does not exist.
        ConfigError: if the file is not valid YAML.
    """
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"config file not found: {p}") from None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
    return {} if parsed is None else parsed


def bin_override_var(engine: str) -> str:
    """Return the env var name that overrides ``engine``'s binary path."""
    return BIN_OVERRIDE_TEMPLATE.format(engine=engine.upper().replace("-", "_"))


def _expand(path: str) -> str:
    return str(Path(os.path.expandvars(path)).expanduser())


def _build_target(name: str, raw: Any, env: dict[str, str]) -> Target:
    """Validate one raw mapping and turn it into a :class:`Target`."""
    if not isinstance(raw, dict):
        raise ConfigError(f"target {name!r} must be a mapping, got {type(raw).__name__}")
    missing = [key for key in _REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"target {name!r} is missing keys: {', '.join(missing)}")
    if raw["name"] != name:
        raise ConfigError(f"target {name!r} has mismatched name field {raw['name']!r}")

    kind = raw["kind"]
    if kind not in VALID_KINDS:
        raise ConfigError(
            f"target {name!r} has kind {kind!r}, expected one of {sorted(VALID_KINDS)}"
        )

    binary = raw["binary"]
    override = env.get(bin_override_var(name))
    if override:
        binary = override
    if binary is not None:
        if not isinstance(binary, str):
            raise ConfigError(f"target {name!r} binary must be a string or null")
        binary = _expand(binary)
    if kind == "cli" and not binary:
        raise ConfigError(f"target {name!r} has kind cli but no binary path")

    version_cmd = raw["version_cmd"]
    if version_cmd is not None:
        if not isinstance(version_cmd, list) or not all(
            isinstance(token, str) for token in version_cmd
        ):
            raise ConfigError(f"target {name!r} version_cmd must be a list of strings")
        version_cmd = tuple(version_cmd)

    oracles = raw["oracles"] or []
    if not isinstance(oracles, list) or not all(isinstance(o, str) for o in oracles):
        raise ConfigError(f"target {name!r} oracles must be a list of strings")

    timeout_s = raw["timeout_s"]
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool):
        raise ConfigError(f"target {name!r} timeout_s must be a number")
    if timeout_s <= 0:
        raise ConfigError(f"target {name!r} timeout_s must be positive, got {timeout_s}")

    return Target(
        name=name,
        kind=kind,
        binary=binary,
        version_cmd=version_cmd,
        repo=raw["repo"],
        label_filter=raw["label_filter"],
        dialect=raw["dialect"],
        oracles=tuple(oracles),
        pinned_commit=raw["pinned_commit"],
        timeout_s=float(timeout_s),
    )


def load_targets(
    path: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Target]:
    """Load ``configs/targets.yaml`` and apply ``GRAFT_<ENGINE>_BIN`` overrides.

    Args:
        path: targets file to read; defaults to ``<config_dir()>/targets.yaml``.
        env: environment mapping to read overrides from; defaults to ``os.environ``.

    Returns:
        Mapping of engine name to :class:`Target`, in file order.

    Raises:
        FileNotFoundError: if the file does not exist.
        ConfigError: if the document shape or any entry is invalid.
    """
    target_path = Path(path) if path is not None else config_dir() / "targets.yaml"
    env = dict(os.environ) if env is None else env

    doc = load_yaml(target_path)
    if not isinstance(doc, dict) or "targets" not in doc:
        raise ConfigError(f"{target_path} must contain a top-level 'targets' mapping")
    raw_targets = doc["targets"]
    if not isinstance(raw_targets, dict) or not raw_targets:
        raise ConfigError(f"{target_path}: 'targets' must be a non-empty mapping")

    return {name: _build_target(name, raw, env) for name, raw in raw_targets.items()}


def get_target(
    name: str,
    path: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> Target:
    """Load one target by name.

    Raises:
        KeyError: if no such target is configured.
    """
    targets = load_targets(path=path, env=env)
    try:
        return targets[name]
    except KeyError:
        raise KeyError(
            f"unknown target {name!r}; configured: {', '.join(sorted(targets))}"
        ) from None
