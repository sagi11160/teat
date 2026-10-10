#!/usr/bin/env bash
# Installs MeetBridge into ./.venv: Python dependencies, the Chromium used by bridge
# bots (with its system libraries when run as root or with sudo), and the database.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"

"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV" || {
    echo "Could not create a virtualenv. On Debian/Ubuntu: sudo apt-get install python3-venv" >&2
    exit 1
  }
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements-dev.txt

if [ "$(id -u)" = "0" ] || sudo -n true 2>/dev/null; then
  "$VENV/bin/python" -m playwright install --with-deps chromium
else
  "$VENV/bin/python" -m playwright install chromium
  echo "note: without root, Chromium's system libraries were not installed;" \
       "run 'sudo $VENV/bin/python -m playwright install-deps chromium' if the doctor reports a browser failure." >&2
fi

[ -f .env ] || cp .env.example .env
"$VENV/bin/python" -m meetbridge.cli migrate
"$VENV/bin/python" -m meetbridge.cli doctor
echo
echo "Installed. Start with: make run   (or ./scripts/run.sh)"
