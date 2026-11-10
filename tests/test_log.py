"""Tests for common.log: JSON-lines output, directory override, bad names."""

from __future__ import annotations

import json
import re

import pytest

from graft import log


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- known-good --------------------------------------------------------------


def test_writes_one_json_object_per_line(tmp_path):
    with log.JsonlLogger("duckdb", tmp_path) as logger:
        logger.log("query", sql="SELECT 1", ok=True)
        logger.log("stats", qps=12.5, hits=0)
    records = read_lines(tmp_path / "duckdb.jsonl")
    assert [r["event"] for r in records] == ["query", "stats"]
    assert records[0]["sql"] == "SELECT 1"
    assert records[0]["ok"] is True
    assert records[1]["qps"] == 12.5


def test_timestamp_is_iso8601_utc(tmp_path):
    with log.JsonlLogger("ts", tmp_path) as logger:
        record = logger.log("ping")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", record["ts"])
    assert read_lines(tmp_path / "ts.jsonl")[0]["ts"] == record["ts"]


def test_appends_across_logger_instances(tmp_path):
    log.JsonlLogger("append", tmp_path).log("first")
    log.JsonlLogger("append", tmp_path).log("second")
    assert [r["event"] for r in read_lines(tmp_path / "append.jsonl")] == [
        "first",
        "second",
    ]


def test_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "logs"
    log.JsonlLogger("fresh", target).log("hello")
    assert (target / "fresh.jsonl").exists()


def test_non_serialisable_value_falls_back_to_repr(tmp_path):
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    with log.JsonlLogger("weird", tmp_path) as logger:
        logger.log("odd", value=Opaque())
    assert read_lines(tmp_path / "weird.jsonl")[0]["value"] == "<opaque>"


def test_log_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(log.LOG_DIR_ENV, str(tmp_path))
    assert log.log_dir() == tmp_path


def test_default_log_dir_is_repo_logs(monkeypatch):
    monkeypatch.delenv(log.LOG_DIR_ENV, raising=False)
    assert log.log_dir().name == "logs"
    assert log.log_dir().parent == log.REPO_ROOT


def test_get_logger_is_shared_per_path(tmp_path):
    first = log.get_logger("shared", tmp_path)
    assert log.get_logger("shared", tmp_path) is first
    first.close()
    assert log.get_logger("shared", tmp_path) is not first


# --- known-bad ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "sub/dir", "../escape", "."])
def test_rejects_names_that_are_not_bare_file_names(tmp_path, name):
    with pytest.raises(ValueError):
        log.JsonlLogger(name, tmp_path)


def test_logging_after_close_raises(tmp_path):
    logger = log.JsonlLogger("closed", tmp_path)
    logger.close()
    logger.close()  # idempotent
    assert logger.closed
    with pytest.raises(ValueError):
        logger.log("too-late")
