"""Tests for variants.capabilities: alias resolution, loading, probed facts."""

from __future__ import annotations

import pytest

from graft.generate import capabilities as C


# --- alias resolution ------------------------------------------------------


def test_one_canonical_label_per_reported_type():
    types = {
        "tinyint": {"declared": "TINYINT", "reported": "integer"},
        "integer": {"declared": "INTEGER", "reported": "integer"},
        "date": {"declared": "DATE", "reported": "integer"},
        "real": {"declared": "REAL", "reported": "real"},
        "uuid": {"declared": "UUID", "reported": "ERR"},
    }
    C.resolve_type_aliases(types)
    assert types["integer"]["distinct"] is True
    assert types["tinyint"]["distinct"] is False
    assert types["tinyint"]["alias_of"] == "integer"
    assert types["date"]["alias_of"] == "integer"
    assert types["real"]["distinct"] is True
    assert types["uuid"]["distinct"] is False
    assert "alias_of" not in types["uuid"]


def test_the_canonical_label_prefers_a_name_matching_the_report():
    types = {
        "real": {"declared": "REAL", "reported": "FLOAT"},
        "float": {"declared": "FLOAT", "reported": "FLOAT"},
    }
    C.resolve_type_aliases(types)
    assert types["float"]["distinct"] is True
    assert types["real"]["alias_of"] == "float"


def test_a_parameterised_report_still_resolves():
    types = {"decimal": {"declared": "DECIMAL(10,2)", "reported": "DECIMAL(10,2)"}}
    C.resolve_type_aliases(types)
    assert types["decimal"]["distinct"] is True


# --- the support dataclasses ----------------------------------------------


def test_only_a_distinct_type_is_usable():
    assert C.TypeSupport("integer", "INTEGER", "integer", True).usable
    assert not C.TypeSupport("date", "DATE", "integer", False, "integer").usable


def test_a_value_must_be_accepted_and_preserved_to_be_usable():
    assert C.ValueSupport("x", "1", True, True).usable
    assert not C.ValueSupport("nan", "CAST('NaN' AS DOUBLE)", True, False).usable
    assert not C.ValueSupport("x", "1", False, True).usable


@pytest.mark.parametrize(
    "label,cell,preserved",
    [
        ("nan", "nan", True),
        ("nan", 0.0, False),
        ("nan", None, False),
        ("infinity", "inf", True),
        ("neg_infinity", "-inf", True),
        ("neg_infinity", "inf", False),
        ("negative_zero", "-0.0", True),
        ("negative_zero", "0.0", False),
        ("int64_max", "9223372036854775807", True),
        ("empty_string", "", True),
        ("unicode_string", "aé中", True),
    ],
)
def test_value_preserved_judgement(label, cell, preserved):
    class FakeResult:
        rows = [(cell,)]

    assert C._value_preserved(label, FakeResult()) is preserved


def test_value_preserved_needs_a_row():
    class Empty:
        rows = []

    assert C._value_preserved("nan", Empty()) is False


# --- loading ---------------------------------------------------------------


def test_load_capabilities_covers_every_configured_engine():
    table = C.load_capabilities()
    assert set(table) == {"sqlite", "turso"}
    for name, caps in table.items():
        assert caps.name == name
        assert caps.dialect
        assert caps.engine_version != "UNKNOWN"
        assert caps.types and caps.values and caps.features and caps.functions
        assert caps.probed_at != "UNKNOWN"


def test_get_capabilities_rejects_an_unprobed_engine():
    with pytest.raises(KeyError):
        C.get_capabilities("postgres")


def test_declared_type_and_value_expression_respect_usability():
    sqlite = C.get_capabilities("sqlite")
    assert sqlite.declared_type("integer") == "INTEGER"
    # DATE is an affinity alias there, so it is not offered
    assert sqlite.declared_type("date") is None
    assert sqlite.value_expression("nan") is None


# --- the probed facts the mutator and translator depend on ----------------


def test_sqlite_and_turso_distinguish_only_four_types():
    """Both resolve any other type name to an affinity."""
    for name in ("sqlite", "turso"):
        usable = C.get_capabilities(name).usable_types()
        assert sorted(usable) == ["blob", "integer", "real", "varchar"], (name, usable)


def test_a_derived_column_alias_is_unsupported_on_sqlite_and_turso():
    for name in ("sqlite", "turso"):
        caps = C.get_capabilities(name)
        assert not caps.has_feature("clause:derived_column_alias"), name
        assert not caps.has_feature("clause:values_column_alias"), name


def test_no_engine_accepts_having_without_an_aggregate():
    """Measured, against an assumption that turned out to be wrong.

    Both engines refuse it: "HAVING clause on a non-aggregate query". So a
    WHERE -> HAVING rewrite is only valid where an aggregate is already there.
    """
    for name in ("sqlite", "turso"):
        assert not C.get_capabilities(name).has_feature(
            "clause:having_without_aggregate"
        ), name


def test_probe_tables_are_non_empty():
    assert len(C.PROBE_TYPES) >= 20
    assert len(C.PROBE_VALUES) >= 8
    assert len(C.FEATURE_PROBES) >= 35
    assert len(C.SCALAR_PROBES) >= 80
    assert len(C.AGGREGATE_PROBES) >= 20
    assert len(C.WINDOW_PROBES) >= 10


def test_main_prints_a_summary(capsys):
    assert C.main([]) == 0
    out = capsys.readouterr().out
    for name in ("turso", "sqlite"):
        assert name in out
