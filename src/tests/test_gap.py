import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from gapharn.gap import api_prefix, compute_gap, executed_functions, exported_functions, parse_nm, signature

TOY = Path(__file__).parent / "fixtures" / "toy"

NM_ARCHIVE = """
toy.o:
0000000000000000 T toy_feed
                 U calloc
0000000000000010 t static_helper
0000000000000020 W weak_fn

other.o:
0000000000000000 T versioned@@LIB_1.0
0000000000000008 D some_data
"""


def fn(name, deprecated=False):
    return {"name": name, "deprecated": deprecated, "return": {"type": "int"},
            "params": [{"name": "x", "type": "int"}], "variadic": False}


def test_parse_nm():
    assert parse_nm(NM_ARCHIVE) == {"toy_feed", "weak_fn", "versioned"}


def test_api_prefix():
    assert api_prefix("archive_write_open_memory") == "archive_write"
    assert api_prefix("TIFFOpen") == "TIFFOpen"
    assert api_prefix("xmlTextWriter_x") == "xmlTextWriter"


def test_signature():
    f = {"name": "f", "return": {"type": "int"}, "variadic": True,
         "params": [{"name": "fmt", "type": "const char *"}]}
    assert signature(f) == "int f(const char * fmt, ...)"
    assert signature({"name": "g", "return": {"type": "void"}, "params": []}) == "void g(void)"


def test_executed_functions_takes_max_over_files(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"data": [{"functions": [{"name": "f", "count": 0}, {"name": "g", "count": 2}]}]}))
    b.write_text(json.dumps({"data": [{"functions": [{"name": "f", "count": 5}]}]}))
    assert executed_functions([a, b]) == Counter({"f": 5, "g": 2})


def test_compute_gap():
    api = [fn("a_x_one"), fn("a_x_two"), fn("a_y_one"), fn("a_old", deprecated=True), fn("a_inline")]
    exported = {"a_x_one", "a_x_two", "a_y_one", "a_old"}
    g = compute_gap(api, exported, Counter({"a_x_one": 3, "a_y_one": 0}))
    assert (g["api_total"], g["reached_total"], g["gap_total"]) == (3, 1, 2)
    assert g["reached"] == ["a_x_one"]
    assert [x["name"] for x in g["gap"]] == ["a_x_two", "a_y_one"]
    assert g["declared_not_exported"] == ["a_inline"]
    assert g["by_prefix"]["a_x"] == {"api": 2, "reached": 1, "gap": 1}
    assert compute_gap(api, exported, Counter(), include_deprecated=True)["api_total"] == 4


TOOLS = ["clang", "llvm-profdata", "llvm-cov", "nm", "ar"]


@pytest.mark.toolchain
@pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS), reason="needs clang + llvm tools")
def test_end_to_end_on_toy_library(tmp_path):
    """Real pipeline: coverage build -> llvm-cov export -> api model -> gap."""
    pytest.importorskip("clang.cindex")
    from gapharn.apimodel import parse_headers

    def run(*cmd, **kw):
        subprocess.run(cmd, check=True, cwd=tmp_path, **kw)

    cov = ["-fprofile-instr-generate", "-fcoverage-mapping"]
    run("clang", *cov, "-c", str(TOY / "toy.c"), "-o", "toy.o")
    run("ar", "rcs", "libtoy.a", "toy.o")
    run("clang", *cov, f"-I{TOY}", str(TOY / "driver.c"), "libtoy.a", "-o", "driver")
    run(str(tmp_path / "driver"), env={"LLVM_PROFILE_FILE": str(tmp_path / "d.profraw")})
    run("llvm-profdata", "merge", "-o", "d.profdata", "d.profraw")
    with open(tmp_path / "cov.json", "w") as out:
        run("llvm-cov", "export", "-format=text", "-instr-profile=d.profdata", "driver", stdout=out)

    model = parse_headers([TOY / "toy.h"], [TOY])
    exported = exported_functions(tmp_path / "libtoy.a")
    assert "toy_internal_helper" in exported  # global, but not declared in the header
    g = compute_gap(model["functions"], exported, executed_functions([tmp_path / "cov.json"]))

    assert g["reached"] == ["toy_feed", "toy_free", "toy_new"]
    assert sorted(x["name"] for x in g["gap"]) == ["toy_log", "toy_set_kind", "toy_set_opts"]
    assert g["api_total"] == 6  # toy_old is deprecated, toy_internal_helper is not public
