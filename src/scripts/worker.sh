#!/usr/bin/env bash
# Lab worker: start (or resume) the AFL++ campaign for a slice of harnesses on
# this PC, under tmux so it survives logout. Run on each lab PC after `git pull`.
#
# Usage:
#   scripts/worker.sh <lib> [harness ...]     # no args => every kept harness
# Env: SEEDS, OUT (default work/<lib>/campaign), JOBS (default nproc).
#
# Different PCs should run DIFFERENT harnesses (no corpus sync needed). Example:
#   pc2$ scripts/worker.sh libarchive harness_write
#   pc3$ scripts/worker.sh libarchive harness_read
#   pc4$ scripts/worker.sh libarchive harness_entry
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIB=${1:?usage: worker.sh <lib> [harness ...]}; shift || true
export ENGINE=afl
export JOBS=${JOBS:-$(nproc)}
echo "host $(hostname): launching AFL++ workers ($JOBS cores) for $LIB ${*:-<all kept>}"
bash "$HERE/scripts/fuzz.sh" "$LIB" "$@"
echo "Running under tmux. Status: tmux ls | Attach: tmux attach -t <name> | Stop all: tmux kill-server"
