#!/usr/bin/env bash
# The same launcher, for Linux and macOS. See run.bat for why it is this thin.
set -euo pipefail
cd "$(dirname "$0")"
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    exec "$candidate" tools/launch.py "$@"
  fi
done
echo "Python 3 was not found. Install it and run this again." >&2
exit 1
