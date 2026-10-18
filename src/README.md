# GapHarn: gap-directed fuzz-harness generation for C libraries

Existing fuzz harnesses for mature C libraries call only part of the public API.
GapHarn measures that **API gap** and generates harnesses for the functions that
no existing harness ever executes.

Status: **Stage 0 (gap analysis) and Stage 1 v0 (API model) are implemented.**
Generation, oracles, selection, fuzzing and triage follow the project plan.

## Layout

```
src/
├── gapharn/            Python package
│   ├── config.py       targets/<lib>/config.yaml loader + project paths
│   ├── apimodel.py     Stage 1: public headers -> functions, enums, int macros, opaque structs
│   ├── gap.py          Stage 0: API ∩ exported symbols − functions executed by existing harnesses
│   └── seeds.py        seed corpus from the library's own test files
├── targets/<lib>/      config.yaml (repo, ref, headers, harness globs) + build.sh
├── scripts/
│   ├── check_env.sh    toolchain verification (laptop / lab)
│   ├── build_target.sh clone + build variants: cov, asan, afl, afl-asan
│   └── existing_cov.sh coverage of the existing harnesses over a corpus (llvm-cov JSON)
├── tests/              pytest (unit tests + a real clang/llvm-cov end-to-end test)
└── results/<lib>/      api.json, gap.json (committed as `data:` commits)
```

## Setup (Ubuntu 24.04 or WSL Ubuntu 24.04)

```bash
sudo apt install -y build-essential clang llvm lld git cmake autoconf automake libtool pkg-config \
  python3-venv python3-pip tmux rsync curl \
  zlib1g-dev libbz2-dev liblzma-dev libzstd-dev liblz4-dev libxml2-dev libssl-dev \
  libacl1-dev libattr1-dev libjpeg-dev libclang-rt-18-dev
cd src
make setup            # .venv with pinned requirements.txt
make test             # expected: all tests pass
bash scripts/check_env.sh laptop
```

On WSL, keep build outputs on the Linux filesystem; `/mnt/c` is much slower:

```bash
export GAPHARN_WORK=~/gapharn-work
```

## Stage 0: measure the API gap

```bash
make stage0 LIB=libarchive
# = build (cov) -> api -> seeds -> existing-cov -> gap   -> results/libarchive/gap.json
```

Steps can be run on their own: `make build`, `make api`, `make seeds`,
`make existing-cov`, `make gap` (all take `LIB=<name>`).

Definitions used by `gap.py`:

* **API** = functions declared in the target's public headers that are also
  global text symbols in the built library, excluding deprecated functions
  (`--include-deprecated` keeps them).
* **reached** = API functions with execution count > 0 in the llvm-cov export
  of any existing harness, run once (`-runs=0`) over the corpus.
* **gap** = API − reached.

Note: the default corpus is built from the library's own test files
(`make seeds`). OSS-Fuzz's accumulated corpus can reach more functions; pass it
with `make existing-cov CORPUS="dir1 dir2"` when it is available.

## Adding a target

Create `targets/<lib>/config.yaml` (copy the libarchive one) and
`targets/<lib>/build.sh`. The build script gets `SRC OUT CC CXX CFLAGS LDFLAGS JOBS`
and must produce `$OUT/lib/<static lib>`, `$OUT/include/` and, if it can,
`$OUT/LINK_FLAGS`.

## Tested versions

Ubuntu 24.04.4 (WSL2), clang/LLVM 18.1.3, Python 3.12.3, libclang 18.1.1,
PyYAML 6.0.3, pytest 9.1.1, CMake from Ubuntu 24.04.
