PYTHON ?= python3
SCHEDULER_ROOT ?= /Users/user/scheduler

.PHONY: test compare lint

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests -q

compare:
	SCHEDULER_ROOT=$(SCHEDULER_ROOT) PYTHONPATH=. $(PYTHON) w31/compare.py --json report.json

lint:
	$(PYTHON) -m ruff check w31 tests
