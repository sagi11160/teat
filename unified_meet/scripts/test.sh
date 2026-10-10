#!/usr/bin/env bash
# Runs the whole suite. Set MB_TEST_DATABASE_URL=postgresql+psycopg://... to run the
# API, permission and supervisor tests against PostgreSQL as well.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Not installed yet. Run: make install   (or ./scripts/install.sh)" >&2
  exit 1
fi
exec "$VENV/bin/python" -m pytest -q "$@"
