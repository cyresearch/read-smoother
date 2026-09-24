#!/bin/bash
# Usage: ./start.sh                     open the shelf
#        ./start.sh book.pdf [--title "Title" --author "Author"]   open one PDF directly
# Extra flags are passed through to reader.py (--port, --config, --data-dir, --no-browser).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating a virtual environment and installing dependencies (first run only)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
exec .venv/bin/python reader.py "$@"
