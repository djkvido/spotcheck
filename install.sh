#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PY=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found."
  if [ "$(uname)" = "Darwin" ]; then
    echo "Install it with: brew install python  (or from https://www.python.org/downloads/)"
  else
    echo "Install it with your package manager, for example: sudo apt install python3 python3-venv"
  fi
  exit 1
fi

[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip uninstall --yes --quiet spotdl >/dev/null 2>&1 || true
.venv/bin/python -m pip install --quiet -e .
cp pyproject.toml .venv/pyproject.installed
echo
.venv/bin/python -m spotcheck --setup || true
echo
if [ "$1" = "--quiet" ]; then
  exit 0
fi
echo "Done. Starting SpotCheck; next time run ./start.sh"
exec ./start.sh
