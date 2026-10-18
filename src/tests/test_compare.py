from gapharn.compare import rank, takes_data


def fn(*canonicals):
    return {"params": [{"canonical": c, "pointer_depth": c.count("*")} for c in canonicals]}


def test_takes_data():
    assert takes_data(fn("struct archive *", "const void *", "unsigned long"))
    assert takes_data(fn("const char *"))
    assert takes_data(fn("unsigned char *"))           # xmlChar *
    assert takes_data(fn("struct _IO_FILE *"))          # FILE *
    assert not takes_data(fn("struct archive *", "int"))
    assert not takes_data(fn("char **"))                # pointer_depth 2: out-parameter
    assert not takes_data(fn())


def row(lib, data_gap, gap, commits):
    return {"lib": lib, "data_gap": data_gap, "gap": gap, "commits_90d": commits}


def test_rank_filters_inactive_then_orders_by_data_gap():
    rows = [row("a", 50, 100, 40), row("b", 80, 90, 3), row("c", 60, 70, 25), row("d", 60, 80, None)]
    assert [r["lib"] for r in rank(rows)] == ["c", "a"]


def test_rank_drops_incompletely_measured_libraries():
    rows = [dict(row("a", 50, 100, 40), complete=True), dict(row("b", 199, 199, 56), complete=False)]
    assert [r["lib"] for r in rank(rows)] == ["a"]
