#!/usr/bin/env bash
# herdr-fanout.sh — stable entry point; the implementation is herdr-fanout.py (stdlib, Python 3.9+).
# Run with --help for the plan schema, report schema, and exit codes.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="$(command -v python3 || true)"
[ -n "$py" ] || { echo "herdr-fanout: python3 required" >&2; exit 2; }
exec "$py" "$here/herdr-fanout.py" "$@"
