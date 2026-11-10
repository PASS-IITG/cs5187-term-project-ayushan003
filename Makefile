# GRAFT — see README.md
PY ?= python3

.PHONY: help reproduce test campaign probe corpus clean

help:
	@echo "GRAFT targets:"
	@echo "  make reproduce   replay the chain that produced defect #1, end to end"
	@echo "  make test        run the test suite"
	@echo "  make campaign    run a bounded campaign against Turso"
	@echo "  make probe       re-probe Turso's capabilities into configs/"
	@echo "  make smoke       run a few statements against each engine adapter"
	@echo "  make corpus      re-mine the DuckDB corpus (needs GITHUB_TOKEN)"
	@echo ""
	@echo "Engine binaries come from configs/targets.yaml; override with"
	@echo "GRAFT_TURSO_BIN and GRAFT_TURSO_PREFIX_BIN."

# The headline result. Steps 1-4 need only this repository; step 5 needs the
# two tursodb builds and says so when they are missing.
reproduce:
	$(PY) -m graft.reproduce

test:
	$(PY) -m pytest -q

# A bounded campaign. Without --max-cases this runs until workdir/STOP appears.
campaign:
	$(PY) -m graft.campaign --target turso --max-cases 2000

probe:
	$(PY) -m graft.generate.capabilities --probe turso

# Re-mining hits the GitHub API and its results change as upstream closes more
# issues, so the corpus in data/ is committed and reproduction does not need it.
corpus:
	$(PY) -m graft.corpus.fetch --target duckdb
	$(PY) -m graft.corpus.extract
	$(PY) -m graft.corpus.classify

clean:
	rm -rf .pytest_cache **/__pycache__ workdir/*.seed

smoke:
	$(PY) -m graft.engines.smoke
