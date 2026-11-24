PYTHON ?= python3

.PHONY: test lint

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests -q

lint:
	$(PYTHON) -m ruff check simplicial tests
