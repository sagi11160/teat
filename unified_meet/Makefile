PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: install run test test-fast doctor backup clean

## Create the virtualenv, install dependencies and the bridge browser, prepare the database.
install:
	./scripts/install.sh

## Start the server (http://localhost:8080 by default; configure in .env).
run:
	./scripts/run.sh

## Run every test: unit, integration and end-to-end media tests.
test:
	./scripts/test.sh

## Everything except the end-to-end browser tests.
test-fast:
	$(BIN)/python -m pytest -m "not e2e" -q

doctor:
	$(BIN)/python -m meetbridge.cli doctor

backup:
	$(BIN)/python -m meetbridge.cli backup

clean:
	rm -rf $(VENV) .pytest_cache
