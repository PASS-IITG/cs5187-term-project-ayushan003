#!/usr/bin/env bash
# Pull campaign results (crashes + fuzzer stats) from the lab worker PCs to this
# machine (the controller/laptop). Run from the controller. Reads nodes.txt:
# one "user@host" per line (the other lab PCs).
#
# Usage: scripts/collect.sh <lib> [nodes.txt]
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIB=${1:?usage: collect.sh <lib> [nodes.txt]}
NODES=${2:-$HERE/scripts/nodes.txt}
PY=${PY:-$HERE/.venv/bin/python}; [ -x "$PY" ] || PY=python3
WORK=$(cd "$HERE" && "$PY" -m gapharn.config "$LIB" work_dir)
DEST="$WORK/collected"
mkdir -p "$DEST"
[ -f "$NODES" ] || { echo "no $NODES (one user@host per line)"; exit 1; }
while read -r host; do
  [ -z "$host" ] && continue
  echo "[collect] $host"
  # Only crashes and stats; corpora stay on the workers (they are large).
  rsync -az --prune-empty-dirs \
    --include='*/' --include='crash_*' --include='crashes/***' --include='fuzzer_stats' \
    --include='*.log' --exclude='*' \
    "$host:~/cs5187-term-project-ayushan003/src/$(basename "$WORK")/$LIB/campaign/" "$DEST/$host/" || \
    echo "  (could not reach $host)"
done < "$NODES"
echo "[collect] -> $DEST ; triage with: $PY -m gapharn.triage $LIB $DEST/<host>"
