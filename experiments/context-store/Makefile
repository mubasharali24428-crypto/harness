PYTHON ?= python3

.PHONY: test lint benchmark-x14-dry
S11 = ../../section11

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests -q

lint:
	$(PYTHON) -m ruff check simplicial tests benchmark

# X-14 dry protocol (no paid models). Artifact name carries the current revision.
benchmark-x14-dry:
	PYTHONPATH=.:$(S11)/src $(S11)/.venv/bin/python benchmark/x14_dry.py \
		--out ../../docs/briefs/evidence/10x/x14-dry-$(shell git rev-parse --short HEAD).json
