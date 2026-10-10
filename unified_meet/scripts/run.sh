#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Not installed yet. Run: make install   (or ./scripts/install.sh)" >&2
  exit 1
fi
exec "$VENV/bin/python" -m meetbridge.main
