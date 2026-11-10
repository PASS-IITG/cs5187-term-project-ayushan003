"""Tests for miner.classify: batching, SPEC validation, resumability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graft.corpus import classify


def valid_record(number: int = 1, **overrides):
    record = {
        "number": number,
        "bug_class": "null_handling",
        "feature_tags": ["op:is_null", "type:integer"],
        "variant_axes": ["nulls", "types"],
        "cross_engine_ok": True,
        "one_line_root_cause": "Something specific went wrong.",
    }
    record.update(overrides)
    return record


# --- validation against the SPEC vocabularies --------------------------------


def test_bug_classes_match_spec():
    assert classify.BUG_CLASSES == (
        "null_handling",
        "type_coercion",
        "integer_overflow",
        "float_precision",
        "window_frame",
        "optimizer_rewrite",
        "join_semantics",
        "aggregate_semantics",
        "nested_type",
        "string_function",
        "datetime",
        "cast",
        "sorting_collation",
        "panic_unimplemented",
        "other",
    )


def test_tag_prefixes_match_spec():
    assert classify.TAG_PREFIXES == (
        "fn", "op", "clause", "type", "agg", "join", "cte", "set", "win",
    )


def test_a_valid_record_passes():
    classify.validate_record(valid_record())


@pytest.mark.parametrize("prefix", classify.TAG_PREFIXES)
def test_every_spec_prefix_is_accepted(prefix):
    classify.validate_record(valid_record(feature_tags=[f"{prefix}:thing_1"]))


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"bug_class": "wrong_results"}, "not in the SPEC list"),
        ({"feature_tags": ["Type:Integer"]}, "feature tag"),
        ({"feature_tags": ["integer"]}, "feature tag"),
        ({"feature_tags": ["bogus:x"]}, "feature tag"),
        ({"feature_tags": []}, "non-empty"),
        ({"variant_axes": ["colors"]}, "variant axis"),
        ({"variant_axes": []}, "non-empty"),
        ({"cross_engine_ok": "yes"}, "bool"),
        ({"one_line_root_cause": ""}, "empty"),
        ({"one_line_root_cause": "two\nlines"}, "one line"),
        ({"number": "12"}, "must be an int"),
    ],
)
def test_invalid_records_are_rejected(overrides, message):
    with pytest.raises(classify.ClassificationError, match=message):
        classify.validate_record(valid_record(**overrides))


def test_a_missing_field_is_rejected():
    record = valid_record()
    del record["variant_axes"]
    with pytest.raises(classify.ClassificationError, match="missing fields"):
        classify.validate_record(record)


# --- appending and resuming --------------------------------------------------


def test_append_writes_only_the_required_fields(tmp_path):
    written = classify.append_records(
        "demo", [valid_record(7, extra="ignored")], root=tmp_path
    )
    assert written == 1
    line = (tmp_path / "demo.jsonl").read_text().strip()
    assert list(json.loads(line)) == list(classify.REQUIRED_FIELDS)
    assert "extra" not in line


def test_append_is_idempotent_per_issue(tmp_path):
    classify.append_records("demo", [valid_record(7)], root=tmp_path)
    assert classify.append_records("demo", [valid_record(7)], root=tmp_path) == 0
    assert len((tmp_path / "demo.jsonl").read_text().splitlines()) == 1


def test_append_writes_nothing_when_any_record_is_invalid(tmp_path):
    with pytest.raises(classify.ClassificationError):
        classify.append_records(
            "demo", [valid_record(1), valid_record(2, bug_class="nope")], root=tmp_path
        )
    assert not (tmp_path / "demo.jsonl").exists()


def test_classified_numbers_reports_progress(tmp_path):
    assert classify.classified_numbers("demo", root=tmp_path) == set()
    classify.append_records(
        "demo", [valid_record(3), valid_record(9)], root=tmp_path
    )
    assert classify.classified_numbers("demo", root=tmp_path) == {3, 9}


# --- batching ----------------------------------------------------------------


def test_batches_are_fifteen_items_by_default():
    assert classify.BATCH_SIZE == 15
    assert classify.DEFAULT_LIMIT == 60
    sizes = [len(b) for b in classify.batches("duckdb")]
    assert sizes == [15, 15, 15, 15]


def test_batch_items_are_newest_closed_first_and_have_a_repro():
    import json as _json

    items = classify.batch_items("duckdb")
    assert len(items) == 60
    numbers = [i["number"] for i in items]
    assert len(set(numbers)) == 60
    closed = {
        _json.loads(l)["number"]: _json.loads(l)["closed_at"]
        for l in classify.mined_path("duckdb").read_text().splitlines()
    }
    order = [closed[n] for n in numbers]
    assert order == sorted(order, reverse=True)
    for item in items:
        assert item["query"].strip()


def test_render_batch_shows_file_names_but_never_a_diff():
    items = classify.batch_items("duckdb")[:3]
    text = classify.render_batch(items)
    assert "files:" in text and "query:" in text
    # a diff would show these markers; the renderer must never emit them
    assert "@@" not in text
    for line in text.splitlines():
        assert not line.startswith("+")
        assert not line.startswith("---")


def test_render_batch_shows_only_basenames():
    items = [
        {
            "number": 1,
            "title": "t",
            "setup": [],
            "query": "SELECT 1",
            "fix_files": ["src/deep/path/to/file.cpp"],
            "source": "fix_test",
            "repro_count": 1,
        }
    ]
    text = classify.render_batch(items)
    assert "file.cpp" in text
    assert "src/deep/path" not in text


def test_status_reports_every_target():
    text = classify.status()
    for target in ("duckdb",):
        assert target in text


def test_main_prints_status_without_a_target(capsys):
    assert classify.main([]) == 0
    assert "duckdb" in capsys.readouterr().out


def test_main_rejects_an_out_of_range_batch(capsys):
    assert classify.main(["--target", "duckdb", "--batch", "99"]) == 1
    assert "batch must be" in capsys.readouterr().out


# --- the classified data on disk ---------------------------------------------


def test_every_classified_line_on_disk_is_valid():
    for target in ("duckdb",):
        path = classify.classified_path(target)
        records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert len(records) >= classify.DEFAULT_LIMIT, f"{target} has {len(records)}"
        numbers = [r["number"] for r in records]
        assert len(set(numbers)) == len(numbers), f"{target} has duplicates"
        for record in records:
            classify.validate_record(record)
            assert list(record) == list(classify.REQUIRED_FIELDS)


def test_every_classified_issue_has_a_repro():
    """A classification must describe an issue that really yielded a repro."""
    for target in ("duckdb",):
        repro_numbers = {
            json.loads(l)["number"]
            for l in classify.repros_path(target).read_text().splitlines()
        }
        for record in [
            json.loads(l)
            for l in classify.classified_path(target).read_text().splitlines()
        ]:
            assert record["number"] in repro_numbers, (
                f"{target} #{record['number']} is classified but has no repro"
            )


