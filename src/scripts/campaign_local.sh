#!/usr/bin/env bash
# Long local fuzzing campaign on THIS machine (laptop/WSL). Runs every kept
# harness in parallel under libFuzzer for HOURS hours, saving crashes and corpus.
# Resumable: re-running continues from the accumulated corpus.
#
# Usage:  scripts/campaign_local.sh <lib> [HOURS]      (default 2 hours)
# Env:    JOBS (total parallel workers, default nproc-1), SEEDS, OUT
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIB=${1:?usage: campaign_local.sh <lib> [hours]}
HOURS=${2:-2}
PY=${PY:-$HERE/.venv/bin/python}; [ -x "$PY" ] || PY=python3
cfg() { (cd "$HERE" && "$PY" -m gapharn.config "$LIB" "$1"); }
WORK=$(cfg work_dir)
SEEDS=${SEEDS:-$WORK/corpus/seeds}
OUT=${OUT:-$WORK/campaign}
RESULTS=$(cfg results_dir)
ASAN="$WORK/build-asan"; TEMPLATES="$HERE/templates"
NCPU=$(nproc); JOBS=${JOBS:-$((NCPU > 1 ? NCPU - 1 : 1))}
SECS=$((HOURS * 3600))
CLANG=${CLANG:-clang}

mapfile -t HARNESSES < <("$PY" -c "import json;print('\n'.join(json.load(open('$RESULTS/kept_harnesses.json'))))" 2>/dev/null) || true
[ ${#HARNESSES[@]} -gt 0 ] || mapfile -t HARNESSES < <(cd "$HERE/harnesses/$LIB" && ls harness_*.c | sed 's/\.c$//')
NH=${#HARNESSES[@]}
PER=$((JOBS / NH > 0 ? JOBS / NH : 1))

linkargs() { local b=$1; for l in $(cfg extra_static_libs); do echo -n "$b/$l "; done
  echo -n "$b/$(cfg static_lib) "; cat "$b/LINK_FLAGS" 2>/dev/null; echo -n " "; cfg link_flags | tr '\n' ' '; }

mkdir -p "$OUT"
echo "campaign: $LIB  harnesses=${HARNESSES[*]}  ${HOURS}h  $PER workers/harness  seeds=$SEEDS"
echo "$(date -u +%FT%TZ) start" >> "$OUT/campaign.log"
for h in "${HARNESSES[@]}"; do
  bin="$OUT/$h"
  echo "[build] $h"
  $CLANG -O1 -g -fsanitize=address,undefined,fuzzer -I"$ASAN/include" -I"$TEMPLATES" \
    "$HERE/harnesses/$LIB/$h.c" $(linkargs "$ASAN") -o "$bin"
  mkdir -p "$OUT/corpus_$h"
  echo "[run] $h for ${HOURS}h ($PER workers)"
  ASAN_OPTIONS=abort_on_error=0:detect_leaks=1:handle_sigpipe=0:dedup_token_length=3 \
  nohup "$bin" -max_total_time="$SECS" -timeout=25 -rss_limit_mb=2048 \
      -fork="$PER" -ignore_crashes=1 -ignore_timeouts=1 -ignore_ooms=1 \
      -artifact_prefix="$OUT/crash_${h}_" "$OUT/corpus_$h" "$SEEDS" \
      > "$OUT/$h.campaign.log" 2>&1 &
  echo "  pid $! -> $OUT/$h.campaign.log"
done
echo "all harnesses launched. monitor: tail -f $OUT/*.campaign.log | grep -E 'NEW|cov:|crash'"
echo "crashes: ls $OUT/crash_*  | triage: $PY -m gapharn.triage $LIB $OUT"
wait
echo "$(date -u +%FT%TZ) done" >> "$OUT/campaign.log"
echo "campaign finished. triage: $PY -m gapharn.triage $LIB $OUT --minimize"
