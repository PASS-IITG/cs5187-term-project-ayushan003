#!/usr/bin/env bash
# Build and run kept harnesses. Two engines:
#   libfuzzer (default): quick local runs, in-process, -jobs parallelism
#   afl: AFL++ for the lab campaign (one afl-asan + N afl-fast per harness)
#
# Usage:
#   scripts/fuzz.sh <lib> [harness ...]              # default: every kept harness
# Env:
#   ENGINE=libfuzzer|afl   SECONDS_PER=60   JOBS=$(nproc)   SEEDS="dir1 dir2"
#   OUT=<campaign dir>     CLANG/CLANGXX
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIB=${1:?usage: fuzz.sh <lib> [harness ...]}; shift || true
PY=${PY:-$HERE/.venv/bin/python}; [ -x "$PY" ] || PY=python3
ENGINE=${ENGINE:-libfuzzer}
SECONDS_PER=${SECONDS_PER:-60}
JOBS=${JOBS:-$(nproc)}
CLANG=${CLANG:-clang}; CLANGXX=${CLANGXX:-clang++}
cfg() { (cd "$HERE" && "$PY" -m gapharn.config "$LIB" "$1"); }
WORK=$(cfg work_dir); RESULTS=$(cfg results_dir)
HDIR="$HERE/harnesses/$LIB"; TEMPLATES="$HERE/templates"
SEEDS=${SEEDS:-$WORK/corpus/seeds}
OUT=${OUT:-$WORK/campaign}
ASAN="$WORK/build-asan"; AFLB="$WORK/build-afl"; AFLA="$WORK/build-afl-asan"

# Which harnesses: args, else the ones oracle kept, else all generated.
if [ $# -gt 0 ]; then HARNESSES=("$@")
elif [ -f "$RESULTS/kept_harnesses.json" ]; then
  mapfile -t HARNESSES < <("$PY" -c "import json,sys;print('\n'.join(json.load(open('$RESULTS/kept_harnesses.json'))))")
else
  mapfile -t HARNESSES < <(cd "$HDIR" && ls harness_*.c 2>/dev/null | sed 's/\.c$//')
fi
[ ${#HARNESSES[@]} -gt 0 ] || { echo "no harnesses for $LIB (run gen_rule / oracle first)" >&2; exit 1; }

linkargs() { local b=$1; for l in $(cfg extra_static_libs); do echo -n "$b/$l "; done
  echo -n "$b/$(cfg static_lib) "; cat "$b/LINK_FLAGS" 2>/dev/null; echo -n " "; cfg link_flags | tr '\n' ' '; }

mkdir -p "$OUT"
echo "[fuzz] $LIB engine=$ENGINE ${SECONDS_PER}s/harness seeds=$SEEDS -> $OUT"
echo "[fuzz] harnesses: ${HARNESSES[*]}"

if [ "$ENGINE" = libfuzzer ]; then
  [ -d "$ASAN/lib" ] || { echo "missing $ASAN - run: scripts/build_target.sh $LIB asan" >&2; exit 1; }
  for h in "${HARNESSES[@]}"; do
    bin="$OUT/$h"
    $CLANG -O1 -g -fsanitize=address,undefined,fuzzer -I"$ASAN/include" -I"$TEMPLATES" \
      "$HDIR/$h.c" $(linkargs "$ASAN") -o "$bin"
    mkdir -p "$OUT/corpus_$h"
    echo "[fuzz] libFuzzer: $h"
    ASAN_OPTIONS=abort_on_error=0:detect_leaks=1 \
      "$bin" -max_total_time="$SECONDS_PER" -timeout=25 -rss_limit_mb=2048 -jobs="$JOBS" -workers="$JOBS" \
      -artifact_prefix="$OUT/crash_${h}_" "$OUT/corpus_$h" "$SEEDS" > "$OUT/$h.log" 2>&1 || true
    grep -m1 -E "ERROR|SUMMARY" "$OUT/$h.log" || true
  done
  CR=$(ls "$OUT"/crash_* 2>/dev/null | wc -l)
  echo "[fuzz] done. crash artifacts: $CR (triage with: python -m gapharn.triage $LIB $OUT)"
  exit 0
fi

# ENGINE=afl : lab campaign. One ASan instance + (cores-1) fast instances per harness.
command -v afl-fuzz >/dev/null || { echo "afl-fuzz not found" >&2; exit 1; }
[ -d "$AFLB/lib" ] || { echo "missing $AFLB - run: scripts/build_target.sh $LIB afl afl-asan" >&2; exit 1; }
export AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_AUTORESUME=1
for h in "${HARNESSES[@]}"; do
  afl-clang-fast  -O2 -g -I"$AFLB/include"  -I"$TEMPLATES" "$HDIR/$h.c" $(linkargs "$AFLB")  -o "$OUT/$h.fast"
  AFL_USE_ASAN=1 afl-clang-fast -O1 -g -I"$AFLA/include" -I"$TEMPLATES" "$HDIR/$h.c" $(linkargs "$AFLA") -o "$OUT/$h.asan"
  mkdir -p "$OUT/sync_$h"
  tmux new-session -d -s "gh_${h}_m" "afl-fuzz -i '$SEEDS' -o '$OUT/sync_$h' -M main -- '$OUT/$h.asan'"
  for i in $(seq 1 $((JOBS > 1 ? JOBS - 1 : 1))); do
    tmux new-session -d -s "gh_${h}_s$i" "afl-fuzz -i '$SEEDS' -o '$OUT/sync_$h' -S s$i -- '$OUT/$h.fast'"
  done
  echo "[fuzz] AFL++ started for $h (1 main+asan, $((JOBS-1)) secondary) under tmux"
done
echo "[fuzz] watch: tmux ls ; stop: tmux kill-server ; crashes land in $OUT/sync_*/\*/crashes"
