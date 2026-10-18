import binascii

from gapharn.config import load_target
from gapharn.seeds import collect, uudecode


def test_libarchive_config_loads():
    t = load_target("libarchive")
    assert t.headers == ["libarchive/archive.h", "libarchive/archive_entry.h"]
    assert t.static_lib == "lib/libarchive.a"
    assert t.src_dir.name == "src" and t.src_dir.parent.name == "libarchive"
    assert t.build_dir("cov").name == "build-cov"


def test_header_base_and_harness_exclude(tmp_path, monkeypatch):
    import gapharn.config as config

    monkeypatch.setattr(config, "WORK_DIR", tmp_path)
    fuzz = tmp_path / "libxml2" / "src" / "fuzz"
    fuzz.mkdir(parents=True)
    for f in ("api.c", "xml.c", "fuzz.c", "genSeed.c", "testFuzzer.c", "lint.c"):
        (fuzz / f).write_text("")
    inc = tmp_path / "libxml2" / "build-cov" / "include" / "libxml"
    inc.mkdir(parents=True)
    (inc / "tree.h").write_text("")

    t = config.load_target("libxml2")
    assert [p.name for p in t.harness_paths()] == ["api.c", "xml.c"]
    assert [p.name for p in t.harness_support_paths()] == ["fuzz.c"]
    assert [p.name for p in t.header_paths()] == ["tree.h"]
    assert t.include_paths()[-1] == tmp_path / "libxml2" / "build-cov" / "include"


def test_unset_env_var_globs_are_skipped(monkeypatch):
    monkeypatch.delenv("OSS_FUZZ", raising=False)
    t = load_target("libarchive")
    assert all("$" not in str(p) for p in t.harness_paths())


def _uuencode(data: bytes) -> str:
    lines = ["begin 644 sample.bin"]
    lines += [binascii.b2a_uu(data[i:i + 45]).decode().rstrip("\n") for i in range(0, len(data), 45)]
    lines += ["`", "end", ""]
    return "\n".join(lines)


def test_uudecode_roundtrip():
    data = bytes(range(256)) * 3
    assert uudecode(_uuencode(data)) == data


def test_collect_dedupes_and_decodes(tmp_path):
    src = tmp_path / "src" / "test"
    src.mkdir(parents=True)
    (src / "a.uu").write_text(_uuencode(b"hello archive"))
    (src / "b.uu").write_text(_uuencode(b"hello archive"))  # duplicate content
    (src / "empty.uu").write_text(_uuencode(b""))
    out = tmp_path / "corpus"
    written, skipped = collect(tmp_path / "src", ["test/*.uu"], out, max_size=1 << 20)
    assert (written, skipped) == (1, 1)
    assert [p.read_bytes() for p in out.iterdir()] == [b"hello archive"]
