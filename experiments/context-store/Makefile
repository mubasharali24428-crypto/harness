PYTHON ?= python3

.PHONY: test lint benchmark-x14-dry benchmark-x14-memory-dry
S11 = ../../section11

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests -q

lint:
	$(PYTHON) -m ruff check simplicial tests benchmark

# X-14 dry protocol (no paid models). Artifact name carries the current revision.
benchmark-x14-dry:
	PYTHONPATH=.:$(S11)/src $(S11)/.venv/bin/python benchmark/x14_dry.py \
		--out ../../docs/briefs/evidence/10x/x14-dry-$(shell git rev-parse --short HEAD).json

# X-14 memory baseline (x14-memory-dry/1): production case/memory layers vs a plain graph
# over the same rows. Needs the section4 venv (imports case_memory, memory_loader).
S4 = ../../section4
benchmark-x14-memory-dry:
	PYTHONPATH=.:$(S4)/src $(S4)/.venv/bin/python benchmark/memory_graph_baseline.py \
		--out ../../docs/briefs/evidence/10x/x14-memory-dry-$(shell git rev-parse --short HEAD).json
