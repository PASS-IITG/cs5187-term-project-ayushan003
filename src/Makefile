# GapHarn - run from src/ (Linux or WSL).  `make help` lists targets.
LIB    ?= libarchive
VENV   ?= .venv
PY     ?= $(VENV)/bin/python
WORK   := $(shell PYTHONPATH=. $(PY) -c "from gapharn.config import WORK_DIR; print(WORK_DIR)" 2>/dev/null)
CORPUS ?= $(WORK)/$(LIB)/corpus/seeds

LIBS   ?= libarchive libtiff libxml2
# Seconds each existing harness is fuzzed before its coverage is measured (0 = seeds only),
# and how many fuzz in parallel (each ASan fuzzer needs up to ~1 GB RAM).
FUZZ_SECONDS ?= 600
PAR    ?= 4
# Fuzzing campaign knobs (used by gen/oracle/fuzz/triage).
ENGINE      ?= libfuzzer
SECONDS_PER ?= 60
RUN_SECONDS ?= 20

.PHONY: help setup test check-env build api seeds existing-cov gap stage0 compare \
        gen oracle fuzz triage pipeline clean-work

help:
	@echo "make setup                 create $(VENV) and install pinned Python deps"
	@echo "make test                  run unit + toolchain tests"
	@echo "make check-env             verify toolchain (scripts/check_env.sh laptop)"
	@echo "make build LIB=x           clone + build cov and asan variants"
	@echo "make api LIB=x             parse public headers -> results/x/api.json"
	@echo "make seeds LIB=x           seed corpus from the library's test files"
	@echo "make existing-cov LIB=x    fuzz existing harnesses FUZZ_SECONDS each, then measure coverage"
	@echo "make gap LIB=x             -> results/x/gap.json (the API gap)"
	@echo "make stage0 LIB=x          build + api + seeds + existing-cov + gap"
	@echo "make compare               rank LIBS=\"$(LIBS)\" -> results/target_selection.md"
	@echo "make gen LIB=x             generate G1 harnesses -> harnesses/x/"
	@echo "make oracle LIB=x          compile/sanity/gap-hit/lint -> results/x/oracle_log.csv"
	@echo "make fuzz LIB=x            run kept harnesses (ENGINE=$(ENGINE) SECONDS_PER=$(SECONDS_PER))"
	@echo "make triage LIB=x          cluster crashes from the last campaign"
	@echo "make pipeline LIB=x        build + stage0 + gen + oracle (ready to fuzz)"

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements.txt

test:
	$(PY) -m pytest -q

check-env:
	bash scripts/check_env.sh laptop

build:
	PY=$(PY) bash scripts/build_target.sh $(LIB) cov asan

api:
	$(PY) -m gapharn.apimodel $(LIB)

seeds:
	$(PY) -m gapharn.seeds $(LIB)

existing-cov:
	PY=$(PY) FUZZ_SECONDS=$(FUZZ_SECONDS) PAR=$(PAR) bash scripts/existing_cov.sh $(LIB) $(CORPUS)

gap:
	$(PY) -m gapharn.gap $(LIB)

stage0: build api seeds existing-cov gap

compare:
	$(PY) -m gapharn.compare $(LIBS)

gen:
	$(PY) -m gapharn.gen_rule $(LIB)

oracle:
	$(PY) -m gapharn.oracle $(LIB) --seeds $(CORPUS) --run-seconds $(RUN_SECONDS)

fuzz:
	PY=$(PY) ENGINE=$(ENGINE) SECONDS_PER=$(SECONDS_PER) SEEDS="$(CORPUS)" bash scripts/fuzz.sh $(LIB)

triage:
	$(PY) -m gapharn.triage $(LIB) $(WORK)/$(LIB)/campaign

pipeline: build api seeds gen
	PY=$(PY) FUZZ_SECONDS=120 PAR=$(PAR) bash scripts/existing_cov.sh $(LIB) $(CORPUS) || true
	$(PY) -m gapharn.gap $(LIB) || true
	$(PY) -m gapharn.oracle $(LIB) --seeds $(CORPUS) --run-seconds $(RUN_SECONDS)

clean-work:
	rm -rf $(WORK)/$(LIB)/build-* $(WORK)/$(LIB)/existing-cov $(WORK)/$(LIB)/campaign $(WORK)/$(LIB)/oracle
