from pathlib import Path

import pytest

ci = pytest.importorskip("clang.cindex")

from gapharn.apimodel import c_int, eval_macro_body, parse_headers, resolve_macros  # noqa: E402

TOY = Path(__file__).parent / "fixtures" / "toy"


@pytest.mark.parametrize("tok,val", [("0", 0), ("42", 42), ("0x1F", 31), ("0644", 420), ("10UL", 10), ("abc", None)])
def test_c_int(tok, val):
    assert c_int(tok) == val


def test_eval_macro_body():
    known = {"BASE": 16}
    assert eval_macro_body(["(", "BASE", "|", "1", ")"], known) == 17
    assert eval_macro_body(["(", "-", "30", ")"], known) == -30
    assert eval_macro_body(["1", "<<", "4"], known) == 16
    assert eval_macro_body(['"str"'], known) is None
    assert eval_macro_body(["UNKNOWN", "|", "1"], known) is None
    assert eval_macro_body([], known) is None


def test_resolve_macros_out_of_order():
    bodies = {"C": ["B", "+", "1"], "B": ["A", "*", "2"], "A": ["3"], "S": ['"x"']}
    assert resolve_macros(bodies) == {"A": 3, "B": 6, "C": 7}


@pytest.fixture(scope="module")
def model():
    return parse_headers([TOY / "toy.h"], [TOY])


def test_no_parse_errors(model):
    assert model["diagnostics"] == []


def test_functions(model):
    fns = {f["name"]: f for f in model["functions"]}
    assert set(fns) == {"toy_new", "toy_feed", "toy_set_kind", "toy_set_opts", "toy_log", "toy_old", "toy_free"}
    assert fns["toy_log"]["variadic"] and not fns["toy_feed"]["variadic"]
    assert fns["toy_old"]["deprecated"] and not fns["toy_new"]["deprecated"]
    assert "Create a new toy handle" in fns["toy_new"]["comment"]
    assert fns["toy_new"]["return"]["pointer_depth"] == 1
    assert fns["toy_new"]["params"] == []

    buf, length = fns["toy_feed"]["params"][1:]
    assert (buf["name"], buf["pointer_depth"], buf["pointee_const"]) == ("buf", 1, True)
    assert length["type"] == "size_t" and length["kind"] in ("ULONG", "ULONGLONG")
    assert fns["toy_set_kind"]["params"][1]["kind"] == "ENUM"


def test_enums_macros_structs(model):
    assert model["enums"]["toy_kind"] == {"TOY_KIND_A": 1, "TOY_KIND_B": 2}
    m = model["int_macros"]
    assert (m["TOY_OK"], m["TOY_ERR"], m["TOY_MODE_FAST"], m["TOY_MODE_SLOW"], m["TOY_PERM"]) == (0, -30, 17, 18, 420)
    assert "TOY_MAX" not in m and "TOY_NAME" not in m and "TOY_H" not in m
    assert model["opaque_structs"] == ["toy"]
    assert "toy_opts" in model["typedefs"]
