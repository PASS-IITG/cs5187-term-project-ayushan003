"""Tests for common.config: real targets.yaml, env overrides, bad documents."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from graft import config

EXPECTED_TARGETS = {"turso", "duckdb", "sqlite"}


def write_targets(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "targets.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# --- known-good: the real config in configs/ ---------------------------------


def test_load_targets_reads_repo_config():
    targets = config.load_targets(env={})
    assert set(targets) == EXPECTED_TARGETS
    for name, target in targets.items():
        assert target.name == name
        assert target.kind in config.VALID_KINDS
        assert target.timeout_s > 0
        assert isinstance(target.oracles, tuple)


def test_repo_config_matches_spec_expectations():
    targets = config.load_targets(env={})
    assert targets["duckdb"].repo == "duckdb/duckdb"
    assert targets["turso"].repo == "tursodatabase/turso"
    assert targets["sqlite"].repo is None
    # Turso is the only engine GRAFT runs an oracle against.
    assert targets["turso"].oracles == ("crash",)
    assert targets["duckdb"].oracles == ()
    assert targets["sqlite"].oracles == ()
    # Each repo's own confirmed-bug label, used by the corpus miner.
    assert targets["duckdb"].label_filter == "reproduced"
    assert targets["turso"].label_filter == "correctness"
    assert targets["sqlite"].label_filter is None


def test_cli_binaries_are_absolute_and_expanded():
    targets = config.load_targets(env={})
    for name in ("duckdb", "turso"):
        binary = targets[name].binary
        assert binary is not None
        assert "~" not in binary
        assert Path(binary).is_absolute()
    assert targets["sqlite"].binary is None


def test_version_argv_substitutes_binary():
    target = config.get_target("duckdb", env={})
    argv = target.version_argv()
    assert argv[0] == target.binary
    assert "{binary}" not in " ".join(argv)


def test_version_argv_raises_for_python_engine():
    target = config.get_target("sqlite", env={})
    with pytest.raises(config.ConfigError):
        target.version_argv()


def test_other_config_files_parse(tmp_path):
    for name in ("weights", "capabilities", "targets", "engine-revisions"):
        doc = config.load_yaml(config.config_dir() / f"{name}.yaml")
        assert isinstance(doc, dict)
        assert isinstance(doc, dict) and doc


# --- env overrides -----------------------------------------------------------


def test_bin_override_var_name():
    assert config.bin_override_var("turso") == "GRAFT_TURSO_BIN"


def test_env_override_replaces_binary():
    env = {config.bin_override_var("turso"): "/opt/turso/tursodb"}
    targets = config.load_targets(env=env)
    assert targets["turso"].binary == "/opt/turso/tursodb"
    # other engines untouched
    assert "duckdb" in targets["duckdb"].binary


def test_env_override_expands_user(monkeypatch):
    monkeypatch.setenv(config.bin_override_var("turso"), "~/elsewhere/tursodb")
    target = config.get_target("turso")
    assert target.binary == str(Path.home() / "elsewhere" / "tursodb")


def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(config.CONFIG_DIR_ENV, str(tmp_path))
    assert config.config_dir() == tmp_path


# --- known-bad ---------------------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load_targets(path=tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text("targets: [unclosed\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_targets(path=path)


def test_missing_top_level_key_raises(tmp_path):
    path = write_targets(tmp_path, "engines: {}\n")
    with pytest.raises(config.ConfigError, match="targets"):
        config.load_targets(path=path)


def test_missing_required_field_raises(tmp_path):
    path = write_targets(
        tmp_path,
        """
        targets:
          duckdb:
            name: duckdb
            kind: cli
            binary: /bin/true
        """,
    )
    with pytest.raises(config.ConfigError, match="missing keys"):
        config.load_targets(path=path)


def _full_entry(**overrides: object) -> str:
    entry = {
        "name": "duckdb",
        "kind": "cli",
        "binary": "/bin/true",
        "version_cmd": '["{binary}", "--version"]',
        "repo": "duckdb/duckdb",
        "label_filter": "TODO",
        "dialect": "duckdb",
        "oracles": "[crash]",
        "pinned_commit": "deadbeef",
        "timeout_s": "10",
    }
    entry.update({k: str(v) for k, v in overrides.items()})
    lines = "\n".join(f"    {k}: {v}" for k, v in entry.items())
    return f"targets:\n  duckdb:\n{lines}\n"


def test_bad_kind_raises(tmp_path):
    path = write_targets(tmp_path, _full_entry(kind="wasm"))
    with pytest.raises(config.ConfigError, match="kind"):
        config.load_targets(path=path)


def test_cli_without_binary_raises(tmp_path):
    path = write_targets(tmp_path, _full_entry(binary="null"))
    with pytest.raises(config.ConfigError, match="no binary path"):
        config.load_targets(path=path)


def test_non_positive_timeout_raises(tmp_path):
    path = write_targets(tmp_path, _full_entry(timeout_s="0"))
    with pytest.raises(config.ConfigError, match="positive"):
        config.load_targets(path=path)


def test_name_mismatch_raises(tmp_path):
    path = write_targets(tmp_path, _full_entry(name="ducks"))
    with pytest.raises(config.ConfigError, match="mismatched name"):
        config.load_targets(path=path)


def test_unknown_target_raises():
    with pytest.raises(KeyError):
        config.get_target("postgres", env={})
