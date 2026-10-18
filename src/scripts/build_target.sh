#!/usr/bin/env bash
# Clone (once) and build a target library in one or more variants.
#
# Usage: scripts/build_target.sh <lib> [variant ...]
#   variants: cov       clang source-based coverage   (gap analysis, oracle O3)
#             asan      clang ASan+UBSan+fuzzer-no-link (libFuzzer harnesses, oracle O2)
#             afl       afl-clang-fast, no sanitizer   (fast AFL++ instances)
#             afl-asan  afl-clang-fast with ASan+UBSan (one AFL++ instance per harness)
#   default:  cov asan
# Env: PY (python with gapharn deps), CLANG/CLANGXX, JOBS, GAPHARN_WORK.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIB=${1:?usage: build_target.sh <lib> [variant ...]}; shift
VARIANTS=${*:-cov asan}
PY=${PY:-python3}
CLANG=${CLANG:-clang}; CLANGXX=${CLANGXX:-clang++}
JOBS=${JOBS:-$(nproc)}
cfg() { (cd "$HERE" && "$PY" -m gapharn.config "$LIB" "$1"); }

REPO=$(cfg repo); REF=$(cfg ref); SRC=$(cfg src_dir); WORK=$(cfg work_dir)
if [ ! -d "$SRC/.git" ]; then
  mkdir -p "$WORK"
  # Blobless clone: full commit history (needed for pinning/activity stats) but
  # file contents are fetched only for the checked-out commit. Much smaller download.
  git clone --filter=blob:none "$REPO" "$SRC"
else
  git -C "$SRC" fetch --quiet origin || echo "[build] warning: fetch failed (offline?), using local checkout"
fi
git -C "$SRC" checkout --quiet "$REF"
COMMIT=$(git -C "$SRC" rev-parse HEAD)

for v in $VARIANTS; do
  unset AFL_USE_ASAN AFL_USE_UBSAN
  case $v in
    cov)      CC=$CLANG; CXX=$CLANGXX
              FLAGS="-O1 -g -fprofile-instr-generate -fcoverage-mapping"; LD="-fprofile-instr-generate" ;;
    asan)     CC=$CLANG; CXX=$CLANGXX
              FLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fsanitize=fuzzer-no-link"
              LD="-fsanitize=address,undefined" ;;
    afl)      CC=afl-clang-fast; CXX=afl-clang-fast++; FLAGS="-O2 -g"; LD="" ;;
    afl-asan) CC=afl-clang-fast; CXX=afl-clang-fast++; FLAGS="-O1 -g"; LD=""
              export AFL_USE_ASAN=1 AFL_USE_UBSAN=1 ;;
    *) echo "[build] unknown variant: $v" >&2; exit 2 ;;
  esac
  command -v "$CC" >/dev/null || { echo "[build] $CC not found (variant $v)" >&2; exit 1; }
  OUT="$WORK/build-$v"
  rm -rf "$OUT"; mkdir -p "$OUT"
  echo "[build] $LIB @ ${COMMIT:0:12}  variant=$v  cc=$CC"
  START=$(date +%s)
  SRC="$SRC" OUT="$OUT" CC="$CC" CXX="$CXX" CFLAGS="$FLAGS" CXXFLAGS="$FLAGS" LDFLAGS="$LD" JOBS="$JOBS" \
    bash "$HERE/targets/$LIB/build.sh"
  {
    echo "library=$LIB"
    echo "commit=$COMMIT"
    echo "variant=$v"
    echo "cc=$($CC --version 2>/dev/null | head -1)"
    echo "cflags=$FLAGS"
    echo "built_utc=$(date -u +%FT%TZ)"
    echo "seconds=$(( $(date +%s) - START ))"
  } > "$OUT/BUILD_INFO"
  echo "[build] done in $(( $(date +%s) - START )) s -> $OUT"
done
