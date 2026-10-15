#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || ! cmp -s pyproject.toml .venv/pyproject.installed; then
  ./install.sh --quiet || exit 1
fi
exec .venv/bin/python -m spotcheck "$@"
