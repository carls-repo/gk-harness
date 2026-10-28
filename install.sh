#!/bin/sh
# gk-harness installer entry point: finds a suitable Python and hands over to install.py.
ROOT=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}

if command -v "$PY" >/dev/null 2>&1 \
   && "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  exec "$PY" "$ROOT/install.py" "$@"
fi

echo "gk-harness: Python 3.9 or newer is required." >&2
if [ "$(uname)" = "Darwin" ] && ! command -v python3 >/dev/null 2>&1; then
  echo "Run xcode-select --install, then run this again." >&2
fi
exit 1
