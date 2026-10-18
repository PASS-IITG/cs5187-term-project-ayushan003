#!/usr/bin/env bash
# check_env.sh - verify the GapHarn environment on the laptop or a lab PC.
# Usage:  ./check_env.sh laptop
#         ./check_env.sh lab [nodes.txt]     # nodes.txt = one "user@host" per line (other lab PCs)
# Optional env: CC_BIN=clang-18  VENV=<path to venv, default src/.venv>  LLAMA_URL=http://127.0.0.1:8080
ROLE="${1:-laptop}"; NODES="${2:-}"
CC_BIN="${CC_BIN:-clang}"
VENV="${VENV:-$(cd "$(dirname "$0")/.." && pwd)/.venv}"
LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:8080}"
P=0; F=0; W=0
pass() { printf '  [PASS] %s\n' "$1"; P=$((P+1)); }
fail() { printf '  [FAIL] %s\n         fix: %s\n' "$1" "$2"; F=$((F+1)); }
warn() { printf '  [WARN] %s\n' "$1"; W=$((W+1)); }
check() { local d="$1" fix="$2"; shift 2; if "$@" >/dev/null 2>&1; then pass "$d"; else fail "$d" "$fix"; fi; }
ver() { "$@" --version 2>/dev/null | grep -oE 'version [0-9]+' | head -1 | grep -oE '[0-9]+'; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
echo "GapHarn environment check  (role: $ROLE)"

echo "== 1. System"
echo "  $(lsb_release -ds 2>/dev/null) | kernel $(uname -r) | cores $(nproc) | RAM $(free -g | awk '/Mem:/{print $2}') GB"
[ "$(nproc)" -ge 4 ] && pass "cores >= 4" || warn "fewer than 4 cores: run fewer fuzzer instances"
AVAIL=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc 0-9)
[ "${AVAIL:-0}" -ge 30 ] && pass "free disk in \$HOME = ${AVAIL} GB (>= 30)" || fail "free disk in \$HOME = ${AVAIL} GB" "free up space (need >= 30 GB)"

echo "== 2. Tools"
for t in git make cmake autoreconf libtoolize pkg-config python3 tmux rsync ssh nm curl; do
  check "$t" "sudo apt install build-essential cmake autoconf automake libtool pkg-config python3 tmux rsync openssh-client curl git" command -v "$t"
done
check "$CC_BIN" "sudo apt install clang" command -v "$CC_BIN"
CV=$(ver "$CC_BIN"); echo "  clang major version: ${CV:-none}"
for t in llvm-profdata llvm-cov llvm-symbolizer; do
  if command -v "$t" >/dev/null 2>&1; then
    TV=$(ver "$t")
    [ "$TV" = "$CV" ] && pass "$t version $TV matches clang" \
      || fail "$t version $TV != clang $CV" "install llvm-$CV and put its bin dir first in PATH (e.g. /usr/lib/llvm-$CV/bin)"
  else fail "$t" "sudo apt install llvm"; fi
done
check "afl-fuzz (AFL++)" "sudo apt install afl++  (or build AFLplusplus from source)" command -v afl-fuzz
check "afl-clang-fast" "same as above" command -v afl-clang-fast

echo "== 3. Library build dependencies (libarchive / libtiff / libxml2)"
for p in zlib liblzma libzstd liblz4 libxml-2.0 openssl libjpeg; do
  check "pkg-config $p" "sudo apt install zlib1g-dev liblzma-dev libzstd-dev liblz4-dev libxml2-dev libssl-dev libjpeg-dev" pkg-config --exists "$p"
done
check "bzlib.h" "sudo apt install libbz2-dev" test -f /usr/include/bzlib.h
check "sys/acl.h" "sudo apt install libacl1-dev libattr1-dev" test -f /usr/include/sys/acl.h

echo "== 4. libFuzzer + ASan/UBSan actually catch a bug"
cat > "$T/bug.c" <<'EOF'
#include <stdint.h>
#include <stddef.h>
#include <string.h>
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) {
  char buf[4];
  if (n >= 3 && d[0] == 'B' && d[1] == 'U' && d[2] == 'G') { memcpy(buf, d, n); return buf[0] == 'X'; }
  return 0;
}
EOF
printf 'BUGxxxxxxxxx' > "$T/crash"
if "$CC_BIN" -g -O1 -fsanitize=fuzzer,address,undefined "$T/bug.c" -o "$T/bug_lf" >"$T/cc.log" 2>&1; then
  pass "compile with -fsanitize=fuzzer,address,undefined"
  "$T/bug_lf" "$T/crash" >"$T/run.log" 2>&1
  grep -q "AddressSanitizer" "$T/run.log" && pass "ASan reports the planted overflow" \
    || fail "ASan did not report the planted bug" "check clang sanitizer runtime install"
else
  fail "compile with -fsanitize=fuzzer" "Ubuntu 24.04: sudo apt install libclang-rt-${CV}-dev ; see $T/cc.log"
  sed -n 1,5p "$T/cc.log"
fi

echo "== 5. AFL++ instrumented build + 20 s fuzz run"
mkdir -p "$T/in"; echo hello > "$T/in/seed"
if AFL_USE_ASAN=1 afl-clang-fast -g -fsanitize=fuzzer "$T/bug.c" -o "$T/bug_afl" >"$T/afl_cc.log" 2>&1; then
  pass "afl-clang-fast + ASan build"
  AFL_NO_UI=1 AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 \
    timeout 60 afl-fuzz -V 20 -i "$T/in" -o "$T/out" -- "$T/bug_afl" >"$T/afl.log" 2>&1
  if [ -f "$T/out/default/fuzzer_stats" ]; then
    pass "afl-fuzz ran ($(awk -F: '/execs_per_sec/{gsub(/ /,"",$2);print $2}' "$T/out/default/fuzzer_stats") exec/s)"
    N=$(ls "$T/out/default/crashes" 2>/dev/null | grep -c '^id' || true)
    echo "  (info) crashes found in 20 s: $N  (0 is acceptable here)"
  else
    fail "afl-fuzz did not start" "read the end of afl output below"; tail -5 "$T/afl.log"
  fi
else
  fail "afl-clang-fast build" "see $T/afl_cc.log"; sed -n 1,5p "$T/afl_cc.log"
fi

echo "== 6. Source-based coverage pipeline (this is how gap.py works)"
cat > "$T/cov.c" <<'EOF'
int f(int x) { return x * 2; }
int g(int x) { return x + 1; }
int main(int c, char **v) { (void)v; return c > 5 ? g(c) : f(c); }
EOF
(
  cd "$T" && "$CC_BIN" -fprofile-instr-generate -fcoverage-mapping cov.c -o cov \
  && { LLVM_PROFILE_FILE=cov.profraw ./cov; true; } \
  && llvm-profdata merge -o cov.profdata cov.profraw \
  && llvm-cov export -format=text -instr-profile=cov.profdata ./cov > cov.json \
  && python3 -c 'import json; d=json.load(open("cov.json")); fs={f["name"]:f["count"] for f in d["data"][0]["functions"]}; assert fs["f"]==1 and fs["g"]==0, fs'
) >"$T/cov.log" 2>&1 && pass "llvm-cov export: f reached, g in gap (as expected)" \
  || { fail "coverage pipeline" "llvm-profdata/llvm-cov must match the clang version"; tail -5 "$T/cov.log"; }

echo "== 7. Python environment ($VENV)"
PY="$VENV/bin/python"
if [ -x "$PY" ]; then
  for m in clang.cindex jinja2 yaml pytest; do
    check "python module $m" "$VENV/bin/pip install libclang jinja2 pyyaml pytest" "$PY" -c "import $m"
  done
  check "libclang parses C" "$VENV/bin/pip install --force-reinstall libclang" \
    "$PY" -c "import clang.cindex as c; tu=c.Index.create().parse('$T/cov.c'); assert len(list(tu.cursor.get_children()))>=3"
else
  fail "venv $VENV" "cd src && make setup"
fi

if [ "$ROLE" = "laptop" ]; then
  echo "== 8. Laptop-only"
  [ -n "$(git config --global user.email)" ] && pass "git identity: $(git config --global user.name) <$(git config --global user.email)>" \
    || fail "git identity" "git config --global user.name 'Your Name'; git config --global user.email <github-email>"
  if docker info >/dev/null 2>&1; then pass "docker usable (for OSS-Fuzz helper.py)"; \
    else warn "docker not usable: OSS-Fuzz helper unavailable, use the manual coverage fallback (Stage 0)"; fi
  if command -v llama-server >/dev/null 2>&1 || ls "$HOME"/llama.cpp/build/bin/llama-server >/dev/null 2>&1; then
    pass "llama.cpp llama-server found"
  else warn "llama-server not found: needed only for G2 (see section 13.1)"; fi
  ls "$HOME"/models/*.gguf >/dev/null 2>&1 && pass "GGUF model present in ~/models" || warn "no GGUF model in ~/models (G2 only)"
  curl -s --max-time 3 "$LLAMA_URL/health" | grep -q ok && pass "llama-server responding at $LLAMA_URL" \
    || echo "  (info) llama-server not running at $LLAMA_URL (fine unless you are testing G2)"
fi

if [ "$ROLE" = "lab" ]; then
  echo "== 8. Lab-only"
  sudo -n true >/dev/null 2>&1 && pass "passwordless sudo available" || warn "no sudo: use AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 (already set in worker.sh)"
  CP=$(cat /proc/sys/kernel/core_pattern)
  [ "${CP:0:1}" != "|" ] && pass "core_pattern = $CP" || warn "core_pattern pipes to a handler: 'echo core | sudo tee /proc/sys/kernel/core_pattern' or keep the AFL env var"
  S=$(systemctl is-enabled sleep.target 2>/dev/null)
  [ "$S" = "masked" ] && pass "suspend disabled (sleep.target masked)" \
    || warn "suspend may stop overnight runs: sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target (or ask lab admin)"
  [ -z "$(swapon --show 2>/dev/null)" ] && echo "  (info) no swap: OOM kills instead of slowdowns, monitor with free -h" || echo "  (info) swap is on: watch 'free -h' during runs"
  if [ -n "$NODES" ] && [ -f "$NODES" ]; then
    while read -r h; do [ -z "$h" ] && continue
      check "ssh to $h (no password)" "ssh-copy-id $h" ssh -o BatchMode=yes -o ConnectTimeout=5 "$h" true
    done < "$NODES"
  else echo "  (info) no nodes.txt given: skipped LAN ssh checks"; fi
  curl -sI --max-time 5 https://github.com >/dev/null 2>&1 && echo "  (info) internet: github.com reachable" \
    || echo "  (info) internet: github.com NOT reachable - use the USB offline kit (section 13.3)"
fi

echo "== Summary: $P passed, $F failed, $W warnings"
[ "$F" -eq 0 ]
