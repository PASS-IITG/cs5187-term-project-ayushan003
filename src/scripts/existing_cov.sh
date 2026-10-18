#!/usr/bin/env bash
# Measure which functions the EXISTING harnesses (e.g. OSS-Fuzz) execute.
#
# 1. (optional, FUZZ_SECONDS > 0) fuzz every harness for the same fixed time on the
#    `asan` build, PAR harnesses in parallel, to grow a corpus from the seeds. This
#    approximates the corpus OSS-Fuzz has accumulated, so "reached" is not
#    underestimated when seeds are few or in the wrong format.
# 2. link every harness against the `cov` build, replay seeds + grown corpus once
#    (-runs=0, as OSS-Fuzz's coverage job does) and export llvm-cov JSON to
#    work/<lib>/existing-cov/json/<harness>.json, which gapharn.gap reads.
#
# Usage: scripts/existing_cov.sh <lib> <seed_dir> [seed_dir ...]
# Env: FUZZ_SECONDS (default 0), PAR (parallel fuzzers, default 4), PY, CLANG/CLANGXX,
#      LLVM_PROFDATA, LLVM_COV, GAPHARN_WORK, OSS_FUZZ
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIB=${1:?usage: existing_cov.sh <lib> <seed_dir>...}; shift
[ $# -ge 1 ] || { echo "give at least one seed dir" >&2; exit 2; }
SEEDS=("$@")
FUZZ_SECONDS=${FUZZ_SECONDS:-0}; PAR=${PAR:-4}
PY=${PY:-python3}
CLANG=${CLANG:-clang}; CLANGXX=${CLANGXX:-clang++}
LLVM_PROFDATA=${LLVM_PROFDATA:-llvm-profdata}; LLVM_COV=${LLVM_COV:-llvm-cov}
cfg() { (cd "$HERE" && "$PY" -m gapharn.config "$LIB" "$1"); }
COVFLAGS="-O1 -g -fprofile-instr-generate -fcoverage-mapping"
ASANFLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined"

WORK=$(cfg work_dir); STATIC_REL=$(cfg static_lib)
mapfile -t EXTRA_REL < <(cfg extra_static_libs | sed '/^$/d')
mapfile -t HARNESSES < <(cfg harnesses | sed '/^$/d')
mapfile -t SUPPORT < <(cfg harness_support_files | sed '/^$/d')
if [ ${#HARNESSES[@]} -eq 0 ]; then
  echo "No existing harness sources found for $LIB." >&2
  echo "Check harness_globs in targets/$LIB/config.yaml; for OSS-Fuzz-hosted harnesses" >&2
  echo "clone https://github.com/google/oss-fuzz and export OSS_FUZZ=<path>." >&2
  exit 1
fi

OUT="$WORK/existing-cov"
rm -rf "$OUT"; mkdir -p "$OUT"/{bin,prof,json,log,obj,corpus,crashes}

# build_variant <variant: cov|asan> <compile flags> <link sanitizer flags>
# -> $OUT/bin/<name>.<variant> for every harness that builds.
build_variant() {
  local v=$1 cflags=$2 lflags=$3 dir="$WORK/build-$v"
  [ -f "$dir/$STATIC_REL" ] || { echo "missing $dir/$STATIC_REL - run: scripts/build_target.sh $LIB $v" >&2; exit 1; }
  local libs=() objs=() link
  for l in "${EXTRA_REL[@]}"; do libs+=("$dir/$l"); done
  libs+=("$dir/$STATIC_REL")
  # Generated flags (pkg-config Libs.private) may miss deps listed as Requires.private,
  # so the config's link_flags are always appended.
  link="$(cat "$dir/LINK_FLAGS" 2>/dev/null || true) $(cfg link_flags | tr '\n' ' ')"
  for s in "${SUPPORT[@]}"; do
    local c=$CLANG; case "$s" in *.cc|*.cpp|*.cxx) c=$CLANGXX ;; esac
    $c $cflags -fsanitize=fuzzer-no-link -I"$dir/include" -c "$s" -o "$OUT/obj/$(basename "${s%.*}").$v.o"
    objs+=("$OUT/obj/$(basename "${s%.*}").$v.o")
  done
  for h in "${HARNESSES[@]}"; do
    local name; name=$(basename "${h%.*}")
    local c=$CLANG; case "$h" in *.cc|*.cpp|*.cxx) c=$CLANGXX ;; esac
    # Link with clang++ so C++ wrapper libraries (e.g. libtiffxx) always find libstdc++.
    if ! { $c $cflags -fsanitize=fuzzer-no-link -I"$dir/include" -c "$h" -o "$OUT/obj/$name.$v.o" \
           && $CLANGXX $cflags $lflags "$OUT/obj/$name.$v.o" "${objs[@]}" "${libs[@]}" $link \
                -o "$OUT/bin/$name.$v"; } 2> "$OUT/log/$name.$v.build.log"; then
      echo "  [$v] $name: BUILD FAILED - $OUT/log/$name.$v.build.log" >&2
      tail -3 "$OUT/log/$name.$v.build.log" >&2
    fi
  done
}

echo "[existing-cov] $LIB: ${#HARNESSES[@]} harnesses, seeds: ${SEEDS[*]}"
build_variant cov "$COVFLAGS" "-fprofile-instr-generate -fsanitize=fuzzer"

if [ "$FUZZ_SECONDS" -gt 0 ]; then
  build_variant asan "$ASANFLAGS" "-fsanitize=fuzzer,address,undefined"
  echo "[existing-cov] fuzzing each harness for ${FUZZ_SECONDS}s, $PAR in parallel" \
       "(~$(( (${#HARNESSES[@]} + PAR - 1) / PAR * FUZZ_SECONDS / 60 )) min)"
  for bin in "$OUT"/bin/*.asan; do
    name=$(basename "$bin" .asan); mkdir -p "$OUT/corpus/$name"
    # Fork mode keeps fuzzing after crashes/timeouts/OOMs; they are known-harness
    # findings saved under crashes/, not part of the gap measurement.
    echo "$bin -fork=1 -ignore_crashes=1 -ignore_timeouts=1 -ignore_ooms=1 -max_total_time=$FUZZ_SECONDS" \
         "-timeout=25 -rss_limit_mb=1024 -close_fd_mask=3 -artifact_prefix=$OUT/crashes/$name- $OUT/corpus/$name ${SEEDS[*]}" \
         "> $OUT/log/$name.fuzz.log 2>&1"
  done | xargs -P "$PAR" -I{} sh -c '{}' || true
fi

MEASURED=0
for bin in "$OUT"/bin/*.cov; do
  [ -e "$bin" ] || continue
  name=$(basename "$bin" .cov)
  dirs=("${SEEDS[@]}"); [ -d "$OUT/corpus/$name" ] && dirs+=("$OUT/corpus/$name")
  LLVM_PROFILE_FILE="$OUT/prof/$name.%p.profraw" \
    "$bin" -runs=0 -timeout=25 -rss_limit_mb=2560 "${dirs[@]}" > "$OUT/log/$name.run.log" 2>&1 \
    || echo "  note: $name replay exited non-zero (crash/timeout on an input?) - $OUT/log/$name.run.log" >&2
  if ! ls "$OUT"/prof/"$name".*.profraw >/dev/null 2>&1; then
    echo "  $name: no coverage profile written - not measured" >&2; continue
  fi
  "$LLVM_PROFDATA" merge -sparse "$OUT"/prof/"$name".*.profraw -o "$OUT/prof/$name.profdata"
  "$LLVM_COV" export -format=text -skip-expansions -instr-profile="$OUT/prof/$name.profdata" \
    "$bin" > "$OUT/json/$name.json"
  MEASURED=$((MEASURED + 1))
done

cat > "$OUT/META.json" <<EOF
{"harnesses_total": ${#HARNESSES[@]}, "harnesses_measured": $MEASURED, "fuzz_seconds_per_harness": $FUZZ_SECONDS,
 "seed_dirs": $(printf '%s\n' "${SEEDS[@]}" | "$PY" -c 'import json,sys; print(json.dumps(sys.stdin.read().split()))'),
 "measured_utc": "$(date -u +%FT%TZ)"}
EOF
echo "[existing-cov] $MEASURED/${#HARNESSES[@]} harnesses measured -> $OUT/json   next: python -m gapharn.gap $LIB"
[ "$MEASURED" -eq "${#HARNESSES[@]}" ] || echo "[existing-cov] WARNING: not all harnesses measured; the gap will be overestimated" >&2
